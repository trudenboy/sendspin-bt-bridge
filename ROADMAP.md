# Roadmap

> **Last updated: March 2026 (v2.52.1)**

## Status legend

| Symbol | Meaning |
|--------|---------|
| ✅ ~~strikethrough~~ | Completed — shipped and in production |
| 🔄 | Partially implemented — foundation exists, scope described below |
| *(unmarked)* | Not yet implemented — planned for a future phase |

---

## Purpose

This roadmap is written for the **v3 wave**, starting from the reality already shipped in `v2.52.x`.

v3 should be treated as a **compatibility-preserving platform refresh**:

- **not** a from-scratch rewrite
- **not** a promise to change only tiny islands forever
- a deliberate chance to modernize architecture, backend contracts, and operator-facing UI without regressing Bluetooth reliability

The project already has:

- ✅ ~~explicit bridge lifecycle and orchestration seams~~ (`bridge_orchestrator.py`, `services/lifecycle_state.py`)
- ✅ ~~typed status and diagnostics read models~~ (`services/status_snapshot.py`, `services/bridge_state_model.py`)
- ✅ ~~normalized onboarding, recovery, and operator-guidance surfaces~~ (`services/onboarding_assistant.py`, `services/recovery_assistant.py`, `services/operator_guidance.py`)
- ✅ ~~config migration and validation flows~~ (`config_migration.py`, `services/config_validation.py`)
- ✅ ~~Home Assistant and Music Assistant integration as part of the normal product path~~ (`services/ma_client.py`, `services/ma_monitor.py`, `routes/ma_auth.py`, `routes/ma_groups.py`, `routes/ma_playback.py`)
- ✅ ~~Docker and Raspberry Pi startup diagnostics for real deployment environments~~ (`services/preflight_status.py`)
- ✅ ~~versioned subprocess IPC and stable operator-facing diagnostics endpoints~~ (`services/ipc_protocol.py`, `services/subprocess_ipc.py`, `routes/api_status.py`)
- 🔄 room, readiness, and handoff foundations for room-following scenarios — MA groups exist but no bridge-native room assignment

The roadmap should therefore answer a sharper question:

- which deeper seams should be replaced so v3 is easier to grow
- how to make wired and USB support feel like a real product wave instead of an experiment
- how to turn the UI into a modern operator console without discarding the deployment realities that already work

## Priority adjustment for v3

The first major v3 expansion is still **wired and USB audio support**, but it should no longer be treated as a backend-only follow-up.

The new sequencing is:

1. treat the operator polish wave as shipped baseline, not the primary active phase
2. launch v3 as a coordinated three-track program across architecture, backend, and frontend
3. ship the first multi-backend product wave as **shared platform contracts + wired/USB runtime + modern operator console**
4. make observability, signal path, and delay intelligence first-class before pushing hard into AI or fleet management
5. expand into AI-assisted support and multi-bridge control only after the single-bridge, multi-backend story feels coherent

Bluetooth still remains the primary and most battle-tested runtime. v3 should widen the product around that core, not demote it.

## Product thesis for v3

Sendspin BT Bridge v3 should become a **Bluetooth-first, room-aware, multi-backend audio platform with a modern operator console**.

That means keeping Bluetooth reliability as the core product while adding five major capabilities on top:

1. **A shared platform layer** with explicit backend contracts, capability modeling, config/runtime separation, and event history
2. **USB DAC and wired audio support** as the first adjacent backend, followed by virtual sink composition
3. **Observability-first operations** with signal-path visibility, health summaries, recovery timelines, and delay intelligence
4. **A modern operator console** for creation, diagnostics, history, and bulk operations
5. **AI-assisted diagnostics and later fleet management** built on the same typed data model rather than separate one-off logic

## Shipped foundations from v2 (baseline for v3)

The roadmap treats the following as already-established and shipped foundations. These are not aspirational — they are real, tested, and running in production.

### Operator Guidance & Recovery

- ✅ ~~Operator guidance surface~~ (`services/operator_guidance.py`, `services/guidance_issue_registry.py`)
- ✅ ~~Recovery assistant with actionable remediation~~ (`services/recovery_assistant.py`)
- ✅ ~~Onboarding assistant with phased checklist~~ (`services/onboarding_assistant.py`)
- ✅ ~~Device health tracking and capability availability~~ (`services/device_health_state.py`, `services/playback_health.py`)
- ✅ ~~Diagnostics API and status endpoints~~ (`routes/api_status.py`, `/api/diagnostics`, `/api/status/*`)
- ✅ ~~Bug report with GitHub issue proxy~~ (`routes/api_status.py`, `services/github_issue_proxy.py`)
- ✅ ~~Recovery timeline builder~~ (`services/recovery_timeline.py`)
- ✅ ~~Operator check runner~~ (`services/operator_check_runner.py`)
- ✅ ~~Log analysis and severity classification~~ (`services/log_analysis.py`)

### Device & Bridge Management

