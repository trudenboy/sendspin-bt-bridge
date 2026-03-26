---
title: 2026-03-20 — Orchestrator extraction and onboarding guidance
description: March 20 shipped the bridge orchestrator extraction, lifecycle state machine, device registry, and a full operator-facing onboarding and recovery layer across v2.40.6 through v2.42.1
---

March 20 was the biggest internal refactoring day since the v2 architecture overhaul — and it shipped operator-visible results too. By the end of the day, the runtime had a dedicated bridge orchestrator, a lifecycle state machine, a device registry, and a config validation layer. On top of that, operators gained an onboarding checklist, a recovery assistant, and a redesigned diagnostics surface. Twenty-seven RC releases and two stable tags moved through the pipeline.

## What shipped

### Bridge orchestrator extraction

The core coordination logic that previously lived inside `sendspin_client.py` was extracted into a standalone `bridge_orchestrator.py` module. This was the culmination of a `services/` module split that also separated device registry, lifecycle state, status snapshot, and event publishing concerns.

The extraction mattered because the monolithic client class had grown beyond what was reasonable for a single module. With the orchestrator owning startup sequencing, device lifecycle transitions, and cross-device coordination, the remaining `SendspinClient` became a per-device subprocess wrapper with a much narrower surface area.

### Lifecycle state machine and device registry

Each device now moves through explicit lifecycle states instead of relying on ad-hoc status flags. The device registry tracks all known devices and their current lifecycle positions, making it possible for other modules — diagnostics, onboarding, recovery — to query device state without reaching into subprocess internals.

This also made status snapshots more reliable. Instead of copying mutable dicts at arbitrary moments, snapshots are now built from the registry's stable view of each device.

### Config validation endpoint

A new validation layer checks configuration payloads before they are persisted. This catches common operator mistakes — duplicate ports, invalid MAC formats, missing required fields — before the bridge restarts with a broken config. The validation endpoint is used by the web UI's save flow and can also be called directly from scripts or automations.

### Onboarding checklist and recovery assistant

The operator-facing layer gained two complementary tools:

- **Onboarding checklist** — a step-by-step guidance flow for new installs that walks operators through Bluetooth pairing, Music Assistant connection, and first-playback verification. It stays accessible from the header and tracks completion against real device and MA state.
- **Recovery assistant** — surfaces actionable guidance when something goes wrong: disconnected speakers, failed MA connections, stale configs. Instead of dumping raw diagnostics, it offers specific next actions.

### Diagnostics redesign

The diagnostics surface was split into a simpler `Overview` panel for daily use and an `Advanced diagnostics` view for expert troubleshooting. Per-section copy helpers and expandable raw payload details replaced the previous wall-of-JSON approach. This made diagnostics usable for operators who are not reading subprocess logs.

### Compact UI design system

The entire dashboard moved to a consistent compact design language. Grid-view playback cards now use larger album-art thumbnails, and the shared action/badge/chip system replaced scattered local CSS overrides. This visual consistency wave touched the login screen, guidance surfaces, notices, configuration panels, and media transport controls.

### CI and demo refresh (v2.40.6)

The day started with the `2.40.6` stable release, which closed out the previous wave's CI release workflow changes, demo fixture refreshes, theme auto-mode controls, and MA async flow improvements. That stable tag provided the clean baseline for the orchestrator extraction work that followed.

## Why this matters

`v2.42.1` was the first stable release where the bridge's internal architecture matched the complexity of what operators were actually doing with it. The orchestrator, registry, and lifecycle layers gave the runtime a foundation that later features — room metadata, standby mode, security hardening — could build on without further monolith surgery.

The onboarding and recovery layers changed the project's relationship with operators. Instead of expecting everyone to read logs and edit JSON, the bridge now meets new users with guidance and offers experienced operators recovery actions when things break.

## Follow-up

This entry is the baseline for the scan modal and guidance refinements that shipped the next day, and for the room metadata and HA area integration that followed later in the week.
