# Roadmap

## Purpose

This roadmap is now written for the **v3 wave**, starting from the reality already shipped in `v2.46.x`.

v3 is **not** a from-scratch rewrite. The project already has:

- explicit bridge lifecycle and orchestration seams
- typed status/diagnostics read models
- normalized recovery and operator-guidance surfaces
- config migration/validation flows
- room/readiness/handoff foundations for room-aware Music Assistant scenarios
- stronger Docker/Raspberry Pi diagnostics for real deployment environments

The roadmap should therefore answer a different question:

- what must be finished before the runtime is stable enough for v3 expansion
- which new product bets are valuable enough to define v3
- how to add those bets without regressing Bluetooth reliability

## Product thesis for v3

Sendspin BT Bridge v3 should become a **Bluetooth-first, room-aware, fleet-manageable player runtime** for Music Assistant.

That means keeping Bluetooth reliability as the core product while adding four major capabilities on top:

1. **AI-assisted diagnostics and deployment planning**
2. **Automatic delay tuning and sync intelligence**
3. **Centralized management of multiple bridge instances**
4. **A backend abstraction layer for selective non-Bluetooth expansion**

## Starting baseline from v2

The roadmap treats the following as already established foundations:

- Bluetooth remains the primary and most battle-tested runtime
- onboarding, recovery guidance, diagnostics, and bugreport tooling are real operator-facing surfaces
- bridge/device health, recent events, and blocked-action reasoning are explicit enough to build on
- room metadata, transfer readiness, and fast-handoff profiles now exist for room-following scenarios
- Home Assistant and Music Assistant integration are part of the normal product path, not an afterthought
- Docker/RPi startup diagnostics now surface runtime UID, audio socket path, socket ownership, and live `pactl` probe status

## North-star outcomes

v3 is successful when the project can do all of the following without becoming fragile or opaque:

1. A single bridge is boring and reliable in HA, Docker, Raspberry Pi, and LXC environments.
2. Operators can ask the system for a deployment plan or a diagnostics explanation and get a useful answer quickly.
3. Speaker delay tuning becomes much less manual.
4. Multiple bridge instances can be understood and managed as one fleet instead of isolated boxes.
5. Bluetooth is still the best-supported backend while one adjacent backend proves the abstraction layer.

## Phase V3-0: Finish pre-v3 operator polish

### Goal

Close the last major v2 UX gaps so v3 starts from a calmer and more explicit operator surface.

### Scope

- keep full onboarding dominant only for the true empty state
- preview and confirm grouped recovery actions before multi-device operations run
- reduce compact/mobile recovery noise (`top issue + N more`, less duplicate copy)
- align blocked row-level hints with one top-level guidance owner
- keep diagnostics and recovery detail available even when top-level guidance is compact

### Exit criteria

- mature installs are calm by default
- grouped recovery actions feel deliberate and understandable
- top-level guidance owns the main explanation instead of duplicated microcopy

## Phase V3-1: AI-assisted diagnostics and deployment planning

### Goal

Use AI as an **operator copilot**, not as a hidden control plane.

### Scope

#### Epic 1. Structured diagnostics bundles for AI consumption

- define a canonical machine-readable diagnostics bundle that combines:
  - bridge/runtime state
  - device snapshots
  - recovery timeline
  - deployment environment facts
  - preflight results
- make the bundle stable enough for support tooling, bug reports, and AI consumers

#### Epic 2. Deployment planner

- add a planner that can inspect environment facts and suggest:
  - recommended install path (HA add-on / Docker / RPi / LXC)
  - required mounts/capabilities
  - likely `AUDIO_UID`, port, and adapter configuration
  - initial latency/delay guidance
  - safe next steps for first deployment
- keep the planner operator-facing: generate plans and config suggestions, not silent changes

#### Epic 3. AI diagnostics summarizer

- summarize failures in plain language from diagnostics data
- rank likely root causes and safe next actions
- produce support-ready summaries for GitHub/forum issues
- allow prompt export / support bundle export for external or local AI analysis

#### Epic 4. AI safety and privacy boundaries

- redact secrets before any external AI handoff
- support pluggable providers and a local/manual mode
- require explicit operator approval before applying suggested changes
- keep non-AI diagnostics fully usable on their own

### Exit criteria

- diagnostics bundles are stable and structured
- deployment planning is useful for real users, especially Docker/RPi and HA installs
- AI-generated explanations improve support without becoming required for normal operation

## Phase V3-2: Automatic delay tuning and sync intelligence

### Goal

Reduce manual `static_delay_ms` guesswork and make sync health more measurable.

### Scope

#### Epic 5. Delay telemetry foundation

- capture timing/drift telemetry that can support per-device delay decisions
- expose sync health, drift, and confidence at the diagnostics/operator level
- distinguish between measurement quality and tuning recommendation quality

