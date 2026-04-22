"""Tests for services/adapter_recovery.py — the bluetooth-auto-recovery
wrapper that runs a progressive adapter-stuck-state recovery ladder
(mgmt reset → rfkill → USB bounce) when our reconnect loop hits its
threshold on a given HCI adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


def test_recover_adapter_blocking_invokes_library_with_hci_and_mac():
    """The sync wrapper must pass ``hci`` (int) and ``mac`` (str) straight
    to the library's ``recover_adapter`` coroutine and return its bool."""
    import services.adapter_recovery as _mod

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
    import services.adapter_recovery as _mod

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
    import services.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(side_effect=PermissionError("CAP_NET_ADMIN missing"))
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")

    assert ok is False


def test_recover_adapter_blocking_returns_false_when_library_unavailable():
    """If ``bluetooth-auto-recovery`` isn't installed (dev machines, old
    builds), the wrapper must be a no-op that returns ``False`` — the
    bridge still boots, just without the recovery ladder."""
    import services.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    with patch.object(_mod, "_lib_recover_adapter", None):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="AA:BB:CC:DD:EE:FF")

    assert ok is False


def test_recover_adapter_blocking_respects_cooldown_per_adapter():
    """Two back-to-back calls for the same adapter within the cooldown
    window must NOT re-invoke the library — recovery is disruptive (USB
    bounce affects every device on that controller). The second call
    must short-circuit and return ``False``."""
    import services.adapter_recovery as _mod

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
    import services.adapter_recovery as _mod

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
    import services.adapter_recovery as _mod

    _mod._reset_state_for_tests()
    fake_recover = AsyncMock(return_value=True)
    with patch.object(_mod, "_lib_recover_adapter", fake_recover):
        ok = _mod.recover_adapter_blocking(hci_index=0, adapter_mac="not-a-mac")

    assert ok is False
    fake_recover.assert_not_awaited()
