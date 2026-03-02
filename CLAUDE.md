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

CI/CD builds multi-platform Docker images (`linux/amd64`, `linux/arm64`) to `ghcr.io/trudenboy/sendspin-bt-bridge` on push to `main`.

## Architecture

**Python modules (v1.4.x modular structure):**

**`sendspin_client.py`** (753 lines) - Core entry point:
- `SendspinClient` - Wraps the `sendspin` CLI subprocess. Parses output to track playback state, syncs volume via `pactl`.
- `main()` - Loads config, instantiates BluetoothManager + SendspinClient per device, starts web server daemon thread, runs async event loop.

**`bluetooth_manager.py`** (492 lines) - BT connection management:
- `BluetoothManager` - Manages pairing/connection via `bluetoothctl` stdin pipe. Auto-reconnects every 10 s. Handles PipeWire and PulseAudio sink routing.
- `_force_sbc_codec()` - Forces SBC A2DP codec via pactl/bluetoothctl.

**`config.py`** (80 lines) - Configuration layer:
- `_CONFIG_PATH`, `_config_lock` (shared threading.Lock — imported by both sendspin_client.py and web_interface.py)
- `load_config()`, `_player_id_from_mac()`, `_save_device_volume()`

**`mpris.py`** (121 lines) - MPRIS D-Bus integration:
- `MprisIdentityService` - Registers MediaPlayer2 D-Bus service so MA discovers the bridge by player name
- `pause_all_via_mpris()`, `read_mpris_metadata_for()` - D-Bus helpers

**`web_interface.py`** (1107 lines) - Flask app served by Waitress on port 8080:
- All `/api/*` routes
- Polls `/api/status` every 2 s via JS in browser
- Imports `_config_lock` from `config.py` (unified shared lock)
- HA Ingress support via `X-Ingress-Path` → Flask `SCRIPT_NAME`

**`templates/index.html`** - Jinja2 HTML template (Flask `render_template`)
**`static/style.css`** - Extracted CSS (360 lines)
**`static/app.js`** - Extracted JavaScript (1242 lines)

**Config persistence:** `/config/config.json` (mounted Docker volume at `/etc/docker/Sendspin`). Changes via the web UI require a container restart to take effect.

**Docs site:** `docs-site/` — Astro Starlight, deployed to GitHub Pages at `https://trudenboy.github.io/sendspin-bt-bridge`

## Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SENDSPIN_NAME` | `Docker-{hostname}` | Player display name in Music Assistant |
| `SENDSPIN_SERVER` | `auto` | Server hostname/IP; `auto` uses mDNS discovery |
| `SENDSPIN_PORT` | `9000` | WebSocket port (`ws://{server}:{port}/sendspin`) |
| `BLUETOOTH_MAC` | `` | Target speaker MAC address (Bluetooth disabled if empty) |
| `WEB_PORT` | `8080` | Web interface port |
| `TZ` | `Australia/Melbourne` | Container timezone |

## Container Requirements

- Network mode: `host` (required for mDNS auto-discovery)
- Privileged mode + capabilities `NET_ADMIN`, `NET_RAW`, `SYS_ADMIN` (required for Bluetooth)
- Volume mounts: D-Bus socket, PulseAudio/PipeWire sockets, config directory
- `entrypoint.sh` handles D-Bus setup, audio system detection, and volume restoration before starting the Python app

## Audio Sink Detection

`BluetoothManager.configure_bluetooth_audio()` tries multiple `pactl` sink naming patterns for the connected device:
- `bluez_output.{MAC}.1` (PipeWire)
- `bluez_output.{MAC}.a2dp-sink`
- `bluez_sink.{MAC}.a2dp_sink` (legacy PulseAudio)
- `bluez_sink.{MAC}`
