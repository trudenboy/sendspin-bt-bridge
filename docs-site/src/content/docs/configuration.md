---
title: Configuration
description: Complete configuration reference for Sendspin Bluetooth Bridge
---


Configuration is stored in `config.json` in the `/config` directory (mounted as a Docker volume). Edit via the web interface or directly (requires restart).

## Home Assistant Addon

When running as a Home Assistant addon, all settings are available through the built-in **Configuration** tab in the HA UI — no file editing required.

Go to **Settings → Add-ons → Sendspin Bluetooth Bridge → Configuration**.

![HA addon configuration panel — Options section with Music Assistant server, port, timezone and other fields](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config.png)

### Options

| Field | Description |
|---|---|
| **Music Assistant server** | Hostname or IP of the MA server. Use `auto` for automatic mDNS discovery (recommended). |
| **Sendspin port** | WebSocket port of the MA Sendspin provider. Default `9000` matches the MA default. |
| **bridge_name** | Optional label appended to every player name in MA (displayed as `Player @ Name`). Leave empty to disable. |
| **bridge_name_suffix** | Toggle — when enabled, appends `@ {bridge_name}` to each individual player name. |
| **Timezone** | IANA timezone (e.g. `Europe/Moscow`). Leave empty to inherit the HA system timezone. |
| **PulseAudio latency (ms)** | Audio buffer latency hint. Increase to `400–600` if you hear dropouts on slow hardware. Default `200`. |
| **Prefer SBC codec** | Toggle — forces SBC codec after each BT connect. Reduces CPU load; useful with multiple speakers on slow hardware. |
| **bt_check_interval** | Reconnect polling interval in seconds (used when D-Bus events are unavailable). Default `10`. |
| **bt_max_reconnect_fails** | Stop retrying reconnects after N consecutive failures. `0` = retry forever. |
| **auth_enabled** | Toggle — enables password protection for the web UI. |
| **ma_api_url** | Music Assistant REST API base URL (e.g. `http://192.168.1.10:8123`). Required for now-playing metadata, transport controls, and group play. |
| **ma_api_token** | Home Assistant long-lived access token for the MA API. Generate in HA: **Profile → Long-lived access tokens**. |
| **volume_via_ma** | Toggle — route volume/mute through MA API (keeps MA UI in sync). Disable to use direct PulseAudio only. Default: on. |
| **log_level** | Log verbosity (`info` or `debug`). Can also be changed at runtime via the web UI without a restart. |

### Bluetooth Devices and Adapters

![HA addon config — Bluetooth devices list and adapters list with edit and delete buttons](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config-bottom.png)

The **Bluetooth devices** section lists all configured speakers. Click **Edit** (✏) to open the device settings dialog, or **Add** to add a new device.

![Device edit dialog showing mac, player_name, adapter, static_delay_ms, listen_host, listen_port, enabled and preferred_format fields](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-device-edit.png)

| Field | Description |
|---|---|
| **mac** | Bluetooth MAC address of the speaker (`AA:BB:CC:DD:EE:FF`) |
| **player_name** | Display name shown in Music Assistant |
| **adapter** | Bluetooth adapter to use (`hci0`, `hci1`, or adapter MAC). Leave empty for the default adapter. |
| **static_delay_ms** | A2DP latency compensation in ms. Negative value delays this player to stay in sync with the group (typical: `-500` to `-700`). |
| **listen_host** | IP address this player's WebSocket server advertises to MA. Leave empty for auto-detection. |
| **listen_port** | WebSocket port for this player (default starts at `8928`, increments per device). |
| **enabled** | Toggle — disable to skip this device on startup without removing it. |
| **preferred_format** | Preferred audio format, e.g. `flac:44100:16:2`. Prevents PulseAudio resampling when MA sends FLAC. |

The **Bluetooth adapters** section allows labelling each adapter (`hci0`, `hci1`) with a friendly name shown in the web UI.

<Aside type="caution">
After editing options, click **Save** at the bottom of the Options section. The addon restarts automatically to apply the changes.
</Aside>

---

