"""PairSession — the one pairing composite (phase 2 of the transport work).

Three near-identical bluetoothctl pair flows used to live in the codebase
(monitor-loop re-pair, manual pair of a new device, reset-and-reconnect),
each holding one behaviour the others lacked.  These tests pin the union
they collapse into:

* fire ``pair`` the moment the target advertises itself (#168) instead of
  always burning the full discovery window, with a deadline fallback for
  speakers that never show up in the stream;
* answer SSP confirmation and legacy PIN prompts;
* walk the popular-PIN ladder while, and only while, the device keeps
  rejecting the PIN;
* stay cancellable (the monitor loop aborts pairing when a device is
  disabled mid-flight);
* trust only after a confirmed pair, optionally connect, and report the
  failure fingerprint the recovery card needs.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter
from sendspin_bridge.bluetooth.pairing import PairOptions, PairSession, PairTimings

ADAPTER_MAC = "C0:FB:F9:62:D7:D6"
MAC = "6C:5C:3D:35:17:99"


class _FakeAgent:
    """Stand-in for the native BlueZ pairing agent."""

    def __init__(self, *, capability: str = "DisplayYesNo", method_calls: list | None = None) -> None:
        self.telemetry = {
            "capability": capability,
            "method_calls": method_calls if method_calls is not None else [],
            "last_passkey": None,
            "peer_cancelled": False,
            "authorized_services": [],
            "rejected_services": [],
        }
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_exc):
        self.exited = True
        return False


@pytest.fixture
def agent():
    return _FakeAgent()


def _session(fake_bluez, agent=None, **kwargs) -> PairSession:
    options = kwargs.pop("options", None) or PairOptions(timings=PairTimings(scan_window_s=12.0, pair_wait_s=15.0))
    return PairSession(
        fake_bluez.control,
        adapter=Adapter.select(ADAPTER_MAC),
        mac=MAC,
        options=options,
        agent_factory=(lambda **_kw: agent) if agent is not None else (lambda **_kw: None),
        **kwargs,
    )


def _sends(fake_bluez) -> list[str]:
    return [c.script for c in fake_bluez.commands if c.kind in ("send", "reply")]


def _sent_pair_at(fake_bluez) -> float | None:
    for c in fake_bluez.commands:
        if c.kind == "send" and f"pair {MAC}" in c.script:
            return c.at
    return None


def test_pair_fires_as_soon_as_the_device_advertises_itself(fake_bluez, agent):
    """#168: waiting out the whole discovery window let the speaker leave
    pairing mode before ``pair`` arrived."""
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"]),
            (f"pair {MAC}", ["Pairing successful"]),
        ]
    )

    outcome = _session(fake_bluez, agent).run()

    assert outcome.success is True
    fired_at = _sent_pair_at(fake_bluez)
    assert fired_at is not None and fired_at < 12.0, f"pair fired at {fired_at}, not early"
    sends = _sends(fake_bluez)
    assert any("power on" in s for s in sends)
    assert any("scan bredr" in s for s in sends)
    # Trust only after a confirmed pair, and the session is always closed out.
    assert any(f"trust {MAC}" in s for s in sends)
    assert any("scan off" in s for s in sends)
    assert agent.entered and agent.exited


def test_pair_falls_back_to_the_discovery_deadline(fake_bluez, agent):
    """A speaker that never appears in the stream still gets a pair attempt."""
    fake_bluez.session_script([(f"pair {MAC}", ["Failed to pair: org.bluez.Error.ConnectionAttemptFailed"])])

    outcome = _session(fake_bluez, agent).run()

    assert outcome.success is False
    fired_at = _sent_pair_at(fake_bluez)
    assert fired_at is not None and fired_at >= 12.0
    assert "ConnectionAttemptFailed" in outcome.reason


def test_ssp_confirmation_is_answered(fake_bluez, agent):
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"]),
            (f"pair {MAC}", ["[agent] Confirm passkey 312997 (yes/no):", "Pairing successful"]),
        ]
    )

    outcome = _session(fake_bluez, agent).run()

    assert outcome.success is True
    replies = [c.script for c in fake_bluez.commands if c.kind == "reply"]
    assert replies == ["yes"]


def test_legacy_pin_prompt_is_answered_and_recorded(fake_bluez, agent):
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} JAM Speaker"]),
            (f"pair {MAC}", ["[agent] Enter PIN code:"]),
            ("0000", ["Pairing successful"]),
        ]
    )

    outcome = _session(fake_bluez, agent).run()

    assert outcome.success is True
    assert outcome.pin_attempted is True
    assert outcome.pin_used == "0000"
    assert [c.script for c in fake_bluez.commands if c.kind == "reply"] == ["0000"]


def test_pin_ladder_retries_only_while_the_pin_is_rejected(fake_bluez, agent):
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} JAM Speaker"]),
            (f"pair {MAC}", ["[agent] Enter PIN code:"]),
            ("0000", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1234", ["Pairing successful"]),
        ]
    )
    options = PairOptions(pins=("0000", "1234"), timings=PairTimings(scan_window_s=12.0, pair_wait_s=15.0))

    outcome = _session(fake_bluez, agent, options=options).run()

    assert outcome.success is True
    assert outcome.pin_used == "1234"
    assert outcome.tried_pins == ("0000", "1234")
    # One bluetoothctl session per attempt.
    assert len([c for c in fake_bluez.commands if c.kind == "popen"]) == 2


def test_non_pin_failure_stops_the_ladder(fake_bluez, agent):
    """Retrying a connection failure just burns ~20 s per attempt."""
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"]),
            (f"pair {MAC}", ["Failed to pair: org.bluez.Error.ConnectionAttemptFailed"]),
        ]
    )
    options = PairOptions(pins=("0000", "1234", "1111"), timings=PairTimings(scan_window_s=1.0, pair_wait_s=2.0))

    outcome = _session(fake_bluez, agent, options=options).run()

    assert outcome.success is False
    assert outcome.pin_rejected is False
    assert outcome.tried_pins == ("0000",)
    assert len([c for c in fake_bluez.commands if c.kind == "popen"]) == 1


def test_cancellation_aborts_before_pairing(fake_bluez, agent):
    """The monitor loop cancels a re-pair when the device gets disabled."""
    fake_bluez.session_script([("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"])])

    outcome = _session(fake_bluez, agent, cancel=lambda: True).run()

    assert outcome.success is False
    assert outcome.cancelled is True
    assert _sent_pair_at(fake_bluez) is None


def test_connect_after_trust_reports_connection_and_drains_the_settle_window(fake_bluez, agent):
    """Reset-and-reconnect connects inside the pair session; the settle
    window must consume output instead of sleeping blind, or the
    ``Connection successful`` line is lost."""
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"]),
            (f"pair {MAC}", ["Pairing successful"]),
            (f"connect {MAC}", ["Connection successful"]),
        ]
    )
    options = PairOptions(
        connect_after_trust=True,
        timings=PairTimings(scan_window_s=12.0, pair_wait_s=15.0, post_trust_settle_s=5.0),
    )

    outcome = _session(fake_bluez, agent, options=options).run()

    assert outcome.success is True
    assert outcome.connected is True
    assert "Connection successful" in outcome.output


def test_stale_bond_and_agent_are_cleared_before_pairing(fake_bluez, agent):
    """Without ``agent off`` the next ``agent on`` fails to register and
    pairing runs with no authentication agent (#162)."""
    fake_bluez.session_script([(f"pair {MAC}", ["Pairing successful"])])

    _session(fake_bluez, agent).run()

    cleanups = [c for c in fake_bluez.commands if c.kind == "run" and "agent off" in c.script]
    assert cleanups, "expected a pre-pair agent/bond cleanup"
    assert f"remove {MAC}" in cleanups[0].script
    assert cleanups[0].adapter_selected == ADAPTER_MAC


def test_samsung_cod_filter_failure_is_fingerprinted(fake_bluez, agent):
    fake_bluez.session_script(
        [
            (
                f"pair {MAC}",
                [
                    "Failed to pair: org.bluez.Error.AuthenticationCanceled",
                    "connect failed (status 0x07 No Resources)",
                ],
            )
        ]
    )

    outcome = _session(fake_bluez, agent).run()

    assert outcome.success is False
    assert outcome.failure_kind == "samsung_cod_filter"
    assert outcome.agent_telemetry is not None
    assert outcome.agent_telemetry["capability"] == "DisplayYesNo"


def test_without_a_native_agent_the_builtin_agent_is_requested(fake_bluez):
    """Hosts that can't reach dbus-fast fall back to bluetoothctl's agent."""
    fake_bluez.session_script([(f"pair {MAC}", ["Pairing successful"])])

    outcome = _session(fake_bluez, agent=None).run()

    assert outcome.success is True
    sends = "\n".join(_sends(fake_bluez))
    assert "agent on" in sends
    assert "default-agent" in sends


def test_no_input_no_output_capability_reaches_the_builtin_agent(fake_bluez):
    fake_bluez.session_script([(f"pair {MAC}", ["Pairing successful"])])
    options = PairOptions(capability="NoInputNoOutput", timings=PairTimings(scan_window_s=1.0, pair_wait_s=2.0))

    _session(fake_bluez, agent=None, options=options).run()

    assert any("agent NoInputNoOutput" in s for s in _sends(fake_bluez))


def test_before_pair_hook_runs_before_the_pair_command(fake_bluez, agent):
    """The Samsung Class-of-Device override has to be re-applied right
    before the outbound connect, after ``power on`` may have reset it."""
    calls: list[float] = []
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} ENEBY Portable"]),
            (f"pair {MAC}", ["Pairing successful"]),
        ]
    )

    _session(fake_bluez, agent, on_before_pair=lambda: calls.append(fake_bluez.now())).run()

    assert len(calls) == 1
    assert calls[0] <= (_sent_pair_at(fake_bluez) or 0.0)
