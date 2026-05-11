# Issue #269 — AVDTP collision on reconnect: implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the AVDTP-collision reconnect loop on PipeWire stacks (Sony WH-1000XM4 reporter, confirmed on diagnostics zip in issue #269 comment dated 2026-05-10).

**Architecture:** Three independent, layered fixes:

1. **Transport-state gate on the `LAST_SINKS` fast-path** in `bluetooth/audio.py` — before skipping the 3-second A2DP stabilization delay, query `org.bluez.MediaTransport1.State` for the device. Only take the fast-path when transport is `idle` / `pending` (i.e. exists but no inbound stream); if the transport is missing or `active` (collision risk), fall back to the existing delayed discovery path and poll for the transport to settle.
2. **Defer the anti-pop mute** in `services/ipc/daemon_process.py` until BlueZ reports `MediaTransport1.State == active` (or a short fallback timeout). The current immediate-mute window on connect is what causes the XM4 to send AVDTP-Suspend on silence, which then collides with PipeWire's AVDTP-Start when MA finally streams.
3. **Unmute watchdog early-bail** in `_startup_unmute_watcher()` — if the BT sink object disappears (BlueZ teardown after AVDTP failure), bail immediately rather than emitting four `sink not found` warnings spaced 2 s apart.

**Tech Stack:** Python 3.13, `dbus-python` (sync), pytest, existing `_dbus_get_device_property` pattern from `bluetooth/dbus.py`.

---

## Pre-work: shared D-Bus helper

### Task 0: Add `_dbus_get_media_transport_state()` to `bluetooth/dbus.py`

**Files:**
- Modify: `src/sendspin_bridge/bluetooth/dbus.py` (add new helper near existing `_dbus_get_device_property`)
- Test: `tests/unit/bluetooth/test_dbus_media_transport.py` (new file)

**Background.** BlueZ exposes A2DP transport state under
`/org/bluez/hciN/dev_XX_XX_XX_XX_XX_XX/sepM/fdN` with interface
`org.bluez.MediaTransport1`, property `State` (string, one of
`"idle" | "pending" | "active" | "suspending"`). The transport path
is not predictable, so we enumerate via `ObjectManager` on `/` and
filter by the `Device` property of each `MediaTransport1` object.

**Step 1: Write failing test**

