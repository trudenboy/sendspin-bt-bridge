# TODO

Roadmap for HA addon standards compliance and improvements.

## Done (v2.15.0–v2.28.2)

- [x] **35 unit tests, diagnostics enrichment, TOCTOU fix, MA WS response matching** (v2.15.0)
- [x] **Multi-arch Docker builds** — amd64/arm64/armv7 (v2.15.2)
- [x] **Fix re-anchor loop on stream start** — sendspin-cli 5.1.4 preserves cooldown timer across `clear()` calls (v2.15.3)
- [x] **Split armv7 CI into separate workflow** — amd64/arm64 publish immediately; armv7 builds independently via QEMU (v2.15.4)
- [x] **Fix AppArmor profile** — temporarily disabled (`apparmor: false`), was blocking Python imports on HAOS (v2.15.5)
- [x] **Auto-unmute BT sink, switched to PyPI sendspin** (v2.15.6)
- [x] **Security audit** — 42 issues fixed, 65 new tests (107 total), `SYS_ADMIN` capability removed (v2.16.0)
- [x] **PyAV armv7l compatibility fix** (v2.16.1)
- [x] **RPi preflight script, `/api/preflight`, startup diagnostics, RPi & Docker docs** (v2.16.2)
- [x] **Add Hadolint config** — `.hadolint.yaml` + Dockerfile linting in CI (v2.16.3)
- [x] **Create `ha-addon/logo.png`** — wide-format logo for HA store listing (v2.16.3)
- [x] **One-liner RPi installer** — `scripts/rpi-install.sh`: install Docker, generate compose, pair BT, start (v2.16.3)
- [x] **MA auto-discovery & auto-login** — mDNS discovery of MA servers + passwordless auth via Ingress JSONRPC in addon mode (v2.17.0–v2.18.3)
- [x] **API modularization** — split `routes/api.py` into 5 modules (`api.py`, `api_bt.py`, `api_ma.py`, `api_config.py`, `api_status.py`) (v2.20.3)
- [x] **Thread-safety audit** — added locks, fixed race conditions (v2.20.3)
- [x] **Remove dead endpoint** — removed unused `/api/set_volume_device` (v2.20.3)
- [x] **Fix postMessage origin** — corrected HA Ingress iframe communication (v2.20.3)
- [x] **Fix JWT folding marker CSS** — corrected ▶/▼ display (v2.20.4)
- [x] **Fix MA API token hint text** — updated to "Create in MA → Settings → Profile → Long-lived access tokens" (v2.20.4)
- [x] **Deprecate `BLUETOOTH_MAC`** — removed legacy single-device env var, all configs use `BLUETOOTH_DEVICES[]` array (v2.21.0)
- [x] **TWS earbuds support** — D-Bus UUID filtering for audio-only profiles, automatic TWS detection and pairing flow (v2.21.0–v2.22.3)
- [x] **Music Assistant live monitor** — persistent WebSocket connection for real-time now-playing, queue state, transport controls (prev/next/shuffle/repeat), album art tooltips (v2.22.0–v2.23.0)
- [x] **Background update checker** — GitHub API version polling, update notification badge, one-click update (HA addon), manual update modal with changelog (v2.23.0–v2.24.0)
- [x] **Demo mode** — `DEMO_MODE=true` runs bridge with fully emulated hardware for screenshots and testing (v2.23.0)
- [x] **Bug report modal** — one-click diagnostic bundle with SVG icons, auto-collected system info, GitHub issue pre-fill (v2.24.0–v2.28.0)
- [x] **Connection column compaction** — 85px dots-only layout for BT/MA status, identity column restructured to 2-row layout (v2.26.0–v2.28.0)
- [x] **Two-tier enabled/disabled** — global `enabled` (requires restart, fully skips device) vs hot `bt_management_enabled` release/reclaim (v2.27.0–v2.28.0)
- [x] **Release state persistence** — `persist_device_released()` saves release state to config.json, restored on startup; `_match_player_name()` handles bridge name suffix matching (v2.28.1–v2.28.2)
- [x] **UI polish** — column labels removed, sink name removed from volume column, shuffle/repeat always visible when MA connected, progress time inline with progress bar, update modal redesign (v2.28.0–v2.28.2)

## Next

- [ ] **LXC auto-update system** — version check via GitHub API + web UI notification badge + one-click update button (see [analysis](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/TODO.md))
- [ ] **Add HA discovery integration** — support HA discovery protocol for auto-configuring MA connection

## Future

- [ ] **IPC: add ack, heartbeat, ready signal** — evolve JSON Lines protocol: message IDs + ack/nack for critical commands (`stop`, `set_volume`), `{"type": "ready"}` signal at subprocess start, heartbeat every 10s, move logs from stdout JSON to stderr
- [ ] **IPC: Unix Domain Sockets transport** — replace stdin/stdout with per-device UDS (`/tmp/sendspin-{mac}.sock`), full duplex, asyncio `open_unix_connection()`, socket cleanup on crash *(depends on: IPC ack/heartbeat)*
- [ ] **Migrate to HA Debian base images** — switch from `python:3.12-slim` to `ghcr.io/home-assistant/{arch}-base-debian:bookworm`
- [ ] **Adopt `rootfs/` overlay pattern** — move entrypoint scripts into `rootfs/etc/` structure *(depends on: base images)*
- [ ] **Merge into single Dockerfile** — eliminate two-image chain, single `ha-addon/Dockerfile` with `ARG BUILD_FROM` *(depends on: base images)*
- [ ] **Adopt S6 Overlay** — `s6-rc.d` service structure for process supervision *(depends on: base images, rootfs)*
- [ ] **Implement proper signal handling** — S6 SIGTERM handling, clean subprocess shutdown *(depends on: S6 overlay)*
- [ ] **Write proper AppArmor profile** — complain mode → audit log → tested whitelist *(depends on: S6 overlay)*
- [ ] **Web UI setup wizard** — first-run wizard: detect speakers, pair, configure MA — all from the browser
