"""DELETE /api/v1/bluetooth/devices/{mac} — never forget a bond a player uses.

Decision 12: the bridge forgets bonds but does not manage players.  A bond
that backs a configured ``bluetooth_a2dp`` player is refused with a named
consequence (``bond_in_use_by_player``) instead of a ``force`` flag; the
operator removes the player, then the bond.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

MAC = "6C:5C:3D:35:17:99"
OTHER_MAC = "AA:BB:CC:DD:EE:01"
PLAYER_ID = "eneby-player"
PLAYER_NAME = "ENEBY20"


@pytest.fixture
def client(tmp_config, api_client):
    """The shared API client, with the config file isolated to this test."""
    return api_client


def _write_players(tmp_config, players: list[dict]) -> None:
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": players, "PLAYER_STATE": {}}))


def _bluetooth_player(mac: str = MAC, adapter: str = "") -> dict:
    return {
        "id": PLAYER_ID,
        "name": PLAYER_NAME,
        "backend": {"type": "bluetooth_a2dp", "mac": mac, "adapter": adapter},
    }


def test_forget_is_refused_when_a_player_uses_the_bond(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player()])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "bond_in_use_by_player"
    assert body["player_id"] == PLAYER_ID
    assert PLAYER_ID in json.dumps(body["remediation"])
    assert "Home" in body["remediation"]
    fake_remove.assert_not_called()


def test_forget_is_allowed_when_no_player_uses_the_bond(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player(mac=OTHER_MAC)])
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 204
    fake_remove.assert_called_once_with(MAC, "")


def test_forget_is_allowed_when_there_are_no_players(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [])
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 204
    fake_remove.assert_called_once_with(MAC, "")


def _pulse_sink_player(name: str = "Desk DAC") -> dict:
    return {
        "id": "desk-dac",
        "name": name,
        "backend": {"type": "pulse_sink", "sink": "alsa_output.usb-dac.analog-stereo"},
    }


def test_the_bond_in_use_refusal_is_a_problem_json_409(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player()])
    monkeypatch.setattr(module, "_bt_remove_device", MagicMock())

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")


def test_a_disabled_player_still_owns_its_bond(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    player = _bluetooth_player()
    player["enabled"] = False
    _write_players(tmp_config, [player])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 409
    assert response.json()["code"] == "bond_in_use_by_player"
    fake_remove.assert_not_called()


def test_a_pulse_sink_player_does_not_own_a_bluetooth_bond(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_pulse_sink_player()])
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 204
    fake_remove.assert_called_once_with(MAC, "")


def test_clearing_another_adapter_leaves_a_pinned_players_bond_alone(client, tmp_config, monkeypatch):
    """The player is pinned to A: clearing B cannot touch it, so the forget is allowed."""
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player(adapter="C0:FB:F9:62:D7:D6")])
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(
        f"/api/v1/bluetooth/devices/{MAC}",
        params={"adapter_mac": "00:15:83:FF:8F:2B"},
    )

    assert response.status_code == 204
    fake_remove.assert_called_once_with(MAC, "00:15:83:FF:8F:2B")


def test_clearing_the_players_own_adapter_is_still_refused(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player(adapter="C0:FB:F9:62:D7:D6")])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(
        f"/api/v1/bluetooth/devices/{MAC}",
        params={"adapter_mac": "C0:FB:F9:62:D7:D6"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "bond_in_use_by_player"
    fake_remove.assert_not_called()


def test_a_player_without_a_configured_adapter_depends_on_every_adapter(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    _write_players(tmp_config, [_bluetooth_player()])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    response = client.delete(
        f"/api/v1/bluetooth/devices/{MAC}",
        params={"adapter_mac": "00:15:83:FF:8F:2B"},
    )

    assert response.status_code == 409
    fake_remove.assert_not_called()


def test_a_player_without_an_id_uses_the_mac_derived_id(client, tmp_config, monkeypatch):
    import sendspin_bridge.application.bluetooth as module
    from sendspin_bridge.config.players import player_id_for_mac

    # The migration drops an id-less player, but the guard must not report an
    # empty ``player_id`` if a config ever reaches it without one (a fixture, a
    # future read path).  Feed the use case that config directly.
    player = _bluetooth_player()
    player.pop("id")
    monkeypatch.setattr(module, "load_config", lambda: {"players": [player], "PLAYER_STATE": {}})
    monkeypatch.setattr(module, "_bt_remove_device", MagicMock())

    response = client.delete(f"/api/v1/bluetooth/devices/{MAC}")

    assert response.status_code == 409
    body = response.json()
    assert body["player_id"] == player_id_for_mac(MAC)
    assert body["player_id"] in body["remediation"]
    assert "Home" in body["remediation"]
    assert "Settings" not in body["remediation"]