```python
# tests/unit/bluetooth/test_dbus_media_transport.py
"""Tests for MediaTransport1.State lookup used by the audio fast-path."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _install_fake_dbus(monkeypatch, managed_objects):
    """Install a fake dbus module exposing the given managed-objects payload."""
    fake_dbus = types.ModuleType("dbus")

    class _Interface:
        def __init__(self, obj, iface):
            self._obj = obj
            self._iface = iface

        def GetManagedObjects(self):  # noqa: N802 (matches D-Bus name)
            return managed_objects

        def Get(self, iface, prop):
            return self._obj._props.get(iface, {}).get(prop)

    class _Object:
        def __init__(self, path, props):
            self._path = path
            self._props = props

    class _Bus:
        def __init__(self, payload):
            self._payload = payload

        def get_object(self, _service, path):
            return _Object(path, self._payload.get(path, {}))

    fake_dbus.SystemBus = lambda: _Bus(managed_objects)
    fake_dbus.Interface = _Interface
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)


def test_media_transport_state_active(monkeypatch):
    """Returns 'active' when a MediaTransport1 for the device is active."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    transport_path = f"{device_path}/sep1/fd0"
    managed = {
        transport_path: {
            "org.bluez.MediaTransport1": {
                "Device": device_path,
                "State": "active",
            }
        }
    }
    _install_fake_dbus(monkeypatch, managed)
    # Re-import to pick up the patched dbus
    import importlib
    import sendspin_bridge.bluetooth.dbus as bt_dbus
    importlib.reload(bt_dbus)

    assert bt_dbus._dbus_get_media_transport_state(device_path) == "active"


def test_media_transport_state_idle(monkeypatch):
    """Returns 'idle' when a MediaTransport1 for the device is idle."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    transport_path = f"{device_path}/sep1/fd0"
    managed = {
        transport_path: {
            "org.bluez.MediaTransport1": {
                "Device": device_path,
                "State": "idle",
            }
        }
    }
    _install_fake_dbus(monkeypatch, managed)
    import importlib
    import sendspin_bridge.bluetooth.dbus as bt_dbus
    importlib.reload(bt_dbus)

    assert bt_dbus._dbus_get_media_transport_state(device_path) == "idle"


def test_media_transport_state_no_transport(monkeypatch):
    """Returns None when no MediaTransport1 exists for the device."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    managed = {}
    _install_fake_dbus(monkeypatch, managed)
    import importlib
    import sendspin_bridge.bluetooth.dbus as bt_dbus
    importlib.reload(bt_dbus)

    assert bt_dbus._dbus_get_media_transport_state(device_path) is None


def test_media_transport_state_filters_by_device(monkeypatch):
    """Ignores MediaTransport1 objects that belong to a different device."""
    our_device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    other_device = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    managed = {
        f"{other_device}/sep1/fd0": {
            "org.bluez.MediaTransport1": {"Device": other_device, "State": "active"}
        }
    }
    _install_fake_dbus(monkeypatch, managed)
    import importlib
    import sendspin_bridge.bluetooth.dbus as bt_dbus
    importlib.reload(bt_dbus)

    assert bt_dbus._dbus_get_media_transport_state(our_device) is None


def test_media_transport_state_handles_missing_dbus(monkeypatch):
    """Returns None gracefully when dbus-python is not installed."""
    monkeypatch.setitem(sys.modules, "dbus", None)
    import importlib
    import sendspin_bridge.bluetooth.dbus as bt_dbus
    importlib.reload(bt_dbus)

    assert bt_dbus._dbus_get_media_transport_state("/org/bluez/hci0/dev_X") is None
```

Run: `uv run pytest tests/unit/bluetooth/test_dbus_media_transport.py -v`
Expected: FAIL (helper does not exist).

**Step 2: Implement helper**

Append to `src/sendspin_bridge/bluetooth/dbus.py`:

```python
def _dbus_get_media_transport_state(device_path: str | None) -> str | None:
    """Return ``MediaTransport1.State`` for *device_path* or None.

    Enumerates BlueZ ObjectManager and finds the first
    ``org.bluez.MediaTransport1`` whose ``Device`` property matches
    *device_path*. State values per BlueZ docs:
    ``"idle" | "pending" | "active" | "suspending"``.

    Returns None when:
    - dbus-python is unavailable,
    - no transport object exists for the device (peer has not negotiated
      a stream endpoint yet — common right after Connect),
    - the lookup raised a D-Bus error.
    """
    if not device_path or dbus is None:
        return None
    try:
        bus = dbus.SystemBus()
        root = bus.get_object("org.bluez", "/")
        om = dbus.Interface(root, "org.freedesktop.DBus.ObjectManager")
        managed = om.GetManagedObjects()
        for _path, ifaces in managed.items():
            transport = ifaces.get("org.bluez.MediaTransport1")
            if not transport:
                continue
            if str(transport.get("Device", "")) != device_path:
                continue
            state = transport.get("State")
            return str(state) if state is not None else None
        return None
    except Exception as exc:
        logger.debug("D-Bus MediaTransport1.State read failed: %s", exc)
        return None
```

Run: `uv run pytest tests/unit/bluetooth/test_dbus_media_transport.py -v`
Expected: PASS.

**Step 3: Commit**

```bash
git add src/sendspin_bridge/bluetooth/dbus.py tests/unit/bluetooth/test_dbus_media_transport.py
git commit -m "feat(bt): add _dbus_get_media_transport_state helper"
```

---

## Fix 1: Transport-state gate on the LAST_SINKS fast-path

### Task 1: Wire `device_path` into `configure_bluetooth_audio()`

