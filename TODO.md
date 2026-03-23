# TODO (v3 baseline)

This TODO now tracks the **v3 wave**, starting from the shipped `v2.46.x` runtime instead of old pre-foundation backlog.

## Current baseline already shipped

The following are considered part of the baseline, not open roadmap items:

- lifecycle/orchestration foundation, typed snapshots, explicit IPC envelopes
- normalized onboarding, recovery guidance, diagnostics, and bugreport tooling
- Home Assistant and Music Assistant integration hardening
- room metadata, transfer readiness, fast-handoff support for room-aware scenarios
- stronger Docker/RPi diagnostics for user-scoped PipeWire/PulseAudio issues

## Now (finish before broad v3 expansion)

- [ ] **Consolidate guidance ownership for non-empty installs** - keep full onboarding dominant only for the true empty state and let mature installs rely on calmer header/banner guidance.
- [ ] **Add grouped recovery action previews** - preview affected devices and confirm bulk recovery intent before multi-device actions run.
- [ ] **Polish compact/mobile recovery density** - reduce noisy issue pills and keep compact actions readable on small screens.
- [ ] **Align blocked-state hints with top-level guidance** - let one visible owner explain root causes instead of duplicating row-level microcopy.

## V3-1: AI-assisted diagnostics and deployment planning

- [ ] **Define the AI boundary** - local/manual vs external providers, redaction rules, operator approval model, and explicit no-secrets-by-default policy.
- [ ] **Create a canonical diagnostics bundle** - machine-readable export combining runtime state, device snapshots, recovery timeline, deployment facts, and preflight output.
- [ ] **Add deployment planner foundations** - recommend install path (HA add-on / Docker / RPi / LXC), ports, mounts, `AUDIO_UID`, adapter mapping, and initial latency guidance.
- [ ] **Add AI diagnostics summaries** - turn diagnostics bundles into plain-language likely causes, safe next steps, and support-ready summaries.
- [ ] **Add support bundle / prompt export** - let operators export a sanitized context bundle for AI-assisted troubleshooting without manual copy-paste.

## V3-2: Automatic delay tuning and sync intelligence

- [ ] **Add delay telemetry foundations** - collect drift/timing data that can support per-device delay decisions.
- [ ] **Expose sync health explicitly** - diagnostics/operator surfaces should show drift, measurement quality, and tuning confidence.
- [ ] **Add a guided delay calibration flow** - measure and suggest `static_delay_ms` instead of forcing raw manual trial-and-error.
- [ ] **Add approve/apply/rollback UX for delay suggestions** - recommendations must be visible and reversible.
- [ ] **Add bounded optional auto-tuning** - conservative automatic delay refinement only where confidence is high enough.

## V3-3: Centralized multi-bridge management

- [ ] **Define stable bridge instance identity** - registry semantics for bridge host/version/room/adapter ownership.
- [ ] **Add fleet overview** - aggregate bridge health, device inventory, room coverage, and update status.
- [ ] **Detect cross-bridge conflicts** - duplicate speakers, overlapping rooms, inconsistent naming, and stale bridge identities.
- [ ] **Add fleet bulk operations** - restart, diagnostics rerun, compare/export/import configuration sets, and version/channel checks.
- [ ] **Add fleet event timeline** - centralize recovery and health events across bridges.

## V3-4: Backend abstraction and config schema v2

- [ ] **Define `AudioBackend` contract** - lifecycle, capability, health, and diagnostics semantics for backends.
- [ ] **Wrap Bluetooth behind the backend contract** - keep Bluetooth as backend #1 and preserve current behavior.
- [ ] **Introduce config schema v2** - player/backend-oriented config instead of Bluetooth-only assumptions.
- [ ] **Add migration tooling and compatibility loading** - safe transition from the current config schema.
- [ ] **Prove the first adjacent backend** - `LocalSinkBackend` first, then optionally `ALSADirectBackend`.

## V3-5: Selective expansion after stability

- [ ] **USB audio auto-discovery**
- [ ] **System-wide audio runtime option for RPi/embedded hosts** - support a non-user-scoped PulseAudio/PipeWire deployment mode so Bluetooth containers are not coupled to per-user login sessions.
- [ ] **Richer sync/drift telemetry across bridges and groups**
- [ ] **Snapcast/VBAN strategy track**
- [ ] **Home Assistant custom component / HACS strategy**
- [ ] **Plugin or extension surface**

## Explicitly not a v3 goal

- [ ] **Do not turn v3 into a giant rewrite** - migrations must stay incremental.
- [ ] **Do not make AI mandatory** - all diagnostics and deployment flows must remain usable without AI.
- [ ] **Do not let backend expansion outrun Bluetooth reliability** - new backends are optional proof points, not the core product.