- ✅ ~~Device registry with duplicate detection~~ (`services/device_registry.py`, `services/duplicate_device_check.py`)
- ✅ ~~Bridge lifecycle orchestration~~ (`bridge_orchestrator.py`, `services/bridge_daemon.py`)
- ✅ ~~Bridge state model and snapshots~~ (`services/bridge_state_model.py`, `services/status_snapshot.py`)
- ✅ ~~Lifecycle state management~~ (`services/lifecycle_state.py`)
- ✅ ~~Device pairing, connection, and reconnect~~ (`services/bluetooth.py`, `routes/api_bt.py`, `bluetooth_manager.py`)
- ✅ ~~Status event builder~~ (`services/status_event_builder.py`)
- ✅ ~~Bridge runtime state publisher~~ (`services/bridge_runtime_state.py`)

### Music Assistant Integration

- ✅ ~~MA client and monitor~~ (`services/ma_client.py`, `services/ma_monitor.py`)
- ✅ ~~MA discovery via mDNS~~ (`services/ma_discovery.py`)
- ✅ ~~MA groups and sync~~ (`routes/ma_groups.py`)
- ✅ ~~Now-playing and queue control~~ (`routes/ma_playback.py`)
- ✅ ~~HMAC-signed artwork proxy~~ (`services/ma_artwork.py`)
- ✅ ~~OAuth/token auth for MA~~ (`routes/ma_auth.py`)
- ✅ ~~WebSocket MA connection for real-time state~~ (`services/ma_monitor.py`)
- ✅ ~~MA runtime state management~~ (`services/ma_runtime_state.py`)
- ✅ ~~MA integration service bootstrap~~ (`services/ma_integration_service.py`)

### Audio Control & Transport

- ✅ ~~Native transport control with lower latency~~ (`routes/api_transport.py`)
- ✅ ~~Standby/idle disconnect~~ (`sendspin_client.py`)
- ✅ ~~PA volume controller~~ (`services/pa_volume_controller.py`)
- ✅ ~~Static delay compensation per device~~ (per-device config)
- ✅ ~~Multi-pattern audio sink discovery~~ (`bluetooth_manager.py` — PipeWire, PulseAudio, HAOS patterns)

### Infrastructure & Observability

- ✅ ~~Event hooks/webhooks with delivery history~~ (`services/event_hooks.py`)
- ✅ ~~Internal events pub/sub~~ (`services/internal_events.py`)
- ✅ ~~IPC protocol versioning~~ (`services/ipc_protocol.py`, `services/subprocess_ipc.py`)
- ✅ ~~Subprocess management~~ (`services/daemon_process.py`, `services/subprocess_command.py`, `services/subprocess_stop.py`, `services/subprocess_stderr.py`)
- ✅ ~~HA Core API integration~~ (`services/ha_core_api.py`)
- ✅ ~~Sendspin compatibility layer~~ (`services/sendspin_compat.py`)
- ✅ ~~Update checker with auto-update support~~ (`services/update_checker.py`)
- ✅ ~~Async job state with TTL eviction~~ (`services/async_job_state.py`)
- ✅ ~~Adapter name cache~~ (`services/adapter_names.py`)
- ✅ ~~Preflight diagnostics~~ (`services/preflight_status.py`)
- ✅ ~~HA addon detection and delivery channels~~ (`services/ha_addon.py`)

### Configuration & Validation

- ✅ ~~Config validation and normalization~~ (`services/config_validation.py`)
- ✅ ~~Config migration with schema versioning~~ (`config_migration.py`)
- ✅ ~~Config persistence with thread-safe locking~~ (`config.py`)
- ✅ ~~Config auth (PBKDF2-SHA256, brute-force protection)~~ (`config_auth.py`, `routes/auth.py`)
- ✅ ~~Config network (port resolution, HA addon detection)~~ (`config_network.py`)

### Deployment & Release

- ✅ ~~HA Addon with stable, beta, and RC channels~~ (`ha-addon/`, `ha-addon-beta/`, `ha-addon-rc/`)
- ✅ ~~Docker multi-arch builds (amd64, arm64, armv7)~~
- ✅ ~~LXC deployment support~~ (`lxc/`)
- ✅ ~~Landing page~~ (`landing/`)
- ✅ ~~Documentation site with Astro Starlight~~ (`docs-site/`)
- ✅ ~~Stats dashboard~~ (`docs-site/src/pages/stats/`)
- ✅ ~~CI/CD unified pipeline with beta branch support~~ (`release.yml`)
- ✅ ~~Beta branch release workflow~~

### Testing & Quality

- ✅ ~~965+ tests across 68+ files~~
- ✅ ~~Type safety with dataclasses throughout~~

---

## Three coordinated tracks for v3

v3 should not be framed as "backend work with frontend enablement on the side". It should run as three coordinated tracks that land together in each major phase.

### Track A. Architecture and platform contracts

This track gives v3 its long-term shape:

- define a real `AudioBackend`-style contract instead of letting Bluetooth remain the implicit model
- make capability modeling explicit in APIs and snapshots
- shrink `state.py` toward a compatibility and cache layer instead of the architectural center
- separate user-owned config, runtime state, and derived metadata more aggressively
- standardize on typed read models and a lightweight internal event model
- keep simulator and mock runtime support first-class so backend and UI work stay hardware-light

