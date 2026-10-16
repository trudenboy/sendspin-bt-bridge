"""Saving MA_ADDON_SLUG or MA_API_URL forgets which Music Assistant add-on was trusted."""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.services.ha import ha_addon


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    import sendspin_bridge.config as config
    from tests.support.api_client import make_client

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"MA_ADDON_SLUG": "", "MA_API_URL": "http://ma:8095"}))
    ha_addon.reset_ma_addon_cache()
    yield make_client()
    ha_addon.reset_ma_addon_cache()


def _remember() -> None:
    ha_addon._ma_addon_slug_cache["http://ma:8095"] = "abcd1234_music_assistant"


@pytest.mark.parametrize("change", [{"MA_ADDON_SLUG": "abcd1234_music_assistant"}, {"MA_API_URL": "http://other:8095"}])
def test_changing_the_trust_inputs_forgets_the_add_on(client, change):
    _remember()

    resp = client.put("/api/v1/config", json={"MA_API_URL": "http://ma:8095", "MA_ADDON_SLUG": "", **change})

    assert resp.status_code == 200
    assert ha_addon._ma_addon_slug_cache == {}


def test_other_settings_keep_it(client):
    _remember()

    resp = client.put(
        "/api/v1/config", json={"BT_CHECK_INTERVAL": 20, "MA_API_URL": "http://ma:8095", "MA_ADDON_SLUG": ""}
    )

    assert resp.status_code == 200
    assert ha_addon._ma_addon_slug_cache == {"http://ma:8095": "abcd1234_music_assistant"}
