"""The bug-report device info names speakers by their player name (config schema v6)."""

from types import SimpleNamespace

from sendspin_bridge.application import diagnostics
from sendspin_bridge.bluetooth.bluez import Outcome


def test_collect_bt_device_info_uses_the_player_name(monkeypatch):
    mac = "6C:5C:3D:35:17:99"
    cfg = {"players": [{"id": "p1", "name": "ENEBY Portable", "backend": {"type": "bluetooth_a2dp", "mac": mac}}]}
    monkeypatch.setattr(diagnostics, "load_config", lambda: cfg)
    fake = SimpleNamespace(device_info=lambda _mac: SimpleNamespace(outcome=Outcome.OK, fields={"paired": "yes"}))
    monkeypatch.setattr(diagnostics, "get_bluez", lambda: fake)

    rows = diagnostics._collect_bt_device_info()

    assert rows == [{"mac": mac, "name": "ENEBY Portable", "paired": "yes"}]
