# Roadmap

## Purpose

This roadmap translates the current architecture analysis into an implementation plan for evolving `sendspin-bt-bridge` without losing its strongest properties:

- Bluetooth-first operational reliability
- per-device subprocess isolation
- strong Music Assistant integration
- practical diagnostics and web UX

The goal is not to turn the project into a generic audio platform immediately. The goal is to make the current Bluetooth bridge easier to reason about, easier to test, easier to extend, and easier to operate.

## Current Position

The project already has a strong runtime model for the Bluetooth problem space:

- a main process owns the web API, configuration, Music Assistant integration, and Bluetooth lifecycle
- each Bluetooth speaker runs in its own subprocess with a dedicated `PULSE_SINK`
- the bridge already handles reconnects, sink rediscovery, MA state sync, and real-time UI updates

The main architectural limitations are:

- orchestration logic is spread across multiple modules
- `state.py` acts as a wide shared mutable state surface
- routes still know too much about runtime internals
- parent/subprocess IPC is useful but not explicitly versioned
- diagnostics explain symptoms well, but not always causes or recovery history
- critical flows are still difficult to test without real Bluetooth hardware

## Guiding Principles

### 1. Stay Bluetooth-first

Every architectural change must preserve the reality of A2DP devices:

- unstable connectivity
- changing PulseAudio/PipeWire sink identity and routing
- delayed availability after reconnect
- device-specific latency and buffering behavior

### 2. Move orchestration into explicit services

The system should be centered around a small set of runtime services rather than implicit shared state and route-driven coordination.

### 3. Normalize read models

UI and API consumers should read stable, typed snapshots instead of reaching into live runtime objects directly.

### 4. Version internal contracts

Parent/subprocess IPC, event payloads, and high-value internal models should have explicit schemas and backward-compatible evolution rules.

### 5. Make runtime behavior observable

Reconnects, re-anchors, sink routing corrections, MA sync gaps, and startup phases should be visible as structured operational data.

### 6. Prefer incremental migration

Do not attempt a full rewrite. Introduce new structures in parallel, migrate callers, and only remove legacy paths once new paths are stable.

## Target Architecture

The desired architecture is still a Bluetooth bridge, but with clearer layers.

### Runtime layer

- `BridgeOrchestrator`
  - owns startup sequencing
  - owns shutdown and restart semantics
  - coordinates bridge-wide services

- `DeviceRegistryService`
  - canonical source of truth for active, disabled, and released devices
  - publishes immutable device snapshots

- `BluetoothLifecycleService`
  - pairing, reconnect, release/reclaim, sink acquisition

- `DaemonProcessService`
  - parent/subprocess lifecycle
  - command dispatch
  - subprocess status ingestion

- `PlaybackHealthService`
  - zombie detection
  - re-anchor tracking
  - playback degradation classification

- `MaIntegrationService`
  - MA auth
  - MA now-playing and group state sync
  - control command routing

- `DiagnosticsService`
  - bridge-wide health summary
  - per-device event history
  - structured export and bugreport surfaces

### Read layer

- `DeviceSnapshot`
- `BridgeSnapshot`
- `StartupProgressSnapshot`
- `DeviceHealthSummary`
- `DeviceEventRecord`

Routes and UI should primarily read from these models.

### Event layer

Introduce a small internal event model for important lifecycle changes, for example:

- `device_connected`
- `device_disconnected`
- `sink_acquired`
- `sink_lost`
- `playback_started`
- `playback_stalled`
- `playback_reanchored`
- `ma_group_changed`
- `config_changed`

This does not need a complex framework. A simple in-process event publisher is enough.

### IPC layer

Parent/subprocess IPC should evolve into an explicit contract:

- `protocol_version`
- command envelope
- status envelope
- event envelope
- structured error payloads
- feature/capability flags

## Phase 1: Foundation and Quick Wins

### Goal

Create a stable foundation for future refactoring without changing the core runtime model.

### Workstreams

#### A. Typed read models

Add normalized bridge/device snapshot models and make routes consume them instead of live internal fields wherever practical.

Deliverables:

- `DeviceSnapshot`
- `BridgeSnapshot`
- snapshot-building helpers
- route migration for status/diagnostics/config read paths

#### B. Event history and health summaries

Track operational history per device:

