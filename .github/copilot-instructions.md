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

# Logs
docker logs -f sendspin-client

# BT debugging inside container
docker exec -it sendspin-client bluetoothctl
```

There is no test suite. Manual verification uses `docker logs` and the web UI at `http://localhost:8080`. See `CONTRIBUTING.md` for the manual test checklist.

## Architecture

**Two Python files + a shell entrypoint:**

- **`sendspin_client.py`** — Core async application. `BluetoothManager` handles pairing/connection/reconnect via `bluetoothctl` subprocess. `SendspinClient` wraps the `sendspin` CLI (run as subprocess with `--headless`) and tracks playback state by parsing its stdout. `main()` reads config, spawns one `BluetoothManager` + `SendspinClient` pair per configured device, starts the web server in a daemon thread, then drives everything through an `asyncio` event loop.

- **`web_interface.py`** — Flask app served by Waitress on port 8080. All HTML/CSS/JS is inline via `render_template_string` — there are no template files or static assets. Shares state with `sendspin_client.py` via the `_clients` list injected at startup.

- **`entrypoint.sh`** — Runs before the Python app. In HA addon mode it translates `/data/options.json` → `/data/config.json`. Sets up D-Bus, detects PipeWire vs PulseAudio socket, and exports `CONFIG_DIR=/data` when running as an HA addon.

**Multi-device:** `BLUETOOTH_DEVICES` in config is a list; each entry spawns its own `BluetoothManager` + `SendspinClient` pair running concurrently.

## Key Conventions

**Version management:** `CLIENT_VERSION` in `sendspin_client.py` and `VERSION` in `web_interface.py` must always match. CI auto-syncs `ha-addon/config.yaml`'s `version` field from `CLIENT_VERSION` on tag push — never edit `ha-addon/config.yaml` version manually.

**Config file:** `/config/config.json` (or `/data/config.json` in HA addon mode). `CONFIG_DIR` env var controls which path is used. Written as raw JSON with `indent=2`; no schema library. Changes require a container restart.

**Blocking calls never touch the event loop directly:** All `bluetoothctl`/`pactl` subprocesses in the async `monitor_and_reconnect` loop are dispatched via `loop.run_in_executor(None, ...)`.

**bluetoothctl interaction:** Always goes through `BluetoothManager._run_bluetoothctl(commands)`, which prepends `select <adapter_mac>` when an adapter is configured. In LXC containers, selecting by MAC (not `hciN`) is required because D-Bus objects use MACs.

**Audio sink discovery:** `BluetoothManager.configure_bluetooth_audio()` tries four sink name patterns in order — `bluez_output.{MAC}.1` (PipeWire), `bluez_output.{MAC}.a2dp-sink`, `bluez_sink.{MAC}.a2dp_sink`, `bluez_sink.{MAC}` — retrying up to 3× because the A2DP sink takes a few seconds to appear after BT connects. Per-process routing uses `PULSE_SINK` env var; the system default sink is not changed.

**dbus-python is optional:** MPRIS metadata (`_read_mpris_metadata_for`, `MprisIdentityService`) gracefully degrades when `dbus-python` isn't available. Always guard D-Bus usage with `try/except`.

**HA addon config:** Defined in `ha-addon/config.yaml`. The schema there controls what options appear in the HA UI. Option names map to `config.json` keys via the Python snippet in `entrypoint.sh`.

## CI/CD

Builds multi-platform images (`linux/amd64`, `linux/arm64`) to `ghcr.io/trudenboy/sendspin-bt-bridge` on `v*` tag push or manual `workflow_dispatch`. PRs against `main` build but do not push. Branch naming: `feat/<description>` or `fix/<description>`.
