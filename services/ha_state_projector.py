"""Projects ``BridgeSnapshot`` into per-HA-entity state dicts.

Pure function over the canonical read model (``services/status_snapshot.py``).
Both transports (MQTT publisher and custom_component coordinator) call
``project_snapshot()`` and then turn the result into wire-level payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from services.ha_entity_model import (
    BRIDGE_ENTITIES,
    DEVICE_ENTITIES,
    EntityKind,
    EntitySpec,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from services.status_snapshot import BridgeSnapshot, DeviceSnapshot


@dataclass(frozen=True)
class EntityState:
    """One entity's state at a point in time.

    ``value`` is intentionally untyped — JSON-serialisable scalar / bool /
    string / int / float / None.  The transport layer chooses how to encode
    it (e.g. ``ON``/``OFF`` for switches, integer for sensors).
    """

    value: Any
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class HAStateProjection:
    """Snapshot of every HA entity the bridge currently exposes.

    Two availability dimensions per device — see
    ``EntitySpec.availability_class`` for which entity uses which:

    * ``availability_config[pid] == True`` — device is in the bridge's
      fleet (configured, whether enabled or disabled, whether the BT
      link is up or in standby).  Drives availability for ``config`` and
      ``cumulative`` entities (toggles, command buttons, counters).
    * ``availability_runtime[pid] == True`` — BT link is up AND the
      daemon subprocess is alive.  Drives availability for ``runtime``
      entities (live RSSI / battery / audio_format / audio_streaming).

    The legacy ``availability`` field is kept as an alias for
    ``availability_runtime`` so callers from before v2.65.0-rc.4 keep
    working.
    """

    devices: dict[str, dict[str, EntityState]]  # player_id → object_id → state
    bridge: dict[str, EntityState]  # object_id → state
    availability_runtime: dict[str, bool] = field(default_factory=dict)
    availability_config: dict[str, bool] = field(default_factory=dict)
    bridge_available: bool = True
    # Stable per-device metadata required to render device-registry blocks
    # (HA Device card identity, MQTT topic prefixes, etc.).  Indexed by
    # player_id alongside ``devices``.
    device_meta: dict[str, DeviceMeta] = field(default_factory=dict)
    bridge_meta: BridgeMeta | None = None
    # Lifecycle state per device: "active" / "standby" / "disabled".
    # Populated by ``project_snapshot`` from BT link + bt_standby +
    # disabled_devices fan-out.  Useful for HA dashboards but not
    # exposed as its own entity (would be redundant with the
    # bt_standby + enabled signals).
    device_lifecycle: dict[str, str] = field(default_factory=dict)

    @property
    def availability(self) -> dict[str, bool]:
        """Legacy alias — runtime availability (BT link up).

        Kept for backwards compat with code written before the
        config/runtime split shipped in v2.65.0-rc.4.
        """
        return self.availability_runtime

    def to_json(self) -> dict[str, Any]:
        return {
            "devices": {
                pid: {oid: {"value": s.value, "attrs": s.attrs} for oid, s in entities.items()}
                for pid, entities in self.devices.items()
            },
            "bridge": {oid: {"value": s.value, "attrs": s.attrs} for oid, s in self.bridge.items()},
            "availability_runtime": dict(self.availability_runtime),
            "availability_config": dict(self.availability_config),
            # Legacy field — drop in a future major; kept for compat with
            # any custom_component coordinator running an older release.
            "availability": dict(self.availability_runtime),
            "bridge_available": self.bridge_available,
            "device_meta": {pid: m.to_dict() for pid, m in self.device_meta.items()},
            "bridge_meta": self.bridge_meta.to_dict() if self.bridge_meta else None,
            "device_lifecycle": dict(self.device_lifecycle),
        }


@dataclass(frozen=True)
class DeviceMeta:
    """Identity fields used to register a device in HA's device registry.

    ``mac`` is the speaker's Bluetooth MAC (lowercased).  Per the plan, every
    per-device entity declares ``connections=[("bluetooth", mac)]`` so HA
    merges our diagnostics into the same device card MA already created
    via its ``media_player.<player_name>`` entity.
    """

    player_id: str
    mac: str
    player_name: str
    adapter_name: str | None
    room_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "mac": self.mac,
            "player_name": self.player_name,
            "adapter_name": self.adapter_name,
            "room_name": self.room_name,
        }


@dataclass(frozen=True)
class BridgeMeta:
    """Identity for the bridge-level HA Device card."""

    bridge_id: str
    bridge_name: str
    version: str
    web_url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_id": self.bridge_id,
            "bridge_name": self.bridge_name,
            "version": self.version,
            "web_url": self.web_url,
        }


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _project_one_device(
    device_dict: dict[str, Any],
    bridge_dict: dict[str, Any],
    specs: Iterable[EntitySpec] = DEVICE_ENTITIES,
) -> dict[str, EntityState]:
    out: dict[str, EntityState] = {}
    for spec in specs:
        if spec.extractor is None:
            continue
        # Device extractors are typed ``(device_dict, bridge_dict) -> Any``;
        # cast away mypy's union-of-arities confusion.
        extractor: Any = spec.extractor
        try:
            value = extractor(device_dict, bridge_dict)
        except Exception:  # pragma: no cover — extractor bugs surface as None
            value = None
        attrs: dict[str, Any] = {}
        if spec.unit:
            attrs["unit_of_measurement"] = spec.unit
        out[spec.object_id] = EntityState(value=value, attrs=attrs)
    return out


def _project_bridge(
    bridge_dict: dict[str, Any],
    specs: Iterable[EntitySpec] = BRIDGE_ENTITIES,
) -> dict[str, EntityState]:
    out: dict[str, EntityState] = {}
    for spec in specs:
        if spec.extractor is None:
            continue
        extractor: Any = spec.extractor
        try:
            value = extractor(bridge_dict)
        except Exception:  # pragma: no cover
            value = None
        attrs: dict[str, Any] = {}
        if spec.kind is EntityKind.UPDATE:
            update_info = bridge_dict.get("update_available") or {}
            if isinstance(update_info, dict):
                latest = update_info.get("latest")
                installed = update_info.get("installed") or bridge_dict.get("version")
                if latest:
                    attrs["latest_version"] = str(latest)
                if installed:
                    attrs["installed_version"] = str(installed)
        out[spec.object_id] = EntityState(value=value, attrs=attrs)
    return out


def _device_meta_from_snapshot(device: DeviceSnapshot) -> DeviceMeta:
    return DeviceMeta(
        player_id=str(device.player_id or ""),
        mac=str(device.bluetooth_mac or "").lower(),
        player_name=str(device.player_name or ""),
        adapter_name=device.bluetooth_adapter_name or device.bluetooth_adapter or None,
        room_name=device.room_name or None,
    )


def _disabled_device_meta(disabled_dict: dict[str, Any]) -> DeviceMeta | None:
    """Build a ``DeviceMeta`` from a ``disabled_devices`` entry.

    The entry ships from ``bridge_orchestrator.initialize_devices`` and
    has at minimum ``mac`` + ``player_name`` (see PR #214 wiring).  Skip
    silently if the MAC is missing — without it we can't derive a stable
    ``player_id`` and HA can't merge with MA's device card.
    """
    mac = str(disabled_dict.get("mac") or "").strip().upper()
    if not mac:
        return None
    # Lazy import — keeps ha_state_projector import-cycle-free.
    from config import _player_id_from_mac

    player_id = _player_id_from_mac(mac)
    if not player_id:
        return None
    return DeviceMeta(
        player_id=player_id,
        mac=mac.lower(),
        player_name=str(disabled_dict.get("player_name") or "").strip(),
        adapter_name=str(disabled_dict.get("adapter") or "").strip() or None,
        room_name=str(disabled_dict.get("room_name") or "").strip() or None,
    )


def _project_disabled_device(
    disabled_dict: dict[str, Any],
    bridge_dict: dict[str, Any],
) -> dict[str, EntityState]:
    """Synthesize entity states for a disabled (out-of-fleet-runtime) device.

    The device has no live client, so all runtime fields default to
    safe falsy values.  The ``enabled`` switch shows ``False`` so HA
    operators can flip it back on, which the dispatcher handles via
    ``bt_commands.apply_device_enabled``.
    """
    synthetic = {
        "enabled": False,
        "bluetooth_connected": False,
        "audio_streaming": False,
        "bt_management_enabled": True,
        "bt_standby": False,
        "bt_power_save": False,
        "reanchoring": False,
        "reconnecting": False,
        "idle_mode": str(disabled_dict.get("idle_mode") or "default"),
        "keep_alive_method": str(disabled_dict.get("keep_alive_method") or "infrasound"),
        "static_delay_ms": int(disabled_dict.get("static_delay_ms") or 0),
        "power_save_delay_minutes": int(disabled_dict.get("power_save_delay_minutes") or 1),
        # Diagnostic keys deliberately absent so extractors return None.
    }
    return _project_one_device(synthetic, bridge_dict)


def _bridge_dict_from_snapshot(
    snapshot: BridgeSnapshot, *, runtime_extras: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Flatten the bridge-level slice of ``BridgeSnapshot`` to the dict shape
    bridge extractors expect."""
    payload: dict[str, Any] = {}
    payload["ma_connected"] = bool(snapshot.ma_connected)
    if snapshot.startup_progress:
        payload["startup_progress"] = snapshot.startup_progress.to_dict()
    payload["runtime_mode"] = snapshot.runtime_mode
    if snapshot.update_available:
        payload["update_available"] = dict(snapshot.update_available)
    if snapshot.devices:
        payload["version"] = snapshot.devices[0].version
    elif runtime_extras and runtime_extras.get("version"):
        payload["version"] = runtime_extras["version"]
    if runtime_extras:
        for key, value in runtime_extras.items():
            payload.setdefault(key, value)
    return payload


def project_snapshot(
    snapshot: BridgeSnapshot,
    *,
    bridge_id: str,
    bridge_name: str,
    web_url: str | None = None,
    runtime_extras: dict[str, Any] | None = None,
) -> HAStateProjection:
    """Build the full HA entity-state projection from a bridge snapshot.

    Iterates *both* active devices (``snapshot.devices``) and disabled
    devices (``snapshot.disabled_devices``) so every member of the
    bridge fleet appears in HA — operators can toggle a disabled device
    back on from the HA ``switch.<player>_enabled`` entity, which the
    bridge handles via ``bt_commands.apply_device_enabled``.

    Per-device availability is split into two channels:

    * ``availability_config[pid] = True`` for every player_id present
      anywhere in the snapshot (active or disabled).  Drives the
      always-online entities (``enabled`` switch, ``idle_mode``
      select, command buttons, cumulative counters).
    * ``availability_runtime[pid] = bluetooth_connected`` for active
      devices, ``False`` for disabled.  Drives live-data entities
      (RSSI, battery, audio_streaming).

    ``bridge_id`` and ``bridge_name`` are passed in (rather than derived
    from snapshot) because the snapshot doesn't carry the resolved
    bridge name — that lives in config / runtime state.
    """
    bridge_dict = _bridge_dict_from_snapshot(snapshot, runtime_extras=runtime_extras)

    devices_state: dict[str, dict[str, EntityState]] = {}
    availability_runtime: dict[str, bool] = {}
    availability_config: dict[str, bool] = {}
    device_meta: dict[str, DeviceMeta] = {}
    device_lifecycle: dict[str, str] = {}

    # Active devices (live clients) ----------------------------------------
    for device in snapshot.devices:
        player_id = str(device.player_id or "").strip()
        if not player_id:
            # Without a stable identity we cannot register HA entities.
            continue
        device_dict = device.to_dict()
        devices_state[player_id] = _project_one_device(device_dict, bridge_dict)
        availability_runtime[player_id] = bool(device.bluetooth_connected)
        availability_config[player_id] = True
        device_meta[player_id] = _device_meta_from_snapshot(device)
        # Lifecycle: standby has its own bt_standby flag set by the
        # daemon when entering the auto-disconnect / power-park state.
        if bool(device_dict.get("bt_standby")):
            device_lifecycle[player_id] = "standby"
        else:
            device_lifecycle[player_id] = "active"

    # Disabled devices — register them in HA so the operator can flip
    # ``enabled`` back on from a switch.  Skip if they collide with an
    # active player_id (defensive — shouldn't happen but DeviceRegistry
    # treats the two lists as disjoint).
    for disabled_dict in snapshot.disabled_devices or []:
        meta = _disabled_device_meta(disabled_dict)
        if meta is None or meta.player_id in devices_state:
            continue
        devices_state[meta.player_id] = _project_disabled_device(disabled_dict, bridge_dict)
        availability_runtime[meta.player_id] = False
        availability_config[meta.player_id] = True
        device_meta[meta.player_id] = meta
        device_lifecycle[meta.player_id] = "disabled"

    return HAStateProjection(
        devices=devices_state,
        bridge=_project_bridge(bridge_dict),
        availability_runtime=availability_runtime,
        availability_config=availability_config,
        bridge_available=True,
        device_meta=device_meta,
        bridge_meta=BridgeMeta(
            bridge_id=bridge_id,
            bridge_name=bridge_name,
            version=str(bridge_dict.get("version") or ""),
            web_url=web_url,
        ),
        device_lifecycle=device_lifecycle,
    )


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateDelta:
    """Subset of entities whose value or attrs changed between projections.

    ``availability_changed`` is the legacy alias for runtime availability
    transitions (kept for backwards compat with rc.1–rc.3 callers).
    ``availability_runtime_changed`` and ``availability_config_changed``
    are the canonical fields.
    """

    devices: dict[str, dict[str, EntityState]]
    bridge: dict[str, EntityState]
    availability_runtime_changed: dict[str, bool] = field(default_factory=dict)
    availability_config_changed: dict[str, bool] = field(default_factory=dict)
    bridge_available_changed: bool | None = None
    devices_added: tuple[str, ...] = ()
    devices_removed: tuple[str, ...] = ()

    @property
    def availability_changed(self) -> dict[str, bool]:
        """Legacy alias for ``availability_runtime_changed``."""
        return self.availability_runtime_changed

    @property
    def is_empty(self) -> bool:
        return (
            not self.devices
            and not self.bridge
            and not self.availability_runtime_changed
            and not self.availability_config_changed
            and self.bridge_available_changed is None
            and not self.devices_added
            and not self.devices_removed
        )


def _entity_state_changed(prior: EntityState | None, current: EntityState) -> bool:
    if prior is None:
        return True
    if prior.value != current.value:
        return True
    return prior.attrs != current.attrs


def compute_delta(prior: HAStateProjection | None, current: HAStateProjection) -> StateDelta:
    """Return only the entities whose state changed between two projections.

    Used by the MQTT publisher (publishes one message per changed entity)
    and by the custom_component coordinator (applies local state updates
    without re-fetching the full snapshot every tick).
    """
    if prior is None:
        return StateDelta(
            devices={pid: dict(entities) for pid, entities in current.devices.items()},
            bridge=dict(current.bridge),
            availability_runtime_changed=dict(current.availability_runtime),
            availability_config_changed=dict(current.availability_config),
            bridge_available_changed=current.bridge_available if not current.bridge_available else None,
            devices_added=tuple(current.devices.keys()),
        )

    # Per-device deltas
    devices_diff: dict[str, dict[str, EntityState]] = {}
    for pid, current_entities in current.devices.items():
        prior_entities = prior.devices.get(pid, {})
        diff: dict[str, EntityState] = {}
        for oid, state in current_entities.items():
            if _entity_state_changed(prior_entities.get(oid), state):
                diff[oid] = state
        if diff:
            devices_diff[pid] = diff

    # Bridge-level deltas
    bridge_diff: dict[str, EntityState] = {}
    for oid, state in current.bridge.items():
        if _entity_state_changed(prior.bridge.get(oid), state):
            bridge_diff[oid] = state

    # Availability transitions — both channels.
    runtime_changed: dict[str, bool] = {}
    for pid, online in current.availability_runtime.items():
        if prior.availability_runtime.get(pid) != online:
            runtime_changed[pid] = online
    for pid in prior.availability_runtime:
        if pid not in current.availability_runtime:
            runtime_changed[pid] = False

    config_changed: dict[str, bool] = {}
    for pid, online in current.availability_config.items():
        if prior.availability_config.get(pid) != online:
            config_changed[pid] = online
    for pid in prior.availability_config:
        if pid not in current.availability_config:
            config_changed[pid] = False

    bridge_available_changed = current.bridge_available if prior.bridge_available != current.bridge_available else None

    devices_added = tuple(pid for pid in current.devices if pid not in prior.devices)
    devices_removed = tuple(pid for pid in prior.devices if pid not in current.devices)

    return StateDelta(
        devices=devices_diff,
        bridge=bridge_diff,
        availability_runtime_changed=runtime_changed,
        availability_config_changed=config_changed,
        bridge_available_changed=bridge_available_changed,
        devices_added=devices_added,
        devices_removed=devices_removed,
    )


__all__ = [
    "BridgeMeta",
    "DeviceMeta",
    "EntityState",
    "HAStateProjection",
    "StateDelta",
    "compute_delta",
    "project_snapshot",
]
