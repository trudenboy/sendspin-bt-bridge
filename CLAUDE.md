# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Dockerized Python client that bridges Music Assistant's Sendspin protocol to Bluetooth speakers, with a Flask web UI for configuration and status monitoring. Designed for headless systems (Raspberry Pi, Home Assistant, Proxmox LXC, OpenWrt LXC).

## Development Commands

```bash
# Build and run locally
docker compose up --build

# Run without Docker (requires system Bluetooth/audio packages)
pip install -r requirements.txt
python sendspin_client.py

# View container logs
docker logs -f sendspin-client

# Bluetooth troubleshooting inside container
docker exec -it sendspin-client bluetoothctl
```

Unit tests: `pytest` (see `tests/`). 965+ tests across 68+ files. Manual testing via `docker logs` and the web UI at `http://localhost:8080`.

CI/CD: The `VERSION` file is the single source of truth. Pushing a VERSION change to `main` triggers `release.yml` which runs lint+pytest, updates `config.py`, creates the git tag, builds Docker images (amd64+arm64), syncs HA addon directories, creates a GitHub Release (stable only), and builds armv7 (stable only). Regular development pushes and PRs trigger `ci.yml` (lint+test only).

## Test-Driven Development (TDD)

Use **red/green TDD** for all new features and bug fixes:

1. **Red**: Write the test first. Confirm it **fails** before writing implementation.
2. **Green**: Write the **minimal** code to make the test pass.
3. **Refactor**: Clean up the implementation while keeping tests green.

### Rules for AI agents

- **Never modify existing tests to make them pass.** If a test fails, fix the implementation. If a test is genuinely wrong, explain the reason in the commit message before changing it.
- **Never write tautological tests** — tests that reimplement the logic under test locally and assert on that local copy. Always call the real function/method.
- **Test real behavior, not mocks.** Avoid over-mocking: if a test only verifies that mocks return what they were configured to return, it tests nothing.
- **Every test must be able to fail.** If removing the implementation doesn't break the test, the test is useless.
- When fixing a bug, first write a test that **reproduces** the bug (red), then fix it (green).

## Local Demo Workflow

- Run no more than one local demo instance at a time.
- Start the demo from the current repository directory so it always picks up live code and template changes.
- When restarting the demo, inspect the process first instead of assuming a busy port belongs to something else.
- After sending `kill`, verify that the exact PID you targeted is actually gone before starting a replacement.
- Account for OS-specific command syntax and behavior when managing the demo process, especially on macOS.

## Architecture (v2.50.0)

**Subprocess isolation**: each Bluetooth speaker runs as a dedicated Python subprocess (`services/daemon_process.py`) with `PULSE_SINK=<bt_sink_name>` in env. This gives every speaker its own PulseAudio context → correct audio routing from the first sample, no `move-sink-input` needed.

```
main process (Flask API, BT manager, web UI)
    ├── asyncio subprocess (PULSE_SINK=bluez_sink.AA_BB...) → daemon_process.py
    ├── asyncio subprocess (PULSE_SINK=bluez_sink.CC_DD...) → daemon_process.py
    └── ...
```

IPC: subprocess→parent via JSON lines on stdout; parent→subprocess via JSON lines on stdin (`set_volume`, `set_mute`, `stop`, `reconnect`, `set_log_level`, `transport`, `set_standby`).

