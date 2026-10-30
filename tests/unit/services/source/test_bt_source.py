"""The Sendspin source follows a phone's Bluetooth capture (#563)."""

from __future__ import annotations

from sendspin_bridge.services.source.bt_source import (
    BLUEZ_INPUT_RE,
    SourceConfig,
    chunk_bytes,
    find_bluez_input,
)

WPCTL = """
PipeWire 'pipewire-0' [1.0.5, renso@HP-ProDesk, cookie:1]
 ├─ Sources:
 │      94. bluez_input.34_C7_E9_25_A3_A3.2
 │     107. alsa_input.pci-0000_00_1b.0.analog-stereo
"""


def test_the_phone_capture_node_is_found_by_name():
    assert find_bluez_input(WPCTL) == "bluez_input.34_C7_E9_25_A3_A3.2"


def test_no_bluetooth_capture_while_no_phone_streams():
    assert find_bluez_input("PipeWire 'pipewire-0'\n ├─ Sources:\n") is None
    # A speaker's output node is not a capture source.
    assert find_bluez_input("bluez_output.6C_5C_3D_35_17_99.1") is None


def test_the_node_pattern_matches_what_pipewire_names():
    name = "bluez_input.6C_5C_3D_35_17_99.0"
    assert BLUEZ_INPUT_RE.fullmatch(name) is not None
    assert BLUEZ_INPUT_RE.fullmatch("bluez_input.34:C7:E9:25:A3:A3.2") is None  # colons never appear


def test_a_chunk_is_ten_milliseconds_of_s16_frames():
    config = SourceConfig()
    assert chunk_bytes(config) == 48000 // 100 * 2 * 2  # 1920 bytes: 480 frames of stereo s16


def test_the_source_config_comes_from_the_environment():
    config = SourceConfig.from_env(
        {"SENDSPIN_SOURCE_PORT": "9001", "SENDSPIN_SOURCE_NAME": "Phone", "SENDSPIN_SOURCE_NODE": "bluez_input.X.0"}
    )
    assert (config.port, config.server_name, config.node) == (9001, "Phone", "bluez_input.X.0")
    defaults = SourceConfig.from_env({})
    assert (defaults.port, defaults.sample_rate, defaults.channels) == (8940, 48000, 2)
