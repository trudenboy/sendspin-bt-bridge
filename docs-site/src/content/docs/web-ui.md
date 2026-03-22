---
title: Web UI
description: Current guide to the Sendspin Bluetooth Bridge dashboard, guidance banners, Bluetooth scan modal, Music Assistant reconfigure flow, diagnostics, and bug reports
---

The web interface is available through a direct browser port controlled by **`WEB_PORT`** (default **8080** for standalone installs) and through **HA Ingress** for the Home Assistant addon. In addon mode, `WEB_PORT` can open an extra direct listener, but Ingress keeps using the fixed addon channel port. The page updates in real time over Server-Sent Events, so most status changes appear without a refresh.

## Sign-in flow

![Login page with Home Assistant, Music Assistant, and Password auth tabs](/sendspin-bt-bridge/screenshots/screenshot-login.png)

When authentication is enabled, the bridge shows a dedicated sign-in screen before the dashboard.

| Method | When it appears | Notes |
|---|---|---|
| **Home Assistant** | Always in HA addon mode, or in standalone when the bridge is connected to an HA-backed Music Assistant instance | Uses the HA `login_flow`, including TOTP / MFA |
| **Music Assistant** | Standalone installs already connected to MA with a built-in MA token flow | Validates MA credentials against the connected MA server |
| **Password** | Standalone auth is enabled and a local password hash exists | PBKDF2-SHA256 local password flow |

If Home Assistant requires MFA, the page switches to a **6-digit verification step**. In standalone mode, the **Security** tab controls the session timeout and brute-force policy for these logins. Login forms are CSRF-protected, and standalone sessions use `SameSite=Lax` plus `HttpOnly` cookies.

<Aside type="tip">
After **5 failed attempts** the default lockout is **5 minutes**, but all lockout values are configurable in **Configuration → Security**.
</Aside>

## Dashboard overview