## Core Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `SENDSPIN_SERVER` | string | `"auto"` | MA server address. `auto` = mDNS discovery |
| `SENDSPIN_PORT` | integer | `9000` | MA WebSocket port (`ws://server:port/sendspin`) |
| `BRIDGE_NAME` | string | `""` | Suffix appended to every player name in MA (empty = off) |
| `BRIDGE_NAME_SUFFIX` | boolean | `false` | Append `@ {BRIDGE_NAME}` to each individual player name in MA |
| `TZ` | string | `""` | Container timezone (empty = inherits `TZ` env var, fallback UTC) |
| `PULSE_LATENCY_MSEC` | integer | `200` | PulseAudio latency in ms. Increase if audio stutters on slow hardware |
| `PREFER_SBC_CODEC` | boolean | `false` | Force SBC codec after each BT connect. Reduces CPU load |
| `BT_CHECK_INTERVAL` | integer | `10` | BT polling interval in seconds when D-Bus is unavailable |
| `BT_MAX_RECONNECT_FAILS` | integer | `0` | Auto-disable BT management after N consecutive failures. `0` = retry forever |
| `AUTH_ENABLED` | boolean | `false` | Enable password protection for the web UI |
| `MA_API_URL` | string | `""` | Music Assistant REST API base URL (e.g. `http://192.168.1.10:8123`). Required for now-playing metadata, transport controls, and group play via MA |
| `MA_API_TOKEN` | string | `""` | Home Assistant long-lived access token for the MA API. Generate in HA: **Profile → Long-lived access tokens** |
| `VOLUME_VIA_MA` | boolean | `true` | Route volume/mute changes through the MA API when MA is connected. Keeps the MA UI in sync with the bridge. Set to `false` to always use direct PulseAudio (`pactl`) |
| `LOG_LEVEL` | string | `"INFO"` | Log verbosity (`INFO` or `DEBUG`). Also changeable at runtime via `POST /api/settings/log_level` without a restart |
| `BT_CHURN_THRESHOLD` | integer | `0` | Number of reconnections within the churn window that trigger auto-disable of BT management. `0` = disabled |
| `BT_CHURN_WINDOW` | integer | `300` | Sliding window in seconds for churn counting |

## Bluetooth Devices

The device list is set in `BLUETOOTH_DEVICES`. Each device becomes a separate player in MA.

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
      "enabled": true
    }
  ]
}
```

### Device Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `mac` | string | ✓ | Bluetooth speaker MAC address (`AA:BB:CC:DD:EE:FF`) |
| `player_name` | string | ✓ | Display name in MA |
| `adapter` | string | — | Adapter ID (`hci0`, `hci1`) or adapter MAC. Empty = default adapter |
| `static_delay_ms` | integer | — | A2DP latency compensation in ms. Negative = delay this player |
| `preferred_format` | string | — | Preferred audio format, e.g. `flac:44100:16:2`. Prevents PulseAudio resampling |
| `listen_host` | string | — | IP for sendspin WebSocket listener (default `0.0.0.0`) |
| `listen_port` | integer | — | sendspin WebSocket port (default `9000 + index`) |
| `enabled` | boolean | — | `false` = device is disabled and skipped on startup |
| `keepalive_silence` | boolean | — | Send periodic silence packets to keep the A2DP connection alive. Legacy flag; use `keepalive_interval` instead |
| `keepalive_interval` | integer | — | Seconds between keepalive silence bursts (minimum 30). Set to any value ≥ 30 to enable; `0` or absent = disabled |

## Bluetooth Adapters

Optional list of adapters for explicit labelling. The `name` field is shown in the BT column of the web UI.

```json
{
  "BLUETOOTH_ADAPTERS": [
    { "id": "hci0", "mac": "C0:FB:F9:62:D6:9D", "name": "Living room dongle" }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Kernel interface name (`hciN`) |
| `mac` | string | Adapter MAC address |
| `name` | string | Human-readable label shown in the web UI |

## Environment Variables

Environment variables override `config.json` values:

| Variable | Description |
|---|---|
| `SENDSPIN_SERVER` | MA server address |
| `SENDSPIN_PORT` | WebSocket port |
| `BLUETOOTH_MAC` | Device MAC (single-device config) |
| `WEB_PORT` | Web UI port (default `8080`) |
| `TZ` | Timezone |
| `CONFIG_DIR` | Config directory path (default `/config`) |

## Performance Optimization

<Aside type="tip">
**Reduce CPU load on slow hardware (Raspberry Pi):**

Enable `PREFER_SBC_CODEC: true` — SBC requires minimal decoding and reduces load ~30% per player.

Alternatively, set `preferred_format` to `pcm:44100:16:2` for a device. PCM is raw uncompressed audio — no FLAC decoding at all, at the cost of slightly higher network bandwidth. Only two codecs are supported in `preferred_format`: `flac` and `pcm`.
</Aside>
