"""API v1 ``/config`` speaks schema v6: ``players`` in, ``players`` out, never ``PLAYER_STATE``."""

import json
from types import SimpleNamespace

import pytest

from sendspin_bridge.config.players import player_id_for_mac

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.application.config as config_uc
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_uc, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_uc, "_sync_ha_options", lambda *_: None)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": "custom-a", "name": "A", "backend": {"type": "bluetooth_a2dp", "mac": A, "adapter": "hci0"}},
                    {"id": player_id_for_mac(B), "name": "B", "backend": {"type": "bluetooth_a2dp", "mac": B}},
                    {"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.usb-dac"}},
                ],
                "PLAYER_STATE": {"custom-a": {"volume": 40, "sink": "s", "released": True}},
            }
        )
    )


@pytest.fixture()
def client():
    from tests.support.api_client import make_client

    return make_client()


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_get_config_returns_players_without_state(client):
    body = client.get("/api/v1/config").json()
    assert [p["id"] for p in body["players"]] == ["custom-a", player_id_for_mac(B), "dac-1"]
    assert "PLAYER_STATE" not in body and "BLUETOOTH_DEVICES" not in body


def test_untouched_save_keeps_everything_and_changes_nothing(client, tmp_path, monkeypatch):
    # Review focus 1.
    import sendspin_bridge.application.config as config_uc

    seen = []
    real = config_uc.diff_configs
    monkeypatch.setattr(config_uc, "diff_configs", lambda a, b: seen.append(real(a, b)) or seen[-1])
    payload = client.get("/api/v1/config").json()
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B), "dac-1"]
    assert disk["PLAYER_STATE"]["custom-a"] == {"volume": 40, "sink": "s", "released": True}
    assert seen == [[]]
    assert "BLUETOOTH_DEVICES" not in disk and "LAST_VOLUMES" not in disk


def test_effective_listen_values_are_read_only(client, tmp_path, monkeypatch):
    # The running client's port/host are reported, not written into the speaker.
    import sendspin_bridge.application.config as config_uc
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

    fake_client = SimpleNamespace(
        player_name="A",
        listen_port=8928,
        listen_host="bridge.local",
        status={"ip_address": "192.168.10.20"},
        bt_manager=SimpleNamespace(mac_address=A),
    )
    monkeypatch.setattr(
        config_uc, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[fake_client])
    )
    seen = []
    real = config_uc.diff_configs
    monkeypatch.setattr(config_uc, "diff_configs", lambda a, b: seen.append(real(a, b)) or seen[-1])

    body = client.get("/api/v1/config").json()
    assert body["players"][0]["_effective_listen_port"] == 8928
    assert body["players"][0]["_effective_listen_host"] == "bridge.local"
    assert "listen_port" not in body["players"][0] and "listen_host" not in body["players"][0]

    assert client.put("/api/v1/config", json=body).status_code == 200
    saved = _disk(tmp_path)["players"][0]
    assert "listen_port" not in saved and "listen_host" not in saved
    assert not any(key.startswith("_") for key in saved)
    assert seen == [[]]


def test_new_bluetooth_player_gets_uuid5_and_readding_is_idempotent(client, tmp_path):
    # Review focus 4.
    new = "AA:BB:CC:DD:EE:03"
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "C", "backend": {"type": "bluetooth_a2dp", "mac": new.lower()}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    ids = [p["id"] for p in _disk(tmp_path)["players"]]
    assert ids[-1] == player_id_for_mac(new)
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "C again", "backend": {"type": "bluetooth_a2dp", "mac": new}})
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "duplicate_player"


def test_two_players_on_one_sink_are_refused(client):
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "DAC 2", "backend": {"type": "pulse_sink", "sink": "alsa_output.usb-dac"}})
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "duplicate_player"


