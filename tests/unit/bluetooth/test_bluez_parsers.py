"""Parser grammars against frozen real-world bluetoothctl transcripts.

Transcripts under ``tests/support/transcripts/bluez-5.72/`` were captured
on the live verification stand (BlueZ 5.72, piped stdin — exactly what
the bridge's subprocess calls see, ANSI escapes included). 5.82/5.86
corpora are captured from the production hosts before rc.1.
"""

from __future__ import annotations

from sendspin_bridge.bluetooth.bluez import (
    Outcome,
    classify_lines,
    parse_adapter_list,
    parse_device_info,
    parse_devices,
    parse_scan,
    parse_show,
    parse_version,
    summarize_connect_output,
)
from tests.support import load_transcript

ADAPTER_MAC = "C0:FB:F9:62:D7:D6"
ENEBY_MAC = "6C:5C:3D:35:17:99"
LENCO_MAC = "30:21:0E:0A:AE:5A"


def test_version_transcript():
    assert parse_version(load_transcript("5.72", "version")) == "bluetoothctl: 5.72"


def test_adapter_list_transcript():
    adapters = parse_adapter_list(load_transcript("5.72", "list"))
    assert [(a.mac, a.name, a.is_default) for a in adapters] == [(ADAPTER_MAC, "HP-ProDesk", True)]


def test_adapter_list_multiple_with_non_default():
    stdout = "Controller C0:FB:F9:62:D7:D6 HP-ProDesk [default]\nController 00:15:83:FF:8F:2B Second Stick\n"
    adapters = parse_adapter_list(stdout)
    assert [(a.mac, a.name, a.is_default) for a in adapters] == [
        (ADAPTER_MAC, "HP-ProDesk", True),
        ("00:15:83:FF:8F:2B", "Second Stick", False),
    ]


def test_show_transcript():
    info = parse_show(load_transcript("5.72", "show"))
    assert info.present is True
    assert info.no_default is False
    assert info.mac == ADAPTER_MAC
    assert info.alias == "HP-ProDesk"
    assert info.name == "HP-ProDesk"
    assert info.powered is True


def test_show_after_select_transcript():
    info = parse_show(load_transcript("5.72", "select-show"))
    assert info.present is True
    assert info.mac == ADAPTER_MAC
    assert info.alias == "HP-ProDesk"


def test_show_no_default_controller():
    info = parse_show("No default controller available\n")
    assert info.present is False
    assert info.no_default is True
    assert info.alias == ""
    assert info.powered is False


def test_device_info_transcript_paired_device():
    info = parse_device_info(load_transcript("5.72", "info-eneby"), ENEBY_MAC)
    assert info.present is True
    assert info.mac == ENEBY_MAC
    assert info.paired is True
    assert info.bonded is True
    assert info.trusted is True
    assert info.blocked is False
    assert info.connected is False
    assert info.name == "ENEBY Portable"
    assert info.alias == "ENEBY Portable"
    # The class field keeps the full value (hex + decimal), matching the
    # legacy /api/bt/info payload; consumers parse the hex out of it.
    assert info.device_class == "0x00240404 (2360324)"
    assert info.icon == "audio-headset"
    assert info.rssi is None
    # ``raw`` reproduces the /api/bt/info payload: ANSI-stripped, stripped,
    # non-empty source lines from **stdout only** — and the field dict is
    # limited to the public _INFO_FIELDS keys.
    assert isinstance(info.raw, tuple)
    assert any(line.startswith("Device 6C:5C:3D:35:17:99") for line in info.raw)
    assert not any("not available" in line for line in info.raw)
    assert set(info.fields) <= {"name", "alias", "paired", "bonded", "trusted", "blocked", "connected", "class", "icon"}


def test_device_info_transcript_second_device():
    info = parse_device_info(load_transcript("5.72", "info-lenco"), LENCO_MAC)
    assert info.present is True
    assert info.name == "Lenco LS-500"
    assert info.paired is True


def test_device_info_missing_device():
    info = parse_device_info(load_transcript("5.72", "info-missing"), "AA:BB:CC:DD:EE:FF")
    assert info.present is False
    assert info.paired is None
    assert info.connected is None
    assert info.fields == {}


def test_device_info_tri_state_contract():
    # paired is bool only when the device is present AND an explicit
    # ``Paired:`` field parsed; transport failures force None so the
    # monitor never escalates to re-pair on a transient timeout.
    timeout = parse_device_info("", ENEBY_MAC, outcome=Outcome.TIMEOUT)
    assert timeout.paired is None
    unavailable = parse_device_info("", ENEBY_MAC, outcome=Outcome.UNAVAILABLE)
    assert unavailable.paired is None
    no_field = parse_device_info(f"Device {ENEBY_MAC} (public)\n\tName: X\n", ENEBY_MAC)
    assert no_field.present is True
    assert no_field.paired is None
    no = parse_device_info(f"Device {ENEBY_MAC} (public)\n\tPaired: no\n", ENEBY_MAC)
    assert no.paired is False


