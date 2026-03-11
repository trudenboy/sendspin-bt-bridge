---
title: Web UI
description: Complete guide to the Sendspin Bluetooth Bridge web interface — dashboard, device cards, configuration, diagnostics and logs
---


The web interface is available on port **8080** (Docker/LXC) or via **HA Ingress** (Home Assistant addon). It updates in real time over Server-Sent Events — no page refresh needed.

## Overview

![Full dashboard showing 6 device cards with group controls and header](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

The dashboard shows all configured Bluetooth devices as cards. Cards are sorted so that active groups appear first — all members of an MA sync group are always shown together. Within a group, playing devices come before idle ones. Ungrouped (solo) speakers follow after all groups.

---

## Header

![Header bar showing version, hostname, IP, uptime and health summary](/sendspin-bt-bridge/screenshots/screenshot-header.png)

The blue header bar shows:

- **Title** — "Sendspin Bluetooth Bridge" with links to Docs and GitHub
- **Version + build date** — top right (e.g. `2.6.8 · 2026-03-04`), fetched live from `/api/version`
- **Hostname · IP · uptime** — the container/host identity and how long the service has been running
- **Health summary** — `4/6 playing · 1 disconnected` with color dots for quick status

---

## Group Controls

![Group controls bar: GROUP dropdown, All 6 players, All checkbox, VOL slider 50%, Mute All, Pause All](/sendspin-bt-bridge/screenshots/screenshot-group-controls.png)

The controls bar appears above the device cards. All operations apply only to currently selected (checked) devices.

| Control | Description |
|---|---|
| **GROUP dropdown** | Filter the card view by MA sync group, or show "All groups" |
| **Player count** | Shows how many devices are visible with the current filter |
| **All checkbox** | Check or uncheck all visible cards at once |
| **VOL slider** | Sets volume on all selected devices simultaneously |
| **🔈 Mute All** | Toggles mute on all selected devices |
| **▮▮ Pause All** | Sends pause once per MA sync group (avoids group desync); sends play to resume |

<Aside type="tip">
  **Pause All** is group-aware: it sends the pause command once per MA sync group, not once per device. This keeps multi-speaker groups in sync.
</Aside>

### How the Group Volume Slider Works

The group **VOL slider** uses a hybrid approach depending on whether a device belongs to a Music Assistant sync group:

**Devices in a MA sync group** (showing a `🔗 GroupName` badge):
- Volume is sent via the MA API using `group_volume`, which applies MA's **proportional (delta) algorithm** — it adjusts each member's volume relative to its current level, preserving the ratio between speakers. For example, if Speaker A is at 60% and Speaker B is at 40%, dragging the group slider down by 10 will set A to ~50% and B to ~33%.
- One `group_volume` command is sent per unique sync group. If selected devices span two groups, both groups are adjusted.
- The UI updates when MA echoes the actual new values back through the sendspin protocol (typically within ~500 ms).

**Devices not in any sync group** (solo players, no badge):
- Volume is set directly via PulseAudio (`pactl`) to the exact slider value. A solo device set to 35% will be exactly 35%.
- The UI updates immediately.

This means after a group slider change, devices in a sync group may show different percentages (proportional), while solo devices show the exact slider value.

---

## Device Cards

Each Bluetooth speaker has its own card. The card is divided into five columns.

### Card Identity (left column)

![Single device card (ENEBY20) while playing with EQ bars](/sendspin-bt-bridge/screenshots/screenshot-device-card-playing.png)

- **Player name** — shown in the primary text color, always visible
- **EQ bars** — four animated bars appear to the right of the name while the device is actively playing; they disappear when stopped
- **Select checkbox** — include/exclude this device from group operations
- **Group badge** — `🔗 GroupName` visible below the name when the device is part of a Music Assistant sync group

**On hover**, additional details appear:

![Device card on hover showing MAC, WebSocket URL, full BT MAC, MA address, sink name, and action buttons](/sendspin-bt-bridge/screenshots/screenshot-device-card-hover.png)

- **MAC address** — the Bluetooth MAC of the speaker
- **WebSocket URL** — the `ws://` address MA uses to connect (e.g. `ws://192.168.10.10:8928/sendspin`)
- **Full BT adapter MAC** — next to the adapter name in the Connection column
- **MA server address** — `host:port` or `auto:9000`
- **Sink name** — the PulseAudio/PipeWire sink in the Volume column

### Connection Column

Two rows showing Bluetooth and Music Assistant connectivity:

**Bluetooth row:**
| Indicator | Meaning |
|---|---|
| 🟢 Connected | Bluetooth A2DP connection established |
| 🟡 Reconnecting (N) | Attempting to reconnect; N = attempt count |
| 🔴 Disconnected | No Bluetooth connection |

The adapter name (`hci0`, `hci1`) is shown next to the dot. Hover to see the full adapter MAC and device MAC.

**Music Assistant row:**
| Indicator | Meaning |
|---|---|
| 🟢 Connected | MA WebSocket session active |
| 🔴 Disconnected / Error | No MA connection |

The MA server `host:port` is shown (or `auto:9000` for mDNS discovery). Hover to see the full resolved WebSocket URL.

When the bridge is actively receiving now-playing data from the MA REST API, a small **`api`** badge appears next to the MA connection indicator. This means full transport controls (prev/next/shuffle/repeat), queue metadata, and album art are available for this device. The badge only appears when `MA_API_URL` and `MA_API_TOKEN` are configured and MA is delivering data for this specific device.

### Playback Column

- **Status** — `▶ Playing` (green dot), `⏸ Stopped` (amber dot), `● No Sink` (red, device not connected)
- **Transport controls** — visible when a sink is active:
  - `◀◀` prev track, `▮▮` / `▶` pause/play, `▶▶` next track — always shown when sink is active
  - `⇄` shuffle, `↻` repeat — shown when MA API is connected; reflect the actual MA queue state and toggle it on click
- **Track / artist** — shown below the status; persists on pause (cleared only when MA sends empty values)
  - **Compilation albums**: if multiple slash-separated artists are present (e.g. `Frank Sinatra/Louis Armstrong/Elvis Presley`), the display shows `Frank Sinatra +2` — hover the element for the full text
  - **Album art tooltip**: hovering over the track name row shows a 120×120 album cover popup (from MA now-playing `image_url`). Only visible when MA API integration is active and delivering cover art.
- **Progress** — current position / total duration (e.g. `3:22 / 4:39`)

### Volume Column

- **Volume slider** (0–100) — dimmed and disabled when no sink is active
- **Volume percentage** — shown next to the slider
- **Mute button** — 🔈 (unmuted) / 🔇 (muted, red background)
- **Audio format** — small text showing the negotiated codec and stream parameters (e.g. `44100Hz/16-bit/2ch`)
- **Sink name** — revealed on hover (e.g. `bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink`); a ⚠ yellow warning is shown if no sink is configured

Individual volume sliders follow the same routing logic as the group slider: when `VOLUME_VIA_MA` is enabled and MA is connected, volume changes are proxied through the MA API. The UI waits for the actual value echoed back from MA before updating. When MA is offline or `VOLUME_VIA_MA` is disabled, changes go directly to PulseAudio.

### Sync Column

- **`✓ In sync`** (green) — Music Assistant has confirmed synchronisation
- **`⚠ Re-anchoring`** (amber) — MA is actively adjusting timing; `Error: N ms` shows the deviation at the last correction
- **`—`** — device is stopped or not connected

**Re-anchors count** — number of sync corrections since the stream started. Turns **orange** at 10+, **red** at 100+. A high count with a fixed `delay:` badge usually means the `static_delay_ms` value needs tuning.

**Delay badge** — `delay: -600ms` shown in the Sync column while the device is playing when `static_delay_ms ≠ 0`. This compensates for A2DP buffer latency to keep the speaker in sync with the group.

**Battery badge** — `🔋 60%` shown next to the device name when the speaker reports battery level via BlueZ Battery1 interface. Requires `Experimental = true` in `/etc/bluetooth/main.conf`.

### Action Buttons (revealed on hover)

| Button | Action |
|---|---|
| **🔄 Reconnect** | Force Bluetooth disconnect + reconnect without re-pairing |
| **🔗 Re-pair** | Full pairing sequence (~25 s); put the device in pairing mode first |
| **🔓 Release** | Disable BT management for this device; bridge stops reconnecting |
| **🔒 Reclaim** | Re-enable BT management (shown instead of Release when device is released) |

<Aside type="tip">
  **Release** is useful when you temporarily want to connect the speaker to a phone or PC. The bridge leaves the device alone until you press **Reclaim**.
</Aside>

---

## Device Sorting

Cards are ordered to keep groups together:

1. **Groups with active members first** — a group where at least one device is playing appears before an all-idle group
2. **Group members stay adjacent** — all devices sharing the same MA sync group are always shown together, regardless of individual play state
3. **Within a group** — playing devices come before idle/disconnected ones
4. **Solo (ungrouped) devices** — appear after all groups, ordered by individual play state

Within groups, the group badge (`🔗 GroupName`) is shown on each member card. Solo players do not show a group badge.

---

## Configuration

The **⚙️ Configuration** section is a collapsible panel at the bottom of the page. Click the header to expand it.

![Configuration section showing adapters table and devices table with Add Device and Scan buttons](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

### Basic Settings

- **Bridge name** — optional suffix added to every player name in MA (displayed as `Player @ Name`). Leave empty to disable.
- **Timezone** — IANA timezone name (e.g. `Europe/Moscow`). A live clock preview updates next to the dropdown.

### Bluetooth Adapters

A table of detected and manually added Bluetooth adapters:

| Column | Description |
|---|---|
| **HCI name** | Kernel interface name (`hci0`, `hci1`) |
| **MAC** | Adapter Bluetooth MAC address |
| **Name** | Editable friendly name |
| **Status dot** | Green = adapter present and active |

Use **↺ Refresh** to re-detect adapters. Use **+ Add** to manually add an adapter by MAC (useful if the adapter is not detected automatically).

### Bluetooth Devices

![Devices table with Add Device, Scan and Already Paired sections](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

A row per configured speaker:

| Column | Description |
|---|---|
| **▶** | Expand/collapse per-device detail row (Listen Address, Port, Format) |
| **Player Name** | Name shown in Music Assistant |
| **MAC Address** | Bluetooth MAC of the speaker |
| **Adapter** | Which BT adapter manages this device (`default`, `hci0`, `hci1`) |
| **Delay (ms)** | `static_delay_ms` — negative value compensates A2DP buffer latency (typical: `-500` to `-700`) |
| **×** | Remove this device |

**+ Add Device** appends a blank row. **🔍 Scan** runs a ~10 s background Bluetooth scan across all adapters and shows discovered devices. Click a result to populate a new row.

The **Already paired** box lists previously paired devices from bluetoothctl. Click **Add** to add one to the table without scanning.

### Bluetooth Settings

Below the devices table, additional Bluetooth options:

| Field | Default | Description |
|---|---|---|
| **BT check interval (s)** | `10` | How often to probe each device's BT connection |
| **Auto-disable threshold** | `0` | Disable device after N consecutive failed reconnects; `0` = never |
| **Prefer SBC codec** | on | Force SBC after each BT connect — reduces CPU on slow hardware (requires PulseAudio 15+) |

### Music Assistant Integration

The **🎵 Music Assistant** section in the Configuration panel:

![Music Assistant integration showing connection status, server settings, and sign-in options](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

| Element | Description |
|---|---|
| **Status indicator** | ⚪ Not connected / ✅ Connected — http://ip:port — shows current MA API connection state. Click **Reconfigure** to change. |
| **MA server** | IP/hostname, or `auto` for mDNS discovery |
| **MA WebSocket port** | Sendspin WebSocket port (default `9000`) |
| **PulseAudio latency (ms)** | Higher values reduce dropouts on slow hardware (default `200`). Located in General section. |
| **🏠 Sign in with Home Assistant** | One-click button (addon mode only) — creates a long-lived MA API token via HA Ingress. No credentials needed. |
| **Advanced: manual JWT token** | Expandable `<details>` for Docker/LXC users — paste MA API URL and token manually. Create token in MA → Settings → Profile → Long-lived access tokens. |
| **Route volume through MA** | When checked, volume changes go through MA API — keeps MA UI sliders in sync |

### Save Actions

- **Save** — writes `config.json`; takes effect after restart
- **Save & Restart** — saves and immediately restarts the bridge service

<Aside type="caution">
  Configuration changes require a restart. In Home Assistant addon mode, the addon restarts automatically when you press **Save & Restart**. In Docker/LXC mode, the Python process is restarted.
</Aside>

---

## Diagnostics

![Diagnostics section showing system health table with green/red status indicators](/sendspin-bt-bridge/screenshots/screenshot-diagnostics.png)

The collapsible **Diagnostics** section shows a system health table:

| Component | What it checks |
|---|---|
| Bluetooth daemon | `bluetoothctl` / `bluetoothd` running |
| D-Bus socket | D-Bus socket reachable |
| Audio server | `pulseaudio` or `pipewire` running |
| Adapter hci0 / hci1 | Adapter present, MAC shown |
| BT audio sinks | List of active `bluez_sink.*` or `bluez_output.*` PulseAudio/PipeWire sinks |
| Per-device rows | Connection status + assigned sink name; red dot if disconnected |
| **Enabled/Disabled** | Shows "Disabled" label in orange next to disconnected devices that have been auto-disabled |
| **MA API** | Music Assistant API connection status and URL |

Click **↻ Refresh** to re-run the checks.

---

## Logs

![Logs section showing monospace log output with color-coded lines and filter buttons](/sendspin-bt-bridge/screenshots/screenshot-logs.png)

The collapsible **📋 Logs** section shows the last 150 lines of the application log.

| Control | Description |
|---|---|
| **Refresh Logs** | Fetch latest log output |
| **Auto-Refresh: Off/On** | Toggle 2-second automatic refresh |
| **All / Error / Warning / Info** | Filter displayed lines by severity |

Log lines are color-coded:
- **Red** — ERROR
- **Amber** — WARNING
- **White** — INFO
- **Gray** — DEBUG

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `R` | Refresh logs |
| `P` | Pause all |
| `S` | Save configuration |

---

## Theming

- **Via HA Ingress** — inherits the active Home Assistant theme (light or dark) automatically via `postMessage` API
- **Direct access** — follows the browser's `prefers-color-scheme` setting