### Track B. Backend and runtime expansion

This track turns v3 into a genuine multi-backend runtime:

- preserve Bluetooth as the primary runtime and reliability benchmark
- add wired and USB outputs as first-class player types, not special cases
- expose virtual sinks and composed zones once the adjacent backend story is real
- carry route ownership, health, and signal-path visibility across backend types
- make delay and sync tooling backend-aware instead of Bluetooth-only

### Track C. Frontend and operator console

This track turns the current web UI into a clearer operational product:

- evolve from a monolithic runtime script toward typed feature modules and shared UI primitives
- use **Vue 3 + TypeScript + Vite** for new or replaced high-churn surfaces
- keep server-driven entry points, ingress compatibility, and fetch/SSE contracts where they still help
- build a real operator console around creation flows, diagnostics, details drawers, timelines, and bulk actions
- allow replacement of whole high-churn screens when that produces a cleaner product than endless incremental patching

### Track D. Management CLI (`sbb`)

This track adds a terminal-first operator interface alongside the web UI:

- a standalone CLI tool (`sbb`) that wraps the existing REST API — no direct runtime coupling
- **Click** as the framework (already a transitive dependency via Flask, zero new deps)
- noun-verb command structure (`sbb device list`, `sbb config get`, `sbb ma groups`) following kubectl/docker conventions
- dual-mode operation: one-shot parametric commands and an interactive REPL shell (`sbb shell`) sharing the same handler functions
- `--output json|table|yaml|csv` for machine-readable and human-friendly output
- shell completion for bash, zsh, and fish generated from Click command definitions
- config discovery chain: CLI flag → `SBB_URL` env var → `~/.config/sbb/config.toml` → default `http://localhost:8080`
- can be installed separately from the bridge (e.g. on a laptop managing a remote instance via `pip install sbb-cli`)
- **rich** as an optional soft-dependency for styled terminal tables; plain-text fallback when absent
- **prompt_toolkit** for the interactive REPL with persistent history and dynamic MAC/adapter auto-completion

The CLI does **not** replace the web UI — it complements it for SSH-only access, scripting, CI/CD automation, and power-user workflows.

## North-star outcomes

v3 is successful when the project can do all of the following without becoming fragile or opaque:

1. A single bridge is boring and reliable in HA, Docker, Raspberry Pi, and LXC environments.
2. The same bridge can host Bluetooth players and at least one wired or USB-backed player type with a coherent operator UX.
3. Operators can create, diagnose, tune, and recover players from a modern console instead of stitching together many ad hoc UI surfaces.
4. Signal path, route ownership, health, and event history are visible enough that problems are discovered by the UI before they are discovered by ear.
5. Delay tuning becomes guided and explainable rather than trial and error.
6. AI support and later fleet management can build on the same contracts, diagnostics bundles, and event history rather than inventing separate data models.
7. Bluetooth runtime is hardened with signal quality monitoring, safe BLE coexistence, hardware media button tracking, and AppArmor security — so the bridge is a good citizen alongside HA's own BT stack.

---

## Phase V3-0: Pre-v3 operator polish baseline ✅

### Status

**Complete.** All scope items shipped and running in production as of v2.52.1.

### Goal

Document the operator polish that now forms the calm starting surface for v3.

### Scope

- ✅ ~~keep full onboarding dominant only for the true empty state~~
- ✅ ~~preview and confirm grouped recovery actions before multi-device operations run~~
- ✅ ~~reduce compact and mobile recovery noise (`top issue + N more`, less duplicate copy)~~
- ✅ ~~align blocked row-level hints with one top-level guidance owner~~
- ✅ ~~keep diagnostics and recovery detail available even when top-level guidance is compact~~

### Exit criteria

- ✅ ~~mature installs are calm by default~~
- ✅ ~~grouped recovery actions feel deliberate and understandable~~
- ✅ ~~top-level guidance owns the main explanation instead of duplicated microcopy~~

---

## Phase V3-1: Platform reset for v3

### Status

**Complete.** All four epics shipped as of 3.0.0-beta.14. AudioBackend ABC, Player model, config schema v2 with auto-migration, BluetoothA2dpBackend, MockAudioBackend, BackendOrchestrator, EventStore, SendspinClient integration, and operator console foundation (Vue 3 + TypeScript + 22 kit components + 10 Pinia stores + 49 test files) are all in production on the beta branch.

### Goal

Create the shared platform model for v3 and ship the first modern operator-console foundations alongside it.

### Scope

#### Epic 1. Runtime contracts and ownership seams

