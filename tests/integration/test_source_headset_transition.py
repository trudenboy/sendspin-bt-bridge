"""Real source/router/profile transitions with external audio and Sendspin IO replaced."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from sendspin_bridge.services.bluetooth import headset
from sendspin_bridge.services.source import state
from sendspin_bridge.services.source.capture import CaptureEndpoint
from sendspin_bridge.services.source.capture.router import SourceInputRouter
from sendspin_bridge.services.source.client import SourceClient, SourceError, SourceSettings

MAC = "80:99:E7:C2:0B:D3"
MIC = f"headset-mic:{MAC}"
SINK = "bluez_sink.80_99_E7_C2_0B_D3.a2dp_sink"


@pytest.fixture
def rig(monkeypatch, tmp_path):
    state.reset()
    events = []
    audio = SimpleNamespace(
        profile="a2dp_sink",
        restore=True,
        sink=True,
        stop_error=False,
        missing=False,
        refusals=0,
        sink_misses=0,
    )
    player = SimpleNamespace(status={}, running=True, starts=0, bt_manager=SimpleNamespace(mac_address=MAC))
    player.is_running = lambda: player.running
    player._update_status = player.status.update

    async def stop_player():
        events.append("player-stop")
        if audio.stop_error:
            raise RuntimeError("player would not stop")
        player.running = False

    async def start_player():
        events.append("player-start")
        player.starts += 1
        player.running = True

    player.stop_sendspin = stop_player
    player.start_sendspin = start_player

    def switch(_name, profile):
        events.append(profile)
        if profile == "a2dp_sink":
            if not audio.restore:
                return False
            if audio.refusals:
                audio.refusals -= 1
                return False
        audio.profile = profile
        return True

    def list_sinks():
        if not audio.sink:
            return []
        if audio.sink_misses:
            audio.sink_misses -= 1
            return []
        return [{"name": SINK}]

    monkeypatch.setattr(headset, "_client_for_mac", lambda _: player)
    monkeypatch.setattr(headset, "set_card_profile", switch)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", list_sinks)
    monkeypatch.setattr(
        headset.bt_audio,
        "card_profile_state",
        lambda _: headset.bt_audio.CardProfileState(
            active_profile=audio.profile,
            profiles=("handsfree_head_unit", "a2dp_sink"),
            a2dp_available=True,
            hfp_available=True,
        ),
    )
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    service = headset.HeadsetProfileService()
    monkeypatch.setattr(headset, "_service", service)

    class Stream:
        async def start(self):
            events.append("pcm-start")

        async def stop(self):
            events.append("pcm-stop")

        async def chunks(self):
            await asyncio.Event().wait()
            yield b""

    class Backend:
        name = "pulseaudio"

        def set_preferred_mac(self, mac):
            self.mac = mac

        async def find(self):
            if audio.missing:
                return None
            return CaptureEndpoint(node="mic", backend="pulseaudio", phone_mac=self.mac, running=True)

        async def open(self, _endpoint):
            return Stream()

    async def probe(_engine, node):
        return node != "missing"

    router = SourceInputRouter(Backend(), MIC, headset=service, probe=probe)
    client = SourceClient(SourceSettings(server_url="unused", state_dir=tmp_path, active_input=MIC), router)

    class Capture:
        async def start(self):
            events.append("feed-start")

        async def stop(self):
            events.append("feed-stop")

    client._client = SimpleNamespace(create_source_capture=lambda _: Capture(), disconnect=lambda: asyncio.sleep(0))
    yield SimpleNamespace(
        client=client,
        router=router,
        owner=service,
        audio=audio,
        player=player,
        events=events,
        switch=switch,
    )
    state.reset()


@pytest.mark.parametrize("selection", ["local:usb", f"bluetooth:{MAC}", None])
async def test_switch_stops_pcm_before_restoring_a2dp_and_requires_fresh_grant(rig, selection):
    await rig.client._sync_endpoint()
    rig.client._stream_requested = True
    await rig.client._start_capture()
    await rig.client.set_active(selection, play_target=False)
    assert rig.events.index("pcm-stop") < rig.events.index("a2dp_sink")
    assert rig.events.index("feed-stop") < rig.events.index("a2dp_sink")
    assert rig.client._stream is None
    assert not rig.client._stream_requested
    assert rig.player.starts == 1
    await rig.client.stop()


async def test_stop_releases_capture_before_profile_and_preserves_restore_failure(rig):
    await rig.client._sync_endpoint()
    await rig.client._start_capture()
    rig.audio.restore = False
    await rig.client.stop()
    assert rig.events.index("pcm-stop") < rig.events.index("a2dp_sink")
    assert state.snapshot().error == "headset_a2dp_restore_failed"
    assert not state.snapshot().enabled
    assert rig.owner.blocked(MAC)
    assert not rig.owner.capture_owned(MAC)
    assert rig.player.starts == 0
    await rig.client.stop()
    assert rig.events.count("a2dp_sink") == 1


async def test_player_stop_failure_aborts_microphone_acquisition(rig):
    rig.audio.stop_error = True
    assert not await rig.owner.acquire_microphone(MAC)
    assert rig.audio.profile == "a2dp_sink"
    assert not rig.owner.active(MAC)


@pytest.mark.parametrize("failure", ["switch", "sink", "actual_profile"])
async def test_restore_requires_actual_a2dp_and_sink_then_explicit_recovery(rig, monkeypatch, failure):
    await rig.owner.acquire_microphone(MAC)
    if failure == "switch":
        rig.audio.restore = False
    elif failure == "sink":
        rig.audio.sink = False
    else:
        monkeypatch.setattr(headset, "set_card_profile", lambda *_: True)
    with pytest.raises(SourceError, match="A2DP"):
        await rig.owner.release_microphone(MAC)
    assert rig.owner.blocked(MAC)
    assert rig.player.starts == 0
    assert not await rig.owner.acquire_microphone(MAC)
    rig.audio.restore = rig.audio.sink = True
    rig.audio.profile = "a2dp_sink"
    assert await rig.owner.retry_a2dp(MAC)
    assert not rig.owner.blocked(MAC)
    assert rig.player.starts == 1
    assert rig.player.bt_manager.audio_sink_ready
    assert rig.player.bt_manager.profile_mode == "a2dp"
    assert rig.player.bluetooth_sink_name == SINK


@pytest.mark.parametrize("transient", ["profile_refusal", "late_sink"])
async def test_a_transient_restore_hiccup_is_not_terminal(rig, monkeypatch, transient):
    """The configure path retries; a one-off hiccup must not fail the call for good.

    PipeWire/PulseAudio routinely answers the first ``set-card-profile`` with
    "Input/output error" and publishes the bluez sink a moment late.  A
    restore failure is sticky by design, so the restore must use the
    manager's own retrying configure path instead of one bare switch and one
    ``list_sinks()`` read.
    """
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    await rig.owner.acquire_microphone(MAC)

    sink_found: list[tuple[str, int | None]] = []
    manager = object.__new__(BluetoothManager)
    manager.mac_address = MAC
    manager.device_name = "Test headset"
    manager.connected = True
    manager.management_enabled = True
    manager._cancel_reconnect = threading.Event()
    manager._enable_a2dp_dance = False
    manager._early_flip_seq = 0
    manager._transition_seq = 0
    manager.prefer_sbc = False
    manager.allow_hfp = False
    manager.host = None
    manager.audio_sink_ready = False
    manager.profile_mode = "microphone"
    manager.on_sink_found = lambda name, volume: sink_found.append((name, volume))
    manager.is_device_connected = lambda: True
    manager._adapter_handle = SimpleNamespace(hci_name="")
    manager._device = SimpleNamespace(controller="", transport_state_blocking=lambda: "idle")
    rig.player.bt_manager = manager

    # The manager's real configure path, with only its external audio IO faked.
    monkeypatch.setattr(headset.bt_audio, "set_card_profile", rig.switch)
    monkeypatch.setattr(headset.bt_audio, "PROFILE_SWITCH_RETRY_S", (0.0, 0.0))
    monkeypatch.setattr(headset.bt_audio, "_A2DP_PROFILE_DELAY", 0.0)
    monkeypatch.setattr(headset.bt_audio, "_SINK_RETRY_DELAY", 0.0)
    monkeypatch.setattr(headset.bt_audio, "set_sink_mute", lambda *_: True)
    # The configure path also asks PulseAudio for a sink's volume; without this
    # the rig reads the host's real sinks and a connected speaker on the
    # developer's machine decides the test's outcome.
    monkeypatch.setattr(headset.bt_audio, "get_sink_volume", lambda *_: None)
    monkeypatch.setattr(headset.bt_audio, "save_device_sink", lambda *_: None)
    monkeypatch.setattr(
        headset.bt_audio,
        "list_cards",
        lambda: [
            {
                "name": f"bluez_card.{MAC.replace(':', '_')}",
                "active_profile": rig.audio.profile,
                "profiles": ["handsfree_head_unit", "a2dp_sink"],
            }
        ],
    )
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "load_player_state_for_mac", lambda *_: {})

    rig.audio.refusals = 1 if transient == "profile_refusal" else 0
    rig.audio.sink_misses = 2 if transient == "late_sink" else 0
    await rig.owner.release_microphone(MAC)

    assert state.snapshot().error is None
    assert not rig.owner.blocked(MAC)
    assert rig.audio.profile == "a2dp_sink"
    assert rig.player.starts == 1
    assert rig.player.bluetooth_sink_name == SINK
    assert manager.audio_sink_ready is True
    assert manager.profile_mode == "a2dp"
    # The restore also registers/persists the sink, which the bare switch skipped.
    assert sink_found == [(SINK, None)]


async def test_a_missing_microphone_endpoint_releases_the_headset(rig, monkeypatch):
    import sendspin_bridge.services.source.capture.router as router_module

    rig.audio.missing = True

    async def instant(_delay):
        return None

    monkeypatch.setattr(router_module, "asyncio", SimpleNamespace(sleep=instant))

    assert await rig.router.find() is None

    # The router found no endpoint, so it must have released the microphone
    # (restoring A2DP and resuming the Player) rather than leaving it held.
    assert rig.events.index("handsfree_head_unit") < rig.events.index("a2dp_sink")
    assert not rig.owner.blocked(MAC)
    assert rig.player.starts == 1


async def test_cancelling_find_while_waiting_for_the_microphone_releases_the_headset(rig, monkeypatch):
    import sendspin_bridge.services.source.capture.router as router_module

    rig.audio.missing = True
    entered = asyncio.Event()

    async def wait_forever(_delay):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(router_module, "asyncio", SimpleNamespace(sleep=wait_forever))

    task = asyncio.create_task(rig.router.find())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cancellation is cleaned up: the release restores A2DP and the Player
    # before the cancellation is delivered.
    assert "a2dp_sink" in rig.events
    assert not rig.owner.blocked(MAC)
    assert rig.player.starts == 1


async def test_failed_selection_restores_choice_without_reusing_waiting_grant(rig):
    await rig.client._sync_endpoint()
    rig.client._stream_requested = True
    with pytest.raises(SourceError):
        await rig.client.set_active("local:missing", play_target=False)
    assert rig.client.active_input == MIC
    assert rig.router.active_input == MIC
    assert rig.client._stream is None
    assert not rig.client._stream_requested
    await rig.client.stop()


async def test_cancel_during_profile_switch_waits_for_io_then_restores_and_resumes_once(rig, monkeypatch):
    import threading

    entered, finish = threading.Event(), threading.Event()
    original = headset.set_card_profile

    def switch(name, profile):
        if profile == "handsfree_head_unit":
            entered.set()
            finish.wait(2)
        return original(name, profile)

    monkeypatch.setattr(headset, "set_card_profile", switch)
    task = asyncio.create_task(rig.client._sync_endpoint())
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.audio.profile == "a2dp_sink"
    assert rig.player.starts == 1
    assert not rig.owner.blocked(MAC)


async def test_automatic_player_start_and_configuration_cannot_take_microphone(rig):
    from sendspin_bridge.bluetooth.manager import BluetoothManager
    from sendspin_bridge.bridge.client import SendspinClient

    await rig.owner.acquire_microphone(MAC)
    player = SendspinClient("Test", "localhost", 9000)
    player.bt_manager = rig.player.bt_manager
    # A real Player must refuse before reaching daemon/network setup.
    await player.start_sendspin()
    assert not player.is_running()
    # An uninitialized real manager suffices: the guard must precede all IO.
    manager = object.__new__(BluetoothManager)
    manager.mac_address = MAC
    assert not await asyncio.to_thread(manager.configure_bluetooth_audio)
    assert not await rig.owner.retry_a2dp(MAC)
    assert rig.audio.profile == "handsfree_head_unit"
    await rig.owner.release_microphone(MAC)


async def test_cancel_while_restoring_waits_and_releases_ownership(rig, monkeypatch):
    import threading

    await rig.client._sync_endpoint()
    entered, finish = threading.Event(), threading.Event()
    original = headset.set_card_profile

    def switch(name, profile):
        if profile == "a2dp_sink":
            entered.set()
            finish.wait(2)
        return original(name, profile)

    monkeypatch.setattr(headset, "set_card_profile", switch)
    task = asyncio.create_task(rig.client.set_active("local:usb", play_target=False))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rig.owner.blocked(MAC)
    assert rig.player.starts == 1
    assert rig.client._stream is None


async def test_retry_during_acquisition_is_rejected_without_queued_recovery(rig, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    original = rig.player.stop_sendspin

    async def stop():
        entered.set()
        await finish.wait()
        await original()

    rig.player.stop_sendspin = stop
    task = asyncio.create_task(rig.owner.acquire_microphone(MAC))
    await entered.wait()
    assert rig.owner.blocked(MAC)
    retry = asyncio.create_task(rig.owner.retry_a2dp(MAC))
    await asyncio.sleep(0)
    assert retry.done()
    assert not await retry
    finish.set()
    await task
    await rig.owner.release_microphone(MAC)
