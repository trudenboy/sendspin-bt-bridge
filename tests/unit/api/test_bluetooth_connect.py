"""POST /api/v1/bluetooth/devices/{mac}/connect — connect a known/bonded device.

The Hardware page can only offer "Connect" on a device BlueZ already knows,
and the connect must run against the controller that holds the bond.  Every
answer is a ``UseCaseError`` so the SPA can branch on a stable code.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from sendspin_bridge.application.bluetooth import connect_known_device
from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.bluez import Adapter, Outcome, VerbResult, get_bluez

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


def _bonded_on_two_adapters(installed_bluez) -> None:
    installed_bluez.on(
        "list",
        stdout=(f"Controller {ADAPTER} HP-ProDesk [default]\nController {OTHER_ADAPTER} USB-Dongle\n"),
    )
    installed_bluez.on("devices Paired", stdout=f"Device {DEVICE} ENEBY Portable\n")


def _write_player(tmp_config, adapter: str = "") -> None:
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": "eneby-player",
                        "name": "ENEBY20",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": adapter},
                    }
                ],
                "PLAYER_STATE": {},
            }
        )
    )


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate(), "the worker did not finish in time"


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


def _assert_lease_is_free() -> None:
    """The lease must be free once the request is over — not just while contended."""
    assert AdapterHandle.current_holder() is None
    probe = AdapterHandle(adapter=ADAPTER).try_lease("test-after-connect")
    assert probe is not None, "the lease was not released"
    probe.release()


def test_connect_releases_the_lease_after_success(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 204
    _assert_lease_is_free()


def test_connect_releases_the_lease_after_a_refusal(client, installed_bluez):
    _bonded_on(installed_bluez)
    installed_bluez.on("connect", stdout=f"Failed to connect: org.bluez.Error.Failed {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 409
    assert response.json()["code"] == "connect_refused"
    _assert_lease_is_free()


def test_connect_prefers_the_players_configured_adapter_over_the_lowest_mac(client, tmp_config, installed_bluez):
    """A bond on two controllers: the player's own adapter wins, not ``sorted()[0]``."""
    _bonded_on_two_adapters(installed_bluez)
    _write_player(tmp_config, adapter="hci1")
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 204
    connect_commands = [c for c in installed_bluez.scoped(OTHER_ADAPTER) if c.verb == "connect"]
    assert connect_commands, f"connect must follow the player's adapter, got: {installed_bluez.commands}"
    assert not [c for c in installed_bluez.scoped(ADAPTER) if c.verb == "connect"]


def test_connect_explicit_adapter_narrows_the_bond_choice(client, installed_bluez):
    """An explicit ``adapter_mac`` picks that controller among the bond's adapters."""
    _bonded_on_two_adapters(installed_bluez)
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(
        f"/api/v1/bluetooth/devices/{DEVICE}/connect",
        params={"adapter_mac": OTHER_ADAPTER},
    )

    assert response.status_code == 204
    assert [c for c in installed_bluez.scoped(OTHER_ADAPTER) if c.verb == "connect"]
    assert not [c for c in installed_bluez.scoped(ADAPTER) if c.verb == "connect"]


def _show_output(mac: str, powered: bool) -> str:
    return f"Controller {mac} (public)\n\tAlias: {mac}\n\tPowered: {'yes' if powered else 'no'}\n"


def test_connect_falls_back_to_a_powered_controller_before_the_lowest_mac(client, installed_bluez):
    """Two bonds, no player preference: the powered controller wins over plain ``sorted()``."""
    _bonded_on_two_adapters(installed_bluez)
    installed_bluez.on(lambda text: "show" in text and ADAPTER in text, stdout=_show_output(ADAPTER, True))
    installed_bluez.on(
        lambda text: "show" in text and OTHER_ADAPTER in text,
        stdout=_show_output(OTHER_ADAPTER, False),
    )
    installed_bluez.on("connect", stdout=f"Attempting to connect to {DEVICE}\n")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 204
    # The lowest MAC (00:...) is unpowered; the connect must use C0:... instead.
    connect_commands = [c for c in installed_bluez.scoped(ADAPTER) if c.verb == "connect"]
    assert connect_commands, f"connect must use the powered controller, got: {installed_bluez.commands}"


def test_connect_rejects_a_malformed_adapter_mac(client, installed_bluez):
    _bonded_on(installed_bluez)

    response = client.post(
        f"/api/v1/bluetooth/devices/{DEVICE}/connect",
        params={"adapter_mac": "not-a-mac"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_adapter"
    assert "connect" not in installed_bluez.verbs()


def test_connect_unreadable_bluez_is_502_not_404(client, installed_bluez):
    """No controller answer and no device answer is "unavailable", not "unknown device"."""
    installed_bluez.on("list", stdout="")
    installed_bluez.timeout("devices")

    response = client.post(f"/api/v1/bluetooth/devices/{DEVICE}/connect")

    assert response.status_code == 502
    assert response.json()["code"] == "bluetooth_unavailable"


async def test_a_cancelled_request_does_not_free_the_lease_until_the_worker_finishes(
    tmp_config, installed_bluez, monkeypatch
):
    """Cancellation must not release the lease while ``bluetoothctl connect`` still runs.

    Uvicorn cancels in-flight requests on shutdown while the controller verb has
    its own 10 s deadline.  If the release lived in the coroutine's ``finally``,
    a scan could take the lease and drive the controller while the connect
    thread is still talking to it.
    """
    _bonded_on(installed_bluez)
    started = threading.Event()
    finished = threading.Event()

    def slow_connect(mac, adapter=Adapter.DEFAULT, *, timeout=None):
        started.set()
        assert finished.wait(5), "the test never let the fake connect finish"
        return VerbResult(Outcome.OK)

    # ``FakeBluez.control`` builds a new instance per access; patch the
    # singleton the fixture installed so the lease's transport uses it.
    monkeypatch.setattr(get_bluez(), "connect", slow_connect)

    task = asyncio.create_task(connect_known_device(DEVICE))
    try:
        assert await asyncio.to_thread(started.wait, 3), "the connect worker never started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The request is gone but the worker is still driving the controller.
        assert AdapterHandle.current_holder() == f"connect {DEVICE}"
        assert AdapterHandle(adapter=ADAPTER).try_lease("scan") is None
    finally:
        finished.set()

    await _wait_until(lambda: AdapterHandle.current_holder() is None)
    _assert_lease_is_free()
