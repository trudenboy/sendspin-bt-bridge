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

## Done since v2.41.x

- [x] **Unified operator guidance + onboarding hierarchy** — normalized bridge/device state now drives checklist onboarding, operator guidance, blocked-action explanations, and recovery summaries from one shared model.
- [x] **Standalone/LXC update flow** — version check, release-ref persistence, RC tracking, and one-click standalone update flow are implemented; the old “LXC auto-update system” item is no longer open work.
- [x] **Recovery center tooling** — rerunnable safe checks, latency recommendations/presets, chronological recovery timeline JSON/CSV export, and the known-good verification path are available in diagnostics.
- [x] **Staged onboarding flow** — onboarding now exposes a foundation → first speaker → Music Assistant → tuning journey instead of only a flat checklist.

## Next

- [ ] **Consolidate guidance ownership for non-empty installs** — keep the large onboarding checklist dominant only for the true empty state and let mature installs rely on calmer header/banner guidance with one clear next-best action owner.
- [ ] **Add grouped recovery action previews** — grouped issue detection and batch actions already exist, but the UI should preview affected devices and confirm bulk recovery intent before running multi-device actions.
- [ ] **Polish compact/mobile recovery density** — add calmer `top issue + N more` issue pills and keep recovery actions readable when multiple warnings/actions compete for space.
- [ ] **Sync roadmap/TODO narrative to the shipped v2 state before v3** — these docs now lag the real feature set and should remain aligned with the RC line.

## Assessed ideas (2026-03-20)

- [x] **Warn when a BT device may already belong to another bridge** — completed. Config validation/save/upload now checks Music Assistant `players/all` using the stable MAC-derived `player_id` and shows a non-blocking warning for newly added MACs that already appear to belong to another bridge.
- [x] **Bind MA long-lived token identity to the physical bridge instance (hostname-based)** — completed. Long-lived MA tokens are now named from the current hostname, non-sensitive instance metadata is persisted, preserved across config save/upload flows, and silent auth reuse now distinguishes current-instance tokens from foreign-instance copies.
- [x] **Sync Home Assistant area name to `BRIDGE_NAME`** — completed. HA ingress sessions can now fetch Home Assistant area/device registry data, offer `BRIDGE_NAME` suggestions, and persist adapter-area mappings keyed by adapter MAC for one-click adapter naming.

## Deferred UX ideas (captured for later review, 2026-03-20)

- [ ] **Align blocked compact hints with top-level guidance** — keep the visible blocked markers, but reduce duplicate row-level warning copy once banner/header guidance owns the root-cause explanation.
- [ ] **Compact recovery pills on mobile** — collapse multiple issue pills into “top issue + N more” to reduce wrapping and improve small-screen scannability.
- [ ] **Make the known-good test path interactive** — let each step expand into concrete checks/actions instead of being read-only status guidance.
- [ ] **Group safe actions into a progressive-disclosure action menu** — reduce button crowding by keeping one primary action and tucking secondary recovery actions behind a “More options” affordance, especially for grouped recovery banners.
- [ ] **Expose latency guidance as a standalone dashboard card** when latency is the main active problem, instead of only inside diagnostics/banner summaries.
- [ ] **Add adaptive explanation depth for novice vs advanced users** — short default explanations with optional deeper technical detail for power users.
- [ ] **Show hierarchical blocking explanations** — explain not only that an action is blocked, but the dependency chain causing it.
- [x] **Expose advanced recovery views for power users** — diagnostics recovery timeline now keeps a longer retained window and exposes advanced severity/scope/source/window filters plus source-density summary chips for power-user trace review.
- [x] **Show current vs recommended latency values together** — diagnostics now surfaces current Pulse latency, recommendation, presets, and safe next-step hints.
- [x] **Allow inline latency editing from onboarding guidance** — onboarding/operator guidance can now surface the recommended latency action directly instead of forcing a detour into wider diagnostics/settings.

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
