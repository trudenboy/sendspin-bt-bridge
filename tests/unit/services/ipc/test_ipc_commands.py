"""The parent↔daemon command set, as a type rather than a convention.

Twelve command strings were parsed by an `elif` chain with no `else`, and each
branch re-derived its own coercion and clamping from raw payload keys.  An
unknown command was a silent no-op; a bad value was a warning and a `continue`
somewhere in the middle of the ladder.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.ipc.commands import (
    InvalidCommand,
    Pause,
    Play,
    Reconnect,
    SetLogLevel,
    SetMinBufferMs,
    SetMute,
    SetRequiredLeadTimeMs,
    SetStandby,
    SetStaticDelayMs,
    SetVolume,
    Stop,
    Transport,
    UnknownCommand,
    decode_command,
    encode_command,
)


def _roundtrip(command):
    return decode_command(encode_command(command))


@pytest.mark.parametrize(
    "command",
    [
        Stop(),
        Pause(),
        Play(),
        SetVolume(value=40),
        SetMute(muted=True),
        Reconnect(delay_s=1.5),
        SetLogLevel(level="DEBUG"),
        SetStaticDelayMs(value=250.0),
        SetRequiredLeadTimeMs(value=120.0),
        SetMinBufferMs(value=80.0),
        Transport(action="next", value=None),
        SetStandby(sink="bluez_sink.AA_BB"),
        SetStandby(sink=None),
    ],
)
def test_every_command_survives_a_roundtrip(command):
    assert _roundtrip(command) == command


def test_the_wire_shape_is_unchanged():
    """A daemon from an older build must still understand what we send."""
    assert encode_command(SetVolume(value=40))["cmd"] == "set_volume"
    assert encode_command(SetVolume(value=40))["value"] == 40
    assert encode_command(Transport(action="volume", value=30))["action"] == "volume"
    assert encode_command(Reconnect(delay_s=2.0))["delay"] == 2.0


# ── clamping lives in the type ───────────────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [(-5, 0), (0, 0), (55, 55), (100, 100), (250, 100)])
def test_volume_is_clamped_on_the_way_in(raw, expected):
    assert decode_command({"cmd": "set_volume", "value": raw}) == SetVolume(value=expected)


@pytest.mark.parametrize(("raw", "expected"), [(-1.0, 0.0), (0.0, 0.0), (5000.0, 5000.0), (9999.0, 5000.0)])
def test_static_delay_is_clamped_on_the_way_in(raw, expected):
    assert decode_command({"cmd": "set_static_delay_ms", "value": raw}) == SetStaticDelayMs(value=expected)


@pytest.mark.parametrize("cmd_name", ["set_required_lead_time_ms", "set_min_buffer_ms"])
def test_buffer_values_are_clamped_on_the_way_in(cmd_name):
    decoded = decode_command({"cmd": cmd_name, "value": 99999})
    assert decoded.value == 30000.0


def test_a_negative_reconnect_delay_becomes_zero():
    assert decode_command({"cmd": "reconnect", "delay": -3}) == Reconnect(delay_s=0.0)


def test_reconnect_without_a_delay_is_immediate():
    assert decode_command({"cmd": "reconnect"}) == Reconnect(delay_s=0.0)


def test_log_level_is_normalised():
    assert decode_command({"cmd": "set_log_level", "level": "debug"}) == SetLogLevel(level="DEBUG")


# ── refusals ─────────────────────────────────────────────────────────────


def test_an_unknown_command_is_reported_not_ignored():
    with pytest.raises(UnknownCommand) as excinfo:
        decode_command({"cmd": "self_destruct"})

    assert "self_destruct" in str(excinfo.value)


def test_a_missing_command_name_is_rejected():
    with pytest.raises(UnknownCommand):
        decode_command({"value": 10})


@pytest.mark.parametrize(
    "payload",
    [
        {"cmd": "set_volume", "value": "loud"},
        {"cmd": "set_volume"},
        {"cmd": "set_static_delay_ms", "value": None},
        {"cmd": "set_log_level", "level": "SHOUT"},
        {"cmd": "set_mute"},
        {"cmd": "transport"},
    ],
)
def test_a_payload_that_cannot_be_honoured_is_rejected(payload):
    with pytest.raises(InvalidCommand):
        decode_command(payload)


def test_a_non_mapping_is_rejected():
    with pytest.raises(InvalidCommand):
        decode_command(["set_volume", 40])
