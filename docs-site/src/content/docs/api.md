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
    "ma_connected": true,
    "group_id": "abc123",
    "group_name": "Sendspin BT",
    "sync_status": "In sync",
    "sync_delay_ms": -600,
    "static_delay_ms": -600,
    "listen_port": 8928,
    "version": "2.28.2",
    "build_date": "2026-03-13"
  }
]
```

### `GET /api/status/stream`

Server-Sent Events stream. The browser connects once; the server pushes `data: {...}` on every device state change. The web UI uses this instead of polling.

Events are batched with a 100 ms debounce window to prevent event storms during rapid state changes (e.g. BT reconnect). The initial response includes a 2 KB padding comment to flush HA Ingress proxy buffers.

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
{ "version": "2.28.2", "build_date": "2026-03-13" }
```

### `GET /api/health`

Health check endpoint. Returns `200 OK` with `{"status": "ok"}`. Useful for Docker health checks and load balancers.

### `GET /api/preflight`

CORS preflight endpoint. Returns `204 No Content` with appropriate CORS headers.

## Playback Control

### `POST /api/pause_all`

Pause/resume all players.

**Body:** `{ "action": "pause" }` or `{ "action": "play" }`

### `POST /api/group/pause`

Pause or resume a specific MA sync group. For `action="play"`, uses the MA REST API if configured so all group members resume in sync; falls back to Sendspin session command.

**Body:** `{ "group_id": "abc123", "action": "pause" }` — action is `"pause"` or `"play"`

### `POST /api/volume`

Set volume on one or more devices. Supports individual, group, and multi-target modes.

**Body parameters:**

| Field | Type | Description |
|---|---|---|
| `volume` | integer | Target volume (0–100). Required. |
| `mac` | string | Target a single device by MAC address |
| `player_name` | string | Target a single device by player name |
| `player_names` | string[] | Target multiple devices by name |
| `group_id` | string | Target all devices in a specific MA sync group |
| `group` | boolean | When `true`, uses MA's proportional `group_volume` for sync group members |
| `force_local` | boolean | When `true`, bypasses MA API and uses direct PulseAudio (`pactl`) |

If no targeting field is provided (`mac`, `player_name`, `player_names`, `group_id`), volume is applied to **all** devices.

**Routing logic** (when `VOLUME_VIA_MA` is enabled and MA is connected):

- **`group: true`** — sends `group_volume` once per unique MA sync group among selected targets. MA applies a proportional delta, preserving relative volumes. Devices **not** in any sync group receive the exact value via direct PulseAudio.
- **`group: false`** (default) — sends `volume_set` to each target individually via MA API.
- The response returns immediately with `"via": "ma"`. The UI updates when bridge_daemon receives the echo from MA (~500 ms).

**Fallback:** if MA is offline, `VOLUME_VIA_MA` is disabled, or `force_local: true`, volume is set directly via PulseAudio and status updates immediately.

```json
// Individual
{ "mac": "AA:BB:CC:DD:EE:FF", "volume": 75 }

// Group (proportional for sync groups, exact for solo devices)
{ "volume": 40, "group": true }

// Force local pactl
{ "mac": "AA:BB:CC:DD:EE:FF", "volume": 50, "force_local": true }
```

### `POST /api/pause`

Pause or resume a single player. Sends the command via IPC to the target daemon subprocess which forwards it over the existing WebSocket connection to MA.

**Body:** `{ "player_name": "Living Room Speaker", "action": "pause" }` — action is `"pause"` or `"play"`

### `POST /api/mute`

