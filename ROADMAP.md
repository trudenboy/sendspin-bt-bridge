# Roadmap

## Purpose

This roadmap defines the next development stages for `sendspin-bt-bridge` based on the **current codebase**, not on an older architectural snapshot.

The project is no longer at the stage where `BridgeOrchestrator`, startup progress, snapshot models, onboarding guidance, and protocol-versioned IPC are merely ideas. Important parts of that work already exist in the live runtime. The roadmap therefore focuses on:

- finishing partially completed architectural migration
- reducing remaining shared-state coupling
- strengthening contracts, diagnostics, and config lifecycle safety
- improving onboarding and recovery UX
- preparing for selective backend expansion only after the Bluetooth core is stable

The goal remains the same: keep the bridge **Bluetooth-first**, operationally reliable, and practical for real Home Assistant, Docker, Raspberry Pi, and LXC deployments.

## Current Status

The current runtime already includes major structural improvements:

- `BridgeOrchestrator` owns bridge-wide bootstrap sequencing and runtime assembly
- `BridgeLifecycleState` publishes startup progress and runtime metadata
- typed read-side models already exist (`DeviceSnapshot`, `BridgeSnapshot`, `StartupProgressSnapshot`, `DeviceHealthSummary`)
- `SendspinClient` already delegates subprocess concerns to focused services
- protocol-versioned IPC helpers already exist (`protocol_version`)
- onboarding guidance and config upload validation already exist in operator-facing APIs

The main architectural gaps are now narrower and more specific:

- `state.py` still remains the main mutable coordination surface
- device registry is still closer to a snapshot helper than a full ownership service
- route and service code still contains some direct runtime/internal lookups
- event history, diagnostics, and health explanations need deeper normalization
- config validation exists, but config migrations and config/runtime separation are not complete
- backend abstraction should not start until the current runtime contracts are cleaner

## Guiding Principles

### 1. Stay Bluetooth-first

Every phase must preserve the core realities of A2DP output:

- unstable connectivity
- sink identity churn
- delayed post-reconnect availability
- hardware-specific latency behavior

### 2. Finish started refactors before starting new abstractions

The repository already contains partial solutions for orchestration, read-side models, startup progress, events, and IPC contracts. The next step is to **complete and normalize** those efforts instead of restarting them under new names.

### 3. Keep migrations incremental

Avoid broad rewrites. Add new services and compatibility layers, migrate callers, validate behavior, then remove legacy access paths only after stability is proven.

### 4. Prefer operational clarity over theoretical purity

This bridge runs in hardware-heavy, failure-prone environments. Diagnostics, contract safety, and recovery visibility are more important than abstract elegance.

### 5. Expand only after the core becomes boring

Backend abstraction, local audio outputs, and broader ecosystem expansion are valid directions, but only after the Bluetooth bridge runtime is explicit, observable, and migration-safe.

## Current Architecture Baseline

The live codebase already points to the near-term target architecture:

### Runtime layer

- `BridgeOrchestrator`
- `BridgeLifecycleState`
- `SendspinClient`
- `BluetoothManager`
- `BridgeMaIntegrationService`
- subprocess command / IPC / stderr / stop services
- playback health and status event builders

### Read layer

- `DeviceSnapshot`
- `BridgeSnapshot`
- `StartupProgressSnapshot`
- `DeviceHealthSummary`
- onboarding assistant snapshots

### Contract layer

- `protocol_version` for parent/subprocess messages
- structured status/log/error envelopes in practice, but not yet fully formalized as a finished contract surface

### Remaining gap

The architecture is improved, but the center of gravity still sits partly in `state.py`. The roadmap below is about moving from **partially modernized runtime** to **fully explicit runtime ownership**.

## Phase 1: Complete the v2 Runtime Foundation

### Goal

Finish the runtime/service/read-model migration that is already underway.

### Why this phase exists

This phase is not about inventing `BridgeOrchestrator`, snapshots, or startup progress. Those already exist. It is about making them the **canonical path** instead of parallel infrastructure sitting beside older shared-state flows.

### Epics

#### Epic 1. Complete read-side migration

Outcome:

- routes and UI rely on snapshot builders and normalized read models consistently

Backlog:

1. Finish migrating status, diagnostics, and config-adjacent read paths to snapshot builders
2. Remove remaining route assumptions about `SendspinClient` internals where practical
3. Normalize cross-route status enrichment logic behind snapshot builders
4. Add targeted tests for snapshot completeness and compatibility behavior

Suggested PRs:

- PR 1: finish snapshot coverage for status and diagnostics
- PR 2: snapshot-first route cleanup and compatibility tests

#### Epic 2. Turn device registry into a real ownership service

Outcome:

- active, disabled, and released device lookup rules become explicit

Backlog:

1. Evolve `device_registry` from snapshot helper to canonical device registry service
2. Move registration and lookup rules out of ad-hoc state access
3. Centralize disabled/released device handling behind registry APIs
4. Keep immutable snapshots as the read-side product of the registry

Suggested PRs:

- PR 3: registry service introduction with compatibility wrapper
- PR 4: route and service migration to registry lookups

