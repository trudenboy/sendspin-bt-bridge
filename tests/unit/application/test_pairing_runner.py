"""The pair use case: adapter resolution, per-attempt options, peer quiesce, bond verification."""

import contextlib
import logging
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.bluetooth.controller import set_controller
from tests.support.fake_dbus import controller_knowing_adapters


def _pair(api_bt_mod, *args, **kwargs):
    """``("ok", mac)`` on success, ``("failed", error)`` when the use case refuses."""
    try:
        return ("ok", api_bt_mod._run_standalone_pair(*args, **kwargs)["mac"])
    except UseCaseError as exc:
        return ("failed", exc)


# ---------------------------------------------------------------------------
# Standalone pair (POST /api/bt/pair_new)
#
# The pairing choreography itself is pinned in
# tests/unit/bluetooth/test_pair_session.py.  What matters here is what the
# use case contributes: adapter resolution, per-request compatibility options,
# peer quiesce, and the result (or the failure) the job reports.
# ---------------------------------------------------------------------------

MAC = "AA:BB:CC:DD:EE:FF"
HCI0_MAC = "C0:FB:F9:62:D6:9D"
HCI1_MAC = "C0:FB:F9:62:D7:D6"


class _RecordingAgent:
    """Stand-in for the native BlueZ agent, capturing how it was built."""

    instances: list["_RecordingAgent"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.telemetry = {"capability": kwargs.get("capability")}
        _RecordingAgent.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def pair_agent(monkeypatch):
    """Capture the native agent's construction arguments."""
    import sendspin_bridge.services.bluetooth.pairing_agent as agent_mod

    _RecordingAgent.instances = []
    monkeypatch.setattr(agent_mod, "PairingAgent", _RecordingAgent)
    return _RecordingAgent


def _script_pair_ok(fake_bluez, mac=MAC):
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {mac} ENEBY Portable"]),
            (f"pair {mac}", ["Pairing successful"]),
        ]
    )


def _pin_hci_map(monkeypatch, api_bt_mod):
    """Make hciN resolution deterministic regardless of the host's controllers.

    The live stand has two real controllers; without pinning the kernel map
    and the D-Bus lookup, what ``hci1`` resolves to depends on the machine
    the suite runs on.
    """
    monkeypatch.setattr(
        api_bt_mod,
        "build_hci_map",
        lambda: {HCI0_MAC.replace(":", ""): "hci0", HCI1_MAC.replace(":", ""): "hci1"},
    )


def _selected_adapters(fake_bluez) -> list[str]:
    return [c.adapter_selected for c in fake_bluez.commands if c.adapter_selected]


def test_run_standalone_pair_resolves_hci_name_to_controller_mac(installed_bluez, monkeypatch):
    """``bluetoothctl select hci1`` fails on HAOS/LXC with "Controller hci1
    not available" and the whole pair sequence then silently runs against
    the default controller — so ``hciN`` must become the controller MAC."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC, HCI1_MAC])
    _pin_hci_map(monkeypatch, api_bt_mod)

    outcome = _pair(api_bt_mod, MAC, "hci1")

    assert _selected_adapters(installed_bluez), "pair ran without ever scoping the controller"
    assert set(_selected_adapters(installed_bluez)) == {HCI1_MAC}
    assert outcome == ("ok", MAC)


def test_run_standalone_pair_keeps_hci_name_when_resolution_fails(installed_bluez, monkeypatch):
    """If ``list_bt_adapters`` returns nothing, keep the supplied ``hciN``
    rather than dropping the ``select`` — a failed ``select`` is a visible
    error, silently pairing against the default controller is not."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    installed_bluez.on("list", stdout="")  # no controller the transport can map either
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [])
    # Every resolution path dry: no sysfs map, no D-Bus answer.
    monkeypatch.setattr(api_bt_mod, "build_hci_map", lambda: {})

    _pair(api_bt_mod, MAC, "hci0")

    assert set(_selected_adapters(installed_bluez)) == {"hci0"}


