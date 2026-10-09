"""Commands on one speaker: claim audio, enable/disable, standby, power save."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import sendspin_bridge.application.devices as devices
import sendspin_bridge.application.status as status
from sendspin_bridge.services.audio.mpris_player import MprisPlayer, get_registry
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture
def registry(monkeypatch):
    clients: list = []
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=clients))
    return clients


@pytest.fixture(autouse=True)
def _clean_mpris():
    get_registry()._by_mac.clear()
    yield
    get_registry()._by_mac.clear()


def _speaker(player_id="kitchen", mac=MAC, **status_fields):
    client = MagicMock()
    client.player_id = player_id
    client.player_name = player_id.title()
    client.status = dict(status_fields)
    client.bt_manager = SimpleNamespace(mac_address=mac, _has_ever_paired_since_start=True, management_enabled=True)
    return client


# -- claim audio (multipoint) ---------------------------------------------------------


def test_claim_pushes_playing_to_the_speakers_mpris_player(api_client, registry, bridge_loop):
    """BlueZ re-asserts the bridge as the active AVRCP source when it reports Playing."""
    registry.append(_speaker())
    player = MprisPlayer(
        mac=MAC,
        player_id="kitchen",
        transport_callback=AsyncMock(return_value=True),
        volume_callback=AsyncMock(return_value=True),
    )
    get_registry().register(MAC, player)

    resp = api_client.post("/api/v1/devices/kitchen/claim")

    assert resp.status_code == 204
    assert player._state.status == "Playing"


def test_claim_without_a_connected_speaker_is_409(api_client, registry):
    registry.append(_speaker())
    resp = api_client.post("/api/v1/devices/kitchen/claim")
    assert resp.status_code == 409
    assert resp.json()["code"] == "not_connected"


# -- enable / disable ------------------------------------------------------------------------


def test_reenabling_clears_the_never_paired_evidence(api_client, registry, monkeypatch):
    """A re-enabled auto-disabled speaker starts a fresh session (#263)."""
    persisted: list[tuple[str, bool]] = []
    monkeypatch.setattr(devices, "_persist_device_enabled", lambda name, enabled: persisted.append((name, enabled)))
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    speaker = _speaker(never_paired=True)
    registry.append(speaker)

    resp = api_client.patch("/api/v1/devices/kitchen", json={"enabled": True})

    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "restart_required": False}
    assert persisted == [("Kitchen", True)]
    cleared = speaker.update_status.call_args.args[0]
    assert cleared["never_paired"] is False
    assert cleared["never_paired_since"] is None
    assert cleared["reconnect_attempt"] == 0
    assert cleared["last_error"] is None
    assert speaker.bt_manager._has_ever_paired_since_start is False


def test_reenabling_tolerates_a_speaker_without_bluetooth_manager(api_client, registry, monkeypatch):
    monkeypatch.setattr(devices, "_persist_device_enabled", lambda *_a: None)
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    speaker = _speaker()
    speaker.bt_manager = None
    registry.append(speaker)

    assert api_client.patch("/api/v1/devices/kitchen", json={"enabled": True}).status_code == 200
    speaker.update_status.assert_called_once()


def test_disabling_a_running_speaker_releases_it(api_client, registry, monkeypatch):
    monkeypatch.setattr(devices, "_persist_device_enabled", lambda *_a: None)
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    speaker = _speaker()
    registry.append(speaker)

    resp = api_client.patch("/api/v1/devices/kitchen", json={"enabled": False})

    assert resp.json() == {"enabled": False, "restart_required": True}
    import time

    deadline = time.monotonic() + 2
    while not speaker.set_bt_management_enabled.called and time.monotonic() < deadline:
        time.sleep(0.01)
    speaker.set_bt_management_enabled.assert_called_once_with(False)


def test_enabling_a_speaker_that_is_not_running_finds_it_in_the_config(api_client, registry, tmp_config, monkeypatch):
    from sendspin_bridge.config import _player_id_from_mac

    tmp_config.write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": MAC, "player_name": "Garage", "enabled": False}]}))
    persisted: list[tuple[str, bool]] = []
    monkeypatch.setattr(devices, "_persist_device_enabled", lambda name, enabled: persisted.append((name, enabled)))
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)

    resp = api_client.patch(f"/api/v1/devices/{_player_id_from_mac(MAC)}", json={"enabled": True})

    assert resp.json() == {"enabled": True, "restart_required": True}
    assert persisted == [("Garage", True)]


def test_unknown_device_cannot_be_enabled(api_client, registry, tmp_config):
    assert api_client.patch("/api/v1/devices/ghost", json={"enabled": True}).status_code == 404


# -- standby / power save ------------------------------------------------------------------------


def test_standby_twice_is_409(api_client, registry):
    registry.append(_speaker(bt_standby=True))
    resp = api_client.post("/api/v1/devices/kitchen/standby")
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_in_standby"


def test_standby_parks_the_player(api_client, registry, bridge_loop):
    speaker = _speaker(bt_standby=False)
    speaker._enter_standby = AsyncMock()
    registry.append(speaker)

    assert api_client.post("/api/v1/devices/kitchen/standby").status_code == 204
    speaker._enter_standby.assert_awaited_once()


@pytest.mark.parametrize(
    ("current", "wanted", "called"), [(False, True, "_enter_power_save"), (True, False, "_exit_power_save")]
)
def test_power_save_switches_only_on_a_change(api_client, registry, bridge_loop, current, wanted, called):
    speaker = _speaker(bt_power_save=current)
    speaker._enter_power_save = AsyncMock()
    speaker._exit_power_save = AsyncMock()
    registry.append(speaker)

    assert api_client.put("/api/v1/devices/kitchen/power-save", json={"enabled": wanted}).status_code == 204
    getattr(speaker, called).assert_awaited_once()

    getattr(speaker, called).reset_mock()
    speaker.status["bt_power_save"] = wanted
    assert api_client.put("/api/v1/devices/kitchen/power-save", json={"enabled": wanted}).status_code == 204
    getattr(speaker, called).assert_not_awaited()