def test_new_pulse_sink_player_gets_an_id(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "DAC 2", "backend": {"type": "pulse_sink", "sink": "alsa_output.other"}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    added = _disk(tmp_path)["players"][-1]
    assert added["name"] == "DAC 2" and added["id"] not in ("", "custom-a", "dac-1")


def test_backend_of_an_existing_player_is_immutable(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][0]["backend"]["mac"] = "AA:BB:CC:DD:EE:09"
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "player_backend_immutable"


def test_backend_type_of_an_existing_player_is_immutable(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["backend"] = {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:09"}
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "player_backend_immutable"


def test_the_sink_of_an_existing_pulse_sink_player_may_change(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["backend"]["sink"] = "alsa_output.renamed"
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][2]["backend"]["sink"] == "alsa_output.renamed"


def test_pulse_sink_rejects_bluetooth_only_options(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["idle_mode"] = "auto_disconnect"
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "option_not_supported_for_backend"


def test_pulse_sink_rejects_infrasound_keep_alive(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["keep_alive_method"] = "infrasound"
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "option_not_supported_for_backend"


def test_other_player_errors_are_field_errors(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["backend"]["sink"] = ""
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 400
    assert any(e["field"] == "players[2].backend.sink" for e in resp.json()["errors"])


def test_v5_payload_is_refused_not_treated_as_empty(client, tmp_path):
    # Review focus 3.
    before = _disk(tmp_path)
    resp = client.put("/api/v1/config", json={"BLUETOOTH_DEVICES": [{"mac": B}]})
    assert resp.status_code == 400
    assert _disk(tmp_path) == before


def test_client_cannot_set_player_state_or_match(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["PLAYER_STATE"] = {"custom-a": {"volume": 1}}
    payload["players"][2]["backend"]["match"] = {"serial": "forged"}
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert disk["PLAYER_STATE"]["custom-a"]["volume"] == 40
    assert disk["players"][2]["backend"].get("match", {}).get("serial") != "forged"


def test_saving_a_reordered_list_with_a_new_speaker(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    new = "AA:BB:CC:DD:EE:03"
    payload["players"] = [
        payload["players"][2],
        payload["players"][1],
        {"name": "C", "backend": {"type": "bluetooth_a2dp", "mac": new}},
    ]
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["dac-1", player_id_for_mac(B), player_id_for_mac(new)]
    assert "custom-a" not in disk["PLAYER_STATE"]


def test_validate_reports_player_errors_and_previews_without_state(client):
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "B again", "backend": {"type": "bluetooth_a2dp", "mac": B}})
    body = client.post("/api/v1/config/validate", json=payload).json()
    assert body["valid"] is False
    assert any(e["field"] == "players[3].backend.mac" for e in body["errors"])
    assert "players" in body["normalized_config"]
    assert "PLAYER_STATE" not in body["normalized_config"]
    assert "BLUETOOTH_DEVICES" not in body["normalized_config"]


def test_validate_of_a_v5_upload_previews_players_without_state(client):
    v5 = {"BLUETOOTH_DEVICES": [{"mac": B}], "LAST_VOLUMES": {B: 30}}
    body = client.post("/api/v1/config/validate", json=v5).json()
    assert body["valid"] is True
    assert [p["backend"]["mac"] for p in body["normalized_config"]["players"]] == [B]
    assert "PLAYER_STATE" not in body["normalized_config"]


def test_remove_by_id_and_import_v5(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "_bt_remove_device", lambda *a, **k: None)
    assert client.delete(f"/api/v1/devices/{player_id_for_mac(B)}").status_code == 200
    assert [p["id"] for p in _disk(tmp_path)["players"]] == ["custom-a", "dac-1"]
    v5 = {"CONFIG_SCHEMA_VERSION": 5, "BLUETOOTH_DEVICES": [{"mac": B, "player_name": "Only B"}]}
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", json.dumps(v5).encode(), "application/json")})
    assert resp.status_code == 200, resp.text
    assert [p["name"] for p in _disk(tmp_path)["players"]] == ["Only B"]


def test_remove_a_pulse_sink_player_by_id(client, tmp_path):
    assert client.delete("/api/v1/devices/dac-1").status_code == 200
    assert [p["id"] for p in _disk(tmp_path)["players"]] == ["custom-a", player_id_for_mac(B)]


def test_export_is_v6_without_state_and_import_accepts_v5(client, tmp_path):
    exported = json.loads(client.get("/api/v1/config/export").content)
    assert "players" in exported and "PLAYER_STATE" not in exported
    v5 = {"CONFIG_SCHEMA_VERSION": 5, "BLUETOOTH_DEVICES": [{"mac": B, "player_name": "Only B"}]}
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", json.dumps(v5).encode(), "application/json")})
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["name"] for p in disk["players"]] == ["Only B"]
    assert "BLUETOOTH_DEVICES" not in disk


def test_import_accepts_a_v6_export_and_keeps_ids_and_state(client, tmp_path):
    exported = client.get("/api/v1/config/export").content
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", exported, "application/json")})
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B), "dac-1"]
    assert disk["PLAYER_STATE"]["custom-a"]["sink"] == "s"


def test_import_never_takes_player_state_from_the_file(client, tmp_path):
    exported = json.loads(client.get("/api/v1/config/export").content)
    exported["PLAYER_STATE"] = {"custom-a": {"volume": 1}}
    resp = client.post(
        "/api/v1/config/import", files={"file": ("c.json", json.dumps(exported).encode(), "application/json")}
    )
    assert resp.status_code == 200, resp.text
    assert _disk(tmp_path)["PLAYER_STATE"]["custom-a"]["volume"] == 40


def test_ha_options_sync_no_longer_pushes_the_device_list(monkeypatch):
    """The add-on options stop carrying speakers; config.json owns them (schema v6)."""
    import importlib
    import urllib.request

    config_uc = importlib.import_module("sendspin_bridge.application.config")
    sync = config_uc.sync_ha_options  # the real one; the fixture only patched the private name
    sent: list[dict] = []
    monkeypatch.setattr(config_uc, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: sent.append(json.loads(req.data)))

    sync({"players": [{"id": "x", "name": "A", "backend": {"type": "bluetooth_a2dp", "mac": A}}]})

    assert len(sent) == 1
    assert "bluetooth_devices" not in sent[0]["options"]


def test_client_id_never_replaces_the_id_of_a_known_bluetooth_mac(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["players"][0]["id"] = "other-id"
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert disk["players"][0]["id"] == "custom-a"
    assert disk["PLAYER_STATE"]["custom-a"]["volume"] == 40


def test_client_id_is_ignored_for_a_new_bluetooth_mac(client, tmp_path):
    new = "AA:BB:CC:DD:EE:03"
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"id": "mine", "name": "C", "backend": {"type": "bluetooth_a2dp", "mac": new}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][-1]["id"] == player_id_for_mac(new)


def test_unknown_client_id_on_a_new_pulse_sink_player_gets_a_fresh_id(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"id": "mine", "name": "N", "backend": {"type": "pulse_sink", "sink": "alsa_output.n"}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    ids = [p["id"] for p in _disk(tmp_path)["players"]]
    assert ids[2] == "dac-1" and ids[3] != "mine"


def test_two_players_with_one_id_are_refused(client, tmp_path):
    before = _disk(tmp_path)
    payload = client.get("/api/v1/config").json()
    payload["players"] = [
        {"id": "x", "name": "P", "backend": {"type": "pulse_sink", "sink": "alsa_output.p"}},
        {"id": "x", "name": "Q", "backend": {"type": "pulse_sink", "sink": "alsa_output.q"}},
    ]
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "duplicate_player"
    assert _disk(tmp_path) == before


def test_validate_reports_what_a_save_would_refuse(client):
    payload = client.get("/api/v1/config").json()
    payload["players"] = [
        {"id": "x", "name": "P", "backend": {"type": "pulse_sink", "sink": "alsa_output.p"}},
        {"id": "x", "name": "Q", "backend": {"type": "pulse_sink", "sink": "alsa_output.q"}},
    ]
    body = client.post("/api/v1/config/validate", json=payload)
    assert body.status_code == 200
    data = body.json()
    assert data["valid"] is False
    assert any(e["field"].startswith("players[") and "duplicate_player" in e["message"] for e in data["errors"])


def test_validate_reports_an_immutable_backend(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][0]["backend"]["mac"] = "AA:BB:CC:DD:EE:99"
    data = client.post("/api/v1/config/validate", json=payload).json()
    assert data["valid"] is False
    assert any("player_backend_immutable" in e["message"] for e in data["errors"])


def test_stored_pulse_sink_match_survives_a_save(client, tmp_path):
    disk = _disk(tmp_path)
    disk["players"][2]["backend"]["match"] = {"serial": "S123"}
    (tmp_path / "config.json").write_text(json.dumps(disk))
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["name"] = "DAC renamed"
    assert client.put("/api/v1/config", json=payload).status_code == 200
    saved = _disk(tmp_path)["players"][2]
    assert saved["name"] == "DAC renamed" and saved["backend"]["match"] == {"serial": "S123"}


def _sink_present(monkeypatch, *sinks):
    from sendspin_bridge.services.audio.sink_inventory import LocalSink

    async def fake():
        return [
            LocalSink(
                name=name,
                description=name,
                state="idle",
                sample_spec="s16le 2ch 48000Hz",
                muted=False,
                volume=50,
                active_port=None,
                active_port_available=None,
                identifiers=ids,
            )
            for name, ids in sinks
        ]

    monkeypatch.setattr("sendspin_bridge.application.audio.list_local_sinks", fake)


def test_new_pulse_sink_player_stores_the_sink_identifiers_as_match(client, tmp_path, monkeypatch):
    _sink_present(monkeypatch, ("alsa_output.other", {"serial": "S9", "bus_path": "/p"}))
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "DAC 2", "backend": {"type": "pulse_sink", "sink": "alsa_output.other"}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][-1]["backend"]["match"] == {"serial": "S9", "bus_path": "/p"}


def test_pulse_sink_saves_with_an_empty_match_when_its_sink_is_absent(client, tmp_path, monkeypatch):
    _sink_present(monkeypatch)
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "DAC 2", "backend": {"type": "pulse_sink", "sink": "alsa_output.gone"}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    added = _disk(tmp_path)["players"][-1]
    assert added["name"] == "DAC 2" and not added["backend"].get("match")


def test_changing_the_sink_recomputes_the_match(client, tmp_path, monkeypatch):
    disk = _disk(tmp_path)
    disk["players"][2]["backend"]["match"] = {"serial": "OLD"}
    (tmp_path / "config.json").write_text(json.dumps(disk))
    _sink_present(monkeypatch, ("alsa_output.new", {"serial": "NEW"}))
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["backend"]["sink"] = "alsa_output.new"
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][2]["backend"]["match"] == {"serial": "NEW"}


def test_an_empty_stored_match_is_filled_on_the_next_save(client, tmp_path, monkeypatch):
    _sink_present(monkeypatch, ("alsa_output.usb-dac", {"serial": "S1"}))
    payload = client.get("/api/v1/config").json()
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][2]["backend"]["match"] == {"serial": "S1"}


def test_client_sent_match_is_ignored_when_the_sink_is_present(client, tmp_path, monkeypatch):
    _sink_present(monkeypatch, ("alsa_output.usb-dac", {"serial": "REAL"}))
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["backend"]["match"] = {"serial": "forged"}
    assert client.put("/api/v1/config", json=payload).status_code == 200
    assert _disk(tmp_path)["players"][2]["backend"]["match"] == {"serial": "REAL"}
