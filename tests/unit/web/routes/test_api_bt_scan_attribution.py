"""Post-scan device→adapter attribution (issue #340, secondary defect).

Per-adapter device enumeration must run in dedicated bluetoothctl
sessions.  In the long-lived scan session the ``select <MAC>; show``
marker lines interleave with async discovery notifications on piped
stdin — the same unreliability that produced the alias swap in #193 —
so devices could be attributed to whichever controller happened to be
selected when the output flushed.
"""

from __future__ import annotations

from unittest.mock import patch

from sendspin_bridge.web.routes.api_bt import _parse_scan_output, _run_bluetoothctl_scan

ADAPTER_A = "A0:AD:9F:6E:B2:D5"
ADAPTER_B = "88:A2:9E:C0:07:0D"
SPEAKER_A = "FC:58:FA:EB:08:6C"
SPEAKER_B = "30:21:0E:0A:AE:5A"


class _FakeScanProc:
    """Long-lived scan session: emits discovery noise only."""

    def __init__(self) -> None:
        self.stdin = self
        self.written: list[str] = []

    def write(self, data: str) -> None:
        self.written.append(data)

    def flush(self) -> None:
        pass

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        return (
            f"[NEW] Device {SPEAKER_A} ENEBY20\n[NEW] Device {SPEAKER_B} Lenco LS-500\n",
            "",
        )

    def kill(self) -> None:
        pass

    def wait(self) -> None:
        pass


def _enum_output(adapter_mac: str, device_mac: str, device_name: str) -> str:
    return f"Controller {adapter_mac} (public)\n\tPowered: yes\nDevice {device_mac} {device_name}\n"


def test_enumeration_runs_in_dedicated_sessions_and_attributes_devices(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.time.sleep", lambda _s: None)
    run_calls: list[str] = []

    def _fake_run(cmd, **kwargs):
        run_calls.append(kwargs.get("input", ""))

        class _Completed:
            returncode = 0
            stdout = (
                _enum_output(ADAPTER_A, SPEAKER_A, "ENEBY20")
                if f"select {ADAPTER_A}" in kwargs.get("input", "")
                else _enum_output(ADAPTER_B, SPEAKER_B, "Lenco LS-500")
            )
            stderr = ""

        return _Completed()

    with (
        patch("sendspin_bridge.web.routes.api_bt.subprocess.Popen", return_value=_FakeScanProc()),
        patch("sendspin_bridge.web.routes.api_bt.subprocess.run", side_effect=_fake_run),
    ):
        stdout = _run_bluetoothctl_scan([ADAPTER_A, ADAPTER_B])

    # One dedicated enumeration session per adapter.
    assert len(run_calls) == 2
    assert f"select {ADAPTER_A}" in run_calls[0]
    assert f"select {ADAPTER_B}" in run_calls[1]

    seen, names, device_adapter, _active, _rssi = _parse_scan_output(stdout)
    assert {SPEAKER_A, SPEAKER_B} <= seen
    assert device_adapter[SPEAKER_A] == ADAPTER_A
    assert device_adapter[SPEAKER_B] == ADAPTER_B


def test_enumeration_failure_degrades_to_unattributed(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.time.sleep", lambda _s: None)

    def _boom(cmd, **kwargs):
        raise OSError("bluetoothctl unavailable")

    with (
        patch("sendspin_bridge.web.routes.api_bt.subprocess.Popen", return_value=_FakeScanProc()),
        patch("sendspin_bridge.web.routes.api_bt.subprocess.run", side_effect=_boom),
    ):
        stdout = _run_bluetoothctl_scan([ADAPTER_A])

    # Scan results survive; devices simply stay unattributed.
    seen, _names, device_adapter, _active, _rssi = _parse_scan_output(stdout)
    assert SPEAKER_A in seen
    assert device_adapter == {}
