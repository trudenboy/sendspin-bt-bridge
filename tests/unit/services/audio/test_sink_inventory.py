"""Local sink inventory: pactl parsing and stable-identifier matching."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from sendspin_bridge.services.audio import sink_inventory
from sendspin_bridge.services.audio.sink_inventory import (
    LocalSink,
    list_local_sinks,
    match_players,
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


def test_unplugged_identical_dac_is_not_replaced_by_its_twin():
    first, second = _load("two_usb_dacs_no_serial.txt")
    ids_first = {k: v for k, v in first.identifiers.items() if k != "alsa_long_card_name"}
    ids_second = {k: v for k, v in second.identifiers.items() if k != "alsa_long_card_name"}
    only_b = [second]  # DAC A unplugged
    res_a = match_sink(ids_first, first.name, only_b)
    assert (res_a.sink, res_a.status) == (None, "sink_missing")
    res_b = match_sink(ids_second, second.name, only_b, claimed=set())
    assert (res_b.sink, res_b.method, res_b.status) == (second, "bus_path", "ok")


def _twins() -> tuple[LocalSink, LocalSink]:
    return tuple(_load("two_usb_dacs_no_serial.txt"))  # type: ignore[return-value]


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_twin_booted_alone_under_the_base_name_stays_with_its_own_player(order):
    """A unplugged at boot: PulseAudio gives B the base name A's player stored.

    Matching the players one by one let A's player take B's DAC by name.  Each
    player's identity claims its sink before any name is considered.
    """
    a, b = _twins()
    b_alone = dataclasses.replace(b, name=a.name)  # B enumerated first, so it gets the base name
    players = {"A": (a.identifiers, a.name), "B": (b.identifiers, b.name)}
    res = match_players({key: players[key] for key in order}, [b_alone])
    assert (res["A"].sink, res["A"].status) == (None, "sink_missing")
    assert (res["B"].sink, res["B"].method, res["B"].status) == (b_alone, "bus_path", "ok")


def test_twins_replugged_in_reverse_order_keep_their_own_players():
    a, b = _twins()
    # B enumerated first this time: the names swap, the ports do not.
    a_now = dataclasses.replace(a, name=b.name)
    b_now = dataclasses.replace(b, name=a.name)
    res = match_players(
        {"A": (a.identifiers, a.name), "B": (b.identifiers, b.name)},
        [b_now, a_now],
    )
    assert (res["A"].sink, res["A"].status) == (a_now, "ok")
    assert (res["B"].sink, res["B"].status) == (b_now, "ok")


def test_lone_dac_moved_to_another_port_is_still_found_by_name_next_to_its_twins_player():
    """S3-R2: a DAC moved to a free port keeps its player, even with its twin's player configured."""
    a, b = _twins()
    moved = dataclasses.replace(
        a,
        identifiers={
            **a.identifiers,
            "bus_path": "pci-0000:00:14.0-usb-0:4:1.0",
            "alsa_long_card_name": "Generic USB DAC at usb-0000:00:14.0-4, high speed",
        },
    )
    res = match_players({"B": (b.identifiers, b.name), "A": (a.identifiers, a.name)}, [moved])
    assert (res["A"].sink, res["A"].method, res["A"].status) == (moved, "name", "ok")
    assert (res["B"].sink, res["B"].status) == (None, "sink_missing")


def test_match_players_marks_a_sink_running_elsewhere_in_use():
    a = _sink("a", serial="S")
    res = match_players({"p": ({"serial": "S"}, None)}, [a], claimed={"a"})
    assert (res["p"].sink, res["p"].status) == (a, "sink_in_use")


def test_vendor_product_still_matches_when_device_moved_port_without_stored_bus_path():
    a = _sink("a", bus_path="p9", vendor_id="0x1", product_id="0x2")
    res = match_sink({"vendor_id": "0x1", "product_id": "0x2"}, None, [a])
    assert (res.sink, res.method) == (a, "vendor_product")


# ---------------------------------------------------------------------------
# pulsectl source and pactl fallback
# ---------------------------------------------------------------------------


class _Enum:
    def __init__(self, value: str) -> None:
        self._value = value


class _Obj:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _fake_info(fmt: int = 3, available: str = "yes", mute: int = 0, state: str = "idle") -> _Obj:
    return _Obj(
        name="alsa_output.usb-x",
        description="USB DAC",
        state=_Enum(state),
        sample_spec=_Obj(format=fmt, channels=2, rate=48000),
        mute=mute,
        volume=_Obj(values=[0.25, 0.8]),
        port_active=_Obj(name="analog-output", available=_Enum(available)),
        proplist={
            "device.serial": "SER1",
            "device.bus_path": "pci-0:1",
            "device.vendor.id": "0x1",
            "device.product.id": "0x2",
            "alsa.long_card_name": "Card",
            "device.description": "ignored",
        },
    )


def test_from_pulsectl_maps_fields():
    sink = sink_inventory._from_pulsectl(_fake_info(mute=1))
    assert sink == LocalSink(
        "alsa_output.usb-x",
        "USB DAC",
        "idle",
        "s16le 2ch 48000Hz",
        True,
        80,
        "analog-output",
        "available",
        {
            "serial": "SER1",
            "bus_path": "pci-0:1",
            "vendor_id": "0x1",
            "product_id": "0x2",
            "alsa_long_card_name": "Card",
        },
    )