**Files:**
- Modify: `src/sendspin_bridge/bluetooth/audio.py` (add new optional `device_path` parameter)
- Modify: `src/sendspin_bridge/bluetooth/manager.py:820-831` (pass `self._dbus_device_path`)
- Test: extend `tests/unit/bluetooth/test_bt_manager.py`

**Step 1: Write failing test (a new test that exercises the fast-path gate)**

Append to `tests/unit/bluetooth/test_bt_manager.py`:

```python
def test_fast_path_falls_back_when_transport_active(bt_manager, tmp_path, monkeypatch):
    """When LAST_SINKS has the sink but MediaTransport1.State == 'active',
    skip the fast-path and go through the delayed-discovery branch.

    Reproduces the collision window from issue #269: on reconnect, the
    PA sink object exists but BlueZ AVDTP is still settling; taking the
    fast-path mutes too early and triggers AVDTP-Suspend from the peer.
    """
    import json
    from unittest.mock import patch

    import sendspin_bridge.config as config
    import sendspin_bridge.bluetooth.audio as bt_audio

    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"

    # Seed LAST_SINKS so the fast-path would otherwise fire
    cfg_payload = {"LAST_SINKS": {bt_manager.mac_address: sink_name}}
    config.CONFIG_FILE.write_text(json.dumps(cfg_payload))

    waits: list[float] = []

    def _wait_with_cancel(sec):
        waits.append(sec)
        return True

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks",
              return_value=[{"name": sink_name, "description": "BT"}]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        # Transport-state lookup: 'active' = collision risk → bail fast-path
        patch("sendspin_bridge.bluetooth.audio._dbus_get_media_transport_state",
              return_value="active"),
        patch.object(bt_manager, "_wait_with_cancel", side_effect=_wait_with_cancel),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    # The 3-second A2DP profile delay should have been taken
    assert any(abs(w - bt_audio._A2DP_PROFILE_DELAY) < 1e-6 for w in waits), (
        f"Expected A2DP profile delay to be taken when transport is 'active', "
        f"got waits={waits!r}"
    )


def test_fast_path_taken_when_transport_idle(bt_manager, tmp_path):
    """When transport state is 'idle' (or unknown), keep the existing
    fast-path: cached sink + no delay. This protects boot-time restarts."""
    import json
    from unittest.mock import patch

    import sendspin_bridge.config as config

    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"
    cfg_payload = {"LAST_SINKS": {bt_manager.mac_address: sink_name}}
    config.CONFIG_FILE.write_text(json.dumps(cfg_payload))

    waits: list[float] = []

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=[]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch("sendspin_bridge.bluetooth.audio._dbus_get_media_transport_state",
              return_value="idle"),
        patch.object(bt_manager, "_wait_with_cancel",
                     side_effect=lambda s: (waits.append(s) or True)),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    assert waits == [], (
        f"Fast-path should not have called wait_with_cancel when transport "
        f"is idle, got waits={waits!r}"
    )
```

Run: `uv run pytest tests/unit/bluetooth/test_bt_manager.py::test_fast_path_falls_back_when_transport_active tests/unit/bluetooth/test_bt_manager.py::test_fast_path_taken_when_transport_idle -v`
Expected: FAIL (no `_dbus_get_media_transport_state` import in audio.py yet).

**Step 2: Implement gate**

Modify `src/sendspin_bridge/bluetooth/audio.py` — add import and gate the fast-path:

```python
# Top of file, with other imports:
from sendspin_bridge.bluetooth.dbus import _dbus_get_media_transport_state
```

Change `configure_bluetooth_audio()` signature:

```python
def configure_bluetooth_audio(
    mac_address: str,
    prefer_sbc: bool,
    on_sink_found: Callable[[str, int | None], None] | None,
    host: BluetoothManagerHost | None,
    wait_with_cancel: Callable[[float], bool],
    *,
    device_path: str | None = None,
    logger: logging.Logger = logger,
) -> bool:
```

Replace the fast-path block (lines 181–184) with:

