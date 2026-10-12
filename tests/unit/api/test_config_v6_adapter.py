"""API v1 still speaks the v5 config shape while the file is v6 (stage 1)."""

import json

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


def test_get_config_returns_v5_shape(client):
    body = client.get("/api/v1/config").json()
    assert [d["mac"] for d in body["BLUETOOTH_DEVICES"]] == [A, B]
    assert body["BLUETOOTH_DEVICES"][0]["player_name"] == "A"
    assert "id" not in body["BLUETOOTH_DEVICES"][0]
    assert body["LAST_VOLUMES"] == {A: 40}
    assert "players" not in body and "PLAYER_STATE" not in body


def test_saving_the_same_list_keeps_ids_order_and_state(client, tmp_path):
    # An untouched save changes nothing Music Assistant sees.
    payload = client.get("/api/v1/config").json()
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B)]
    assert disk["PLAYER_STATE"]["custom-a"]["sink"] == "s"
    assert disk["PLAYER_STATE"]["custom-a"]["released"] is True
    assert disk["PLAYER_STATE"]["custom-a"]["volume"] == 40
    assert "BLUETOOTH_DEVICES" not in disk and "LAST_VOLUMES" not in disk


def test_saving_a_reordered_list_with_a_new_speaker(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    new = "AA:BB:CC:DD:EE:03"
    payload["BLUETOOTH_DEVICES"] = [payload["BLUETOOTH_DEVICES"][1], {"mac": new, "player_name": "C"}]
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == [player_id_for_mac(B), player_id_for_mac(new)]
    assert "custom-a" not in disk["PLAYER_STATE"]


def test_validate_reports_device_errors_in_the_v5_shape(client):
    payload = client.get("/api/v1/config").json()
    payload["BLUETOOTH_DEVICES"].append({"mac": B, "player_name": "B again"})
    body = client.post("/api/v1/config/validate", json=payload).json()
    assert body["valid"] is False
    assert any(e["field"] == "BLUETOOTH_DEVICES[2].mac" for e in body["errors"])
    assert "BLUETOOTH_DEVICES" in body["normalized_config"]
    assert "players" not in body["normalized_config"]


def test_export_is_v6_without_state_and_import_accepts_v5(client, tmp_path):
    exported = json.loads(client.get("/api/v1/config/export").content)
    assert "players" in exported and "PLAYER_STATE" not in exported
    v5 = {"CONFIG_SCHEMA_VERSION": 5, "BLUETOOTH_DEVICES": [{"mac": B, "player_name": "Only B"}]}
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", json.dumps(v5).encode(), "application/json")})
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["name"] for p in disk["players"]] == ["Only B"]
    assert "BLUETOOTH_DEVICES" not in disk


def test_import_accepts_a_v6_export_and_keeps_ids(client, tmp_path):
    exported = client.get("/api/v1/config/export").content
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", exported, "application/json")})
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B)]
    assert disk["PLAYER_STATE"]["custom-a"]["sink"] == "s"


def test_remove_speaker_through_devices_endpoint(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "_bt_remove_device", lambda *a, **k: None)
    resp = client.delete(f"/api/v1/devices/{player_id_for_mac(B)}")
    assert resp.status_code == 200, resp.text
    assert [p["id"] for p in _disk(tmp_path)["players"]] == ["custom-a"]


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

    sync({"BLUETOOTH_DEVICES": [{"mac": A, "player_name": "A"}], "BLUETOOTH_ADAPTERS": []})

    assert len(sent) == 1
    assert "bluetooth_devices" not in sent[0]["options"]
