# Contributing to Sendspin Bluetooth Bridge

## Vision

Bring Bluetooth speakers into Music Assistant as first-class players — no extra hardware, no cloud dependency, works headless on Raspberry Pi, Home Assistant, Proxmox LXC, and OpenWrt LXC.

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

Run `pytest` for the automated test suite (138 tests across 16 files covering config, volume routing, device status, state management, auth, ingress middleware, API endpoints, and more). Additionally, use this manual checklist when testing changes:

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

## Reporting Bugs & Requesting Features

We use **GitHub Issues** for actionable bug reports and feature requests, and **GitHub Discussions** for questions, ideas, and community help.

### Issues (bugs & features)

Open a new issue using one of the structured templates:

| Template | When to use |
|----------|-------------|
| [🐛 Bug Report](https://github.com/trudenboy/sendspin-bt-bridge/issues/new?template=bug_report.yml) | Something is broken — reproducible unexpected behavior |
| [🔊 Bluetooth & Audio](https://github.com/trudenboy/sendspin-bt-bridge/issues/new?template=bluetooth_audio.yml) | BT pairing failures, no sound, disconnects, codec issues |
| [💡 Feature Request](https://github.com/trudenboy/sendspin-bt-bridge/issues/new?template=feature_request.yml) | Suggest a new feature or enhancement |

**Tips for good bug reports:**
- Include your deployment method (Docker / HA Addon / Proxmox LXC / OpenWrt LXC)
- Paste log output: `docker logs sendspin-client --tail 100` or `journalctl -u sendspin-client`
- For BT/audio issues: include `bluetoothctl info <MAC>` and `pactl list sinks short` output
- Mention your host OS, audio system (PipeWire/PulseAudio), and BT adapter model

### Discussions (questions & ideas)

For questions, setup help, and early-stage ideas — use [GitHub Discussions](https://github.com/trudenboy/sendspin-bt-bridge/discussions):

| Category | When to use |
|----------|-------------|
| 🙏 Q&A | General usage questions and troubleshooting |
| 🔊 Bluetooth & Audio | Speaker compatibility, audio routing, codec questions |
| 🏗️ Deployment | Help with Docker, HA Addon, Proxmox LXC, OpenWrt setup |
| 💡 Ideas | Early-stage feature ideas and brainstorming |
| 🛠️ Show and Tell | Share your setup, config, or creative use case |

---

## Attribution

This project originated from [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client)
and has since grown into a fully independent project. Credit to loryanstrant for the original concept.

When contributing:
- Credit the [Music Assistant](https://www.music-assistant.io/) team for the Sendspin protocol and CLI
