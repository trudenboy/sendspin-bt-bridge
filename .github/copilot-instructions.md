# Copilot Instructions

## Project Overview

Dockerized Python bridge between Music Assistant's Sendspin protocol and Bluetooth speakers. Designed for headless systems (Raspberry Pi, Home Assistant addon, Proxmox LXC, OpenWrt LXC).

## Build & Run

```bash
# Build and run (primary workflow)
docker compose up --build

# Run without Docker (requires system BT/audio packages)
pip install -r requirements.txt
python sendspin_client.py

# Run tests
pytest

# Logs
docker logs -f sendspin-client

# BT debugging inside container
docker exec -it sendspin-client bluetoothctl
```

Tests: `tests/test_config.py` and `tests/test_volume_routing.py` (pytest). Manual verification uses `docker logs` and the web UI at `http://localhost:8080`. See `CONTRIBUTING.md` for the manual test checklist.

## Architecture (v2.12.0)

**Multi-process Python bridge.** Main process handles BT management, Flask/Waitress web API, and MA integration. Each BT speaker runs as an isolated subprocess (`services/daemon_process.py`) with `PULSE_SINK=<bt_sink_name>` for correct audio routing.

**Key modules:**

- **`sendspin_client.py`** — Core orchestration. `SendspinClient` manages per-device subprocess lifecycle, `DeviceStatus` dataclass for typed status. `main()` loads config, spawns `BluetoothManager` + `SendspinClient` per device, starts Waitress in daemon thread, runs asyncio loop.
- **`bluetooth_manager.py`** — `BluetoothManager`: pairing/connection/reconnect via `bluetoothctl`, D-Bus disconnect detection, exponential backoff, churn isolation.
- **`config.py`** — `VERSION`, `load_config()`, `save_device_volume()`, `hash_password()`, thread-safe config persistence.
- **`state.py`** — Shared runtime state, SSE signaling with batched notifications (100ms), async BT-scan jobs, MA groups cache.
- **`web_interface.py`** — Flask app with Waitress, HA Ingress middleware, auth enforcement, blueprint registration.

- **`services/bridge_daemon.py`** — `BridgeDaemon(SendspinDaemon)` inside each subprocess.
- **`services/daemon_process.py`** — Subprocess entry point. JSON-line IPC (stdin/stdout).
- **`services/bluetooth.py`** — `bt_remove_device()`, `persist_device_enabled()`, `is_audio_device()`.
- **`services/pulse.py`** — PulseAudio helpers: `afind_sink_for_mac()`, `amove_pid_sink_inputs()`. Dual-mode: pulsectl or pactl fallback.

- **`routes/api.py`** — All `/api/*` endpoints (28 total), SSE stream, async BT scan.
- **`routes/views.py`** — HTML page renders via `templates/index.html`.
- **`routes/auth.py`** — Password auth (PBKDF2-SHA256), HA login_flow with 2FA, brute-force protection.

- **`templates/`** — `index.html`, `login.html`.
- **`static/`** — `app.js`, `style.css`, `favicon.png`, `favicon.svg`.
- **`scripts/translate_ha_config.py`** — Converts HA `/data/options.json` → `/data/config.json`.
- **`entrypoint.sh`** — D-Bus setup, audio detection, HA addon config translation.

## Key Conventions

**Version management:** Single `VERSION` in `config.py`. `ha-addon/config.yaml` version is synced by the manual GitHub release workflow, not by tag push.

**Config file:** `/config/config.json` (or `/data/config.json` in HA addon). `CONFIG_DIR` env var controls path. Raw JSON with `indent=2`.

**Thread safety:** `_status_lock` + `_update_status()` for status mutation. `_config_lock` for config file access. All `bluetoothctl`/`pactl` subprocesses dispatched via `run_in_executor()`.

**Audio sink discovery:** `BluetoothManager.configure_bluetooth_audio()` tries four sink patterns — `bluez_output.{MAC}.1` (PipeWire), `bluez_output.{MAC}.a2dp-sink`, `bluez_sink.{MAC}.a2dp_sink`, `bluez_sink.{MAC}` — retrying up to 3×. Per-process routing uses `PULSE_SINK` env var.

**IPC protocol:** Parent↔subprocess communicate via JSON lines on stdin/stdout. Commands: `set_volume`, `set_mute`, `stop`, `pause`/`play`, `reconnect`, `set_log_level`.

## CI/CD

Builds multi-platform images (`linux/amd64`, `linux/arm64`) to `ghcr.io/trudenboy/sendspin-bt-bridge` on `v*` tag push or `workflow_dispatch`. GitHub releases are created by the separate manual `Create GitHub Release` workflow, which defaults to the latest tag and syncs `ha-addon/config.yaml` at release time.