**`sendspin_client.py`** — core orchestration per device:
- `DeviceStatus` — `@dataclass` typed per-device status; dict-compatible (`["key"]`, `.get()`, `.update()`, `.copy()`)
- `SendspinClient` — manages the per-device subprocess lifecycle. Spawns `services/daemon_process.py` with correct `PULSE_SINK` env var. Reads JSON status from subprocess stdout, sends volume/stop commands via stdin.
- `_status_lock = threading.Lock()` + `_update_status(updates)` — thread-safe status mutation from asyncio loop, D-Bus callback thread, and Flask WSGI threads; calls `notify_status_changed()` after each mutation
- `idle_mode` — per-device enum (`default`|`power_save`|`auto_disconnect`|`keep_alive`). Dispatches idle behavior: power_save suspends PA sink, auto_disconnect enters standby, keep_alive sends 2 Hz infrasound bursts
- `_start_power_save_timer()` / `_enter_power_save()` / `_exit_power_save()` — PA sink suspend/resume for power_save mode
- `_start_sendspin_inner()` — subprocess spawn with `PULSE_SINK` and JSON args
- `stop_sendspin()` — graceful stop: sends `{"cmd":"stop"}` to stdin, kills if timeout
- `_read_subprocess_output()` — async task: forwards log lines, detects volume changes, calls `save_device_volume()`
- `_read_subprocess_stderr()` — async task: forwards subprocess stderr to `logger.warning`
- `main()` — loads config, instantiates `BluetoothManager` + `SendspinClient` per device, starts Waitress server in daemon thread, runs async event loop

**`bluetooth_manager.py`** — BT connection management:
- `BluetoothManager` — pairing/connection via `bluetoothctl`. Auto-reconnects every 10 s. Detects PipeWire (`bluez_output.MAC.1`) and PulseAudio (`bluez_sink.MAC.a2dp_sink`) sinks.
- `configure_bluetooth_audio()` — finds the correct PulseAudio sink name for the connected device

**`config.py`** — configuration layer:
- `CONFIG_FILE: Path` — single source of truth for config path (replaces old `_CONFIG_PATH` string)
- `config_lock` (threading.RLock shared across modules)
- `load_config()`, `_player_id_from_mac()`, `save_device_volume()`, `save_device_sink()`, `update_config()`, `write_config_file()`, `migrate_config_payload()`
- `ensure_bridge_name()` — auto-populates BRIDGE_NAME from hostname on first startup
- `ensure_secret_key()` — generates and persists SECRET_KEY if absent
- `get_runtime_version()` — returns version from `.release-ref` file or falls back to `VERSION`
- `VERSION = "2.50.0"`, `BUILD_DATE = "2025-07-18"` — set by CI from `VERSION` file
- Re-exports from `config_auth.py` (password hashing), `config_migration.py` (schema migration, normalization), `config_network.py` (port resolution, HA addon detection)

**`services/` module (core):**
- `bridge_daemon.py` — `BridgeDaemon` subclass. Runs inside each subprocess. Handles `on_status_change` callbacks, stream events. `_sink_routed` flag prevents re-anchor feedback loop after PA rescue-streams correction.
- `daemon_process.py` — subprocess entry point. Reads JSON args from argv, sets up `BridgeDaemon`, emits status as JSON to stdout, reads commands from stdin. IPC commands: `set_volume`, `set_mute`, `stop`, `reconnect`, `set_log_level`, `transport`, `set_standby`. `_startup_unmute_watcher` accepts `on_status_change` callback and calls it after unmuting; startup unmute timeout is 15 s.
- `bluetooth.py` — BT helpers: `bt_remove_device()`, `persist_device_enabled()`, `persist_device_released()` (sync to config.json + options.json), `is_audio_device()`, `_match_player_name()`
- `pulse.py` — PulseAudio async helpers: `afind_sink_for_mac()`, `amove_pid_sink_inputs()` (corrects streams after PA module-rescue-streams moves them on BT reconnect), `_PULSECTL_AVAILABLE` flag
- `pa_volume_controller.py` — PulseAudio/PipeWire volume controller implementing the sendspin VolumeController protocol

**`services/` module (IPC & subprocess):**
- `ipc_protocol.py` — versioned IPC contract helpers for parent↔daemon message envelopes (status, log, error, command)
- `subprocess_command.py` — serializes daemon stdin commands as JSON envelopes with protocol versioning
- `subprocess_ipc.py` — parses daemon stdout JSON-line messages, dispatches status/error/log envelopes
- `subprocess_stderr.py` — classifies daemon stderr severity and mirrors crash-like output into device status
- `subprocess_stop.py` — handles reader-task cancellation and graceful daemon stop/kill flow with configurable timeouts

