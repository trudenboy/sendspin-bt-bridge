---
title: API Reference
description: REST API for Sendspin Bluetooth Bridge
---


The web interface provides a REST API on port `8080`. All endpoints return JSON.

<Aside type="caution">
  By default the API requires no authentication. In `host` network mode anyone on the LAN has access. Do not expose port 8080 to the internet.

  If `AUTH_ENABLED=true` is set, all endpoints except `/login`, `/logout`, `/api/status`, and `/static/*` require a valid session cookie obtained by logging in via the web UI.
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
    "audio_format": "flac 48000Hz/24-bit/2ch",
    "management_enabled": true
  }
]
```

### `GET /api/status/stream`

Server-Sent Events stream. The browser connects once; the server pushes `data: {...}` on every device state change. The web UI uses this instead of polling.

```
GET /api/status/stream
Accept: text/event-stream

data: [{"player_name": "Living Room Speaker", "playing": true, ...}]
data: [{"player_name": "Living Room Speaker", "playing": false, ...}]
```

### `GET /api/diagnostics`

Structured diagnostics: adapters, sinks, D-Bus, per-device state.

### `GET /api/version`

```json
{ "version": "2.6.2", "build_date": "2026-03-04" }
```

## Playback Control

### `POST /api/pause`

Pause/resume a specific player.

**Body:** `{ "player_name": "Living Room" }`

### `POST /api/pause_all`

Pause/resume all players.

**Body:** `{ "action": "pause" }` or `{ "action": "play" }`

### `POST /api/volume`

Set volume on a device.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "value": 75 }` (value: 0–100)

### `POST /api/mute`

Toggle mute on a device.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "muted": true }`

## Bluetooth Control

### `POST /api/bt/reconnect`

Force Bluetooth reconnect.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF" }`

### `POST /api/bt/pair`

Start pairing (~25 sec). Device must be in pairing mode first.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0" }`

### `POST /api/bt/management`

Toggle BT management (Release/Reclaim).

**Body:** `{ "player_name": "Living Room", "enabled": false }`

### `POST /api/bt/scan`

Start a background BT scan (~10 s). Returns immediately with a job ID.

**Response:** `{ "job_id": "550e8400-e29b-41d4-a716-446655440000" }`

### `GET /api/bt/scan/result/<job_id>`

Poll for scan results.

**Response while running:**
```json
{ "status": "running" }
```

**Response when complete:**
```json
{
  "status": "done",
  "devices": [
    { "mac": "AA:BB:CC:DD:EE:FF", "name": "JBL Flip 5" }
  ]
}
```

**Response on error:**
```json
{ "status": "done", "error": "Scan failed: bluetoothctl timed out" }
```

### `GET /api/bt/adapters`

List of available BT adapters.

### `GET /api/bt/paired`

List of currently paired devices (name + MAC).

## System

### `GET /api/logs`

Recent log lines from the bridge. Useful for debugging without SSH access.

**Query parameters:**
- `lines` — number of lines to return (default `100`)

### `POST /api/restart`

Restart the bridge process (causes container/service restart).

## Configuration

### `GET /api/config`

Current configuration from `config.json`.

### `POST /api/config`

Save configuration. Body: JSON config object.

## Examples

```bash
# Get all player status
curl http://localhost:8080/api/status

# Subscribe to live status updates (SSE)
curl -N http://localhost:8080/api/status/stream

# Set volume to 50% on a specific device
curl -X POST http://localhost:8080/api/volume \
  -H 'Content-Type: application/json' \
  -d '{"mac": "AA:BB:CC:DD:EE:FF", "value": 50}'

# Pause a specific player
curl -X POST http://localhost:8080/api/pause \
  -H 'Content-Type: application/json' \
  -d '{"player_name": "Living Room"}'

# Pause all
curl -X POST http://localhost:8080/api/pause_all \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Start BT scan and poll for results
JOB=$(curl -s -X POST http://localhost:8080/api/bt/scan | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
curl http://localhost:8080/api/bt/scan/result/$JOB
```
