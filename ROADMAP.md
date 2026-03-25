# Roadmap

## Purpose

This roadmap is written for the **v3 wave**, starting from the reality already shipped in `v2.46.x+`.

v3 is **not** a from-scratch rewrite. The project already has:

- explicit bridge lifecycle and orchestration seams
- typed status and diagnostics read models
- normalized onboarding, recovery, and operator-guidance surfaces
- config migration and validation flows
- room, readiness, and handoff foundations for room-aware Music Assistant scenarios
- stronger Docker and Raspberry Pi diagnostics for real deployment environments
- versioned subprocess IPC and stable operator-facing diagnostics endpoints

The roadmap should therefore answer a different question:

- what still needs to be finished before the runtime is stable enough for v3 expansion
- which product bets are important enough to define v3
- how to add those bets without regressing Bluetooth reliability

## Priority adjustment for v3

The first major v3 expansion is now **wired and USB audio support**, not a late-stage optional track.

That changes the sequencing:

1. finish the last operator polish gaps
2. establish backend and config contracts
3. ship the first adjacent backend for USB DACs and wired outputs
4. add the observability and tuning needed to run Bluetooth and wired players well
5. expand into AI-assisted support and multi-bridge fleet management after the single-bridge multi-backend story is real

Bluetooth still remains the primary and most battle-tested runtime. Wired and USB support should widen the product without displacing that core.

## Product thesis for v3

Sendspin BT Bridge v3 should become a **Bluetooth-first, room-aware, multi-backend-capable player runtime** for Music Assistant.

That means keeping Bluetooth reliability as the core product while adding five major capabilities on top:

1. **A backend abstraction layer and config schema that can safely host more than Bluetooth**
2. **USB DAC and wired audio support as the first adjacent backend**
3. **Audio health visibility plus delay and sync intelligence**
4. **AI-assisted diagnostics and deployment planning**
5. **Centralized management of multiple bridge instances**

## Starting baseline from v2

The roadmap treats the following as already-established foundations:

- Bluetooth remains the primary and most battle-tested runtime
- onboarding, recovery guidance, diagnostics, and bugreport tooling are real operator-facing surfaces
- bridge and device health, recent events, and blocked-action reasoning are explicit enough to build on
- room metadata, transfer readiness, and fast-handoff profiles already exist for room-following scenarios
- Home Assistant and Music Assistant integration are part of the normal product path, not an afterthought
- Docker and Raspberry Pi startup diagnostics already surface runtime UID, audio socket path, socket ownership, and live `pactl` probe status
- subprocess IPC, config migration, and diagnostics endpoints already behave like versioned product contracts

## North-star outcomes

v3 is successful when the project can do all of the following without becoming fragile or opaque:

1. A single bridge is boring and reliable in HA, Docker, Raspberry Pi, and LXC environments.
2. The same bridge can host Bluetooth players and at least one wired or USB-backed player type with a coherent operator UX.
3. Operators can see signal path, health, and sync status instead of inferring them from logs.
4. Speaker delay tuning becomes much less manual.
5. Operators can ask the system for a deployment plan or a diagnostics explanation and get a useful answer quickly.
6. Multiple bridge instances can later be understood and managed as one fleet instead of isolated boxes.

## Phase V3-0: Finish pre-v3 operator polish

### Goal

Close the last major v2 UX gaps so v3 starts from a calmer and more explicit operator surface.

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

## Phase V3-1: Backend abstraction and config schema v2

### Goal

Prepare the bridge for wired and USB expansion without destabilizing the shipped Bluetooth runtime.

### Scope

#### Epic 1. Backend contract

- define an `AudioBackend`-style contract for lifecycle, capabilities, health, diagnostics, and room metadata
- wrap the existing Bluetooth runtime behind that contract first
- keep subprocess and control-plane contracts backend-agnostic where practical

#### Epic 2. Config schema v2

- move from a Bluetooth-device-only model to player and backend-oriented configuration
- add compatibility loading and migration tooling from the current schema
- keep downgrade assumptions explicit and documented

#### Epic 3. Contract guardrails

- extend IPC, status snapshots, diagnostics payloads, and API contracts so new backend fields can land incrementally
- keep compatibility rules explicit for older configs and deployed subprocesses
- document the stable contract surfaces needed for future backend work

### Exit criteria

- Bluetooth remains the most stable backend
- config migration is incremental and safe
- the runtime can describe backend-neutral players without pretending Bluetooth is the only shape

