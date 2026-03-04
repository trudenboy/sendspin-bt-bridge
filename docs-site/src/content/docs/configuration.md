---
title: Configuration
description: Complete configuration reference for Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

Configuration is stored in `config.json` in the `/config` directory (mounted as a Docker volume). Edit via the web interface or directly (requires restart).

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

1. Enable `PREFER_SBC_CODEC: true` — SBC requires minimal decoding
2. In MA: Settings → Providers → Sendspin → Audio Quality → **PCM 44.1 kHz / 16-bit**

Together these eliminate FLAC decoding and reduce load ~30% per player.
</Aside>