```python
        if cached_sink and get_sink_volume(cached_sink) is not None:
            # Even if the PA sink exists, BlueZ AVDTP may still be settling.
            # MediaTransport1.State == "active" means an inbound stream is
            # already running on the peer side; taking the fast-path here
            # races with anti-pop mute → AVDTP-Suspend from peer →
            # cancel_request collision (issue #269).
            transport_state = _dbus_get_media_transport_state(device_path)
            if transport_state == "active":
                logger.info(
                    "Cached sink %s exists but MediaTransport1 is active — "
                    "skipping fast-path to avoid AVDTP collision",
                    cached_sink,
                )
                # Fall through to the delayed-discovery branch below
            else:
                logger.info(
                    "✓ Using cached sink: %s (skipped A2DP delay, transport=%s)",
                    cached_sink,
                    transport_state or "unknown",
                )
                configured_sink = cached_sink
                success = True
```

Restructure the surrounding `if/else` so the "fast-path declined" path
also enters the discovery branch (concretely: wrap the `else:` block at
old line 185 + the cached-sink-success block in an `if not success` so
the discovery code runs when the gate rejected the fast-path).

Modify `src/sendspin_bridge/bluetooth/manager.py:824-831`:

```python
        return bt_audio.configure_bluetooth_audio(
            mac_address=self.mac_address,
            prefer_sbc=self.prefer_sbc,
            on_sink_found=self.on_sink_found,
            host=self.host,
            wait_with_cancel=self._wait_with_cancel,
            device_path=self._dbus_device_path,
            logger=logger,
        )
```

Run: `uv run pytest tests/unit/bluetooth/test_bt_manager.py -v -k "fast_path or configure_bluetooth_audio"`
Expected: PASS, including the two new tests and the four pre-existing ones.

**Step 3: Commit**

```bash
git add src/sendspin_bridge/bluetooth/audio.py src/sendspin_bridge/bluetooth/manager.py tests/unit/bluetooth/test_bt_manager.py
git commit -m "fix(bt): gate LAST_SINKS fast-path on MediaTransport1.State (#269)"
```

---

## Fix 2: Defer anti-pop mute until transport is active

### Task 2: AVDTP-ready gate before muting in daemon

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py:735-748`
- Test: extend `tests/unit/services/ipc/` (new test file)

**Background.** Today the daemon mutes the BT sink immediately on
startup. On stacks where MA streaming starts ~10 s later (issue #269
RPi 4 + BlueZ 5.66 + PipeWire), the XM4 sees silence on a half-formed
AVDTP transport and proactively sends AVDTP-Suspend, which then
collides with PipeWire's AVDTP-Start.

The cleanest fix: only mute when an AVDTP transport actually exists and
is in `pending` / `active` (i.e. about to carry or already carrying
audio). For headsets that never bring the transport up until the first
sample arrives, fall back to muting after a short wait (1.5 s).

**Step 1: Write failing test**

```python
# tests/unit/services/ipc/test_daemon_startup_mute_gate.py
"""Tests for the AVDTP-ready gate on daemon startup mute (issue #269)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_startup_mute_waits_for_transport_active(monkeypatch):
    """Mute should fire only after MediaTransport1 reaches 'pending'/'active'
    (or after a 1.5 s safety timeout). Prevents the XM4 AVDTP-Suspend
    collision documented in issue #269."""
    from sendspin_bridge.services.ipc import daemon_process

    states = iter(["idle", "idle", "pending"])
    seen_at_mute = []

    async def _fake_mute(sink, mute):
        # Snapshot transport state at the moment mute is called
        seen_at_mute.append(daemon_process._last_state_for_test)
        return True

    def _fake_state(device_path):
        try:
            s = next(states)
        except StopIteration:
            s = "active"
        daemon_process._last_state_for_test = s
        return s

    with (
        patch.object(daemon_process, "_dbus_get_media_transport_state",
                     side_effect=_fake_state),
    ):
        ok = await daemon_process._mute_when_transport_ready(
            sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
            device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            do_mute=_fake_mute,
            poll_interval=0.0,
            timeout=2.0,
        )

    assert ok is True
    assert seen_at_mute == ["pending"], (
        f"Mute should fire on first non-idle state, got {seen_at_mute!r}"
    )


