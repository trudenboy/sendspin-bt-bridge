"""A headset's microphone needs the HFP profile; music needs A2DP back."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sendspin_bridge.bluetooth.audio import CardProfileState
from sendspin_bridge.services.bluetooth import headset
from sendspin_bridge.services.bluetooth.device_health_state import compute_device_health_state
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.capture import CaptureEndpoint
from sendspin_bridge.services.source.capture.router import SourceInputRouter
from sendspin_bridge.services.source.client import SourceError
from sendspin_bridge.services.source.inputs import build_inputs, headset_mic_id, parse_source_id

_MAC = "80:99:E7:C2:0B:D3"
_SINK = "bluez_sink.80_99_E7_C2_0B_D3.a2dp_sink"


@pytest.fixture(autouse=True)
def _clean_source_state():
    source_state.reset()
    yield
    source_state.reset()


def _card(*, a2dp=True, hfp=True):
    profiles = ["off", "headset-head-unit", "handsfree_head_unit"]
    if a2dp:
        profiles.append("a2dp_sink")
    return CardProfileState(
        active_profile="a2dp_sink" if a2dp else "headset-head-unit",
        profiles=tuple(profiles),
        a2dp_available=a2dp,
        hfp_available=hfp,
    )


def test_classify_uses_form_factor_but_keeps_microphone_capability():
    assert headset.classify(hfp_available=True, a2dp_available=True, form_factor="headset").kind == "headset"
    assert headset.classify(hfp_available=True, a2dp_available=True, form_factor="speaker").kind == "speaker_with_mic"
    assert headset.classify(hfp_available=False, a2dp_available=True).kind == "speaker"


def test_headset_source_id_is_distinct_from_a2dp_source():
    assert headset_mic_id(_MAC) == f"headset-mic:{_MAC}"
    assert parse_source_id(f"headset-mic:{_MAC}") == ("headset_microphone", _MAC)
    assert parse_source_id(f"bluetooth:{_MAC}") == ("bluetooth", _MAC)


def test_build_inputs_lists_a_headset_microphone(monkeypatch):
    inputs = build_inputs(
        {"players": []},
        active_input=f"headset-mic:{_MAC}",
        headsets=[{"mac": _MAC, "name": "Sony", "hfp_available": True}],
    )

    assert len(inputs) == 1
    assert inputs[0].id == f"headset-mic:{_MAC}"
    assert inputs[0].kind == "headset_microphone"
    assert inputs[0].active is True


def test_acquire_stops_the_player_and_switches_to_hfp(monkeypatch):
    client = SimpleNamespace(
        is_running=lambda: True,
        stop_sendspin=AsyncMock(),
        status={},
        _update_status=Mock(),
        start_sendspin=AsyncMock(),
    )
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", lambda _pa: _card())
    monkeypatch.setattr(headset, "set_card_profile", lambda _name, profile: profile == "handsfree_head_unit")
    monkeypatch.setattr(headset, "_client_for_mac", lambda _mac: client)
    service = headset.HeadsetProfileService()

    assert asyncio.run(service.acquire_microphone(_MAC)) is True
    client.stop_sendspin.assert_awaited_once()
    assert service.active(_MAC) is True
    client._update_status.assert_called_with({"profile_mode": "microphone", "a2dp_available": True})


def test_release_restores_a2dp_and_the_player(monkeypatch):
    client = SimpleNamespace(
        is_running=lambda: True,
        stop_sendspin=AsyncMock(),
        status={},
        _update_status=Mock(),
        start_sendspin=AsyncMock(),
    )
    profiles: list[str] = []
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", lambda _pa: _card())
    monkeypatch.setattr(headset, "set_card_profile", lambda _name, profile: profiles.append(profile) or True)
    monkeypatch.setattr(headset, "_client_for_mac", lambda _mac: client)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", lambda: [{"name": "bluez_sink.80_99_E7_C2_0B_D3.a2dp_sink"}])
    service = headset.HeadsetProfileService()

    asyncio.run(service.acquire_microphone(_MAC))
    asyncio.run(service.release_microphone(_MAC))

    assert "a2dp_sink" in profiles
    assert service.active(_MAC) is False
    client.start_sendspin.assert_awaited_once()


def test_router_acquires_the_headset_and_exposes_the_capture():
    class _Base:
        name = "pulseaudio"

        def set_preferred_mac(self, mac):
            self.mac = mac

        async def find(self):
            return CaptureEndpoint(node="bluez_source.80_99_E7_C2_0B_D3", backend="pulseaudio", running=True)

        async def open(self, endpoint):
            raise AssertionError("not opened here")

    class _Headset:
        def __init__(self):
            self.acquired = []

        async def acquire_microphone(self, mac):
            self.acquired.append(mac)
            return True

        async def release_microphone(self, mac):
            raise AssertionError("must not release while the source is active")

    svc = _Headset()
    router = SourceInputRouter(_Base(), f"headset-mic:{_MAC}", headset=svc)

    endpoint = asyncio.run(router.find())

    assert endpoint is not None
    assert endpoint.node.startswith("bluez_source.")
    assert svc.acquired == [_MAC]


# -- a failed restore must leave the operator a way back --------------------


def _restore_rig(*, configure_ok: bool):
    """A client/manager pair whose card reaches A2DP only on a successful configure."""
    state = {"configure_ok": configure_ok, "mode": "hfp"}

    def configure() -> bool:
        if state["configure_ok"]:
            state["mode"] = "a2dp"
        return state["configure_ok"]

    def card(_pa_mac: str) -> CardProfileState:
        return CardProfileState(
            active_profile="a2dp_sink" if state["mode"] == "a2dp" else "handsfree_head_unit",
            profiles=("handsfree_head_unit", "a2dp_sink"),
            a2dp_available=True,
            hfp_available=True,
        )

    client = SimpleNamespace(
        status={},
        bluetooth_sink_name=_SINK,
        is_running=lambda: True,
        stop_sendspin=AsyncMock(),
        start_sendspin=AsyncMock(),
    )
    client._update_status = client.status.update
    manager = SimpleNamespace(
        mac_address=_MAC,
        audio_sink_ready=True,
        profile_mode="microphone",
        a2dp_available=True,
        hfp_available=True,
        _configure_bluetooth_audio_owned=configure,
    )
    client.bt_manager = manager
    return SimpleNamespace(client=client, manager=manager, state=state, card=card)


def _device_health(client) -> str:
    """The card's health code, read from what the client actually published."""
    health = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=True,
            bluetooth_sink_name=client.bluetooth_sink_name,
            connected=True,
            server_connected=True,
            playing=bool(client.status.get("playing", False)),
            recent_events=[],
            extra=dict(client.status),
        )
    )
    return health.code