**`services/` module (Music Assistant):**
- `ma_discovery.py` — mDNS-based Music Assistant server discovery
- `ma_client.py` — MA REST API helpers: `discover_ma_groups()`, `_fetch_all_players()`, `_normalize_ma_url()`, `ma_group_play()`
- `ma_monitor.py` — `MaMonitor` class: persistent WebSocket connection to MA for real-time now-playing, queue state, and transport control
- `ma_artwork.py` — HMAC-signed artwork proxy URL builders for safe same-origin MA image access
- `ma_integration_service.py` — bootstrap helper that resolves MA credentials, discovers groups, and starts the async monitor task
- `ma_runtime_state.py` — MA state owner: API credentials, syncgroup mappings, now-playing cache with pending-op tracking

**`services/` module (device & bridge state):**
- `device_health_state.py` — computes device health state (ready/streaming/offline/degraded) and capability availability with remediation actions
- `device_registry.py` — canonical thread-safe inventory service for active clients and disabled devices with listener callbacks
- `bridge_runtime_state.py` — central publisher for bridge startup progress, runtime mode, and status-change notifications
- `bridge_state_model.py` — normalized dataclass models for runtime substrate, config, and per-device state shared across API surfaces
- `lifecycle_state.py` — publishes bridge-wide lifecycle events (startup, shutdown, client changes) into the shared state store
- `status_event_builder.py` — pure builder deriving meaningful device events from status-transition deltas
- `status_snapshot.py` — read-side snapshot models normalizing bridge/device status for API routes
- `playback_health.py` — tracks playback watchdog state (zombie timeouts, restart counts, streaming status)

**`services/` module (diagnostics & guidance):**
- `recovery_assistant.py` — recovery-oriented diagnostics helpers identifying device issues and building actionable recovery guidance
- `recovery_timeline.py` — chronological event timeline builder from startup progress and device events for diagnostics/CSV export
- `operator_guidance.py` — unified guidance builder combining onboarding, capability, and recovery data with visibility and grace periods
- `operator_check_runner.py` — safe, rerunnable operator checks (runtime access, Bluetooth, audio, sink verification, MA auth)
- `onboarding_assistant.py` — operator-facing onboarding checklist generator with phases (foundation, first speaker, MA, tuning)
- `guidance_issue_registry.py` — metadata registry for machine-readable operator guidance issues with priority, severity, and remediation codes
- `preflight_status.py` — collects runtime diagnostics (audio backend, BT controller, D-Bus, memory) for health checks and reports
- `log_analysis.py` — classifies log severity, detects issue-worthy lines from daemon stderr, and summarizes problem logs

**`services/` module (infrastructure):**
- `update_checker.py` — background version polling: `run_update_checker()`, `check_latest_version()`, auto-update support
- `adapter_names.py` — thread-safe cache for Bluetooth adapter MAC→friendly name lookups
- `async_job_state.py` — manages in-process state for long-running async jobs (MA discovery, scan, updates) with TTL eviction
- `config_validation.py` — validates and normalizes uploaded config payloads including device MACs, ports, handoff modes
- `duplicate_device_check.py` — cross-bridge duplicate device detection via MA API to prevent disconnect/reconnect loops
- `event_hooks.py` — runtime-scoped webhook registry with delivery history and host validation
- `internal_events.py` — lightweight pub/sub for typed internal runtime events (connections, playback, errors)
- `ha_addon.py` — Home Assistant Supervisor integration for addon detection, delivery channels, and MA discovery candidates
- `ha_core_api.py` — WebSocket client for fetching HA device/area registry data and deriving adapter→area suggestions
- `sendspin_compat.py` — runtime dependency version inspection and sendspin audio API compatibility analysis
- `_helpers.py` — shared helpers for device state extraction and ISO-8601 timestamp parsing