- ✅ Runtime contracts — `AudioBackend` ABC (`services/audio_backend.py`) with `BackendType`, `BackendCapability` enums, `BackendStatus` dataclass. `BluetoothA2dpBackend` (`services/backends/bluetooth_a2dp.py`) wraps existing `BluetoothManager`. `MockAudioBackend` (`services/backends/mock_backend.py`) for hardware-free testing. `create_backend()` factory (`services/backends/__init__.py`).
- ✅ `BackendOrchestrator` (`services/backend_orchestrator.py`) — per-player backend lifecycle management with event integration, fully wired at runtime
- ✅ `SendspinClient` AudioBackend integration — `audio_backend` property, `audio_destination`, `backend_connect()`/`backend_disconnect()`
- ✅ ~~Reduce `state.py`~~ — transitioned from logic owner to service locator/event hub pattern (699 lines). Explicit ownership delegated to `BackendOrchestrator`, `DeviceRegistry`, `EventStore`, `player_model.Player`. SSE signaling, scan jobs, and MA cache remain in `state.py` by design as shared infrastructure.

#### Epic 2. Config and runtime model v2

- ✅ Config schema v2 — `CONFIG_SCHEMA_VERSION=2` with `players[]` array alongside `BLUETOOTH_DEVICES[]`. Auto-migration v1→v2 in `config_migration.py`. Config validation extended for `players[]` entries.
- ✅ Player model — `Player` dataclass with `from_config()` supporting v1/v2 config formats, `PlayerState` lifecycle enum (`services/player_model.py`)
- ✅ `persist_device_enabled`/`persist_device_released` now sync both `BLUETOOTH_DEVICES[]` and `players[]`
- ✅ `device_registry.py` extended — `find_client_by_player_id()`, `client_map_by_player_id()`, `find_clients_by_backend_type()`

#### Epic 3. Event model, read models, and simulator foundation

- ✅ Event model — `EventStore` (`services/event_store.py`): thread-safe ring buffer for per-player and bridge-wide event history, wired to `InternalEventPublisher` singleton in `state.py`, queryable via `/api/events` and `/api/events/stats`
- ✅ `MockAudioBackend` (`services/backends/mock_backend.py`) — hardware-free test backend with configurable failures, enabling simulator and contract testing paths
- ✅ ~~Typed snapshots~~ — `DeviceSnapshot` enriched with `backend_info`, `player_state`, `health_summary`, `capabilities`, `recent_events`. `BridgeSnapshot` enriched with `orchestrator_summary`.
- ✅ ~~Hardware-light tests~~ — `test_mock_backend.py`, `test_v3_integration.py`, `test_backend_factory.py`, `test_sendspin_client_backend.py` validate backend contracts without hardware

#### Epic 4. Operator console foundation

- ✅ ~~Vue 3 + TypeScript + Vite~~ — `ui/` directory with Vue 3.5, TypeScript 5.9, Vite 8.0, Vue Router, Pinia, Tailwind CSS 4.2
- ✅ ~~Typed frontend models~~ — `ui/src/api/types.ts` mirrors Python dataclasses: `BridgeSnapshot`, `DeviceSnapshot`, `BackendType`, `PlayerState`, `BackendStatus`
- ✅ ~~Shared design tokens and primitives~~ — 22 `Sb*` kit components (`ui/src/kit/`): `SbDialog`, `SbDrawer`, `SbTable`, `SbFilterBar`, `SbTabs`, `SbTimeline`, `SbToast`, `SbBadge`, `SbCard`, `SbButton`, `SbInput`, `SbToggle`, `SbSlider`, `SbDropdown`, `SbSpinner`, `SbStatusDot`, `SbTooltip`, `SbEmptyState`, `SbSignalPath`
- ✅ ~~Flask-rendered entry points with SPA fallback~~ — `routes/views.py` serves Vue SPA from `ui/dist/` with automatic fallback to legacy Jinja template when Vue build is absent. Adaptive CSP headers for each mode.
- 10 Pinia stores (auth, bluetooth, bridge, config, devices, diagnostics, events, ma, notifications, update), 30+ feature components, 49 test files

### Exit criteria

- ✅ the runtime can describe backend-neutral players and explicit capabilities
- ✅ config/runtime separation is real enough to support future backends cleanly
- ✅ event history and typed read models are usable by diagnostics and UI layers
- ✅ key backend and UI flows can be validated without requiring real Bluetooth hardware
- ✅ the project has a viable modern-console foundation with 22 kit components, typed stores, and Flask SPA fallback

---

## Phase V3-1.5: Bluetooth runtime hardening

### Goal

Harden the Bluetooth runtime with improvements inspired by community projects (ha-bluetooth-audio-manager, Multi-SendSpin-Player-Container) before expanding to new backend types.

### Scope

#### Epic 4a. Infrasound keep-alive method

Our existing keep-alive sends 500 ms PCM silence bursts via `paplay` at configurable intervals. Some Bluetooth speakers detect PCM zeros as silence and still auto-sleep between bursts.

- add an **infrasound** keep-alive method that streams a continuous 2 Hz sine wave (below 20 Hz hearing threshold) instead of periodic silence bursts
- make the method configurable per device: `keepalive_method: "silence" | "infrasound"` (default: `"silence"` for backward compatibility)
- infrasound mode streams continuously while idle (no interval gaps), silence mode keeps the existing burst behavior
- validate that the 2 Hz sine wave does not trigger PulseAudio volume normalization or flat-volume artifacts