#### Epic 3. Close orchestration boundaries

Outcome:

- bridge-wide lifecycle ownership becomes explicit and testable

Backlog:

1. Finish centralizing startup/shutdown/restart semantics around `BridgeOrchestrator`
2. Clarify service ownership boundaries between orchestrator, MA bootstrap, registry, and per-device runtime
3. Reduce hidden startup coordination through `state.py`
4. Add integration tests for startup ordering, shutdown ordering, and restart-sensitive flows

Suggested PRs:

- PR 5: orchestrator lifecycle completion
- PR 6: startup/shutdown integration tests and compat cleanup

### Exit Criteria

- snapshots are the default read path
- registry owns device lookup and inventory semantics
- orchestrator owns bridge lifecycle semantics
- `state.py` is no longer the primary architectural center

## Phase 2: Contracts, Diagnostics, and Config Lifecycle

### Goal

Make the bridge easier to evolve safely and easier to operate in production.

### Why this phase exists

The codebase already has the beginnings of versioned IPC, structured diagnostics, health summaries, and config validation. This phase turns those into a finished operational contract.

### Epics

#### Epic 4. Finish the IPC contract

Outcome:

- parent/subprocess communication can evolve without accidental breakage

Backlog:

1. Formalize command, status, log, and error envelopes around the existing `protocol_version`
2. Define compatibility behavior for missing/legacy fields
3. Add explicit contract tests for parent/child parsing behavior
4. Document supported message guarantees

Suggested PRs:

- PR 7: explicit IPC envelope contract
- PR 8: IPC compatibility and contract tests

#### Epic 5. Normalize event history and health explanations

Outcome:

- diagnostics can explain why a device is degraded, not just that it is degraded

Backlog:

1. Standardize per-device event records for reconnects, sink loss, sink recovery, re-anchor, subprocess failure, and MA sync failures
2. Build health summaries from event history plus current state
3. Normalize retention, ordering, and severity classification
4. Expose richer cause/recovery history in diagnostics and bugreport surfaces

Suggested PRs:

- PR 9: event history normalization
- PR 10: health explanation and diagnostics enrichment

#### Epic 6. Finish config lifecycle safety

Outcome:

- config changes become migration-ready and safer to reason about

Backlog:

1. Extend validation beyond upload paths to the broader load/save/import lifecycle
2. Add migration functions keyed by `CONFIG_SCHEMA_VERSION`
3. Improve validation reporting for import/export flows
4. Separate user-owned config from runtime-derived state where practical

Suggested PRs:

- PR 11: config lifecycle validation expansion
- PR 12: config migration framework and runtime/user-state separation

#### Epic 7. Add resource telemetry and hook surfaces

Outcome:

- operators can understand bridge resource usage and external systems can react to lifecycle events

Backlog:

1. Add bridge and subprocess resource telemetry surfaces
2. Surface startup timings and resource summaries in diagnostics APIs
3. Formalize hook/webhook events for key lifecycle actions
4. Add test coverage and failure reporting for hook execution

Suggested PRs:

- PR 13: resource telemetry surfaces
- PR 14: hook/webhook framework

### Exit Criteria

- IPC is explicit, versioned, and tested
- diagnostics explain recent failure and recovery paths
- config lifecycle is migration-ready
- the bridge exposes structured operational data, not just logs

## Phase 3: Onboarding, Recovery UX, and Capability Clarity

### Goal

Reduce setup friction and make recovery actions understandable to operators.

### Why this phase exists

The project already has onboarding assistant logic, startup progress, diagnostics, and runtime explainability. The next step is not to invent onboarding, but to turn the current guidance surfaces into more actionable flows.

### Epics

#### Epic 8. Expand onboarding assistant into guided setup flows

Outcome:

- the path from install to first successful playback becomes shorter and clearer

Backlog:

1. Turn current onboarding checks into guided flows for adapters, devices, sinks, and MA auth
2. Add more actionable remediation text and direct UI entry points
3. Align onboarding state with live diagnostics and startup progress
4. Reuse the same guidance model across dashboard, diagnostics, and bugreport outputs

Suggested PRs:

- PR 15: guided onboarding backend surfaces
- PR 16: onboarding UI integration and remediation actions

#### Epic 9. Introduce an explicit capability model

Outcome:

- device differences become first-class, not implied

Backlog:

1. Model bridge/device capabilities explicitly
2. Distinguish battery support, release/reclaim support, volume routing modes, sink presence, and format-related capabilities
3. Expose capabilities in API payloads and diagnostics
4. Let UI render capability-aware controls and messaging

Suggested PRs:

- PR 17: capability model and API exposure
- PR 18: capability-aware UI and diagnostics

#### Epic 10. Improve latency and recovery tooling

Outcome:

- multi-device deployments become easier to tune and debug

Backlog:

1. Add latency guidance workflow for multi-device setups
2. Improve sink verification and sink recovery explainability
3. Add richer structured exports for event timelines, health summaries, and recovery history
4. Expose sync-related operator hints in diagnostics and onboarding

Suggested PRs:

- PR 19: latency and sink recovery guidance
- PR 20: richer diagnostics exports and recovery tooling

### Exit Criteria

- new users can identify setup blockers with less guesswork
- operators can understand available recovery actions
- capability differences are explicit in the UI and API

## Phase 4: Backend Abstraction for v3

### Goal

Prepare the bridge for selective non-Bluetooth expansion without destabilizing the current runtime.

### Why this phase exists

The v3 direction is reasonable only after the current Bluetooth runtime is explicit and stable. Backend abstraction should emerge from a stable core, not be used as a substitute for finishing current refactors.

### Epics

#### Epic 11. Introduce backend abstraction layer

Outcome:

- the current Bluetooth implementation becomes one backend under a common contract

Backlog:

1. Define `AudioBackend` abstraction and capability/status contracts
2. Wrap the existing Bluetooth runtime in `BluetoothA2DPBackend`
3. Keep subprocess behavior backend-agnostic where possible
4. Preserve current behavior while introducing abstraction seams

Suggested PRs:

- PR 21: backend abstraction contract
- PR 22: Bluetooth backend wrapper and compatibility path

#### Epic 12. Introduce backend-oriented config schema

Outcome:

- the config model is ready for multiple backend types

Backlog:

1. Define config schema v2 around player/backend configuration
2. Add migration tooling from the current Bluetooth-device model
3. Preserve compatibility loading during transition
4. Document migration behavior and downgrade assumptions

Suggested PRs:

- PR 23: config schema v2
- PR 24: migration tool and compatibility loading

#### Epic 13. Add the first non-Bluetooth backends

Outcome:

- backend abstraction is proven on realistic adjacent use cases

Backlog:

1. Add `LocalSinkBackend` for PulseAudio/PipeWire
2. Add `ALSADirectBackend` for stripped or minimal environments
3. Validate capability reporting and subprocess startup behavior across backend types
4. Ensure diagnostics and config UX remain coherent across backends

Suggested PRs:

- PR 25: Local sink backend
- PR 26: ALSA direct backend

### Exit Criteria

- Bluetooth remains the primary and most stable backend
- backend abstraction is proven without regressing the current bridge
- config and diagnostics remain coherent across backend types

## Phase 5: Selective Expansion After Core Stability

### Goal

Expand only where the project gains clear product value without diluting its focus.

### Candidate Areas

#### Epic 14. High-value adjacent expansion

Possible backlog:

1. USB audio auto-discovery
2. virtual sink/testing-oriented backend
3. richer sync telemetry and drift reporting
4. stronger Sendspin-aligned capability reporting

Suggested PRs:

- PR 27: one adjacent backend or USB-discovery feature
- PR 28: sync telemetry and diagnostics integration

#### Epic 15. Strategic optional work

Only pursue if core phases are stable and justified by demand:

1. Snapcast client backend
2. VBAN backend
3. multi-bridge federation
4. HACS/custom component strategy
5. plugin SDK or platform extension surface
6. OpenHome or similar ecosystem alignment

Suggested PRs:

- PR 29+: only as separate strategy tracks, not as default roadmap assumptions

### Exit Criteria

- expansion does not weaken Bluetooth reliability
- the architecture stays understandable
- new backends or platforms prove practical value, not just conceptual neatness

## Recommended PR Sequence

The safest implementation order is:

1. finish snapshot/read-side migration
2. formalize registry ownership
3. close orchestrator lifecycle boundaries
4. finish IPC contracts
5. normalize event history and health explanations
6. finish config lifecycle and migrations
7. add resource telemetry and hooks
8. expand onboarding into guided flows
9. introduce capability model
10. improve latency and recovery tooling
11. add backend abstraction
12. add backend-oriented config schema
13. add the first non-Bluetooth backends
14. only then consider broader expansion

## Dependency Summary

- route cleanup should follow snapshot-first read paths
- registry ownership should precede full `state.py` de-centering
- explicit IPC contracts should land before substantial backend expansion
- config schema v2 should come after the current config lifecycle is migration-ready
- backend abstraction should come after runtime ownership is explicit
- adjacent backends should come before speculative platform work

## Definition of Done

This roadmap is successfully executed when:

- runtime ownership is explicit
- routes and UI read from normalized models by default
- device inventory and lookup rules are centralized
- subprocess contracts are explicit and versioned
- diagnostics explain both symptoms and recent recovery history
- config changes are validated and migration-ready
- onboarding and recovery flows reduce setup friction
- the project can expand selectively without compromising Bluetooth reliability

## Guardrails

Do not:

- restart already completed refactors as if they do not exist
- treat demo/mock infrastructure as the main roadmap instead of a supporting tool
- begin generic platform expansion before finishing current runtime ownership work
- move multiple critical lifecycle responsibilities in one PR
- turn backend abstraction into a pretext for rewriting the bridge

Do:

- migrate incrementally
- keep compatibility layers while callers are being moved
- test real runtime behavior after each structural change
- keep diagnostics, docs, and contract surfaces aligned with runtime changes
- preserve Bluetooth recovery reliability as the highest priority