- reconnect attempts
- disconnect reasons
- sink rediscovery attempts
- sink routing corrections
- re-anchor timestamps and counts
- zombie restart counts
- MA sync failures

Expose a compact health summary:

- status severity
- current degraded mode
- recent recovery actions
- active fallback mode, if any

#### C. Startup progress model

Expose startup phases so the UI and diagnostics can show where the bridge is blocked:

- config loaded
- adapters enumerated
- BT managers ready
- MA integration initialized
- devices restored
- sinks resolved
- subprocesses online

#### D. Mock runtime mode

Introduce a simulation mode for API/UI and integration tests:

- fake adapters
- fake BT devices
- fake subprocess statuses
- fake MA groups and metadata

This can start as a code-only debug/test mode before it becomes a user-facing feature.

### Exit Criteria

- API responses are built from normalized snapshot models
- diagnostics can explain why a device is unhealthy
- startup progress is visible
- key API/UI flows can be tested without real Bluetooth hardware

### Out of Scope

- no large orchestration rewrite yet
- no new backend abstraction yet
- no major UI redesign

## Phase 2: Orchestration Refactor

### Goal

Move the core lifecycle out of global shared state and into explicit services.

### Workstreams

#### A. Introduce `BridgeOrchestrator`

Responsibilities:

- startup sequencing
- dependency ordering
- shutdown behavior
- restart boundaries
- bridge-wide error containment

#### B. Introduce `DeviceRegistryService`

Responsibilities:

- register active devices
- store disabled/released devices
- publish immutable snapshots
- mediate device lookup for routes and services

#### C. Separate lifecycle services

Split current responsibilities into explicit services:

- `BluetoothLifecycleService`
- `DaemonProcessService`
- `PlaybackHealthService`
- `MaIntegrationService`

#### D. Reduce `state.py`

Convert `state.py` from a broad state owner into a compatibility layer or remove most of its current responsibilities.

### Exit Criteria

- routes no longer rely on runtime object internals for most reads
- orchestration lives in explicit services
- device ownership and lookup rules are centralized
- `state.py` is no longer the architectural center of the application

### Main Risk

This is the phase most likely to introduce runtime regressions. Migrate incrementally and preserve compatibility wrappers until new code paths are fully validated.

## Phase 3: Contracts, Events, and Operability

### Goal

Make the system easier to evolve and easier to operate.

### Workstreams

#### A. Versioned parent/subprocess IPC

Define explicit IPC models and contract tests.

Add:

- protocol version field
- explicit status schema
- explicit command schema
- structured subprocess errors
- capability negotiation for optional features

#### B. Internal event bus

Introduce a lightweight event system for key lifecycle events.

Use it for:

- diagnostics history
- UI updates
- startup progress
- optional hooks/webhooks

#### C. Hook and webhook model

Formalize operational hooks for:

- stream start
- stream stop
- reconnect start
- reconnect success
- reconnect failure
- device released/reclaimed
- update available

#### D. Config lifecycle improvements

Add:

- configuration validation report
- explicit migration path for stored config
- import/export validation messages
- safer separation between runtime state and user config

### Exit Criteria

- IPC is versioned and tested
- high-value lifecycle changes are modeled as events
- hooks have a consistent contract
- config changes are validated and migration-ready

## Phase 4: Product and UX Enhancements

### Goal

Use the stronger architecture to improve usability, onboarding, and diagnostics depth.

### Workstreams

#### A. Guided onboarding

Add guided flows for:

- adapter selection
- device assignment
- sink verification
- MA auth validation
- latency calibration support

#### B. Capability model

Model device/bridge capabilities explicitly, for example:

- battery-capable
- release/reclaim-capable
- preferred-format-capable
- MA-volume-capable
- hardware-volume-capable

#### C. Richer diagnostics exports

Add structured exports for:

- event timeline
- health summary
- sync behavior history
- adapter and sink recovery logs

### Exit Criteria

- a new user can understand setup failures with less guesswork
- diagnostics are useful for both users and maintainers
- device differences are explicit in the UI and API

## Phase 5: Broader Sendspin Alignment

### Goal

Borrow selectively from the broader Sendspin ecosystem once the bridge core is stable.

### Opportunities

#### A. Sync telemetry

Use ideas from `time-filter` and `sync-test`:

- drift trend reporting
- measured sync error surfaces
- structured re-anchor analysis
- optional calibration or export tooling

