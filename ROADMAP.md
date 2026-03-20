# Roadmap

## Purpose

This roadmap reflects the **current `main` branch after the `v2.41.0-rc.2` release line and the subsequent bridge-identity safeguards on `main`**.

Its job is no longer to describe an aspirational Phase 1 / Phase 2 foundation that has not shipped yet. That foundation is now largely in the repository and on the release track. The roadmap should therefore answer a different question:

- what was already completed in the recent runtime/contract push
- what architectural cleanup is still genuinely unfinished
- what the next practical product and UX phases should be
- when backend expansion is actually safe to start

The project remains Bluetooth-first. Reliability on real Home Assistant, Docker, Raspberry Pi, and LXC deployments still matters more than architectural novelty.

## Current Status

### What is already shipped

The former Phase 1 and Phase 2 foundation work shipped in `v2.41.0-rc.1`, and the follow-up **Phase 1 integration cleanup shipped in `v2.41.0-rc.2`**:

- snapshot-first read models are the default path across the main status and diagnostics surfaces
- `DeviceRegistry` is a real canonical inventory service instead of only a read helper
- `BridgeOrchestrator` / `BridgeLifecycleState` own more explicit startup and shutdown publication
- parent/subprocess IPC uses explicit status / log / error / command envelopes
- device event history and health explanations are normalized enough to drive richer diagnostics
- config lifecycle handling is schema-aware across load / save / import / validation / HA translation paths
- bridge telemetry and runtime event hooks exist and are operator-visible
- route modules now read bridge / MA / async-job / adapter state through dedicated services instead of direct `state.py` imports
- `state.py` is now a compatibility facade over explicit runtime-state owners (`bridge_runtime_state`, `ma_runtime_state`, `async_job_state`, `adapter_names`)
- lifecycle startup/shutdown contracts are integration-tested and documented as operator-facing runtime guarantees
- Music Assistant long-lived tokens can now be named and tracked per physical bridge host instead of only as anonymous bridge credentials
- config validation now warns when a newly added Bluetooth MAC already appears in Music Assistant under the same stable player identity

### What is still unfinished

The remaining gaps are now narrower and more specific:

- `state.py` is still the compatibility home for some shared runtime surfaces (event bus, client publication, device-event history), even though it is no longer the route-level ownership center
- onboarding exists as snapshot-based guidance and next-step hints, but not yet as guided operator flows with remediation actions
- there is still no explicit device / bridge capability model; the UI/API expose useful status fields, but not a first-class capability schema
- latency tuning, recovery tooling, and timeline-style diagnostics are still shallow; the code has sink verification, event history, and playback-health groundwork, but operators still lack stronger recovery guidance
- Music Assistant bridge-identity safeguards now exist at config/auth time, but they are not yet integrated into onboarding or capability-aware UX
- backend abstraction should remain deferred until the v2 runtime is cleaner and more boring

## Recently Completed Foundations

The roadmap should treat the following work as **done**, not as future backlog:

### Former Phase 1 — runtime foundation

Completed in `v2.41.0-rc.1`:

1. **Read-side migration**
   - snapshots and normalized read helpers now drive the main status surfaces
   - cross-route enrichment moved closer to snapshot builders and health summaries

2. **Canonical device registry**
   - device inventory and lookups now flow through `DeviceRegistry`
   - active / disabled / released inventory semantics are explicit

3. **Orchestration boundaries**
   - startup / shutdown publication is more explicit around `BridgeOrchestrator`
   - lifecycle state and runtime publication gained stronger seams and regression coverage

### Former Phase 2 — contracts, diagnostics, config lifecycle

Completed in `v2.41.0-rc.1`:

4. **Explicit IPC contract**
   - structured envelopes exist for command / status / log / error traffic
   - compatibility behavior is centralized around protocol helpers

5. **Normalized event history and health explanations**
   - canonical device event types exist
   - recent event history is used to explain degraded and recovering devices

