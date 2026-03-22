# Screenshots to Retake

Use the built-in local demo mode as the **primary screenshot source** for bridge/web UI documentation.

## Primary workflow: canonical demo screenshot stand

From the repository root:

```bash
DEMO_MODE=true python sendspin_client.py
```

Then open `http://127.0.0.1:8080/`.

### What demo mode guarantees for docs work

- six configured demo players on every run
- one real sync group plus multiple solo players
- mixed playing / idle / disconnected states from first render
- seeded Music Assistant metadata, artwork, diagnostics, and logs
- no Bluetooth, PulseAudio, or MA hardware required
- a stable layout that is safe to use as the canonical docs capture stand

### Canonical six-player layout

| Player | Role | Expected state | Notes for screenshots |
| --- | --- | --- | --- |
| Living Room | `Main Floor` sync group | Playing | Primary rich card: artwork, EQ bars, transport, progress |
| Kitchen | `Main Floor` sync group | Playing | Second active group member |
| Studio | `Main Floor` sync group | Idle / connected | Shows grouped-but-not-playing state |
| Office | Solo player | Playing | Rich solo player with MA metadata/artwork |
| Patio | Solo player | Idle / connected / muted | Useful for muted + paused UI states |
| Bedroom | Solo player | Disconnected | Canonical disconnected card |

Use this stand for dashboard, header, guidance banners, config, diagnostics, logs, bug-report dialog, Music Assistant reconfigure, Bluetooth scan modal, and most other docs/web UI screenshots.

Special-case captures that still need non-demo environments:

- HA addon configuration screenshots
- login/auth screenshots (demo mode forces auth off)
- any screenshot that specifically needs real HA Ingress chrome

---

## Audit snapshot — 2026-03-22

Canonical published assets live in `docs-site/public/screenshots/`.

### Published core assets

| Asset | Docs usage | Status | Notes |
| --- | --- | --- | --- |
| `screenshot-dashboard-full.png` | `index.md`, `web-ui.md`, root `README.md` | ✅ current | Canonical demo stand, list view, representative active row expanded. |
| `screenshot-header.png` | `web-ui.md` | ✅ current | Demo stand with live version/update pills. |
| `screenshot-group-controls.png` | `web-ui.md` | ✅ current | Demo stand in list view with current toolbar controls. |
| `screenshot-device-card-playing.png` | `web-ui.md` | ✅ current | Playing demo device card. |
| `screenshot-device-card-hover.png` | `web-ui.md` | ✅ current | Hover/details state with routing metadata and quick actions. |
| `screenshot-config.png` | `configuration.md`, `web-ui.md` | ✅ current | General tab from demo stand. |
| `screenshot-config-devices.png` | `devices.md`, `configuration.md`, `web-ui.md` | ✅ current | Devices tab focused on saved fleet table. |
| `screenshot-config-adapters.png` | `devices.md`, `configuration.md`, `web-ui.md` | ✅ usable | Bluetooth tab asset remains valid, but future retakes should include clearer paired-device context. |
| `screenshot-advanced-settings.png` | `configuration.md`, `web-ui.md` | ✅ usable | Music Assistant tab asset remains valid for current connection/token settings. |
| `screenshot-diagnostics.png` | `web-ui.md` | ✅ current | Demo diagnostics payload aligned with current fixture state. |
| `screenshot-logs.png` | `web-ui.md` | ✅ current | Demo logs with deterministic lines. |
| `screenshot-empty-no-devices.png` | `devices.md`, `web-ui.md` | ✅ usable | Still valid for empty-state CTA behavior. |
| `screenshot-update-modal.png` | `web-ui.md` | ✅ current | Update modal from demo stand. |
| `screenshot-login.png` | `web-ui.md` | ⚠️ retained | Still requires a dedicated auth-enabled capture path; demo mode cannot produce it. |
| `screenshot-ha-addon-config.png` | `configuration.md` | ℹ️ retained | HA-only asset; intentionally untouched in this pass. |
| `screenshot-ha-addon-config-bottom.png` | `configuration.md` | ℹ️ retained | HA-only asset; intentionally untouched in this pass. |
| `screenshot-ha-addon-device-edit.png` | `configuration.md` | ℹ️ retained | HA-only asset; intentionally untouched in this pass. |

