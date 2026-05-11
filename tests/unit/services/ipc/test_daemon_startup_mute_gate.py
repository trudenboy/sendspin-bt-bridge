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
