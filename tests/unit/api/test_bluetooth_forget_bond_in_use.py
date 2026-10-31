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


def _bluetooth_player(mac: str = MAC) -> dict:
    return {
        "id": PLAYER_ID,
        "name": PLAYER_NAME,
        "backend": {"type": "bluetooth_a2dp", "mac": mac},
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
    assert PLAYER_NAME in json.dumps(body["remediation"])
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
