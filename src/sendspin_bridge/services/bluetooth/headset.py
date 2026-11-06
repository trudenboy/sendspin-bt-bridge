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
from typing import TYPE_CHECKING, Any

import sendspin_bridge.bluetooth.audio as bt_audio
from sendspin_bridge.services.audio.pulse import set_card_profile

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "HeadsetCapabilities",
    "HeadsetProfileService",
    "classify",
    "get_headset_profiles",
    "headset_inputs",
    "headset_mic_id",
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


def _form_factor(pa_mac: str) -> str | None:
    """Read the card's ``device.form_factor`` without a second PA model API."""
    try:
        from sendspin_bridge.services.audio.pulse import list_cards

        cards = list_cards() or []
    except Exception:
        return None
    card = next((entry for entry in cards if entry.get("name") == f"bluez_card.{pa_mac}"), None)
    if card is None:
        return None
    value = card.get("form_factor")
    return str(value) if value else None


def _client_for_mac(mac: str) -> Any | None:
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot

    wanted = mac.upper()
    for client in get_device_registry_snapshot().active_clients:
        manager = getattr(client, "bt_manager", None)
        if manager is not None and str(getattr(manager, "mac_address", "") or "").upper() == wanted:
            return client
    return None


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


def _profile_mode_now(mac: str) -> str:
    """The card's own word on its profile, for the failed-restore branch.

    A refused switch leaves the card on HFP; a switch that claims success may
    still not have moved it.  Publishing ``a2dp`` (or the acquisition's
    ``microphone``) after a failed restore would lie about what the operator
    can expect, so read it back instead.
    """
    try:
        address = bt_audio.DeviceAddress.require(mac)
        card = bt_audio.card_profile_state(address.underscores)
    except Exception:
        return "unknown"
    return card.mode if card is not None else "unknown"


