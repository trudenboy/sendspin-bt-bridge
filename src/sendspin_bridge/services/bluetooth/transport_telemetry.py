"""BlueZ MediaTransport telemetry used by latency diagnostics.

The AVDTP Delay Reporting value is advisory: it is reported by the peer and
must never be treated as an end-to-end acoustic measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MEDIA_TRANSPORT_IFACE = "org.bluez.MediaTransport1"
UTC = timezone.utc

_STATE_PRIORITY = {"active": 3, "pending": 2, "idle": 1}
_STANDARD_CODEC_NAMES = {0x00: "sbc", 0x01: "mpeg12", 0x02: "aac", 0xFF: "vendor"}


@dataclass(frozen=True, slots=True)
class BluetoothTransportSnapshot:
    path: str | None = None
    state: str | None = None
    codec_id: int | None = None
    codec_name: str | None = None
    delay_supported: bool = False
    delay_tenths_ms: int | None = None
    delay_ms: float | None = None
    updated_at: str | None = None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def select_transport_snapshot(managed_objects: Any, device_path: str) -> BluetoothTransportSnapshot:
    """Select the best MediaTransport belonging to *device_path*.

    ``GetManagedObjects`` returns D-Bus scalar wrappers; only their ordinary
    ``str``/``int`` conversions are relied upon so the function remains easy to
    test without a live system bus.
    """
    if not isinstance(managed_objects, dict) or not device_path:
        return BluetoothTransportSnapshot()

    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for raw_path, ifaces in managed_objects.items():
        if not hasattr(ifaces, "get"):
            continue
        props = ifaces.get(MEDIA_TRANSPORT_IFACE)
        if not hasattr(props, "get") or str(props.get("Device", "")) != device_path:
            continue
        state = str(props.get("State", "")) or None
        candidates.append((_STATE_PRIORITY.get(state or "", 0), str(raw_path), props))

    if not candidates:
        return BluetoothTransportSnapshot()
    _, path, props = max(candidates, key=lambda item: (item[0], item[1]))
    state = str(props.get("State", "")) or None
    codec_id = _as_int(props.get("Codec"))
    has_delay = "Delay" in props
    delay_tenths_ms = _as_int(props.get("Delay")) if has_delay else None
    if delay_tenths_ms is None:
        has_delay = False
    return BluetoothTransportSnapshot(
        path=path,
        state=state,
        codec_id=codec_id,
        codec_name=_STANDARD_CODEC_NAMES.get(codec_id) if codec_id is not None else None,
        delay_supported=has_delay,
        delay_tenths_ms=delay_tenths_ms,
        delay_ms=round(delay_tenths_ms / 10.0, 1) if delay_tenths_ms is not None else None,
        updated_at=datetime.now(tz=UTC).isoformat(),
    )
