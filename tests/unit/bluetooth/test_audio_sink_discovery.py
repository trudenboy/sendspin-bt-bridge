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
    """The host knows no Bluetooth sink yet, and no profile switch helps."""
    monkeypatch.setattr(audio, "list_sinks", lambda: [{"name": "sendspin_fallback"}])
    monkeypatch.setattr(audio, "get_sink_volume", lambda _name: None)
    monkeypatch.setattr(audio, "sink_name_candidates", lambda _address: ["bluez_output.6C_5C_3D_35_17_99.1"])


def _configure(*, waits, link_alive):
    return audio.configure_bluetooth_audio(
        mac_address=_MAC,
        prefer_sbc=False,
        on_sink_found=None,
        host=None,
        wait_with_cancel=waits,
        device=None,
        link_alive=link_alive,
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