**`routes/` module (Flask blueprints):**
- `api.py` — core volume/mute/pause/restart endpoints, `_schedule_volume_persist()` (1 s debounce)
- `api_bt.py` — BT scan/pair/remove/reconnect/enable/disable/device/enabled/scan/result. `_get_bt_device_info()` helper. ANSI stripping in adapter power success detection.
- `api_transport.py` — POST `/api/transport/cmd` endpoint for native Sendspin transport commands (play/pause/volume/etc.) with lower latency than MA REST
- `api_ma.py` — MA integration, OAuth sign-in, groups/nowplaying/queue control
- `api_config.py` — configuration CRUD, adapter management, logs/download, update/check, update/info, update/apply, config/download, config/upload
- `api_status.py` — status/diagnostics/version/logs/diagnostics/download/bugreport. `_collect_bt_device_info()` helper for bugreport BT device info.
- `ma_auth.py` — MA OAuth/token routes (`/api/ma/login`, `/api/ma/ha-*`) and helpers for secure token exchange and HA integration
- `ma_groups.py` — MA discovery and groups routes (`/api/ma/discover*`, `/api/ma/groups`, `/api/ma/rediscover*`, `/api/ma/reload`, `/api/debug/ma`)
- `ma_playback.py` — MA playback control routes (`/api/ma/queue/*`, `/api/ma/nowplaying`, `/api/ma/artwork`) and queue command helpers
- `views.py` — HTML page renders
- `auth.py` — optional web UI password protection (PBKDF2-SHA256); HA login_flow with 2FA/TOTP support; brute-force lockout (5 attempts / 5 min)
- `_helpers.py` — shared route helpers for MAC/adapter validation and device lookup by player_name

**`state.py`** — shared runtime state:
- List of `SendspinClient` instances + global lock
- `notify_status_changed()` — SSE signaling (increments version, wakes threading.Condition)
- `_adapter_cache_lock` — double-checked locking in `get_adapter_name()`
- `create_scan_job()` / `get_scan_job()` / `finish_scan_job()` — storage for async BT-scan jobs (TTL 2 min)
- `set_ma_groups()` / `set_ma_now_playing_for_group()` — MA sync group and now-playing cache
- Batched SSE notifications (100 ms debounce window) to prevent event storms

**`scripts/translate_ha_config.py`** — called from `entrypoint.sh` in HA addon mode:
- Converts `/data/options.json` → `/data/config.json` with full field typing
- `_detect_adapters()` — enumerates BT controllers via `bluetoothctl list`
- `_merge_adapters()` — merges user-supplied names with detected hardware
- Preserves runtime state from previous config (LAST_VOLUMES, AUTH_PASSWORD_HASH, SECRET_KEY)

**Config persistence:** `/config/config.json` (mounted Docker volume at `/etc/docker/Sendspin`). Changes via the web UI require a container restart to take effect. See `config.schema.json` for the machine-readable JSON Schema describing all fields, types, and constraints.

**`static/app.js`** — frontend logic: `_showBtInfoModal()` (BT device info modal), `rebootAdapter()` (adapter power cycle), `_startScanCooldown()` (scan button cooldown timer), `uploadConfig()` (config file upload).

