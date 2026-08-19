"""BluezControl behaviour through the shared FakeBluez spawner substitute.

These tests never patch ``subprocess`` — they substitute the ``Spawner``
protocol that ``BluezControl`` takes at construction, which is the whole
point of the seam: assertions run against structured command records
(``fake.commands``) instead of hand-rolled Popen fakes.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, BluezControl, Outcome

ADAPTER_MAC = "C0:FB:F9:62:D7:D6"
ENEBY_MAC = "6C:5C:3D:35:17:99"


@pytest.fixture
def bluez(fake_bluez):
    return fake_bluez.control


def test_version(bluez, fake_bluez):
    assert bluez.version() == "bluetoothctl: 5.72"
    assert fake_bluez.commands[-1].argv == ("bluetoothctl", "--version")


def test_list_adapters(bluez, fake_bluez):
    adapters = bluez.list_adapters()
    assert [a.mac for a in adapters] == [ADAPTER_MAC]
    assert adapters[0].is_default is True
    assert fake_bluez.commands[-1].argv == ("bluetoothctl", "list")


def test_device_info_emits_select_for_scoped_adapter(bluez, fake_bluez):
    info = bluez.device_info(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert info.present is True
    assert info.paired is True
    assert info.name == "ENEBY Portable"
    cmd = fake_bluez.commands[-1]
    assert cmd.adapter_selected == ADAPTER_MAC
    assert f"info {ENEBY_MAC}" in cmd.script


def test_device_info_default_adapter_sends_no_select(bluez, fake_bluez):
    bluez.device_info(ENEBY_MAC)
    assert fake_bluez.commands[-1].adapter_selected == ""


def test_device_info_unknown_mac_is_not_present(bluez):
    info = bluez.device_info("AA:BB:CC:DD:EE:FF")
    assert info.present is False
    assert info.paired is None


def test_show_addressed_never_selects(bluez, fake_bluez):
    """The #193 regression test in one line: addressed show emits no select."""
    info = bluez.show(Adapter.addressed(ADAPTER_MAC))
    assert info.alias == "HP-ProDesk"
    assert info.powered is True
    fake_bluez.assert_never_selected(verb="show")


def test_select_hci_resolves_via_positional_fallback(bluez, fake_bluez):
    # No sysfs, no injected resolver in the fake → positional resolution
    # against ``list_adapters()`` (hci0 → first listed controller).
    info = bluez.device_info(ENEBY_MAC, Adapter.select("hci0"))
    assert info.present is True
    assert fake_bluez.commands[-1].adapter_selected == ADAPTER_MAC


def test_unresolvable_adapter_passes_through_with_scope_flag(bluez, fake_bluez, caplog):
    bluez.device_info(ENEBY_MAC, Adapter.select("hci9"))
    cmd = fake_bluez.commands[-1]
    assert cmd.adapter_selected == "hci9"
    result = bluez.run(["show"], adapter=Adapter.select("hci9"))
    assert result.scope_unresolved is True


def test_list_devices(bluez):
    devices = bluez.list_devices(Adapter.select(ADAPTER_MAC))
    assert [(d.mac, d.name) for d in devices] == [(ENEBY_MAC, "ENEBY Portable"), ("30:21:0E:0A:AE:5A", "Lenco LS-500")]


def test_power_result_reproduces_endpoint_heuristic(bluez):
    on = bluez.power(True, Adapter.select(ADAPTER_MAC))
    assert on.changed is True
    assert on.powered is True
    off = bluez.power(False, Adapter.select(ADAPTER_MAC))
    assert off.changed is True
    assert off.powered is False


