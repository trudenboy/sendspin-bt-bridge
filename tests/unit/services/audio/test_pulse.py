"""Tests for services/pulse.py — PulseAudio helpers."""

import asyncio
import sys
from unittest.mock import MagicMock, patch

# Stub pulsectl libraries before importing the module under test.
sys.modules.setdefault("pulsectl", MagicMock())
sys.modules.setdefault("pulsectl_asyncio", MagicMock())

# Another test file (test_daemon_process) may stub services.pulse with a
# MagicMock at module-import time.  Force-reload the real module here.
sys.modules.pop("sendspin_bridge.services.audio.pulse", None)

import sendspin_bridge.services.audio.pulse as _pulse_mod  # noqa: E402
from sendspin_bridge.services.audio.pulse import _fallback_set_volume  # noqa: E402

# ---------------------------------------------------------------------------
# _fallback_set_volume clamping
# ---------------------------------------------------------------------------


def test_fallback_set_volume_clamps_high():
    """Volume above 100 should be clamped to 100."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", 150)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "100%"]


def test_fallback_set_volume_clamps_low():
    """Negative volume should be clamped to 0."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", -10)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "0%"]


def test_fallback_set_volume_normal():
    """Normal volume value should pass through unchanged."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", 50)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "50%"]


def test_fallback_set_volume_failure():
    """Non-zero returncode should return False."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1)
        assert _fallback_set_volume("bad_sink", 50) is False


# ---------------------------------------------------------------------------
# _cleanup_loops
# ---------------------------------------------------------------------------


def test_cleanup_loops_closes_open_loops():
    """_cleanup_loops should close all tracked loops and clear the list."""
    loop1 = asyncio.new_event_loop()
    loop2 = asyncio.new_event_loop()
    with _pulse_mod._thread_loops_lock:
        _pulse_mod._thread_loops.append(loop1)
        _pulse_mod._thread_loops.append(loop2)
    try:
        _pulse_mod._cleanup_loops()
        assert loop1.is_closed()
        assert loop2.is_closed()
        with _pulse_mod._thread_loops_lock:
            assert len(_pulse_mod._thread_loops) == 0
    finally:
        if not loop1.is_closed():
            loop1.close()
        if not loop2.is_closed():
            loop2.close()


def test_cleanup_loops_skips_already_closed():
    """_cleanup_loops should not error on already-closed loops."""
    loop = asyncio.new_event_loop()
    loop.close()
    with _pulse_mod._thread_loops_lock:
        _pulse_mod._thread_loops.append(loop)
    _pulse_mod._cleanup_loops()  # should not raise
    with _pulse_mod._thread_loops_lock:
        assert len(_pulse_mod._thread_loops) == 0


# ---------------------------------------------------------------------------
# _fallback_list_cards — parses `pactl list cards` output
# ---------------------------------------------------------------------------


_PACTL_LIST_CARDS_SAMPLE = """\
Card #2
\tName: alsa_card.platform-sound
\tDriver: module-alsa-card.c
\tProperties:
\t\talsa.card = "0"
\tProfiles:
\t\toutput:analog-stereo: Analog Stereo Output (sinks: 1, sources: 0, priority: 6500, available: yes)
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\tActive Profile: output:analog-stereo
\tPorts:
\t\tanalog-output: Analog Output (type: Analog, priority: 9900, latency offset: 0 usec, available: unknown)

Card #3
\tName: bluez_card.FC_58_FA_EB_08_6C
\tDriver: module-bluez5-device.c
\tOwner Module: 25
\tProperties:
\t\tdevice.description = "ENEBY20"
\tProfiles:
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\t\ta2dp_sink: High Fidelity Playback (A2DP Sink) (sinks: 1, sources: 0, priority: 40, available: yes)
\t\theadset_head_unit: Headset Head Unit (HSP/HFP) (sinks: 1, sources: 1, priority: 30, available: yes)
\tActive Profile: headset_head_unit
\tPorts:
"""


