"""End-to-end tests for /api/bt/adapters (issue #193).

Two specific regressions guarded here:

1. The endpoint must label each adapter with its **kernel** ``hciN``
   (read from sysfs), not the index from ``bluetoothctl list`` — the
   latter follows BlueZ's internal registration order and can disagree
   with the kernel after a USB hotplug.

2. The endpoint must surface the alias of the **adapter it was asked
   about**, not the alias of whatever controller bluetoothctl considers
   the default.  The original ``select <MAC>; show`` recipe could
   return the default controller's alias when piped on stdin.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from routes.api_bt import bt_bp


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


def _show_output(mac: str, alias: str, *, powered: bool = True) -> str:
    powered_line = "yes" if powered else "no"
    return (
        f"Controller {mac} (public)\n"
        f"\tManufacturer: 0x0131\n"
        f"\tName: {alias}\n"
        f"\tAlias: {alias}\n"
        f"\tPowered: {powered_line}\n"
    )


def test_api_bt_adapters_resolves_kernel_hci_via_sysfs(tmp_path, monkeypatch, client):
    # bluetoothctl list returns the Realtek USB stick first (BlueZ
    # registered it before the built-in Cypress on this Pi) — that order
    # is exactly what produced the wrong "hci0/hci1" labels in #193.
    macs = ["88:A2:9E:C0:07:0D", "A0:AD:9F:6E:B2:D5"]
    sysfs = tmp_path / "bluetooth"
    sysfs.mkdir()
    (sysfs / "hci0").mkdir()
    (sysfs / "hci0" / "address").write_text("A0:AD:9F:6E:B2:D5\n")
    (sysfs / "hci1").mkdir()
    (sysfs / "hci1" / "address").write_text("88:A2:9E:C0:07:0D\n")
    monkeypatch.setattr("sendspin_bridge.services.bluetooth._BT_SYSFS_DIR", sysfs)

    monkeypatch.setattr("routes.api_bt.list_bt_adapters", lambda: macs)

    def _fake_alias(mac: str, *, timeout: int = 5) -> tuple[str, bool]:
        return ({"A0:AD:9F:6E:B2:D5": "SendSpinEG", "88:A2:9E:C0:07:0D": "SendSpinEG #2"}[mac], True)

    monkeypatch.setattr("routes.api_bt.get_adapter_alias", _fake_alias)

    response = client.get("/api/bt/adapters")
    assert response.status_code == 200
    adapters = response.get_json()["adapters"]

    # Even though bluetoothctl list returned the Realtek MAC first
    # (would have been "hci0" under the index-based labelling), the
    # response must label adapters by their kernel hci number.
    by_mac = {a["mac"]: a for a in adapters}
    assert by_mac["A0:AD:9F:6E:B2:D5"]["id"] == "hci0"
    assert by_mac["88:A2:9E:C0:07:0D"]["id"] == "hci1"
    # And each adapter's alias must match its actual MAC, not the
    # default controller's alias.
    assert by_mac["A0:AD:9F:6E:B2:D5"]["name"] == "SendSpinEG"
    assert by_mac["88:A2:9E:C0:07:0D"]["name"] == "SendSpinEG #2"


def test_api_bt_adapters_falls_back_to_index_label_without_sysfs(tmp_path, monkeypatch, client):
    # Non-Linux / containerised dev environments without /sys mounted —
    # the endpoint must still respond with usable labels rather than
    # erroring out.
    monkeypatch.setattr("sendspin_bridge.services.bluetooth._BT_SYSFS_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr("routes.api_bt.list_bt_adapters", lambda: ["AA:BB:CC:DD:EE:01"])
    monkeypatch.setattr("routes.api_bt.get_adapter_alias", lambda mac, **_: ("Some Controller", True))

    response = client.get("/api/bt/adapters")
    assert response.status_code == 200
    adapters = response.get_json()["adapters"]
    assert adapters == [{"id": "hci0", "mac": "AA:BB:CC:DD:EE:01", "name": "Some Controller", "powered": True}]


def test_api_bt_adapters_uses_explicit_show_form_for_alias(tmp_path, monkeypatch, client):
    # Direct end-to-end check that the endpoint goes through
    # ``get_adapter_alias`` (which uses ``show <MAC>``) — not the legacy
    # ``select <MAC>; show`` path that produced the alias swap in #193.
    monkeypatch.setattr("sendspin_bridge.services.bluetooth._BT_SYSFS_DIR", tmp_path / "missing")
    monkeypatch.setattr("routes.api_bt.list_bt_adapters", lambda: ["AA:BB:CC:DD:EE:FF"])

    captured: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = _show_output("AA:BB:CC:DD:EE:FF", "Probe")

    def _fake_run(cmd, *, input=None, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = input
        return _Completed()

    with patch("sendspin_bridge.services.bluetooth.subprocess.run", side_effect=_fake_run):
        response = client.get("/api/bt/adapters")

    assert response.status_code == 200
    assert captured["cmd"] == ["bluetoothctl"]
    # Critical: the bluetoothctl input is the explicit ``show <MAC>``
    # form — never ``select <MAC>; show``.  This is the one-line
    # difference that fixes #193.
    assert captured["input"] == "show AA:BB:CC:DD:EE:FF\n"
    assert "select" not in (captured["input"] or "")