def test_device_info_rssi_formats():
    modern = parse_device_info(f"Device {ENEBY_MAC} (public)\n\tRSSI: -43\n", ENEBY_MAC)
    assert modern.rssi == -43
    legacy = parse_device_info(f"Device {ENEBY_MAC} (public)\n\tRSSI: 0xffffffd5 (-43)\n", ENEBY_MAC)
    assert legacy.rssi == -43


def test_devices_transcript():
    devices = parse_devices(load_transcript("5.72", "devices-paired"))
    assert [(d.mac, d.name) for d in devices] == [(ENEBY_MAC, "ENEBY Portable"), (LENCO_MAC, "Lenco LS-500")]
    # tuple-compatibility for the existing ``for mac, name in pairs`` callers
    mac, name = devices[0]
    assert (mac, name) == (ENEBY_MAC, "ENEBY Portable")


def test_devices_ignore_async_noise():
    stdout = (
        "[\x1b[0;94mbluetoothctl]> \x1b[0m"
        "[\x1b[0;93mCHG\x1b[0m] Device 68:3A:48:D3:62:68 RSSI: 0xffffffaa (-86)\n"
        "[CHG] Device 54:66:39:DC:B9:4D ManufacturerData.Value:\n"
        "  01 00 00 00  ................\n"
        "[NEW] Device 11:22:33:44:55:66 Ghost Discovery\n"
        "[DEL] Device 11:22:33:44:55:66 Ghost Discovery\n"
        "Device FC:58:FA:EB:08:6C ENEBY20\n"
    )
    assert [(d.mac, d.name) for d in parse_devices(stdout)] == [("FC:58:FA:EB:08:6C", "ENEBY20")]


def test_devices_normalise_mac_shaped_names():
    assert parse_devices("Device AA:BB:CC:DD:EE:FF AA:BB:CC:DD:EE:FF\n")[0].name == ""
    assert parse_devices("Device AA:BB:CC:DD:EE:FF aa-bb-cc-dd-ee-ff\n")[0].name == ""


def test_summarize_connect_output_prefers_failure_line():
    lines = classify_lines(
        "\x1b[0;94m[bluetooth]\x1b[0m#             connect 6C:5C:3D:35:17:99\n"
        "Agent registered\n"
        "[CHG] Device 6C:5C:3D:35:17:99 Connected: no\n"
        "Failed to connect: org.bluez.Error.Failed br-connection-page-timeout\n"
        "\x1b[0;94m[bluetooth]\x1b[0m#                         \n"
    )
    assert summarize_connect_output(lines) == "Failed to connect: org.bluez.Error.Failed br-connection-page-timeout"


def test_summarize_connect_output_falls_back_to_last_content_line():
    lines = classify_lines("[CHG] Device 6C:5C:3D:35:17:99 RSSI: -43\nAttempting to connect\n")
    assert summarize_connect_output(lines) == "Attempting to connect"


def test_summarize_connect_output_empty_when_no_signal():
    assert (
        summarize_connect_output(classify_lines("Agent registered\n[CHG] Device 6C:5C:3D:35:17:99 RSSI: -43\n")) == ""
    )
    assert summarize_connect_output([]) == ""


def test_summarize_connect_output_caps_excerpt_length():
    lines = classify_lines("x" * 500 + "\n")
    assert len(summarize_connect_output(lines)) == 200


def test_parse_scan_attributes_devices_and_rssi():
    lines = classify_lines(
        "[NEW] Device 6C:5C:3D:35:17:99 ENEBY Portable\n"
        "[CHG] Device 6C:5C:3D:35:17:99 RSSI: 0xffffffd5 (-43)\n"
        "[CHG] Device 30:21:0E:0A:AE:5A RSSI: -61\n"
        "[CHG] Device 30:21:0E:0A:AE:5A Name: Lenco LS-500\n"
        "Controller C0:FB:F9:62:D7:D6 (public)\n"
        "Device 6C:5C:3D:35:17:99 ENEBY Portable\n"
        "Device 30:21:0E:0A:AE:5A Lenco LS-500\n"
    )
    transcript = parse_scan(lines)
    assert transcript.seen_macs == {ENEBY_MAC}
    assert transcript.active_macs == {ENEBY_MAC, LENCO_MAC}
    assert transcript.names[ENEBY_MAC] == "ENEBY Portable"
    assert transcript.names[LENCO_MAC] == "Lenco LS-500"
    assert transcript.rssi_by_mac == {ENEBY_MAC: -43, LENCO_MAC: -61}
    assert transcript.device_adapter == {ENEBY_MAC: ADAPTER_MAC, LENCO_MAC: ADAPTER_MAC}


def test_parse_scan_ignores_mac_shaped_names():
    lines = classify_lines("[NEW] Device AA:BB:CC:DD:EE:FF AA-BB-CC-DD-EE-FF\n")
    transcript = parse_scan(lines)
    assert transcript.seen_macs == {"AA:BB:CC:DD:EE:FF"}
    assert "AA:BB:CC:DD:EE:FF" not in transcript.names
