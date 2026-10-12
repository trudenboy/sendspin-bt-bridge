"""Bug-report config redaction for schema v6 players."""

from sendspin_bridge.application import diagnostics
from tests.support.config_v6 import v6_config

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


def test_players_mask_backend_mac_and_mac_names_but_keep_real_names(monkeypatch):
    cfg = v6_config([{"mac": MAC_A, "player_name": "Kitchen"}, {"mac": MAC_B}])
    monkeypatch.setattr(diagnostics, "load_config", lambda: cfg)

    named, nameless = diagnostics._sanitized_config()["players"]

    assert named["name"] == "Kitchen"
    assert nameless["name"] != MAC_B
    assert named["backend"]["mac"] != MAC_A
    assert nameless["backend"]["mac"] != MAC_B
    assert MAC_B not in str(nameless) and MAC_A not in str(named)
