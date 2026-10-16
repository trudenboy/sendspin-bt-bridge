"""Every BT connect logs the speaker's transport once (codec, Delay, Volume).

The HAOS add-on has no shell: what the transport offers must be in the log.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.bluetooth.transport_telemetry import BluetoothTransportSnapshot

PATH = "/org/bluez/hci1/dev_6C_5C_3D_35_17_99/sep1/fd3"


def _snapshot(path: str | None) -> BluetoothTransportSnapshot:
    return BluetoothTransportSnapshot(path=path, state="active", codec_id=0, codec_name="sbc")


@pytest.mark.asyncio
async def test_one_line_per_transport_and_again_after_a_reconnect(monkeypatch, caplog):
    client = SendspinClient("ENEBY Portable", "localhost", 9000)
    snapshots = iter([_snapshot(PATH), _snapshot(PATH), _snapshot(None), _snapshot(PATH)])

    async def _transport_snapshot():
        snapshot = next(snapshots, None)
        if snapshot is None:
            client.running = False
            return _snapshot(PATH)
        return snapshot

    client.bt_manager = SimpleNamespace(
        device=SimpleNamespace(transport_snapshot=_transport_snapshot), prefer_sbc=False
    )
    client.running = True
    monkeypatch.setattr(client, "_update_status", lambda _u: None)

    async def _no_recommendation(*_a, **_k):
        return False

    monkeypatch.setattr(client, "_apply_initial_latency_recommendation", _no_recommendation)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bridge.client")

    await client._transport_telemetry_loop()

    lines = [r.getMessage() for r in caplog.records if "BT transport" in r.getMessage()]
    assert len(lines) == 2
    assert lines[0].startswith("[ENEBY Portable] BT transport " + PATH)
