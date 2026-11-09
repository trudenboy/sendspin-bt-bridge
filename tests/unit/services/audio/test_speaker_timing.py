"""Timing changes cross the same interface from HTTP, HA and the initializer."""

from types import SimpleNamespace

import pytest

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.bridge.exceptions import IPCError
from sendspin_bridge.services.audio.speaker_timing import SpeakerTiming


def speaker():
    host = SimpleNamespace(status={"static_delay_ms": 0}, static_delay_ms=0, commands=[])
    host.is_running = lambda: True
    host._update_status = host.status.update

    async def send(command):
        host.commands.append(command)

    host._send_subprocess_command = send
    return host


async def test_a_change_applies_then_persists_with_provenance():
    host = speaker()
    saved = []
    owner = SpeakerTiming(host, persist=lambda *args: saved.append(args), busy=lambda: False)
    assert await owner.change("static_delay_ms", 42, source="manual") == 42
    assert host.commands[0].value == 42
    assert host.static_delay_ms == 42
    assert saved == [("static_delay_ms", 42, "manual", None)]
    assert host.status["static_delay_source"] == "manual"


async def test_an_ipc_failure_commits_neither_parent_state_nor_file():
    host = speaker()
    saved = []

    async def refuse(_command):
        raise IPCError("the daemon went away")

    host._send_subprocess_command = refuse
    owner = SpeakerTiming(host, persist=lambda *args: saved.append(args), busy=lambda: False)
    with pytest.raises(IPCError):
        await owner.change("static_delay_ms", 42)
    assert host.static_delay_ms == 0
    assert saved == []


async def test_an_external_write_cannot_interrupt_a_group_measurement():
    host = speaker()
    owner = SpeakerTiming(host, busy=lambda: True)
    with pytest.raises(UseCaseError) as error:
        await owner.change("static_delay_ms", 42)
    assert error.value.code == "session_active"
    assert host.commands == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 5001, True])
async def test_invalid_timing_cannot_reach_the_daemon(value):
    host = speaker()
    owner = SpeakerTiming(host, busy=lambda: False)
    with pytest.raises(UseCaseError):
        await owner.change("static_delay_ms", value)
    assert host.commands == []


@pytest.mark.parametrize("protected", ["group", "air"])
async def test_auto_pending_does_not_initialize_an_already_targeted_speaker(protected, monkeypatch):
    from sendspin_bridge.services.audio.latency_recommendation import LatencyRecommendation

    host = speaker()
    host.status["static_delay_source"] = "auto_pending"
    host.player_id = "speaker-a"
    host.air_calibration_ms = 150 if protected == "air" else None
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_group_for_player_id",
        lambda _id: {"id": "group"} if protected == "group" else None,
    )
    owner = SpeakerTiming(host, busy=lambda: False)
    recommendation = LatencyRecommendation(150, "bluez_delay_report", "medium", "Reported delay")
    assert not await owner.initialize(recommendation, "sbc")
    assert host.commands == []


async def test_a_server_observation_wins_over_an_in_flight_local_change():
    host = speaker()
    saved = []
    owner = SpeakerTiming(host, persist=lambda *args: saved.append(args), busy=lambda: False)
    host._schedule_persist = lambda _key, save, identity, value: save(identity, value)

    async def command_then_remote_edit(command):
        host.commands.append(command)
        owner.observe_server(80)

    host._send_subprocess_command = command_then_remote_edit
    with pytest.raises(UseCaseError) as error:
        await owner.change("static_delay_ms", 42)
    assert error.value.code == "timing_changed"
    assert host.static_delay_ms == 80
    assert saved == [("static_delay_ms", 80, "music_assistant", None)]


async def test_a_debounced_old_server_value_cannot_overwrite_a_new_manual_change():
    host = speaker()
    saved, callbacks = [], []
    host._schedule_persist = lambda _key, save, identity, value: callbacks.append((save, identity, value))
    owner = SpeakerTiming(host, persist=lambda *args: saved.append(args), busy=lambda: False)
    owner.observe_server(80)
    await owner.change("static_delay_ms", 42)
    save, identity, value = callbacks[0]
    save(identity, value)  # the old debounce fires after the user's newer write
    assert saved == [("static_delay_ms", 42, "manual", None)]


async def test_persistence_failure_is_reported_as_applied_but_not_saved():
    host = speaker()

    def fail(*_args):
        raise PermissionError("read-only config")

    owner = SpeakerTiming(host, persist=fail, busy=lambda: False)
    with pytest.raises(UseCaseError) as error:
        await owner.change("static_delay_ms", 42)
    assert error.value.code == "config_write_failed"
    assert host.static_delay_ms == 42
    assert host.status["static_delay_source"] == "manual"