#### Epic 4b. RSSI signal strength monitoring

- 🔄 RSSI parsing from `bluetoothctl` output exists in `routes/api_bt.py` (`_CHG_RSSI_PAT` regex) but values are used only during scan to track active MACs — not stored in `DeviceStatus`
- store `rssi_dbm` in `DeviceStatus` and `DeviceSnapshot` for connected devices
- color-code signal quality in UI (good / fair / weak / stale)
- distinguish live vs stale RSSI readings (BR/EDR-only devices cannot refresh RSSI while connected; dual-mode devices can)
- surface weak signal as a health warning in operator guidance

#### Epic 4c. AVRCP media button tracking

- monitor D-Bus MPRIS/AVRCP events per connected device for hardware media buttons (play/pause/next/previous/volume)
- sync hardware volume button presses with bridge volume state
- add per-device `avrcp_enabled` toggle (some speakers refuse to enter power-save with active AVRCP registration)
- publish AVRCP events to `EventStore` with typed event categories (MPRIS, AVRCP, Transport)

#### Epic 4d. BLE coexistence improvements

- 🔄 Bluetooth scan currently uses `bluetoothctl scan on` globally without transport filter
- add `Transport=bredr` filter for BT scan to avoid interfering with HA BLE integrations (sensors, beacons, ESPHome proxies)
- never modify adapter power, discoverable, or pairable states during scan
- reference-count discovery start/stop to coexist with other BlueZ D-Bus clients

#### Epic 4e. AppArmor security profile

- ✅ ~~AppArmor profile~~ — `ha-addon/apparmor.txt` exists with deny-by-default policy: denies raw HCI device access (`/dev/hci*`), allows `NET_ADMIN`/`NET_RAW` capabilities, D-Bus system bus, PulseAudio sockets, config/data paths, Python runtime. Enabled in `ha-addon/config.yaml`.

#### Epic 4f. Dedicated health endpoint

- ✅ ~~`GET /api/health`~~ — lightweight endpoint at `routes/api_status.py:1723` returning `{"ok": true}`, no auth required. Distinct from full `/api/diagnostics` payload.
- integrate with Docker `HEALTHCHECK` in `Dockerfile` and `docker-compose.yml`
- integrate with HA addon `watchdog` URL in `config.yaml`

### Exit criteria

- keep-alive infrasound method prevents speaker sleep on devices that ignore PCM silence
- RSSI monitoring provides signal quality visibility without requiring additional hardware
- AVRCP events are tracked and visible in the event store
- BT scan does not interfere with HA BLE integrations
- HA addon runs under AppArmor with deny-by-default policy
- health endpoint enables reliable container orchestration health checks

---

## Phase V3-2: Modern operator console and wired/USB runtime

### Goal

Ship the first clearly multi-backend product wave: wired and USB players plus the new operator workflows needed to manage them well.

### Scope

#### Epic 5. Wired and USB backend

- detect ALSA and PulseAudio or PipeWire output sinks from `pactl list sinks` and `aplay -l`
- filter and classify likely outputs such as USB DAC, built-in audio, HDMI, and virtual sinks
- create a direct-sink player type that can reuse the subprocess model, status reporting, volume control, and diagnostics patterns without Bluetooth pairing lifecycle
- support per-device volume persistence, mute state, and backend-specific health reporting
- add per-device **max volume** safety limit (especially important for amplified wired outputs)
- 🔄 per-device **boot mute** — partially exists: `_startup_unmute_watcher` in `daemon_process.py` unmutes after stream starts (15 s timeout), but not configurable per-device and applies to BT only
- support **device aliases** — human-readable names for physical USB/HDMI audio devices independent of PulseAudio sink names
- add per-card **profile selection** (e.g. `output:analog-stereo`, `output:hdmi-stereo`) configurable from the UI

> **Note:** `BackendType.LOCAL_SINK` is already defined in `services/audio_backend.py` but `create_backend()` raises `ValueError` — planned, not yet implemented.

#### Epic 6. Capability-driven player management UX

- replace the highest-churn device-management flows with a backend-aware creation and edit experience
- add typed forms, validation, and room or alias mapping for Bluetooth and wired players
- show discovered hardware with backend type, friendly naming, and capability hints
- use clearer overview plus details-drawer patterns instead of overloading one monolithic page surface

#### Epic 7. Hotplug and route lifecycle

- watch for wired and USB device appearance or disappearance
- notify the UI when a new sink becomes available, changes identity, or disappears
- optionally allow operator-approved player creation for newly detected USB DACs
- surface route ownership and sink disappearance issues explicitly in the new console instead of burying them in logs

#### Epic 8. Management CLI foundation (`sbb`)

> **Note:** `sendspin-cli/` is a legacy standalone Sendspin audio player client (argparse-based), **not** the bridge management CLI described here. The `sbb` CLI will be a new `sbb_cli/` package.

