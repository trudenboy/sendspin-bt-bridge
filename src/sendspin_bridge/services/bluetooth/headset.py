"""Headset capabilities and the A2DP ⇄ HFP profile hand-off for a microphone.

The bridge is a music player on A2DP. A headset's microphone exists only on the
HFP/HSP profile, which cannot coexist with A2DP on the same link. This module is
the one place that gives the card to HFP for a microphone and takes it back.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import sendspin_bridge.bluetooth.audio as bt_audio
from sendspin_bridge.services.audio.pulse import set_card_profile

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


class HeadsetProfileService:
    """The single owner of the A2DP/HFP hand-off for microphone capture."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: set[str] = set()
        self._resume: dict[str, bool] = {}

    def active(self, mac: str) -> bool:
        return mac.upper() in self._active

    async def acquire_microphone(self, mac: str) -> bool:
        wanted = mac.upper()
        async with self._lock:
            if wanted in self._active:
                return True
            from sendspin_bridge.config import load_config

            config = load_config() or {}
            player = next(
                (
                    entry
                    for entry in config.get("players") or ()
                    if isinstance(entry, dict) and str((entry.get("backend") or {}).get("mac") or "").upper() == wanted
                ),
                None,
            )
            if player is not None and player.get("allow_call_mode") is False:
                return False
            address = bt_audio.DeviceAddress.require(wanted)
            card = bt_audio.card_profile_state(address.underscores)
            if card is None or not card.hfp_available:
                return False
            hfp = _pick_hfp(card.profiles)
            if hfp is None:
                return False
            client = _client_for_mac(wanted)
            was_running = bool(getattr(client, "is_running", lambda: False)())
            self._resume[wanted] = was_running or bool(
                getattr(client, "status", {}).get("playing", False) if client is not None else False
            )
            if client is not None and was_running:
                try:
                    await client.stop_sendspin()
                except Exception:
                    logger.warning("Headset %s: could not stop the player for microphone mode", wanted, exc_info=True)
            ok = await asyncio.to_thread(set_card_profile, f"bluez_card.{address.underscores}", hfp)
            if not ok:
                self._resume.pop(wanted, None)
                if client is not None and was_running:
                    _scheduled_start(client)
                return False
            self._active.add(wanted)
            _set_status(client, profile_mode="microphone", a2dp_available=card.a2dp_available)
            logger.info("Headset %s: switched card to %s for microphone capture", wanted, hfp)
            return True

    async def release_microphone(self, mac: str) -> None:
        wanted = mac.upper()
        async with self._lock:
            if wanted not in self._active:
                return
            self._active.discard(wanted)
            resume = self._resume.pop(wanted, False)
            address = bt_audio.DeviceAddress.require(wanted)
            card = bt_audio.card_profile_state(address.underscores)
            a2dp = _pick_a2dp(card.profiles) if card is not None else None
            client = _client_for_mac(wanted)
            if a2dp is not None:
                ok = await asyncio.to_thread(set_card_profile, f"bluez_card.{address.underscores}", a2dp)
                if not ok:
                    logger.warning("Headset %s: could not restore the A2DP profile", wanted)
            _set_status(client, profile_mode="a2dp" if a2dp else "unknown")
            if resume and client is not None:
                _scheduled_start(client)


def _scheduled_start(client: Any) -> None:
    """Start the player from a worker thread without owning the bridge loop."""
    try:
        from sendspin_bridge.bridge.loop_scheduling import schedule_on_bridge_loop

        schedule_on_bridge_loop(client.start_sendspin(), description="headset profile restore")
    except Exception:
        logger.warning("Headset: could not schedule player restart", exc_info=True)


_service: HeadsetProfileService | None = None


def get_headset_profiles() -> HeadsetProfileService:
    global _service
    if _service is None:
        _service = HeadsetProfileService()
    return _service


# -- source-input discovery ---------------------------------------------------


def headset_inputs(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Configured Bluetooth players whose running client reports an HFP profile.

    Reads the client's already-published status, never PulseAudio: the source
    status is built on the bridge loop and must not block on a subprocess.
    """
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot

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
        if status.get("hfp_available") is not True:
            continue
        caps = classify(
            hfp_available=True,
            a2dp_available=status.get("a2dp_available") is not False,
            form_factor=None,
        )
        found.append(
            {
                "mac": mac,
                "name": str(player.get("name") or mac),
                "kind": caps.kind,
                "a2dp_available": caps.a2dp_available,
                "hfp_available": True,
            }
        )
    return found
