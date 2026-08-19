"""api_status diagnostics collectors go through BluezControl (batch 1).

``_collect_bluetooth_daemon_status`` / ``_collect_adapter_diagnostics`` /
``_collect_environment`` used to spawn ``bluetoothctl`` inline with
hand-rolled parsing (one of the two ``_parse_bluetoothctl_adapter``
copies lived here).  After the migration every ``bluetoothctl list`` /
``--version`` invocation flows through ``get_bluez()``; these tests pin
the mapping onto the shared FakeBluez.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace


def test_collect_environment_reports_bluez_version(installed_bluez):
    installed_bluez.on("--version", stdout="bluetoothctl: 9.99-fake\n")

    from sendspin_bridge.web.routes import api_status

    env = api_status._collect_environment()

    assert env["bluez"] == "bluetoothctl: 9.99-fake"
    assert any("--version" in c.argv for c in installed_bluez.commands)


def test_collect_environment_bluez_unknown_when_unavailable(installed_bluez):
    installed_bluez.fail("--version")

    from sendspin_bridge.web.routes import api_status

    env = api_status._collect_environment()

    assert env["bluez"] == "unknown"


def test_collect_environment_bluez_unknown_on_timeout(installed_bluez):
    installed_bluez.timeout("--version")

    from sendspin_bridge.web.routes import api_status

    env = api_status._collect_environment()

    assert env["bluez"] == "unknown"


def test_bluetooth_daemon_status_active_goes_through_bluez(installed_bluez):
    from sendspin_bridge.web.routes import api_status

    assert api_status._collect_bluetooth_daemon_status() == "active"
    assert any(c.argv == ("bluetoothctl", "list") for c in installed_bluez.commands)


def test_bluetooth_daemon_status_falls_back_to_systemctl(installed_bluez, monkeypatch):
    """No controller rows → the systemd probe disambiguates daemon-down
    from adapter-not-passed-through (issue #254 path, route side)."""
    installed_bluez.on("list", stdout="")  # daemon reachable, zero controllers

    from sendspin_bridge.web.routes import api_status

    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if list(args[:2]) == ["systemctl", "is-active"]:
            return SimpleNamespace(stdout="inactive\n")
        return real_run(args, **kwargs)

    monkeypatch.setattr(api_status.subprocess, "run", fake_run)

    assert api_status._collect_bluetooth_daemon_status() == "inactive"


def test_collect_adapter_diagnostics_maps_controller_rows(installed_bluez):
    installed_bluez.on(
        "list",
        stdout="Controller 11:22:33:44:55:66 FakeOne [default]\nController AA:BB:CC:DD:EE:00 FakeTwo\n",
    )

    from sendspin_bridge.web.routes import api_status

    assert api_status._collect_adapter_diagnostics() == [
        {"id": "hci0", "mac": "11:22:33:44:55:66", "default": True},
        {"id": "hci1", "mac": "AA:BB:CC:DD:EE:00", "default": False},
    ]


# ---------------------------------------------------------------------------
# _collect_bt_device_info (bugreport per-device info, batch 2)
# ---------------------------------------------------------------------------


def _patch_bugreport_devices(monkeypatch, devices):
    from sendspin_bridge.web.routes import api_status

    monkeypatch.setattr(api_status, "load_config", lambda: {"BLUETOOTH_DEVICES": devices})


def test_collect_bt_device_info_maps_fields(installed_bluez, monkeypatch):
    _patch_bugreport_devices(monkeypatch, [{"mac": "6C:5C:3D:35:17:99", "name": "ENEBY Portable"}])

    from sendspin_bridge.web.routes import api_status

    rows = api_status._collect_bt_device_info()

    assert rows == [
        {
            "mac": "6C:5C:3D:35:17:99",
            "name": "ENEBY Portable",
            "paired": "yes",
            "bonded": "yes",
            "trusted": "yes",
            "blocked": "no",
            "connected": "no",
            "class": "0x00240404 (2360324)",
            "icon": "audio-headset",
        }
    ]


def test_collect_bt_device_info_marks_error_on_transport_failure(installed_bluez, monkeypatch):
    installed_bluez.fail("info")
    _patch_bugreport_devices(monkeypatch, [{"mac": "6C:5C:3D:35:17:99", "name": "ENEBY Portable"}])

    from sendspin_bridge.web.routes import api_status

    rows = api_status._collect_bt_device_info()

    assert rows[0]["error"] == "Failed to retrieve device info"


def test_collect_bt_device_info_unknown_device_yields_bare_row(installed_bluez, monkeypatch):
    """BlueZ has no object for the MAC → no fields, no error (legacy contract:
    the error key is reserved for transport-level failures)."""
    _patch_bugreport_devices(monkeypatch, [{"mac": "DE:AD:BE:EF:00:01", "name": "Ghost"}])

    from sendspin_bridge.web.routes import api_status

    rows = api_status._collect_bt_device_info()

    assert rows == [{"mac": "DE:AD:BE:EF:00:01", "name": "Ghost"}]
