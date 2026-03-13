---
title: Development & Contributing
description: Developer guide for Sendspin Bluetooth Bridge
---

## Running Locally

Requires Docker and Docker Compose. The speaker must be **paired with the host** before starting.

```bash
git clone https://github.com/trudenboy/sendspin-bt-bridge.git
cd sendspin-bt-bridge

docker compose up --build
docker logs -f sendspin-client
open http://localhost:8080
```

Without Docker (requires system Bluetooth and audio packages):

```bash
pip install -r requirements.txt
python sendspin_client.py
```

## Project Structure

```
sendspin_client.py    # Entry point: SendspinClient + main()
bluetooth_manager.py  # BluetoothManager — BT connections via bluetoothctl
config.py             # Configuration, shared lock, load_config()
state.py              # Shared runtime state (list of SendspinClient instances)

services/
  bridge_daemon.py    # BridgeDaemon — runs inside each subprocess; stream events, sink routing
  daemon_process.py   # Subprocess entry point: reads args, runs BridgeDaemon, emits JSON status
  bluetooth.py        # Async BT helpers (D-Bus monitor)
  pulse.py            # PulseAudio helpers (pulsectl + pactl): find sink, move sink-inputs

routes/
  api.py              # Core playback & volume (6 routes)
  api_bt.py           # Bluetooth management (9 routes)
  api_ma.py           # Music Assistant integration (10 routes)
  api_config.py       # Configuration (9 routes)
  api_status.py       # Status & diagnostics (8 routes)
  views.py            # HTML page renders
  auth.py             # Optional web UI password protection

entrypoint.sh         # Docker entrypoint: D-Bus, audio init
ha-addon/             # Home Assistant addon configuration
lxc/                  # LXC install scripts (Proxmox & OpenWrt)
```

> **Architecture note**: each Bluetooth speaker runs as an isolated asyncio subprocess (`services/daemon_process.py`) with `PULSE_SINK=<bt_sink_name>` in its environment. This gives every speaker its own PulseAudio context so audio routes to the correct speaker from the first sample, without any `move-sink-input` calls at startup.

## Testing

Run `pytest` for automated unit tests:

```bash
python3 -m pytest tests/ -v
```

The test suite has **187 tests** across 18 test files covering config, volume routing, device status, state management, authentication, Ingress middleware, BT services, daemon process, MA integration, and scan cooldown.

### Linting

```bash
ruff check .                    # Fast Python linter
ruff format --check .           # Code formatting check
```

### Manual Test Checklist

Use this checklist when making changes:

- [ ] Container starts without errors (`docker logs -f sendspin-client`)
- [ ] Web UI loads at `http://localhost:8080`
- [ ] Bluetooth device connects and appears in the web UI
- [ ] Music Assistant detects the player
- [ ] Audio plays through the Bluetooth speaker
- [ ] Volume slider changes speaker volume
- [ ] Auto-reconnect triggers after disconnecting (~10 s)
- [ ] Config changes persist after container restart
- [ ] `/api/status` returns valid JSON

## Branching Strategy

- `main` — stable, always releasable
- Feature branches — branch off `main`, name `feat/<description>` or `fix/<description>`
- Submit a PR against `main`

## Reporting a Bug

Open an [issue on GitHub](https://github.com/trudenboy/sendspin-bt-bridge/issues). Include:
- Deployment method (Docker / HA Addon / Proxmox LXC)
- Log output
- Host OS, audio system (PipeWire/PulseAudio), Bluetooth adapter model
- Steps to reproduce

## CI/CD

Pushing a `v*` tag to `main` automatically:
1. Builds multi-platform Docker image (`linux/amd64`, `linux/arm64`)
2. Publishes to `ghcr.io/trudenboy/sendspin-bt-bridge`
3. Syncs the version to `ha-addon/config.yaml`

## Attribution

This project grew out of [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client). Thanks to the [Music Assistant](https://www.music-assistant.io/) team for the Sendspin protocol and CLI.

## Further reading

- [Architecture](/sendspin-bt-bridge/architecture/) — deep dive into the process model, IPC protocol, audio routing, Bluetooth state machine, MA integration, authentication, and graceful degradation
- [HISTORY.md](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/HISTORY.md) — narrative history of the project's evolution (v1 → v2, key design decisions)
- [CHANGELOG.md](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CHANGELOG.md) — full version history
