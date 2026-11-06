"""Headset capabilities and the A2DP ⇄ HFP profile hand-off for a microphone.

The bridge is a music player on A2DP. A headset's microphone exists only on the
HFP/HSP profile, which cannot coexist with A2DP on the same link. This module is
the one place that gives the card to HFP for a microphone and takes it back.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

import sendspin_bridge.bluetooth.audio as bt_audio
from sendspin_bridge.services.audio.pulse import set_card_profile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "CardProfiles",
    "HandoffPhase",
    "HeadsetCapabilities",
    "HeadsetProfileService",
    "HostCardProfiles",
    "classify",
    "get_headset_profiles",
    "headset_inputs",
    "headset_mic_id",
    "owns_link",
]

_HFP_MARKERS = ("headset_head_unit", "headset-head-unit", "handsfree", "headset_audio_gateway", "headset-audio-gateway")
_A2DP = ("a2dp_sink", "a2dp-sink")


@dataclass(frozen=True, slots=True)
class HeadsetCapabilities:
    kind: str
    a2dp_available: bool
    hfp_available: bool
    microphone_available: bool


def classify(*, hfp_available: bool, a2dp_available: bool, form_factor: str | None = None) -> HeadsetCapabilities:
    """Conservative classification from card evidence and an optional BlueZ form factor."""
    if not hfp_available:
        return HeadsetCapabilities("speaker", a2dp_available, False, False)
    kind = "headset" if (form_factor or "").lower() == "headset" else "speaker_with_mic"
    return HeadsetCapabilities(kind, a2dp_available, True, True)


def headset_mic_id(mac: str) -> str:
    return f"headset-mic:{mac.upper()}"


def _pick(profiles: tuple[str, ...], markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        for profile in profiles:
            if marker in profile.lower() and profile.lower() != "off":
                return profile
    return None


def _pick_hfp(profiles: tuple[str, ...]) -> str | None:
    # Prefer the full HFP profile over HSP when both are offered.
    for marker in ("handsfree_head_unit", "headset-head-unit", "headset_audio_gateway", "handsfree"):
        for profile in profiles:
            if marker in profile.lower() and profile.lower() != "off":
                return profile
    return _pick(profiles, _HFP_MARKERS)


def _pick_a2dp(profiles: tuple[str, ...]) -> str | None:
    return _pick(profiles, _A2DP)


def _set_status(client: Any | None, **values: Any) -> None:
    if client is not None and hasattr(client, "_update_status"):
        client._update_status(values)


def _clear_missing_sink_error(client: Any | None) -> None:
    """Clear the monitor's "link has no sink" card error once A2DP is proven back.

    That error is published to ``last_error``; nothing else owns it after a call
    ends, so without this it outlives the microphone and keeps the device card
    degraded until an unrelated reconnect.
    """
    if client is None or not hasattr(client, "status"):
        return
    from sendspin_bridge.bluetooth.monitor import _MISSING_SINK_ERROR

    if client.status.get("last_error") == _MISSING_SINK_ERROR:
        _set_status(client, last_error=None)


def _client_for_mac(mac: str) -> Any | None:
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot

    wanted = mac.upper()
    for client in get_device_registry_snapshot().active_clients:
        manager = getattr(client, "bt_manager", None)
        if manager is not None and str(getattr(manager, "mac_address", "") or "").upper() == wanted:
            return client
    return None


class CardProfiles(Protocol):
    """The audio server's card for one Bluetooth device, by its underscored address.

    ``state`` reads the card's profiles, ``select`` switches it and answers
    whether the server accepted, ``sinks`` names the sinks present now.
    """

    def state(self, pa_mac: str) -> bt_audio.CardProfileState | None: ...

    def select(self, pa_mac: str, profile: str) -> bool: ...

    def sinks(self) -> set[str]: ...


class HostCardProfiles:
    """The host's PulseAudio/PipeWire cards (``pactl``)."""

    def state(self, pa_mac: str) -> bt_audio.CardProfileState | None:
        return bt_audio.card_profile_state(pa_mac)

    def select(self, pa_mac: str, profile: str) -> bool:
        return bool(set_card_profile(f"bluez_card.{pa_mac}", profile))

    def sinks(self) -> set[str]:
        return {entry["name"] for entry in bt_audio.list_sinks() or ()}


#: Where a device is in its Microphone hand-off (CONTEXT.md).  ``none`` — no
#: hand-off, the device plays as an output; ``acquiring`` — the card is being
#: given to HFP; ``microphone`` — the microphone is in use and the Player
#: rests; ``restoring`` — the card is going back to A2DP; ``restore_failed`` —
#: it did not, and the Player stays stopped until an explicit retry.
HandoffPhase = Literal["none", "acquiring", "microphone", "restoring", "restore_failed"]