def test_fallback_list_cards_parses_name_and_active_profile():
    """`pactl list cards` output → list of dicts with name, driver, active_profile."""
    from sendspin_bridge.services.audio.pulse import _fallback_list_cards

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0, stdout=_PACTL_LIST_CARDS_SAMPLE)
        cards = _fallback_list_cards()

    assert len(cards) == 2
    alsa = next(c for c in cards if c["name"] == "alsa_card.platform-sound")
    bluez = next(c for c in cards if c["name"] == "bluez_card.FC_58_FA_EB_08_6C")
    assert alsa["driver"] == "module-alsa-card.c"
    assert alsa["active_profile"] == "output:analog-stereo"
    assert bluez["driver"] == "module-bluez5-device.c"
    assert bluez["active_profile"] == "headset_head_unit"


def test_fallback_list_cards_collects_available_profiles():
    """Each card dict lists available profile names."""
    from sendspin_bridge.services.audio.pulse import _fallback_list_cards

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0, stdout=_PACTL_LIST_CARDS_SAMPLE)
        cards = _fallback_list_cards()

    bluez = next(c for c in cards if c["name"] == "bluez_card.FC_58_FA_EB_08_6C")
    assert "a2dp_sink" in bluez["profiles"]
    assert "headset_head_unit" in bluez["profiles"]
    assert "off" in bluez["profiles"]


def test_fallback_list_cards_handles_pactl_error():
    """Non-zero returncode returns empty list."""
    from sendspin_bridge.services.audio.pulse import _fallback_list_cards

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1, stdout="")
        assert _fallback_list_cards() == []


def test_fallback_list_cards_handles_subprocess_exception():
    """subprocess errors return empty list rather than crashing."""
    import subprocess as _sp

    from sendspin_bridge.services.audio.pulse import _fallback_list_cards

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.SubprocessError = _sp.SubprocessError
        mock_sub.run.side_effect = OSError("pactl missing")
        assert _fallback_list_cards() == []


# ---------------------------------------------------------------------------
# _fallback_set_card_profile — wraps `pactl set-card-profile`
# ---------------------------------------------------------------------------


def test_fallback_set_card_profile_success():
    """pactl returncode 0 → True."""
    from sendspin_bridge.services.audio.pulse import _fallback_set_card_profile

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        assert _fallback_set_card_profile("bluez_card.FC_58_FA_EB_08_6C", "a2dp_sink") is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == [
            "pactl",
            "set-card-profile",
            "bluez_card.FC_58_FA_EB_08_6C",
            "a2dp_sink",
        ]


def test_fallback_set_card_profile_failure():
    """pactl returncode non-zero → False."""
    from sendspin_bridge.services.audio.pulse import _fallback_set_card_profile

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1, stderr="Failed")
        assert _fallback_set_card_profile("bluez_card.xx", "a2dp_sink") is False


# ---------------------------------------------------------------------------
# _fallback_cycle_card_profile — off → target cycle via pactl
# ---------------------------------------------------------------------------


def test_fallback_cycle_card_profile_sets_off_then_target():
    """Cycle runs `pactl set-card-profile <card> off`, sleeps, then sets target."""
    from sendspin_bridge.services.audio.pulse import _fallback_cycle_card_profile

    with (
        patch.object(_pulse_mod, "subprocess") as mock_sub,
        patch.object(_pulse_mod, "time", create=True) as mock_time,
    ):
        mock_sub.run.return_value = MagicMock(returncode=0)
        mock_time.sleep = MagicMock()
        result = _fallback_cycle_card_profile("bluez_card.xx", "a2dp_sink", off_wait=0.0)

    assert result is True
    cmds = [call.args[0] for call in mock_sub.run.call_args_list]
    assert ["pactl", "set-card-profile", "bluez_card.xx", "off"] in cmds
    assert ["pactl", "set-card-profile", "bluez_card.xx", "a2dp_sink"] in cmds


