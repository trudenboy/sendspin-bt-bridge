"""Hardware use cases: the read model joined with the configuration.

Two things are added here and nowhere else: the player a bond backs and the
Home Assistant area an adapter is mapped to.  Both come from ``load_config()``
in a worker thread — the reader is blocking, and a request handler is not where
a config migration should run.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sendspin_bridge.application.errors import not_found, unavailable
from sendspin_bridge.application.models.hardware import (
    AddressForms,
    AreaRef,
    BtDeviceDetail,
    BtDeviceSummary,
    CapabilitySet,
    ControllerDetail,
    ControllerSummary,
    DegradedSection,
    HardwareSnapshot,
    RoleEvidence,
    RssiSummary,
)
from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.vendor_map import vendor_from_modalias
from sendspin_bridge.config import load_config
from sendspin_bridge.config.players import player_id_for_mac
from sendspin_bridge.services.hardware.inventory import (
    ControllerFacts,
    DeviceFacts,
    HardwareUnavailable,
    InventoryRead,
    read_inventory,
)

if TYPE_CHECKING:
    from sendspin_bridge.services.hardware.capabilities import Capabilities

_BLUETOOTH_BACKEND = "bluetooth_a2dp"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def _inventory() -> InventoryRead:
    try:
        return await asyncio.to_thread(read_inventory)
    except HardwareUnavailable as exc:
        raise unavailable(str(exc), code="hardware_unavailable") from exc


async def _config() -> dict[str, Any]:
    return await asyncio.to_thread(load_config)


async def _read() -> tuple[InventoryRead, dict[str, Any]]:
    return await asyncio.gather(_inventory(), _config())


# ---------------------------------------------------------------------------
# The config join
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    address = DeviceAddress.parse(value)
    return address.colons if address is not None else ""


def _config_view(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, AreaRef]]:
    """``{mac: player_id}`` and ``{mac: area}`` from the loaded config."""
    players: dict[str, str] = {}
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if not isinstance(backend, dict) or backend.get("type") != _BLUETOOTH_BACKEND:
            continue
        mac = _canonical(backend.get("mac"))
        if not mac:
            continue
        players[mac] = str(player.get("id") or player_id_for_mac(mac))

    areas: dict[str, AreaRef] = {}
    raw_areas = config.get("HA_ADAPTER_AREA_MAP")
    if isinstance(raw_areas, dict):
        for raw_mac, entry in raw_areas.items():
            mac = _canonical(raw_mac)
            if not mac or not isinstance(entry, dict):
                continue
            area_id = str(entry.get("area_id") or "").strip()
            if not area_id:
                continue
            raw_name = entry.get("area_name")
            areas[mac] = AreaRef(id=area_id, name=str(raw_name) if raw_name else None)
    return players, areas


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _capabilities(capabilities: Capabilities) -> CapabilitySet:
    return CapabilitySet(
        bluetooth=capabilities.bluetooth.available,
        dbus=capabilities.dbus.available,
        audio_server=capabilities.audio_server,
        capture=capabilities.capture.available,
        default_sink=capabilities.default_sink.available,
        reasons=capabilities.reasons,
    )


def _rssi(facts: DeviceFacts) -> RssiSummary | None:
    if facts.rssi_delta_db is None:
        return None
    return RssiSummary(delta_db=facts.rssi_delta_db, at=facts.rssi_at)


def _device_summary(facts: DeviceFacts, players: dict[str, str]) -> BtDeviceSummary:
    return BtDeviceSummary(
        mac=facts.mac,
        name=facts.name,
        alias=facts.alias,
        paired=facts.paired,
        bonded=facts.bonded,
        trusted=facts.trusted,
        connected=facts.connected,
        controller_macs=list(facts.controller_macs),
        battery_pct=facts.battery_pct,
        rssi=_rssi(facts),
        # The transport's codec and delay are E2's audio half; the role evidence
        # already carries the one transport fact this stage consumes.
        transport=None,
        roles=[RoleEvidence(role=role.role, evidence=role.evidence) for role in facts.roles],
        player_id=players.get(facts.mac),
        # The source feed is E3; until then no device is the streaming phone.
        source_phone=False,
    )


def _area(facts: ControllerFacts, areas: dict[str, AreaRef]) -> AreaRef | None:
    return areas.get(facts.mac)


def _controller_summary(facts: ControllerFacts, areas: dict[str, AreaRef]) -> ControllerSummary:
    return ControllerSummary(
        id=facts.id,
        id_synthetic=facts.id_synthetic,
        mac=facts.mac,
        name=facts.name,
        alias=facts.alias,
        powered=facts.powered,
        present=facts.present,
        class_of_device=facts.class_of_device,
        area=_area(facts, areas),
        device_count=len(facts.device_macs),
    )


def _address_forms(mac: str) -> AddressForms:
    address = DeviceAddress.parse(mac)
    if address is None:  # the reader only produces canonical addresses
        return AddressForms(colons=mac, underscores=mac, bare=mac)
    return AddressForms(colons=address.colons, underscores=address.underscores, bare=address.bare)


def _degraded(read: InventoryRead) -> list[DegradedSection]:
    return [
        DegradedSection(section=section.section, code=section.code, detail=section.detail) for section in read.degraded
    ]


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


async def snapshot() -> HardwareSnapshot:
    """One aggregate read of the physical layer."""
    read, config = await _read()
    players, areas = _config_view(config)
    return HardwareSnapshot(
        as_of=read.as_of,
        stale_after_s=read.stale_after_s,
        stale=read.stale,
        capabilities=_capabilities(read.capabilities),
        controllers=[_controller_summary(facts, areas) for facts in read.controllers],
        bluetooth_devices=[_device_summary(facts, players) for facts in read.devices],
        degraded=_degraded(read),
    )


def _find_controller(controllers: tuple[ControllerFacts, ...], controller_id: str) -> ControllerFacts | None:
    wanted = (controller_id or "").strip()
    wanted_mac = _canonical(wanted)
    for facts in controllers:
        if facts.id.lower() == wanted.lower() or (wanted_mac and facts.mac == wanted_mac):
            return facts
    return None


async def controller(controller_id: str) -> ControllerDetail:
    """One adapter with its bonds, by ``hciN`` or by controller MAC."""
    read, config = await _read()
    facts = _find_controller(read.controllers, controller_id)
    if facts is None:
        raise not_found(f"Unknown controller: {controller_id}", code="entity_not_found")
    players, areas = _config_view(config)
    by_mac = {device.mac: device for device in read.devices}
    return ControllerDetail(
        **_controller_summary(facts, areas).model_dump(),
        devices=[_device_summary(by_mac[mac], players) for mac in facts.device_macs if mac in by_mac],
        address_forms=_address_forms(facts.mac),
    )


async def bluetooth_device(mac: str) -> BtDeviceDetail:
    """One Bluetooth device with the detail-only fields."""
    address = DeviceAddress.parse(mac)
    if address is None:
        raise not_found(f"Unknown Bluetooth device: {mac}", code="entity_not_found")
    read, config = await _read()
    facts = next((device for device in read.devices if device.mac == address.colons), None)
    if facts is None:
        raise not_found(f"Unknown Bluetooth device: {mac}", code="entity_not_found")
    players, _areas = _config_view(config)
    summary = _device_summary(facts, players)
    return BtDeviceDetail(
        **summary.model_dump(),
        uuids=list(facts.uuids),
        services_resolved=facts.services_resolved,
        modalias=facts.modalias,
        manufacturer=vendor_from_modalias(facts.modalias),
        bonds=list(facts.controller_macs),
    )
