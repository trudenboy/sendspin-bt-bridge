---
title: 2026-03-24 — Security audit and IPC hardening
description: March 24 delivered a comprehensive security audit (session fixation, MAC validation, SSRF, OOM), IPC buffer hardening, SSE debouncing, PulseAudio sink auto-correction, and major module splits across v2.47.0 through v2.47.3
---

March 24 was a security and reliability day. Four stable releases in rapid succession addressed a comprehensive security audit, IPC buffer limits, SSE event storms during playback, and a long-standing PulseAudio sink-routing race condition. The runtime also received its largest module split since the orchestrator extraction.

## What shipped

### Security audit — eight fixes in v2.47.0

A systematic security review produced eight fixes that shipped together:

- **Session fixation** — the session is now cleared before setting authenticated state, preventing an attacker from pre-setting a session cookie and inheriting the operator's login.
- **MAC address validation** — `BluetoothManager.__init__` now validates MAC format to block `bluetoothctl` command injection through crafted device addresses.
- **SSRF protection** — artwork proxy requests are rejected for non-MA-origin URLs, and `ha_url` parameters in HA Core API calls are guarded against SSRF.
- **OOM prevention** — artwork proxy reads are capped at 10 MB to prevent out-of-memory crashes from malicious upstream responses.
- **Action parameter whitelist** — pause/play endpoints validate the `action` parameter before IPC dispatch.
- **Update tag validation** — character whitelist on update tag refs prevents path traversal or injection through crafted version strings.
- **Auth flag in diagnostics** — diagnostics endpoints now include an `auth_enabled` flag and warning when authentication is disabled, so operators and automated scanners can detect unprotected instances.

### Sendspin and volume controller upgrades

The security release also upgraded the core audio dependency from sendspin 5.3.2 to 5.7.1, bringing upstream fixes for volume reset on reconnect, pitch shift on format change, and server/hello ordering. A new `PulseVolumeController` implements the sendspin `VolumeController` protocol for direct PulseAudio/PipeWire sink control via `pulsectl`, and `BridgeDaemon` skips manual sink sync when the upstream library handles volume natively.

### Major module splits

Three of the project's largest modules were split to reduce complexity:

- `bluetooth_manager.py` (1,226 → 669 lines) — extracted `bt_audio.py` (sink discovery), `bt_monitor.py` (polling and D-Bus loops), `bt_dbus.py` (D-Bus helpers)
- `routes/api_ma.py` (2,343 → 150 lines) — extracted `routes/ma_auth.py`, `routes/ma_playback.py`, `routes/ma_groups.py`
- `config.py` (999 → 449 lines) — extracted `config_auth.py`, `config_migration.py`, `config_network.py`

All public APIs and re-exports were preserved for backward compatibility. The splits made each module small enough to reason about independently.

### BluetoothManager decoupling

`BluetoothManager` was decoupled from `SendspinClient` through a new `bt_types.BluetoothManagerHost` Protocol. This broke a circular dependency that had been making testing and module extraction increasingly painful.

### IPC artwork buffer fix (v2.47.1)

A crash surfaced when artwork binary frames exceeded asyncio's default 64 KB readline buffer: `Separator is found, but chunk is longer than limit`. The fix raised the subprocess stdout buffer to 1 MB and capped artwork frames at 48 KB raw to stay within the IPC line budget.

### SSE frame filtering (v2.47.2)

During playback with visualizer and artwork roles active, SSE status notifications were firing many times per second — fast enough to close modals and popups in the web UI. Visualizer frames no longer trigger status notifications at all, and artwork frames only notify when the image content actually changes.

### PulseAudio rescue-streams auto-correction (v2.47.3)

When a new Bluetooth device connects, PulseAudio's `module-rescue-streams` can silently move an existing stream to the newly-appeared sink. This meant speaker A's audio could suddenly start playing through speaker B after speaker B reconnected.

The bridge now detects this within 3 seconds and moves streams back to their correct sinks. A new `get_subprocess_pid()` method on the `BluetoothManagerHost` protocol enables safe PID-based stream identification for the correction.

### Concurrency and reliability fixes

The release wave also addressed a substantial list of concurrency issues: a race in volume tracking (atomic read-compare-update under a single lock), TOCTOU in `build_device_snapshot` (new atomic `snapshot()` method), non-atomic list swap in `state.py` (slice assignment instead of `clear()` + `extend()`), task leaks on `CancelledError` in the MA monitor, and `asyncio.run()` usage in WSGI threads replaced with `ThreadPoolExecutor`.

### Test coverage expansion

Sixty-nine new tests shipped across the security and reliability fixes: thread-safety tests for concurrent status/config/notification operations, auth enforcement regression tests, IPC protocol integration tests, error-path tests for malformed IPC and invalid inputs, and two new test files for the PulseAudio volume controller and bridge daemon features.

## Why this matters

The security audit was overdue. The bridge runs on home networks with varying levels of isolation, and several of the fixed issues — session fixation, SSRF, MAC injection — could have been exploited on misconfigured networks. Shipping all eight fixes in a single stable release made the upgrade path clean.

The IPC and SSE fixes addressed the two most disruptive runtime issues reported by operators: crashes during artwork-heavy playback and the UI becoming unusable when modals kept closing. The PulseAudio sink correction fixed a problem that had been causing confused audio routing since the project first supported multiple simultaneous speakers.

## Follow-up

With the security and IPC layers hardened, the project moved to native Sendspin transport commands and the Phase 2 null-sink standby system that required a reliable IPC and sink-routing foundation.
