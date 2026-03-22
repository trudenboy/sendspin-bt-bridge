---
title: Devices & Adapters
description: Adding speakers, importing paired devices, and understanding release, reclaim, disable, and repair workflows in the current UI
---

## First-run guidance and empty states

![Empty dashboard state with Scan for devices action](/sendspin-bt-bridge/screenshots/screenshot-empty-no-devices.png)

The bridge now guides first-run setup from the top of the dashboard before you ever open logs.

### Setup checklist

When the bridge is missing key setup steps, the page can show a **Setup checklist** card with explicit **Show checklist** / **Hide checklist** controls.

- Expanded mode shows the next steps and one-click actions.
- Collapsed mode keeps a compact summary such as **`2/5 complete - Next: Scan nearby devices`**.
- **Don’t show again** hides it until you re-enable **Show empty-state onboarding guidance** in **Configuration → General**.

![Collapsed setup checklist with compact progress summary and Show checklist action](/sendspin-bt-bridge/screenshots/screenshot-onboarding-checklist-collapsed.png)

### Empty-state shortcuts

When the checklist is not taking over the page, the empty state gives you the fastest next action:

- If no adapter is detected, **Add adapter** opens **Configuration → Bluetooth**, inserts a manual adapter row, and focuses the first field.
- If adapters exist but no speakers are configured, **Scan for devices** opens **Configuration → Bluetooth** and launches the **Scan nearby** modal automatically.

## Adding a speaker

The recommended flow is now split between the **Bluetooth** and **Devices** tabs:

1. Open **Configuration → Bluetooth**.
2. Click **Scan nearby**.
3. Choose **All adapters** or a specific adapter.
4. Leave **Audio devices only** enabled unless you are debugging non-audio candidates.
5. Use **Add** or **Add & Pair** on a discovered speaker.
6. Open **Configuration → Devices** to fine-tune player name, adapter binding, ports, delay, and advanced fields.
7. Save the config, then restart if required for your change.

### Scan modal behavior

The Bluetooth scan flow is now a dedicated modal instead of an inline card.

- The scan runs in the background and shows a live **countdown/progress bar**.
- **Rescan** is available from inside the modal after the cooldown expires.
- **Add & Pair** performs pairing/trust/connect before importing the device.
- You can narrow the scan to one adapter or broaden it to **All adapters**.
- Turning off **Audio devices only** is useful when you need to inspect non-speaker Bluetooth candidates.

![Bluetooth scan modal with adapter selection, audio-only filter, progress, and import actions](/sendspin-bt-bridge/screenshots/screenshot-bt-scan-modal.png)

### Already paired devices

The **Already paired devices** list in **Configuration → Bluetooth** lets you import speakers the host already knows about without scanning again.

![Paired devices card with import and repair actions](/sendspin-bt-bridge/screenshots/screenshot-paired-devices-card.png)

Use it when:

- the speaker is already paired from a previous setup;
- a scan is not finding it but the host-level pairing still exists;
- you want repair/reset tools without touching the saved device fleet first.

## Device fleet table

