"""Tests for services/adapter_recovery.py — the bluetooth-auto-recovery
wrapper that runs a progressive adapter-stuck-state recovery ladder
(mgmt reset → rfkill → USB bounce) when our reconnect loop hits its
threshold on a given HCI adapter."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, patch


def test_recover_adapter_blocking_invokes_library_with_hci_and_mac():
    """The sync wrapper must pass ``hci`` (int) and ``mac`` (str) straight
    to the library's ``recover_adapter`` coroutine and return its bool."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=1, adapter_mac="C0:FB:F9:62:D6:9D")

    assert ok is True
    fake_recover.assert_awaited_once_with(1, "C0:FB:F9:62:D6:9D")


def test_recover_adapter_blocking_returns_false_when_library_returns_false():
    """Failure from the library must be propagated as ``False`` — callers
    use the bool to decide whether to retry the reconnect or to give up
    and auto-release."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=False)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")

    assert ok is False


def test_recover_adapter_blocking_swallows_library_exceptions():
    """If ``recover_adapter`` raises (permissions, USB unbind denied,
    netlink not available inside a sandbox), the wrapper must return
    ``False`` — a partially-recovered adapter is not worth crashing the
    reconnect loop over."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(side_effect=PermissionError("CAP_NET_ADMIN missing"))
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")

    assert ok is False


def test_recover_adapter_blocking_returns_false_when_library_unavailable():
    """If ``bluetooth-auto-recovery`` isn't installed (dev machines, old
    builds), the wrapper must be a no-op that returns ``False`` — the
    bridge still boots, just without the recovery ladder."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    with patch.object(_mod, "_lib_recover_adapter", None):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")

    assert ok is False


def test_recover_adapter_blocking_respects_cooldown_per_adapter():
    """Two back-to-back calls for the same adapter within the cooldown
    window must NOT re-invoke the library — recovery is disruptive (USB
    bounce affects every device on that controller). The second call
    must short-circuit and return ``False``."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        first = _mod.recover_adapter_blocking(hci_index=1, adapter_mac="C0:FB:F9:62:D6:9D")
        second = _mod.recover_adapter_blocking(hci_index=1, adapter_mac="C0:FB:F9:62:D6:9D")

    assert first is True
    assert second is False
    fake_recover.assert_awaited_once()  # library called exactly once


def test_recover_adapter_blocking_cooldown_is_per_adapter_not_global():
    """Cooldown must key on adapter MAC — a stuck hci0 must not block a
    separate, genuinely-failing hci1 from getting its own recovery
    attempt."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        a = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")
        b = _mod.recover_adapter_blocking(hci_index=1, adapter_mac="C0:FB:F9:62:D6:9D")

    assert a is True
    assert b is True
    assert fake_recover.await_count == 2


def test_recover_adapter_blocking_rejects_invalid_mac():
    """Invalid MAC must short-circuit before touching the library — the
    underlying netlink/USB paths would fail later with a less useful
    error anyway."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="not-a-mac")

    assert ok is False
    fake_recover.assert_not_awaited()


def test_recover_adapter_blocking_works_from_inside_running_event_loop():
    """Production path calls this sync helper from within a running
    asyncio loop: ``bt_monitor.monitor_and_reconnect`` → ``await
    run_in_executor(..., mgr._handle_reconnect_failure)`` is one path,
    but ``_handle_reconnect_failure`` itself is also invoked directly
    from coroutine bodies at lines 145 and 410 of bt_monitor.py. In
    that second path ``asyncio.run()`` raises ``RuntimeError`` (`cannot
    be called from a running event loop`) and the except-block would
    silently turn every recovery attempt into a no-op. The wrapper must
    run the library coroutine on a worker-thread loop so it is safe
    regardless of caller context."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)

    async def _driver():
        with patch.object(_mod, "_lib_recover_adapter", fake_recover):
            return _mod.recover_adapter_blocking(hci_index=2, adapter_mac="AA:BB:CC:DD:EE:FF")

    result = asyncio.run(_driver())
    assert result is True
    fake_recover.assert_awaited_once_with(2, "AA:BB:CC:DD:EE:FF")


def test_recover_adapter_blocking_cooldown_check_and_mark_are_atomic():
    """Concurrent callers for the same adapter must not both observe
    'no cooldown' and proceed. With a two-step check-then-mark, thread
    A can read the empty slot, thread B reads it too before A marks,
    and both invoke the library back-to-back. Cooldown check+mark must
    be a single locked critical section so exactly one caller wins."""
    import sendspin_bridge.services.bluetooth.adapter_recovery as _mod

    _mod._reset_state_for_tests()

    call_count = 0
    call_count_lock = threading.Lock()

    async def _slow_recover(hci, mac):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        # Hold the library call open so concurrent threads overlap the
        # check+mark window in recover_adapter_blocking.
        await asyncio.sleep(0.05)
        return True

    N = 16
    barrier = threading.Barrier(N)
    results: list[bool] = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")
        with results_lock:
            results.append(ok)

    with patch.object(_mod, "_lib_recover_adapter", _slow_recover):
        threads = [threading.Thread(target=_worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert call_count == 1, f"library invoked {call_count} times, expected 1"
    assert results.count(True) == 1
    assert results.count(False) == N - 1