6. **Config lifecycle hardening**
   - schema-aware migration / validation / persistence flows are in place
   - config-sensitive keys and runtime-state handling are better normalized

7. **Telemetry and hook surfaces**
   - `/api/bridge/telemetry` and `/api/hooks` are real runtime surfaces
   - diagnostics and bugreport paths were tightened after PR review follow-up

### Post-`v2.41.0-rc.2` safeguards and operator hardening

Completed on `main` after the `rc.2` tag:

8. **Music Assistant bridge identity safeguards**
   - long-lived MA tokens are now named from the current hostname and tracked with instance metadata
   - silent auth distinguishes current-instance tokens from copied/foreign-instance credentials while remaining backward compatible with legacy metadata-free tokens

9. **Duplicate-device protection across bridges**
   - config validate/save/upload flows now check MA `players/all` using the stable MAC-derived `player_id`
   - newly added devices warn when they appear to belong to another bridge instead of silently creating a conflict-prone configuration

## Guiding Principles

### 1. Stay Bluetooth-first

Every phase must preserve the realities of A2DP output:

- unstable connectivity
- sink identity churn
- delayed post-reconnect availability
- hardware-specific latency behavior

### 2. Keep integration cleanup finished before starting new abstractions

The biggest risk is no longer “missing architecture”. It is regressing back into **two architectural styles in parallel**:

- the newer registry / snapshot / lifecycle / runtime-state seams
- ad hoc `state.py`-centric access patterns reintroduced by later feature work

The next phase should preserve the completed cleanup while moving on to onboarding, capability modeling, and recovery UX.

### 3. Keep migrations incremental

Do not replace working surfaces wholesale. Add new seams, migrate callers, validate behavior, then remove legacy access only when the new path is proven.

### 4. Prefer operational clarity over theoretical purity

This bridge runs in hardware-heavy, failure-prone environments. Diagnostics, recovery visibility, and safe config evolution still outrank elegant abstractions.

### 5. Expand only after the runtime becomes boring

Backend abstraction, local audio outputs, and broader platform expansion are still reasonable directions, but they should be built on a runtime that is explicit, observable, and mostly free of compatibility-era coupling.

## Current Architecture Baseline

### Runtime layer

- `BridgeOrchestrator`
- `BridgeLifecycleState`
- `SendspinClient`
- `BluetoothManager`
- MA integration service / monitor helpers
- subprocess command / IPC / stderr / stop services

### Read layer

- `DeviceSnapshot`
- `BridgeSnapshot`
- `StartupProgressSnapshot`
- `DeviceHealthSummary`
- onboarding assistant snapshot builders

### Contract / diagnostics layer

- explicit IPC envelopes
- normalized internal device events
- telemetry payload builders
- runtime webhook registry with delivery history
- schema-aware config migration / validation helpers

### Remaining architectural hotspot

The architecture is materially better than it was before `v2.41.0-rc.1`: route-level cleanup is complete, lifecycle contracts are documented, and `state.py` is no longer the practical ownership center for bridge / MA / job / adapter state. The remaining hotspot is narrower:

- shared compatibility state still mixes event publication, client snapshots, and device-event history
- future features still need to avoid sliding back into direct mutable shared-state coupling
- newer operational safeguards should keep building on explicit service seams instead of adding special-case stateful shortcuts

## Phase 1: Finish v2 Integration Cleanup — Completed

### Delivery summary

Phase 1 was completed across PR `#81` and tag `v2.41.0-rc.2`.

#### Epic 1. De-centered route and service reads from `state.py` ✓

Delivered:

1. Route modules now read bridge / MA / async-job / adapter state through dedicated services
2. Route knowledge of shared mutable internals was reduced substantially
3. Compatibility tests were updated to patch the new route-facing seams instead of old `state` imports

#### Epic 2. Clarified write-side ownership and shared-state boundaries ✓

Delivered:

1. `bridge_runtime_state`, `ma_runtime_state`, `async_job_state`, and `adapter_names` now own their respective runtime domains
2. `state.py` delegates to those owners as a compatibility facade instead of remaining the practical center of runtime ownership
3. Lifecycle publication, MA state, jobs, and adapter cache responsibilities are now materially more explicit

#### Epic 3. Added lifecycle integration tests and contract documentation ✓

Delivered:

1. Lifecycle integration coverage now locks down startup/shutdown publication ordering and failure publication
2. README-level runtime contract documentation now describes lifecycle events, diagnostics/telemetry, IPC, and runtime hook guarantees
3. Release-facing runtime contracts are aligned with tests and the changelog

### Exit Criteria Verification

- ✓ routes no longer depend heavily on direct `state.py` reads
- ✓ `state.py` is a compatibility facade, not the architectural center for route/runtime ownership
- ✓ lifecycle and IPC guarantees are documented and integration-tested
- ✓ the v2 runtime foundation is clearly finished, not merely shipped

### Follow-through, not a new phase

Phase 1 should not be re-planned. The only remaining Phase 1-shaped work is minor guardrail follow-through:

1. keep new features on the explicit runtime-state seams instead of reintroducing direct mutable shared-state access
2. continue trimming compatibility-only responsibilities from `state.py` when adjacent feature work makes that safe
3. treat future cleanup as opportunistic maintenance, not as a standalone roadmap phase

## Phase 2: Onboarding, Capability Model, and Recovery UX

### Status

This is now the **current** roadmap phase.

Recent work on `main` after `v2.41.0-rc.2` improved operator safety (bridge-instance token identity, duplicate-device warnings), but it did **not** start the main Phase 2 epics yet. The current focus is still to turn existing snapshots and diagnostics into guided onboarding, explicit capabilities, and stronger recovery UX.

### Goal

Reduce setup friction and make operational recovery more actionable.

### Why this phase exists

The project already has onboarding snapshots, diagnostics, startup progress, and event history. The next step is to turn those into **guided operator flows**, not just passive status outputs.

### Design principles for Phase 2

Phase 2 should follow a few proven UI/UX patterns instead of growing ad hoc status screens:

1. **Checklist first, advanced details second**
   - show the shortest path to first successful playback up front
   - progressively disclose advanced diagnostics and tuning only when the operator needs them

2. **Use staged flows for linear setup tasks**
   - pairing, assigning, authenticating, and testing are better represented as a short sequence than as a large static configuration screen
   - keep exploratory diagnostics outside the main setup path so operators do not have to scan everything at once

3. **Make diagnostics action-oriented**
   - every important warning should answer three questions:
     - what is wrong
     - what probably caused it
     - what the safest next action is

4. **Separate capability from current health**
   - the UI should distinguish:
     - what a bridge/device supports in principle
     - what is currently available right now
     - what is blocked and why

5. **Prefer traceable recovery over opaque automation**
   - automatic reconnects and self-healing still matter, but operators should be able to inspect the recent path, rerun checks, and understand what the bridge just attempted

6. **Keep operator UX mobile-safe and low-friction**
   - primary actions should stay obvious, touch-safe, and readable in Home Assistant/mobile contexts
   - auth/setup forms should favor clear labels, inline validation, and minimal dead-end states

7. **Teach the system model, not just the settings**
   - the UI should make it obvious how the operator should think about the bridge:
     - adapter / speaker / bridge device / MA player / zone
   - first-run UX should reinforce that mental model instead of only exposing raw config fields

### Epics

#### Epic 4. Expand onboarding assistant into guided setup flows

Outcome:

- install-to-first-playback becomes shorter and more explicit

Backlog:

1. Turn current onboarding checks into guided flows for adapters, devices, sinks, and MA auth
2. Add remediation actions and stronger UI entry points
3. Reuse the same guidance model across dashboard, diagnostics, and bugreport outputs
4. Align onboarding state with live lifecycle and telemetry data
5. Add a persistent setup checklist with completion state, current blocker, and explicit “next best action”
6. Split first-run setup from advanced diagnostics via progressive disclosure instead of one large mixed surface
7. Turn MA sign-in and device-attachment steps into staged flows with inline validation, duplicate-device warnings, and explicit success/failure checkpoints
8. Add context-aware empty states that explain why a section is empty and what action unlocks it
9. Introduce a simple operator IA that makes “Get started”, “Operate”, and “Recover” feel like distinct jobs instead of one flat admin page
10. Add a “first room / first speaker” golden path that explicitly walks through naming, assignment, and first successful playback instead of dropping the operator into the full admin surface
11. Confirm successful player creation with live status updates (for example: bridge device created, BT connected, sink attached, MA player visible) so operators do not have to infer success from scattered status widgets

Suggested PRs:

- PR 7: checklist-driven onboarding backend and step model
- PR 8: staged onboarding UI, empty states, and remediation actions

#### Epic 5. Introduce an explicit capability model

Outcome:

- device and bridge differences become first-class instead of implicit

Backlog:

1. Define explicit device / bridge capability surfaces
2. Model battery support, release/reclaim support, routing modes, sink presence, and recovery affordances
3. Expose capabilities in API payloads and diagnostics
4. Render capability-aware UI controls and messaging
5. Distinguish `supported`, `currently_available`, and `blocked_reason` so UI controls can explain themselves
6. Expose recommended actions / safe actions per runtime state instead of deriving them ad hoc in the frontend
7. Group capabilities into operator-facing domains such as connectivity, playback/control, Music Assistant integration, recovery, and diagnostics
8. Use the capability model to decide which onboarding and recovery steps are relevant on a given device/runtime

Suggested PRs:

- PR 9: capability schema, action affordances, and API exposure
- PR 10: capability-aware UI states, onboarding, and diagnostics

#### Epic 6. Improve latency and recovery tooling

Outcome:

- multi-device deployments become easier to tune and debug

Backlog:

1. Add latency guidance for multi-device setups
2. Improve sink verification and sink recovery explainability
3. Add structured timeline/export surfaces for event history and recovery paths
4. Expose sync-related hints in diagnostics and onboarding
5. Add an issue/recovery center that groups active problems by severity, recommended action, and affected devices
6. Add trace-style recovery timelines for startup, reconnect, sink attach, MA auth, and playback-health transitions
7. Let operators rerun safe checks/actions from the UI (for example: rerun preflight, retry MA auth validation, retry sink verification, reconnect a device)
8. Add a latency calibration assistant for multi-device setups with presets, comparison hints, and safe default recommendations
9. Attach trace/timeline context directly to diagnostics download and bugreport generation so support flows start with actionable evidence
10. Add a known-good test path for recovery (“test this speaker / test this routing path / confirm MA visibility”) so operators can separate wiring/config issues from playback/content issues

Suggested PRs:

- PR 11: issue center, rerunnable checks, and sink recovery guidance
- PR 12: trace timelines, latency assistant, and richer recovery tooling

### Exit Criteria

- new users can identify setup blockers with less guesswork
- operators can see which actions are possible on a given device/runtime
- multi-device recovery and tuning are practical without deep code familiarity
- onboarding feels like a checklist-driven workflow instead of a passive status dump
- operators can trace and retry the most important recovery paths without leaving the UI

## Phase 2.1: Guidance Consolidation and Operator Control

### Goal

Consolidate the Phase 2 onboarding + capability + recovery surfaces into one calmer, more operator-controlled guidance layer without losing the detailed diagnostics that now exist.

### Why this phase exists

Phase 2 successfully added:

- guided onboarding
- explicit capability-aware controls
- recovery banners and a diagnostics recovery center

The next UX step is not “add even more surfaces”, but to **reduce overlap** between them:

- full onboarding should dominate only in the real first-run empty state
- ongoing readiness should move into compact header/status guidance
- recovery guidance should stay actionable without permanently taking over the dashboard
- repeated multi-device problems should be recoverable in fewer clicks
- disabled controls should be explained by one visible top-level guidance surface instead of scattered microcopy

