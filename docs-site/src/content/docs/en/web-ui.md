---
title: Web UI
description: Web interface guide for Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

The web interface is available on port **8080** (Docker/LXC) or via **HA Ingress** (addon).

## Dashboard

### Group Controls

At the top of the page:
- **All checkbox** — enable/disable all devices
- **Vol slider** — adjust volume on all active devices simultaneously
- **🔈 Mute All** — mute all devices
- **▮▮ Pause All** — pause all playing players

### Device Card

Each Bluetooth device has its own card:

| Section | Content |
|---|---|
| **Header** | Name, MAC address, WebSocket URL |
| **Bluetooth** | Connection status, since time, adapter and its MAC |
| **Server** | MA server connection status, IP:port |
| **Playback** | ▶ Playing / ▮▮ Paused / No Sink, pause/play button, audio format |
| **Volume** | Volume slider, mute button |
| **Sync** | Sync status, re-anchor count, current delay |
| **Controls** | Reconnect, Re-pair, Release/Reclaim buttons |
| **Track** | Artist — Track title |

### Device Control Buttons

| Button | Action |
|---|---|
| **🔄 Reconnect** | Force Bluetooth reconnect |
| **🔗 Re-pair** | Re-pair (~25 sec, put device in pairing mode first) |
| **🔓 Release** | Release device — bridge stops managing the connection |
| **🔒 Reclaim** | Return device management to the bridge |

<Aside type="tip">
  **Release** is useful when you want to temporarily connect the speaker to another source (phone, PC) without stopping the bridge.
</Aside>

## Configuration Section

The collapsible **⚙️ Configuration** section lets you change settings without editing files:

- **MA Server / Port** — Music Assistant address and port
- **Bridge Name** — player name suffix
- **PulseAudio latency** — latency in ms
- **Prefer SBC codec**
- **Timezone**
- **Bluetooth Adapters** — adapter list with Refresh and Add buttons
- **Bluetooth Devices** — device table with Add Device and Scan buttons

Save buttons:
- **Save Configuration** — save without restarting
- **Save & Restart** — save and restart the service

## Theme

The interface automatically applies the theme:
- Via HA Ingress — reads the Home Assistant theme via `postMessage` API
- Direct access — follows the browser's system theme (`prefers-color-scheme`)
