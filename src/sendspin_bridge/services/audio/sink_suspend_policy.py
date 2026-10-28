"""Read-only check: does the host suspend a Bluetooth sink between songs?

An AVDTP restart after a sink suspend occasionally moves the speaker's buffer
by tens of milliseconds, so a host that suspends the sink in every gap
(PipeWire's WirePlumber suspend timeout, PulseAudio's ``module-suspend-on-idle``)
is worth a warning.  Two sources feed it:

* a static probe of the audio server's configuration (:func:`probe_suspend_policy`),
  which explains *why* a suspend may happen, and
* an observation (:class:`SuspendObserver`): the sink went to ``suspended`` while the
  speaker was connected and the bridge had not asked for it.  This one is authoritative.

The bridge only reads; it never changes a host audio setting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

__all__ = [
    "SuspendObserver",
    "SuspendPolicy",
    "parse_pa_modules",
    "parse_pactl_info",
    "parse_pw_dump",
    "probe_suspend_policy",
]

# Both servers suspend an idle sink after 5 s unless told otherwise.
DEFAULT_SUSPEND_TIMEOUT_S = 5.0
# A static policy only warns when it suspends sooner than this.
WARN_TIMEOUT_S = 60.0
_PROBE_TIMEOUT_S = 3.0

Server = Literal["pulseaudio", "pipewire", "unknown"]
Source = Literal["module-suspend-on-idle", "wireplumber", "none", "unknown"]
Run = Callable[[list[str]], Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class SuspendPolicy:
    server: Server
    suspends: bool | None  # None = unknown
    timeout_s: float | None
    source: Source


_UNKNOWN = SuspendPolicy(server="unknown", suspends=None, timeout_s=None, source="unknown")


def parse_pactl_info(text: str) -> Server:
    """Tell PulseAudio from PulseAudio-on-PipeWire by ``pactl info``'s server name."""
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() == "server name":
            lowered = value.lower()
            if "pipewire" in lowered:
                return "pipewire"
            if "pulseaudio" in lowered:
                return "pulseaudio"
    return "unknown"


def parse_pa_modules(text: str) -> SuspendPolicy:
    """``pactl list modules short``: is ``module-suspend-on-idle`` loaded, with which timeout?"""
    seen_any = False
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        seen_any = True
        if cols[1].strip() == "module-suspend-on-idle":
            args = cols[2] if len(cols) > 2 else ""
            match = re.search(r"\btimeout=(\d+(?:\.\d+)?)", args)
            timeout = float(match.group(1)) if match else DEFAULT_SUSPEND_TIMEOUT_S
            return SuspendPolicy("pulseaudio", True, timeout, "module-suspend-on-idle")
    if not seen_any:
        return SuspendPolicy("pulseaudio", None, None, "unknown")
    return SuspendPolicy("pulseaudio", False, None, "none")


def parse_pw_dump(nodes: list[dict[str, Any]]) -> SuspendPolicy:
    """``pw-dump``: ``session.suspend-timeout-seconds`` on the ``bluez_output.*`` nodes.

    0 disables the suspend; absent means WirePlumber's default (5 s, it suspends).
    """
    timeouts: list[float] = []
    for node in nodes:
        info = node.get("info") if isinstance(node, dict) else None
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict) or not str(props.get("node.name", "")).startswith("bluez_output."):
            continue
        raw = props.get("session.suspend-timeout-seconds")
        try:
            timeouts.append(DEFAULT_SUSPEND_TIMEOUT_S if raw is None else float(raw))
        except (TypeError, ValueError):
            timeouts.append(DEFAULT_SUSPEND_TIMEOUT_S)
    if not timeouts:
        return SuspendPolicy("pipewire", None, None, "unknown")
    suspending = [t for t in timeouts if t > 0]
    if not suspending:
        return SuspendPolicy("pipewire", False, None, "wireplumber")
    return SuspendPolicy("pipewire", True, min(suspending), "wireplumber")


async def _run_subprocess(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await proc.communicate()
    except BaseException:
        proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def probe_suspend_policy(*, run: Run = _run_subprocess) -> SuspendPolicy:
    """Read the host's suspend policy.  Never raises; anything unreadable is ``unknown``."""

    async def call(argv: list[str]) -> tuple[int, str]:
        return await asyncio.wait_for(run(argv), _PROBE_TIMEOUT_S)

    try:
        rc, info = await call(["pactl", "info"])
        if rc != 0:
            return _UNKNOWN
        server = parse_pactl_info(info)
        if server == "pulseaudio":
            rc, modules = await call(["pactl", "list", "modules", "short"])
            return parse_pa_modules(modules) if rc == 0 else SuspendPolicy("pulseaudio", None, None, "unknown")
        if server == "pipewire":
            try:
                rc, dump = await call(["pw-dump"])
                nodes = json.loads(dump) if rc == 0 else None
            except Exception as exc:  # pw-dump missing, no native socket, bad JSON, timeout
                logger.debug("sink suspend probe: pw-dump unavailable: %s", exc)
                nodes = None
            if isinstance(nodes, list):
                return parse_pw_dump(nodes)
            return SuspendPolicy("pipewire", None, None, "unknown")
        return _UNKNOWN
    except Exception as exc:
        logger.debug("sink suspend probe failed: %s", exc)
        return _UNKNOWN


class SuspendObserver:
    """Per device: host suspends observed while the bridge did not ask for one."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, clear_after_s: float = 86_400.0) -> None:
        self._clock = clock
        self._clear_after_s = clear_after_s
        self._observed = 0
        self._last_mono: float | None = None
        self._last_at: str | None = None
        self._last_idle_s: float | None = None
        self._idle_since: float | None = None

    def on_state(self, old: str | None, new: str, *, bridge_requested: bool, connected: bool) -> None:
        now = self._clock()
        if new == "idle":
            if self._idle_since is None:
                self._idle_since = now
            return
        idle_since, self._idle_since = self._idle_since, None
        if new != "suspended" or bridge_requested or not connected:
            return
        self._observed += 1
        self._last_mono = now
        self._last_at = datetime.now(tz=UTC).isoformat()
        self._last_idle_s = (now - idle_since) if idle_since is not None else None

    def snapshot(self, policy: SuspendPolicy | None) -> dict[str, object] | None:
        if self._observed == 0 and policy is None:
            return None
        recent = self._last_mono is not None and (self._clock() - self._last_mono) <= self._clear_after_s
        reason: str | None = None
        if recent:
            reason = "observed_suspend"
        elif policy is not None and policy.suspends and (policy.timeout_s is None or policy.timeout_s < WARN_TIMEOUT_S):
            reason = (
                "module_suspend_on_idle" if policy.source == "module-suspend-on-idle" else "wireplumber_suspend_timeout"
            )
        return {
            "observed": self._observed,
            "last_at": self._last_at,
            "last_idle_s": self._last_idle_s,
            "policy": asdict(policy) if policy is not None else None,
            "warn": reason is not None,
            "reason": reason,
        }


async def run_suspend_probe_loop(
    get_clients: Callable[[], list[Any]],
    *,
    interval_s: float = 600.0,
    probe: Callable[[], Awaitable[SuspendPolicy]] = probe_suspend_policy,
) -> None:
    """Probe at start and every *interval_s*, handing the policy to every client."""
    while True:
        policy = await probe()
        for client in list(get_clients()):
            try:
                client.set_suspend_policy(policy)
            except Exception:
                logger.debug("sink suspend probe: client rejected the policy", exc_info=True)
        await asyncio.sleep(interval_s)
