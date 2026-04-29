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
# Recommended: uv-managed venv frozen to uv.lock. One-time install: https://docs.astral.sh/uv/
uv venv
uv sync --frozen --extra dev
uv run python -m sendspin_bridge

# Fallback: pip + venv. uv.lock is canonical, requirements.txt is regenerated from it.
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
python -m sendspin_bridge
```

---

## Manual Test Checklist

Run `uv run python -m pytest -q` for the automated runtime suite and use `cd docs-site && npm run build` when changing the Starlight docs. The test suite and docs should be treated as separate validation surfaces: Python catches runtime regressions, while the docs build catches broken Starlight routes, content, screenshot references, and frontmatter/schema issues.

### Test-Driven Development (TDD)

Use **red/green TDD** for new features and bug fixes:

1. **Red** — Write the test first. Confirm it fails.
2. **Green** — Write the minimal code to make the test pass.
3. **Refactor** — Clean up while keeping tests green.

When fixing a bug, first write a test that reproduces it (red), then fix (green).

**Do not** modify existing tests to make them pass — fix the implementation instead. If a test is genuinely wrong, explain why in the commit message.

### Linting

```bash
uv run ruff check .             # Fast Python linter
uv run ruff format --check .    # Code formatting check
uv run mypy --config-file pyproject.toml src/sendspin_bridge/

# CI also enforces these (driven by uv-pre-commit hooks):
uv lock --check                 # uv.lock matches pyproject.toml
uv export --no-hashes --no-dev --no-emit-project --output-file requirements.txt
                                # requirements.txt regenerates cleanly

# One-time pre-commit setup:
uv tool install pre-commit
pre-commit install
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
- [ ] Onboarding guidance points to the next unmet setup step in a fresh or demo environment
- [ ] Recovery guidance and release/reclaim actions appear for disconnected or released speakers
- [ ] The bug-report flow downloads diagnostics and opens a prefilled GitHub issue description

For HA addon changes, additionally test via the HA Addon Store dev workflow (local repository). For documentation changes that include screenshots, prefer the built-in demo stack (`DEMO_MODE=true python sendspin_client.py`) as the repeatable local capture environment and stable screenshot/test stand for onboarding, recovery, diagnostics, and bug-report UX before resorting to live HA-only screenshots.

## Demo mode for docs, screenshots, and UX review

The built-in demo mode is the canonical no-hardware environment for documentation work and most UI validation:

```bash
DEMO_MODE=true python sendspin_client.py
```

Open `http://127.0.0.1:8080/` after startup. Demo mode ships a stable nine-player stand with:

- mixed playing / idle / disconnected states
- MA-connected metadata and album art
- realistic diagnostics and logs
- deterministic onboarding/recovery surfaces for repeatable captures

Use it for:

- dashboard and configuration screenshots
- onboarding / recovery / bug-report UI review
- docs-site screenshot refreshes tracked in `docs-site/src/assets/SCREENSHOTS_TO_RETAKE.md`

Still use a non-demo environment for:

- auth/login screenshots
- HA-only addon configuration captures
- anything that depends on real Ingress or Supervisor behavior

---

## Docker & HA Addon Architecture

The single `Dockerfile` builds published multi-arch images for `linux/amd64` and `linux/arm64` on every release channel, while `linux/arm/v7` is published only for stable releases.

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
- `state.py` plus the newer lifecycle/read-side services publish startup progress, snapshots, update info, onboarding guidance, recovery guidance, operator guidance, and SSE payloads to the UI/API.
- Treat `/api/diagnostics`, `/api/bridge/telemetry`, `/api/hooks`, `/api/onboarding/assistant`, `/api/recovery/assistant`, and `/api/operator/guidance` as operator-facing contracts when updating docs, support flows, or dashboard UI.
- `DEMO_MODE=true` swaps the hardware/runtime layers for deterministic mocks; use it when you need repeatable screenshot states or to exercise guidance/diagnostics flows without Bluetooth hardware.

---

## Release Workflow & Generated Add-on Variants

- `VERSION` file in the repo root is the **single source of truth** for the project version.
- `CHANGELOG.md` is the release notes source of truth. Put user-facing release notes there first.
- **To release:** edit `VERSION` (e.g. `2.49.0-rc.9`), update `CHANGELOG.md`, commit and push to `main`. The `release.yml` workflow handles everything else automatically:
  1. Validates version format, runs lint + pytest
  2. Updates `config.py` VERSION/BUILD_DATE, commits, creates `v{version}` tag
  3. Builds Docker images (amd64 + arm64), pushes to ghcr.io
  4. Syncs `ha-addon*/` directories via `generate_ha_addon_variants.py`
  5. Creates GitHub Release (stable only), builds armv7 (stable only)
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
- Prefer the web UI **Submit bug report** flow when possible; it downloads masked diagnostics and pre-fills the GitHub issue body from the current diagnostics and recovery state.
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

- Edit `VERSION` file with the new version (e.g. `2.49.0`, `2.49.0-rc.9`, `2.50.0-beta.1`).
- Move entries from `[Unreleased]` to the new version section in `CHANGELOG.md`.
- Validate locally with `python3 -m pytest -q && node --check static/app.js && git --no-pager diff --check`.
- Validate docs changes with `cd docs-site && npm run build`.
- Commit `VERSION` + `CHANGELOG.md` and push to `main`. CI handles: config.py update, tagging, Docker build, addon sync, GitHub Release (stable), armv7 (stable).
- GitHub Release notes are composed from the matching `CHANGELOG.md` section; GitHub-generated notes are supplemental only.
