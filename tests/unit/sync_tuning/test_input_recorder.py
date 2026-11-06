"""The bridge's own recording of one input: direct node access, one format, no selection.

The recorder is a shell-out on purpose (``pw-record`` / ``parecord``), so these tests read its
argv and the WAV it hands the phone decode path.  The process itself is faked one layer up.
"""

from __future__ import annotations

from sendspin_bridge.sync_tuning import audio_decode, input_recorder


def test_the_pipewire_recorder_opens_the_node_it_is_given():
    argv = input_recorder.capture_argv("bluez_input.AA_BB_CC_DD_EE_FF.2", tool="pw-record")
    assert argv == [
        "pw-record",
        "--target",
        "bluez_input.AA_BB_CC_DD_EE_FF.2",
        "--rate",
        "16000",
        "--channels",
        "1",
        "--format",
        "s16",
        "-",
    ]


def test_the_pulse_recorder_opens_the_node_it_is_given():
    argv = input_recorder.capture_argv("alsa_input.usb-mic", tool="parecord")
    assert argv == [
        "parecord",
        "--raw",
        "--device",
        "alsa_input.usb-mic",
        "--rate",
        "16000",
        "--channels",
        "1",
        "--format",
        "s16le",
    ]


def test_the_recorder_opens_the_node_and_nothing_else():
    """Borrow, never select: no card profile, no source selection, no sink or volume command."""
    for tool, node in (("pw-record", "bluez_input.AA_BB_CC_DD_EE_FF.2"), ("parecord", "alsa_input.usb-mic")):
        argv = input_recorder.capture_argv(node, tool=tool)
        assert argv[0] in {"pw-record", "parecord"}
        assert node in argv
        assert "pactl" not in argv and "set-card-profile" not in argv and "set-source-volume" not in argv


def test_the_raw_capture_becomes_the_wav_the_phone_decode_path_reads():
    import struct

    samples = [0, 1200, -1200, 30000, -30000, 7]
    raw = struct.pack(f"<{len(samples)}h", *samples)
    pcm, rate = audio_decode.decode_to_mono(input_recorder.to_wav(raw), max_seconds=5.0)
    assert rate == audio_decode.DECODE_RATE
    assert pcm == raw


def test_to_wav_drops_a_torn_sample_and_declares_the_format():
    wav = input_recorder.to_wav(b"\x01\x00\x02\x00\x03")
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert len(wav) == 44 + 4


# -- a recorder that runs until it is stopped ------------------------------------------------------

import sys  # noqa: E402

import pytest  # noqa: E402

_STREAMING = [
    sys.executable,
    "-c",
    "import sys, time\nwhile True:\n    sys.stdout.buffer.write(b'\\x01\\x00' * 160)\n    sys.stdout.flush()\n    time.sleep(0.01)",
]


def test_a_running_capture_records_until_it_is_stopped():
    capture = input_recorder.open_capture(_STREAMING)
    try:
        assert capture.wait_for_audio(5.0)
    finally:
        raw = capture.stop()
    assert raw and len(raw) % 2 == 0


def test_a_recorder_that_refuses_the_node_is_unavailable_when_stopped():
    capture = input_recorder.open_capture(
        [sys.executable, "-c", "import sys; sys.stderr.write('no such node'); sys.exit(1)"]
    )
    assert not capture.wait_for_audio(5.0)
    with pytest.raises(input_recorder.RecorderUnavailable, match="no such node"):
        capture.stop()


def test_a_recorder_that_cannot_start_is_unavailable():
    with pytest.raises(input_recorder.RecorderUnavailable):
        input_recorder.open_capture(["/nonexistent/recorder"])
