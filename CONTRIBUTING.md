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

Run `pytest` for the automated test suite and use `cd docs-site && npm run build` when changing the Starlight docs. The Python suite currently validates the v2.40.5 runtime, while the docs build catches broken Starlight routes, content, and screenshots.

### Linting

```bash
ruff check .                    # Fast Python linter
ruff format --check .           # Code formatting check
```

Additionally, use this manual checklist when testing changes:

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

For HA addon changes, additionally test via the HA Addon Store dev workflow (local repository). For documentation changes that include screenshots, prefer the built-in demo stack (`DEMO_MODE=true python sendspin_client.py`) as the repeatable local capture environment before resorting to live HA-only screenshots.

---

## Docker & HA Addon Architecture

The single `Dockerfile` builds a multi-arch image (`linux/amd64`, `linux/arm64`, `linux/arm/v7`) pushed to `ghcr.io/trudenboy/sendspin-bt-bridge`.

**S6 overlay** (v3.2.0.2) provides PID 1 process supervision:
- `/init` → S6 boot → `rootfs/etc/s6-overlay/s6-rc.d/sendspin/run` → `/app/entrypoint.sh` → `python3 sendspin_client.py`
- Handles zombie reaping, signal forwarding (SIGTERM → graceful shutdown), and automatic restarts on crash

**HA addon** (`ha-addon/Dockerfile`) is a thin `FROM` wrapper — no additional layers. HA Supervisor pulls the pre-built image via the `image:` field in `config.yaml`.

**AppArmor** is enabled in enforce mode (`ha-addon/apparmor.txt`). The profile covers S6, Python, bluetoothctl, pactl, D-Bus, and all runtime paths.

---


## Runtime Architecture Touchpoints

Keep the current runtime layering in mind when making code or docs changes:

- `entrypoint.sh` prepares D-Bus/audio, translates Home Assistant add-on options, and then `exec`s `python3 sendspin_client.py`.
- `sendspin_client.py` is now the thin runtime entrypoint; bridge-wide startup sequencing lives in `bridge_orchestrator.py`.
- `BridgeOrchestrator` owns runtime bootstrap, channel-aware port defaults, lifecycle publication, web startup, Music Assistant bootstrap, and long-running task assembly.
- `SendspinClient` still owns one speaker lifecycle, but focused subprocess concerns live in `services.subprocess_command`, `services.subprocess_ipc`, `services.subprocess_stderr`, and `services.subprocess_stop`.
- `services/daemon_process.py` + `services/bridge_daemon.py` run one isolated Sendspin daemon per speaker with `PULSE_SINK` preselected before audio starts.
- `state.py` plus the newer lifecycle/read-side services publish startup progress, snapshots, update info, onboarding guidance, and SSE payloads to the UI/API.

---

## Release Workflow & Generated Add-on Variants

- `CHANGELOG.md` is the release source of truth. Put user-facing release notes there first.
- The manual `Create GitHub Release` workflow resolves the target tag/channel, syncs `ha-addon/`, `ha-addon-rc/`, and `ha-addon-beta/` on `main`, and then builds the GitHub Release body from the matching changelog section.
- `ha-addon/` is the hand-maintained stable source surface. RC/Beta directories are generated/published variants; prefer regenerating them via the script/workflow instead of hand-editing multiple channel copies.
- `update_channel` in config only controls release lookup/warning surfaces. It does **not** switch the installed Home Assistant add-on variant by itself.

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


---

## Release workflow

- Update `config.py` version and add matching sections to `CHANGELOG.md` and `ha-addon/CHANGELOG.md`.
- Sync addon variants with `python3 scripts/generate_ha_addon_variants.py sync-current-repo ...`.
- Validate runtime changes with `python3 -m pytest -q && node --check static/app.js && git --no-pager diff --check`.
- Validate docs changes with `cd docs-site && npm run build`.
- GitHub Release notes are composed from the matching `CHANGELOG.md` section; GitHub-generated notes are supplemental only.
