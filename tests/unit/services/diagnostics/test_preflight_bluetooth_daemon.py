"""Preflight Bluetooth probe: detect daemon-down state.

Issue #254 — operators land on a Docker host where ``bluetoothd`` was
never started, so ``bluetoothctl list`` returns no Controller and the
bridge surfaces a generic "no controller detected" error.  The
actionable next step (``systemctl start bluetooth``) lives on the
host, but the original onboarding flow only suggested fixes inside
the bridge UI.  Preflight now probes ``systemctl is-active bluetooth``
when no controller is found, so onboarding can branch on
``bluetooth.daemon`` and lead with the host-side fix.

Batch 1 (BluezControl migration): the ``subprocess_module`` seam is
replaced by an injected ``bluez`` transport plus a narrow
``daemon_state_fn`` for the one non-bluetoothctl probe (systemctl).
"""

from __future__ import annotations

from sendspin_bridge.services.diagnostics.preflight_status import collect_preflight_status


def _runtime_version_stub() -> str:
    return "test"


def _open_stub(*_a, **_kw):
    return __import__("io").StringIO("")


class _DaemonProbe:
    """Recording stand-in for the systemctl daemon-state probe."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.state


def _collect(fake_bluez, daemon_probe) -> dict:
    return collect_preflight_status(
        get_server_name_fn=lambda: "pipewire",
        list_sinks_fn=lambda: [],
        bluez=fake_bluez.control,
        daemon_state_fn=daemon_probe,
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda _p: True,
        open_fn=_open_stub,
        connect_fn=lambda *_a: None,
    )


def test_bluetooth_daemon_active_when_controller_present(fake_bluez):
    """Happy path — controller surfaces, daemon recorded as 'active'."""
    probe = _DaemonProbe("unused")

    bt = _collect(fake_bluez, probe)["bluetooth"]

    assert bt["controller"] is True
    assert bt["adapter"] == "C0:FB:F9:62:D7:D6"  # first controller row, as before
    assert bt["daemon"] == "active"
    assert bt["paired_devices"] == 2  # default 5.72 corpus lists ENEBY + Lenco
    assert probe.calls == 0  # systemctl probe must not run when a controller is present
    assert any(c.argv == ("bluetoothctl", "list") for c in fake_bluez.commands)


def test_paired_count_ignores_async_event_noise(fake_bluez):
    """Async ``[CHG] Device …`` notifications interleaved on stdout must not
    inflate the paired-device count — only literal ``Device <mac> <name>``
    response rows count (the ghost-row whitelist from the paired parser).
    """
    fake_bluez.on(
        "devices Paired",
        stdout=(
            "[CHG] Device 6C:5C:3D:35:17:99 RSSI: 0xffffffc3 (-61)\n"
            "Device 6C:5C:3D:35:17:99 ENEBY Portable\n"
            "Device 30:21:0E:0A:AE:5A Lenco LS-500\n"
        ),
    )
    probe = _DaemonProbe("unused")

    bt = _collect(fake_bluez, probe)["bluetooth"]

    assert bt["paired_devices"] == 2


def test_bluetooth_daemon_inactive_when_no_controller_and_systemd_says_inactive(fake_bluez):
    """Issue #254 — bluetoothd inactive on host, no controller surfaces.

    Preflight should record the systemd state so onboarding can lead
    with ``systemctl start bluetooth`` instead of "Refresh adapters".
    """
    fake_bluez.on("list", stdout="")  # no controller
    probe = _DaemonProbe("inactive")

    bt = _collect(fake_bluez, probe)["bluetooth"]

    assert bt["controller"] is False
    assert bt["daemon"] == "inactive"
    assert probe.calls == 1


def test_bluetooth_daemon_unknown_when_systemctl_unavailable():
    """Non-systemd hosts (alpine, distroless, WSL) — the systemd probe
    raises FileNotFoundError inside the default ``daemon_state_fn``.
    The daemon field falls back to ``unknown`` so onboarding doesn't
    false-flag a daemon-down state."""
    import sendspin_bridge.services.diagnostics.preflight_status as preflight

    def _raise_not_found(*_a, **_kw):
        raise FileNotFoundError("systemctl not found")

    original_run = preflight.subprocess.run
    preflight.subprocess.run = _raise_not_found
    try:
        assert preflight._default_daemon_state() == "unknown"
    finally:
        preflight.subprocess.run = original_run
