"""The five safe audio commands: validation, inventory membership and backend capability."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import audio as audio_uc
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.audio.sink_inventory import LocalSink
from sendspin_bridge.services.hardware.capabilities import build_capabilities
from sendspin_bridge.services.hardware.inventory import (
    CaptureFacts,
    InventoryRead,
    build_audio_facts,
)

SINK = LocalSink(
    name="alsa_output.usb-dac.analog-stereo",
    description="USB DAC",
    state="idle",
    sample_spec="s16le 2ch 48000Hz",
    muted=False,
    volume=40,
    active_port="analog-output",
    active_port_available="available",
    identifiers={"serial": "DAC-1"},
)


def _read(
    *, audio_server: str = "PulseAudio (on PipeWire 1.0.5)", sinks: tuple[LocalSink, ...] = (SINK,)
) -> InventoryRead:
    return InventoryRead(
        as_of="2026-10-31T00:00:00+00:00",
        stale=False,
        stale_after_s=2,
        capabilities=build_capabilities(
            bluetooth=True,
            dbus=True,
            server_name=audio_server,
            capture=True,
            default_sink=True,
        ),
        controllers=(),
        devices=(),
        degraded=(),
        audio=build_audio_facts([], list(sinks), [], CaptureFacts()),
    )


def _pulse():
    """The live pulse module (another test file re-imports it during collection)."""
    import sendspin_bridge.services.audio.pulse as pulse

    return pulse


@pytest.fixture
def host(monkeypatch):
    """A host with one sink and a PulseAudio server that accepts every command."""
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read())
    pulse = _pulse()

    def _record(action: str, returns: object = True):
        def _run(name: str, *args: object) -> object:
            calls.append((action, name, args))
            return returns

        return _run

    monkeypatch.setattr(pulse, "set_sink_volume", _record("volume"))
    monkeypatch.setattr(pulse, "set_sink_mute", _record("mute"))
    monkeypatch.setattr(pulse, "set_default_sink", _record("default"))
    monkeypatch.setattr(pulse, "suspend_sink", _record("suspend"))
    return calls


def _no_server(monkeypatch) -> None:
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read(audio_server="not available"))


# -- set_sink_volume ----------------------------------------------------------


async def test_set_sink_volume_writes_the_level_and_returns_the_state(host):
    state = await audio_uc.set_sink_volume(SINK.name, 37)

    assert host == [("volume", SINK.name, (37,))]
    assert state.name == SINK.name
    assert state.volume == 37
    assert state.muted is False
    assert state.state == "idle"


@pytest.mark.parametrize("volume", [-1, 101])
async def test_set_sink_volume_outside_zero_to_hundred_is_invalid_volume(host, volume):
    with pytest.raises(UseCaseError) as exc:
        await audio_uc.set_sink_volume(SINK.name, volume)

    assert exc.value.status == 400
    assert exc.value.code == "invalid_volume"
    assert host == []


async def test_set_sink_volume_is_idempotent(host):
    first = await audio_uc.set_sink_volume(SINK.name, 37)
    second = await audio_uc.set_sink_volume(SINK.name, 37)

    assert first == second
    assert host == [("volume", SINK.name, (37,)), ("volume", SINK.name, (37,))]


# -- set_sink_mute ------------------------------------------------------------


async def test_set_sink_mute_writes_the_flag(host):
    state = await audio_uc.set_sink_mute(SINK.name, True)

    assert host == [("mute", SINK.name, (True,))]
    assert state.muted is True
    assert state.volume == SINK.volume

    again = await audio_uc.set_sink_mute(SINK.name, True)
    assert again.muted is True


# -- set_default_sink ---------------------------------------------------------


async def test_set_default_sink_uses_the_named_sink(host):
    state = await audio_uc.set_default_sink(SINK.name)

    assert host == [("default", SINK.name, ())]
    assert state.name == SINK.name
    assert state.volume == SINK.volume


# -- suspend / resume ---------------------------------------------------------


async def test_suspend_and_resume_are_idempotent(host):
    suspended = await audio_uc.suspend_sink(SINK.name)
    suspended_again = await audio_uc.suspend_sink(SINK.name)
    resumed = await audio_uc.resume_sink(SINK.name)
    resumed_again = await audio_uc.resume_sink(SINK.name)

    assert host == [
        ("suspend", SINK.name, (True,)),
        ("suspend", SINK.name, (True,)),
        ("suspend", SINK.name, (False,)),
        ("suspend", SINK.name, (False,)),
    ]
    assert suspended.state == "suspended"
    assert suspended_again.state == "suspended"
    assert resumed.state == SINK.state
    assert resumed_again.state == SINK.state


# -- the shared failure modes -------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda: audio_uc.set_sink_volume(SINK.name, 20),
        lambda: audio_uc.set_sink_mute(SINK.name, True),
        lambda: audio_uc.set_default_sink(SINK.name),
        lambda: audio_uc.suspend_sink(SINK.name),
        lambda: audio_uc.resume_sink(SINK.name),
    ],
)
async def test_an_unknown_sink_is_entity_not_found(host, monkeypatch, call):
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read(sinks=()))

    with pytest.raises(UseCaseError) as exc:
        await call()

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"
    assert host == []


@pytest.mark.parametrize(
    "call",
    [
        lambda: audio_uc.set_sink_volume(SINK.name, 20),
        lambda: audio_uc.set_sink_mute(SINK.name, True),
        lambda: audio_uc.set_default_sink(SINK.name),
        lambda: audio_uc.suspend_sink(SINK.name),
        lambda: audio_uc.resume_sink(SINK.name),
    ],
)
async def test_no_audio_server_is_not_supported_for_backend(monkeypatch, call):
    _no_server(monkeypatch)

    with pytest.raises(UseCaseError) as exc:
        await call()

    assert exc.value.status == 409
    assert exc.value.code == "not_supported_for_backend"


@pytest.mark.parametrize(
    "call",
    [
        lambda: audio_uc.set_sink_volume("not a name", 20),
        lambda: audio_uc.set_sink_mute("not a name", True),
        lambda: audio_uc.set_default_sink("not a name"),
        lambda: audio_uc.suspend_sink("not a name"),
        lambda: audio_uc.resume_sink("not a name"),
    ],
)
async def test_a_name_outside_the_grammar_is_invalid_audio_name(host, call):
    with pytest.raises(UseCaseError) as exc:
        await call()

    assert exc.value.status == 400
    assert exc.value.code == "invalid_audio_name"
    assert host == []


async def test_an_unknown_sink_outside_the_grammar_is_a_400_before_the_lookup(host):
    with pytest.raises(UseCaseError) as exc:
        await audio_uc.set_sink_volume("not a name", 20)

    assert exc.value.code == "invalid_audio_name"


async def test_a_server_that_refuses_the_command_is_a_409(monkeypatch):
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read())
    monkeypatch.setattr(_pulse(), "set_sink_volume", lambda _name, _volume: False)

    with pytest.raises(UseCaseError) as exc:
        await audio_uc.set_sink_volume(SINK.name, 20)

    assert exc.value.status == 409
    assert exc.value.code == "not_supported_for_backend"


async def test_an_unreadable_inventory_still_reports_a_missing_server_as_409(monkeypatch):
    from sendspin_bridge.services.hardware import capabilities
    from sendspin_bridge.services.hardware.inventory import HardwareUnavailable

    def _unreadable() -> InventoryRead:
        raise HardwareUnavailable("no bus and no bluetoothctl")

    monkeypatch.setattr(audio_uc, "read_inventory", _unreadable)
    monkeypatch.setattr(capabilities, "probe_audio_server", lambda: "not available")

    with pytest.raises(UseCaseError) as exc:
        await audio_uc.set_sink_volume(SINK.name, 20)

    assert exc.value.status == 409
    assert exc.value.code == "not_supported_for_backend"


async def test_an_unreadable_inventory_with_a_live_server_is_a_503(monkeypatch):
    from sendspin_bridge.services.hardware import capabilities
    from sendspin_bridge.services.hardware.inventory import HardwareUnavailable

    def _unreadable() -> InventoryRead:
        raise HardwareUnavailable("no bus and no bluetoothctl")

    monkeypatch.setattr(audio_uc, "read_inventory", _unreadable)
    monkeypatch.setattr(capabilities, "probe_audio_server", lambda: "PulseAudio")

    with pytest.raises(UseCaseError) as exc:
        await audio_uc.set_sink_volume(SINK.name, 20)

    assert exc.value.status == 503
    assert exc.value.code == "hardware_unavailable"
