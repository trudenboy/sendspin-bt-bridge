---
title: Configuration
description: Current configuration reference for the redesigned Sendspin Bluetooth Bridge setup flows
---

Sendspin Bluetooth Bridge stores persistent settings in `config.json` inside the `/config` directory. You can manage those values through the web UI, through the Home Assistant addon options, or by editing the file directly.

## Configuration surfaces

There are two main ways to manage settings:

| Surface | Best for | Notes |
|---|---|---|
| **Web UI** | Day-to-day management of devices, adapters, auth, and runtime behavior | Works for Docker, LXC, and addon installs |
| **HA addon Configuration tab** | Supervisor-managed addon options | Useful when you prefer HA-native YAML-style editing |

## Web UI configuration

![General tab of the redesigned Configuration section](/sendspin-bt-bridge/screenshots/screenshot-config.png)

The redesigned configuration area is organized into five tabs instead of one long form.

| Tab | What lives there |
|---|---|
| **General** | Bridge name, timezone, latency, smooth restart, update policy |
| **Devices** | Speaker fleet table, scanning, paired-device import |
| **Bluetooth** | Adapter naming, reconnect policy, codec preference |
| **Music Assistant** | Token flows, MA endpoint, monitor, volume/mute routing |
| **Security** | Local auth, session timeout, brute-force protection |

### General tab

The **General** tab contains settings that apply to the whole bridge instance:

- **Bridge name** — appended to player names as `Player @ Name`.
- **Timezone** — with a live preview of the current local time.
- **PulseAudio latency (ms)** — higher values trade latency for stability on slower hardware.
- **Smooth restart** — mutes before restart and shows restart progress.
- **Check for updates / Auto-update** — available outside HA addon mode.

### Devices tab

