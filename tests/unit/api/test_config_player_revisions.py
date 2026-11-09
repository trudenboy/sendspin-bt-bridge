"""A stale config document must not put back a player's runtime-owned timing."""

from __future__ import annotations

import copy
import json

import pytest

from sendspin_bridge.config import update_config


@pytest.fixture
def config_file(tmp_config, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "CONFIG_FILE", tmp_config)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": "kitchen",
                        "name": "Kitchen",
                        "enabled": False,
                        "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF", "adapter": ""},
                        "static_delay_ms": 40,
                        "static_delay_source": "music_assistant",
                    }
                ],
            }
        )
    )
    return tmp_config


def _runtime_change():
    def change(config):
        config["players"][0]["static_delay_ms"] = 95
        config["players"][0]["air_learned_latency"] = []

    update_config(change)


def test_a_stale_full_save_is_refused_before_writing(api_client, config_file):
    loaded = api_client.get("/api/v1/config")
    old = loaded.json()
    _runtime_change()
    before = config_file.read_bytes()

    response = api_client.put("/api/v1/config", json=old, headers={"If-Match": loaded.headers.get("etag", '"missing"')})

    assert response.status_code == 412, response.text
    assert response.json()["code"] == "config_conflict"
    assert config_file.read_bytes() == before


def test_a_legacy_full_save_preserves_current_timing(api_client, config_file):
    old = api_client.get("/api/v1/config").json()
    old.pop("_players_revision", None)  # an older client has no revision support
    old["players"][0]["name"] = "Renamed Kitchen"
    _runtime_change()

    response = api_client.put("/api/v1/config", json=old)

    assert response.status_code == 200, response.text
    player = json.loads(config_file.read_text())["players"][0]
    assert player["name"] == "Renamed Kitchen"
    assert player["static_delay_ms"] == 95
    assert player["static_delay_source"] == "music_assistant"
    assert player["air_learned_latency"] == []


def test_a_current_revision_allows_an_intentional_timing_edit(api_client, config_file):
    loaded = api_client.get("/api/v1/config")
    doc = loaded.json()
    doc["players"][0]["static_delay_ms"] = 125

    response = api_client.put("/api/v1/config", json=doc, headers={"If-Match": loaded.headers.get("etag", '"missing"')})

    assert response.status_code == 200, response.text
    assert json.loads(config_file.read_text())["players"][0]["static_delay_ms"] == 125


def test_the_revision_describes_players_not_volume_cache(api_client, config_file):
    first = api_client.get("/api/v1/config")
    update_config(lambda doc: doc.update(PLAYER_STATE={"kitchen": {"volume": 50}}))
    second = api_client.get("/api/v1/config")
    assert first.headers.get("etag") is not None
    assert first.headers["etag"] == second.headers["etag"]


def test_revision_metadata_is_not_saved(api_client, config_file):
    doc = api_client.get("/api/v1/config").json()
    assert "_players_revision" in doc
    assert api_client.put("/api/v1/config", json=doc).status_code == 200
    assert "_players_revision" not in json.loads(config_file.read_text())


def test_a_partial_save_keeps_players_changed_during_validation(api_client, config_file, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    real_read = config_uc._read_sinks_for

    def read_sinks(players):
        # Disk changes after the save copied players for validation, but before its commit lock.
        _runtime_change()
        return real_read(players)

    monkeypatch.setattr(config_uc, "_read_sinks_for", read_sinks)
    response = api_client.put("/api/v1/config", json={"LOG_LEVEL": "DEBUG"})
    assert response.status_code == 200, response.text
    assert json.loads(config_file.read_text())["players"][0]["static_delay_ms"] == 95


def test_a_body_revision_also_protects_clients_that_round_trip_the_document(api_client, config_file):
    old = copy.deepcopy(api_client.get("/api/v1/config").json())
    _runtime_change()
    assert api_client.put("/api/v1/config", json=old).status_code == 412


def test_a_partial_save_keeps_a_player_added_during_validation(api_client, config_file, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    def read_sinks(_players):
        def add(config):
            second = copy.deepcopy(config["players"][0])
            second.update(id="office", name="Office")
            second["backend"]["mac"] = "AA:BB:CC:DD:EE:01"
            config["players"].append(second)

        update_config(add)
        return []

    monkeypatch.setattr(config_uc, "_read_sinks_for", read_sinks)
    response = api_client.put("/api/v1/config", json={"LOG_LEVEL": "DEBUG"})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in json.loads(config_file.read_text())["players"]] == ["kitchen", "office"]


def test_an_explicit_import_replaces_timing_but_does_not_store_revision_metadata(api_client, config_file):
    backup = api_client.get("/api/v1/config").json()
    _runtime_change()
    response = api_client.post(
        "/api/v1/config/import",
        files={"file": ("config.json", json.dumps(backup).encode(), "application/json")},
    )
    assert response.status_code == 200, response.text
    stored = json.loads(config_file.read_text())
    assert stored["players"][0]["static_delay_ms"] == 40
    assert "_players_revision" not in stored


def _own_kitchen_timing(monkeypatch, tmp_path):
    from dataclasses import replace

    from sendspin_bridge.sync_tuning import session_store
    from tests.support.sync_fakes import FakeControl, FakeHost

    control = FakeControl()
    control.member_list = [replace(m, client_id="kitchen") if m.ma_player_id == "a" else m for m in control.member_list]
    store = session_store.SessionStore(
        control_factory=lambda: control, host=FakeHost(), publish=lambda _data: None, snapshot_dir=lambda: tmp_path
    )
    monkeypatch.setattr(session_store, "_store", store)
    store.create("g", "semi", "device")


def test_even_a_current_config_cannot_change_timing_owned_by_a_session(api_client, config_file, monkeypatch, tmp_path):
    _own_kitchen_timing(monkeypatch, tmp_path)
    doc = api_client.get("/api/v1/config").json()
    doc["players"][0]["static_delay_ms"] = 125
    before = config_file.read_bytes()
    response = api_client.put("/api/v1/config", json=doc)
    assert response.status_code == 409 and response.json()["code"] == "session_active"
    assert config_file.read_bytes() == before


def test_a_current_save_with_unchanged_timing_is_allowed_during_tuning(api_client, config_file, monkeypatch, tmp_path):
    _own_kitchen_timing(monkeypatch, tmp_path)
    doc = api_client.get("/api/v1/config").json()
    doc["LOG_LEVEL"] = "DEBUG"
    response = api_client.put("/api/v1/config", json=doc)
    assert response.status_code == 200, response.text
