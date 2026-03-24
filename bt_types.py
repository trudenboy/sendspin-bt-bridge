"""Type definitions for Bluetooth manager abstractions."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BluetoothManagerHost(Protocol):
    """Interface BluetoothManager uses to communicate with its owner.

    SendspinClient implements this protocol.  The abstraction decouples
    BT connection management from the specific client orchestrator so that
    each can be tested and evolved independently.
    """

    bt_management_enabled: bool
    bluetooth_sink_name: str | None

    def update_status(self, updates: dict[str, Any]) -> None:
        """Push a status update dict (triggers SSE notification)."""
        ...

    def get_status_value(self, key: str, default: Any = None) -> Any:
        """Read a single status value under the status lock."""
        ...

    def is_subprocess_running(self) -> bool:
        """Return True when the audio daemon subprocess is alive."""
        ...

    async def stop_subprocess(self) -> None:
        """Gracefully stop the audio daemon subprocess."""
        ...

    async def start_subprocess(self) -> None:
        """Start (or restart) the audio daemon subprocess."""
        ...

    async def send_subprocess_command(self, cmd: dict[str, Any]) -> None:
        """Send a JSON command dict to the daemon subprocess stdin."""
        ...
