"""One grammar for bluez sink names, in both directions.

The bridge writes these names in `bluetooth/audio.py` — five candidate
spellings, because PipeWire, WirePlumber and PulseAudio each name the same
speaker differently — and reads them back in `sink_monitor.py` with a regex of
its own.  Two descriptions of one grammar, and they had already drifted: the
raw-colon form WirePlumber publishes on Ubuntu 26.04 (issue #314) is a name
the bridge itself selects, and the reader did not recognise it.

A name the bridge can choose must be a name the bridge can read.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.services.audio.sink_names import (
    address_from_sink_name,
    is_bluez_sink_name,
    sink_name_candidates,
)

ADDRESS = DeviceAddress.require("FC:58:FA:EB:08:6C")


# ── the property the two halves must share ───────────────────────────────


def test_every_name_the_bridge_can_choose_reads_back_as_the_same_speaker():
    for name in sink_name_candidates(ADDRESS):
        assert address_from_sink_name(name) == ADDRESS, name


def test_the_raw_colon_name_wireplumber_publishes_is_understood():
    """The drift that was there: selected by the bridge, unreadable to it."""
    assert address_from_sink_name("bluez_output.FC:58:FA:EB:08:6C") == ADDRESS


# ── what the candidates are ──────────────────────────────────────────────


def test_the_candidates_cover_both_audio_servers():
    names = sink_name_candidates(ADDRESS)

    assert "bluez_output.FC_58_FA_EB_08_6C.1" in names  # PipeWire
    assert "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink" in names  # PulseAudio


def test_the_more_specific_names_are_offered_first():
    """The bare forms match many things; the specific ones must win."""
    names = sink_name_candidates(ADDRESS)

    assert names.index("bluez_output.FC_58_FA_EB_08_6C.1") < names.index("bluez_output.FC:58:FA:EB:08:6C")


# ── reading names the bridge did not choose ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
        "bluez_sink.FC_58_FA_EB_08_6C",
        "bluez_output.FC_58_FA_EB_08_6C.a2dp-sink",
        "bluez_output.FC_58_FA_EB_08_6C.1",
        "bluez_sink.fc_58_fa_eb_08_6c.a2dp_sink",
    ],
)
def test_the_shapes_seen_in_the_wild_are_read(name):
    assert address_from_sink_name(name) == ADDRESS


@pytest.mark.parametrize(
    "name",
    [
        "alsa_output.pci-0000_00_1f.3.analog-stereo",
        "sendspin_fallback",
        "bluez_sink.NOT_A_MAC_AT_ALL",
        "bluez_output.FC_58_FA_EB_08",
        "",
    ],
)
def test_a_name_that_is_not_a_speaker_reads_as_nothing(name):
    assert address_from_sink_name(name) is None
    assert is_bluez_sink_name(name) is False


def test_a_bluez_name_is_recognised_as_one():
    assert is_bluez_sink_name("bluez_output.FC_58_FA_EB_08_6C.1") is True
