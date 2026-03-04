# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Dockerized Python client that bridges Music Assistant's Sendspin protocol to Bluetooth speakers, with a Flask web UI for configuration and status monitoring. Designed for headless systems (Raspberry Pi, Home Assistant).

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

There is no test suite. Manual testing is via `docker logs` and the web UI at `http://localhost:8080`.

CI/CD builds multi-platform Docker images (`linux/amd64`, `linux/arm64`) to `ghcr.io/trudenboy/sendspin-bt-bridge` on `v*` tag push. Automatically syncs `ha-addon/config.yaml` version from `VERSION` in `config.py` before the build.

## Architecture (v2.6.2)

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
- `VERSION = "2.6.2"`

**`mpris.py`** — MPRIS D-Bus integration:
- `MprisIdentityService` — registers MediaPlayer2 D-Bus service so MA discovers the bridge by player name

**`services/` module:**
- `bridge_daemon.py` — `BridgeDaemon` subclass. Runs inside each subprocess. Handles `on_status_change` callbacks, stream events. `_sink_routed` flag prevents re-anchor feedback loop after PA rescue-streams correction.
- `daemon_process.py` — subprocess entry point. Reads JSON args from argv, sets up `BridgeDaemon`, emits status as JSON to stdout, reads commands from stdin.
- `bluetooth.py` — BT helpers: `bt_remove_device()`, `persist_device_enabled()` (sync to config.json + options.json), `is_audio_device()`
- `pulse.py` — PulseAudio async helpers: `afind_sink_for_mac()`, `amove_pid_sink_inputs()` (corrects streams after PA module-rescue-streams moves them on BT reconnect), `_PULSECTL_AVAILABLE` flag

**`routes/` module (Flask blueprints):**
- `api.py` — all `/api/*` endpoints; includes `GET /api/status/stream` (SSE), `POST /api/bt/scan` → `{job_id}`, `GET /api/bt/scan/result/<id>` (async scan), `_schedule_volume_persist()` (1 s debounce before writing config.json)
- `views.py` — HTML page renders
- `auth.py` — optional web UI password protection

**`state.py`** — shared runtime state:
- List of `SendspinClient` instances + global lock
- `notify_status_changed()` — SSE signaling (increments version, wakes threading.Condition)
- `_adapter_cache_lock` — double-checked locking in `get_adapter_name()`
- `create_scan_job()` / `get_scan_job()` / `finish_scan_job()` — storage for async BT-scan jobs (TTL 2 min)

**`scripts/translate_ha_config.py`** — called from `entrypoint.sh` in HA addon mode:
- Converts `/data/options.json` → `/data/config.json` with full field typing
- `_detect_adapters()` — enumerates BT controllers via `bluetoothctl list`
- `_merge_adapters()` — merges user-supplied names with detected hardware
- Preserves runtime state from previous config (LAST_VOLUMES, AUTH_PASSWORD_HASH, SECRET_KEY)

**Config persistence:** `/config/config.json` (mounted Docker volume at `/etc/docker/Sendspin`). Changes via the web UI require a container restart to take effect.

**Docs site:** `docs-site/` — Astro Starlight, deployed to GitHub Pages at `https://trudenboy.github.io/sendspin-bt-bridge`

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENDSPIN_SERVER` | `auto` | MA server hostname/IP; `auto` uses mDNS discovery |
| `SENDSPIN_PORT` | `9000` | WebSocket port |
| `BLUETOOTH_MAC` | `` | Single-device MAC (legacy); use `BLUETOOTH_DEVICES` in config.json for multi-device |
| `WEB_PORT` | `8080` | Web interface port |
| `TZ` | `Australia/Melbourne` | Container timezone |
| `CONFIG_DIR` | `/config` | Config directory path |

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
