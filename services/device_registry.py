"""Canonical device inventory service for the current bridge runtime."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class DeviceRegistrySnapshot:
    """Thread-safe snapshot of the current device registry surfaces."""

    active_clients: list[Any] = field(default_factory=list)
    disabled_devices: list[dict[str, Any]] = field(default_factory=list)

    def find_client_by_player_name(self, player_name: str | None) -> Any | None:
        """Return the active client with the requested player name, if present."""
        if not player_name:
            return None
        return next(
            (client for client in self.active_clients if getattr(client, "player_name", None) == player_name),
            None,
        )

    def find_client_by_mac(self, mac: str | None) -> Any | None:
        """Return the active client with the requested BT MAC address, if present."""
        if not mac:
            return None
        return next(
            (
                client
                for client in self.active_clients
                if getattr(getattr(client, "bt_manager", None), "mac_address", None) == mac
            ),
            None,
        )

    def client_map_by_player_name(self) -> dict[str, Any]:
        """Index active clients by non-empty player name."""
        return {
            player_name: client
            for client in self.active_clients
            if (player_name := str(getattr(client, "player_name", "") or "").strip())
        }

    def client_map_by_mac(self) -> dict[str, Any]:
        """Index active clients by BT MAC address."""
        return {
            mac: client
            for client in self.active_clients
            if (mac := getattr(getattr(client, "bt_manager", None), "mac_address", None))
        }

    def released_clients(self) -> list[Any]:
        """Return active clients whose BT management is currently released/disabled."""
        return [client for client in self.active_clients if not getattr(client, "bt_management_enabled", True)]


class DeviceRegistry:
    """Own active and disabled device inventory for the bridge runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_clients: list[Any] = []
        self._disabled_devices: list[dict[str, Any]] = []

    def snapshot(self) -> DeviceRegistrySnapshot:
        """Return a stable snapshot of the current inventory."""
        with self._lock:
            return DeviceRegistrySnapshot(
                active_clients=list(self._active_clients),
                disabled_devices=list(self._disabled_devices),
            )

    def set_active_clients(self, active_clients: list[Any] | None) -> DeviceRegistrySnapshot:
        """Replace the active client inventory and return the updated snapshot."""
        with self._lock:
            self._active_clients = list(active_clients or [])
            return DeviceRegistrySnapshot(
                active_clients=list(self._active_clients),
                disabled_devices=list(self._disabled_devices),
            )

    def set_disabled_devices(self, disabled_devices: list[dict[str, Any]] | None) -> DeviceRegistrySnapshot:
        """Replace the disabled-device inventory and return the updated snapshot."""
        with self._lock:
            self._disabled_devices = list(disabled_devices or [])
            return DeviceRegistrySnapshot(
                active_clients=list(self._active_clients),
                disabled_devices=list(self._disabled_devices),
            )


_device_registry = DeviceRegistry()
_registry_listener_lock = threading.Lock()
_registry_listeners: list[Callable[[DeviceRegistrySnapshot], None]] = []


def register_registry_listener(listener: Callable[[DeviceRegistrySnapshot], None]) -> None:
    """Register a callback that receives fresh registry snapshots after writes."""
    with _registry_listener_lock:
        if listener not in _registry_listeners:
            _registry_listeners.append(listener)


def unregister_registry_listener(listener: Callable[[DeviceRegistrySnapshot], None]) -> None:
    """Remove a previously registered registry listener."""
    with _registry_listener_lock:
        if listener in _registry_listeners:
            _registry_listeners.remove(listener)


def _notify_registry_listeners(snapshot: DeviceRegistrySnapshot) -> None:
    with _registry_listener_lock:
        listeners = list(_registry_listeners)
    for listener in listeners:
        listener(snapshot)


def set_active_clients(active_clients: list[Any] | None) -> DeviceRegistrySnapshot:
    """Replace the active client inventory and notify listeners."""
    snapshot = _device_registry.set_active_clients(active_clients)
    _notify_registry_listeners(snapshot)
    return snapshot


def get_active_clients_snapshot() -> list[Any]:
    """Return a copy of the current active client inventory."""
    return get_device_registry_snapshot().active_clients


def set_disabled_devices(disabled_devices: list[dict[str, Any]] | None) -> DeviceRegistrySnapshot:
    """Replace the disabled-device inventory and notify listeners."""
    snapshot = _device_registry.set_disabled_devices(disabled_devices)
    _notify_registry_listeners(snapshot)
    return snapshot


def get_disabled_devices_snapshot() -> list[dict[str, Any]]:
    """Return a copy of the current disabled-device inventory."""
    return get_device_registry_snapshot().disabled_devices


def build_device_registry_snapshot(
    *,
    active_clients: list[Any] | None = None,
    disabled_devices: list[dict[str, Any]] | None = None,
) -> DeviceRegistrySnapshot:
    """Build a registry snapshot from explicit values or the current registry service."""
    current = _device_registry.snapshot()
    return DeviceRegistrySnapshot(
        active_clients=list(current.active_clients if active_clients is None else active_clients),
        disabled_devices=list(current.disabled_devices if disabled_devices is None else disabled_devices),
    )


def get_device_registry_snapshot() -> DeviceRegistrySnapshot:
    """Return the current thread-safe device registry snapshot."""
    return _device_registry.snapshot()
