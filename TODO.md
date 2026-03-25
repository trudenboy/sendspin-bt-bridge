# TODO (aligned with `ROADMAP.md`)

This TODO tracks the **v3 wave** against the current codebase. It now separates:

- shipped foundations that should stay visible as context
- open work ordered by the current roadmap priority
- later candidate work that should not jump ahead of the roadmap

## Current baseline already shipped

These items are already part of the baseline and should not be treated as open roadmap work:

- [x] Lifecycle and orchestration foundation, typed snapshots, and explicit IPC envelopes
- [x] Normalized onboarding, recovery guidance, diagnostics, and bugreport tooling
- [x] Home Assistant and Music Assistant integration hardening
- [x] Room metadata, transfer readiness, and fast-handoff support for room-aware scenarios
- [x] Stronger Docker and Raspberry Pi diagnostics for PipeWire and PulseAudio deployment issues

## V3-0: Finish pre-v3 operator polish

Foundation already present:

- [x] Guidance ownership foundation for empty-state vs mature installs
- [x] Grouped recovery action foundation for multi-device actions

Still open:

- [ ] Add grouped recovery action previews and explicit confirmation before bulk actions run
- [ ] Polish compact and mobile recovery density (`top issue + N more`, calmer issue pills, clearer compact actions)
- [ ] Finish aligning blocked-state hints with one top-level guidance owner instead of duplicated microcopy

## V3-1: Backend abstraction and config schema v2

Foundation already present:

- [x] Config schema v1 migration and validation foundation
- [x] Versioned IPC plus stable diagnostics and status contract foundations

Still open:

- [ ] Define an `AudioBackend` contract for lifecycle, capabilities, health, diagnostics, and room metadata
- [ ] Wrap the current Bluetooth runtime behind the backend contract as backend #1
- [ ] Introduce config schema v2 with player and backend-oriented config instead of Bluetooth-only assumptions
- [ ] Add migration tooling and compatibility loading from schema v1 to schema v2
- [ ] Extend snapshots, diagnostics, and API contracts with backend-neutral player identity

## V3-2: USB DAC and wired audio backend

Still open:

- [ ] Enumerate wired and USB outputs from PulseAudio, PipeWire, and ALSA
- [ ] Add a direct-sink player type for non-Bluetooth outputs
- [ ] Add aliasing and room mapping for raw wired and USB sink identifiers
- [ ] Add wired and USB hotplug or discovery follow-up after the first backend lands

## V3-2.5: Custom PulseAudio sinks

Still open:

- [ ] Add combine-sink creation flow for party mode and open-floor-plan layouts
- [ ] Add remap-sink creation flow for split-zone and multichannel DAC scenarios
- [ ] Persist and manage custom sink lifecycle across restart, validation, and deletion flows

## V3-3: Audio health dashboard and signal path visibility

Foundation already present:

- [x] Device health, operator diagnostics, and latency recommendation foundations

Still open:

- [ ] Expose codec, sample rate, sink route, uptime, and backend-aware audio telemetry per device
- [ ] Publish signal path visibility for Bluetooth and wired backends
- [ ] Add explicit sync health indicators and sync-degraded guidance surfaces

## V3-4: Automatic delay tuning and sync intelligence

Foundation already present:

- [x] Manual latency recommendation foundation

Still open:

- [ ] Add real drift telemetry and tuning-confidence signals for per-device delay decisions
- [ ] Add a guided per-device `static_delay_ms` calibration flow instead of manual trial-and-error edits
- [ ] Add approve, apply, and rollback UX for delay recommendations
- [ ] Add bounded optional auto-tuning only where confidence is high enough

## V3-5: AI-assisted diagnostics and deployment planning

Foundation already present:

- [x] Diagnostics, bugreport, preflight, and export foundations

Still open:

- [ ] Define the AI boundary: local/manual vs external providers, redaction rules, and operator approval model
- [ ] Create a canonical AI-ready diagnostics bundle schema on top of existing diagnostics foundations
- [ ] Add deployment planner foundations for install path, ports, mounts, `AUDIO_UID`, adapter mapping, and initial latency guidance
- [ ] Add AI diagnostics summaries with safe next steps and support-ready explanations
- [ ] Add support bundle or prompt export for sanitized AI-assisted troubleshooting

## V3-6: Centralized multi-bridge management

Foundation already present:

- [x] Duplicate-device and conflict-check foundations inside a single bridge

Still open:

- [ ] Define stable bridge instance identity for host, version, room, adapter, and backend ownership
- [ ] Add fleet overview for bridge health, device inventory, room coverage, and update status
- [ ] Detect cross-bridge conflicts such as duplicate speakers, overlapping rooms, and stale identities
- [ ] Add fleet bulk operations for restart, diagnostics rerun, compare, export, and import flows
- [ ] Add fleet event timeline across bridge instances

## V3-7: Selective expansion after core stability

Still open:

- [ ] Add a system-wide audio runtime option for Raspberry Pi and embedded hosts that struggle with user-scoped PipeWire or PulseAudio
- [ ] Add richer sync and drift telemetry across groups and bridges
- [ ] Explore Snapcast and VBAN backend strategy tracks
- [ ] Decide Home Assistant custom component or HACS strategy
- [ ] Design a plugin or extension surface

## Post-roadmap candidate work

These are still valid ideas, but they should not jump ahead of the roadmap phases above.

### TTS and announcement support

Priority: medium-high after the main v3 roadmap phases.

- [ ] Add a TTS injection endpoint for device-targeted announcements
- [ ] Add priority audio ducking and restore behavior around announcements
- [ ] Add Home Assistant webhook integration for announcements
- [ ] Add an announcement queue for sequential playback

### Hardware power control

Priority: medium after the main v3 roadmap phases.

- [ ] Add Home Assistant entity power binding for amps, plugs, or relays
- [ ] Add idle detection for delayed power-off automation
- [ ] Explore direct USB relay support as a stretch goal

## Explicitly not an early-v3 goal

- Do not turn v3 into a giant rewrite; migrations must stay incremental
- Do not make AI mandatory; diagnostics and deployment flows must remain usable without AI
- Do not let backend expansion outrun Bluetooth reliability; new backends are additive proof points, not the core product
