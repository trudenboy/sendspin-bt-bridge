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