def test_resolve_adapter_to_mac_uses_kernel_hci_map_not_list_position(monkeypatch):
    """Issue #340 on the pair path: ``bluetoothctl list`` order is BlueZ
    registration order, not kernel hciN numbering.  When hci1 is registered
    first (list position 0) the positional resolver silently paired against
    hci0 — the live ENEBY pair failure on the two-adapter stand.  Resolution
    must go through the sysfs-backed kernel map, same as the scan path."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    # bluetoothctl list returns hci1's MAC first (registration order).
    monkeypatch.setattr(
        api_bt_mod,
        "list_bt_adapters",
        lambda: ["00:02:72:0A:E4:3B", "C0:FB:F9:62:D7:D6"],
    )
    # Kernel map (sysfs): hci0=C0:FB…, hci1=00:02….
    monkeypatch.setattr(
        api_bt_mod,
        "build_hci_map",
        lambda: {"C0FBF962D7D6": "hci0", "0002720AE43B": "hci1"},
    )

    assert api_bt_mod._resolve_adapter_to_mac("hci1") == "00:02:72:0A:E4:3B"
    assert api_bt_mod._resolve_adapter_to_mac("hci0") == "C0:FB:F9:62:D7:D6"


def test_resolve_adapter_to_mac_falls_back_to_dbus_when_sysfs_has_no_address(monkeypatch, bridge_loop):
    """Live rc.1 stand finding: some kernels expose /sys/class/bluetooth/hciN
    WITHOUT an ``address`` file (only device/power/rfkill) — the sysfs map is
    then empty and positional ``bluetoothctl list`` indexing resolves hciN to
    the wrong controller (registration order ≠ kernel numbering, issue #340).
    The D-Bus adapter object path /org/bluez/hciN is keyed by the kernel index
    unambiguously, so it must be tried before the positional fallback."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    # No sysfs visibility at all.
    monkeypatch.setattr(api_bt_mod, "build_hci_map", lambda: {})
    # list order: hci1's MAC first (BlueZ registration order).
    monkeypatch.setattr(
        api_bt_mod,
        "list_bt_adapters",
        lambda: ["00:02:72:0A:E4:3B", "C0:FB:F9:62:D7:D6"],
    )
    # BlueZ knows the truth: hci0=C0:FB…, hci1=00:02….
    set_controller(controller_knowing_adapters({"hci0": "C0:FB:F9:62:D7:D6", "hci1": "00:02:72:0A:E4:3B"}))

    # Without the D-Bus step, hci1 would positionally resolve to 00:02…'s
    # list-position neighbour — the live mispairing.
    assert api_bt_mod._resolve_adapter_to_mac("hci1") == "00:02:72:0A:E4:3B"
    assert api_bt_mod._resolve_adapter_to_mac("hci0") == "C0:FB:F9:62:D7:D6"


def test_run_standalone_pair_fails_when_the_bond_is_not_on_the_requested_adapter(installed_bluez, monkeypatch):
    """Live two-adapter failure (rc.1 stand): the device is already bonded on
    the other controller, so the SSP exchange never fires, the session prints
    "Pairing successful" anyway, and the job used to report success while the
    bond stayed put.  The route must surface the pair as failed."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    installed_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} Lenco LS-500"]),
            (f"pair {MAC}", ["Pairing successful"]),
            (f"info {MAC}", [f"Device {MAC} (public)", "\tPaired: no", "\tTrusted: no"]),
        ]
    )
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC, HCI1_MAC])

    outcome = _pair(api_bt_mod, MAC, HCI1_MAC)

    assert outcome[0] == "failed"


def test_run_standalone_pair_succeeds_when_the_bond_is_confirmed(installed_bluez, monkeypatch):
    """Happy-path guard for the verification: the scoped ``info`` confirms the
    bond, so the job still reports success."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    installed_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {MAC} Lenco LS-500"]),
            (f"pair {MAC}", ["Pairing successful"]),
            (f"info {MAC}", [f"Device {MAC} (public)", "\tPaired: yes", "\tTrusted: yes"]),
        ]
    )
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC, HCI1_MAC])

    outcome = _pair(api_bt_mod, MAC, HCI1_MAC)

    assert outcome == ("ok", MAC)


