"""Read-side helpers for the current bridge device registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import state


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


def build_device_registry_snapshot(
    *,
    active_clients: list[Any] | None = None,
    disabled_devices: list[dict[str, Any]] | None = None,
) -> DeviceRegistrySnapshot:
    """Build a registry snapshot from explicit values or current shared state."""
    return DeviceRegistrySnapshot(
        active_clients=list(state.get_clients_snapshot() if active_clients is None else active_clients),
        disabled_devices=list(state.get_disabled_devices() if disabled_devices is None else disabled_devices),
    )


def get_device_registry_snapshot() -> DeviceRegistrySnapshot:
    """Return the current thread-safe device registry snapshot."""
    return build_device_registry_snapshot()