## Phase V3-2: USB DAC and wired audio backend

### Goal

Support USB DACs and wired sound cards as Sendspin players — the clearest adjacent product win and the first real proof that the backend abstraction is worth having.

### Scope

#### Epic 4. Wired device enumeration

- detect ALSA and PulseAudio or PipeWire output sinks from `pactl list sinks` and `aplay -l`
- filter and classify likely outputs: USB DAC, built-in audio, HDMI, virtual
- present discovered devices in the web UI with hardware details and backend type

#### Epic 5. Direct sink player type

- create a player type that spawns a Sendspin daemon subprocess with a non-Bluetooth sink such as `alsa_output.usb-*`
- avoid Bluetooth pairing and reconnect lifecycle for direct sinks while reusing existing subprocess IPC, volume control, and status reporting
- support per-device volume persistence and mute state

#### Epic 6. Device aliasing and room mapping

- allow operators to assign friendly room names to raw ALSA and PulseAudio identifiers
- persist aliases in config and display them consistently across the UI
- support rename without forcing a full player recreation where practical

#### Epic 7. Hotplug and discovery follow-up

- watch for wired and USB device appearance or disappearance
- notify the UI when a new sink becomes available or disappears
- optionally allow operator-approved player creation for newly detected USB DACs

### Exit criteria

- USB DACs and wired outputs appear in the UI alongside Bluetooth speakers
- operators can create and manage wired players with the same general workflow as Bluetooth players
- no regression in Bluetooth reliability
- the subprocess model cleanly supports both backend types

## Phase V3-2.5: Custom PulseAudio sinks (combine and remap)

### Goal

Expose PulseAudio's virtual sink capabilities via the web UI once wired and USB foundations exist.

This phase is intentionally **parallel-friendly** after V3-2 lands. It should not block the first wired and USB release, but it becomes much more valuable once non-Bluetooth outputs are real product surfaces.

### Scope

#### Epic 8. Combine sink creation

- add web UI flows to select 2+ sinks and create a `module-combine-sink`
- target use cases such as party mode and open floor plans
- include a test-tone or route verification action

#### Epic 9. Remap sink creation

- add web UI flows to extract specific channels from multi-channel devices via `module-remap-sink`
- target use cases such as splitting a 4-channel USB DAC into two stereo zones or creating mono outputs
- support standard PulseAudio channel-name mapping

#### Epic 10. Sink lifecycle management

- persist custom sinks in config and recreate them on restart
- show state, configuration summary, and delete actions
- validate master and slave sink existence before attempting creation

### Exit criteria

- operators can create combine and remap sinks without touching `pactl` directly
- custom sinks survive restarts and can appear in the player selection flow
- failures are explicit when prerequisite sinks are unavailable

## Phase V3-3: Audio health dashboard and signal path visibility

### Goal

Give operators real-time visibility into audio quality, backend state, sync health, and the end-to-end signal path.

### Scope

#### Epic 11. Per-device audio telemetry panel

- expose current codec, sample rate, buffer and stream state, uptime, reconnect count, and resolved output sink where available
- pull telemetry from subprocess status lines, bridge state, and backend-specific callbacks
- update in real time via the existing SSE status stream

#### Epic 12. Signal path visualization

- render the end-to-end path for each backend type:
  - MA → Sendspin → subprocess → PulseAudio or PipeWire sink → Bluetooth A2DP → speaker
  - MA → Sendspin → subprocess → PulseAudio or ALSA sink → wired speaker or DAC
- show measured or estimated latency at each hop where available
- indicate bottlenecks or degraded hops such as codec fallback, sink mismatch, or missing route ownership

#### Epic 13. Sync health indicators

- add green, yellow, and red sync badges on dashboard device cards based on drift magnitude or measurement quality
- surface alerts when sync degrades past configurable thresholds
- integrate sync-specific issues into operator guidance instead of burying them in logs

### Exit criteria

- operators can see codec, sample rate, sink route, and sync health without reading logs
- the signal path is understandable at a glance for both Bluetooth and wired players
- degradation is surfaced proactively instead of discovered by ear

## Phase V3-4: Automatic delay tuning and sync intelligence

### Goal

Reduce manual `static_delay_ms` guesswork and make sync decisions more measurable and explainable.

### Scope

#### Epic 14. Delay telemetry foundation