def test_fallback_cycle_card_profile_continues_when_off_set_fails():
    """Cycle does not abort if the `off` step errors — final target still attempted."""
    import subprocess as _sp

    from sendspin_bridge.services.audio.pulse import _fallback_cycle_card_profile

    call_results = [_sp.SubprocessError("off failed"), MagicMock(returncode=0)]

    def run_side_effect(*_args, **_kwargs):
        result = call_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.SubprocessError = _sp.SubprocessError
        mock_sub.run.side_effect = run_side_effect
        assert _fallback_cycle_card_profile("bluez_card.xx", "a2dp_sink", off_wait=0.0) is True
    assert not call_results  # both steps executed


# ---------------------------------------------------------------------------
# areload_bluez5_discover_module — global cooldown + module lookup
# ---------------------------------------------------------------------------


def _reset_bluez5_reload_cooldown():
    _pulse_mod._LAST_BLUEZ5_RELOAD_TS = 0.0


def test_reload_bluez5_discover_module_cooldown_throttles_calls():
    """Second reload within the 60s window returns False without hitting pactl."""
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()
    _pulse_mod._LAST_BLUEZ5_RELOAD_TS = 1000.0

    with patch("time.monotonic", return_value=1010.0):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is False


def test_reload_bluez5_discover_module_returns_false_when_module_not_loaded():
    """If `pactl list modules short` shows no module-bluez5-discover, return False."""
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    async def fake_create_subprocess_exec(*args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            # stdout has no module-bluez5-discover row
            return (b"12\tmodule-null-sink\tc=1\n", b"")

        proc.communicate = communicate
        return proc

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is False


def test_reload_bluez5_discover_module_unloads_and_loads_when_present():
    """When module is found, pactl unload + load are issued in order."""
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    call_log: list[tuple[str, ...]] = []

    async def fake_create_subprocess_exec(*args, **_kwargs):
        call_log.append(args)
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            if args[:3] == ("pactl", "list", "modules"):
                return (b"25\tmodule-bluez5-discover\t\n", b"")
            return (b"", b"")

        proc.communicate = communicate
        return proc

    async def fake_sleep(_delay):
        return None

    with (
        patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec),
        patch.object(asyncio, "sleep", side_effect=fake_sleep),
    ):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is True
    verbs = [args[1] if len(args) > 1 else "" for args in call_log]
    assert "list" in verbs
    assert "unload-module" in verbs
    assert "load-module" in verbs


def test_reload_bluez5_discover_module_propagates_cancellation():
    """``asyncio.CancelledError`` during pactl must NOT be swallowed.

    Suppressing cancellation here can hang shutdown/restart. The function
    must let the cancel propagate so the outer task can unwind cleanly.
    """
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        raise asyncio.CancelledError

    async def runner():
        with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            await areload_bluez5_discover_module()

    import pytest as _pytest

    with _pytest.raises(asyncio.CancelledError):
        asyncio.run(runner())


def test_reload_bluez5_discover_module_does_not_burn_cooldown_on_trivial_failures():
    """A failed ``pactl list modules`` call must NOT set the cooldown timestamp.

    If we burn the 60s cooldown before confirming anything happened, a later
    successful reload attempt is needlessly blocked. Cooldown should only
    fire after a reload actually completes.
    """
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()
    assert _pulse_mod._LAST_BLUEZ5_RELOAD_TS == 0.0

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 1  # pactl list fails

        async def communicate():
            return (b"", b"pactl not available")

        proc.communicate = communicate
        return proc

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is False
    # Cooldown must NOT be burned because nothing actually got reloaded.
    assert _pulse_mod._LAST_BLUEZ5_RELOAD_TS == 0.0


def test_reload_bluez5_discover_module_does_not_burn_cooldown_when_module_absent():
    """If module-bluez5-discover isn't loaded, cooldown must NOT be burned."""
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            return (b"12\tmodule-null-sink\tc=1\n", b"")

        proc.communicate = communicate
        return proc

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is False
    assert _pulse_mod._LAST_BLUEZ5_RELOAD_TS == 0.0