### Epics

#### Epic 6A. Unify guidance and reduce dashboard noise

Outcome:

- onboarding and recovery stop competing for the same attention slot
- first-run installs stay guided, while existing installs regain dashboard space

Backlog:

1. Add a unified operator-guidance model that resolves current state into `empty_state`, `progress`, `attention`, or `healthy`
2. Use onboarding, recovery, capability, and startup-progress data as inputs to that unified model instead of keeping multiple unrelated top-level guidance surfaces
3. Keep the full onboarding checklist only for the true empty state (no configured adapters and no configured devices)
4. Move non-empty-state setup/readiness guidance into compact header/runtime progress and status messaging
5. Ensure one primary banner/headline owns the “next best action” at any given time
6. Preserve the richer diagnostics detail behind that summary layer instead of deleting it

Suggested PRs:

- PR 13: unified guidance contract and API exposure
- PR 14: empty-state onboarding and header-progress UI refactor

#### Epic 6B. Make guidance operator-controlled

Outcome:

- operators can reduce noise without losing access to help and diagnostics

Backlog:

1. Add “Don’t show again” dismissal for onboarding guidance
2. Add “Don’t show again” dismissal for recovery banners
3. Persist those visibility preferences as UI/operator preferences
4. Add explicit settings to restore onboarding guidance, recovery banners, and dismissed guidance state
5. Always keep `Open Diagnostics` as a stable secondary action on guidance/attention banners
6. Keep diagnostics and bugreport detail available even when top-level banners are hidden

Suggested PRs:

- PR 15: dismissible guidance banners and preference persistence
- PR 16: guidance visibility settings and diagnostics fallback polish

#### Epic 6C. Improve recovery efficiency and blocked-state clarity

Outcome:

- multi-device attention states become easier to understand and recover from

Backlog:

1. Detect repeated issue groups such as:
   - multiple disconnected devices
   - multiple released devices
   - multiple missing sinks
   - repeated Music Assistant-related failures
2. Expose bulk actions for repeated issues, such as:
   - reconnect all affected
   - reclaim all affected
   - rerun checks
   - retry MA discovery
3. Show the affected count and affected device list before a bulk action runs
4. Align capability-blocked controls with the unified guidance layer so important disabled states always have a matching visible explanation
5. Keep compact inline controls visually clean; prefer banner/header-level explanation over extra inline warning chrome
6. Ensure diagnostics still provide the deeper per-device breakdown after the top-level guidance has explained the root cause

Suggested PRs:

- PR 17: grouped issue detection and bulk recovery actions
- PR 18: blocked-state explanation alignment and compact control UX cleanup

### Exit Criteria

- only true empty-state installs show the full onboarding card as the dominant guidance surface
- existing installs see readiness/progress in the header/status layer instead of persistent large onboarding chrome
- operators can dismiss onboarding/recovery banners and restore them later from settings
- repeated multi-device issues can be recovered with grouped actions instead of only one-by-one clicks
- important disabled states are explained by the unified top-level guidance surface

## Phase 3: Backend Abstraction for v3

### Goal

Prepare the bridge for selective non-Bluetooth expansion without destabilizing the current runtime.

### Why this phase exists

Backend abstraction is still a valid long-term direction, but it should happen **after**:

- v2 runtime cleanup is complete
- capabilities are explicit
- operational diagnostics are strong enough to compare backends safely

### Epics

#### Epic 7. Introduce a backend abstraction layer

Outcome:

- Bluetooth becomes one backend under an explicit contract

Backlog:

1. Define `AudioBackend`-style abstraction and capability/status contracts
2. Wrap the existing Bluetooth runtime behind that contract
3. Keep subprocess behavior backend-agnostic where practical
4. Preserve current behavior while introducing abstraction seams

Suggested PRs:

- PR 13: backend abstraction contract
- PR 14: Bluetooth backend wrapper and compatibility path

#### Epic 8. Introduce a backend-oriented config schema

Outcome:

- configuration is ready for multiple backend types

