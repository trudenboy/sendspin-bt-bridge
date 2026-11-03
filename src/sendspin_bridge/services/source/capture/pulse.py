"""The phone's capture on PulseAudio: ``bluez_source.<MAC>...`` read by ``parecord``.

HAOS runs PulseAudio in ``hassio-audio``; a phone that connects as an A2DP source becomes a
card with the ``a2dp-source`` profile and a recording source.  This backend finds that
source, nudges the card's profile when HAOS' missing ``module-bluetooth-policy`` leaves it
off, and records it as raw s16 PCM.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

from sendspin_bridge.services.source.capture import (
    CHANNELS,
    SAMPLE_RATE,
    CaptureEndpoint,
    CaptureStream,
    CommandResult,
    ProcessCapture,
    mac_from_node,
    run_command,
    terminate_process,
)

__all__ = [
    "BLUEZ_SOURCE_RE",
    "PulseAudioBackend",
    "PulseSubscription",
    "parse_card_profiles",
    "parse_short_sources",
    "parse_subscribe_line",
]

logger = logging.getLogger(__name__)

BLUEZ_SOURCE_RE = re.compile(r"^bluez_source\.([0-9A-Fa-f_]{17})(?:\..*)?$")
_A2DP_SOURCE_PROFILE = "a2dp-source"


@dataclass(frozen=True, slots=True)
class PulseSource:
    """One row of ``pactl list short sources``."""

    index: int
    name: str
    driver: str
    sample_spec: str
    state: str


def parse_short_sources(text: str) -> list[PulseSource]:
    """Rows of ``pactl list short sources`` (tab-separated; tolerate runs of spaces)."""
    out: list[PulseSource] = []
    for line in text.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 5:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        out.append(PulseSource(index=index, name=parts[1], driver=parts[2], sample_spec=parts[3], state=parts[4]))
    return out


def parse_subscribe_line(line: str) -> bool:
    """Whether a ``pactl subscribe`` line describes a source or card change.

    ``source-output`` lines are playback streams, not capture sources: matching the space
    before ``#`` keeps them out.
    """
    lowered = line.lower()
    return "on source #" in lowered or "on card #" in lowered


def parse_card_profiles(text: str) -> dict[str, str]:
    """Card name → active profile, from ``pactl list cards``."""
    active: dict[str, str] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Name:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("Active Profile:"):
            profile = line.split(":", 1)[1].strip().strip("<>")
            if current:
                active[current] = profile
    return active


def parse_card_available_profiles(text: str) -> dict[str, set[str]]:
    """Card name → the profile names it offers, from ``pactl list cards``.

    ``Name:`` and ``Profiles:`` are themselves indented, so they are matched before the
    indentation test; the profile list ends at the next non-indented line or ``Ports:``.
    """
    available: dict[str, set[str]] = {}
    current: str | None = None
    in_profiles = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Name:"):
            current = line.split(":", 1)[1].strip()
            in_profiles = False
            continue
        if line.startswith("Profiles:"):
            in_profiles = True
            continue
        if line.startswith("Active Profile:") or line.startswith("Ports:"):
            in_profiles = False
            continue
        if not raw[:1].isspace():
            in_profiles = False
            continue
        if in_profiles and current and ":" in line:
            name = line.split(":", 1)[0].strip()
            if name:
                available.setdefault(current, set()).add(name)
    return available


class PulseSubscription:
    """A running ``pactl subscribe`` process feeding change callbacks."""

    def __init__(self, proc: Any, task: asyncio.Task[None]) -> None:
        self._proc = proc
        self._task = task

    @property
    def running(self) -> bool:
        return not self._task.done() and getattr(self._proc, "returncode", None) is None

    async def stop(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        await terminate_process(self._proc)


class PulseAudioBackend:
    """Find, profile and record the phone's PulseAudio capture source."""

    name = "pulseaudio"

    def __init__(
        self,
        *,
        run: Any = run_command,
        spawn: Any | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
    ) -> None:
        self._run = run
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._sample_rate = sample_rate
        self._channels = channels
        self.preferred_mac: str | None = None

    def set_preferred_mac(self, mac: str | None) -> None:
        self.preferred_mac = str(mac or "").upper() or None

    async def find(self) -> CaptureEndpoint | None:
        """The newest ``bluez_source`` PulseAudio exposes, if any."""
        result: CommandResult = await self._run(["pactl", "list", "short", "sources"])
        if result.returncode != 0:
            logger.warning("pactl could not list sources: %s", result.stderr.strip()[:200])
            return None
        candidates = [source for source in parse_short_sources(result.stdout) if BLUEZ_SOURCE_RE.match(source.name)]
        if self.preferred_mac:
            candidates = [source for source in candidates if mac_from_node(source.name) == self.preferred_mac]
            if not candidates:
                return None
        if not candidates:
            return None
        if len(candidates) > 1:
            logger.warning("%d Bluetooth capture sources are present; preferring the running one", len(candidates))
        # PA reuses the lowest free index, so index order is not connection order.  A phone
        # that is sending audio makes its source RUNNING; when none is, the highest index is
        # the least arbitrary tie-break.
        chosen = max(candidates, key=lambda source: (source.state.upper() == "RUNNING", source.index))
        return CaptureEndpoint(
            node=chosen.name,
            backend=self.name,
            phone_mac=mac_from_node(chosen.name),
            # Presence, not activity: ``module-suspend-on-idle`` suspends a BlueZ source while
            # nothing records it, and a suspended source still means the phone is connected.
            # ``line_sense`` reports the phone's presence, matching the PipeWire backend.
            running=True,
        )

    async def ensure_a2dp_source(self, mac: str | None = None) -> bool:
        """Switch a phone's card to the recording profile when it is not already there.

        ``hassio-audio`` does not load ``module-bluetooth-policy``, so nothing else asks for
        ``a2dp-source`` when a phone starts streaming — and until the profile is switched the
        phone has no ``bluez_source`` for :meth:`find` to see.  With no ``mac`` (the usual
        case: nothing found yet) every BlueZ card that offers ``a2dp-source`` and is not on it
        is switched.  Best-effort: a refusal is logged and the caller keeps waiting.
        """
        cards = await self._run(["pactl", "list", "cards"])
        if cards.returncode != 0:
            logger.warning("pactl could not list cards: %s", cards.stderr.strip()[:200])
            return False
        active = parse_card_profiles(cards.stdout)
        available = parse_card_available_profiles(cards.stdout)
        targets = [
            name
            for name in active
            if name.startswith("bluez_card.")
            and (mac is None or name == f"bluez_card.{mac.replace(':', '_')}")
            and _A2DP_SOURCE_PROFILE in available.get(name, set())
            and active[name] != _A2DP_SOURCE_PROFILE
        ]
        changed = False
        for card in targets:
            result = await self._run(["pactl", "set-card-profile", card, _A2DP_SOURCE_PROFILE])
            if result.returncode == 0:
                logger.info("Switched %s to %s for the phone source", card, _A2DP_SOURCE_PROFILE)
                changed = True
            else:
                logger.warning("Could not switch %s to %s: %s", card, _A2DP_SOURCE_PROFILE, result.stderr.strip()[:200])
        if mac is not None and active.get(f"bluez_card.{mac.replace(':', '_')}") == _A2DP_SOURCE_PROFILE:
            return True
        return changed

    async def open(self, endpoint: CaptureEndpoint) -> CaptureStream:
        """Record ``endpoint`` as raw s16 PCM on stdout."""
        argv: Sequence[str] = (
            "parecord",
            "--raw",
            "--device",
            endpoint.node,
            "--rate",
            str(self._sample_rate),
            "--channels",
            str(self._channels),
            "--format",
            "s16le",
        )
        return ProcessCapture(
            argv,
            sample_rate=self._sample_rate,
            channels=self._channels,
            spawn=self._spawn,
        )

    async def subscribe(self, on_change: Callable[[], None]) -> PulseSubscription:
        """Call ``on_change`` for every source or card event; the caller stops the subscription."""
        proc = await self._spawn(
            "pactl", "subscribe", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        task = asyncio.create_task(self._read_events(proc, on_change), name="pulse-subscribe")
        return PulseSubscription(proc, task)

    @staticmethod
    async def _read_events(proc: Any, on_change: Callable[[], None]) -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            if parse_subscribe_line(line.decode(errors="replace")):
                on_change()
