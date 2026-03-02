---
title: API Reference
description: REST API for Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

The web interface provides a REST API on port `8080`. All endpoints return JSON.

<Aside type="caution">
  The API requires no authentication. In `host` network mode anyone on the LAN has access. Do not expose port 8080 to the internet.
</Aside>

## Status & Monitoring

### `GET /api/status`

Status of all players.

**Response:**
```json
[
  {
    "player_name": "Living Room Speaker",
    "mac": "AA:BB:CC:DD:EE:FF",
    "websocket_url": "ws://192.168.1.10:8928/sendspin",
    "connected": true,
    "playing": true,
    "bluetooth_connected": true,
    "volume": 48,
    "current_track": "Song Title",
    "current_artist": "Artist Name",
    "audio_format": "48000Hz/24-bit/2ch",
    "management_enabled": true
  }
]
```

### `GET /api/diagnostics`

Structured diagnostics: adapters, sinks, D-Bus, per-device state.

### `GET /api/version`

```json
{ "version": "1.4.1", "build_date": "2026-03-02" }
```

## Playback Control

### `POST /api/pause`

**Body:** `{ "index": 0 }`

### `POST /api/pause_all`

Pause/resume all players.

### `POST /api/volume`

**Body:** `{ "index": 0, "volume": 75 }` (volume: 0–100)

### `POST /api/mute`

**Body:** `{ "index": 0 }`

## Bluetooth Control

### `POST /api/bt/reconnect`

**Body:** `{ "index": 0 }`

### `POST /api/bt/pair`

**Body:** `{ "index": 0 }` — device must be in pairing mode first

### `POST /api/bt/management`

**Body:** `{ "index": 0, "enabled": false }` — Release/Reclaim

### `POST /api/bt/scan`

Scan ~10 s. **Response:** `{ "devices": [{ "mac": "...", "name": "..." }] }`

### `GET /api/bt/adapters`

List of available BT adapters.

## Configuration

### `GET /api/config`

Current configuration from `config.json`.

### `POST /api/config`

Save configuration. Body: JSON config object.

## Examples

```bash
# Get all player status
curl http://localhost:8080/api/status

# Set volume to 50% on first player
curl -X POST http://localhost:8080/api/volume \
  -H 'Content-Type: application/json' \
  -d '{"index": 0, "volume": 50}'

# Pause all
curl -X POST http://localhost:8080/api/pause_all
```
