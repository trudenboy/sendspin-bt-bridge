"""Tests for the AVDTP-ready gate on daemon startup mute (issue #269).

The daemon used to mute the BT sink immediately on startup. On stacks
where the peer (Sony WH-1000XM4 confirmed) treats silence on a half-formed
AVDTP transport as "no inbound stream", this triggered AVDTP-Suspend from
the peer, which then collided with PipeWire's AVDTP-Start once MA
streaming began.

The fix: wait until ``MediaTransport1.State`` is ``"pending"`` or
``"active"`` (with a safety timeout) before muting.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub external modules unavailable on the dev test env (mirrors the
# pattern from test_daemon_process.py).
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "sendspin",
    "sendspin.audio",
    "sendspin.audio_devices",
    "sendspin.daemon",
    "sendspin.daemon.daemon",
    "sendspin.settings",
    "sendspin.client_settings",
    "sendspin.models",
    "sendspin.models.player_command",
    "aiosendspin",
    "aiosendspin.models",
    "aiosendspin.models.types",
    "pulsectl",
    "pulsectl_asyncio",
    "dbus",
    "dbus.mainloop",
    "dbus.mainloop.glib",
    "gi",
    "gi.repository",
    "sendspin_bridge.services.ipc.bridge_daemon",
    "sendspin_bridge.services.audio.pulse",
]

for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


@pytest.mark.asyncio
async def test_mute_waits_for_transport_pending(monkeypatch):
    """Mute should fire on the first non-idle state ('pending')."""
    from sendspin_bridge.services.ipc import daemon_process

    states = iter(["idle", "idle", "pending"])

    def _fake_state(_path):
        try:
            return next(states)
        except StopIteration:
            return "active"

    mute_calls = []

    async def _do_mute(sink, mute):
        mute_calls.append((sink, mute))
        return True

    monkeypatch.setattr(daemon_process, "_dbus_get_media_transport_state", _fake_state)

    ok = await daemon_process._mute_when_transport_ready(
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        do_mute=_do_mute,
        poll_interval=0.0,
        timeout=2.0,
    )

    assert ok is True
    assert mute_calls == [("bluez_output.AA_BB_CC_DD_EE_FF.1", True)]


@pytest.mark.asyncio
async def test_mute_fires_after_timeout_if_transport_never_settles(monkeypatch):
    """If transport never reaches non-idle, mute anyway after timeout —
    better than never muting; cost is at most one click."""
    from sendspin_bridge.services.ipc import daemon_process

    monkeypatch.setattr(daemon_process, "_dbus_get_media_transport_state", lambda _p: "idle")

    mute_calls = []

    async def _do_mute(sink, mute):
        mute_calls.append((sink, mute))
        return True

    ok = await daemon_process._mute_when_transport_ready(
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        do_mute=_do_mute,
        poll_interval=0.02,
        timeout=0.1,
    )

    assert ok is True
    assert mute_calls == [("bluez_output.AA_BB_CC_DD_EE_FF.1", True)]


@pytest.mark.asyncio
async def test_mute_fires_immediately_when_no_device_path(monkeypatch):
    """Without a device_path we can't introspect transport state; mute right
    away to preserve pre-#269 behavior on adapters that can't be probed."""
    from sendspin_bridge.services.ipc import daemon_process

    state_calls = []

    def _fake_state(_p):
        state_calls.append(_p)
        return None

    monkeypatch.setattr(daemon_process, "_dbus_get_media_transport_state", _fake_state)

    mute_calls = []

    async def _do_mute(sink, mute):
        mute_calls.append((sink, mute))
        return True

    ok = await daemon_process._mute_when_transport_ready(
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        device_path=None,
        do_mute=_do_mute,
        poll_interval=0.5,
        timeout=10.0,
    )

    assert ok is True
    assert mute_calls == [("bluez_output.AA_BB_CC_DD_EE_FF.1", True)]
    assert state_calls == [], "Should not consult D-Bus when device_path is None"


@pytest.mark.asyncio
async def test_mute_fires_immediately_when_transport_active(monkeypatch):
    """If transport is already active (stream in flight) we still mute —
    the gate is about not muting on idle, not about declining to mute."""
    from sendspin_bridge.services.ipc import daemon_process

    monkeypatch.setattr(daemon_process, "_dbus_get_media_transport_state", lambda _p: "active")

    mute_calls = []

    async def _do_mute(sink, mute):
        mute_calls.append((sink, mute))
        return True

    ok = await daemon_process._mute_when_transport_ready(
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
        do_mute=_do_mute,
        poll_interval=0.5,
        timeout=2.0,
    )

    assert ok is True
    assert mute_calls == [("bluez_output.AA_BB_CC_DD_EE_FF.1", True)]