Backlog:

1. Define config schema v2 around player/backend configuration
2. Add migration tooling from the current Bluetooth-device model
3. Preserve compatibility loading during transition
4. Document migration behavior and downgrade assumptions

Suggested PRs:

- PR 15: config schema v2
- PR 16: migration tooling and compatibility loading

#### Epic 9. Add the first non-Bluetooth backends

Outcome:

- backend abstraction is proven on real adjacent use cases

Backlog:

1. Add `LocalSinkBackend` for PulseAudio/PipeWire
2. Add `ALSADirectBackend` for minimal environments
3. Validate capability reporting and subprocess startup behavior across backend types
4. Keep diagnostics and config UX coherent across backend types

Suggested PRs:

- PR 17: local sink backend
- PR 18: ALSA direct backend

### Exit Criteria

- Bluetooth remains the primary and most stable backend
- abstraction is proven without regressing the shipped bridge
- config and diagnostics remain coherent across backend types

## Phase 4: Selective Expansion After Core Stability

### Goal

Expand only where the project gains clear product value without diluting its focus.

### Candidate Areas

#### Epic 10. High-value adjacent expansion

Possible backlog:

1. USB audio auto-discovery
2. virtual sink / testing-oriented backend
3. richer sync telemetry and drift reporting
4. stronger Sendspin-aligned capability reporting

Suggested PRs:

- PR 19: one adjacent backend or USB-discovery feature
- PR 20: sync telemetry and diagnostics integration

#### Epic 11. Strategic optional work

Only pursue if the earlier phases are stable and demand is proven:

1. Snapcast client backend
2. VBAN backend
3. multi-bridge federation
4. HACS / custom component strategy
5. plugin SDK or extension surface
6. OpenHome or similar ecosystem alignment

Suggested PRs:

- PR 21+: only as separate strategy tracks, not as default assumptions

### Exit Criteria

- expansion does not weaken Bluetooth reliability
- the architecture stays understandable
- new backends or platforms prove practical value, not just conceptual neatness

## Recommended Sequence

The safest sequence from the current codebase is now:

1. expand onboarding into guided flows
2. introduce the capability model
3. improve latency and recovery tooling
4. only then start backend abstraction
5. add backend-oriented config schema
6. prove one or two non-Bluetooth backends
7. only then consider broader expansion

## Dependency Summary

- guided onboarding should build on the shipped snapshot / registry / lifecycle seams instead of bypassing them
- capability modeling should precede most UX branching so the UI/API can describe real bridge/device constraints
- backend abstraction should come after onboarding, capability modeling, and recovery semantics are clearer
- config schema v2 should come after the current config lifecycle is fully settled
- speculative platform work should remain downstream from proven backend and diagnostics work

## Definition of Done

This roadmap is successfully executed when:

- the shipped v2 runtime foundation is also the canonical architectural path
- routes and UI read from normalized models and capability surfaces by default
- device inventory and lifecycle ownership are explicit
- subprocess and telemetry contracts are documented, tested, and stable to evolve
- onboarding and recovery flows reduce setup friction and recovery guesswork
- the project can expand selectively without compromising Bluetooth reliability

## Guardrails

Do not:

- re-plan already completed Phase 1 / Phase 2 work as if it still has to be implemented
- reintroduce direct `state.py` coupling in new feature work now that Phase 1 cleanup is complete
- start backend abstraction before onboarding, capability, and recovery semantics are clearer
- treat hooks, telemetry, or diagnostics as substitutes for fixing ownership boundaries
- expand to speculative backends before guided onboarding and capability clarity exist
- turn backend abstraction into a rewrite excuse

Do:

- treat `v2.41.0-rc.1` as the baseline for future roadmap decisions
- reduce overlapping architectural styles instead of adding a third one
- keep diagnostics, docs, and contract surfaces aligned with runtime behavior
- preserve Bluetooth recovery reliability as the highest priority
- use incremental migrations with compatibility layers only as long as they are still needed