- scaffold `sbb_cli/` package with Click-based grouped subcommands
- implement `BridgeClient` HTTP wrapper for REST API communication with timeout, auth, and structured error mapping
- deliver core command groups: `device` (list, info, scan, pair, remove, connect, disconnect, enable, disable, wake, standby), `adapter` (list, power, scan), `config` (show, get, set, export, import, validate), `status` (show, health, groups), `logs` (show, follow, download), `diag` (preflight, runtime, bugreport, recovery), `ma` (discover, groups, nowplaying, login), `update` (check, apply)
- add top-level shortcuts: `volume`, `mute`, `restart`
- implement `--output json|table|yaml|csv` formatting with rich as an optional soft-dependency
- config discovery: CLI flag → `SBB_URL` env var → `~/.config/sbb/config.toml` → default
- generate shell completion scripts for bash, zsh, and fish via `sbb completion`
- support SSE streaming for `status show --watch` and `logs follow`
- implement `sbb shell` interactive REPL with prompt_toolkit, persistent history, and dynamic MAC/adapter auto-completion
- publish to PyPI as `sbb-cli` for standalone installation

### Exit criteria

- USB DACs and wired outputs appear in the UI alongside Bluetooth speakers as first-class player shapes
- operators can create and manage wired players through the new operator workflows rather than raw config edits
- Bluetooth and wired players share one capability-driven model without regressing Bluetooth reliability
- the modern console is now responsible for the highest-churn player-management paths
- `sbb` CLI can list devices, show status, manage config, and run diagnostics from a terminal without requiring a browser

---

## Phase V3-2.5: Virtual sinks and composed zones

### Goal

Turn PulseAudio virtual sinks into real product surfaces once the first multi-backend model is live.

### Scope

#### Epic 9. Combine sink creation

- add operator flows to select 2+ sinks and create a `module-combine-sink`
- target party mode, open floor plans, and lightweight multi-room grouping scenarios
- include a test-tone or route verification action

#### Epic 10. Remap sink creation

- add operator flows to extract channels from multi-channel devices via `module-remap-sink`
- target split-zone scenarios such as a 4-channel USB DAC becoming two stereo zones
- support standard PulseAudio channel-name mapping and clear channel previews

#### Epic 11. Composed-zone lifecycle management

- persist custom sinks in config and recreate them on restart
- expose state, configuration summary, capability surface, and delete actions
- validate master and slave sink existence before attempting creation
- let virtual sinks participate in player creation and room assignment flows

### Exit criteria

- operators can create combine and remap sinks without touching `pactl` directly
- composed zones survive restarts and fit naturally into player-management flows
- failures are explicit when prerequisite sinks are unavailable

---

## Phase V3-3: Observability-first runtime and operations center

### Goal

Make health, signal path, and recovery state first-class operator surfaces rather than advanced diagnostics hidden behind logs.

### Scope

#### Epic 12. Live telemetry and degraded-mode summaries

- expose current codec, sample rate, buffer and stream state, uptime, reconnect count, and resolved output sink where available
- include RSSI signal strength telemetry for Bluetooth devices (from Epic 4b) in live telemetry views
- pull telemetry from subprocess status lines, bridge state, backend callbacks, and event history
- include structured per-device event history such as reconnects, sink loss or acquisition, route corrections, re-anchor events, and MA sync failures
- include AVRCP media button events (from Epic 4c) in the device event timeline
- publish compact degraded-mode and health-summary surfaces in addition to raw live status

#### Epic 13. Signal path and route ownership visibility

- 🔄 Signal path visibility — device health state and capability modeling exist (`services/device_health_state.py`), `SbSignalPath` Vue kit component exists in `ui/src/kit/` but is NOT wired to live backend data yet
- render the end-to-end path for each backend type:
  - MA → Sendspin → subprocess → PulseAudio or PipeWire sink → Bluetooth A2DP → speaker
  - MA → Sendspin → subprocess → PulseAudio or ALSA sink → wired speaker or DAC
- show measured or estimated latency at each hop where available
- indicate route ownership, bottlenecks, or degraded hops such as codec fallback, sink mismatch, or missing route ownership

#### Epic 14. Operations center and reusable UI system

- build a unified diagnostics and recovery center instead of scattering operational detail across many unrelated UI sections
- add a frontend operation model that can present live state, pending actions, recovery history, and bulk actions without duplicating business logic across cards, rows, dialogs, and modals
- ✅ ~~UI component system~~ — 22 `Sb*` kit components shipped in `ui/src/kit/`: badges, toasts, drawers, dialogs, filters, timeline, signal-path, table, tabs, tooltip, empty-state, and mobile-friendly primitives. Remaining: integrate operations center views that use these components for combined diagnostics/recovery/event-history UX.
- favor split-pane, drawer, and progressive-disclosure patterns that scale on desktop and mobile better than endlessly expanding rows

### Exit criteria

- operators can see codec, sample rate, sink route, health, and event history without reading logs
- the signal path is understandable at a glance for Bluetooth, wired, and virtual-sink players
- degradation is surfaced proactively instead of discovered only after audio sounds wrong
- the UI has a reusable operations vocabulary instead of repeatedly hand-assembling each diagnostic surface

