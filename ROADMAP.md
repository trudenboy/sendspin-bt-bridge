# Roadmap

## Purpose

This roadmap is written for the **v3 wave**, starting from the reality already shipped in `v2.46.x+`.

v3 should be treated as a **compatibility-preserving platform refresh**:

- **not** a from-scratch rewrite
- **not** a promise to change only tiny islands forever
- a deliberate chance to modernize architecture, backend contracts, and operator-facing UI without regressing Bluetooth reliability

The project already has:

- explicit bridge lifecycle and orchestration seams
- typed status and diagnostics read models
- normalized onboarding, recovery, and operator-guidance surfaces
- config migration and validation flows
- room, readiness, and handoff foundations for room-aware Music Assistant scenarios
- stronger Docker and Raspberry Pi diagnostics for real deployment environments
- versioned subprocess IPC and stable operator-facing diagnostics endpoints

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

## Starting baseline from v2

The roadmap treats the following as already-established foundations:

- Bluetooth remains the primary and most battle-tested runtime
- onboarding, recovery guidance, diagnostics, and bugreport tooling are real operator-facing surfaces
- bridge and device health, recent events, and blocked-action reasoning are explicit enough to build on
- room metadata, transfer readiness, and fast-handoff profiles already exist for room-following scenarios
- Home Assistant and Music Assistant integration are part of the normal product path, not an afterthought
- Docker and Raspberry Pi startup diagnostics already surface runtime UID, audio socket path, socket ownership, and live `pactl` probe status
- subprocess IPC, config migration, and diagnostics endpoints already behave like versioned product contracts

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

## Phase V3-0: Pre-v3 operator polish baseline

### Status

Effectively complete in the current codebase. Keep this section as baseline context, not as the primary active phase.

### Goal

Document the operator polish that now forms the calm starting surface for v3.

### Scope

- keep full onboarding dominant only for the true empty state
- preview and confirm grouped recovery actions before multi-device operations run
- reduce compact and mobile recovery noise (`top issue + N more`, less duplicate copy)
- align blocked row-level hints with one top-level guidance owner
- keep diagnostics and recovery detail available even when top-level guidance is compact

### Exit criteria

- mature installs are calm by default
- grouped recovery actions feel deliberate and understandable
- top-level guidance owns the main explanation instead of duplicated microcopy

### Current assessment

These outcomes are already reflected in the shipped operator-guidance and recovery flows. Active roadmap work should therefore start at **V3-1**, not at V3-0.

## Phase V3-1: Platform reset for v3

### Goal

Create the shared platform model for v3 and ship the first modern operator-console foundations alongside it.

### Scope

#### Epic 1. Runtime contracts and ownership seams

- define an `AudioBackend`-style contract for lifecycle, capabilities, health, diagnostics, room metadata, and route ownership
- wrap the existing Bluetooth runtime behind that contract first
- keep subprocess and control-plane contracts backend-agnostic where practical
- reduce `state.py` from architectural center toward a compatibility and cache layer as routes and services move to explicit ownership and snapshot reads

#### Epic 2. Config and runtime model v2

- move from a Bluetooth-device-only model to player and backend-oriented configuration
- separate user-owned config from runtime-derived state and generated metadata
- add compatibility loading and migration tooling from the current schema
- keep downgrade and partial-migration assumptions explicit and documented

#### Epic 3. Event model, read models, and simulator foundation

- standardize on a lightweight internal event model that can feed diagnostics history, hooks, recovery timelines, and later fleet views
- make per-device and per-bridge event history a first-class typed surface rather than scattered ad hoc payloads
- broaden typed snapshots and health summaries so degraded-mode reporting is a product surface, not just a debug aid
- keep the mock runtime and simulator path viable for backend, config, diagnostics, and onboarding flows
- make hardware-light tests a normal validation path for contract work

#### Epic 4. Operator console foundation

- adopt **Vue 3 + TypeScript + Vite** for new or replaced high-churn surfaces
- build typed frontend models and stores around `BridgeSnapshot`, `DeviceSnapshot`, guidance, diagnostics, jobs, and event history
- establish shared design tokens, headless accessible primitives, and reusable drawer/dialog/filter/table patterns
- keep Flask-rendered entry points and ingress compatibility, but allow replacement of high-churn UI surfaces where a cleaner product benefits

