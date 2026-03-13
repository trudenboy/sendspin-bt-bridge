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

Unit tests: `pytest` (see `tests/`). 187 tests across 18 files. Manual testing via `docker logs` and the web UI at `http://localhost:8080`.

CI/CD builds multi-platform Docker images (`linux/amd64`, `linux/arm64`) to `ghcr.io/trudenboy/sendspin-bt-bridge` on `v*` tag push. Automatically syncs `ha-addon/config.yaml` version from `VERSION` in `config.py` before the build.

## Architecture (v2.30.6)

**Subprocess isolation**: each Bluetooth speaker runs as a dedicated Python subprocess (`services/daemon_process.py`) with `PULSE_SINK=<bt_sink_name>` in env. This gives every speaker its own PulseAudio context → correct audio routing from the first sample, no `move-sink-input` needed.

```
main process (Flask API, BT manager, web UI)
    ├── asyncio subprocess (PULSE_SINK=bluez_sink.AA_BB...) → daemon_process.py
    ├── asyncio subprocess (PULSE_SINK=bluez_sink.CC_DD...) → daemon_process.py
    └── ...
```

IPC: subprocess→parent via JSON lines on stdout; parent→subprocess via JSON lines on stdin (`set_volume`, `stop`).

**`sendspin_client.py`** — core orchestration per device:
- `DeviceStatus` — `@dataclass` typed per-device status; dict-compatible (`["key"]`, `.get()`, `.update()`, `.copy()`)
- `SendspinClient` — manages the per-device subprocess lifecycle. Spawns `services/daemon_process.py` with correct `PULSE_SINK` env var. Reads JSON status from subprocess stdout, sends volume/stop commands via stdin.
- `_status_lock = threading.Lock()` + `_update_status(updates)` — thread-safe status mutation from asyncio loop, D-Bus callback thread, and Flask WSGI threads; calls `notify_status_changed()` after each mutation
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
- `_config_lock` (threading.Lock shared across modules)
- `load_config()`, `_player_id_from_mac()`, `save_device_volume()` (public; `_save_device_volume` alias retained for compatibility)
- `VERSION = "2.30.6"`, `BUILD_DATE = "2026-03-13"`

**`services/` module:**
- `bridge_daemon.py` — `BridgeDaemon` subclass. Runs inside each subprocess. Handles `on_status_change` callbacks, stream events. `_sink_routed` flag prevents re-anchor feedback loop after PA rescue-streams correction.
- `daemon_process.py` — subprocess entry point. Reads JSON args from argv, sets up `BridgeDaemon`, emits status as JSON to stdout, reads commands from stdin. `_startup_unmute_watcher` accepts `on_status_change` callback and calls it after unmuting (fixes stale mute indicators); startup unmute timeout is 15 s (was 60 s).
- `bluetooth.py` — BT helpers: `bt_remove_device()`, `persist_device_enabled()`, `persist_device_released()` (sync to config.json + options.json), `is_audio_device()`, `_match_player_name()` — handles bridge name suffix matching for config persistence
- `pulse.py` — PulseAudio async helpers: `afind_sink_for_mac()`, `amove_pid_sink_inputs()` (corrects streams after PA module-rescue-streams moves them on BT reconnect), `_PULSECTL_AVAILABLE` flag
- `ma_discovery.py` — mDNS-based Music Assistant server discovery
- `ma_client.py` — MA REST API helpers: `discover_ma_groups()`, `_fetch_all_players()`, `_normalize_ma_url()`, `ma_group_play()`
- `ma_monitor.py` — `MaMonitor` class: persistent WebSocket connection to MA for real-time now-playing, queue state, and transport control
- `update_checker.py` — Background version polling: `run_update_checker()`, `check_latest_version()`, auto-update support

**`routes/` module (Flask blueprints):**
- `api.py` — core volume/mute/pause/restart endpoints (6 routes), `_schedule_volume_persist()` (1 s debounce)
- `api_bt.py` — BT scan/pair/remove/reconnect/enable/disable/device/enabled/scan/result (9 routes). `_get_bt_device_info()` helper extracted from inline code. ANSI stripping in adapter power success detection.
- `api_ma.py` — MA integration, OAuth sign-in, groups/nowplaying/queue control (10 routes)
- `api_config.py` — configuration CRUD, adapter management, logs/download, update/check, update/info, update/apply, config/download (raw config.json with timestamped filename), config/upload (upload config.json replacing current, preserves sensitive keys) (11 routes)
- `api_status.py` — status/diagnostics/version/logs/diagnostics/download/bugreport (8 routes). `_collect_bt_device_info()` helper for bugreport BT device info.
- `views.py` — HTML page renders
- `auth.py` — optional web UI password protection (PBKDF2-SHA256); HA login_flow with 2FA/TOTP support; brute-force lockout (5 attempts / 5 min)

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

**Config persistence:** `/config/config.json` (mounted Docker volume at `/etc/docker/Sendspin`). Changes via the web UI require a container restart to take effect.

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

### Proxmox host

- PVE 8.4.16, kernel 6.8.12-18-pve
- USB device mappings (passed through to HAOS VM 104):
  - `Audio` → CSR8510 A10 BT adapter → hci0 inside HAOS
  - `BLE` → CSR8510 A10 BT adapter → hci1 inside HAOS
  - SONOFF Zigbee dongle (`1a86:55d4`) → passed directly as `usb0`

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
