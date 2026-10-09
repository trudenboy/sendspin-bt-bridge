"""Regression tests for POST /api/v1/bluetooth/devices/{mac}/disconnect (rc.1 checklist item 8).

Two defects surfaced on the live two-adapter stand:

1. The endpoint passed **no adapter** to ``BluezControl.disconnect()``, so
   on a multi-adapter host the disconnect ran against whichever controller
   bluetoothctl considered default — not necessarily the one the device is
   bonded to.  It must resolve the device's owning adapter from the
   per-adapter paired lists first.
2. The ok-heuristic was ``"successful" in stdout`` — but BlueZ ≥5.72 says
   "Attempting to disconnect …" and stays silent on success, so a real
   disconnect reported ``ok: false``.
"""

from __future__ import annotations

import pytest

ENEBY_MAC = "6C:5C:3D:35:17:99"
ADAPTER_A = "C0:FB:F9:62:D7:D6"  # default controller — device is NOT here
ADAPTER_B = "00:02:72:0A:E4:3B"  # device bonded here


@pytest.fixture
def client(tmp_config):
    from tests.support.api_client import make_client

    return make_client()


def _two_adapters_one_device(fake_bluez):
    """Two controllers; ENEBY bonded to the non-default one (hci1)."""
    fake_bluez.on(
        "list",
        stdout=f"Controller {ADAPTER_A} HP-ProDesk [default]\nController {ADAPTER_B} HP-ProDesk #2\n",
    )
    fake_bluez.on_adapter(ADAPTER_A).on("devices Paired", stdout="")
    fake_bluez.on_adapter(ADAPTER_B).on("devices Paired", stdout=f"Device {ENEBY_MAC} ENEBY Portable\n")


def test_disconnect_targets_the_adapter_the_device_is_bonded_to(client, installed_bluez):
    _two_adapters_one_device(installed_bluez)
    installed_bluez.on(
        "disconnect",
        stdout="Attempting to disconnect from 6C:5C:3D:35:17:99\n",  # BlueZ 5.72 success shape
    )

    response = client.post(f"/api/v1/bluetooth/devices/{ENEBY_MAC}/disconnect")

    assert response.status_code == 204
    scoped = installed_bluez.scoped(ADAPTER_B)
    assert any(c.kind == "run" and "disconnect" in c.script for c in scoped), (
        f"disconnect must run scoped to {ADAPTER_B}, got: {installed_bluez.commands}"
    )


def test_disconnect_silent_success_is_ok(client, installed_bluez):
    """BlueZ ≥5.72 prints 'Attempting to disconnect…' and nothing else on
    success — silence (no failure marker) must map to ok=true."""
    _two_adapters_one_device(installed_bluez)
    installed_bluez.on("disconnect", stdout="Attempting to disconnect from 6C:5C:3D:35:17:99\n")

    response = client.post(f"/api/v1/bluetooth/devices/{ENEBY_MAC}/disconnect")

    assert response.status_code == 204


def test_disconnect_failure_marker_is_not_ok(client, installed_bluez):
    """A speaker BlueZ declined to disconnect is an answer, not a bridge failure.

    5xx is reserved for the cases where nothing could be asked — a timeout,
    a missing bluetoothctl, an exit nobody can read. "BlueZ says it is not
    connected" is an answer: 409 with the reason.
    """
    _two_adapters_one_device(installed_bluez)
    installed_bluez.on("disconnect", stdout="Failed to disconnect: org.bluez.Error.NotConnected\n")

    response = client.post(f"/api/v1/bluetooth/devices/{ENEBY_MAC}/disconnect")

    assert response.status_code == 409
    assert "NotConnected" in response.json()["detail"]


def test_disconnect_reports_failure_when_bluetoothctl_exits_nonzero(client, installed_bluez):
    """A non-zero exit whose error text lands on stderr (or nowhere) has no
    failure marker in stdout, so the silence-means-success heuristic read it
    as a completed disconnect."""
    _two_adapters_one_device(installed_bluez)
    installed_bluez.nonzero("disconnect", stderr="org.bluez.Error.NotConnected\n")

    response = client.post(f"/api/v1/bluetooth/devices/{ENEBY_MAC}/disconnect")

    assert response.status_code == 502
    assert response.json()["code"] == "disconnect_failed"
