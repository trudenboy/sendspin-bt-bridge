"""Pydantic models shared by every adapter (REST, events, MCP)."""

from sendspin_bridge.application.models.bridge import (
    Bridge,
    BridgeStatus,
    Group,
    GroupMember,
    StartupProgress,
    UpdateInfo,
)
from sendspin_bridge.application.models.devices import Device, DisabledDevice

__all__ = [
    "Bridge",
    "BridgeStatus",
    "Device",
    "DisabledDevice",
    "Group",
    "GroupMember",
    "StartupProgress",
    "UpdateInfo",
]
