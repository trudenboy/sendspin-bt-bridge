"""A Settings save that does not mention the players leaves them, and their timing, as the bridge has them.

The Settings page leaves ``players`` out of its save (its copy may predate a
speaker's latency compensation, air calibration or learned latency written
since). That relies on this contract.
"""

from __future__ import annotations

import json

import pytest

PLAYER = {
    "id": "kitchen",
    "name": "Kitchen",
    "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF", "adapter": ""},
    "static_delay_ms": 40,
    "static_delay_source": "music_assistant",
    "air_to_sound_latency": [
        {"codec": "sbc", "rate": 48000, "adapter": "C0:FB:F9:62:D6:9D", "latency_ms": 152.0, "d_ref_ms": 0}
    ],
}


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config
    import sendspin_bridge.config as config

    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    monkeypatch.setattr(api_config, "CONFIG_FILE", path)
    path.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [PLAYER], "LOG_LEVEL": "INFO"}))
    return path


def test_a_save_without_players_keeps_their_timing(config_file):
    from tests.support.api_client import make_client

    before = json.loads(config_file.read_text())["players"]

    resp = make_client().put("/api/v1/config", json={"LOG_LEVEL": "DEBUG"})

    assert resp.status_code == 200, resp.text
    saved = json.loads(config_file.read_text())
    assert saved["LOG_LEVEL"] == "DEBUG"
    assert saved["players"] == before
