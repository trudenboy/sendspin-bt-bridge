"""Pairing, re-pairing and reconnecting: one Bluetooth operation at a time, options per attempt."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import sendspin_bridge.application.bluetooth as bt
import sendspin_bridge.application.devices as devices
import sendspin_bridge.application.jobs as jobs_module
import sendspin_bridge.application.status as status
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from tests.support.api_client import wait_for_job

MAC = "AA:BB:CC:DD:EE:FF"
HCI0_MAC = "C0:FB:F9:62:D6:9D"


@pytest.fixture(autouse=True)
def _host(monkeypatch, tmp_config):
    monkeypatch.setattr(bt, "list_bt_adapters", lambda: [HCI0_MAC])
    monkeypatch.setattr(bt, "build_hci_map", lambda: {HCI0_MAC.replace(":", ""): "hci0"})
    monkeypatch.setattr(bt, "_last_scan_completed", 0.0)
    monkeypatch.setattr(devices.time, "sleep", lambda _s: None)
    yield
    from sendspin_bridge.bluetooth.adapter_session import force_release_lease

    force_release_lease()


@pytest.fixture
def runner(monkeypatch):
    calls: list[dict] = []

    def _fake(mac, adapter, **options):
        calls.append({"mac": mac, "adapter": adapter, **options})
        return {"mac": mac, "paired": True}

    monkeypatch.setattr(bt, "_run_standalone_pair", _fake)
    return calls


@pytest.fixture
def speaker(monkeypatch):
    manager = MagicMock()
    manager.adapter_handle = None
    manager.effective_adapter_mac = HCI0_MAC
    manager.mac_address = "11:11:11:11:11:11"
    manager.pair_device.return_value = True
    manager.connect_device.return_value = True
    client = SimpleNamespace(player_id="kitchen", player_name="Kitchen", bt_manager=manager, status={})
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[client]))
    return manager


# -- pairing a new device ---------------------------------------------------------------------


def test_pairing_forwards_the_per_attempt_options(api_client, runner):
    resp = api_client.post(
        "/api/v1/bluetooth/pairings",
        json={
            "mac": "aa-bb-cc-dd-ee-ff",
            "adapter": HCI0_MAC,
            "no_input_no_output_agent": True,
            "allow_hfp_profile": True,
            "quiesce_adapter": True,
        },
    )
    assert resp.status_code == 202
    job = wait_for_job(api_client, resp.json())
    assert job["status"] == "succeeded"
    assert runner == [
        {
            "mac": MAC,
            "adapter": HCI0_MAC,
            "quiesce_adapter": True,
            "no_input_no_output_agent": True,
            "allow_hfp_profile": True,
        }
    ]


def test_pairing_defaults_to_the_secure_path(api_client, runner):
    wait_for_job(api_client, api_client.post("/api/v1/bluetooth/pairings", json={"mac": MAC}).json())
    assert runner[0]["no_input_no_output_agent"] is False
    assert runner[0]["allow_hfp_profile"] is False
    assert runner[0]["quiesce_adapter"] is False


@pytest.mark.parametrize("value", ["yes", 2, [True]])
def test_pairing_option_must_be_a_boolean(api_client, runner, value):
    """A truthy string must not silently switch on Just-Works pairing."""
    resp = api_client.post("/api/v1/bluetooth/pairings", json={"mac": MAC, "no_input_no_output_agent": value})
    assert resp.status_code == 422
    assert runner == []


def test_a_refused_pairing_fails_the_job_with_its_reason(api_client, monkeypatch):
    from sendspin_bridge.application.errors import UseCaseError

    def _refuse(*_a, **_kw):
        raise UseCaseError(422, "pin_required", "The speaker wants a custom PIN")

    monkeypatch.setattr(bt, "_run_standalone_pair", _refuse)
    job = wait_for_job(api_client, api_client.post("/api/v1/bluetooth/pairings", json={"mac": MAC}).json())
    assert job["status"] == "failed"
    assert job["error"] == {"code": "pin_required", "detail": "The speaker wants a custom PIN", "status": 422}


def test_invalid_mac_is_400(api_client, runner):
    assert api_client.post("/api/v1/bluetooth/pairings", json={"mac": "DEADBEEF"}).status_code == 400


# -- one Bluetooth operation at a time -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/bluetooth/pairings", {"mac": MAC}),
        ("post", "/api/v1/bluetooth/resets", {"mac": MAC}),
        ("post", "/api/v1/bluetooth/scans", {"adapter": HCI0_MAC}),
        ("post", "/api/v1/devices/kitchen/reconnect", None),
        ("post", "/api/v1/devices/kitchen/repair", None),
    ],
)
def test_a_busy_adapter_is_409_and_nothing_runs(api_client, speaker, runner, method, path, body):
    holder = bt._lease("someone else")
    try:
        resp = getattr(api_client, method)(path, json=body)
    finally:
        holder.release()
    assert resp.status_code == 409
    assert resp.json()["code"] == "bluetooth_busy"
    assert runner == []
    speaker.pair_device.assert_not_called()
    speaker.disconnect_device.assert_not_called()


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/bluetooth/pairings", {"mac": MAC}),
        ("/api/v1/bluetooth/resets", {"mac": MAC}),
        ("/api/v1/bluetooth/scans", {"adapter": HCI0_MAC}),
        ("/api/v1/devices/kitchen/reconnect", None),
        ("/api/v1/devices/kitchen/repair", None),
    ],
)
def test_the_lease_is_released_when_the_worker_cannot_start(api_client, speaker, monkeypatch, path, body):
    """Otherwise every later Bluetooth operation answers 409 until a restart."""

    class _DeadThread:
        def __init__(self, *_a, **_kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(jobs_module.threading, "Thread", _DeadThread)

    resp = api_client.post(path, json=body)

    assert resp.status_code == 500
    lease = bt._lease("probe")  # would raise 409 if the lease leaked
    lease.release()


# -- a configured speaker ---------------------------------------------------------------------------


def test_reconnect_disconnects_then_connects_and_frees_the_adapter(api_client, speaker):
    job = wait_for_job(api_client, api_client.post("/api/v1/devices/kitchen/reconnect").json())
    assert job["status"] == "succeeded"
    assert job["result"] == {"connected": True}
    speaker.disconnect_device.assert_called_once()
    speaker.connect_device.assert_called_once()
    bt._lease("probe").release()


def test_repair_parks_adapter_peers_only_when_asked(api_client, speaker, monkeypatch):
    calls: list[tuple] = []

    @contextlib.contextmanager
    def _quiesce(adapter, *, exclude_mac):
        calls.append((adapter, exclude_mac))
        yield []

    monkeypatch.setattr(devices, "quiesce_adapter_peers", _quiesce)

    wait_for_job(api_client, api_client.post("/api/v1/devices/kitchen/repair").json())
    assert calls == []

    job = wait_for_job(
        api_client, api_client.post("/api/v1/devices/kitchen/repair", json={"quiesce_adapter": True}).json()
    )
    assert calls == [(HCI0_MAC, "11:11:11:11:11:11")]
    assert job["result"] == {"paired": True, "connected": True}


def test_repair_that_cannot_pair_fails_the_job(api_client, speaker):
    speaker.pair_device.return_value = False
    job = wait_for_job(api_client, api_client.post("/api/v1/devices/kitchen/repair").json())
    assert job["status"] == "failed"
    assert job["error"]["code"] == "pairing_failed"