---

## Phase V3-4: Delay intelligence and guided tuning

### Goal

Reduce manual `static_delay_ms` guesswork and make sync decisions more measurable, guided, and explainable.

### Scope

#### Epic 15. Delay telemetry foundation

- 🔄 Latency telemetry — per-device `static_delay_ms` configuration exists but no measured per-hop timing or drift telemetry
- capture timing and drift telemetry that can support per-device delay decisions
- expose sync health, drift, confidence, and measurement quality at the diagnostics and operator level
- distinguish between "we can measure something" and "we trust this enough to recommend a tuning change"

#### Epic 16. Guided delay calibration

- add a calibration flow that can measure and suggest `static_delay_ms`
- show recommended value, confidence, and before/after comparison
- allow approve, apply, and rollback instead of forcing raw manual edits

#### Epic 17. Bounded auto-tuning

- add optional conservative automatic adjustment for devices with stable measurement quality
- keep adjustments bounded, visible, and reversible
- surface when auto-tuning is disabled, uncertain, or recently rolled back

### Exit criteria

- most users can reach a good delay value without trial-and-error editing
- delay recommendations are visible and explainable
- any automatic tuning stays conservative and operator-traceable

---

## Phase V3-5: AI-assisted diagnostics and deployment planning

### Goal

Use AI as an **operator copilot**, not as a hidden control plane.

### Scope

#### Epic 18. Structured diagnostics bundles

- define a canonical machine-readable diagnostics bundle that combines:
  - bridge and runtime state
  - device snapshots
  - recovery timeline
  - deployment environment facts
  - preflight results
  - backend identity and routing facts
- make the bundle stable enough for support tooling, bug reports, and future AI consumers

#### Epic 19. Deployment planner

- add a planner that can inspect environment facts and suggest:
  - recommended install path (HA add-on, Docker, Raspberry Pi, LXC)
  - required mounts and capabilities
  - likely `AUDIO_UID`, port, and adapter configuration
  - when wired or USB outputs are a better fit than Bluetooth for a room
  - safe next steps for first deployment
- keep the planner operator-facing: generate plans and config suggestions, not silent changes

#### Epic 20. AI diagnostics summarizer

- summarize failures in plain language from diagnostics data
- rank likely root causes and safe next actions
- produce support-ready summaries for GitHub or forum issues
- allow prompt export or support bundle export for external or local AI analysis
- present AI summaries in a way that preserves operator trust:
  - explicit provenance from diagnostics data
  - visible confidence and uncertainty
  - one-click access to the underlying raw diagnostics and event history

#### Epic 21. AI safety and privacy boundaries

- redact secrets before any external AI handoff
- support pluggable providers and a local or manual mode
- require explicit operator approval before applying suggested changes
- keep non-AI diagnostics fully usable on their own
- keep AI summaries built on the same typed diagnostics, capability, and event-history models used by non-AI tooling and the operator console

### Exit criteria

- diagnostics bundles are stable and structured
- deployment planning is useful for real users, especially HA, Docker, Raspberry Pi, and mixed Bluetooth or wired installs
- AI-generated explanations improve support without becoming required for normal operation

---

## Phase V3-6: Centralized multi-bridge control plane

### Goal

Turn multiple bridge instances into a manageable fleet after the single-bridge multi-backend product and modern operator console are solid.

### Scope

#### Epic 22. Bridge registry and fleet identity

- define stable bridge instance identity and registration semantics
- aggregate version, host, adapter, room, backend, and health metadata across bridges
- detect duplicate speakers, overlapping rooms, and inconsistent bridge naming

#### Epic 23. Fleet overview and bulk operations

- build a centralized overview for:
  - bridge health
  - device inventory
  - room coverage
  - recovery attention
  - update status
- add safe bulk actions such as:
  - restart selected bridges
  - re-run diagnostics on selected bridges
  - export and import configuration sets
  - compare configs and versions across the fleet

#### Epic 24. Fleet event timeline and policy surfaces

- centralize event and recovery timelines across bridges
- add fleet-level webhook and telemetry views
- allow higher-level policies such as room ownership or update-channel consistency
- reuse the same lightweight internal event model and hardened hook/webhook contracts instead of introducing separate fleet-only event semantics

### Exit criteria

- operators can reason about multiple bridges as one system
- duplicate or conflicting configuration becomes easier to spot before it causes runtime issues
- fleet operations do not replace single-bridge simplicity; they extend it

---

## Phase V3-7: Selective expansion after core stability

### Candidate work

Only start these once earlier phases are stable and demand is proven:

- system-wide audio runtime or non-user-scoped socket support for Raspberry Pi and other embedded hosts that struggle with per-user PulseAudio or PipeWire sessions
- richer sync and drift telemetry across groups and bridges
- Snapcast, VBAN, or other backend strategy tracks
- multi-bridge federation beyond a single control plane
- Home Assistant custom component or HACS strategy
- plugin or extension surfaces
- per-room DSP and EQ via virtual sinks or backend-specific processing surfaces
- **12V trigger / amplifier power control** — automatic amplifier on/off via USB relay boards (HID, FTDI, Modbus/CH340) tied to player lifecycle events. Alternatively, expose player lifecycle webhooks for HA automations controlling amplifier switches via ESPHome/Zigbee/Z-Wave entities. Inspired by chrisuthe/Multi-SendSpin-Player-Container.
- **OpenAPI / Swagger specification** — auto-generated API documentation for the 28+ REST endpoints, improving third-party integration and CLI development

---

## Cross-cutting guardrails

### 1. Bluetooth reliability stays first

No v3 theme should regress real Bluetooth deployments in HA, Docker, Raspberry Pi, or LXC.

### 2. v3 is a compatibility-preserving platform refresh

Preserve operator trust, ingress compatibility, and stable contracts where they already work, but allow deeper replacement of runtime seams and high-churn UI surfaces when that produces a cleaner v3 foundation.

### 3. Wired and USB support must stay additive

The first adjacent backend should reuse proven runtime seams, diagnostics, and subprocess patterns rather than replacing them wholesale.

### 4. Architecture must stay ahead of product sprawl

Backend expansion, AI summaries, and fleet views should build on explicit services, typed read models, capability modeling, event history, and hardware-light testability rather than bypassing those foundations.

### 5. Frontend modernization may replace high-churn surfaces

New frontend infrastructure should reduce complexity, improve accessibility, and make operational workflows clearer. It does not need to stop at tiny islands if replacing a high-churn screen is the cleaner path.

### 6. AI must be optional and operator-controlled

- no mandatory cloud dependency
- no silent external sharing of sensitive config or state
- no hidden auto-remediation without explicit approval

### 7. Delay automation must be bounded and explainable

The system may suggest or conservatively auto-apply delay changes, but never as opaque magic.

### 8. Fleet management must stay additive

A single bridge should remain simple to deploy and operate. Fleet management should not become a requirement for basic use.

### 9. Migrations, docs, and tests must ship with each phase

v3 should add compatibility layers, migrate callers and config gradually, document new contracts as they land, and extend tests alongside each new backend or diagnostics surface.

### 10. CLI must stay a pure HTTP client

The `sbb` CLI must never import bridge runtime code or depend on Bluetooth, PulseAudio, or D-Bus libraries. It communicates exclusively through the REST API so it can be installed on any machine, including developer laptops without audio hardware.

### 11. Bluetooth operations must not interfere with HA BLE

All Bluetooth discovery and connection operations must use Classic Bluetooth (BR/EDR) transport only. The bridge must never disrupt HA's BLE scanning for sensors, beacons, or ESPHome proxies. Adapter power, discoverable, and pairable states must not be modified by scan operations.

---

## Execution and dependency notes

The roadmap phases above are product-facing, but the safest implementation order inside them should still respect a few program-level dependencies:

- move the three tracks together in release waves instead of treating frontend as late enablement after backend work is done
- land backend contracts, event history, and config/runtime separation before AI or fleet features depend on them
- keep simulator and mock-runtime improvements close to backend and UI changes so new flows stay testable without hardware
- use the same event contracts for diagnostics, hooks, operator timelines, and future fleet views
- replace high-churn surfaces first: player creation and editing, details views, diagnostics, and history should move before already-stable pages are rewritten for their own sake
- let virtual sinks and later fleet views build on the same capability and read-model surfaces used by Bluetooth and wired players

## Out of scope for early v3

- a giant all-at-once rewrite of every layer
- speculative backends before the backend contract exists
- AI-driven silent configuration edits
- fleet-first complexity before the single-bridge multi-backend story is proven
- replacing operator diagnostics with AI instead of complementing them
- abandoning deployment compatibility realities like HA ingress without a demonstrably better operator path

## Likely first v3 milestone

A realistic `v3.0.0-rc.1` should include:

- ✅ V3-0 already-landed guidance and recovery polish as the baseline
- ✅ V3-1 (complete as of beta.14):
  - backend contracts and capability modeling
  - config and runtime model v2 foundations
  - event-history and simulator foundations
  - operator-console foundation (Vue 3 + 22 kit components + 10 stores + 49 tests)
- the core of V3-1.5:
  - infrasound keep-alive method for Bluetooth speakers
  - RSSI signal quality monitoring (parsing exists, storage/UI needed)
  - ✅ AppArmor security profile for HA addon
  - ✅ dedicated health endpoint (Docker/HA integration remaining)
- the core of V3-2:
  - the first wired and USB backend with max volume, boot mute, device aliases, and card profiles
  - backend-aware player creation and editing flows
  - the first new diagnostics and details surfaces in the operator console
  - `sbb` CLI with core device, config, status, and diagnostics commands
- baseline audio health visibility and signal-path publication
- delay telemetry foundations and a manual calibration path
- structured diagnostics bundle foundations for future planner and AI work

That is enough to make v3 feel materially different: **"Bluetooth-first multiroom with a real multi-backend platform, a modern operator console, and much clearer audio visibility"**.