def owns_link(mac: str | None) -> bool:
    """Is a Source capture using this link — taking it, holding it, returning it, or stuck returning it?

    Every automatic output operation asks this before touching the link.  The
    Source feed deliberately stops the Player and switches the card to HFP, so
    the heartbeat sees "connected with no player" and no A2DP sink: that is the
    expected shape of a live call, not a broken link, and reconnect, sink
    recovery and Player starts must stay out of it.
    """
    return bool(mac) and get_headset_profiles().handoff(str(mac)) != "none"


class HeadsetProfileService:
    """The one owner of the Microphone hand-off: it alone switches a headset between A2DP and HFP.

    It is also the only writer of the link facts the rest of the bridge reads
    — the Bluetooth manager's ``profile_mode``, ``audio_sink_ready``,
    ``a2dp_available``, the client's output sink and the device status.

    Lock order: SourceClient endpoint -> capture -> this async lock. Automatic
    Player starts also take this async lock. Blocking configuration takes only
    _audio_lock, then reads _view_lock; never wait for either on the event loop.
    The phase is published before draining existing configuration work.
    """

    def __init__(
        self,
        *,
        cards: CardProfiles | None = None,
        clients: Callable[[str], Any | None] | None = None,
    ) -> None:
        self._cards: CardProfiles = cards if cards is not None else HostCardProfiles()
        self._clients = clients if clients is not None else (lambda mac: _client_for_mac(mac))
        self._lock = asyncio.Lock()
        self._audio_lock = threading.RLock()
        self._view_lock = threading.Lock()
        self._phases: dict[str, HandoffPhase] = {}
        self._resume: dict[str, bool] = {}

    # -- the hand-off phase -----------------------------------------------------

    def handoff(self, mac: str) -> HandoffPhase:
        """Where *mac* is in its Microphone hand-off; ``none`` when it plays as an output."""
        with self._view_lock:
            return self._phases.get(mac.upper(), "none")

    def _set_handoff(self, mac: str, phase: HandoffPhase) -> None:
        with self._view_lock:
            if phase == "none":
                self._phases.pop(mac, None)
            else:
                self._phases[mac] = phase

    def active(self, mac: str) -> bool:
        """The microphone is in use."""
        return self.handoff(mac) == "microphone"

    def blocked(self, mac: str) -> bool:
        """Automatic output operations must stay out, including after failure."""
        return self.handoff(mac) != "none"

    def capture_owned(self, mac: str) -> bool:
        """Explicit recovery is forbidden until capture and its transition finish."""
        return self.handoff(mac) in {"acquiring", "microphone", "restoring"}

    def restore_failed(self) -> bool:
        """Is any headset stuck after a failed A2DP restore, awaiting retry?

        The bridge status uses this to keep the source error visible while the
        feed itself is off: the operator turned capture off, the hand-off back
        to A2DP failed, and without the block nothing would say why the speaker
        is silent.
        """
        with self._view_lock:
            return "restore_failed" in self._phases.values()

    # -- the facts the rest of the bridge reads ------------------------------

    def _publish_microphone(self, client: Any | None, card: bt_audio.CardProfileState) -> None:
        manager = getattr(client, "bt_manager", None)
        if manager is not None:
            manager.audio_sink_ready = False
            manager.profile_mode = "microphone"
        _set_status(client, profile_mode="microphone", a2dp_available=card.a2dp_available)

    def _publish_restore_failed(self, client: Any | None, profile_mode: str) -> None:
        manager = getattr(client, "bt_manager", None)
        if manager is not None:
            manager.audio_sink_ready = False
            manager.a2dp_available = False
            manager.profile_mode = profile_mode
        if client is not None:
            # HFP removed this sink: keeping its name makes the volume slider,
            # transfer readiness and the health view claim a sink that is no
            # longer there.
            client.bluetooth_sink_name = None
        # These are the facts the existing a2dp_unavailable health path and the
        # SPA's retry control already read.
        _set_status(client, profile_mode=profile_mode, a2dp_available=False, sink_missing=True, playing=False)

    def _publish_output(self, client: Any | None, card: bt_audio.CardProfileState, sink: str) -> None:
        manager = getattr(client, "bt_manager", None)
        if manager is not None:
            manager.audio_sink_ready = True
            manager.a2dp_available = card.a2dp_available
            manager.hfp_available = card.hfp_available
            manager.profile_mode = card.mode
        if client is not None:
            client.bluetooth_sink_name = sink
        _set_status(client, profile_mode="a2dp", a2dp_available=True, sink_missing=False)
        _clear_missing_sink_error(client)

    def _profile_mode_now(self, mac: str) -> str:
        """The card's own word on its profile, for the failed-restore branch.

        A refused switch leaves the card on HFP; a switch that claims success
        may still not have moved it.  Publishing ``a2dp`` (or the acquisition's
        ``microphone``) after a failed restore would lie about what the
        operator can expect, so read it back instead.
        """
        try:
            card = self._cards.state(bt_audio.DeviceAddress.require(mac).underscores)
        except Exception:
            return "unknown"
        return card.mode if card is not None else "unknown"

    # -- fences for automatic output work -------------------------------------

    @contextmanager
    def automatic_audio(self, mac: str) -> Iterator[bool]:
        """Reserve a whole blocking output configuration, including role flips."""
        with self._audio_lock:
            yield not self.blocked(mac)

    @asynccontextmanager
    async def player_start(self, mac: str) -> AsyncIterator[bool]:
        async with self._lock:
            yield not self.blocked(mac)

    def _drain_audio(self) -> None:
        with self._audio_lock:
            pass

    # -- transitions ------------------------------------------------------------

    async def acquire_microphone(self, mac: str) -> bool:
        try:
            return bool(await _complete(self._acquire_microphone(mac)))
        except asyncio.CancelledError:
            await self.release_microphone(mac)
            raise

    async def _acquire_microphone(self, mac: str) -> bool:
        wanted = mac.upper()
        acquired = False
        async with self._lock:
            if self.active(wanted):
                return True
            if self.blocked(wanted):
                return False
            from sendspin_bridge.config import load_config

            config = await asyncio.to_thread(load_config) or {}
            if any(
                isinstance(entry, dict)
                and str((entry.get("backend") or {}).get("mac") or "").upper() == wanted
                and entry.get("allow_call_mode") is False
                for entry in config.get("players") or ()
            ):
                return False
            self._set_handoff(wanted, "acquiring")
            client = self._clients(wanted)
            stopped = False
            try:
                await asyncio.to_thread(self._drain_audio)
                address = bt_audio.DeviceAddress.require(wanted)
                card = await asyncio.to_thread(self._cards.state, address.underscores)
                if card is None or not card.hfp_available:
                    self._set_handoff(wanted, "none")
                    return False
                hfp = _pick_hfp(card.profiles)
                if hfp is None:
                    self._set_handoff(wanted, "none")
                    return False
                was_running = bool(getattr(client, "is_running", lambda: False)())
                if client is not None and was_running:
                    await client.stop_sendspin()
                stopped = True
                self._resume[wanted] = was_running
                acquired = await asyncio.to_thread(self._cards.select, address.underscores, hfp)
                self._set_handoff(wanted, "microphone")
                if acquired:
                    self._publish_microphone(client, card)
            except Exception:
                if not stopped:
                    self._set_handoff(wanted, "none")
                logger.warning("Headset %s: microphone acquisition failed", wanted, exc_info=True)
        if not acquired and stopped:
            await self.release_microphone(wanted)
        return acquired

    async def _restore_locked(self, wanted: str) -> bool:
        from sendspin_bridge.services.source import state
        from sendspin_bridge.services.source.client import SourceError

        self._set_handoff(wanted, "restoring")
        client = self._clients(wanted)
        manager = getattr(client, "bt_manager", None)
        try:
            evidence = await asyncio.to_thread(self._restore_audio, wanted, manager)
        except Exception:
            evidence = None
        if evidence is None:
            self._set_handoff(wanted, "restore_failed")
            self._publish_restore_failed(client, await asyncio.to_thread(self._profile_mode_now, wanted))
            state.publish(error="headset_a2dp_restore_failed", streaming=False)
            raise SourceError("headset_a2dp_restore_failed", "Could not restore the headset A2DP profile and output.")
        card, sink = evidence
        self._publish_output(client, card, sink)
        self._set_handoff(wanted, "none")
        if state.snapshot().error == "headset_a2dp_restore_failed":
            state.publish(error=None)
        return self._resume.pop(wanted, False)

    def _restore_audio(self, wanted: str, manager: Any | None = None) -> tuple[bt_audio.CardProfileState, str] | None:
        """Switch the card back to A2DP and prove it, with a real sink.

        A manager's own configure path retries the profile switch and the late
        sink publication (the reason it is retried everywhere else); a restore
        failure is sticky, so one transient refusal must not become terminal.
        The lightweight rigs have no manager; they keep the direct switch, and
        either way the card and sink are read back rather than trusted.
        """
        from sendspin_bridge.services.audio.sink_names import sink_name_candidates

        address = bt_audio.DeviceAddress.require(wanted)
        with self._audio_lock:
            configure_owned = getattr(manager, "_configure_bluetooth_audio_owned", None)
            if callable(configure_owned):
                # The owner doing its own work bypasses the automatic fence;
                # this also fires on_sink_found (sink registration/persist),
                # which the hand-rolled switch never did.
                if not configure_owned():
                    return None
            else:
                card = self._cards.state(address.underscores)
                profile = _pick_a2dp(card.profiles) if card else None
                if profile is None or not self._cards.select(address.underscores, profile):
                    return None
            card = self._cards.state(address.underscores)
            names = self._cards.sinks()
            sink = next((name for name in sink_name_candidates(address) if name in names), None)
            if card is None or card.mode != "a2dp" or sink is None:
                return None
            return card, sink

    async def release_microphone(self, mac: str) -> None:
        await _complete(self._release_microphone(mac))

    async def _release_microphone(self, mac: str) -> None:
        wanted = mac.upper()
        async with self._lock:
            if self.handoff(wanted) not in {"microphone", "acquiring"}:
                return
            resume = await self._restore_locked(wanted)
        if resume and (client := self._clients(wanted)) is not None:
            await client.start_sendspin()

    async def retry_a2dp(self, mac: str) -> bool:
        """One explicit recovery on the bridge loop, after capture has released."""
        if self.capture_owned(mac):
            return False
        return bool(await _complete(self._retry_a2dp(mac)))

    async def _retry_a2dp(self, mac: str) -> bool:
        wanted = mac.upper()
        async with self._lock:
            if self.capture_owned(wanted):
                return False
            if self.handoff(wanted) == "restore_failed":
                resume = await self._restore_locked(wanted)
            else:
                client = self._clients(wanted)
                manager = getattr(client, "bt_manager", None)
                if manager is None or not manager.connected:
                    return False
                if not await asyncio.to_thread(manager.configure_bluetooth_audio):
                    return False
                if client is not None:
                    client._sync_bt_profile_status()
                resume = True
        if resume and (client := self._clients(wanted)) is not None:
            await client.start_sendspin()
        return True