![Devices tab with fleet table and discovery workflow](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

The **Devices** tab is split into two responsibilities:

- **Device fleet** — the main table for daily edits.
- **Discovery & import** — scanning nearby speakers or importing already paired devices.

Each device row can store:

| Field | Purpose |
|---|---|
| **Enabled** | Skip a device on startup without deleting it |
| **Player name** | Display name in Music Assistant |
| **MAC** | Speaker Bluetooth address |
| **Adapter** | Specific adapter binding, if needed |
| **Port** | Optional custom sendspin listener port |
| **Delay** | `static_delay_ms` latency compensation |
| **Live** | Runtime badge such as Playing, Connected, Released, or Not seen |

Advanced row details expose:

- **Preferred format** such as `flac:44100:16:2`.
- **Listen host** override.
- **Keepalive interval** for speakers that go to sleep aggressively.

### Bluetooth tab

![Bluetooth tab with adapter inventory and recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

The **Bluetooth** tab combines inventory and policy:

- Rename adapters for clearer dashboard labels.
- Add manual adapter entries when auto-detect is incomplete.
- Refresh detection without leaving the page.
- Set **BT check interval** for reconnect probing.
- Set **Auto-disable threshold** for unstable devices.
- Toggle **Prefer SBC codec** to reduce CPU load.

### Music Assistant tab

![Music Assistant tab with token actions and bridge integration settings](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

The **Music Assistant** tab includes both connection state and auth helpers:

- **Connection status** summary.
- **Discover** nearby MA servers.
- **Get token** with MA credentials.
- **Get token automatically** in addon mode.
- Manual **MA API token** field.
- **MA server** and **MA WebSocket port** for the sendspin endpoint.
- **WebSocket monitor**, **Route volume through MA**, and **Route mute through MA** toggles.

### Security tab

In standalone deployments, the **Security** tab controls local access to the web UI:

- **Enable web UI authentication**.
- **Session timeout** in hours.
- **Brute-force protection** toggle.
- **Max attempts**, **Window**, and **Lockout** policy fields.
- **Set password** flow for the local password hash.

In Home Assistant addon mode, Home Assistant handles access control instead, so the standalone security controls are hidden.

### Footer actions

The footer is shared across tabs:

- **Save** writes `config.json` now.
- **Save & Restart** saves and restarts immediately.
- **Cancel** restores the last saved values in the form.
- **Download** exports the current config as a timestamped JSON file.
- **Upload** imports a previously exported config and preserves sensitive keys on the server.

## Home Assistant addon options

![HA addon configuration panel with core options](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config.png)

When running as a Home Assistant addon, the Supervisor exposes a native **Configuration** tab in HA.

Go to **Settings → Add-ons → Sendspin Bluetooth Bridge → Configuration**.

Core addon options include:

| Option | Purpose |
|---|---|
| **sendspin_server** | Hostname/IP of the Music Assistant server, or `auto` for mDNS |
| **sendspin_port** | Sendspin WebSocket port, usually `9000` |
| **bridge_name** | Optional instance label appended to players |
| **tz** | IANA timezone |
| **pulse_latency_msec** | Audio buffer latency hint |
| **prefer_sbc_codec** | Lower-CPU codec preference |
| **bt_check_interval** | Reconnect polling interval |
| **bt_max_reconnect_fails** | Auto-disable threshold |
| **auth_enabled** | Enables the bridge auth flow when supported |
| **ma_api_url / ma_api_token** | Music Assistant REST integration |
| **volume_via_ma / mute_via_ma** | Route controls through MA to keep UI state aligned |
| **log_level** | Base logging verbosity |

![HA addon device and adapter lists plus device edit dialog](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config-bottom.png)

![Home Assistant addon device edit dialog](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-device-edit.png)

The addon form also exposes the full **Bluetooth devices** and **Bluetooth adapters** structures.

## `config.json` reference

### Core keys

| Key | Type | Description |
|---|---|---|
| `SENDSPIN_SERVER` | string | Music Assistant host or `auto` |
| `SENDSPIN_PORT` | integer | Sendspin WebSocket port |
| `BRIDGE_NAME` | string | Optional label appended to player names |
| `TZ` | string | IANA timezone |
| `PULSE_LATENCY_MSEC` | integer | Audio buffer latency hint |
| `BT_CHECK_INTERVAL` | integer | Probe interval for Bluetooth recovery |
| `BT_MAX_RECONNECT_FAILS` | integer | Auto-disable threshold |
| `PREFER_SBC_CODEC` | boolean | Lower-CPU codec preference |
| `AUTH_ENABLED` | boolean | Enable local web auth |
| `SESSION_TIMEOUT_HOURS` | integer | Browser session lifetime |
| `BRUTE_FORCE_PROTECTION` | boolean | Enable temporary lockout after failed sign-ins |
| `BRUTE_FORCE_MAX_ATTEMPTS` | integer | Maximum failed attempts within the window |
| `BRUTE_FORCE_WINDOW_MINUTES` | integer | Rolling window for failed attempts |
| `BRUTE_FORCE_LOCKOUT_MINUTES` | integer | Lockout duration |
| `MA_API_URL` | string | Music Assistant REST URL |
| `MA_API_TOKEN` | string | Music Assistant API token |
| `MA_WEBSOCKET_MONITOR` | boolean | Live queue/now-playing monitor |
| `VOLUME_VIA_MA` | boolean | Route volume changes through MA |
| `MUTE_VIA_MA` | boolean | Route mute changes through MA |
| `LOG_LEVEL` | string | Base log verbosity |

### Bluetooth devices

```json
{
  "BLUETOOTH_DEVICES": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "player_name": "Living Room Speaker",
      "adapter": "hci0",
      "static_delay_ms": -500,
      "listen_host": "0.0.0.0",
      "listen_port": 8928,
      "preferred_format": "flac:44100:16:2",
      "keepalive_interval": 60,
      "enabled": true
    }
  ]
}
```

| Field | Description |
|---|---|
| `mac` | Speaker Bluetooth MAC |
| `player_name` | Display name in Music Assistant |
| `adapter` | Adapter ID or MAC |
| `static_delay_ms` | Fixed sync compensation |
| `listen_host` | Advertised host for this device listener |
| `listen_port` | Custom sendspin listener port |
| `preferred_format` | Preferred output format |
| `keepalive_interval` | Silence keepalive interval in seconds |
| `enabled` | Skip on startup when `false` |

### Bluetooth adapters

```json
{
  "BLUETOOTH_ADAPTERS": [
    {
      "id": "hci0",
      "mac": "C0:FB:F9:62:D6:9D",
      "name": "Living room dongle"
    }
  ]
}
```

| Field | Description |
|---|---|
| `id` | Adapter interface name such as `hci0` |
| `mac` | Adapter MAC address |
| `name` | Friendly label shown in the UI |

## Environment variables

Environment variables still work for bootstrap and automation. If `config.json` exists, its values take precedence.

| Variable | Description |
|---|---|
| `SENDSPIN_SERVER` | Music Assistant host |
| `SENDSPIN_PORT` | Sendspin WebSocket port |
| `WEB_PORT` | Web UI port (default `8080`) |
| `TZ` | Timezone |
| `CONFIG_DIR` | Config directory path |
