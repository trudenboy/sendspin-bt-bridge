"""SHOW_TECHNICAL_DETAILS: the server default for the web UI's technical chips and rows.

Off for a new install, on for an install that already had speakers when this key
first appeared. The decision is made once and then stored, so adding a first
speaker later does not flip a new install to "on".
"""

from __future__ import annotations

import json

from sendspin_bridge import config
from sendspin_bridge.application.models.config import BridgeConfig
from sendspin_bridge.config import DEFAULT_CONFIG, migrate_config_payload

PLAYER = {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen"}


def test_is_an_ordinary_config_key():
    assert DEFAULT_CONFIG["SHOW_TECHNICAL_DETAILS"] is False
    assert "SHOW_TECHNICAL_DETAILS" in BridgeConfig.model_fields


def test_an_existing_install_with_speakers_defaults_on():
    cfg = migrate_config_payload({"players": [dict(PLAYER)]}).normalized_config
    assert cfg["SHOW_TECHNICAL_DETAILS"] is True


def test_a_new_install_without_speakers_defaults_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"players": []}))
    assert config.load_config()["SHOW_TECHNICAL_DETAILS"] is False


def test_an_explicit_choice_is_kept():
    cfg = migrate_config_payload({"players": [dict(PLAYER)], "SHOW_TECHNICAL_DETAILS": False}).normalized_config
    assert cfg["SHOW_TECHNICAL_DETAILS"] is False


def test_the_first_write_stamps_the_decision_from_the_state_before_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"BRIDGE_NAME": "x"}))
    config.update_config(lambda c: c.update({"players": [dict(PLAYER)]}))
    stored = json.loads((tmp_path / "config.json").read_text())
    assert stored["SHOW_TECHNICAL_DETAILS"] is False  # new install: adding a speaker does not turn it on


def test_the_first_write_on_an_install_with_speakers_stamps_on(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"players": [dict(PLAYER)]}))
    config.update_config(lambda c: c.update({"BRIDGE_NAME": "y"}))
    stored = json.loads((tmp_path / "config.json").read_text())
    assert stored["SHOW_TECHNICAL_DETAILS"] is True
