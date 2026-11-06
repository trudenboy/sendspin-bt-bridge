"""A device's Microphone hand-off through its owner's own interface.

The owner is given a card adapter and a way to find the speaker's client;
nothing below patches a module global.  Each case drives one transition and
reads the phase and the facts the rest of the bridge sees: the manager's
profile evidence, the device status and the output sink.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from sendspin_bridge.bluetooth.audio import CardProfileState
from sendspin_bridge.services.bluetooth.headset import HeadsetProfileService
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.client import SourceError

MAC = "80:99:E7:C2:0B:D3"
PA = "80_99_E7_C2_0B_D3"
SINK = f"bluez_sink.{PA}.a2dp_sink"


@dataclass
class FakeCards:
    """A headset card: HFP and A2DP offered; switches recorded; refusals scripted."""

    profile: str = "a2dp_sink"
    hfp: bool = True
    refuse: set[str] = field(default_factory=set)
    sink_after_a2dp: bool = True
    switches: list[str] = field(default_factory=list)

    def state(self, pa_mac: str) -> CardProfileState | None:
        assert pa_mac == PA
        profiles = ("off", "a2dp_sink", *(("handsfree_head_unit",) if self.hfp else ()))
        return CardProfileState(
            active_profile=self.profile, profiles=profiles, a2dp_available=True, hfp_available=self.hfp
        )

    def select(self, pa_mac: str, profile: str) -> bool:
        assert pa_mac == PA
        self.switches.append(profile)
        if profile in self.refuse:
            return False
        self.profile = profile
        return True

    def sinks(self) -> set[str]:
        return {SINK} if self.profile == "a2dp_sink" and self.sink_after_a2dp else set()


def _speaker():
    manager = SimpleNamespace(
        mac_address=MAC,
        connected=True,
        profile_mode="a2dp",
        audio_sink_ready=True,
        a2dp_available=True,
        hfp_available=True,
    )
    client = SimpleNamespace(status={}, bt_manager=manager, bluetooth_sink_name=SINK, running=True, starts=0)
    client._update_status = client.status.update
    client.is_running = lambda: client.running

    async def stop():
        client.running = False

    async def start():
        client.running = True
        client.starts += 1

    client.stop_sendspin = stop
    client.start_sendspin = start
    return client


@pytest.fixture(autouse=True)
def _no_host(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    source_state.reset()
    yield
    source_state.reset()


def _owner(cards: FakeCards, client) -> HeadsetProfileService:
    return HeadsetProfileService(cards=cards, clients=lambda _mac: client)


def test_a_device_starts_with_no_handoff():
    assert _owner(FakeCards(), _speaker()).handoff(MAC) == "none"


def test_taking_the_input_stops_the_player_and_publishes_the_microphone():
    cards, speaker = FakeCards(), _speaker()
    owner = _owner(cards, speaker)

    assert asyncio.run(owner.acquire_microphone(MAC)) is True

    assert owner.handoff(MAC) == "microphone"
    assert cards.profile == "handsfree_head_unit"
    assert speaker.running is False
    assert (speaker.bt_manager.profile_mode, speaker.bt_manager.audio_sink_ready) == ("microphone", False)
    assert speaker.status["profile_mode"] == "microphone"


def test_a_card_without_hfp_stays_an_output_and_keeps_playing():
    cards, speaker = FakeCards(hfp=False), _speaker()
    owner = _owner(cards, speaker)

    assert asyncio.run(owner.acquire_microphone(MAC)) is False

    assert owner.handoff(MAC) == "none"
    assert speaker.running is True
    assert cards.switches == []


def test_a_refused_hfp_switch_gives_the_output_back():
    cards, speaker = FakeCards(refuse={"handsfree_head_unit"}), _speaker()
    owner = _owner(cards, speaker)

    assert asyncio.run(owner.acquire_microphone(MAC)) is False

    assert owner.handoff(MAC) == "none"
    assert speaker.running is True
    assert speaker.status["profile_mode"] == "a2dp"


def test_giving_the_input_back_proves_the_sink_and_resumes_the_player():
    cards, speaker = FakeCards(), _speaker()
    owner = _owner(cards, speaker)
    asyncio.run(owner.acquire_microphone(MAC))

    asyncio.run(owner.release_microphone(MAC))

    assert owner.handoff(MAC) == "none"
    assert cards.profile == "a2dp_sink"
    assert speaker.starts == 1
    assert (speaker.bt_manager.profile_mode, speaker.bt_manager.audio_sink_ready) == ("a2dp", True)
    assert speaker.bluetooth_sink_name == SINK
    assert speaker.status["sink_missing"] is False


def test_a_failed_restore_is_sticky_and_says_so():
    cards, speaker = FakeCards(), _speaker()
    owner = _owner(cards, speaker)
    asyncio.run(owner.acquire_microphone(MAC))
    cards.refuse.add("a2dp_sink")

    with pytest.raises(SourceError):
        asyncio.run(owner.release_microphone(MAC))

    assert owner.handoff(MAC) == "restore_failed"
    assert speaker.starts == 0
    assert speaker.bt_manager.audio_sink_ready is False
    assert speaker.bt_manager.a2dp_available is False
    assert speaker.bluetooth_sink_name is None
    assert speaker.status["sink_missing"] is True
    assert source_state.snapshot().error == "headset_a2dp_restore_failed"


def test_a_restore_without_a_sink_is_a_failed_restore():
    cards, speaker = FakeCards(sink_after_a2dp=False), _speaker()
    owner = _owner(cards, speaker)
    asyncio.run(owner.acquire_microphone(MAC))

    with pytest.raises(SourceError):
        asyncio.run(owner.release_microphone(MAC))

    assert owner.handoff(MAC) == "restore_failed"


def test_retrying_a_failed_restore_returns_the_output_and_clears_the_error():
    cards, speaker = FakeCards(), _speaker()
    owner = _owner(cards, speaker)
    asyncio.run(owner.acquire_microphone(MAC))
    cards.refuse.add("a2dp_sink")
    with pytest.raises(SourceError):
        asyncio.run(owner.release_microphone(MAC))
    cards.refuse.clear()

    assert asyncio.run(owner.retry_a2dp(MAC)) is True

    assert owner.handoff(MAC) == "none"
    assert speaker.starts == 1
    assert source_state.snapshot().error is None