#### B. Role-aware evolution

Move closer to the wider Sendspin model where useful:

- richer metadata handling
- improved artwork handling
- stronger controller semantics around MA groups
- capability reporting aligned with protocol concepts

#### C. Future backend abstraction

Prepare for additional endpoint backends only after Bluetooth lifecycle contracts are stable.

Possible future backends:

- Bluetooth output backend
- local audio output backend
- web/cast companion endpoint

### Exit Criteria

- the bridge remains Bluetooth-first
- sync quality is observable, not inferred only from symptoms
- the architecture does not block future endpoint expansion

## Phase 6: Strategic Platform Work

This phase is optional and should only start if the project deliberately evolves beyond a focused Bluetooth bridge.

Potential areas:

- multiple bridge federation
- aggregated cross-bridge diagnostics
- unified control plane
- plugin or extension surfaces
- deployment-profile specialization

## What to Borrow from Other Projects

### From `Multi-SendSpin-Player-Container`

Adopt:

- explicit startup orchestrator
- startup progress tracking
- mock hardware/runtime strategy
- richer diagnostics surface
- cleaner service registration mindset

Do not copy blindly:

- a generic local-audio-first domain model
- heavy DI patterns without clear value
- service sprawl for simple flows

### From the Sendspin ecosystem

Adopt:

- clearer role and capability thinking
- explicit sync telemetry
- versioned contracts
- stronger separation between protocol core and platform-specific output logic

Do not adopt too early:

- broad backend abstraction before Bluetooth lifecycle refactor
- protocol-expansion work that distracts from BT reliability

## Execution Backlog

The backlog below is ordered by implementation value and dependency safety, not by estimated duration.

### Epic 1: Typed Read Models and Snapshot Builders

Outcome:

- routes and UI consume stable, normalized models

Backlog:

1. Add `DeviceSnapshot` dataclass/model
2. Add `BridgeSnapshot` dataclass/model
3. Add snapshot builders around current runtime objects
4. Migrate `/api/status*` and diagnostics routes to snapshots
5. Migrate config read enrichment to snapshot-based lookup
6. Add tests for snapshot serialization and missing-field behavior

Suggested PRs:

- PR 1: snapshot models and builders
- PR 2: route migration for status and diagnostics

### Epic 2: Device Health and Event History

Outcome:

- the bridge can explain current and recent device problems

Backlog:

1. Add `DeviceEventRecord`
2. Add per-device event ring buffer
3. Record reconnect, re-anchor, sink recovery, zombie restart, and MA sync failures
4. Add `DeviceHealthSummary`
5. Expose health and recent history in diagnostics API
6. Add tests for event retention and severity classification

Suggested PRs:

- PR 3: event history infrastructure
- PR 4: health summary and diagnostics exposure

### Epic 3: Startup Progress and Bridge Readiness

Outcome:

- startup issues become visible and debuggable

Backlog:

1. Add `StartupProgressSnapshot`
2. Model startup phases and current blocking step
3. Surface progress through API
4. Add simple UI hooks for progress display
5. Add tests for success and failure paths

Suggested PR:

- PR 5: startup progress model and API surface

### Epic 4: Mock Runtime / Simulator Mode

Outcome:

- critical flows are testable without real hardware

Backlog:

1. Add a mock adapter/device provider
2. Add fake subprocess state source
3. Add fake MA state provider
4. Allow routes and snapshot builders to run against mock runtime
5. Add integration tests for common UI/API scenarios

Suggested PRs:

- PR 6: mock runtime core
- PR 7: integration tests and developer documentation

### Epic 5: Bridge Orchestrator

Outcome:

- startup and shutdown logic is centralized

Backlog:

1. Introduce `BridgeOrchestrator`
2. Move bootstrap sequencing into the orchestrator
3. Move shutdown and cleanup semantics into the orchestrator
4. Keep compatibility wrapper paths until stable
5. Add integration tests for startup and shutdown ordering

Suggested PR:

- PR 8: orchestrator introduction with compatibility layer

### Epic 6: Device Registry Service

Outcome:

- device ownership and lookup become explicit

Backlog:

1. Introduce `DeviceRegistryService`
2. Move active client registration into the registry
3. Move disabled/released device metadata into the registry
4. Expose immutable registry snapshots
5. Migrate routes away from direct `state.py` lookups