- capture timing and drift telemetry that can support per-device delay decisions
- expose sync health, drift, confidence, and measurement quality at the diagnostics and operator level
- distinguish between "we can measure something" and "we trust this enough to recommend a tuning change"

#### Epic 15. Guided delay calibration

- add a manual calibration flow that can measure and suggest `static_delay_ms`
- show recommended value, confidence, and before/after comparison
- allow approve, apply, and rollback instead of forcing raw manual edits

#### Epic 16. Bounded auto-tuning

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

#### Epic 17. Structured diagnostics bundles

- define a canonical machine-readable diagnostics bundle that combines:
  - bridge and runtime state
  - device snapshots
  - recovery timeline
  - deployment environment facts
  - preflight results
  - backend identity and routing facts
- make the bundle stable enough for support tooling, bug reports, and future AI consumers

#### Epic 18. Deployment planner

- add a planner that can inspect environment facts and suggest:
  - recommended install path (HA add-on, Docker, Raspberry Pi, LXC)
  - required mounts and capabilities
  - likely `AUDIO_UID`, port, and adapter configuration
  - when wired or USB outputs are a better fit than Bluetooth for a room
  - safe next steps for first deployment
- keep the planner operator-facing: generate plans and config suggestions, not silent changes

#### Epic 19. AI diagnostics summarizer

- summarize failures in plain language from diagnostics data
- rank likely root causes and safe next actions
- produce support-ready summaries for GitHub or forum issues
- allow prompt export or support bundle export for external or local AI analysis

#### Epic 20. AI safety and privacy boundaries

- redact secrets before any external AI handoff
- support pluggable providers and a local or manual mode
- require explicit operator approval before applying suggested changes
- keep non-AI diagnostics fully usable on their own

### Exit criteria

- diagnostics bundles are stable and structured
- deployment planning is useful for real users, especially HA, Docker, Raspberry Pi, and mixed Bluetooth or wired installs
- AI-generated explanations improve support without becoming required for normal operation

## Phase V3-6: Centralized multi-bridge control plane

### Goal

Turn multiple bridge instances into a manageable fleet after the single-bridge multi-backend story is solid.

### Scope

#### Epic 21. Bridge registry and fleet identity

- define stable bridge instance identity and registration semantics
- aggregate version, host, adapter, room, backend, and health metadata across bridges
- detect duplicate speakers, overlapping rooms, and inconsistent bridge naming

#### Epic 22. Fleet overview and bulk operations

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

#### Epic 23. Fleet event timeline and policy surfaces

- centralize event and recovery timelines across bridges
- add fleet-level webhook and telemetry views
- allow higher-level policies such as room ownership or update-channel consistency

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

## Cross-cutting guardrails

### 1. Bluetooth reliability stays first

No v3 theme should regress real Bluetooth deployments in HA, Docker, Raspberry Pi, or LXC.

### 2. Wired and USB support must be additive, not a rewrite

The first adjacent backend should reuse proven runtime seams, diagnostics, and subprocess patterns rather than replacing them.

### 3. AI must be optional and operator-controlled

- no mandatory cloud dependency
- no silent external sharing of sensitive config or state
- no hidden auto-remediation without explicit approval

### 4. Delay automation must be bounded and explainable

The system may suggest or conservatively auto-apply delay changes, but never as opaque magic.

### 5. Fleet management must stay additive

A single bridge should remain simple to deploy and operate. Fleet management should not become a requirement for basic use.

### 6. Migrations, docs, and tests must ship with each phase

v3 should add compatibility layers, migrate callers and config gradually, document new contracts as they land, and extend tests alongside each new backend or diagnostics surface.

## Out of scope for early v3

- a giant all-at-once rewrite
- direct app-specific protocols for MassDroid or other clients
- speculative backends before the backend contract exists
- AI-driven silent configuration edits
- fleet-first complexity before single-bridge multi-backend value is proven
- replacing operator diagnostics with AI instead of complementing them

## Likely first v3 milestone

A realistic `v3.0.0-rc.1` should include:

- finished V3-0 guidance and recovery polish
- backend abstraction and config schema v2 as the foundation
- the first wired and USB backend with manual operator creation flow
- baseline audio health visibility and signal path publication
- delay telemetry foundations and a manual calibration path
- structured diagnostics bundle foundations for future planner and AI work

That is enough to make v3 feel materially different — **"Bluetooth-first multiroom with a real wired/USB expansion path, shared runtime contracts, and much clearer audio visibility"** — without forcing the full AI or fleet story into the first RC.
