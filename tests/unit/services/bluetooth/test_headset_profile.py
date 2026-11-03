"""A headset's microphone needs the HFP profile; music needs A2DP back."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from sendspin_bridge.bluetooth.audio import CardProfileState
from sendspin_bridge.services.bluetooth import headset
from sendspin_bridge.services.source.capture import CaptureEndpoint
from sendspin_bridge.services.source.capture.router import SourceInputRouter
from sendspin_bridge.services.source.inputs import build_inputs, headset_mic_id, parse_source_id

_MAC = "80:99:E7:C2:0B:D3"


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
    started: list[object] = []
    monkeypatch.setattr(headset, "_scheduled_start", lambda c: started.append(c))
    service = headset.HeadsetProfileService()

    asyncio.run(service.acquire_microphone(_MAC))
    asyncio.run(service.release_microphone(_MAC))

    assert "a2dp_sink" in profiles
    assert service.active(_MAC) is False
    assert started == [client]


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
