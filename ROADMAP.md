# Roadmap

## Purpose

This roadmap reflects the **current `main` branch after PR #80 and release `v2.41.0-rc.1`**.

Its job is no longer to describe an aspirational Phase 1 / Phase 2 foundation that has not shipped yet. That foundation is now largely in the repository and on the release track. The roadmap should therefore answer a different question:

- what was already completed in the recent runtime/contract push
- what architectural cleanup is still genuinely unfinished
- what the next practical product and UX phases should be
- when backend expansion is actually safe to start

The project remains Bluetooth-first. Reliability on real Home Assistant, Docker, Raspberry Pi, and LXC deployments still matters more than architectural novelty.

## Current Status

### What is already shipped

The former Phase 1 and Phase 2 foundation work is now effectively **complete and shipped in `v2.41.0-rc.1`**:

- snapshot-first read models are the default path across the main status and diagnostics surfaces
- `DeviceRegistry` is a real canonical inventory service instead of only a read helper
- `BridgeOrchestrator` / `BridgeLifecycleState` own more explicit startup and shutdown publication
- parent/subprocess IPC uses explicit status / log / error / command envelopes
- device event history and health explanations are normalized enough to drive richer diagnostics
- config lifecycle handling is schema-aware across load / save / import / validation / HA translation paths
- bridge telemetry and runtime event hooks exist and are operator-visible

### What is still unfinished

The remaining gaps are now narrower and more specific:

- `state.py` is still the main mutable coordination surface and compatibility layer
- routes and services still contain direct `state.*` access that should move behind registry / snapshot / lifecycle seams
- onboarding exists as a guidance snapshot, but not yet as guided operator flows
- there is still no explicit device / bridge capability model
- latency tuning, recovery tooling, and timeline-style diagnostics are still shallow
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

## Guiding Principles

### 1. Stay Bluetooth-first

Every phase must preserve the realities of A2DP output:

- unstable connectivity
- sink identity churn
- delayed post-reconnect availability
- hardware-specific latency behavior

### 2. Finish integration cleanup before starting new abstractions

The biggest risk is no longer “missing architecture”. It is **running two architectural styles in parallel**:

- newer registry / snapshot / lifecycle seams
- older `state.py`-centric coordination and route access patterns

The next phase should reduce that overlap before adding broader backend concepts.

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

The architecture is materially better than it was before `v2.41.0-rc.1`, but `state.py` still carries too much coordination weight. The next phase should make the shipped architecture the **only** architecture, not just the preferred one.

## Phase 1: Finish v2 Integration Cleanup

### Goal

Turn the newly shipped runtime foundation into the unambiguous canonical path.

### Why this phase exists

Former Phase 1 and 2 already delivered the main building blocks. What remains is the cleanup work that those phases exposed:

- route-level dependence on `state.py`
- compatibility wrappers that still act like first-class runtime ownership
- lifecycle and contract seams that are implemented, but not yet documented and integration-tested enough

### Epics

#### Epic 1. De-center route and service reads from `state.py`

Outcome:

- routes read from registry / snapshot / lifecycle services by default

Backlog:

1. Audit and migrate remaining direct `state.*` read paths in route modules
2. Move remaining status/MA/runtime lookups behind explicit helpers or snapshot builders
3. Reduce route knowledge of shared mutable internals
4. Add focused tests proving compatibility after the migration

Suggested PRs:

- PR 1: route read-path cleanup
- PR 2: registry/lifecycle helper expansion and compatibility tests

#### Epic 2. Clarify write-side ownership and shared-state boundaries

Outcome:

- `state.py` becomes a thinner compatibility facade instead of the practical center of the runtime

Backlog:

1. Separate true ownership surfaces from compatibility shims inside `state.py`
2. Make lifecycle publication, MA state, jobs, and event persistence boundaries more explicit
3. Identify what should remain in shared state versus move into dedicated services
4. Document stable write-side responsibilities

Suggested PRs:

- PR 3: state ownership reduction
- PR 4: shared-state boundary documentation and cleanup