### Exit criteria

- the runtime can describe backend-neutral players and explicit capabilities
- config/runtime separation is real enough to support future backends cleanly
- event history and typed read models are usable by diagnostics and UI layers
- key backend and UI flows can be validated without requiring real Bluetooth hardware
- the project has a viable modern-console foundation instead of only one growing runtime script

## Phase V3-2: Modern operator console and wired/USB runtime

### Goal

Ship the first clearly multi-backend product wave: wired and USB players plus the new operator workflows needed to manage them well.

### Scope

#### Epic 5. Wired and USB backend

- detect ALSA and PulseAudio or PipeWire output sinks from `pactl list sinks` and `aplay -l`
- filter and classify likely outputs such as USB DAC, built-in audio, HDMI, and virtual sinks
- create a direct-sink player type that can reuse the subprocess model, status reporting, volume control, and diagnostics patterns without Bluetooth pairing lifecycle
- support per-device volume persistence, mute state, and backend-specific health reporting

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

#### Epic 8a. Live config reload without bridge restart

- classify config fields into tiers by reload cost:
  - **Tier 0** (instant via IPC): volume, mute, log level — already supported
  - **Tier 1** (hot-reload): `BRIDGE_NAME`, `SENDSPIN_SERVER`, `SENDSPIN_PORT`, UI auth password, `keepalive_enabled`, `idle_disconnect_seconds`, `static_delay_ms` — apply in-process without subprocess restart
  - **Tier 2** (per-device subprocess restart): adapter assignment, sink name, listen port — recreate only the affected subprocess
  - **Tier 3** (full bridge restart): adding or removing devices, changing `WEB_PORT`, deep audio backend changes
- expose a `/api/config/apply` endpoint that detects what changed, applies the cheapest tier, and returns what was reloaded versus what requires restart
- show a UI indicator when pending changes require different restart levels
- persist reload tier metadata alongside config schema for programmatic introspection

#### Epic 8b. PA sink state monitoring for idle disconnect ✅ (in progress)

- replace the 3-tier idle guard fallback (daemon flags → MA monitor → event history) with PulseAudio sink state as ground truth
- subscribe to PA sink events via `pulsectl_asyncio` in the parent process — one subscription covers all devices
- extract MAC from `bluez_sink.*` / `bluez_output.*` names to route events to the correct `SendspinClient`
- fire `on_sink_active` (cancel idle timer) and `on_sink_idle` (start idle timer) callbacks on actual state transitions
- graceful fallback to daemon-flag-based timer when pulsectl is unavailable (dev machines, containers without libpulse)
- keep auto-wake system (`_check_group_auto_wake()`) separate and MA-driven

#### Epic 8c. Per-device idle mode and power save

- add per-device `idle_mode` config field with values: `default` (current idle disconnect), `power_save` (PA `suspend-sink` instead of BT disconnect), `stay_awake` (infrasound keepalive), `auto_disconnect` (aggressive short timeout)
- `power_save` mode: on idle timeout, issue `pactl suspend-sink <sink> 1` to put the BT speaker into low-power mode while keeping BT connected — wake via `pactl suspend-sink <sink> 0` on playback resume
- `stay_awake` mode: replace current silence-burst keepalive with continuous sub-20Hz sine wave (inaudible infrasound) to prevent BT speaker auto-off, inspired by scyto/ha-bluetooth-audio-manager
- expose idle mode selection in the web UI device settings and `sbb` CLI
- validate that `suspend-sink` and infrasound approaches work across PulseAudio and PipeWire

#### Epic 8d. AVRCP metadata and Bluetooth signal monitoring

- expose AVRCP track metadata (title, artist, album) via D-Bus `org.bluez.MediaPlayer1` for BT speakers that support it
- surface now-playing metadata in the device status card and diagnostics
- add RSSI signal strength monitoring via `hcitool rssi` or D-Bus `org.bluez.Device1.RSSI` for connected devices
- use RSSI trends in health scoring: flag degraded signal as a possible cause of audio dropouts
- add BLE coexistence awareness: detect when classic BT and BLE share an adapter and surface potential interference in diagnostics

