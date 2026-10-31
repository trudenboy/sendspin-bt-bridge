"""POST /api/v1/bluetooth/devices/{mac}/connect — connect a known/bonded device.

The Hardware page can only offer "Connect" on a device BlueZ already knows,
and the connect must run against the controller that holds the bond.  Every
answer is a ``UseCaseError`` so the SPA can branch on a stable code.
"""

from __future__ import annotations

import pytest

DEVICE = "6C:5C:3D:35:17:99"
ADAPTER = "C0:FB:F9:62:D7:D6"
OTHER_ADAPTER = "00:15:83:FF:8F:2B"


@pytest.fixture
def client(tmp_config, api_client):
    """The shared API client, with the config file isolated to this test."""
    return api_client


def _bonded_on(installed_bluez, adapter: str = ADAPTER) -> None:
    """One controller with ``DEVICE`` bonded to it."""
    installed_bluez.on("list", stdout=f"Controller {adapter} HP-ProDesk [default]\n")
    installed_bluez.on("devices Paired", stdout=f"Device {DEVICE} ENEBY Portable\n")


def test_connect_success_is_204_and_runs_on_the_bond_owning_adapter(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 204
    connect_commands = [c for c in installed_bluez.scoped(ADAPTER) if c.verb == "connect"]
    assert connect_commands, f"connect must run scoped to {ADAPTER}, got: {installed_bluez.commands}"


def test_connect_explicit_adapter_must_hold_the_bond(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(
        f"/api/v1/bluetooth/devices/{DEVICE}/connect",
        params={"adapter_mac": OTHER_ADAPTER},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_adapter"
    assert "connect" not in installed_bluez.verbs()


def test_connect_unknown_device_is_404(client, installed_bluez):
    installed_bluez.on("list", stdout=f"Controller {ADAPTER} HP-ProDesk [default]\n")
    installed_bluez.on("devices Paired", stdout="")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"
    assert "connect" not in installed_bluez.verbs()


def test_connect_refused_by_bluez_is_409_with_the_verdict_detail(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.on(
        "connect",
        stdout=f"Failed to connect: org.bluez.Error.Failed {DEVICE}\n",
    )

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "connect_refused"
    assert "Failed to connect" in body["detail"]


def test_connect_transport_unavailable_is_502(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.fail("connect")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 502
    assert response.json()["code"] == "bluetooth_unavailable"


def test_connect_rejects_a_malformed_mac(client, installed_bluez):
    response = client.post("/api/v1/bluetooth/devices/not-a-mac/connect")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_mac"


def test_connect_without_a_controller_uses_the_default_scope(client, installed_bluez):
    """No controller answers ``list``: the bond is still known, so connect falls back to the default."""
    installed_bluez.on("list", stdout="")
    installed_bluez.on("devices", stdout=f"Device {DEVICE} ENEBY Portable\n")
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 204
    connect_commands = [c for c in installed_bluez.commands if c.verb == "connect"]
    assert connect_commands and connect_commands[0].adapter_selected == ""


def test_connect_is_refused_while_another_operation_holds_the_adapter_lease(client, installed_bluez):
    """The verb runs under the shared lease: a concurrent scan/pair wins, this answers 409."""
    from sendspin_bridge.bluetooth.adapter_session import AdapterHandle

    _bonded_on(installed_bluez)
    lease = AdapterHandle(adapter=ADAPTER).try_lease("test-holds-the-adapter")
    assert lease is not None
    try:
        response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")
    finally:
        lease.release()

    assert response.status_code == 409
    assert response.json()["code"] == "bluetooth_busy"
    assert "connect" not in installed_bluez.verbs()
