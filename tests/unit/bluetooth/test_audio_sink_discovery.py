"""Sink discovery stops when the speaker's link is already gone (#476).

A just-paired ENEBY drops the link while the bridge is still waiting for its
sink.  Five 3-second retries then burn 12 seconds on a device that is no
longer there, and the reconnect that would fix it starts only afterwards.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from sendspin_bridge.bluetooth import audio

_MAC = "6C:5C:3D:35:17:99"


@pytest.fixture
def no_sinks(monkeypatch):
    """The host knows no Bluetooth sink yet, and no profile switch helps.

    ``list_cards``/``set_card_profile`` are faked too: these tests must never
    reach the host's real PulseAudio, where switching a live card's profile
    drops the speaker.
    """
    monkeypatch.setattr(audio, "list_sinks", lambda: [{"name": "sendspin_fallback"}])
    monkeypatch.setattr(audio, "get_sink_volume", lambda _name: None)
    monkeypatch.setattr(audio, "sink_name_candidates", lambda _address: ["bluez_output.6C_5C_3D_35_17_99.1"])
    monkeypatch.setattr(audio, "list_cards", lambda: [])
    monkeypatch.setattr(audio, "set_card_profile", lambda _name, _profile: True)


def _configure(*, waits, link_alive, allow_hfp: bool = True):
    # allow_hfp keeps the early headset-profile switch out of these sink-search
    # tests; the switch has its own tests below.
    return audio.configure_bluetooth_audio(
        mac_address=_MAC,
        prefer_sbc=False,
        on_sink_found=None,
        host=None,
        wait_with_cancel=waits,
        device=None,
        link_alive=link_alive,
        allow_hfp=allow_hfp,
    )


def test_the_search_stops_as_soon_as_the_link_is_gone(no_sinks, caplog):
    pauses: list[float] = []

    def _wait(seconds: float) -> bool:
        pauses.append(seconds)
        return True

    # Up as the search begins, gone by the first retry.
    alive = iter([True, False])

    with patch.object(audio, "_switch_card_profile_to_a2dp") as profile_switch:
        caplog.set_level(logging.INFO, logger=audio.logger.name)
        assert _configure(waits=_wait, link_alive=lambda: next(alive, False)) is False

    # Only the initial A2DP settle pause; no 3-second retry was spent.
    assert pauses == [audio._A2DP_PROFILE_DELAY]
    profile_switch.assert_not_called()
    messages = [record.getMessage() for record in caplog.records]
    assert any("went away while looking for its audio sink" in message for message in messages)
    assert not any("Could not find Bluetooth sink" in message for message in messages)


def test_a_live_link_still_gets_the_full_retry_budget(no_sinks):
    pauses: list[float] = []

    def _wait(seconds: float) -> bool:
        pauses.append(seconds)
        return True

    with (
        patch.object(audio, "_switch_card_profile_to_a2dp", return_value=False),
        patch.object(audio, "_cycle_card_profile_for_mac", return_value=False),
    ):
        assert _configure(waits=_wait, link_alive=lambda: True) is False

    assert pauses == [audio._A2DP_PROFILE_DELAY] + [audio._SINK_RETRY_DELAY] * (audio._SINK_RETRY_COUNT - 1)


# ---------------------------------------------------------------------------
# A2DP-only playback: PipeWire/WirePlumber restore the last card profile, so a
# speaker that once landed in HFP comes back in HFP on every reconnect (#476).
# ---------------------------------------------------------------------------

_HFP_CARD = {
    "name": "bluez_card.6C_5C_3D_35_17_99",
    "active_profile": "headset-head-unit-cvsd",
    "profiles": ["a2dp-sink", "headset-head-unit-cvsd", "off"],
    "available_profiles": ["a2dp-sink", "headset-head-unit-cvsd", "off"],
}


def _configure_with_card(monkeypatch, *, allow_hfp: bool, card=_HFP_CARD):
    pauses: list[float] = []
    switched: list[tuple[str, str]] = []
    sinks = ["bluez_output.6C_5C_3D_35_17_99.1"]

    monkeypatch.setattr(audio, "list_cards", lambda: [dict(card)])
    monkeypatch.setattr(audio, "set_card_profile", lambda name, profile: switched.append((name, profile)) or True)
    monkeypatch.setattr(audio, "list_sinks", lambda: [{"name": name} for name in sinks])
    monkeypatch.setattr(audio, "get_sink_volume", lambda _name: 50)
    monkeypatch.setattr(audio, "sink_name_candidates", lambda _address: sinks)
    monkeypatch.setattr(audio, "save_device_sink", lambda *_args, **_kwargs: None)

    def _wait(seconds: float) -> bool:
        pauses.append(seconds)
        return True

    ok = audio.configure_bluetooth_audio(
        mac_address=_MAC,
        prefer_sbc=False,
        on_sink_found=None,
        host=None,
        wait_with_cancel=_wait,
        device=None,
        allow_hfp=allow_hfp,
    )
    return ok, pauses, switched


def test_a_restored_headset_profile_is_switched_back_to_a2dp(monkeypatch, no_sinks):
    ok, pauses, switched = _configure_with_card(monkeypatch, allow_hfp=False)

    assert switched == [("bluez_card.6C_5C_3D_35_17_99", "a2dp-sink")]
    # The switch gets the A2DP settle pause, then the sink is found.
    assert pauses == [audio._A2DP_PROFILE_DELAY]
    assert ok is True


def test_a_speaker_allowed_the_headset_profile_keeps_it(monkeypatch, no_sinks):
    ok, _pauses, switched = _configure_with_card(monkeypatch, allow_hfp=True)

    assert switched == []
    assert ok is True


def test_a_transient_profile_switch_refusal_is_retried(monkeypatch, caplog):
    """#476: a fresh connect answers "Input/output error" until BlueZ has the sink (bluez#1898)."""
    attempts: list[tuple[str, str]] = []

    def _flaky(name: str, profile: str) -> bool:
        attempts.append((name, profile))
        return len(attempts) >= 3  # refused twice, then accepted

    monkeypatch.setattr(audio, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    monkeypatch.setattr(audio, "list_cards", lambda: [
        {"name": "bluez_card.6C_5C_3D_35_17_99", "active_profile": "a2dp_source",
         "profiles": ["a2dp_source", "a2dp_sink", "off"]},
    ])
    monkeypatch.setattr(audio, "set_card_profile", _flaky)

    with caplog.at_level(logging.INFO, logger=audio.logger.name):
        switched = audio._switch_card_profile_to_a2dp(_MAC.replace(":", "_"))

    assert switched is True
    assert len(attempts) == 3
    assert all(profile == "a2dp_sink" for _name, profile in attempts)
    assert "Switched bluez_card.6C_5C_3D_35_17_99 to a2dp_sink" in caplog.text


def test_a_card_without_an_a2dp_sink_profile_is_not_retried(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(audio, "list_cards", lambda: [
        {"name": "bluez_card.6C_5C_3D_35_17_99", "active_profile": "a2dp_source",
         "profiles": ["a2dp_source", "off"]},
    ])
    monkeypatch.setattr(audio, "set_card_profile", lambda name, profile: calls.append(profile) or True)

    assert audio._switch_card_profile_to_a2dp(_MAC.replace(":", "_")) is False
    assert calls == []  # nothing to switch to: no pointless retries