def test_remove_result_types_the_stdout_marker(bluez, fake_bluez):
    fake_bluez.on("remove AA:BB:CC:DD:EE:FF", stdout="Device AA:BB:CC:DD:EE:FF not available\n", returncode=0)
    result = bluez.remove("AA:BB:CC:DD:EE:FF", Adapter.select(ADAPTER_MAC))
    assert result.removed is False
    assert result.not_available is True
    ok = bluez.remove(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert ok.removed is True
    assert ok.not_available is False


def test_connect_result_summary(bluez, fake_bluez):
    fake_bluez.on(
        "connect",
        stdout="[CHG] Device 6C:5C:3D:35:17:99 Connected: no\nFailed to connect: org.bluez.Error.Failed br-connection-page-timeout\n",
        returncode=0,
    )
    result = bluez.connect(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert result.ok is True  # bluetoothctl exits 0 even on connect failure
    assert result.summary == "Failed to connect: org.bluez.Error.Failed br-connection-page-timeout"


def test_timeout_outcome_via_injection(bluez, fake_bluez):
    fake_bluez.timeout("info")
    info = bluez.device_info(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert info.outcome is Outcome.TIMEOUT
    assert info.paired is None


def test_unavailable_outcome_via_injection(bluez, fake_bluez):
    fake_bluez.fail("list", exc=FileNotFoundError("No such file or directory: 'bluetoothctl'"))
    assert bluez.list_adapters() == []
    result = bluez.run(["show"])
    assert result.outcome is Outcome.OK  # unregistered verbs still hit the default corpus
    fake_bluez.fail("show", exc=FileNotFoundError("gone"))
    missing = bluez.run(["show"])
    assert missing.outcome is Outcome.UNAVAILABLE
    assert missing.ok is False
    assert "gone" in missing.error


def test_nonzero_outcome(bluez, fake_bluez):
    fake_bluez.nonzero("trust", stderr="boom")
    result = bluez.trust(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert result.outcome is Outcome.NONZERO
    assert result.ok is False
    assert result.text.endswith("boom")


def test_per_call_timeout_override_is_recorded(bluez, fake_bluez):
    bluez.device_info(ENEBY_MAC, Adapter.select(ADAPTER_MAC), timeout=4.0)
    assert fake_bluez.commands[-1].timeout == 4.0


def test_run_merges_stdout_and_stderr_like_legacy_runner(bluez, fake_bluez):
    fake_bluez.on("disconnect", stdout="out-line\n", stderr="err-line\n")
    result = bluez.disconnect(ENEBY_MAC, Adapter.select(ADAPTER_MAC))
    assert result.text == "out-line\nerr-line"


def test_session_ssp_reply_is_structured(bluez, fake_bluez):
    fake_bluez.session_script(
        [
            ("pair ", ["[agent] Confirm passkey 312997 (yes/no):", "Pairing successful"]),
        ]
    )
    with bluez.session(adapter=Adapter.select(ADAPTER_MAC)) as s:
        s.send(f"pair {ENEBY_MAC}")
        for line in s.lines(deadline=s._now() + 5.0):
            if "confirm passkey" in line.text.lower():
                s.reply("yes")
                break
    kinds = [c.kind for c in fake_bluez.commands]
    assert "popen" in kinds and "send" in kinds and "reply" in kinds


def test_session_cancel_on_enter_yields_noop_session(bluez, fake_bluez):
    with bluez.session(cancel=lambda: True) as s:
        assert s.aborted is True
        assert s.alive is False
        s.send("power on")  # no-op, must not raise
        assert list(s.lines(deadline=1.0)) == []
    kinds = [c.kind for c in fake_bluez.commands]
    assert "send" not in kinds


def test_scan_composite_collects_events_and_attributes_adapters(bluez, fake_bluez):
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {ENEBY_MAC} ENEBY Portable", f"[CHG] Device {ENEBY_MAC} RSSI: -43"]),
        ]
    )
    transcript = bluez.scan([ADAPTER_MAC], window_s=15.0)
    assert ENEBY_MAC in transcript.seen_macs
    assert transcript.rssi_by_mac[ENEBY_MAC] == -43
    # Per-adapter enumeration ran as its own scoped one-shot (#340 fix).
    enums = [c for c in fake_bluez.commands if c.kind == "run" and c.adapter_selected == ADAPTER_MAC]
    assert enums, "expected a scoped show+devices enumeration per adapter"
    assert transcript.device_adapter[ENEBY_MAC] == ADAPTER_MAC
    assert transcript.discovery_errors == ()


def test_scan_composite_surfaces_refused_discovery(bluez, fake_bluez):
    """A wedged controller refuses ``scan bredr``; the transcript must say so
    instead of reporting an empty room."""
    fake_bluez.session_script([("scan bredr", ["Failed to start discovery: org.bluez.Error.InProgress"])])
    transcript = bluez.scan([ADAPTER_MAC], window_s=1.0)
    assert transcript.discovery_errors == ("org.bluez.Error.InProgress",)


# ---------------------------------------------------------------------------
# hci_map — kernel MAC → hciN enumeration (sysfs canonical; hciconfig fallback)
# ---------------------------------------------------------------------------

_HCICONFIG_OUT = """hci0:\tType: Primary  Bus: USB
\tBD Address: C0:FB:F9:62:D7:D6  ACL MTU: 1021:8  SCO MTU: 64:1
\tUP RUNNING
\tRX bytes:1024 acl:0 sco:0 events:12 errors:0
"""


def _sysfs_tree(tmp_path, entries):
    root = tmp_path / "bluetooth"
    root.mkdir()
    for name, addr in entries.items():
        hci = root / name
        hci.mkdir()
        (hci / "address").write_text(addr + "\n")
    return root


def test_hci_map_reads_kernel_names_from_sysfs(tmp_path, fake_bluez):
    sysfs = _sysfs_tree(tmp_path, {"hci0": "A0:AD:9F:6E:B2:D5", "hci1": "88:a2:9e:c0:07:0d"})
    control = BluezControl(spawner=fake_bluez, sysfs_dir=sysfs)

    # Keys are uppercase, colon-stripped — what resolve_hci_for_mac looks up.
    # Sysfs is canonical for the kernel hciN label (issue #193).
    assert control.hci_map() == {"A0AD9F6EB2D5": "hci0", "88A29EC0070D": "hci1"}
    assert fake_bluez.commands == []  # sysfs answered — no subprocess spawned


def test_hci_map_falls_back_to_hciconfig_when_sysfs_missing(tmp_path, fake_bluez):
    fake_bluez.on("hciconfig", stdout=_HCICONFIG_OUT)
    control = BluezControl(spawner=fake_bluez, sysfs_dir=tmp_path / "missing")

    # Docker without /sys passthrough: hciconfig talks to the kernel via the
    # BlueZ control socket and prints the same canonical hciN labels.
    assert control.hci_map() == {"C0FBF962D7D6": "hci0"}
    assert fake_bluez.commands[-1].argv == ("hciconfig", "-a")


def test_hci_map_empty_when_sysfs_and_hciconfig_both_fail(tmp_path, fake_bluez):
    fake_bluez.fail("hciconfig")
    control = BluezControl(spawner=fake_bluez, sysfs_dir=tmp_path / "missing")

    assert control.hci_map() == {}


def test_power_reports_the_controller_state_when_the_command_prints_no_confirmation(bluez, fake_bluez):
    """Live BlueZ 5.72 with piped stdin exits before printing "Changing power
    on succeeded", so scraping stdout reported a power toggle that plainly
    happened as a failure.  The controller's own state is the authority."""
    fake_bluez.on("power off", stdout="")
    fake_bluez.on(
        "show",
        stdout=("Controller C0:FB:F9:62:D7:D6 (public)\n\tName: HP-ProDesk\n\tAlias: HP-ProDesk\n\tPowered: no\n"),
    )

    off = bluez.power(False, Adapter.select(ADAPTER_MAC))

    assert off.powered is False
    assert off.changed is True, "a confirmed state change must not read as a failed command"


def test_power_reports_failure_when_the_controller_did_not_change(bluez, fake_bluez):
    fake_bluez.on("power off", stdout="")
    # The controller stays powered: the toggle did not take.
    off = bluez.power(False, Adapter.select(ADAPTER_MAC))

    assert off.powered is True
    assert off.changed is False


def test_power_waits_for_bluez_to_apply_the_change(bluez, fake_bluez):
    """BlueZ applies a power toggle asynchronously: on the live stand the
    controller still reported the old state for about a second, so a single
    immediate read called every successful toggle a failure."""
    fake_bluez.on("power off", stdout="")
    fake_bluez.on(
        "show",
        stdout="Controller C0:FB:F9:62:D7:D6 (public)\n\tName: HP-ProDesk\n\tAlias: HP-ProDesk\n\tPowered: yes\n",
    )

    off = bluez.power(False, Adapter.select(ADAPTER_MAC))

    shows = [c for c in fake_bluez.commands if c.kind == "run" and "show" in c.script]
    assert len(shows) > 1, "the controller state was read once and never re-checked"
    assert off.changed is False
    assert off.powered is True
