"""Pairing without a bluetoothctl session.

The pair flow drove an interactive bluetoothctl: `power on`, `scan bredr`,
wait for the speaker to advertise, `pair`, answer prompts, `trust`. That
session registered an agent of its own, which BlueZ consulted ahead of ours
(#471), and its outcome had to be read out of a transcript.

With the native agent registered, the same choreography runs over BlueZ's bus:
the controller is powered, discovery runs until the speaker's object appears,
`Device1.Pair` is called — BlueZ asks the default agent, which is ours — and
the bond is trusted. The PIN ladder, cancellation and failure wording stay as
they were; bluetoothctl is the fallback only when the bus cannot be reached.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, BluezControl
from sendspin_bridge.bluetooth.controller import DbusController
from sendspin_bridge.bluetooth.pairing import PairOptions, PairSession, PairTimings
from tests.support.fake_dbus import FakeBlueZ

HCI1 = "/org/bluez/hci1"
HCI1_MAC = "C0:FB:F9:62:D7:D6"
SPEAKER = "6C:5C:3D:35:17:99"
SPEAKER_PATH = f"{HCI1}/dev_6C_5C_3D_35_17_99"
FAST = PairTimings(scan_window_s=0.5, pair_wait_s=2.0, pre_cleanup_settle_s=0.0, drain_s=0.0)


class _Agent:
    """The native agent: registered, and answering what BlueZ asks."""

    def __init__(self, *, pin_attempted: bool = False):
        self.telemetry = {"capability": "DisplayYesNo", "method_calls": [], "pin_attempted": pin_attempted}
        self.entered = self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_exc):
        self.exited = True
        return False


@pytest.fixture
def bus() -> FakeBlueZ:
    fake = FakeBlueZ()
    fake.add_adapter(HCI1, HCI1_MAC, powered=False)
    fake.add_in_range(HCI1, SPEAKER_PATH, SPEAKER, Name="ENEBY Portable", Alias="ENEBY Portable", RSSI=-50)
    return fake


def _session(fake_bluez, bus, *, agents, options=None, **kwargs) -> PairSession:
    control = BluezControl(spawner=fake_bluez, query_backend=DbusController(bus_factory=bus.bus))
    queue = list(agents)
    return PairSession(
        control,
        adapter=Adapter.select(HCI1_MAC),
        mac=SPEAKER,
        options=options or PairOptions(timings=FAST),
        agent_factory=lambda **_kw: queue.pop(0) if queue else None,
        **kwargs,
    )


def _calls(bus, name):
    return [path for path, call, _args in bus.calls if call == name]


def test_a_speaker_is_found_paired_and_trusted_over_the_bus(fake_bluez, bus):
    agent = _Agent()
    hooks: list[str] = []

    outcome = _session(fake_bluez, bus, agents=[agent], on_before_pair=lambda: hooks.append("pre-pair")).run()

    assert outcome.success is True
    assert bus.objects[HCI1]["org.bluez.Adapter1"]["Powered"] is True
    assert _calls(bus, "StartDiscovery") == [HCI1] and _calls(bus, "StopDiscovery") == [HCI1]
    assert _calls(bus, "Pair") == [SPEAKER_PATH]
    assert bus.objects[SPEAKER_PATH]["org.bluez.Device1"]["Trusted"] is True
    assert hooks == ["pre-pair"]
    assert agent.entered and agent.exited
    # No bluetoothctl session was opened for the pair itself.
    assert not [c for c in fake_bluez.commands if c.kind == "popen"]


def test_connect_after_trust_brings_the_link_up(fake_bluez, bus):
    options = PairOptions(timings=FAST, connect_after_trust=True)

    outcome = _session(fake_bluez, bus, agents=[_Agent()], options=options).run()

    assert outcome.success is True
    assert outcome.connected is True
    assert _calls(bus, "Connect") == [SPEAKER_PATH]


def test_a_rejected_pin_moves_down_the_ladder(fake_bluez, bus):
    bus.fail["Pair"] = RuntimeError("[org.bluez.Error.AuthenticationFailed] Authentication Failed")
    options = PairOptions(timings=FAST, pins=("0000", "1234"))

    outcome = _session(
        fake_bluez, bus, agents=[_Agent(pin_attempted=True), _Agent(pin_attempted=True)], options=options
    ).run()

    assert outcome.success is False
    assert outcome.tried_pins == ("0000", "1234")
    assert len(_calls(bus, "Pair")) == 2
    assert "AuthenticationFailed" in outcome.output


def test_a_speaker_that_never_appears_is_a_failure_with_a_reason(fake_bluez, bus):
    bus.in_range.clear()

    outcome = _session(fake_bluez, bus, agents=[_Agent()]).run()

    assert outcome.success is False
    assert outcome.reason
    assert _calls(bus, "Pair") == []


def test_without_the_bus_the_bluetoothctl_session_still_pairs(fake_bluez, bus):
    bus.connected = False
    fake_bluez.session_script([(f"pair {SPEAKER}", ["Pairing successful"])])

    outcome = _session(fake_bluez, bus, agents=[_Agent()]).run()

    assert outcome.success is True
    assert [c for c in fake_bluez.commands if c.kind == "popen"]


def test_cancelling_during_discovery_stops_before_pairing(fake_bluez, bus):
    bus.in_range.clear()
    checks = iter([False, False, True, True, True, True, True, True])

    outcome = _session(fake_bluez, bus, agents=[_Agent()], cancel=lambda: next(checks, True)).run()

    assert outcome.cancelled is True
    assert _calls(bus, "Pair") == []