@pytest.mark.asyncio
async def test_startup_mute_fires_after_timeout_if_transport_never_appears():
    """If MediaTransport1 never appears (peer doesn't open a stream
    endpoint), mute anyway after the safety timeout — better than never
    muting, since the cost is at worst one click."""
    from sendspin_bridge.services.ipc import daemon_process

    muted = []

    async def _fake_mute(sink, mute):
        muted.append(mute)
        return True

    with patch.object(daemon_process, "_dbus_get_media_transport_state",
                      return_value=None):
        ok = await daemon_process._mute_when_transport_ready(
            sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
            device_path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            do_mute=_fake_mute,
            poll_interval=0.05,
            timeout=0.15,
        )

    assert ok is True
    assert muted == [True], f"Expected one mute call on timeout, got {muted!r}"
```

Run: `uv run pytest tests/unit/services/ipc/test_daemon_startup_mute_gate.py -v`
Expected: FAIL (helper does not exist).

**Step 2: Implement helper + wire into startup path**

Add to `src/sendspin_bridge/services/ipc/daemon_process.py`:

```python
# Near top with other imports
from sendspin_bridge.bluetooth.dbus import _dbus_get_media_transport_state


async def _mute_when_transport_ready(
    *,
    sink_name: str,
    device_path: str | None,
    do_mute,  # async callable: (sink_name, True) -> bool
    poll_interval: float = 0.2,
    timeout: float = 1.5,
) -> bool:
    """Mute the BT sink only once BlueZ AVDTP transport is non-idle.

    Issue #269: when the daemon mutes immediately on startup, certain
    A2DP sinks (Sony WH-1000XM4 confirmed) interpret the silence as
    "no inbound stream" and proactively send AVDTP-Suspend. If MA then
    starts streaming, PipeWire's AVDTP-Start races with the Suspend and
    bluetoothd reports ``cancel_request() Start: Operation canceled``.

    We delay the mute until ``MediaTransport1.State`` is
    ``"pending"`` / ``"active"`` (the peer has opened or is opening the
    stream endpoint), or until ``timeout`` elapses — whichever is first.
    """
    deadline = asyncio.get_event_loop().time() + max(0.0, timeout)
    while True:
        state = _dbus_get_media_transport_state(device_path) if device_path else None
        if state in ("pending", "active"):
            return await do_mute(sink_name, True)
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return await do_mute(sink_name, True)
        await asyncio.sleep(min(poll_interval, remaining))
```

Change the existing mute block (currently lines 735–748) to call the
new helper instead of muting unconditionally. The block becomes:

```python
    # Mute the PA sink to hide re-anchor clicks and routing glitches.
    # Wait until AVDTP transport is non-idle before muting (issue #269):
    # some A2DP sinks (XM4) treat silence on a half-formed transport as
    # "drop the stream" and send AVDTP-Suspend, racing with PipeWire's
    # subsequent Start when MA finally connects.
    _startup_muted = False
    bluetooth_device_path: str | None = params.get("bluetooth_device_path")
    if bluetooth_sink_name:
        try:
            from sendspin_bridge.services.audio.pulse import aset_sink_mute

            ok = await _mute_when_transport_ready(
                sink_name=bluetooth_sink_name,
                device_path=bluetooth_device_path,
                do_mute=aset_sink_mute,
            )
            if ok:
                _startup_muted = True
                status["sink_muted"] = True
                logger.info("[%s] Muted sink %s during startup", player_name, bluetooth_sink_name)
                _on_status_change()
        except Exception as exc:
            logger.debug("[%s] Could not mute sink on startup: %s", player_name, exc)