**Docs site:** `docs-site/` — Astro Starlight, deployed to GitHub Pages at `https://trudenboy.github.io/sendspin-bt-bridge`

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENDSPIN_SERVER` | `auto` | MA server hostname/IP; `auto` uses mDNS discovery |
| `SENDSPIN_PORT` | `9000` | WebSocket port |
| `WEB_PORT` | `8080` | Web interface port |
| `TZ` | `Australia/Melbourne` | Container timezone |
| `CONFIG_DIR` | `/config` | Config directory path |
| `LOG_LEVEL` | `INFO` | Root logger level (`INFO` or `DEBUG`); set via HA addon option or web UI |
| `BASE_LISTEN_PORT` | (auto) | Override per-device Sendspin listener base port |
| `BRIDGE_NAME` | (hostname) | Override bridge name; `auto`/`hostname` resolved to machine hostname |
| `WEB_THREADS` | `8` | Waitress worker thread count |
| `PULSE_SINK` | (per-subprocess) | PulseAudio sink name; set automatically per daemon subprocess |
| `SUPERVISOR_TOKEN` | — | Presence indicates HA addon runtime (set by HA Supervisor) |
| `SENDSPIN_STATIC_DELAY_MS` | `-300` | Static audio delay in ms passed to daemon subprocess |
| `SENDSPIN_VERSION_REF_FILE` | `/opt/sendspin-client/.release-ref` | Path to persisted install/update version ref |

## Container Requirements

- Network mode: `host` (required for mDNS auto-discovery)
- Privileged mode + capabilities `NET_ADMIN`, `NET_RAW`, `SYS_ADMIN` (required for Bluetooth)
- Volume mounts: D-Bus socket, PulseAudio/PipeWire sockets, config directory
- `entrypoint.sh` handles D-Bus setup, audio system detection, and volume restoration before starting the Python app

## Audio Routing

`BluetoothManager.configure_bluetooth_audio()` tries multiple `pactl` sink naming patterns:
- `bluez_output.{MAC}.1` (PipeWire)
- `bluez_output.{MAC}.a2dp-sink`
- `bluez_sink.{MAC}.a2dp_sink` (PulseAudio on HAOS)
- `bluez_sink.{MAC}`

Each `SendspinClient` spawns a subprocess with `PULSE_SINK=<found_sink_name>`. The subprocess creates its own PulseAudio context → audio routed to the correct BT speaker from the first sample.

On BT reconnect: PulseAudio's `module-rescue-streams` may move streams to the default sink. `BridgeDaemon._ensure_sink_routing()` corrects this once on the next `Stream STARTED` event via `services/pulse.py:amove_pid_sink_inputs()`. The `_sink_routed` flag prevents repeated corrections that would cause a re-anchor feedback loop.

**Confirmed production (HAOS):** PulseAudio 17.0 is used, not PipeWire. The active sink pattern is always `bluez_sink.{MAC_underscores}.a2dp_sink` (e.g. `bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink`). The PipeWire patterns (`bluez_output.*`) are tried first but will not match on HAOS.

## Deployment Environment

### Network topology

| Host | IP | SSH alias |
|------|-----|-----------|
| Turris router | 192.168.10.1 | `turris` |
| Proxmox | 192.168.10.12 | `proxmox` |
| HAOS VM 104 | 192.168.10.10 | `haos` |
| Test VM 105 | 192.168.10.105 | `sendspin-test` |

### Proxmox host

- PVE 8.4.16, kernel 6.8.12-18-pve
- USB device mappings (passed through to HAOS VM 104):
  - `Audio` → CSR8510 A10 BT adapter → hci0 inside HAOS
  - `BLE` → CSR8510 A10 BT adapter → VM 105 (test)
  - `aTick` → CSR8510 A10 BT adapter → not assigned
  - SONOFF Zigbee dongle (`1a86:55d4`) → passed directly as `usb0` to VM 104

### HAOS VM 104

- HAOS 17.1, `qemux86-64` (OVA/QEMU), 2 vCPU, 6 GB RAM, 64 GB disk
- HA Core 2026.02.3 (stable channel)
- **Audio system: PulseAudio 17.0** (not PipeWire)
- BT adapters inside HAOS:
  - `hci0` MAC `C0:FB:F9:62:D6:9D` (CSR8510 A10, Proxmox `Audio` mapping)
  - `hci1` MAC `C0:FB:F9:62:D7:D6` (CSR8510 A10, Proxmox `BLE` mapping)
- Addon slug: `85b1ecde_sendspin_bt_bridge`

### Configured Bluetooth devices (production)

| Player name | MAC | Adapter | Port | Delay | Enabled |
|-------------|-----|---------|------|-------|---------|
| ENEBY20 | FC:58:FA:EB:08:6C | hci0 | 8928 | −600 ms | ✅ |
| Yandex mini 2 007 a | 2C:D2:6B:B8:EC:5B | hci1 | 8929 | −400 ms | ✅ |
| WH-1000XM4 | 80:99:E7:C2:0B:D3 | hci0 | 8931 | −600 ms | ❌ |
| Lenco LS-500 | 30:21:0E:0A:AE:5A | hci1 | 8932 | −600 ms | ✅ |
| OpenMove AfterShokz | 20:74:CF:61:FB:D8 | hci0 | 8933 | −600 ms | ❌ |
| ENEBY Portable | 6C:5C:3D:35:17:99 | hci1 | 8933 | −600 ms | ❌ |

### Production addon settings

```
TZ: Europe/Moscow
pulse_latency_msec: 800   # high — compensates for QEMU VM audio overhead
prefer_sbc_codec: true
bt_check_interval: 15
bt_max_reconnect_fails: 10
```

### Proxmox LXC 101 (standalone deployment)

A second deployment of the bridge runs in a Proxmox LXC container (not HAOS):
- OS: Ubuntu, 2 cores, 1 GB RAM, 4 GB disk, DHCP
- AppArmor: unconfined (required for bluetoothd socket access)
- USB passthrough: `/dev/bus/usb` (host BT adapters)
- Mounts host's `/run/dbus` and `/var/lib/bluetooth` (read-only) so it shares the host BT stack

### Proxmox VM 105 (Docker test environment)

Docker-based test deployment matching reporter environments (Ubuntu + PipeWire):
- OS: Ubuntu 24.04 LTS, 2 vCPU, 2 GB RAM, 16 GB disk, static IP 192.168.10.105
- Audio: PipeWire with pipewire-pulse (Ubuntu 24.04 default)
- BT adapter: `BLE` mapping (C0:FB:F9:62:D7:D6)
- Bridge runs via `docker compose` (same as end-user deployment)
- Config dir: `/etc/docker/Sendspin/config.json`
- Web UI: `http://192.168.10.105:8080`
- Created with: `scripts/proxmox-vm-create.sh` (on Proxmox host)
- Deploy/update: `scripts/proxmox-vm-deploy.sh` (from Mac)

