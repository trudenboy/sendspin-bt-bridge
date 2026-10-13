"""Local sink inventory: pactl parsing and stable-identifier matching."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sendspin_bridge.services.audio import sink_inventory
from sendspin_bridge.services.audio.sink_inventory import (
    LocalSink,
    list_local_sinks,
    match_sink,
    parse_pactl_sinks,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "pactl"


def _load(name: str) -> list[LocalSink]:
    return parse_pactl_sinks((FIXTURES / name).read_text())


def _sink(name: str, **ids: str) -> LocalSink:
    return LocalSink(name, name, "idle", "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


def test_parse_usb_dac_with_serial():
    (sink,) = _load("usb_dac_with_serial.txt")
    assert sink.name == "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
    assert sink.description == "USB DAC Analog Stereo"
    assert sink.state == "idle"
    assert sink.sample_spec == "s24le 2ch 96000Hz"
    assert sink.muted is False
    assert sink.volume == 80
    assert sink.active_port == "analog-output"
    assert sink.active_port_available == "availability unknown"
    assert sink.identifiers == {
        "serial": "Generic_USB_DAC_0000A1",
        "bus_path": "pci-0000:00:14.0-usb-0:2:1.0",
        "vendor_id": "0x262a",
        "product_id": "0x3b3c",
        "alsa_long_card_name": "Generic USB DAC at usb-0000:00:14.0-2, high speed",
    }


def test_parse_two_dacs_without_serial():
    first, second = _load("two_usb_dacs_no_serial.txt")
    assert "serial" not in first.identifiers and "serial" not in second.identifiers
    assert first.identifiers["bus_path"] != second.identifiers["bus_path"]
    assert first.state == "running" and second.state == "suspended"
    assert second.muted is True and second.volume == 0


def test_parse_port_availability_per_sink():
    analog, hdmi = _load("analog_and_hdmi_one_card.txt")
    assert analog.active_port == "analog-output-speaker"
    assert analog.active_port_available == "availability unknown"
    assert hdmi.active_port == "hdmi-output-0"
    assert hdmi.active_port_available == "available"
    assert analog.identifiers["bus_path"] == hdmi.identifiers["bus_path"]


def test_parse_not_available_active_port():
    text = (
        (FIXTURES / "analog_and_hdmi_one_card.txt")
        .read_text()
        .replace("Active Port: analog-output-speaker", "Active Port: analog-output-lineout")
    )
    analog = parse_pactl_sinks(text)[0]
    assert analog.active_port_available == "not available"


def test_parse_sink_without_ports_and_empty_input():
    text = "Sink #1\n\tState: RUNNING\n\tName: x\n\tDescription: X\n\tMute: no\n\tVolume: mono: 6554 /  10% / -60 dB\n"
    (sink,) = parse_pactl_sinks(text)
    assert sink.active_port is None and sink.active_port_available is None
    assert sink.volume == 10 and sink.sample_spec == ""
    assert parse_pactl_sinks("") == []


def test_list_local_sinks_excludes_bridge_and_bluez_sinks(monkeypatch):
    sinks = [
        _sink("alsa_output.usb-x"),
        _sink("bluez_sink.AA_BB.a2dp_sink"),
        _sink("bluez_output.AA_BB.1"),
        _sink("sendspin_fallback"),
        _sink("sendspin_standby"),
    ]

    async def fake_source():
        return sinks

    monkeypatch.setattr(sink_inventory, "_list_all_sinks", fake_source)
    result = asyncio.run(list_local_sinks())
    assert [s.name for s in result] == ["alsa_output.usb-x"]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_match_by_serial():
    a = _sink("a", serial="S1", bus_path="p1")
    b = _sink("b", serial="S2", bus_path="p2")
    res = match_sink({"serial": "S2", "bus_path": "p1"}, "a", [a, b])
    assert (res.sink, res.method, res.status) == (b, "serial", "ok")


def test_match_bus_path_when_serial_absent():
    a = _sink("a", bus_path="p1")
    b = _sink("b", bus_path="p2")
    res = match_sink({"serial": "gone", "bus_path": "p2"}, None, [a, b])
    assert (res.sink, res.method, res.status) == (b, "bus_path", "ok")


def test_match_vendor_product_unique():
    a = _sink("a", vendor_id="0x1", product_id="0x2")
    b = _sink("b", vendor_id="0x1", product_id="0x3")
    res = match_sink({"vendor_id": "0x1", "product_id": "0x3"}, None, [a, b])
    assert (res.sink, res.method) == (b, "vendor_product")


def test_match_vendor_product_twice_narrowed_by_alsa_name():
    a = _sink("a", vendor_id="0x1", product_id="0x2", alsa_long_card_name="card A")
    b = _sink("b", vendor_id="0x1", product_id="0x2", alsa_long_card_name="card B")
    res = match_sink({"vendor_id": "0x1", "product_id": "0x2", "alsa_long_card_name": "card B"}, None, [a, b])
    assert (res.sink, res.method, res.status) == (b, "alsa_long_card_name", "ok")


def test_match_vendor_product_twice_without_tiebreaker_is_ambiguous():
    a = _sink("a", vendor_id="0x1", product_id="0x2")
    b = _sink("b", vendor_id="0x1", product_id="0x2")
    res = match_sink({"vendor_id": "0x1", "product_id": "0x2"}, None, [a, b])
    assert (res.sink, res.method, res.status) == (None, None, "sink_ambiguous")


def test_sinks_of_one_card_are_told_apart_by_stored_name():
    analog, hdmi = _load("analog_and_hdmi_one_card.txt")
    res = match_sink(hdmi.identifiers, hdmi.name, [analog, hdmi])
    assert (res.sink, res.method, res.status) == (hdmi, "bus_path", "ok")
    res = match_sink(hdmi.identifiers, None, [analog, hdmi])
    assert res.status == "sink_ambiguous"


def test_match_by_stored_name_only():
    a = _sink("a")
    res = match_sink({}, "a", [a])
    assert (res.sink, res.method, res.status) == (a, "name", "ok")


def test_match_nothing_is_missing():
    res = match_sink({"serial": "S"}, "gone", [_sink("a", serial="other")])
    assert (res.sink, res.method, res.status) == (None, None, "sink_missing")
    assert match_sink({}, None, []).status == "sink_missing"


def test_match_claimed_sink_is_in_use():
    a = _sink("a", serial="S")
    res = match_sink({"serial": "S"}, None, [a], claimed={"a"})
    assert res.status == "sink_in_use"
    assert res.sink == a and res.method == "serial"


def test_two_identical_dacs_resolve_each_player_to_its_own_bus_path():
    first, second = _load("two_usb_dacs_no_serial.txt")
    # Stored names are stale (e.g. the kernel renumbered the cards), only the port is stable.
    ids_first = {k: v for k, v in first.identifiers.items() if k != "alsa_long_card_name"}
    ids_second = {k: v for k, v in second.identifiers.items() if k != "alsa_long_card_name"}
    sinks = [first, second]
    r1 = match_sink(ids_first, "old-name-1", sinks)
    r2 = match_sink(ids_second, "old-name-2", sinks, claimed={r1.sink.name})
    assert (r1.sink, r1.method) == (first, "bus_path")
    assert (r2.sink, r2.method, r2.status) == (second, "bus_path", "ok")


@pytest.mark.parametrize("fixture", ["usb_dac_with_serial.txt", "analog_and_hdmi_one_card.txt"])
def test_fixture_sinks_match_their_own_identifiers(fixture):
    sinks = _load(fixture)
    for s in sinks:
        res = match_sink(s.identifiers, s.name, sinks)
        assert (res.status, res.sink) == ("ok", s)