async def _complete(operation: Any) -> Any:
    """Settle a whole ownership transaction before delivering cancellation.

    Shielding is bounded to a task we always join, including repeated cancel;
    no profile worker or resume task escapes to mutate a later selection.
    """
    task = asyncio.create_task(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


_service: HeadsetProfileService | None = None


def get_headset_profiles() -> HeadsetProfileService:
    global _service
    if _service is None:
        _service = HeadsetProfileService()
    return _service


# -- source-input discovery ---------------------------------------------------


def _headset_mac_from_input(source_id: str | None) -> str | None:
    """The MAC of a ``headset-mic:`` id, or None for any other (or no) input."""
    from sendspin_bridge.services.source.inputs import parse_source_id

    parsed = parse_source_id(str(source_id or ""))
    if parsed is None or parsed[0] != "headset_microphone":
        return None
    return parsed[1]


def headset_inputs(config: dict[str, Any] | None, *, active_input: str | None = None) -> list[dict[str, Any]]:
    """Configured Bluetooth players whose card offers an HFP profile.

    Reads the client's already-published status, never PulseAudio: the source
    status is built on the bridge loop and must not block on a subprocess.

    The selected input is the one being captured, so it stays listed whatever
    the stopped player's status says: while the Source feed owns the card, the
    daemon is down and its last status no longer reports ``hfp_available``.
    The manager's card evidence is the capability that counts for it, and a
    capture that is still opening (``profile_mode == "microphone"``) is the
    same commitment.
    """
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot

    selected = active_input if active_input is not None else (config or {}).get("SENDSPIN_SOURCE_ACTIVE")
    selected_mac = _headset_mac_from_input(selected)
    clients = {
        str(getattr(getattr(client, "bt_manager", None), "mac_address", "") or "").upper(): client
        for client in get_device_registry_snapshot().active_clients
        if getattr(client, "bt_manager", None) is not None
    }
    found: list[dict[str, Any]] = []
    for player in (config or {}).get("players") or ():
        if not isinstance(player, dict):
            continue
        backend = player.get("backend")
        if not isinstance(backend, dict) or backend.get("type") != "bluetooth_a2dp":
            continue
        mac = str(backend.get("mac") or "").strip().upper()
        if not mac or player.get("allow_call_mode") is False:
            continue
        client = clients.get(mac)
        status = getattr(client, "status", {}) if client is not None else {}
        owns_input = mac == selected_mac or str(status.get("profile_mode") or "") == "microphone"
        hfp_available = status.get("hfp_available") is True
        if not hfp_available and not owns_input:
            continue
        if not hfp_available:
            known = getattr(getattr(client, "bt_manager", None), "hfp_available", None)
            hfp_available = known if isinstance(known, bool) else True
        caps = classify(
            hfp_available=hfp_available,
            a2dp_available=status.get("a2dp_available") is not False,
            form_factor=None,
        )
        found.append(
            {
                "mac": mac,
                "name": str(player.get("name") or mac),
                "kind": caps.kind,
                "a2dp_available": caps.a2dp_available,
                "hfp_available": hfp_available,
            }
        )
    return found