# ---------------------------------------------------------------------------
# Fix 3: unmute watchdog early-bail when sink object is gone
# ---------------------------------------------------------------------------


def _install_pulse_stubs(monkeypatch, *, mute_fn, list_sinks_fn, move_fn):
    """Patch the stubbed pulse module's attributes so the watcher's
    ``from sendspin_bridge.services.audio.pulse import ...`` picks them up."""
    pulse_module = sys.modules["sendspin_bridge.services.audio.pulse"]
    monkeypatch.setattr(pulse_module, "aset_sink_mute", mute_fn, raising=False)
    monkeypatch.setattr(pulse_module, "alist_sinks", list_sinks_fn, raising=False)
    monkeypatch.setattr(pulse_module, "amove_pid_sink_inputs", move_fn, raising=False)


@pytest.mark.asyncio
async def test_unmute_watchdog_bails_when_sink_gone(monkeypatch):
    """When the sink object has disappeared (BlueZ teardown after AVDTP
    failure), the unmute watchdog must bail after the first failed
    aset_sink_mute call rather than spending 6+ s on doomed 3 retries
    (issue #269)."""
    import asyncio as _asyncio
    import time as _time

    from sendspin_bridge.services.ipc import daemon_process

    status: dict = {}
    stop_event = _asyncio.Event()

    # Bump time past the watcher's 15 s streaming-wait deadline so the
    # control flow goes straight to the unmute path.
    real_monotonic = _time.monotonic
    monkeypatch.setattr(daemon_process.time, "monotonic", lambda: real_monotonic() + 1e6)

    mute_calls: list[tuple[str, bool]] = []

    async def _fake_mute(sink, muted):
        mute_calls.append((sink, muted))
        return False  # always fail — sink is gone

    async def _fake_alist_sinks():
        return []  # sink no longer in the system

    async def _fake_amove(_pid, _sink):
        return 0

    _install_pulse_stubs(
        monkeypatch,
        mute_fn=_fake_mute,
        list_sinks_fn=_fake_alist_sinks,
        move_fn=_fake_amove,
    )

    await daemon_process._startup_unmute_watcher(
        status=status,
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        stop_event=stop_event,
        player_name="TEST",
        on_status_change=None,
    )

    assert len(mute_calls) == 1, (
        f"Expected single unmute attempt when sink is gone, got {len(mute_calls)} (retry storm — issue #269)"
    )


@pytest.mark.asyncio
async def test_unmute_watchdog_retries_when_sink_present(monkeypatch):
    """When the sink IS present but the mute call transiently failed, keep
    the existing 3-retry behavior — only the missing-sink case should
    short-circuit."""
    import asyncio as _asyncio
    import time as _time

    from sendspin_bridge.services.ipc import daemon_process

    status: dict = {}
    stop_event = _asyncio.Event()

    real_monotonic = _time.monotonic
    monkeypatch.setattr(daemon_process.time, "monotonic", lambda: real_monotonic() + 1e6)

    async def _no_sleep(*_a, **_kw):
        return None

    monkeypatch.setattr(daemon_process.asyncio, "sleep", _no_sleep)

    mute_calls: list[tuple[str, bool]] = []

    async def _fake_mute(sink, muted):
        mute_calls.append((sink, muted))
        # Succeed on the 3rd call (1 initial + 2 retries)
        return len(mute_calls) >= 3

    async def _fake_alist_sinks():
        # Sink IS present — retries should run
        return [{"name": "bluez_output.AA_BB_CC_DD_EE_FF.1"}]

    async def _fake_amove(_pid, _sink):
        return 0

    _install_pulse_stubs(
        monkeypatch,
        mute_fn=_fake_mute,
        list_sinks_fn=_fake_alist_sinks,
        move_fn=_fake_amove,
    )

    await daemon_process._startup_unmute_watcher(
        status=status,
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        stop_event=stop_event,
        player_name="TEST",
        on_status_change=None,
    )

    assert len(mute_calls) == 3, (
        f"Expected exactly 3 unmute attempts (1 initial + 2 retries before success), got {len(mute_calls)}"
    )
