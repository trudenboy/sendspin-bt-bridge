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


async def _risk_after_one_poll(monkeypatch, config: dict, env: str | None) -> bool:
    client = SendspinClient("ENEBY Portable", "localhost", 9000)
    client.status["backend_output_latency_ms"] = 110.0
    updates: dict = {}

    async def _transport_snapshot():
        client.running = False
        return BluetoothTransportSnapshot(
            path=PATH, state="active", codec_id=0, codec_name="sbc", delay_supported=True, delay_ms=120.0
        )

    client.bt_manager = SimpleNamespace(
        device=SimpleNamespace(transport_snapshot=_transport_snapshot), prefer_sbc=False
    )
    client.running = True
    monkeypatch.setattr(client, "_update_status", updates.update)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: dict(config))
    if env is None:
        monkeypatch.delenv("SENDSPIN_COMPENSATE_OUTPUT_LATENCY", raising=False)
    else:
        monkeypatch.setenv("SENDSPIN_COMPENSATE_OUTPUT_LATENCY", env)

    async def _no_recommendation(*_a, **_k):
        return False

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(client, "_apply_initial_latency_recommendation", _no_recommendation)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    await client._transport_telemetry_loop()
    return updates["latency_double_count_risk"]


@pytest.mark.asyncio
async def test_a_compensated_backend_latency_is_no_double_count_risk(monkeypatch):
    assert await _risk_after_one_poll(monkeypatch, {}, None) is False


@pytest.mark.asyncio
async def test_the_risk_stays_when_compensation_is_off(monkeypatch):
    assert await _risk_after_one_poll(monkeypatch, {"COMPENSATE_OUTPUT_LATENCY": False}, None) is True
    assert await _risk_after_one_poll(monkeypatch, {}, "0") is True


def _delayed(path: str | None, delay_ms: float | None) -> BluetoothTransportSnapshot:
    return BluetoothTransportSnapshot(
        path=path,
        state="active",
        codec_id=0,
        codec_name="sbc",
        delay_supported=delay_ms is not None,
        delay_tenths_ms=None if delay_ms is None else round(delay_ms * 10),
        delay_ms=delay_ms,
    )


async def _delay_lines(monkeypatch, caplog, sequence: list[BluetoothTransportSnapshot]) -> list[str]:
    client = SendspinClient("ENEBY Portable", "localhost", 9000)
    snapshots = iter(sequence)

    async def _transport_snapshot():
        snapshot = next(snapshots, None)
        if snapshot is None:
            client.running = False
            return sequence[-1]
        return snapshot

    client.bt_manager = SimpleNamespace(
        device=SimpleNamespace(transport_snapshot=_transport_snapshot), prefer_sbc=False
    )
    client.running = True
    monkeypatch.setattr(client, "_update_status", lambda _u: None)

    async def _no_recommendation(*_a, **_k):
        return False

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(client, "_apply_initial_latency_recommendation", _no_recommendation)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bridge.client")

    await client._transport_telemetry_loop()
    return [r.getMessage() for r in caplog.records if "A2DP delay report" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_delay_report_arriving_after_connect_is_logged_once(monkeypatch, caplog):
    lines = await _delay_lines(
        monkeypatch,
        caplog,
        [_delayed(PATH, None), _delayed(PATH, 123.4), _delayed(PATH, 123.4), _delayed(PATH, 123.9)],
    )

    assert lines == ["[ENEBY Portable] A2DP delay report: 123.4 ms"]


@pytest.mark.asyncio
async def test_a_delay_change_of_a_millisecond_is_logged(monkeypatch, caplog):
    lines = await _delay_lines(
        monkeypatch,
        caplog,
        [_delayed(PATH, 123.4), _delayed(PATH, 150.0), _delayed(PATH, 150.5)],
    )

    # The first value is in the connect line; only the change gets its own.
    assert lines == ["[ENEBY Portable] A2DP delay report: 150.0 ms"]


@pytest.mark.asyncio
async def test_a_speaker_without_delay_reporting_logs_no_delay_lines(monkeypatch, caplog):
    lines = await _delay_lines(monkeypatch, caplog, [_delayed(PATH, None)] * 4)

    assert lines == []
    absent = [r.getMessage() for r in caplog.records if "does not report an A2DP delay" in r.getMessage()]
    assert len(absent) == 1


@pytest.mark.asyncio
async def test_a_reconnect_starts_the_delay_log_afresh(monkeypatch, caplog):
    lines = await _delay_lines(
        monkeypatch,
        caplog,
        [
            _delayed(PATH, None),
            _delayed(PATH, 120.0),
            _delayed(None, None),
            _delayed(PATH, None),
            _delayed(PATH, 120.0),
        ],
    )

    assert lines == ["[ENEBY Portable] A2DP delay report: 120.0 ms"] * 2
