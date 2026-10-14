"""Commands on one speaker: claim audio, enable/disable, standby, power save."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import sendspin_bridge.application.devices as devices
import sendspin_bridge.application.status as status
from sendspin_bridge.config.players import device_entries
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


# -- removal ---------------------------------------------------------------------------------------


@pytest.fixture
def config_on_disk(tmp_config, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "CONFIG_FILE", tmp_config)
    unpaired: list[str] = []
    monkeypatch.setattr(config_uc, "_bt_remove_device", lambda mac, adapter="": unpaired.append(mac))
    other = "11:22:33:44:55:66"
    tmp_config.write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {"mac": MAC, "player_name": "Garage", "enabled": False},
                    {"mac": other, "player_name": "Office", "enabled": True},
                ]
            }
        )
    )
    return SimpleNamespace(file=tmp_config, unpaired=unpaired, other=other)


def test_removing_a_speaker_takes_it_off_the_bridge_and_unpairs_it(api_client, registry, config_on_disk):
    from sendspin_bridge.config import _player_id_from_mac

    resp = api_client.delete(f"/api/v1/devices/{_player_id_from_mac(MAC)}")

    assert resp.status_code == 200
    saved = json.loads(config_on_disk.file.read_text())
    assert [d["mac"] for d in device_entries(saved)] == [config_on_disk.other]
    assert config_on_disk.unpaired == [MAC]


def test_removing_an_unknown_speaker_is_404(api_client, registry, config_on_disk):
    assert api_client.delete("/api/v1/devices/ghost").status_code == 404
    assert len(device_entries(json.loads(config_on_disk.file.read_text()))) == 2


def test_a_removed_disabled_speaker_leaves_the_disabled_list(api_client, registry, config_on_disk):
    """The disabled list is built at startup; removing a disabled speaker used
    to leave it there until the bridge restarted."""
    from sendspin_bridge.config import _player_id_from_mac
    from sendspin_bridge.services.bluetooth import device_registry

    device_registry.set_disabled_devices(
        [
            {"mac": MAC, "player_name": "Garage", "enabled": False},
            {"mac": "AA:AA:AA:AA:AA:AA", "player_name": "Attic", "enabled": False},
        ]
    )
    try:
        assert api_client.delete(f"/api/v1/devices/{_player_id_from_mac(MAC)}").status_code == 200
        left = [d["mac"] for d in device_registry.get_device_registry_snapshot().disabled_devices]
        assert left == ["AA:AA:AA:AA:AA:AA"]
    finally:
        device_registry.set_disabled_devices([])


def test_removing_a_running_speaker_finds_it_by_its_player_id(api_client, registry, config_on_disk):
    """A running speaker's id is its player id, which is not derived from its
    MAC the way a disabled one's is; removing it from its menu answered 404."""
    registry.append(_speaker(player_id="65c5ca96-running", mac=config_on_disk.other))

    resp = api_client.delete("/api/v1/devices/65c5ca96-running")

    assert resp.status_code == 200
    saved = json.loads(config_on_disk.file.read_text())
    assert [d["mac"] for d in device_entries(saved)] == [MAC]
    assert config_on_disk.unpaired == [config_on_disk.other]


# -- enabling a configured player that is not running starts it online -------------------------


PULSE_ID = "dac-1"


@pytest.fixture
def disabled_players(tmp_config, monkeypatch):
    """One disabled Bluetooth speaker and one disabled sound card, neither running."""
    from sendspin_bridge.config import _player_id_from_mac

    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": _player_id_from_mac(MAC),
                        "name": "Garage",
                        "enabled": False,
                        "backend": {"type": "bluetooth_a2dp", "mac": MAC},
                    },
                    {
                        "id": PULSE_ID,
                        "name": "DAC",
                        "enabled": False,
                        "backend": {"type": "pulse_sink", "sink": "alsa_output.usb-dac"},
                    },
                ],
            }
        )
    )
    return {"bluetooth_a2dp": _player_id_from_mac(MAC), "pulse_sink": PULSE_ID}


@pytest.fixture
def recording_orchestrator(monkeypatch):
    """Stand-in orchestrator that starts every START_CLIENT it is handed."""
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind
    from sendspin_bridge.services.lifecycle import reconfig_orchestrator as ro

    applied: list = []

    class _Recording:
        def __init__(self, *_a, **_kw):
            pass

        def apply(self, actions):
            applied.extend(actions)
            summary = ro.ReconfigSummary()
            summary.started = [a.to_summary() for a in actions if a.kind is ActionKind.START_CLIENT]
            return summary

    monkeypatch.setattr(ro, "ReconfigOrchestrator", _Recording)
    return applied


@pytest.mark.parametrize("backend", ["bluetooth_a2dp", "pulse_sink"])
def test_enabling_a_player_that_is_not_running_starts_it_without_a_restart(
    api_client, registry, disabled_players, recording_orchestrator, backend
):
    from sendspin_bridge.config import load_config
    from sendspin_bridge.config.players import find_player_by_id
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind

    player_id = disabled_players[backend]

    resp = api_client.patch(f"/api/v1/devices/{player_id}", json={"enabled": True})

    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "restart_required": False}
    starts = [a for a in recording_orchestrator if a.kind is ActionKind.START_CLIENT]
    assert [a.player_id for a in starts] == [player_id]
    assert find_player_by_id(load_config(), player_id)["enabled"] is True


@pytest.mark.parametrize("backend", ["bluetooth_a2dp", "pulse_sink"])
def test_enabling_a_player_the_bridge_cannot_start_still_asks_for_a_restart(
    api_client, registry, disabled_players, monkeypatch, backend
):
    """Without an activation context the orchestrator reports the player in restart_required."""
    from sendspin_bridge.services.lifecycle import bridge_runtime_state

    monkeypatch.setattr(bridge_runtime_state, "get_activation_context", lambda: None)

    resp = api_client.patch(f"/api/v1/devices/{disabled_players[backend]}", json={"enabled": True})

    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "restart_required": True}


def test_a_started_player_leaves_the_disabled_list(api_client, registry, disabled_players, recording_orchestrator):
    from sendspin_bridge.services.bluetooth import device_registry

    device_registry.set_disabled_devices(
        [
            {"mac": MAC, "player_name": "Garage", "enabled": False},
            {"mac": "AA:AA:AA:AA:AA:AA", "player_name": "Attic", "enabled": False},
        ]
    )
    try:
        resp = api_client.patch(f"/api/v1/devices/{disabled_players['bluetooth_a2dp']}", json={"enabled": True})
        assert resp.json()["restart_required"] is False
        left = [d["mac"] for d in device_registry.get_device_registry_snapshot().disabled_devices]
        assert left == ["AA:AA:AA:AA:AA:AA"]
    finally:
        device_registry.set_disabled_devices([])
