# Contributing to Sendspin Bluetooth Bridge

## Vision

Bring Bluetooth speakers into Music Assistant as first-class players — no extra hardware, no cloud dependency, works headless on Raspberry Pi, Home Assistant, and Proxmox LXC.

---

## Running Locally

Requires Docker and Docker Compose. Bluetooth speakers must be **paired with the host** before starting.

```bash
git clone https://github.com/trudenboy/sendspin-bt-bridge.git
cd sendspin-bt-bridge

# Build and start
docker compose up --build

# View logs
docker logs -f sendspin-client

# Web UI
open http://localhost:8080
```

To run without Docker (requires system Bluetooth and audio packages):

```bash
pip install -r requirements.txt
python sendspin_client.py
```

---

## Manual Test Checklist

There is no automated test suite. Use this checklist when testing changes:

- [ ] Container starts without errors (`docker logs -f sendspin-client`)
- [ ] Web UI loads at `http://localhost:8080`
- [ ] Bluetooth device connects and appears in the web UI
- [ ] Music Assistant detects the player
- [ ] Audio plays through the Bluetooth speaker
- [ ] Volume slider in web UI changes speaker volume
- [ ] Auto-reconnect triggers after disconnecting the speaker (wait ~10 s)
- [ ] Config changes via the web UI persist after container restart
- [ ] `/api/status` returns valid JSON
- [ ] `/api/config` GET returns current config; POST with valid payload saves it

For HA addon changes, additionally test via the HA Addon Store dev workflow (local repository).

---

## Branching Strategy

- `main` — stable, always releasable
- Feature branches — branch off `main`, name them `feat/<description>` or `fix/<description>`
- Submit a pull request against `main` when ready

---

## Reporting Bugs

Open an issue at [GitHub Issues](https://github.com/trudenboy/sendspin-bt-bridge/issues). Include:

- Deployment method (Docker / HA Addon / Proxmox LXC)
- Relevant log output (`docker logs sendspin-client` or `journalctl -u sendspin-client`)
- Host OS, audio system (PipeWire or PulseAudio), Bluetooth adapter model
- Steps to reproduce

---

## Attribution

This project originated from [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client)
and has since grown into a fully independent project. Credit to loryanstrant for the original concept.

When contributing:
- Credit the [Music Assistant](https://www.music-assistant.io/) team for the Sendspin protocol and CLI
