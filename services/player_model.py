"""Typed player configuration and state model for the bridge runtime.

Replaces raw config dicts with structured ``Player`` instances that carry
backend-type discriminator, stable player ID, and all per-device settings.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from services.audio_backend import BackendType


class PlayerState(str, Enum):
    """Runtime lifecycle states for a player."""

    INITIALIZING = "initializing"
    CONNECTING = "connecting"
    READY = "ready"
    STREAMING = "streaming"
    STANDBY = "standby"
    ERROR = "error"
    DISABLED = "disabled"
    OFFLINE = "offline"


def _player_id_from_mac(mac: str) -> str:
    """Deterministic player ID from BT MAC address (matches config.py logic)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac.lower()))


def _player_id_from_name(name: str) -> str:
    """Deterministic player ID from player name."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name.strip().lower()))


@dataclass
class Player:
    """Typed representation of a configured audio player."""

    id: str
    player_name: str
    backend_type: BackendType = BackendType.BLUETOOTH_A2DP
    backend_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    listen_port: int = 0
    static_delay_ms: Optional[float] = None
    handoff_mode: str = "default"
    room_id: Optional[str] = None
    room_name: Optional[str] = None
    volume_controller: str = "pa"
    keepalive_enabled: bool = False
    keepalive_interval: int = 30
    idle_disconnect_minutes: int = 0

    @property
    def mac(self) -> Optional[str]:
        """Shortcut: BT MAC address from backend_config, or None."""
        if self.backend_type == BackendType.BLUETOOTH_A2DP:
            return self.backend_config.get("mac")
        return None

    @property
    def adapter(self) -> str:
        """Shortcut: BT adapter from backend_config, or empty string."""
        if self.backend_type == BackendType.BLUETOOTH_A2DP:
            return self.backend_config.get("adapter", "")
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses and config persistence."""
        data = asdict(self)
        data["backend_type"] = self.backend_type.value
        return data

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> Player:
        """Create a Player from either v1 (BLUETOOTH_DEVICES) or v2 (players[]) config dict."""
        backend_block = cfg.get("backend")
        if isinstance(backend_block, dict) and "type" in backend_block:
            return cls._from_v2(cfg, backend_block)
        return cls._from_v1(cfg)

    @classmethod
    def _from_v1(cls, cfg: dict[str, Any]) -> Player:
        """Parse v1 BLUETOOTH_DEVICES entry."""
        mac = (cfg.get("mac") or "").strip().upper()
        player_name = (cfg.get("player_name") or mac or "Unknown").strip()
        player_id = cfg.get("id") or (_player_id_from_mac(mac) if mac else _player_id_from_name(player_name))

        return cls(
            id=player_id,
            player_name=player_name,
            backend_type=BackendType.BLUETOOTH_A2DP,
            backend_config={
                "mac": mac,
                "adapter": (cfg.get("adapter") or "").strip(),
            },
            enabled=cfg.get("enabled", True),
            listen_port=int(cfg.get("listen_port") or 0),
            static_delay_ms=cfg.get("delay_ms"),
            handoff_mode=cfg.get("handoff_mode", "default"),
            room_id=cfg.get("room_id"),
            room_name=cfg.get("room_name"),
            volume_controller=cfg.get("volume_controller", "pa"),
            keepalive_enabled=cfg.get("keepalive_enabled", False),
            keepalive_interval=cfg.get("keepalive_interval", 30),
            idle_disconnect_minutes=cfg.get("idle_disconnect_minutes", 0),
        )

    @classmethod
    def _from_v2(cls, cfg: dict[str, Any], backend: dict[str, Any]) -> Player:
        """Parse v2 players[] entry."""
        backend_type_str = backend.get("type", "bluetooth_a2dp")
        try:
            backend_type = BackendType(backend_type_str)
        except ValueError:
            backend_type = BackendType.BLUETOOTH_A2DP

        backend_config = {k: v for k, v in backend.items() if k != "type"}

        mac = backend_config.get("mac", "")
        if isinstance(mac, str):
            mac = mac.strip().upper()
            backend_config["mac"] = mac

        player_name = (cfg.get("player_name") or mac or "Unknown").strip()
        player_id = cfg.get("id") or (_player_id_from_mac(mac) if mac else _player_id_from_name(player_name))

        return cls(
            id=player_id,
            player_name=player_name,
            backend_type=backend_type,
            backend_config=backend_config,
            enabled=cfg.get("enabled", True),
            listen_port=int(cfg.get("listen_port") or 0),
            static_delay_ms=cfg.get("static_delay_ms"),
            handoff_mode=cfg.get("handoff_mode", "default"),
            room_id=cfg.get("room_id"),
            room_name=cfg.get("room_name"),
            volume_controller=cfg.get("volume_controller", "pa"),
            keepalive_enabled=cfg.get("keepalive_enabled", False),
            keepalive_interval=cfg.get("keepalive_interval", 30),
            idle_disconnect_minutes=cfg.get("idle_disconnect_minutes", 0),
        )
