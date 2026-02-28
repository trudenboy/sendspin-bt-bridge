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

CI/CD builds multi-platform Docker images (`linux/amd64`, `linux/arm64`) to `ghcr.io/loryanstrant/sendspin-client` on push to `main`.

## Architecture

Two main Python files:

**`sendspin_client.py`** - Core application:
- `BluetoothManager` - Manages Bluetooth pairing/connection via `bluetoothctl` subprocess. Auto-reconnects every 10 seconds. Handles both PipeWire and PulseAudio sink routing.
- `SendspinClient` - Wraps the `sendspin` CLI (run as subprocess with `--headless`). Parses CLI output to track playback state and syncs volume to the Bluetooth sink via `pactl`.
- `main()` - Loads config, instantiates both classes, starts web server in a daemon thread, then runs the async event loop.

**`web_interface.py`** - Flask app served by Waitress on port 8080:
- Embeds all HTML/CSS/JS inline (no template files or static assets)
- Polls `/api/status` every 2 seconds to update the dashboard
- `/api/config` GET/POST reads/writes `/config/config.json`
- `/api/volume` invokes `pactl` to set speaker volume

**Config persistence:** `/config/config.json` (mounted Docker volume at `/etc/docker/Sendspin`). Changes via the web UI require a container restart to take effect.

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