### Exit criteria

- USB DACs and wired outputs appear in the UI alongside Bluetooth speakers as first-class player shapes
- operators can create and manage wired players through the new operator workflows rather than raw config edits
- Bluetooth and wired players share one capability-driven model without regressing Bluetooth reliability
- the modern console is now responsible for the highest-churn player-management paths
- `sbb` CLI can list devices, show status, manage config, and run diagnostics from a terminal without requiring a browser
- config changes that don't require a full restart are applied live with clear feedback
- idle disconnect decisions are driven by PA sink state rather than fragile daemon-flag cascades
- operators can choose per-device idle behavior (disconnect, power save, stay awake) to match speaker capabilities

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

## Phase V3-3: Observability-first runtime and operations center

### Goal

Make health, signal path, and recovery state first-class operator surfaces rather than advanced diagnostics hidden behind logs.

### Scope

#### Epic 12. Live telemetry and degraded-mode summaries

- expose current codec, sample rate, buffer and stream state, uptime, reconnect count, and resolved output sink where available
- pull telemetry from subprocess status lines, bridge state, backend callbacks, and event history
- include structured per-device event history such as reconnects, sink loss or acquisition, route corrections, re-anchor events, and MA sync failures
- publish compact degraded-mode and health-summary surfaces in addition to raw live status

#### Epic 13. Signal path and route ownership visibility

- render the end-to-end path for each backend type:
  - MA → Sendspin → subprocess → PulseAudio or PipeWire sink → Bluetooth A2DP → speaker
  - MA → Sendspin → subprocess → PulseAudio or ALSA sink → wired speaker or DAC
- show measured or estimated latency at each hop where available
- indicate route ownership, bottlenecks, or degraded hops such as codec fallback, sink mismatch, or missing route ownership

#### Epic 14. Operations center and reusable UI system

- build a unified diagnostics and recovery center instead of scattering operational detail across many unrelated UI sections
- add a frontend operation model that can present live state, pending actions, recovery history, and bulk actions without duplicating business logic across cards, rows, dialogs, and modals
- establish a stronger UI component system for badges, notices, toasts, drawers, dialogs, filters, timeline or event-list views, and calmer mobile density
- favor split-pane, drawer, and progressive-disclosure patterns that scale on desktop and mobile better than endlessly expanding rows

### Exit criteria

- operators can see codec, sample rate, sink route, health, and event history without reading logs
- the signal path is understandable at a glance for Bluetooth, wired, and virtual-sink players
- degradation is surfaced proactively instead of discovered only after audio sounds wrong
- the UI has a reusable operations vocabulary instead of repeatedly hand-assembling each diagnostic surface

## Phase V3-4: Delay intelligence and guided tuning

### Goal

Reduce manual `static_delay_ms` guesswork and make sync decisions more measurable, guided, and explainable.

### Scope

#### Epic 15. Delay telemetry foundation

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
- AppArmor security profile for Bluetooth and PulseAudio D-Bus access in hardened containers
- `/health` HTTP endpoint for container orchestrators (Docker healthcheck, Kubernetes liveness probe)

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

- V3-0 already-landed guidance and recovery polish as the baseline
- the core of V3-1:
  - backend contracts and capability modeling
  - config and runtime model v2 foundations
  - event-history and simulator foundations
  - first operator-console platform pieces
- the core of V3-2:
  - the first wired and USB backend
  - backend-aware player creation and editing flows
  - the first new diagnostics and details surfaces in the operator console
  - `sbb` CLI with core device, config, status, and diagnostics commands
  - PA sink state monitoring as idle disconnect ground truth
  - per-device idle mode (disconnect, power save, stay awake)
- baseline audio health visibility and signal-path publication
- delay telemetry foundations and a manual calibration path
- structured diagnostics bundle foundations for future planner and AI work

That is enough to make v3 feel materially different: **"Bluetooth-first multiroom with a real multi-backend platform, a modern operator console, and much clearer audio visibility"**.
