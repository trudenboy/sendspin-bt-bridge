"""Pure Source feed projection from one runtime snapshot and one hardware read.

No runtime singleton, config reader, BlueZ or audio calls belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.uuids import A2DP_SOURCE_UUID
from sendspin_bridge.services.source import inputs as source_inputs
from sendspin_bridge.services.source import state as source_state

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sendspin_bridge.services.hardware.inventory import EndpointFacts, InventoryRead


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """The source's state, for the bridge status and the hardware read model."""

    enabled: bool
    state: str
    phone: str | None
    phone_mac: str | None
    paired_phone_mac: str | None
    streaming: bool
    server: str | None
    ma_connected: bool
    ma_paired: bool
    ma_pairing_required: bool
    ma_pairing_code: str | None
    ma_pairing_seconds_left: int
    ma_error: str | None
    pairing_seconds_left: int
    pairing_adapters: tuple[str, ...]
    devices: tuple[source_state.SourceDevice, ...]
    target_player: str | None
    target_error: str | None
    active_input: str | None
    inputs: tuple[source_inputs.SourceInput, ...]
    error: str | None

    def as_status_dict(self) -> dict[str, Any]:
        """The bridge-status shape (the keys ``SendspinSourceStatus`` reads)."""
        return {
            "enabled": self.enabled,
            "state": self.state,
            "phone": self.phone,
            "phone_mac": self.phone_mac,
            "paired_phone_mac": self.paired_phone_mac,
            "streaming": self.streaming,
            "server": self.server,
            "ma_connected": self.ma_connected,
            "ma_paired": self.ma_paired,
            "ma_pairing_required": self.ma_pairing_required,
            "ma_pairing_code": self.ma_pairing_code,
            "ma_pairing_seconds_left": self.ma_pairing_seconds_left,
            "ma_error": self.ma_error,
            "pairing_seconds_left": self.pairing_seconds_left,
            "pairing_adapters": list(self.pairing_adapters),
            "devices": [device.as_dict() for device in self.devices],
            "target_player": self.target_player,
            "target_error": self.target_error,
            "active_input": self.active_input,
            "inputs": [entry.as_dict() for entry in self.inputs],
            "error": self.error,
        }


def _configured_player_macs(config: dict[str, Any]) -> set[str]:
    """MACs the bridge already plays to; they are not source devices."""
    macs: set[str] = set()
    for player in config.get("players") or ():
        if not isinstance(player, dict):
            continue
        backend = player.get("backend")
        if isinstance(backend, dict) and backend.get("type") == "bluetooth_a2dp":
            address = DeviceAddress.parse(backend.get("mac"))
            mac = address.colons if address else ""
            if mac:
                macs.add(mac)
    return macs


def _canonical(value: object) -> str | None:
    address = DeviceAddress.parse(value)
    return address.colons if address else None


def devices_from_read(
    config: dict[str, Any],
    current: source_state.SourceState,
    inventory: InventoryRead | None,
) -> tuple[source_state.SourceDevice, ...]:
    """Project paired source bonds; apply current config after the shared cache."""
    excluded = _configured_player_macs(config)
    active = _canonical(current.phone_mac)
    found: dict[str, source_state.SourceDevice] = {}
    for facts in inventory.devices if inventory is not None else ():
        mac = _canonical(facts.mac)
        if mac is None or mac in excluded or A2DP_SOURCE_UUID not in facts.uuids:
            continue
        if facts.paired is False and facts.bonded is not True:
            continue
        found[mac] = source_state.SourceDevice(mac, facts.alias or facts.name or mac, facts.connected, mac == active)
    paired = _canonical(current.paired_phone_mac)
    if paired and paired not in found and paired not in excluded:
        found[paired] = source_state.SourceDevice(paired, paired)
    return tuple(sorted(found.values(), key=lambda device: (device.name.lower(), device.mac)))


def audio_sources_from_read(inventory: InventoryRead | None) -> tuple[EndpointFacts, ...] | None:
    """None means unknown; an empty tuple means the source list answered empty.

    Unavailable source lists keep saved local profiles selectable; capture owns
    the eventual input_missing error. Other unavailable lists do not hide a
    successfully read source list.
    """
    if inventory is None or inventory.capabilities.audio_server == "none":
        return None
    for reason in inventory.audio.unavailable:
        if reason.startswith("sources:") or reason.split(":", 1)[0] not in {"cards", "sinks", "sources"}:
            return None
    return inventory.audio.sources


def effective_active(current: source_state.SourceState) -> str | None:
    raw = current.active_input
    if raw is None and current.phone_mac:
        return source_inputs.bluetooth_id(_canonical(current.phone_mac) or current.phone_mac)
    parsed = source_inputs.parse_source_id(raw) if raw else None
    if parsed is not None and parsed[0] in {"bluetooth", "headset_microphone"}:
        mac = _canonical(parsed[1])
        if mac:
            return source_inputs.bluetooth_id(mac) if parsed[0] == "bluetooth" else source_inputs.headset_mic_id(mac)
    return None if raw in (None, "none") else raw


def active_id(config: dict[str, Any], current: source_state.SourceState) -> str | None:
    if current.enabled:
        return effective_active(current)
    raw = str(config.get("SENDSPIN_SOURCE_ACTIVE") or "").strip() or None
    return None if raw in (None, "none") else raw


def project_status(
    config: dict[str, Any],
    current: source_state.SourceState,
    inventory: InventoryRead | None,
    *,
    headsets: Iterable[dict[str, Any]] = (),
) -> SourceStatus:
    """The source's state, for the bridge status and the hardware read model."""
    devices = devices_from_read(config, current, inventory)
    active = active_id(config, current)
    return SourceStatus(
        enabled=current.enabled,
        state=current.state,
        phone=current.phone,
        phone_mac=current.phone_mac,
        paired_phone_mac=current.paired_phone_mac,
        streaming=current.streaming,
        server=current.server,
        ma_connected=current.ma_connected,
        ma_paired=current.ma_paired,
        ma_pairing_required=current.enabled and not current.ma_paired,
        ma_pairing_code=current.ma_pairing_code,
        ma_pairing_seconds_left=current.ma_pairing_seconds_left(),
        ma_error=current.ma_error,
        pairing_seconds_left=current.pairing_seconds_left(),
        pairing_adapters=tuple(current.pairing_adapters),
        devices=devices if current.enabled or current.pairing_until or current.paired_phone_mac else (),
        target_player=current.target_player,
        target_error=current.target_error,
        active_input=active,
        inputs=tuple(
            source_inputs.build_inputs(
                config,
                active_input=active,
                bluetooth=devices,
                audio_sources=audio_sources_from_read(inventory),
                headsets=headsets,
            )
        ),
        error=current.error,
    )