def test_reload_bluez5_discover_module_serializes_concurrent_callers():
    """Two concurrent callers must not both execute unload+load back-to-back.

    The 60s cooldown is set only on success, so without an in-progress guard
    two callers can both pass the cooldown check before either sets the
    timestamp, causing two global BT-sink disruptions where the contract
    promised one. Second concurrent caller must observe "in progress" and
    return False without issuing its own ``unload-module`` call.
    """
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    started = asyncio.Event()
    block = asyncio.Event()
    call_log: list[tuple[str, ...]] = []
    unload_seen = 0

    async def fake_create_subprocess_exec(*args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            nonlocal unload_seen
            call_log.append(args[:2])
            if args[:3] == ("pactl", "list", "modules"):
                return (b"25\tmodule-bluez5-discover\t\n", b"")
            if len(args) >= 2 and args[1] == "unload-module":
                unload_seen += 1
                if unload_seen == 1:
                    # Only the first caller blocks — guarantees the second caller
                    # can't also stall the test if the in-progress guard is absent.
                    started.set()
                    await block.wait()
            return (b"", b"")

        proc.communicate = communicate
        return proc

    async def fake_sleep(_delay):
        return None

    async def runner():
        with (
            patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec),
            patch.object(asyncio, "sleep", side_effect=fake_sleep),
        ):
            task1 = asyncio.create_task(areload_bluez5_discover_module())
            await started.wait()  # first caller is mid-unload, holding in-progress
            r2 = await areload_bluez5_discover_module()  # should bail out fast
            block.set()
            r1 = await task1
            return r1, r2

    r1, r2 = asyncio.run(runner())

    assert r1 is True, "first caller should complete the reload"
    assert r2 is False, "second concurrent caller must bail out, not run another reload"
    unload_count = sum(1 for c in call_log if len(c) >= 2 and c[1] == "unload-module")
    assert unload_count == 1, f"expected exactly one unload across concurrent callers, got {unload_count}"


def test_reload_bluez5_discover_module_burns_cooldown_after_success():
    """On successful unload+load, the cooldown timestamp must be updated."""
    from sendspin_bridge.services.audio.pulse import areload_bluez5_discover_module

    _reset_bluez5_reload_cooldown()

    async def fake_create_subprocess_exec(*args, **_kwargs):
        proc = MagicMock()
        proc.returncode = 0

        async def communicate():
            if args[:3] == ("pactl", "list", "modules"):
                return (b"25\tmodule-bluez5-discover\t\n", b"")
            return (b"", b"")

        proc.communicate = communicate
        return proc

    async def fake_sleep(_delay):
        return None

    with (
        patch.object(asyncio, "create_subprocess_exec", side_effect=fake_create_subprocess_exec),
        patch.object(asyncio, "sleep", side_effect=fake_sleep),
    ):
        result = asyncio.run(areload_bluez5_discover_module())

    assert result is True
    assert _pulse_mod._LAST_BLUEZ5_RELOAD_TS > 0.0


# ---------------------------------------------------------------------------
# aread_sink_state — single-connection sink state read used by
# pa_volume_controller's subscribe loop (avoids per-event PA churn).
# ---------------------------------------------------------------------------


def test_aread_sink_state_returns_volume_and_mute_for_existing_sink():
    """When the sink is found, aread_sink_state returns (vol_pct, muted)."""

    class _FakeSink:
        name = "bluez_sink.AA_BB.a2dp_sink"

        class volume:
            value_flat = 0.42  # 42%

        mute = 1  # truthy non-bool to confirm coercion

    async def _fake_sink_list():
        return [_FakeSink()]

    pulse = MagicMock()
    pulse.sink_list = _fake_sink_list

    vol, muted = asyncio.run(_pulse_mod.aread_sink_state(pulse, "bluez_sink.AA_BB.a2dp_sink"))

    assert vol == 42
    assert muted is True


def test_aread_sink_state_returns_none_when_sink_absent():
    """Sink not present (BT disconnect, PA restart) → (None, None)."""

    async def _empty_sink_list():
        return []

    pulse = MagicMock()
    pulse.sink_list = _empty_sink_list

    vol, muted = asyncio.run(_pulse_mod.aread_sink_state(pulse, "bluez_sink.MISSING.a2dp_sink"))

    assert vol is None
    assert muted is None
