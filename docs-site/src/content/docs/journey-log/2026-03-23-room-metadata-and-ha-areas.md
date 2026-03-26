---
title: 2026-03-23 — Room metadata and HA area integration
description: March 23 introduced stable room metadata, Home Assistant area registry integration, transfer readiness contracts, user-scoped Docker audio, and recovery timeline filters across v2.45.0 and v2.46.0
---

March 23 was the day the bridge started understanding where speakers physically live. Twenty-eight RC releases across two stable tags delivered room metadata, Home Assistant area registry integration, transfer readiness contracts, and a fundamental change to how Docker containers handle audio permissions.

## What shipped

### Home Assistant area registry integration

Home Assistant ingress sessions can now fetch the HA area registry directly into the bridge's config UI. When editing a device's `Bridge name`, operators see one-click room suggestions pulled from their actual HA area hierarchy. Bluetooth adapters can also surface exact area matches from the HA device registry.

This integration only activates in HA add-on mode where ingress sessions have the necessary Supervisor API access. The suggestions are configurable and stay enabled by default.

### Room metadata system

Bridge-backed Bluetooth devices can now carry stable room metadata: `room_name`, `room_id`, plus source and confidence indicators. This metadata flows through status snapshots and is available to Music Assistant, Home Assistant, and MassDroid for room mapping.

The room system is deliberately simple at this stage — a device knows which room it is in, and that information is stable across restarts. The confidence field exists because room assignment can come from manual operator input (high confidence), HA area matching (medium), or heuristic bridge-name parsing (low).

### Transfer readiness contracts

Device snapshots now include a compact `transfer_readiness` contract that answers the question: is this speaker actually ready for a fast room handoff? The contract checks Bluetooth connection state, audio sink availability, daemon health, and room metadata completeness.

This is the foundation for future multi-room transfer workflows. Instead of attempting a handoff and discovering halfway through that the target speaker's Bluetooth is disconnected, automations and the UI can check readiness first.

### User-scoped Docker audio

Docker and Raspberry Pi images now keep container init and root setup for Bluetooth and D-Bus, but automatically re-exec the bridge process as `AUDIO_UID` for user-scoped host audio sockets. This removes the most common Raspberry Pi deployment issue: PulseAudio or PipeWire refusing connections because the bridge runs as root while the audio server expects the host user's session.

The change uses a split-privileges model — init runs as root for hardware access, then the bridge process drops to the audio user. Startup diagnostics and the Raspberry Pi pre-flight checker now distinguish init UID from app UID and explain the model clearly.

### ARMv7 runtime fix

ARMv7 release images now install the FFmpeg runtime libraries needed by PyAV/sendspin, and the publish workflow smoke-tests the actual daemon import path. This fixed the `libavformat.so.61` crash that affected older Raspberry Pi hardware on fresh installs.

### Recovery timeline with advanced filters

The diagnostics recovery tooling gained a retained recovery timeline with advanced severity, scope, source, and window filters. Power users can now trace recovery events over time instead of only seeing the current state. This made post-incident review practical without requiring manual log analysis.

### Calmer operator guidance

Operator guidance became calmer across the board. Onboarding stays out of the notice stack on non-empty installs by default. Grouped actions preview affected devices before execution. Dense recovery issue pills collapse into `+N more` instead of flooding the screen.

### Music Assistant live reload

The MA runtime can now reload after URL or token changes without forcing a full bridge restart. Auth refreshes and rediscovery apply in place, which matters for HA environments where MA tokens rotate or the MA server IP changes after a reboot.

### Standby mode foundation

Per-device settings now support an explicit `handoff_mode`, with `fast_handoff` reusing the existing keepalive path to keep selected speakers warmer for transfer-heavy room workflows. Runtime device events are enriched with room and readiness context, and the web UI surfaces room and transfer badges plus manual room assignment controls. This groundwork would become the basis for the full null-sink standby system that shipped later in the week.

## Why this matters

`v2.45.0` and `v2.46.0` moved the bridge from a device-centric model (here are your speakers) to a room-centric model (here is where your speakers are and whether they are ready). The HA area integration made that transition practical for the majority of users who already have rooms defined in Home Assistant.

The Docker audio fix was arguably the most impactful change for new Raspberry Pi operators, eliminating the number one deployment friction point.

## Follow-up

With room metadata and transfer readiness in place, the next priority was hardening the security and IPC layers before the standby and transport features that would build on these contracts.