```

Also: the parent must now pass `bluetooth_device_path` in the daemon
spawn payload. Find the spawn helper (likely `_start_sendspin_inner`
or its delegate in `sendspin_client.py`) and add the field. Use
`self.bt_manager._dbus_device_path` as the source.

Run: `uv run pytest tests/unit/services/ipc/test_daemon_startup_mute_gate.py -v`
Expected: PASS.

Also run: `uv run pytest tests/unit/bridge/ tests/unit/services/ipc/ -q`
Expected: no regressions.

**Step 3: Commit**

```bash
git add src/sendspin_bridge/services/ipc/daemon_process.py \
        src/sendspin_bridge/bridge/client.py \
        tests/unit/services/ipc/test_daemon_startup_mute_gate.py
git commit -m "fix(daemon): defer anti-pop mute until AVDTP transport is non-idle (#269)"
```

---

## Fix 3: Unmute watchdog early-bail on sink gone

### Task 3: Skip retry storm when sink has been torn down

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py:341-362`
- Test: extend `tests/unit/services/ipc/` (same new file or sibling)

**Background.** After every failed reconnect cycle in the reporter's
log, the unmute watchdog hits the 15 s timeout, then makes 4 attempts
to unmute a sink that no longer exists (BlueZ tore down the BT
endpoint). Each attempt logs `aset_sink_mute: sink ... not found` —
8 s of noise plus a held subprocess.

The fix: if the first unmute returns False AND the sink is not visible
in `list_sinks()`, bail immediately without the 3 × 2 s retries.

**Step 1: Write failing test**

Append to `tests/unit/services/ipc/test_daemon_startup_mute_gate.py`:

```python
@pytest.mark.asyncio
async def test_unmute_watchdog_bails_when_sink_gone(monkeypatch):
    """When the sink object has disappeared (BlueZ teardown after AVDTP
    failure), the unmute watchdog must bail after the first failed
    aset_sink_mute call rather than spending 8 s on doomed retries
    (issue #269)."""
    import asyncio
    from sendspin_bridge.services.ipc import daemon_process

    status = {}
    stop_event = asyncio.Event()
    stop_event.set()  # short-circuit the streaming wait

    mute_calls = []

    async def _fake_mute(sink, mute):
        mute_calls.append(sink)
        return False  # always fail — sink is gone

    def _fake_list_sinks():
        return []  # sink no longer in the system

    with (
        patch("sendspin_bridge.services.audio.pulse.aset_sink_mute",
              side_effect=_fake_mute),
        patch("sendspin_bridge.services.audio.pulse.list_sinks",
              side_effect=_fake_list_sinks),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
              AsyncMock(return_value=0)),
    ):
        # Allow the streaming-wait loop to break immediately
        stop_event.clear()
        # Trip the post-wait branch via timeout: set a tiny deadline
        # by patching time.monotonic? Simpler: run watcher with the
        # deadline already past.
        monkeypatch.setattr("time.monotonic", lambda: 1e12)
        await daemon_process._startup_unmute_watcher(
            status=status,
            sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
            stop_event=stop_event,
            player_name="TEST",
            on_status_change=None,
        )

    # Exactly one mute attempt — no retry storm
    assert len(mute_calls) == 1, (
        f"Expected single unmute attempt when sink gone, got {len(mute_calls)}"
    )
```

Run: `uv run pytest tests/unit/services/ipc/test_daemon_startup_mute_gate.py::test_unmute_watchdog_bails_when_sink_gone -v`
Expected: FAIL (current code retries 4 times).

**Step 2: Implement early-bail**

Modify `_startup_unmute_watcher()` in `daemon_process.py:341-362`:

```python
    try:
        ok = await aset_sink_mute(sink_name, False)
        if not ok:
            # Issue #269: if the sink itself is gone (BT teardown after
            # AVDTP failure), retries are guaranteed to fail. Check once
            # before burning 6s on retries.
            from sendspin_bridge.services.audio.pulse import list_sinks
            sink_present = any(s.get("name") == sink_name for s in list_sinks())
            if not sink_present:
                _logger.info(
                    "[%s] Sink %s no longer present, skipping unmute retries",
                    player_name, sink_name,
                )
                return
            for retry in range(1, 4):
                _logger.info("[%s] Unmute retry %d/3 for %s", player_name, retry, sink_name)
                await asyncio.sleep(2)
                ok = await aset_sink_mute(sink_name, False)
                if ok:
                    break
        # ... rest unchanged
```