#### Epic 6. Guided delay calibration

- add a manual calibration flow that can measure and suggest `static_delay_ms`
- show recommended value, confidence, and before/after comparison
- allow approve/apply/rollback instead of forcing raw manual edits

#### Epic 7. Bounded auto-tuning

- add optional conservative automatic adjustment for devices with stable measurement quality
- keep adjustments bounded, visible, and reversible
- surface when auto-tuning is disabled, uncertain, or recently rolled back

### Exit criteria

- most users can reach a good delay value without trial-and-error editing
- delay recommendations are visible and explainable
- any automatic tuning is conservative and operator-traceable

## Phase V3-3: Centralized multi-bridge control plane

### Goal

Turn multiple bridge instances into a manageable fleet.

### Scope

#### Epic 8. Bridge registry and fleet identity

- define stable bridge instance identity and registration semantics
- aggregate version, host, adapter, room, and health metadata across bridges
- detect duplicate speakers, overlapping rooms, and inconsistent bridge naming

#### Epic 9. Fleet overview and bulk operations

- build a centralized overview for:
  - bridge health
  - device inventory
  - room coverage
  - recovery attention
  - update status
- add safe bulk actions such as:
  - restart selected bridges
  - re-run diagnostics on selected bridges
  - export/import configuration sets
  - compare configs and versions across the fleet

#### Epic 10. Fleet event timeline and policy surfaces

- centralize event/recovery timelines across bridges
- add fleet-level webhooks/telemetry views
- allow higher-level policies such as room ownership or update-channel consistency

### Exit criteria

- operators can reason about multiple bridges as one system
- duplicate/conflicting configuration becomes easier to spot before it causes runtime issues
- fleet operations do not replace single-bridge simplicity; they extend it

## Phase V3-4: Backend abstraction and config schema v2

### Goal

Prepare the bridge for selective non-Bluetooth expansion without destabilizing the shipped Bluetooth runtime.

### Scope

#### Epic 11. Backend contract

- define an `AudioBackend`-style contract for lifecycle, capabilities, health, and diagnostics
- wrap the existing Bluetooth runtime behind that contract first
- keep subprocess and control-plane contracts backend-agnostic where practical

#### Epic 12. Config schema v2

- move from a Bluetooth-device-only model to player/backend-oriented configuration
- add compatibility loading and migration tooling from the current schema
- keep downgrade assumptions explicit and documented

#### Epic 13. First adjacent backend

- prove the abstraction with one high-value adjacent backend:
  - `LocalSinkBackend` for PipeWire/PulseAudio
  - optionally `ALSADirectBackend` for minimal environments
- keep diagnostics and operator UX coherent across backend types

### Exit criteria

- Bluetooth remains the most stable backend
- config migration is incremental and safe
- one adjacent backend proves the abstraction with real product value

## Phase V3-5: Selective expansion after core stability

### Candidate work

Only start these once earlier phases are stable and demand is proven:

- USB audio auto-discovery
- system-wide audio runtime / non-user-scoped socket support for Raspberry Pi and other embedded hosts that struggle with per-user PulseAudio or PipeWire sessions
- richer sync/drift telemetry across groups and bridges
- Snapcast/VBAN/backend strategy tracks
- multi-bridge federation beyond a single control plane
- Home Assistant custom component / HACS strategy
- plugin or extension surfaces

## Cross-cutting guardrails

### 1. Bluetooth reliability stays first

No v3 theme should regress real Bluetooth deployments in HA, Docker, Raspberry Pi, or LXC.

### 2. AI must be optional and operator-controlled

- no mandatory cloud dependency
- no silent external sharing of sensitive config/state
- no hidden auto-remediation without explicit approval

### 3. Delay automation must be bounded and explainable

The system may suggest or conservatively auto-apply delay changes, but never as opaque magic.

### 4. Fleet management must stay additive

A single bridge should remain simple to deploy and operate. Fleet management should not become a requirement for basic use.

### 5. Migrations must stay incremental

v3 should add compatibility layers, migrate callers/config gradually, and remove legacy paths only after the new path is proven.

## Out of scope for early v3

- a giant all-at-once rewrite
- direct app-specific protocols for MassDroid or other clients
- speculative backends before the backend contract exists
- AI-driven silent configuration edits
- replacing operator diagnostics with AI instead of complementing them

## Likely first v3 milestone

A realistic `v3.0.0-rc.1` should include:

- finished V3-0 guidance/recovery polish
- structured diagnostics bundle foundations
- a first operator-facing deployment planner draft
- delay telemetry foundations and a manual calibration path
- the first fleet identity/inventory surfaces

That is enough to make v3 feel materially different without forcing the entire backend-expansion story into the first RC.
