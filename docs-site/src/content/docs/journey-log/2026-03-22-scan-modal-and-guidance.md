---
title: 2026-03-22 — Scan modal redesign and guidance refinements
description: March 21–22 delivered the Bluetooth scan modal redesign with adapter selection and audio filtering, onboarding all-released detection, bug report auto-description, and guidance status/disclosure separation across v2.42.2 and v2.42.3
---

The two days after the orchestrator release focused on the surfaces operators interact with most during setup: Bluetooth discovery, onboarding guidance, and bug reporting. Ten RC releases across two stable tags refined these workflows until they felt coherent instead of bolted on.

## What shipped

### Bluetooth scan modal with adapter selection

The scan modal was rebuilt from the ground up. Operators now choose which Bluetooth adapter to scan with, toggle an explicit audio-only filter, and trigger rescans without closing the dialog. On multi-adapter systems — the common HAOS setup with separate hci0/hci1 controllers — this eliminated the guesswork of which adapter was discovering which device.

Scan results stay aligned with the selected discovery scope, and non-audio Bluetooth candidates are surfaced honestly when the audio-only filter is disabled. The modal copy now explains the real operator workflow instead of showing raw `bluetoothctl` output.

### Onboarding recognizes all-released installs

A subtle but important edge case: when every configured speaker has been manually released (handed off to another bridge or freed for other use), the onboarding system now detects this state and offers direct reclaim actions. Operators can resume playback without hunting through configuration screens to figure out why nothing is playing.

### Bug report auto-description from diagnostics

The bug report dialog now pre-fills an editable description generated from attached diagnostics. It summarizes recent errors, Bluetooth and device health, daemon status, and Music Assistant connectivity. This means issue reports start with useful context instead of a blank text box, and operators can edit or extend the generated summary before submitting.

### Guidance status and disclosure separation

The onboarding guidance system received a significant UX rethink. The header now shows a passive setup-status badge — always visible, never intrusive. Checklist visibility is controlled separately through an explicit `Show checklist` / `Hide checklist` toggle. When collapsed, a summary state appears in the notice stack instead of the checklist disappearing entirely.

This separation matters because the previous design conflated "setup is incomplete" (a status) with "show me what to do" (a user action). Operators who knew their setup was incomplete but didn't want the checklist open were forced to dismiss it repeatedly.

### Compact UI consistency pass

The compact design system introduced in `2.42.1` was extended to cover the login screen, Bluetooth discovery surfaces, paired-device management, and scan result badges. Spacing, typography, focus rings, and action menus now follow the shared design language across the entire interface.

### Music Assistant configuration re-entry

The MA configuration flow became easier to re-enter after initial setup. The connection-status card now owns the `Reconfigure` action, and the sign-in/token section stays hidden until reconfiguration is explicitly requested. This prevented accidental token invalidation from operators who opened the MA settings just to check status.

### Home Assistant auth fixes

Two auth-path fixes landed: HA login failures against Music Assistant now return the actual MA-side bootstrap reason when HA OAuth is unavailable, and standalone HA login against MA add-ons completes again after TOTP by falling back to direct HA login flow and resolving MA ingress through Supervisor APIs.

## Why this matters

`v2.42.2` and `v2.42.3` were refinement releases, but they addressed the friction points that new operators hit most often: scanning for speakers, understanding setup state, and reporting problems. The scan modal redesign alone eliminated the most common support question on multi-adapter HAOS installs.

## Follow-up

With discovery and onboarding stabilized, the next wave moved into room metadata, HA area registry integration, and the transfer readiness contracts that would make multi-room handoffs practical.
