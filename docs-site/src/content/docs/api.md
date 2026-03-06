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
    "connected": true,
    "server_connected": true,
    "bluetooth_connected": true,
    "bluetooth_since": "2026-03-05T10:00:00",
    "server_since": "2026-03-05T10:00:01",
    "playing": true,
    "volume": 48,
    "muted": false,
    "current_track": "Song Title",
    "current_artist": "Artist Name",
    "audio_format": "flac 48000Hz/24-bit/2ch",
    "connected_server_url": "ws://192.168.1.10:8928/sendspin",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter": "C0:FB:F9:62:D6:9D",
    "bluetooth_adapter_name": "Living room dongle",
    "bluetooth_adapter_hci": "hci0",
    "has_sink": true,
    "sink_name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
    "bt_management_enabled": true,
    "group_id": "abc123",
    "group_name": "Sendspin BT",
    "sync_status": "In sync",
    "sync_delay_ms": -600,
    "static_delay_ms": -600,
    "listen_port": 8928,
    "version": "2.10.6",
    "build_date": "2026-03-05"
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

### `GET /api/groups`

Returns all configured players grouped by MA syncgroup. Players sharing the same `group_id` appear as one entry; solo players each appear as their own single-member group with `group_id: null`.

```json
[
  {
    "group_id": "abc123",
    "group_name": "Sendspin BT",
    "avg_volume": 52,
    "playing": true,
    "members": [
      { "player_name": "Living Room", "volume": 48, "playing": true, "connected": true, "bluetooth_connected": true },
      { "player_name": "Kitchen", "volume": 55, "playing": true, "connected": true, "bluetooth_connected": true }
    ]
  },
  {
    "group_id": null,
    "group_name": null,
    "avg_volume": 70,
    "playing": false,
    "members": [
      { "player_name": "Bedroom", "volume": 70, "playing": false, "connected": true, "bluetooth_connected": false }
    ]
  }
]
```

### `GET /api/version`

```json
{ "version": "2.10.6", "build_date": "2026-03-05" }
```

## Playback Control

### `POST /api/pause_all`

Pause/resume all players.

**Body:** `{ "action": "pause" }` or `{ "action": "play" }`

### `POST /api/group/pause`

Pause or resume a specific MA sync group. For `action="play"`, uses the MA REST API if configured so all group members resume in sync; falls back to Sendspin session command.

**Body:** `{ "group_id": "abc123", "action": "pause" }` — action is `"pause"` or `"play"`

### `POST /api/volume`

Set volume on a device.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "value": 75 }` (value: 0–100)

### `POST /api/mute`

Toggle mute on a device.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "muted": true }`

## Music Assistant Integration

These endpoints require `MA_API_URL` and `MA_API_TOKEN` to be configured.

### `GET /api/ma/groups`

Returns all MA syncgroup players discovered from the MA REST API. Empty list if MA API is not configured or discovery hasn't run yet.

```json
[
  {
    "id": "ma-syncgroup-abc123",
    "name": "Sendspin BT",
    "members": [
      { "id": "...", "name": "Living Room", "state": "playing", "volume": 48, "available": true }
    ]
  }
]
```

### `POST /api/ma/rediscover`

Re-runs MA syncgroup discovery without restarting the bridge. Reads current `MA_API_URL` / `MA_API_TOKEN` from `config.json`.

**Response:**
```json
{ "success": true, "syncgroups": 2, "mapped_players": 3, "groups": [{"id": "...", "name": "Sendspin BT"}] }
```

### `GET /api/ma/nowplaying`

Returns current now-playing metadata from MA. Returns `{"connected": false}` when MA integration is inactive.

```json
{
  "connected": true,
  "state": "playing",
  "track": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "image_url": "http://...",
  "elapsed": 142.5,
  "elapsed_updated_at": "2026-03-05T10:01:30",
  "duration": 279,
  "shuffle": false,
  "repeat": "off",
  "queue_index": 3,
  "queue_total": 12,
  "syncgroup_id": "ma-syncgroup-abc123"
}
```

### `POST /api/ma/queue/cmd`

Send a playback control command to the active MA syncgroup queue.

**Body:**
```json
{ "action": "next", "syncgroup_id": "ma-syncgroup-abc123" }
```

| Field | Description |
|---|---|
| `action` | `"next"`, `"previous"`, `"shuffle"`, `"repeat"`, or `"seek"` |
| `value` | For `shuffle`: `true`/`false`. For `repeat`: `"off"`, `"all"`, `"one"`. For `seek`: seconds (int) |
| `syncgroup_id` | Optional — target a specific syncgroup; uses the first active group if omitted |

### `GET /api/debug/ma`

Dump MA integration state for diagnostics: now-playing cache keys, discovered groups, per-client player IDs, and live queue IDs fetched from the MA WebSocket.

```json
{
  "cache_keys": ["ma-syncgroup-abc123"],
  "groups": [...],
  "clients": [{ "player_name": "Living Room", "player_id": "...", "group_id": "abc123" }],
  "live_queue_ids": ["up_abc123def456"]
}
```

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

### `POST /api/set-password`

Set or change the web UI password. Not available in HA addon mode (use HA user management instead).

**Body:** `{ "password": "mysecretpassword" }` (min 8 characters)

**Response:** `{ "success": true }`

### `POST /api/settings/log_level`

Change log level immediately and persist to `config.json`. Propagates to all running subprocesses via stdin IPC — no restart needed.

**Body:** `{ "level": "debug" }` — `"info"` or `"debug"`

**Response:** `{ "success": true, "level": "DEBUG" }`

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

# Pause all players
curl -X POST http://localhost:8080/api/pause_all \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Pause a specific MA sync group
curl -X POST http://localhost:8080/api/group/pause \
  -H 'Content-Type: application/json' \
  -d '{"group_id": "abc123", "action": "pause"}'

# Skip to next track (requires MA API configured)
curl -X POST http://localhost:8080/api/ma/queue/cmd \
  -H 'Content-Type: application/json' \
  -d '{"action": "next"}'

# Start BT scan and poll for results
JOB=$(curl -s -X POST http://localhost:8080/api/bt/scan | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
curl http://localhost:8080/api/bt/scan/result/$JOB

# Get diagnostics
curl http://localhost:8080/api/diagnostics | python3 -m json.tool

# Change log level at runtime
curl -X POST http://localhost:8080/api/settings/log_level \
  -H 'Content-Type: application/json' \
  -d '{"level": "debug"}'
```