## Agent Operations

Commands for agents working with the production deployment.

### Check addon status / available update

```bash
ssh haos "ha addons info 85b1ecde_sendspin_bt_bridge | grep -E 'version|state|update'"
```

### Update addon to latest version

```bash
ssh haos "ha addons update 85b1ecde_sendspin_bt_bridge"
```

### View addon logs (last 50 lines)

```bash
ssh haos "ha addons logs 85b1ecde_sendspin_bt_bridge | tail -50"
```

### Restart addon

```bash
ssh haos "ha addons restart 85b1ecde_sendspin_bt_bridge"
```

### Check active Bluetooth audio sinks

```bash
ssh haos "pactl list sinks short"
```

### Check Proxmox VM 104 status

```bash
ssh proxmox "qm status 104"
```

### Test VM 105 operations

```bash
# Check VM status
ssh proxmox "qm status 105"

# Start / stop test VM
ssh proxmox "qm start 105"
ssh proxmox "qm stop 105"

# View bridge logs
ssh sendspin-test "docker logs -f sendspin-client"

# Restart bridge
ssh sendspin-test "docker restart sendspin-client"

# Redeploy / update bridge
bash scripts/proxmox-vm-deploy.sh

# BT debugging inside container
ssh sendspin-test "docker exec -it sendspin-client bluetoothctl"

# Check audio sinks
ssh sendspin-test "docker exec sendspin-client pactl list sinks short"
```