@pytest.mark.parametrize("fmt,text", [(3, "s16le"), (5, "float32le"), (7, "s32le")])
def test_from_pulsectl_sample_formats(fmt, text):
    assert sink_inventory._from_pulsectl(_fake_info(fmt=fmt)).sample_spec == f"{text} 2ch 48000Hz"


@pytest.mark.parametrize(
    "raw,expected", [("yes", "available"), ("no", "not available"), ("unknown", "availability unknown")]
)
def test_from_pulsectl_port_availability(raw, expected):
    assert sink_inventory._from_pulsectl(_fake_info(available=raw)).active_port_available == expected


def test_from_pulsectl_unknown_state_and_no_port():
    info = _fake_info(state="weird")
    info.port_active = None
    sink = sink_inventory._from_pulsectl(info)
    assert sink.state == "unknown"
    assert sink.active_port is None and sink.active_port_available is None


def test_list_all_sinks_uses_pulsectl_when_available(monkeypatch):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def sink_list(self):
            return [_fake_info()]

    fake_module = _Obj(PulseAsync=lambda name: _Client())
    monkeypatch.setattr(sink_inventory.pulse, "_PULSECTL_AVAILABLE", True)
    monkeypatch.setattr(sink_inventory.pulse, "pulsectl_asyncio", fake_module)
    (sink,) = asyncio.run(sink_inventory._list_all_sinks())
    assert sink.name == "alsa_output.usb-x" and sink.identifiers["serial"] == "SER1"


def test_list_local_sinks_falls_back_to_pactl_when_pulsectl_raises(monkeypatch):
    class _Broken:
        async def __aenter__(self):
            raise ConnectionError("no pulse")

        async def __aexit__(self, *a):
            return False

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (FIXTURES / "usb_dac_with_serial.txt").read_bytes(), b""

    seen: dict = {}

    async def fake_exec(*args, **kwargs):
        seen["args"] = args
        seen["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setattr(sink_inventory.pulse, "_PULSECTL_AVAILABLE", True)
    monkeypatch.setattr(sink_inventory.pulse, "pulsectl_asyncio", _Obj(PulseAsync=lambda name: _Broken()))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    (sink,) = asyncio.run(list_local_sinks())
    assert sink.identifiers["serial"] == "Generic_USB_DAC_0000A1"
    assert seen["args"] == ("pactl", "list", "sinks")
    assert seen["env"]["LC_ALL"] == "C"


def test_pactl_timeout_kills_stray_process(monkeypatch):
    events: list[str] = []

    class _Proc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)

        def kill(self):
            events.append("kill")

        async def wait(self):
            events.append("wait")

    async def fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(sink_inventory.pulse, "_TIMEOUT", 0.01)
    assert asyncio.run(sink_inventory._list_all_sinks_pactl()) == []
    assert events == ["kill", "wait"]


def test_strict_listing_raises_when_no_source_can_be_read(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError("pactl")

    monkeypatch.setattr(sink_inventory.pulse, "_PULSECTL_AVAILABLE", False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert asyncio.run(list_local_sinks()) == []
    with pytest.raises(sink_inventory.SinkInventoryUnavailable):
        asyncio.run(list_local_sinks(strict=True))


def test_strict_listing_treats_a_failing_pactl_as_unreadable(monkeypatch):
    class _Proc:
        returncode = 1

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(sink_inventory.pulse, "_PULSECTL_AVAILABLE", False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(sink_inventory.SinkInventoryUnavailable):
        asyncio.run(list_local_sinks(strict=True))


def test_strict_listing_returns_an_empty_list_when_nothing_is_plugged_in(monkeypatch):
    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return _Proc()

    monkeypatch.setattr(sink_inventory.pulse, "_PULSECTL_AVAILABLE", False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert asyncio.run(list_local_sinks(strict=True)) == []


def test_mock_sinks_file_replaces_the_real_source(monkeypatch, tmp_path):
    mock = tmp_path / "sinks.txt"
    mock.write_text((FIXTURES / "usb_dac_with_serial.txt").read_text())
    monkeypatch.setenv("SENDSPIN_MOCK_SINKS", str(mock))

    async def _boom(*_a, **_k):
        raise AssertionError("real sink source must not be used")

    monkeypatch.setattr(sink_inventory, "_list_all_sinks_pulsectl", _boom)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sinks_text", _boom)

    for strict in (False, True):
        (sink,) = asyncio.run(list_local_sinks(strict=strict))
        assert sink.name == _load("usb_dac_with_serial.txt")[0].name

    mock.write_text("")
    assert asyncio.run(list_local_sinks(strict=True)) == []


def test_mock_sinks_unreadable_file_is_unavailable_in_strict_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("SENDSPIN_MOCK_SINKS", str(tmp_path / "missing.txt"))
    with pytest.raises(sink_inventory.SinkInventoryUnavailable):
        asyncio.run(list_local_sinks(strict=True))
    assert asyncio.run(list_local_sinks()) == []
