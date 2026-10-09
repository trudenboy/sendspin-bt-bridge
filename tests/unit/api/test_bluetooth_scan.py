"""``POST /bluetooth/scans``: one adapter at a time, never concurrent, with a rest period after each."""

from __future__ import annotations

import threading
import types

import pytest

import sendspin_bridge.application.bluetooth as bt
from tests.support.api_client import wait_for_job

ADAPTERS = ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]


@pytest.fixture(autouse=True)
def _host(monkeypatch, tmp_config):
    monkeypatch.setattr(bt, "list_bt_adapters", lambda: list(ADAPTERS))
    # hciN labels resolve through the sysfs map since issue #340.
    monkeypatch.setattr(bt, "build_hci_map", lambda: {"AABBCCDDEE01": "hci0", "AABBCCDDEE02": "hci1"})
    yield
    from sendspin_bridge.bluetooth.adapter_session import force_release_lease

    force_release_lease()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(bt.time, "monotonic", clock.monotonic)
    return clock


def test_scan_reports_its_options_and_releases_the_adapter(api_client, monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(bt, "_scan", lambda adapter, audio_only: calls.append((adapter, audio_only)) or {"devices": []})

    resp = api_client.post("/api/v1/bluetooth/scans", json={"adapter": "hci1", "audio_only": False})

    assert resp.status_code == 202
    job = resp.json()
    assert job["kind"] == "bluetooth.scan"
    assert job["progress"]["scan_options"] == {
        "adapter": "hci1",
        "audio_only": False,
        "adapter_scope": "selected",
        "adapter_count": 1,
    }
    assert job["progress"]["expected_duration"] == 15
    assert wait_for_job(api_client, job)["status"] == "succeeded"
    assert calls == [("hci1", False)]
    # The lease is back: another adapter operation may start.
    lease = bt._lease("probe")
    lease.release()


@pytest.mark.parametrize("body", [{}, {"adapter": ""}, {"adapter": "all"}])
def test_scan_needs_one_specific_adapter(api_client, body):
    resp = api_client.post("/api/v1/bluetooth/scans", json=body)
    assert resp.status_code in (400, 422)


def test_scan_rejects_a_malformed_adapter(api_client):
    resp = api_client.post("/api/v1/bluetooth/scans", json={"adapter": "hciX"})
    assert resp.status_code == 400
    assert "invalid adapter" in resp.json()["detail"].lower()


def test_a_second_scan_while_one_runs_is_409(api_client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(bt, "_scan", lambda *_a: release.wait(5) and {"devices": []})
    first = api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]})
    try:
        second = api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[1]})
        assert second.status_code == 409
    finally:
        release.set()
        wait_for_job(api_client, first.json())


def test_cooldown_answers_429_with_retry_after(api_client, clock):
    bt._last_scan_completed = clock.now - 3
    resp = api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]})
    assert resp.status_code == 429
    assert resp.json()["code"] == "scan_cooldown"
    assert int(resp.headers["Retry-After"]) == 8


def test_scan_allowed_after_the_cooldown(api_client, clock, monkeypatch):
    monkeypatch.setattr(bt, "_scan", lambda *_a: {"devices": []})
    bt._last_scan_completed = clock.now - bt._SCAN_COOLDOWN - 1
    resp = api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]})
    assert resp.status_code == 202
    wait_for_job(api_client, resp.json())


def test_cooldown_runs_from_the_end_of_the_scan(api_client, clock, monkeypatch):
    """The 15 s scan is longer than the 10 s cooldown: stamping at the start
    would leave the adapter no rest at all."""

    def _scan(adapter_macs, window_s=0.0):
        clock.now += window_s
        return types.SimpleNamespace(
            names={}, device_adapter={}, rssi_by_mac={}, discovery_errors=[], seen_macs=set(), active_macs=set()
        )

    monkeypatch.setattr(bt, "get_bluez", lambda: types.SimpleNamespace(scan=_scan))
    started_at = clock.now

    wait_for_job(api_client, api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]}).json())

    assert bt._last_scan_completed == started_at + bt._SCAN_BASE_DURATION
    assert api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]}).status_code == 429


def test_cooldown_is_stamped_even_when_the_scan_fails(api_client, clock, monkeypatch):
    def _boom(_adapter):
        clock.now += 3.0
        raise RuntimeError("adapter wedged")

    monkeypatch.setattr(bt, "_resolve_scan_adapter_macs", lambda adapter: [] if clock.now == 1000.0 else _boom(adapter))
    started_at = clock.now
    monkeypatch.setattr(bt, "_scan", lambda adapter, audio_only: _boom(adapter))

    job = wait_for_job(api_client, api_client.post("/api/v1/bluetooth/scans", json={"adapter": ADAPTERS[0]}).json())

    assert job["status"] == "failed"
    assert bt._last_scan_completed == started_at + 3.0