Run: `uv run pytest tests/unit/services/ipc/test_daemon_startup_mute_gate.py -v`
Expected: PASS for all tests in the file.

**Step 3: Commit**

```bash
git add src/sendspin_bridge/services/ipc/daemon_process.py \
        tests/unit/services/ipc/test_daemon_startup_mute_gate.py
git commit -m "fix(daemon): bail unmute retries when BT sink already torn down (#269)"
```

---

## Wrap-up

### Task 4: CHANGELOG + lint

**Files:**
- Modify: `CHANGELOG.md` (add three entries under `[Unreleased]/### Fixed`)

**Step 1: Add entries**

Under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### Fixed
- **AVDTP-collision reconnect loop on Sony WH-1000XM4 and similar A2DP sinks no longer pins reconnect attempts in a permanent loop on PipeWire stacks.** When the cached sink name was still valid after a Bluetooth reconnect, the bridge took a fast-path that skipped the 3-second A2DP stabilization delay even though BlueZ's underlying AVDTP transport hadn't finished settling. With certain peers the anti-pop sink mute then raced with the peer's own AVDTP-Suspend, producing a cluster of `cancel_request() Start: Operation canceled` and `SEP in bad state` errors in `bluetoothd` and an immediate disconnect ~14 seconds in. The bridge now queries `org.bluez.MediaTransport1.State` before taking the fast-path and only does so when the transport is `idle` or unknown; an `active` transport falls back to the regular delayed-discovery path. ([#269](https://github.com/trudenboy/sendspin-bt-bridge/issues/269))
- **The anti-pop sink mute applied during daemon startup is now deferred until the Bluetooth peer has opened (or is opening) its A2DP stream endpoint.** Sony WH-1000XM4 and a few other A2DP sinks treat silence on a half-formed transport as "no inbound stream" and proactively send AVDTP-Suspend; if Music Assistant subsequently starts streaming, PipeWire's AVDTP-Start collides with that Suspend. The daemon now polls `MediaTransport1.State` for up to 1.5 seconds and only mutes when state is `pending` or `active`, with a safety fallback that mutes anyway on timeout. ([#269](https://github.com/trudenboy/sendspin-bt-bridge/issues/269))
- **The startup unmute watchdog no longer burns 8 seconds on doomed retries after the BT sink has already been torn down.** Previously, every failed reconnect produced four `aset_sink_mute: sink ... not found` warnings spaced 2 seconds apart. The watchdog now probes the sink list once after the first failure and short-circuits the retry loop when the sink has disappeared. ([#269](https://github.com/trudenboy/sendspin-bt-bridge/issues/269))
```

**Step 2: Lint changelog**

Run: `uv run python scripts/lint_changelog.py CHANGELOG.md`
Expected: `CHANGELOG.md: clean`.

**Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS.

**Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note AVDTP-collision reconnect fixes for issue #269"
git push origin main
```

---

## Risk + roll-back notes

- **Risk 1: D-Bus enumeration cost.** `GetManagedObjects()` on a busy
  BlueZ instance can return a sizeable payload. We call it once per
  reconnect, so cost is bounded. If profiling shows hot-path
  regressions, switch to a directed `GetAll` on the device path's
  `MediaTransport1` interface once we discover the right `sepN/fdM`
  path.
- **Risk 2: `device_path` is None.** On adapters without an `hciN`
  alias (extreme LXC cases) `_dbus_device_path` is None. In that case
  the gate is silently skipped (existing fast-path behaviour preserved
  for stacks where we can't introspect transport state).
- **Risk 3: Anti-pop pop returns.** If the mute is deferred and a peer
  sends a pop before the transport reaches `pending`, the user hears
  a click. Acceptable trade-off: today the alternative is no audio at
  all from the affected stacks.
- **Roll-back:** All three commits are independent and revertable.
  Reverting the daemon-mute commit is the lowest-impact: it only
  changes timing of an existing operation, no schema or IPC envelope
  changes.