Toggle mute on a device. When `MUTE_VIA_MA` is enabled and MA is connected, the mute command is routed through the MA API. Otherwise, mute is applied directly via PulseAudio.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "muted": true }`

## Music Assistant Integration

These endpoints require `MA_API_URL` and `MA_API_TOKEN` to be configured (auto-filled via "Sign in with Home Assistant" in addon mode, or set manually).

### `GET /api/ma/discover`

Discover Music Assistant servers on the network via mDNS. Returns a list of found servers.

**Response:**
```json
{ "success": true, "servers": [{ "url": "http://192.168.1.10:8095", "name": "Music Assistant" }] }
```

### `POST /api/ma/login`

Authenticate with MA using username and password. Supports multiple auth providers (`ma`, `ha`, `ha-via-ma`).

**Body:**
```json
{ "ma_url": "http://192.168.1.10:8095", "username": "user", "password": "pass", "provider": "ma" }
```

| Field | Description |
|---|---|
| `ma_url` | MA server URL (optional if already configured) |
| `username` | MA or HA username |
| `password` | Password |
| `provider` | Auth provider: `"ma"` (MA built-in), `"ha"` (HA via MA OAuth), `"ha-via-ma"` (HA credentials sent to MA) |

**Response:** `{ "success": true, "url": "...", "username": "...", "message": "..." }`

### `GET /api/ma/ha-auth-page`

Returns the HA OAuth authorization URL for browser-based sign-in via Home Assistant.

**Response:** `{ "auth_url": "http://haos:8123/auth/authorize?..." }`

### `POST /api/ma/ha-silent-auth`

Creates an MA API token silently using the HA session token. Available only in addon mode (running as an HA addon with Ingress).

**Body:** `{ "ha_token": "<HA access token>", "ma_url": "http://192.168.1.x:8095" }`

**Flow:** The bridge connects to HA WebSocket with the provided token, calls `auth/current_user` to get user info, then POSTs a JSONRPC request to MA's Ingress endpoint with `X-Remote-User-*` headers to create a long-lived token. The token is saved to `config.json` and the MA API connection is established immediately.

**Response:**
```json
{ "success": true, "url": "http://192.168.1.x:8095", "username": "Renso", "message": "Connected to Music Assistant via Home Assistant." }
```

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

### `POST /api/ma/ha-login`

Authenticate with HA using username and password credentials, then exchange the HA token for an MA API token. Used in Docker/LXC mode when MA runs as an HA addon.

**Body:**
```json
{ "ma_url": "http://192.168.1.10:8095", "username": "ha_user", "password": "ha_pass" }
```

**Response:** `{ "success": true, "url": "...", "username": "...", "message": "Connected to Music Assistant via Home Assistant credentials." }`

Supports 2FA: if the HA login flow requires MFA, the response includes `"step": "mfa"` with a `flow_id` to continue the flow.

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

### `POST /api/device/enabled`

Toggle global device enabled/disabled state. Requires a restart to take effect — disabled devices are fully skipped (no client, no BT manager, no port).

**Body:** `{ "player_name": "Living Room", "enabled": false }`

**Response:**
```json
{
  "success": true,
  "enabled": false,
  "restart_required": true,
  "message": "Device will be disabled after restart"
}
```

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

### `GET /api/bugreport`

Generate a diagnostic bug report bundle. Returns a JSON object with system info, device status, recent logs, and BT/audio diagnostics — formatted for GitHub issue submission.

### `GET /api/diagnostics/download`

Download the full diagnostics as a JSON file attachment.

### `GET /api/logs/download`

Download recent logs as a text file attachment.

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

### `POST /api/update/check`

Check for available updates by querying the GitHub API. Returns version comparison.

**Response:** `{ "update_available": true, "latest_version": "2.28.2", "current_version": "2.28.1" }`

### `GET /api/update/info`

Get cached update information without triggering a new check.

### `POST /api/update/apply`

Apply a pending update. In HA addon mode, triggers addon update via Supervisor API. In Docker/LXC mode, returns instructions.

## Configuration

### `GET /api/config`

Current configuration from `config.json`.

### `POST /api/config`

Save configuration. Body: JSON config object.

### `GET /api/config/download`

Download the current `config.json` as a file attachment. The response includes a `Content-Disposition` header with a timestamped filename (e.g. `config-2026-03-15T10-30-00.json`).

### `POST /api/config/upload`

Upload a `config.json` file. Accepts `multipart/form-data` with a `file` field containing the JSON config.

The uploaded file is validated as valid JSON before saving. Sensitive keys (`AUTH_PASSWORD_HASH`, `SECRET_KEY`, `MA_API_TOKEN`) are preserved from the current config and not overwritten by the upload.

**Response:**
```json
{ "success": true, "message": "Configuration uploaded successfully" }
```

**Error response (invalid JSON):**
```json
{ "success": false, "error": "Invalid JSON in uploaded file" }
```

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
