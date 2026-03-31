"""Audio backend implementations.

Factory function ``create_backend`` dispatches by backend type string
and returns a concrete ``AudioBackend`` instance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from services.audio_backend import BackendType

if TYPE_CHECKING:
    from services.audio_backend import AudioBackend


def create_backend(backend_type: str, **kwargs: Any) -> AudioBackend:
    """Create an AudioBackend instance for the given type.

    Args:
        backend_type: One of BackendType values or "mock" for testing.
        **kwargs: Backend-specific arguments.

    Returns:
        Configured AudioBackend instance.

    Raises:
        ValueError: If backend_type is unknown or required args missing.
    """
    if backend_type == BackendType.BLUETOOTH_A2DP or backend_type == "bluetooth_a2dp":
        from services.backends.bluetooth_a2dp import BluetoothA2dpBackend

        bt_manager = kwargs.get("bt_manager")
        if bt_manager is None:
            raise ValueError("bluetooth_a2dp backend requires 'bt_manager' argument")
        return BluetoothA2dpBackend(bt_manager)

    if backend_type == "mock":
        from services.backends.mock_backend import MockAudioBackend

        return MockAudioBackend(
            **{
                k: v
                for k, v in kwargs.items()
                if k in ("backend_id", "fail_connect", "connect_latency", "failure_rate", "backend_type")
            }
        )

    # Future backends
    if backend_type in (BackendType.LOCAL_SINK, "local_sink"):
        raise ValueError(f"Backend type '{backend_type}' is planned but not yet implemented")
    if backend_type in (BackendType.SNAPCAST, "snapcast"):
        raise ValueError(f"Backend type '{backend_type}' is planned but not yet implemented")

    raise ValueError(f"Unknown backend type: '{backend_type}'")
