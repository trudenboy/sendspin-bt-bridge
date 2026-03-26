"""Project-wide exception hierarchy.

Modules should raise specific subclasses instead of bare ``Exception`` so
callers can distinguish recoverable errors from unexpected failures.
"""

from __future__ import annotations


class BridgeError(Exception):
    """Base exception for all sendspin-bt-bridge errors."""


class BluetoothError(BridgeError):
    """Bluetooth operation failed (pairing, connection, adapter)."""


class PulseAudioError(BridgeError):
    """PulseAudio/PipeWire operation failed (sink lookup, volume, stream move)."""


class MusicAssistantError(BridgeError):
    """Music Assistant API or WebSocket error."""


class ConfigError(BridgeError):
    """Configuration load, parse, or validation error."""


class IPCError(BridgeError):
    """IPC protocol error (subprocess JSON-line communication)."""