### New capture specs for current UI surfaces

These surfaces track the current UI screenshot queue, mixing already captured assets with the remaining demo states we still want to publish.

| Proposed asset | Status | Primary doc target | Capture source | Spec |
| --- | --- | --- | --- | --- |
| `screenshot-onboarding-checklist.png` | ✅ captured | `web-ui.md` | Demo | Expanded setup checklist showing explicit **Show/Hide** control, progress summary, and at least one recommended action. |
| `screenshot-onboarding-checklist-collapsed.png` | ✅ captured | `devices.md` | Demo | Collapsed setup checklist from the demo showing the compact progress summary and explicit **Show checklist** control. |
| `screenshot-recovery-guidance.png` | ✅ captured | `web-ui.md`, `troubleshooting.md` | Demo | Recovery banner with at least one actionable item and one jump action. |
| `screenshot-ma-connection-status.png` | ✅ captured | `configuration.md`, `web-ui.md` | Demo | Music Assistant **Connection status** card with **Reconfigure** visible. |
| `screenshot-bt-scan-modal.png` | ✅ captured | `devices.md`, `troubleshooting.md` | Demo | Bluetooth scan modal showing adapter selector, **Audio devices only**, progress, and import workflow. |
| `screenshot-bug-report-dialog.png` | ✅ captured | `web-ui.md`, `troubleshooting.md` | Demo | Bug report dialog with diagnostics-generated suggested description and preview section visible. |
| `screenshot-device-released.png` | ✅ captured | `devices.md` | Demo | Released device row showing reclaim-ready runtime state. |
| `screenshot-paired-devices-card.png` | ✅ captured | `configuration.md`, `devices.md` | Demo | Bluetooth tab **Paired devices** card with count badge and import/repair affordances visible. |

### Capture notes for the new queue

- Prefer **1400×900** browser captures for page-level surfaces.
- Prefer **800×600** or node-level captures for individual cards/modals.
- Keep the UI in **list view** unless the screenshot specifically needs a card-style grid example.
- Use demo mode for every non-HA screenshot unless the asset explicitly depends on HA Ingress or addon configuration pages.
- Do **not** replace HA-only addon screenshots during general docs refreshes unless the task explicitly calls for it.

---

## Existing asset specs

### `screenshot-dashboard-full.png`

**Used in:** `web-ui.md`, `index.md`, `README.md`

**State:** Full dashboard in list view with all six demo players visible and one active row expanded.

### `screenshot-header.png`

**Used in:** `web-ui.md`

**State:** Header bar only, with version/update pills and runtime health indicators.

### `screenshot-device-card-playing.png`

**Used in:** `web-ui.md`

**State:** Single active player with artwork, transport, progress bar, and volume.

### `screenshot-device-card-hover.png`

**Used in:** `web-ui.md`

**State:** Hover/details state with routing metadata and quick actions, including release/reclaim-capable controls.

### `screenshot-group-controls.png`

**Used in:** `web-ui.md`

**State:** Toolbar area with group filter, adapter filter, selection tools, and list/grid toggle.

### `screenshot-config-devices.png`

**Used in:** `web-ui.md`, `configuration.md`, `devices.md`

**State:** Devices tab focused on the saved fleet table. Scan/import controls no longer need to be visible here.

### `screenshot-config-adapters.png`

**Used in:** `web-ui.md`, `configuration.md`, `devices.md`

**State:** Bluetooth tab showing adapter inventory. Future retakes should try to include enough surrounding context to make it obvious that paired-device tools live in the same tab.