def test_run_standalone_pair_passes_adapter_mac_through_unchanged(installed_bluez, monkeypatch):
    """MAC inputs must never be mutated by ``_resolve_adapter_to_mac``."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC, HCI1_MAC])

    _pair(api_bt_mod, MAC, HCI1_MAC)

    assert set(_selected_adapters(installed_bluez)) == {HCI1_MAC}


def test_run_standalone_pair_clears_stale_agent_before_pairing(installed_bluez, monkeypatch):
    """Cleanup must ``agent off`` before the next pair attempt.

    BlueZ keeps an agent object registered on the system bus when the
    previous bluetoothctl session didn't tear down cleanly (or when HA
    Core's own Bluetooth integration registered one).  ``agent on`` then
    returns ``Failed to register agent object``, leaving the pair without
    an authentication agent and producing
    ``org.bluez.Error.ConnectionAttemptFailed`` (issue #162).
    """
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    _pair(api_bt_mod, MAC, HCI0_MAC)

    cleanup = next(c for c in installed_bluez.commands if "agent off" in c.script)
    assert f"remove {MAC}" in cleanup.script
    assert cleanup.script.index("agent off") < cleanup.script.index("remove")
    assert cleanup.at < min(c.at for c in installed_bluez.commands if c.kind == "send" and "power on" in c.script), (
        "the stale agent must be cleared before the pair session starts"
    )


def test_run_standalone_pair_reports_failure_in_the_job_payload(installed_bluez, monkeypatch):
    import sendspin_bridge.application.bluetooth as api_bt_mod

    installed_bluez.session_script([(f"pair {MAC}", ["Failed to pair: org.bluez.Error.AuthenticationCanceled"])])
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    outcome = _pair(api_bt_mod, MAC, HCI0_MAC)

    assert outcome[0] == "failed"


def test_run_standalone_pair_reports_failure_when_the_transport_raises(installed_bluez, monkeypatch):
    """A crash inside pairing propagates; the job registry records it as a
    failed job, so the UI never polls a job that never completes."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])
    monkeypatch.setattr(api_bt_mod, "PairSession", MagicMock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        api_bt_mod._run_standalone_pair(MAC, HCI0_MAC)


def test_run_standalone_pair_uses_no_input_no_output_for_explicit_request(installed_bluez, monkeypatch, pair_agent):
    """``NoInputNoOutput`` forces Just-Works SSP for speakers that cancel a
    passkey exchange (issue #168); it is a per-request override."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    _pair(api_bt_mod, MAC, HCI0_MAC, no_input_no_output_agent=True)

    assert pair_agent.instances, "no native agent was constructed"
    assert pair_agent.instances[0].kwargs["capability"] == "NoInputNoOutput"


def test_run_standalone_pair_uses_display_yes_no_by_default(installed_bluez, monkeypatch, pair_agent):
    """DisplayYesNo is what manual ``bluetoothctl`` uses and what reached
    ``Bonded: yes`` in the #168 reproduction."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    _pair(api_bt_mod, MAC, HCI0_MAC)

    assert pair_agent.instances[0].kwargs["capability"] == "DisplayYesNo"
    assert pair_agent.instances[0].kwargs["allow_hfp"] is False


def test_run_standalone_pair_forwards_hfp_authorization_when_requested(installed_bluez, monkeypatch, pair_agent):
    import sendspin_bridge.application.bluetooth as api_bt_mod

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    _pair(api_bt_mod, MAC, HCI0_MAC, allow_hfp_profile=True)

    assert pair_agent.instances[0].kwargs["allow_hfp"] is True


def test_run_standalone_pair_walks_the_pin_ladder_and_reports_exhaustion(installed_bluez, monkeypatch, caplog):
    """A device that keeps rejecting PINs must be tried with every popular
    candidate, then reported as needing a custom PIN."""
    import sendspin_bridge.application.bluetooth as api_bt_mod
    from sendspin_bridge.services.bluetooth import COMMON_BT_PAIR_PINS

    installed_bluez.session_script(
        [
            (f"pair {MAC}", ["[agent] Enter PIN code:"]),
            ("0000", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1234", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1111", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("8888", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1212", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("9999", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
        ]
    )
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    with caplog.at_level(logging.WARNING):
        outcome = _pair(api_bt_mod, MAC, HCI0_MAC)

    replied = [c.script.strip() for c in installed_bluez.commands if c.kind == "reply"]
    assert replied == list(COMMON_BT_PAIR_PINS)
    assert outcome[0] == "failed"
    assert any("custom PIN" in record.getMessage() for record in caplog.records)


def test_run_standalone_pair_stops_the_ladder_on_a_non_pin_failure(installed_bluez, monkeypatch):
    """Retrying a connection failure costs ~20 s per attempt and changes
    nothing — the ladder is only for PIN rejections."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    installed_bluez.session_script([(f"pair {MAC}", ["Failed to pair: org.bluez.Error.ConnectionAttemptFailed"])])
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])

    _pair(api_bt_mod, MAC, HCI0_MAC)

    pair_sends = [c for c in installed_bluez.commands if c.kind == "send" and f"pair {MAC}" in c.script]
    assert len(pair_sends) == 1, f"expected one attempt, got {len(pair_sends)}"


def test_run_standalone_pair_quiesces_peers_only_when_requested(installed_bluez, monkeypatch):
    """Single-adapter hosts can't pair while another A2DP ACL is up; the
    peer-park is opt-in per request."""
    import sendspin_bridge.application.bluetooth as api_bt_mod

    calls = []

    @contextlib.contextmanager
    def fake_quiesce(adapter, *, exclude_mac):
        calls.append((adapter, exclude_mac))
        yield

    _script_pair_ok(installed_bluez)
    monkeypatch.setattr(api_bt_mod, "list_bt_adapters", lambda: [HCI0_MAC])
    monkeypatch.setattr(api_bt_mod, "quiesce_adapter_peers", fake_quiesce)

    _pair(api_bt_mod, MAC, HCI0_MAC)
    assert calls == []

    _pair(api_bt_mod, MAC, HCI0_MAC, quiesce_adapter=True)
    assert calls == [(HCI0_MAC, MAC)]
