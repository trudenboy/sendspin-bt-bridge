"""Pydantic models shared by every adapter (REST, events, MCP)."""

from sendspin_bridge.application.models.bridge import (
    Bridge,
    BridgeStatus,
    BridgeVerdict,
    Group,
    GroupMember,
    SendspinSourceStatus,
    StartupProgress,
    UpdateInfo,
)
from sendspin_bridge.application.models.devices import Device, DisabledDevice

__all__ = [
    "Bridge",
    "BridgeStatus",
    "BridgeVerdict",
    "Device",
    "DisabledDevice",
    "Group",
    "GroupMember",
    "SendspinSourceStatus",
    "StartupProgress",
    "UpdateInfo",
]