#### Epic 3. Add lifecycle integration tests and contract documentation

Outcome:

- startup/shutdown/runtime contracts are testable and easier to evolve safely

Backlog:

1. Add integration-style coverage for startup ordering, shutdown ordering, and recovery-sensitive flows
2. Document supported IPC and telemetry guarantees in code/docs
3. Tighten diagnostics expectations around lifecycle transitions
4. Keep release-facing operational contracts aligned with tests

Suggested PRs:

- PR 5: lifecycle integration coverage
- PR 6: contract and diagnostics documentation

### Exit Criteria

- routes no longer depend heavily on direct `state.py` reads
- `state.py` is a compatibility facade, not the architectural center
- lifecycle and IPC guarantees are documented and integration-tested
- the v2 runtime foundation is clearly “finished”, not merely shipped

## Phase 2: Onboarding, Capability Model, and Recovery UX

### Goal

Reduce setup friction and make operational recovery more actionable.

### Why this phase exists

The project already has onboarding snapshots, diagnostics, startup progress, and event history. The next step is to turn those into **guided operator flows**, not just passive status outputs.

### Epics

#### Epic 4. Expand onboarding assistant into guided setup flows

Outcome:

- install-to-first-playback becomes shorter and more explicit

Backlog:

1. Turn current onboarding checks into guided flows for adapters, devices, sinks, and MA auth
2. Add remediation actions and stronger UI entry points
3. Reuse the same guidance model across dashboard, diagnostics, and bugreport outputs
4. Align onboarding state with live lifecycle and telemetry data

Suggested PRs:

- PR 7: guided onboarding backend surfaces
- PR 8: onboarding UI integration and remediation actions

#### Epic 5. Introduce an explicit capability model

Outcome:

- device and bridge differences become first-class instead of implicit

Backlog:

1. Define explicit device / bridge capability surfaces
2. Model battery support, release/reclaim support, routing modes, sink presence, and recovery affordances
3. Expose capabilities in API payloads and diagnostics
4. Render capability-aware UI controls and messaging

Suggested PRs:

- PR 9: capability model and API exposure
- PR 10: capability-aware UI and diagnostics

#### Epic 6. Improve latency and recovery tooling

Outcome:

- multi-device deployments become easier to tune and debug

Backlog:

1. Add latency guidance for multi-device setups
2. Improve sink verification and sink recovery explainability
3. Add structured timeline/export surfaces for event history and recovery paths
4. Expose sync-related hints in diagnostics and onboarding

Suggested PRs:

- PR 11: latency and sink recovery guidance
- PR 12: timeline exports and richer recovery tooling

### Exit Criteria

- new users can identify setup blockers with less guesswork
- operators can see which actions are possible on a given device/runtime
- multi-device recovery and tuning are practical without deep code familiarity

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

1. finish v2 integration cleanup and de-center `state.py`
2. add lifecycle integration coverage and explicit contract docs
3. expand onboarding into guided flows
4. introduce the capability model
5. improve latency and recovery tooling
6. only then start backend abstraction
7. add backend-oriented config schema
8. prove one or two non-Bluetooth backends
9. only then consider broader expansion

## Dependency Summary

- route cleanup should follow the shipped snapshot / registry / lifecycle seams
- reducing `state.py` coupling should happen before onboarding and capability work spreads new read paths
- backend abstraction should come after runtime cleanup and capability modeling
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
- start backend abstraction while `state.py` still dominates runtime coordination
- treat hooks, telemetry, or diagnostics as substitutes for fixing ownership boundaries
- expand to speculative backends before guided onboarding and capability clarity exist
- turn backend abstraction into a rewrite excuse

Do:

- treat `v2.41.0-rc.1` as the baseline for future roadmap decisions
- reduce overlapping architectural styles instead of adding a third one
- keep diagnostics, docs, and contract surfaces aligned with runtime behavior
- preserve Bluetooth recovery reliability as the highest priority
- use incremental migrations with compatibility layers only as long as they are still needed