class HeadsetProfileService:
    """Own profile transitions on the bridge loop, and fence monitor threads.

    Lock order: SourceClient endpoint -> capture -> this async lock. Automatic
    Player starts also take this async lock. Blocking configuration takes only
    _audio_lock, then reads _view_lock; never wait for either on the event loop.
    Ownership is published before draining existing configuration work.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._audio_lock = threading.RLock()
        self._view_lock = threading.Lock()
        self._owners: dict[str, str] = {}
        self._resume: dict[str, bool] = {}

    def _phase(self, mac: str) -> str | None:
        with self._view_lock:
            return self._owners.get(mac.upper())

    def _publish(self, mac: str, phase: str | None) -> None:
        with self._view_lock:
            if phase is None:
                self._owners.pop(mac, None)
            else:
                self._owners[mac] = phase

    def active(self, mac: str) -> bool:
        return self._phase(mac) == "microphone"

    def blocked(self, mac: str) -> bool:
        """Automatic output operations must stay out, including after failure."""
        return self._phase(mac) is not None

    def capture_owned(self, mac: str) -> bool:
        """Explicit recovery is forbidden until capture and its transition finish."""
        return self._phase(mac) in {"acquiring", "microphone", "restoring"}

    def restore_failed(self) -> bool:
        """Is any headset stuck after a failed A2DP restore, awaiting retry?

        The bridge status uses this to keep the source error visible while the
        feed itself is off: the operator turned capture off, the hand-off back
        to A2DP failed, and without the block nothing would say why the speaker
        is silent.
        """
        with self._view_lock:
            return "failed" in self._owners.values()

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
            self._publish(wanted, "acquiring")
            client = _client_for_mac(wanted)
            stopped = False
            try:
                await asyncio.to_thread(self._drain_audio)
                address = bt_audio.DeviceAddress.require(wanted)
                card = await asyncio.to_thread(bt_audio.card_profile_state, address.underscores)
                if card is None or not card.hfp_available:
                    self._publish(wanted, None)
                    return False
                hfp = _pick_hfp(card.profiles)
                if hfp is None:
                    self._publish(wanted, None)
                    return False
                was_running = bool(getattr(client, "is_running", lambda: False)())
                if client is not None and was_running:
                    await client.stop_sendspin()
                stopped = True
                self._resume[wanted] = was_running
                acquired = await asyncio.to_thread(set_card_profile, f"bluez_card.{address.underscores}", hfp)
                self._publish(wanted, "microphone")
                if acquired:
                    manager = getattr(client, "bt_manager", None)
                    if manager is not None:
                        manager.audio_sink_ready = False
                        manager.profile_mode = "microphone"
                    _set_status(client, profile_mode="microphone", a2dp_available=card.a2dp_available)
            except Exception:
                if not stopped:
                    self._publish(wanted, None)
                logger.warning("Headset %s: microphone acquisition failed", wanted, exc_info=True)
        if not acquired and stopped:
            await self.release_microphone(wanted)
        return acquired

    async def _restore_locked(self, wanted: str) -> bool:
        from sendspin_bridge.services.source import state
        from sendspin_bridge.services.source.client import SourceError

        self._publish(wanted, "restoring")
        client = _client_for_mac(wanted)
        manager = getattr(client, "bt_manager", None)
        try:
            evidence = await asyncio.to_thread(self._restore_audio, wanted, manager)
        except Exception:
            evidence = None
        if evidence is None:
            self._publish(wanted, "failed")
            profile_mode = await asyncio.to_thread(_profile_mode_now, wanted)
            if manager is not None:
                manager.audio_sink_ready = False
                manager.a2dp_available = False
                manager.profile_mode = profile_mode
            if client is not None:
                # HFP removed this sink: keeping its name makes the volume
                # slider, transfer readiness and the health view claim a sink
                # that is no longer there.
                client.bluetooth_sink_name = None
            # These are the facts the existing a2dp_unavailable health path and
            # the SPA's retry control already read. No new health code is
            # needed for the operator's manual recovery to be reachable.
            _set_status(
                client,
                profile_mode=profile_mode,
                a2dp_available=False,
                sink_missing=True,
                playing=False,
            )
            state.publish(error="headset_a2dp_restore_failed", streaming=False)
            raise SourceError("headset_a2dp_restore_failed", "Could not restore the headset A2DP profile and output.")
        card, sink = evidence
        if manager is not None:
            manager.audio_sink_ready = True
            manager.a2dp_available = card.a2dp_available
            manager.hfp_available = card.hfp_available
            manager.profile_mode = card.mode
        if client is not None:
            client.bluetooth_sink_name = sink
        _set_status(client, profile_mode="a2dp", a2dp_available=True, sink_missing=False)
        _clear_missing_sink_error(client)
        self._publish(wanted, None)
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
                card = bt_audio.card_profile_state(address.underscores)
                profile = _pick_a2dp(card.profiles) if card else None
                if profile is None or not set_card_profile(f"bluez_card.{address.underscores}", profile):
                    return None
            card = bt_audio.card_profile_state(address.underscores)
            names = {entry["name"] for entry in bt_audio.list_sinks() or ()}
            sink = next((name for name in sink_name_candidates(address) if name in names), None)
            if card is None or card.mode != "a2dp" or sink is None:
                return None
            return card, sink

    async def release_microphone(self, mac: str) -> None:
        await _complete(self._release_microphone(mac))

    async def _release_microphone(self, mac: str) -> None:
        wanted = mac.upper()
        async with self._lock:
            if self._phase(wanted) not in {"microphone", "acquiring"}:
                return
            resume = await self._restore_locked(wanted)
        if resume and (client := _client_for_mac(wanted)) is not None:
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
            if self._phase(wanted) == "failed":
                resume = await self._restore_locked(wanted)
            else:
                client = _client_for_mac(wanted)
                manager = getattr(client, "bt_manager", None)
                if manager is None or not manager.connected:
                    return False
                if not await asyncio.to_thread(manager.configure_bluetooth_audio):
                    return False
                if client is not None:
                    client._sync_bt_profile_status()
                resume = True
        if resume and (client := _client_for_mac(wanted)) is not None:
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