async def test_a_failed_restore_publishes_a_retryable_state_and_the_retry_clears_it(monkeypatch):
    """A dead end in the SPA: no retry control, no error, and a sink that is gone.

    The card is on HFP after the failed switch.  The device must publish the
    facts the existing ``a2dp_unavailable`` health path reads, stop pointing at
    the sink HFP removed, and the explicit retry must clear it again.
    """
    rig = _restore_rig(configure_ok=False)
    monkeypatch.setattr(headset, "_client_for_mac", lambda _mac: rig.client)
    monkeypatch.setattr(headset, "set_card_profile", lambda _name, _profile: True)
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", rig.card)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", lambda: [{"name": _SINK}])
    service = headset.HeadsetProfileService()
    assert await service.acquire_microphone(_MAC)

    with pytest.raises(SourceError, match="A2DP"):
        await service.release_microphone(_MAC)

    status = rig.client.status
    assert status["a2dp_available"] is False
    assert status["sink_missing"] is True
    assert status["playing"] is False
    # The card still reads HFP, so the device must not claim either end state.
    assert status["profile_mode"] == "hfp"
    assert rig.client.bluetooth_sink_name is None
    assert _device_health(rig.client) == "a2dp_unavailable"
    assert source_state.snapshot().error == "headset_a2dp_restore_failed"

    # The advertised retry is the way back: the same owner runs it and publishes
    # the healthy evidence, so the card leaves a2dp_unavailable and the error clears.
    rig.state["configure_ok"] = True
    assert await service.retry_a2dp(_MAC)
    assert service.blocked(_MAC) is False
    assert rig.client.status["a2dp_available"] is True
    assert rig.client.status["sink_missing"] is False
    assert rig.client.status["profile_mode"] == "a2dp"
    assert rig.client.bluetooth_sink_name == _SINK
    assert rig.manager.audio_sink_ready is True
    assert source_state.snapshot().error is None
    assert _device_health(rig.client) != "a2dp_unavailable"
    rig.client.start_sendspin.assert_awaited_once()


def test_restore_audio_reports_failure_when_the_owned_configure_refuses(monkeypatch):
    """The manager's own configure path can refuse (cancel/ownership fence).

    A refusal is a failure even when the card happens to already look fine:
    the profile was not proven by the owner, so the player must not resume.
    """
    rig = _restore_rig(configure_ok=False)
    rig.state["mode"] = "a2dp"
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", rig.card)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", lambda: [{"name": _SINK}])
    service = headset.HeadsetProfileService()

    assert service._restore_audio(_MAC, rig.manager) is None


def test_restore_audio_rejects_a_card_that_still_reads_hfp_after_configure(monkeypatch):
    """configure returned True, but the card never actually left HFP."""
    rig = _restore_rig(configure_ok=True)
    rig.state["mode"] = "hfp"  # the switch claimed success; the card is still HFP

    def configure_lies() -> bool:
        return True

    rig.manager._configure_bluetooth_audio_owned = configure_lies
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", rig.card)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", lambda: [{"name": _SINK}])
    service = headset.HeadsetProfileService()

    assert service._restore_audio(_MAC, rig.manager) is None


def test_restore_audio_reports_success_only_with_a_real_a2dp_card_and_sink(monkeypatch):
    rig = _restore_rig(configure_ok=True)
    monkeypatch.setattr(headset.bt_audio, "card_profile_state", rig.card)
    monkeypatch.setattr(headset.bt_audio, "list_sinks", lambda: [{"name": _SINK}])
    service = headset.HeadsetProfileService()

    evidence = service._restore_audio(_MAC, rig.manager)

    assert evidence is not None
    card, sink = evidence
    assert card.mode == "a2dp"
    assert sink == _SINK
