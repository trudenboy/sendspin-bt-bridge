"""Preflight Bluetooth probe: detect daemon-down state.

Issue #254 — operators land on a Docker host where ``bluetoothd`` was
never started, so ``bluetoothctl list`` returns no Controller and the
bridge surfaces a generic "no controller detected" error.  The
actionable next step (``systemctl start bluetooth``) lives on the
host, but the original onboarding flow only suggested fixes inside
the bridge UI.  Preflight now probes ``systemctl is-active bluetooth``
when no controller is found, so onboarding can branch on
``bluetooth.daemon`` and lead with the host-side fix.
"""

from __future__ import annotations

from sendspin_bridge.services.diagnostics.preflight_status import collect_preflight_status


def _runtime_version_stub() -> str:
    return "test"


def _open_stub(*_a, **_kw):
    return __import__("io").StringIO("")


class _SubprocessStub:
    """Subprocess module stub returning canned output per command."""

    def __init__(self, responses: dict[tuple[str, ...], str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, args, capture_output=True, text=True, timeout=5):
        key = tuple(args)
        self.calls.append(key)
        stdout = self._responses.get(key, "")
        return type("R", (), {"stdout": stdout, "returncode": 0})()


def test_bluetooth_daemon_active_when_controller_present():
    """Happy path — controller surfaces, daemon recorded as 'active'."""
    sub = _SubprocessStub(
        {
            ("bluetoothctl", "list"): "Controller AA:BB:CC:DD:EE:FF Asus Laptop [default]\n",
            ("bluetoothctl", "devices", "Paired"): "",
        }
    )
    result = collect_preflight_status(
        get_server_name_fn=lambda: "pipewire",
        list_sinks_fn=lambda: [],
        subprocess_module=sub,
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda _p: True,
        open_fn=_open_stub,
        connect_fn=lambda *_a: None,
    )
    bt = result["bluetooth"]
    assert bt["controller"] is True
    assert bt["daemon"] == "active"


def test_bluetooth_daemon_inactive_when_no_controller_and_systemd_says_inactive():
    """Issue #254 — bluetoothd inactive on host, no controller surfaces.

    Preflight should record the systemd state so onboarding can lead
    with ``systemctl start bluetooth`` instead of "Refresh adapters".
    """
    sub = _SubprocessStub(
        {
            ("bluetoothctl", "list"): "",  # no controller
            ("systemctl", "is-active", "bluetooth"): "inactive\n",
            ("bluetoothctl", "devices", "Paired"): "",
        }
    )
    result = collect_preflight_status(
        get_server_name_fn=lambda: "pipewire",
        list_sinks_fn=lambda: [],
        subprocess_module=sub,
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda _p: True,
        open_fn=_open_stub,
        connect_fn=lambda *_a: None,
    )
    bt = result["bluetooth"]
    assert bt["controller"] is False
    assert bt["daemon"] == "inactive"
    assert ("systemctl", "is-active", "bluetooth") in sub.calls


def test_bluetooth_daemon_unknown_when_systemctl_unavailable():
    """Non-systemd hosts (alpine, distroless, some BSDs in WSL) — the
    systemd probe raises FileNotFoundError.  Daemon field falls back
    to ``unknown`` so onboarding doesn't false-flag a daemon-down
    state.
    """

    class _Sub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, args, **_kw):
            self.calls.append(tuple(args))
            if args[:2] == ["systemctl", "is-active"]:
                raise FileNotFoundError("systemctl not found")
            return type("R", (), {"stdout": "", "returncode": 0})()

    sub = _Sub()
    result = collect_preflight_status(
        get_server_name_fn=lambda: "pipewire",
        list_sinks_fn=lambda: [],
        subprocess_module=sub,
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda _p: True,
        open_fn=_open_stub,
        connect_fn=lambda *_a: None,
    )
    bt = result["bluetooth"]
    assert bt["controller"] is False
    assert bt["daemon"] == "unknown"
