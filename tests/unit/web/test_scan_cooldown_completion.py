"""The scan cooldown must run from the end of a scan, not from its start.

``_SCAN_BASE_DURATION`` (15 s) is longer than ``_SCAN_COOLDOWN`` (10 s), so
stamping the completion timestamp when the worker *starts* means the cooldown
has always elapsed by the time the scan finishes — the adapter never gets the
rest period the gate exists to give it.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def api_bt(monkeypatch):
    if "sendspin_bridge.web.routes.api_bt" in sys.modules:
        stub = sys.modules["sendspin_bridge.web.routes.api_bt"]
        if getattr(stub, "__file__", None) is None:
            sys.modules.pop("sendspin_bridge.web.routes.api_bt")

    import sendspin_bridge.web.routes.api_bt as mod

    monkeypatch.setattr(mod, "_last_scan_completed", 0.0)
    return mod


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cooldown_starts_when_the_scan_finishes(api_bt, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(api_bt.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(api_bt, "_resolve_scan_adapter_macs", lambda adapter: [])
    monkeypatch.setattr(api_bt, "finish_scan_job", lambda *a, **kw: None)

    def _scan(adapter_macs, window_s=0.0):
        clock.advance(window_s)
        return types.SimpleNamespace(
            names={},
            device_adapter={},
            rssi_by_mac={},
            discovery_errors=[],
            seen_macs=set(),
            active_macs=set(),
        )

    monkeypatch.setattr(api_bt, "get_bluez", lambda: types.SimpleNamespace(scan=_scan))

    started_at = clock.now
    api_bt._run_bt_scan("job-1")

    assert api_bt._last_scan_completed == started_at + api_bt._SCAN_BASE_DURATION


def test_cooldown_is_stamped_even_when_the_scan_raises(api_bt, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(api_bt.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(api_bt, "finish_scan_job", lambda *a, **kw: None)

    def _boom(adapter):
        clock.advance(3.0)
        raise RuntimeError("adapter wedged")

    monkeypatch.setattr(api_bt, "_resolve_scan_adapter_macs", _boom)

    started_at = clock.now
    api_bt._run_bt_scan("job-2")

    assert api_bt._last_scan_completed == started_at + 3.0