Suggested PRs:

- PR 9: registry service
- PR 10: route and service migration to registry

### Epic 7: Lifecycle Service Split

Outcome:

- runtime responsibilities are easier to reason about and test

Backlog:

1. Extract `BluetoothLifecycleService`
2. Extract `DaemonProcessService`
3. Extract `PlaybackHealthService`
4. Extract `MaIntegrationService`
5. Reduce `SendspinClient` to a thinner per-device runtime component

Suggested PRs:

- PR 11: extract daemon and playback health service
- PR 12: extract Bluetooth lifecycle and MA integration service

### Epic 8: Versioned IPC Contracts

Outcome:

- subprocess communication can evolve safely

Backlog:

1. Define command envelope
2. Define status envelope
3. Add protocol version
4. Add structured subprocess error payloads
5. Add IPC contract tests
6. Add compatibility behavior for older payload assumptions if needed

Suggested PR:

- PR 13: versioned IPC contract

### Epic 9: Internal Event Model

Outcome:

- lifecycle changes become first-class runtime signals

Backlog:

1. Add lightweight event publisher
2. Publish high-value device and bridge events
3. Route diagnostics history through the event publisher
4. Integrate startup progress and optional hook triggers

Suggested PR:

- PR 14: event publisher and event migration

### Epic 10: Hook/Webhook Framework

Outcome:

- external integrations become predictable and extensible

Backlog:

1. Define hook event contract
2. Add start/stop/reconnect hook support
3. Add error and update-related hook support
4. Add validation and logging around hook execution
5. Add tests for payloads and failure reporting

Suggested PR:

- PR 15: formal hook framework

### Epic 11: Config Validation and Migration

Outcome:

- config changes are safer and more maintainable

Backlog:

1. Add explicit config schema validation
2. Add migration functions for persisted config versions
3. Improve import/export validation reporting
4. Separate runtime state from user-owned config where practical

Suggested PR:

- PR 16: config validation and migrations

### Epic 12: Onboarding and UX Improvements

Outcome:

- setup becomes easier for new users

Backlog:

1. Adapter assignment assistant
2. Sink verification helper
3. MA auth diagnostics assistant
4. Latency calibration guidance
5. UI exposure for health and startup progress

Suggested PRs:

- PR 17: backend support for onboarding flows
- PR 18: UI integration for onboarding and diagnostics

### Epic 13: Sync Telemetry and Sendspin Alignment

Outcome:

- sync quality becomes measurable and future extension becomes easier

Backlog:

1. Add richer sync telemetry model
2. Track drift and re-anchor patterns over time
3. Expose metrics through diagnostics exports
4. Improve capability reporting in a way that aligns with broader Sendspin concepts

Suggested PR:

- PR 19: sync telemetry and capability model

## Recommended PR Sequence

The safest order is:

1. snapshot models
2. event history
3. health summary
4. startup progress
5. mock runtime
6. orchestrator
7. registry
8. lifecycle service extraction
9. versioned IPC
10. internal events
11. hooks
12. config validation and migrations
13. onboarding improvements
14. sync telemetry

## Dependency Summary

- snapshot models should come before broad route migration
- event history should come before health summary
- startup progress can land before the orchestration rewrite
- mock runtime should land before heavy lifecycle refactors
- orchestrator should land before major service extraction
- registry should land before route de-coupling from runtime internals
- versioned IPC should land before adding many subprocess features
- capability model should come after snapshots and registry exist

## Definition of Done for the Roadmap

The roadmap can be considered successfully executed when:

- runtime ownership is explicit
- API and UI read from normalized models
- subprocess contracts are versioned
- Bluetooth failure and recovery paths are observable
- critical flows are testable without real hardware
- configuration is validated and migration-ready
- the architecture is ready for selective Sendspin ecosystem alignment without compromising Bluetooth reliability

## Guardrails

Do not:

- rewrite the bridge into a generic audio platform too early
- expand backend abstraction before lifecycle contracts are stable
- move multiple critical lifecycle responsibilities in one PR
- let diagnostics and mock mode lag behind refactors
- over-engineer the event layer when a small in-process publisher is enough

Do:

- migrate incrementally
- preserve compatibility shims while changing runtime ownership
- validate behavior after each structural change
- keep Bluetooth recovery paths as the top priority
