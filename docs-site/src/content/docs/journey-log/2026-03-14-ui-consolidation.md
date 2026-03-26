---
title: "March 14: UI system consolidation"
description: "Design-system unification, MFA/TOTP hotfix, and empty-state navigation fix"
---

## March 14, 2026 — Empty-state navigation hotfix (v2.31.8)

The `2.31.8` release is a narrow UI follow-up shipped after the larger redesign work from `2.31.6` and the auth hotfix in `2.31.7`. It fixes the two dashboard empty-state actions that were still wired to assumptions from the pre-redesign layout.

When no Bluetooth adapter is present, the empty-state CTA now opens `Configuration → Bluetooth`, lands on the adapters card, and prepares a manual adapter row so the user can act immediately. When adapters exist but no devices are configured yet, the scan CTA now opens `Configuration → Devices → Discovery & import` and launches the Bluetooth scan from the correct redesigned section. In short: the empty dashboard is once again an actionable starting point instead of a dead-end hint.

---

## March 14, 2026 — MFA/TOTP login hotfix (v2.31.7)

The `2.31.7` release is a focused auth hotfix shipped immediately after `2.31.6`. It fixes a regression in the direct Home Assistant login flow: when a user entered their TOTP code on the second MFA step, the bridge rendered that form without a valid CSRF token, so the verification POST was rejected as an invalid session.

This release restores the intended HA login-flow behavior by preserving the CSRF token across the MFA step and adds a regression test that walks the full `username/password -> MFA -> successful sign-in` path. In practice, that means Home Assistant users can again complete sign-in normally when TOTP is enabled.

---

## March 14, 2026 — UI system consolidation (v2.31.6)

The `2.31.6` release completes the first full polish pass after the major `2.31.0` redesign. The work focused less on introducing new primitives and more on making the new UI internally consistent: the Configuration section was rebuilt as a card-based settings surface, dashboard badges were normalized into a shared chip system, and list/card views were brought back into functional and visual parity.

Three themes define this release:

- **Configuration maturity** — `Cancel` now restores the last saved state, security/runtime controls were expanded (session timeout, brute-force protection, MA WebSocket monitor), and the information hierarchy across General / Security / Bluetooth / Devices / Music Assistant was tightened.
- **Device-management ergonomics** — adapter badges link directly into `Configuration → Bluetooth`, custom adapter names are editable, MA sync-group badges deep-link to the correct Music Assistant settings view, and view-mode behavior now defaults to list mode on larger fleets while remembering the user's choice.
- **Badge/runtime cleanup** — delay is visible in both list and card views, list rows expose the same key runtime context as cards, empty placeholder badges were removed, overlapping/misaligned chips were fixed, and list sorting now includes adapters while reusing the same adapter/status chip language as cards.

This release is best understood as the “consistency” release for the redesign: fewer conceptual changes than `2.31.0`, but a much stronger match between mockup, runtime behavior, and the final shipped UI.

---