![Devices tab with the speaker fleet table](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

The **Device fleet** table is the canonical place for saved speaker configuration.

| Column | What it controls |
|---|---|
| **Enabled** | Whether the device should remain part of the saved bridge fleet |
| **Player name** | Friendly MA-visible name |
| **MAC** | Bluetooth address |
| **Adapter** | Specific controller binding |
| **Port** | Custom sendspin listener port |
| **Delay** | `static_delay_ms` sync offset |
| **Live** | Runtime state badge from the currently running bridge |
| **Actions** | Remove the row or act on the saved configuration |

Expanding a row reveals advanced fields such as **preferred format**, **listen host**, and **keepalive interval**.

## Per-device ports, hosts, and keepalive

The current device flow uses these network/runtime fields:

| Field | Current behavior |
|---|---|
| `listen_port` | If set, the device always uses that explicit port |
| `listen_host` | Overrides the advertised host/address for that device listener |
| `keepalive_interval` | Any positive value enables silence keepalive; values below 30 seconds are raised to 30 |
| `keepalive_silence` | Legacy compatibility field from older addon configs; the current web UI no longer exposes it as a separate toggle |

If `listen_port` is empty, the runtime falls back to **`BASE_LISTEN_PORT + device index`**. Every effective listener port must be unique. For multi-bridge setups on one host, either assign different `BASE_LISTEN_PORT` ranges per bridge or set explicit `listen_port` values for every speaker.

`listen_host` is mainly useful when Music Assistant must reach the bridge through a different address than the one the bridge would auto-detect.

## Adapter management

![Bluetooth tab with adapter naming and recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

The **Bluetooth** tab is where adapter-level management lives:

- Friendly adapter names for clearer dashboard badges.
- Manual adapter entries for unusual environments.
- Refreshing detection without leaving the page.
- Paired-device inventory, scan tools, and repair actions.
- Connection recovery policy and codec preference.

### Binding a speaker to an adapter

In the device row, set **Adapter** to either:

- `hci0`, `hci1`, etc. for interface names.
- The adapter MAC address when names are unstable or ambiguous.

Using the adapter MAC is especially helpful in some LXC environments where `hciN` naming can change after reboot.

## Dashboard deep links

The dashboard links back into configuration instead of sending you to a generic section:

- **Device gear** → highlights the matching row in **Configuration → Devices**.
- **Adapter gear / adapter shortcut** → highlights the matching row in **Configuration → Bluetooth**.
- **Group badge** → opens the corresponding Music Assistant group settings page in a new tab.

## Grid and list views

The same fleet can be viewed in two layouts:

- **List view** is the current default and works best for larger or more active fleets.
- **Grid view** is still available when you want card-style browsing.

Your manual choice is remembered in the browser and reused on the next visit.

## Release, reclaim, disable, and repair

![Released device row with reclaim-ready runtime state](/sendspin-bt-bridge/screenshots/screenshot-device-released.png)

The current UI exposes several device-management actions that solve different problems.

| Action | Where | Use it when |
|---|---|---|
| **Reconnect** | Dashboard action | You want to force a Bluetooth reconnect without changing config |
| **Re-pair** | Dashboard action | The host pairing/trust state is stale or broken |
| **Release** | Dashboard action | You want to temporarily give the speaker back to another source |
| **Reclaim** | Dashboard action | You want the bridge to take Bluetooth management back |
| **Disable** | Configuration → Devices | The speaker should stay in the saved fleet but not be used by the bridge until you re-enable it |
| **Remove** | Configuration → Devices | You no longer want this saved device row at all |
| **Paired-device reset/remove** | Configuration → Bluetooth | You need to clear broken host-level pairing or cleanup stale Bluetooth records |

The important distinction:

- **Release / Reclaim** is an immediate live-runtime handoff. The device stays configured, but the bridge stops trying to own the Bluetooth connection until you reclaim it.
- **Disable / Enable** is a saved fleet decision. Use it when the bridge should stop treating that device as part of the active configuration.
- **Re-pair** or paired-device reset/remove is for broken host pairing, trust, or stale Bluetooth records.

## Reconnect policy and auto-disable

The **Bluetooth** tab controls two behaviors that affect device availability:

- **BT check interval** decides how often the bridge probes and retries Bluetooth recovery.
- **Auto-disable threshold** can persist a device as disabled after repeated failed reconnects.

If a speaker keeps flapping, the bridge may protect the rest of the group by auto-disabling it. Re-enable the device in **Configuration → Devices** after fixing the underlying Bluetooth problem.

## Delay tuning and keepalive

Use these per-device fields when tuning difficult speakers:

- **`static_delay_ms`** — compensates for Bluetooth latency differences in grouped playback.
- **`keepalive_interval`** — periodically sends silence so some speakers do not fall asleep between tracks.
- **`keepalive_silence`** — legacy boolean from older addon configs; keepalive is now effectively controlled by `keepalive_interval > 0`.
- **`preferred_format`** — can reduce resampling or CPU load depending on your MA output settings.
