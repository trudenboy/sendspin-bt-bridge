"""Source input gain/mute persist per input id, next to the rest of the config."""

from __future__ import annotations

import json

import pytest

PHONE = "bluetooth:34:C7:E9:25:A3:A3"
LINE = "local:alsa_input.line"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config, "_config_load_logged_once", False, raising=False)
    return tmp_path


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_gain_and_mute_round_trip_per_input_id(tmp_path):
    from sendspin_bridge.config import load_source_input_state, save_source_input_state

    save_source_input_state(PHONE, volume=42)
    save_source_input_state(PHONE, muted=True)

    assert load_source_input_state(PHONE) == {"volume": 42, "muted": True}
    assert load_source_input_state(LINE) == {}


def test_saving_one_input_does_not_forget_the_other(tmp_path):
    from sendspin_bridge.config import load_source_input_state, save_source_input_state

    save_source_input_state(PHONE, volume=10, muted=False)
    save_source_input_state(LINE, volume=80, muted=True)

    assert load_source_input_state(PHONE) == {"volume": 10, "muted": False}
    assert load_source_input_state(LINE) == {"volume": 80, "muted": True}
    assert _disk(tmp_path)["SENDSPIN_SOURCE_INPUT_STATE"][LINE] == {"volume": 80, "muted": True}


def test_an_out_of_range_volume_is_not_saved(tmp_path):
    from sendspin_bridge.config import load_source_input_state, save_source_input_state

    save_source_input_state(PHONE, volume=101)
    save_source_input_state("headset-mic:80:99:E7:C2:0B:D3", volume=-1)

    assert load_source_input_state(PHONE) == {}
    assert "SENDSPIN_SOURCE_INPUT_STATE" not in _disk(tmp_path)


def test_the_saved_state_is_a_known_config_key(tmp_path):
    from sendspin_bridge.config import load_config, save_source_input_state

    save_source_input_state(LINE, volume=35, muted=True)

    assert load_config()["SENDSPIN_SOURCE_INPUT_STATE"][LINE] == {"volume": 35, "muted": True}
