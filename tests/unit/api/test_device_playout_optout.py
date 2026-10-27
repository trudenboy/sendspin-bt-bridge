"""PATCH /devices/{id} with ``playout_sync_enabled``: the per-speaker opt-out."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import sendspin_bridge.application.devices as devices
import sendspin_bridge.application.status as status
from sendspin_bridge.config.players import find_player_by_id
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from sendspin_bridge.services.infrastructure.config_diff import ActionKind
from sendspin_bridge.services.lifecycle import reconfig_orchestrator as ro
from tests.support.api_client import make_client
from tests.support.config_v6 import v6_config

KITCHEN = "AA:BB:CC:DD:EE:01"
HALL = "AA:BB:CC:DD:EE:02"


@pytest.fixture
def two_players(tmp_config, monkeypatch):
    cfg = v6_config(
        [{"mac": KITCHEN, "player_name": "Kitchen"}, {"mac": HALL, "player_name": "Hall"}], PLAYOUT_SYNC="loop"
    )
    tmp_config.write_text(json.dumps(cfg))
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    return cfg


@pytest.fixture
def applied(monkeypatch):
    """The action lists the reconfig orchestrator was asked to apply."""
    calls: list[list] = []

    def _apply(self, actions):
        calls.append(list(actions))
        return MagicMock(started=[], restart_required=[])

    monkeypatch.setattr(ro.ReconfigOrchestrator, "apply", _apply)
    return calls


def _stored(tmp_config, player_id):
    return find_player_by_id(json.loads(tmp_config.read_text()), player_id)


def test_the_flag_is_persisted_and_the_updated_state_answered(two_players, tmp_config, applied):
    kitchen = two_players["players"][0]["id"]
    resp = make_client().patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False})

    assert resp.status_code == 200, resp.text
    assert resp.json()["playout_sync_enabled"] is False
    assert _stored(tmp_config, kitchen)["playout_sync_enabled"] is False


def test_a_running_speaker_answers_its_device(two_players, tmp_config, applied, monkeypatch):
    kitchen = two_players["players"][0]["id"]
    client = MagicMock()
    client.player_id = kitchen
    client.player_name = "Kitchen"
    client.to_dict.return_value = {
        "player_id": kitchen,
        "player_name": "Kitchen",
        "backend_type": "bluetooth_a2dp",
        "bluetooth_mac": KITCHEN,
    }
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[client]))

    resp = make_client().patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False})

    assert resp.status_code == 200, resp.text
    assert resp.json()["device"]["id"] == kitchen
    from sendspin_bridge.application.models.devices import Device

    # The nested device is a complete Device: required keys with null values stay.
    assert Device.model_validate(resp.json()["device"]).playout_sync_enabled is False
    assert "config_id" in resp.json()["device"]
    assert "enabled" not in resp.json()
    assert resp.json()["device"]["playout_sync_enabled"] is False


def test_the_change_warm_restarts_that_speaker_only(two_players, tmp_config, applied):
    kitchen = two_players["players"][0]["id"]
    make_client().patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False})

    (actions,) = applied
    assert [(a.kind, a.player_id) for a in actions] == [(ActionKind.WARM_RESTART, kitchen)]
    assert _stored(tmp_config, two_players["players"][1]["id"]).get("playout_sync_enabled") is None


def test_setting_the_stored_value_again_restarts_nothing(two_players, tmp_config, applied):
    kitchen = two_players["players"][0]["id"]
    client = make_client()
    client.patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False})
    applied.clear()

    assert client.patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False}).status_code == 200
    assert applied == []


def test_an_unknown_speaker_is_404(two_players, applied):
    assert make_client().patch("/api/v1/devices/ghost", json={"playout_sync_enabled": False}).status_code == 404


def test_a_string_is_refused(two_players, applied):
    kitchen = two_players["players"][0]["id"]
    resp = make_client().patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": "no"})
    assert resp.status_code == 422


def test_the_device_model_defaults_to_enabled():
    from sendspin_bridge.application.models.devices import Device

    assert Device.model_fields["playout_sync_enabled"].default is True


# -- authentication and CSRF -----------------------------------------------------------------

PASSWORD = "correct horse battery"


@pytest.fixture
def secured(two_players, tmp_config, applied, monkeypatch):
    from sendspin_bridge.config import hash_password

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cfg = {**two_players, "AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password(PASSWORD)}
    tmp_config.write_text(json.dumps(cfg))
    return make_client(cfg), two_players["players"][0]["id"]


def test_it_needs_a_signed_in_principal(secured):
    client, kitchen = secured
    assert client.patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False}).status_code == 401


def test_a_session_write_needs_the_csrf_token(secured, tmp_config):
    client, kitchen = secured
    signed_in = client.post("/api/v1/auth/session", json={"method": "password", "password": PASSWORD})
    csrf = signed_in.json()["csrf_token"]

    missing = client.patch(f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False})
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"
    assert _stored(tmp_config, kitchen).get("playout_sync_enabled") is None

    ok = client.patch(
        f"/api/v1/devices/{kitchen}", json={"playout_sync_enabled": False}, headers={"X-CSRF-Token": csrf}
    )
    assert ok.status_code == 200, ok.text
    assert _stored(tmp_config, kitchen)["playout_sync_enabled"] is False


def test_the_legacy_enabled_reply_is_unchanged(two_players, tmp_config, applied):
    kitchen = two_players["players"][0]["id"]
    resp = make_client().patch(f"/api/v1/devices/{kitchen}", json={"enabled": False})

    assert resp.json() == {"enabled": False, "restart_required": True}
