"""The PulseAudio capture: source discovery, profile nudge and the recording command."""

from __future__ import annotations

from sendspin_bridge.services.source.capture import CaptureEndpoint, CommandResult
from sendspin_bridge.services.source.capture.pulse import (
    BLUEZ_SOURCE_RE,
    PulseAudioBackend,
    PulseSource,
    parse_card_profiles,
    parse_short_sources,
    parse_subscribe_line,
)

SHORT_SOURCES = """\
1\talsa_output.pci-0000_00_1b.0.analog-stereo.monitor\tmodule-also-sink.c\ts16le 2ch 44100Hz\tIDLE
9\tbluez_source.34_C7_E9_25_A3_A3\tmodule-bluez5-device.c\ts16le 2ch 44100Hz\tRUNNING
"""

SHORT_SOURCES_TWO_PHONES = """\
9\tbluez_source.34_C7_E9_25_A3_A3\tmodule-bluez5-device.c\ts16le 2ch 44100Hz\tIDLE
12\tbluez_source.FC_58_FA_EB_08_6C.a2dp_source\tmodule-bluez5-device.c\ts16le 2ch 48000Hz\tRUNNING
"""

CARDS = """\
Card #3
\tName: bluez_card.34_C7_E9_25_A3_A3
\tDriver: module-bluez5-device.c
\tActive Profile: off
\tProfiles:
\t\ta2dp-sink: High Fidelity Playback (A2DP Sink)
\t\ta2dp-source: High Fidelity Capture (A2DP Source)
\t\theadset-head-unit: Headset Head Unit
"""


def test_short_sources_are_parsed_into_rows():
    rows = parse_short_sources(SHORT_SOURCES)

    assert rows == [
        PulseSource(
            1, "alsa_output.pci-0000_00_1b.0.analog-stereo.monitor", "module-also-sink.c", "s16le 2ch 44100Hz", "IDLE"
        ),
        PulseSource(9, "bluez_source.34_C7_E9_25_A3_A3", "module-bluez5-device.c", "s16le 2ch 44100Hz", "RUNNING"),
    ]


def test_only_bluez_sources_match_the_phone_pattern():
    assert BLUEZ_SOURCE_RE.match("bluez_source.34_C7_E9_25_A3_A3") is not None
    assert BLUEZ_SOURCE_RE.match("bluez_source.34_C7_E9_25_A3_A3.a2dp_source") is not None
    assert BLUEZ_SOURCE_RE.match("bluez_sink.34_C7_E9_25_A3_A3.a2dp_sink") is None


def test_subscribe_lines_only_fire_for_sources_and_cards():
    assert parse_subscribe_line("Event 'change' on source #9") is True
    assert parse_subscribe_line("Event 'new' on card #3") is True
    assert parse_subscribe_line("Event 'change' on sink #1") is False
    assert parse_subscribe_line("Event 'new' on source-output #12") is False


def test_the_active_profile_is_read_per_card():
    assert parse_card_profiles(CARDS) == {"bluez_card.34_C7_E9_25_A3_A3": "off"}


async def test_the_backend_picks_the_newest_phone_and_reports_it_running():
    async def run(argv, **_kwargs):
        assert argv == ["pactl", "list", "short", "sources"]
        return CommandResult(returncode=0, stdout=SHORT_SOURCES_TWO_PHONES)

    endpoint = await PulseAudioBackend(run=run).find()

    assert endpoint == CaptureEndpoint(
        node="bluez_source.FC_58_FA_EB_08_6C.a2dp_source",
        backend="pulseaudio",
        phone_mac="FC:58:FA:EB:08:6C",
        running=True,
    )


async def test_no_source_when_no_phone_streams():
    async def run(_argv, **_kwargs):
        return CommandResult(returncode=0, stdout=SHORT_SOURCES.splitlines()[0] + "\n")

    assert await PulseAudioBackend(run=run).find() is None


async def test_every_bluez_card_off_the_recording_profile_is_nudged_without_a_mac():
    calls: list[list[str]] = []

    async def run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[1:] == ["list", "cards"]:
            return CommandResult(returncode=0, stdout=CARDS)
        return CommandResult(returncode=0)

    changed = await PulseAudioBackend(run=run).ensure_a2dp_source()

    assert changed is True
    assert ["pactl", "set-card-profile", "bluez_card.34_C7_E9_25_A3_A3", "a2dp-source"] in calls


async def test_an_already_active_profile_is_left_alone():
    calls: list[list[str]] = []

    async def run(argv, **_kwargs):
        calls.append(list(argv))
        return CommandResult(returncode=0, stdout=CARDS.replace("Active Profile: off", "Active Profile: a2dp-source"))

    changed = await PulseAudioBackend(run=run).ensure_a2dp_source("34:C7:E9:25:A3:A3")

    assert changed is True
    assert not any(call[1:2] == ["set-card-profile"] for call in calls)


async def test_the_recording_command_is_parecord_raw_of_the_source():
    captured: list[list[str]] = []

    class _Proc:
        stdout = None
        returncode = None

    async def spawn(*argv, **_kwargs):
        captured.append(list(argv))
        return _Proc()

    backend = PulseAudioBackend(spawn=spawn)
    stream = await backend.open(CaptureEndpoint(node="bluez_source.34_C7_E9_25_A3_A3", backend="pulseaudio"))
    await stream.start()

    assert captured[0][:4] == ["parecord", "--raw", "--device", "bluez_source.34_C7_E9_25_A3_A3"]
    assert captured[0][-1] == "s16le"