![Dashboard with filters, guidance banners, device rows, and the redesigned configuration section](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

The top of the page combines the live device dashboard with quick filters, guidance, and batch actions:

- **Onboarding checklist** for first-run setup and empty-state recovery.
- **Recovery guidance** when the bridge detects grouped issues that need attention.
- **Device cards or list rows** for every configured speaker.
- **Realtime status** for Bluetooth, sink routing, playback, Music Assistant connectivity, and sync state.
- **Configuration**, **Diagnostics**, and **Logs** as collapsible sections below the live fleet view.

## Header

![Header with version badge, update badge, quick links, runtime chip, and health pills](/sendspin-bt-bridge/screenshots/screenshot-header.png)

The header is split into a main action row and a runtime/status row.

### Main row

- **Bridge title and logo** — always shown on the left.
- **Version badge** — links directly to the matching GitHub release.
- **Update badge** — shows `check`, `up to date`, or the available target version.
- **Report / Docs / GitHub** — quick links for support and documentation.
- **User area** — shows the current user and **Sign out** when authentication is active.

### Runtime row

- **Runtime chip** such as `LXC`, `systemd`, or `demo`.
- **Hostname, IP, and uptime**.
- **Health pills** summarizing Bluetooth devices, Music Assistant state, and active playback.
- **Restart progress banner** during **Save & Restart**.

## Onboarding and recovery guidance

![Onboarding checklist with explicit show-hide control, progress summary, and guided actions](/sendspin-bt-bridge/screenshots/screenshot-onboarding-checklist.png)

The current UI has two distinct guidance surfaces above the device list:

### Setup checklist

- The **Setup checklist** appears during first-run and empty-state onboarding.
- **Show checklist** / **Hide checklist** explicitly expands or collapses it.
- When collapsed, it stays useful instead of disappearing completely: you get a compact summary such as **`2/5 complete - Next: Add a speaker`**.
- **Don’t show again** hides it until you re-enable **Show empty-state onboarding guidance** in **Configuration → General**.

### Recovery guidance

- The **Recovery guidance** banner appears when the bridge detects issues that need operator action.
- Actions can jump straight to the right place, such as **Music Assistant settings**, **Diagnostics**, or the affected device action.
- Use **Configuration → General → Show recovery banners** if you want to hide or restore those notices.

Together, these cards give you both first-run help and day-two operational guidance without sending you hunting through logs first.

![Recovery guidance banner with actionable operator recommendations](/sendspin-bt-bridge/screenshots/screenshot-recovery-guidance.png)

## Filters, batch actions, and view modes

![Toolbar with group filter, adapter filter, status filter, selection count, batch volume, and view toggle](/sendspin-bt-bridge/screenshots/screenshot-group-controls.png)

The toolbar above the fleet view includes:

- **Group filter** — show one Music Assistant sync group or all groups.
- **Adapter filter** — narrow the view to a specific Bluetooth adapter.
- **Status filter** — filter by playing, idle, reconnecting, released, or error states.
- **Selection controls** — select all visible devices, then apply group volume, mute, pause, reconnect, or release.
- **Grid/List toggle** — switch between card view and table view.

### View modes

The current UI defaults to **list view**. You can still switch to **grid view** when you want larger per-device cards, and your manual choice is remembered in browser storage.

In **list view**, one row can be expanded at a time for transport controls, routing details, and device actions. The list auto-expands the most relevant row on load, usually the first active device.

## Device cards and rows

![Device card while playing, with badges, track info, progress, and transport controls](/sendspin-bt-bridge/screenshots/screenshot-device-card-playing.png)

Each device exposes the same core information in both grid and list layouts:

- **Player name** and playback animation.
- **Bluetooth and Music Assistant state**.
- **Track, artist, progress, and album art** when available.
- **Volume and mute controls**.
- **Sync badges** including re-anchor status and configured delay.
- **Battery and adapter badges** when those signals are available.

![Hovered device card with extra routing details and quick actions](/sendspin-bt-bridge/screenshots/screenshot-device-card-hover.png)

Hovering a card or expanding a row reveals more context and quick actions, including:

- **Reconnect** and **Re-pair**.
- **Release / Reclaim** for temporarily handing the speaker back to a phone or PC without deleting it from the bridge.
- **BT Info** modal for copyable diagnostics.
- **Settings gear** that jumps straight into the matching row in **Configuration → Devices**.

A few action pairs look similar but mean different things:

- **Release / Reclaim** is immediate and only toggles Bluetooth management for the live bridge.
- **Disable / Enable** lives in **Configuration → Devices** and controls whether the device should be part of the saved fleet.
- **Re-pair** or paired-device reset tools are for broken host pairing/trust state, not day-to-day handoff.

Group badges are interactive too: clicking a Music Assistant group badge opens the matching group settings page in the MA web UI.

## Configuration panel

![General tab of the redesigned Configuration section, with cards and footer actions](/sendspin-bt-bridge/screenshots/screenshot-config.png)

The redesigned **Configuration** section is organized into five tabs:

| Tab | Purpose |
|---|---|
| **General** | Bridge identity, timezone, latency, direct web port, base listener port, restart behavior, update policy, and guidance visibility |
| **Devices** | Speaker fleet table and per-device saved settings |
| **Bluetooth** | Adapter inventory, paired-device import, scan modal, reconnect policy, and codec preference |
| **Music Assistant** | Connection status, token flows, reconfigure flow, and sync/routing toggles |
| **Security** | Local auth, session timeout, brute-force settings (standalone only) |

The footer actions behave differently on purpose:

- **Save** writes the config without forcing a restart.
- **Save & Restart** applies restart-sensitive changes immediately and shows progress in the header.
- **Cancel** restores the last saved values in the form.
- **Download** exports a share-safe `config.json` with sensitive values removed.
- **Upload** imports a config file while preserving the current password hash, secret key, and stored MA token on the server.

Unsaved edits enable **Cancel**, mark the configuration area as dirty, and trigger a browser warning if you try to leave the page mid-edit.

### Devices tab

![Devices tab with the main device fleet table](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

The **Devices** tab is now the saved fleet table only:

- **Enabled**, **player name**, **MAC**, **adapter**, **port**, **delay**, **live badge**, and **remove** all live here.
- Advanced per-speaker fields include **preferred audio format**, **`listen_host`**, and **`keepalive_interval`**.
- Dashboard device gears scroll here, highlight the matching row, and focus the relevant controls.

If `listen_port` is left blank, the runtime uses **`BASE_LISTEN_PORT + device index`**. Positive `keepalive_interval` values enable silence keepalive, values below 30 seconds are raised to 30, and the old `keepalive_silence` compatibility flag is no longer exposed as a separate web-UI toggle.

### Bluetooth tab

![Bluetooth tab with adapter inventory, reconnect policy, and codec preference](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

The **Bluetooth** tab now owns the bridge-side Bluetooth tools:

- **Adapters** card for rename, refresh, and manual adapter rows.
- **Paired devices** card for inventory, import, and repair/reset actions.
- **Scan nearby** opens a dedicated Bluetooth scan modal from this tab.
- **Connection recovery** settings cover BT check interval and auto-disable threshold.
- **Prefer SBC codec** remains the low-CPU audio policy toggle.

The scan modal improves first-run discovery and troubleshooting:

- choose **All adapters** or a specific adapter;
- keep **Audio devices only** on for normal use, or turn it off when debugging non-audio candidates;
- watch a live **countdown/progress bar** while the scan runs;
- use **Add** or **Add & Pair** directly from the results;
- use **Rescan** after the cooldown without leaving the modal.

The **Already paired devices** list is the faster option when the host already knows the speaker and you just want to import or repair it.

### Music Assistant tab

![Music Assistant tab with connection status, token flows, and bridge integration toggles](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

The **Music Assistant** tab combines connection state with authentication helpers:

- **Connection status** card shows whether the bridge is connected and who authenticated it.
- **Reconfigure** appears on that status card once you are connected, so you can reopen the token tools without clearing the existing config first.
- The **Sign in & token** card stays hidden when the connection is healthy, then reappears when you click **Reconfigure** or when the bridge is not connected yet.
- **Discover** finds or confirms the MA URL.
- **Get token** signs in with MA credentials, saves a long-lived `MA_API_TOKEN`, and does not store the password.
- For HA-backed Music Assistant targets, the UI can offer **Get token automatically**.
- In addon mode, **Auto-get token on UI open** can try silent token creation automatically.

Important token-flow constraints:

- **Auto-get token on UI open** is useful only in the **Home Assistant addon** web UI.
- Silent token creation depends on running under **HA Ingress** with a valid current **Home Assistant browser session/token**.
- If silent auth cannot complete, the UI falls back to the visible HA-assisted or manual token flow instead of leaving you stuck.

The tab also includes the manual token field plus **WebSocket monitor**, **Route volume through MA**, and **Route mute through MA** toggles.

## Empty states and deep links

![Empty state with Scan for devices action and the redesigned collapsed sections underneath](/sendspin-bt-bridge/screenshots/screenshot-empty-no-devices.png)

The empty-state actions now jump to the exact working surface:

- **No Bluetooth devices configured** → **Scan for devices** opens the **Bluetooth** tab and launches the **Scan nearby** modal.
- **No Bluetooth adapter detected** → **Add adapter** opens **Configuration → Bluetooth**, inserts an empty manual adapter row, and focuses the first field.
- **Device gear** → highlights the matching row in **Configuration → Devices**.
- **Adapter gear / adapter shortcut** → highlights the matching row in **Configuration → Bluetooth**.

## Authentication and security UX

Standalone installs expose a dedicated **Security** tab for local access control:

- **Enable web UI authentication** toggle.
- **Session timeout** in hours.
- **Brute-force protection** toggle plus max-attempt, window, and lockout fields.
- **Set password** flow with password confirmation.

When auth is disabled, the page shows a yellow warning banner with a shortcut that jumps straight to **Configuration → Security** and highlights the auth toggle.

In **HA addon mode**, Home Assistant owns access control and auth is always enforced, so the local Security tab is replaced by the addon auth model and direct HA login / TOTP flow. Use **Save & Restart** after changing auth, session, or port settings because those behaviors are applied at startup.

## Diagnostics

![Diagnostics section with health summary cards, routing details, bridge devices, and advanced runtime data](/sendspin-bt-bridge/screenshots/screenshot-diagnostics.png)

**Diagnostics** is a live troubleshooting surface, not just a static info dump. It includes:

- Bridge device counts and sink routing summary.
- Music Assistant state and sync-group summary.
- Adapter inventory and attached sinks.
- Per-device runtime cards with connection and last-known issues.
- Subprocess and advanced runtime information.
- **Download diagnostics**, **Submit bug report**, and **Refresh** actions.

## Logs

![Logs section with filters, runtime log-level controls, and downloadable output](/sendspin-bt-bridge/screenshots/screenshot-logs.png)

The **Logs** section exposes both log viewing and runtime control:

- Filter by **All / Errors / Warnings / Info+ / Debug**.
- Toggle **Auto-refresh**.
- Change the backend log level between **INFO** and **DEBUG** without a restart.
- Download the current log output.

## Updates and bug reports

![Update modal showing current version, target version, release notes, and available actions](/sendspin-bt-bridge/screenshots/screenshot-update-modal.png)

Clicking the update badge opens a modal that summarizes:

- **Current vs target version**.
- Short **release notes** excerpt.
- Runtime-specific actions such as **Update Now**, **Release Notes**, or a manual update hint.

The header **Report** link and the Diagnostics action both open the bug-report flow. The dialog now pre-fills the description with a **diagnostics-generated suggested summary**, lets you edit it before submission, and keeps the full auto-attached diagnostics report available in an expandable preview.

![Bug report dialog with diagnostics-driven suggested description and auto-attached report preview](/sendspin-bt-bridge/screenshots/screenshot-bug-report-dialog.png)

![Bug report dialog with diagnostics-driven suggested description and auto-attached report preview](/sendspin-bt-bridge/screenshots/screenshot-bug-report-dialog.png)
