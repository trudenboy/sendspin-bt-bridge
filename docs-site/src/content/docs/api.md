---
title: API Reference
description: REST API for Sendspin Bluetooth Bridge
---


The web interface exposes a REST/HTML API on the resolved web port. In Home Assistant add-on mode the effective port is track-aware (`8080` stable, `8081` rc, `8082` beta), and most operators reach it through HA Ingress rather than directly.

<Aside type="caution">
  By default the bridge exposes its API without authentication. In `host` network mode anyone on the LAN can reach it unless you enable UI auth or rely on HA ingress/session controls.
</Aside>

## Status & Monitoring

### `GET /api/status`

Bridge snapshot used by the dashboard and SSE stream. For backward compatibility the top-level object still mirrors one device; multi-device deployments also include a `devices` array plus bridge-wide fields such as `groups`, `disabled_devices`, `startup_progress`, `runtime_mode`, and `operator_guidance`.

```json
{
  "player_name": "Living Room Speaker",
  "connected": true,
  "bluetooth_connected": true,
  "has_sink": true,
  "volume": 48,
  "devices": [
    { "player_name": "Living Room Speaker", "connected": true },
    { "player_name": "Kitchen Speaker", "connected": false }
  ],
  "groups": [],
  "disabled_devices": [],
  "ma_connected": true,
  "startup_progress": {
    "status": "complete",
    "phase": "ready",
    "current_step": 6,
    "total_steps": 6,
    "percent": 100,
    "message": "Startup complete"
  },
  "runtime_mode": "production",
  "operator_guidance": {
    "mode": "healthy",
    "header_status": {
      "tone": "success",
      "label": "1/1 active devices ready",
      "summary": "All active devices have sinks and are ready for playback."
    },
    "issue_groups": []
  }
}
```

### `GET /api/status/stream`

Server-Sent Events stream that emits the same bridge snapshot shape as `/api/status`.

Runtime contract:

- updates are batched with a **100 ms debounce window** to avoid event storms;
- the first response starts with a **2 KiB SSE comment padding block** so HA ingress/proxies flush immediately;
- a heartbeat comment is sent every **15 seconds**;
- sessions are capped at **30 minutes** and the server limits the stream to **4 concurrent listeners**.

### `GET /api/startup-progress`

Current startup/shutdown lifecycle snapshot.

```json
{
  "status": "running",
  "phase": "web",
  "current_step": 4,
  "total_steps": 6,
  "percent": 67,
  "message": "Web interface and event loop ready",
  "details": { "web_thread": "WebServer" },
  "started_at": "2026-03-22T19:00:00+00:00",
  "updated_at": "2026-03-22T19:00:02+00:00",
  "completed_at": null
}
```

### `GET /api/runtime-info`

Explains whether the bridge is running in production or demo/mock mode.

**Key fields:** `mode`, `is_mocked`, `simulator_active`, `fixture_devices`, `fixture_groups`, `disclaimer`, `mocked_layers`, `details`, `updated_at`.

### `GET /api/bridge/telemetry`

Bridge-level telemetry snapshot assembled from runtime state.

```json
{
  "bridge": {
    "uptime_seconds": 1234,
    "process_rss_mb": 84.1,
    "python": "3.13.2",
    "platform": "Linux-...",
    "arch": "x86_64",
    "kernel": "6.8.0",
    "audio_server": "PulseAudio 17.0",
    "bluez": "bluetoothctl: 5.79"
  },
  "startup_progress": { "status": "complete" },
  "runtime_info": { "mode": "production" },
  "subprocesses": [],
  "event_hooks": { "delivery_mode": "runtime", "summary": { "registered_hooks": 0 } }
}
```

### `GET /api/hooks`

Returns the runtime webhook registry snapshot:

- `delivery_mode: "runtime"`
- `summary` with registered/success/failure counts
- `hooks[]` with current registrations
- `recent_deliveries[]` with the latest delivery attempts

### `POST /api/hooks`

Register a runtime-scoped webhook for bridge/device events.

**Body parameters:**

| Field | Type | Description |
|---|---|---|
| `url` | string | Required absolute `http://` or `https://` URL |
| `categories` | string[] | Optional event-category filter |
| `event_types` | string[] | Optional event-type filter |
| `timeout_sec` | number | Optional request timeout, default `5.0` |

**Notes:** registrations are in-memory only; loopback, `.local`, and private-network targets are rejected.

**Response:** `201 Created`

```json
{
  "success": true,
  "hook": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "url": "https://example.net/sendspin-events",
    "categories": ["bridge"],
    "event_types": ["bridge.startup.completed"],
    "timeout_sec": 5.0
  }
}
```

### `DELETE /api/hooks/<hook_id>`

Unregister a runtime hook. Returns `{ "success": true }` or `404` when the hook does not exist.

### `GET /api/diagnostics`

Comprehensive masked diagnostics snapshot. In addition to environment/adapters/sinks/device state, the response includes:

- `contract_versions` (`config_schema_version`, `ipc_protocol_version`)
- `startup_progress` and `runtime_info`
- `ma_integration`, `sink_inputs`, `subprocesses`, `event_hooks`
- `onboarding_assistant`, `recovery_assistant`, `operator_guidance`
- `telemetry` (same shape as `/api/bridge/telemetry`)

### `GET /api/bugreport`

Builds a GitHub-issue-friendly bug-report bundle from masked diagnostics.

**Response fields:**

| Field | Description |
|---|---|
| `markdown_short` | Short markdown summary for issue bodies or clipboard use |
| `text_full` | Full plain-text report |
| `suggested_description` | Editable issue template derived from diagnostics |
| `report` | Full masked structured report |

`suggested_description` is intentionally operator-editable. It is seeded from recent issue logs, Bluetooth connection health, device `last_error` fields, subprocess health, D-Bus / `bluetoothd` state, MA connectivity, and the top recovery-guidance issue.

### `GET /api/diagnostics/download`

Downloads the masked diagnostics report as a **plain-text attachment** (`diagnostics-<timestamp>.txt`).

### `GET /api/groups`

Returns configured players grouped by MA syncgroup. Groups may include `external_members` / `external_count` when the same MA syncgroup also contains players from other bridges.

### `GET /api/onboarding/assistant`

First-run/setup guidance derived from preflight checks, config, device state, and MA connectivity.

**Key fields:** `runtime_mode`, `counts`, `checks[]`, `next_steps[]`, and `checklist`.

The checklist always orders steps as: `bluetooth`, `audio`, `sink_verification`, `ma_auth`, `latency`.

### `GET /api/recovery/assistant`

Recovery-oriented guidance derived from live device health and startup state.

**Key fields:** `summary`, `issues[]`, `traces[]`, `safe_actions[]`, `latency_assistant`, and `known_good_test_path`.

### `GET /api/operator/guidance`

Unified dashboard guidance surface assembled from onboarding + recovery.

**Key fields:**

- `mode` — `empty_state`, `progress`, `attention`, or `healthy`
- `visibility_keys` — local UI preference keys for dismissible cards
- `header_status` — compact status pill shown at the top of the dashboard
- `banner` — optional attention banner
- `onboarding_card` — optional guided setup card
- `issue_groups[]` — grouped recovery/setup issues with recommended actions

### `GET /api/version`

```json
{ "version": "2.x.y", "build_date": "2026-03-22" }
```

### `GET /api/health`

Lightweight health endpoint. Returns `200 OK` with:

```json
{ "ok": true }
```

### `GET /api/preflight`

Setup-verification endpoint used by onboarding and diagnostics.

```json
{
  "platform": "x86_64",
  "audio": { "system": "pulseaudio", "socket": "unix:/run/pulse/native", "sinks": 2 },
  "bluetooth": { "controller": true, "adapter": "C0:FB:F9:62:D6:9D", "paired_devices": 3 },
  "dbus": true,
  "memory_mb": 2048,
  "version": "2.x.y",
  "ok": true
}
```

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

MA credentials are persisted in `config.json` (`MA_API_URL`, `MA_API_TOKEN`). Successful auth flows automatically trigger group rediscovery so `/api/status`, `/api/groups`, and queue metadata can update without a restart.

### `GET /api/ma/discover`

Start asynchronous Music Assistant discovery.

- In HA add-on mode the bridge prefers add-on-local MA URLs before falling back to saved config / mDNS.
- The response is always async.

```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "running", "is_addon": true }
```

### `GET /api/ma/discover/result/<job_id>`

Poll MA discovery.

- While running: `{ "status": "running", "is_addon": true }`
- On completion: returns the stored job payload (for example discovered servers or an error)

### `POST /api/ma/login`

Direct Music Assistant login using MA credentials.

**Body:**
```json
{ "url": "http://192.168.1.10:8095", "username": "ma_user", "password": "ma_pass" }
```

**Contract notes:**

- `url` is optional. If omitted, the bridge tries the saved MA URL, `SENDSPIN_SERVER`, connected Sendspin hosts, then mDNS.
- This endpoint is for MA's own credential flow. It does **not** switch between HA auth providers.
- On success the bridge saves the long-lived token and triggers rediscovery immediately.

### `GET /api/ma/ha-auth-page`

Returns a self-contained **HTML popup document** for browser-driven Home Assistant login/MFA.

**Query parameter:** `ma_url`

The popup posts its success/failure result back to `window.opener`; it is not a JSON endpoint.

### `POST /api/ma/ha-silent-auth`

Silent add-on-mode auth using an existing HA access token.

**Body:**
```json
{ "ha_token": "<HA access token>", "ma_url": "http://homeassistant.local:8095" }
```

**Contract notes:**

- Intended for HA add-on runtime only.
- The bridge validates the HA token over the HA WebSocket API, creates an MA token via ingress JSON-RPC, validates that token against the regular MA API, then saves it.
- If a previously saved MA token already matches the same MA instance and still validates, the endpoint returns success without minting a new token.

### `POST /api/ma/ha-login`

Explicit Home Assistant credential flow with optional MFA.

**Step 1 — init:**
```json
{ "step": "init", "ma_url": "http://192.168.1.10:8095", "username": "ha_user", "password": "ha_pass" }
```

**Possible response:**
```json
{ "success": true, "step": "mfa", "auth_mode": "ha_direct", "flow_id": "...", "ha_url": "http://haos:8123", "client_id": "http://haos:8123/", "state": "...", "mfa_module_name": "Authenticator app" }
```

**Step 2 — mfa:**
```json
{ "step": "mfa", "ma_url": "http://192.168.1.10:8095", "flow_id": "...", "ha_url": "http://haos:8123", "client_id": "http://haos:8123/", "auth_mode": "ha_direct", "code": "123456" }
```

A successful completion returns `step: "done"`, saves the MA token, and triggers rediscovery.

### `GET /api/ma/groups`

Returns cached MA syncgroup players from the MA API. Empty list means discovery has not run yet or MA is not configured.

### `POST /api/ma/rediscover`

Re-run MA syncgroup discovery from the currently saved `MA_API_URL` / `MA_API_TOKEN`.

**Response:** `202 Accepted`
```json
{ "success": true, "job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "running" }
```

### `GET /api/ma/rediscover/result/<job_id>`

Poll the async rediscovery job.

- While running: `{ "status": "running" }`
- On completion: returns the stored job payload (for example `syncgroups`, `mapped_players`, `groups`, or an error)

### `GET /api/ma/nowplaying`

Returns the bridge's current MA now-playing cache.

- When MA is inactive: `{ "connected": false }`
- When active: includes `state`, `track`, `artist`, `album`, `image_url`, `elapsed`, `duration`, `shuffle`, `repeat`, `queue_index`, `queue_total`, `syncgroup_id`, and optional adjacent-track metadata.

### `POST /api/ma/queue/cmd`

Send an asynchronous playback-control command to the active MA queue.

**Body:**
```json
{ "action": "next", "syncgroup_id": "ma-syncgroup-abc123" }
```

| Field | Description |
|---|---|
| `action` | `"next"`, `"previous"`, `"shuffle"`, `"repeat"`, or `"seek"` |
| `value` | For `shuffle`: boolean. For `repeat`: `"off"`, `"all"`, `"one"`. For `seek`: seconds |
| `syncgroup_id` | Optional syncgroup target |
| `player_id` | Optional explicit player target |
| `group_id` | Optional legacy group target |

**Accepted response:**
```json
{
  "success": true,
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "op_id": "6a6f...",
  "syncgroup_id": "ma-syncgroup-abc123",
  "queue_id": "up_abc123def456",
  "accepted": false,
  "confirmed": false,
  "pending": true,
  "ma_now_playing": { "state": "playing" }
}
```

The HTTP response is optimistic: it includes a predicted `ma_now_playing` patch immediately, while final confirmation arrives through the async job plus `MaMonitor` updates.

### `GET /api/ma/queue/cmd/result/<job_id>`

Poll the async MA queue-command job.

- While running: `{ "status": "running" }`
- On completion: returns the stored job payload

### `GET /api/debug/ma`

Debug dump of MA cache keys, discovered groups, per-client player IDs, and live queue IDs fetched from the MA WebSocket.

## Bluetooth Control

### `POST /api/bt/reconnect`

Force Bluetooth reconnect.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF" }`

### `POST /api/bt/pair`

Start pairing (~25 sec). Device must already be in pairing mode.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0" }`

### `POST /api/bt/management`

Toggle Bluetooth management (Release/Reclaim).

**Body:** `{ "player_name": "Living Room", "enabled": false }`

### `POST /api/device/enabled`

Toggle global device enabled/disabled state. Requires a restart to take effect — disabled devices are skipped completely (no client, no BT manager, no listen port).

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

Start a background Bluetooth scan (~10 s). Returns immediately with a job ID.

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

List available Bluetooth adapters.

### `GET /api/bt/paired`

List currently paired devices (name + MAC).

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

Current configuration from `config.json`. In HA add-on mode the response also exposes `_delivery_channel`, `_effective_web_port`, and `_effective_base_listen_port`, while `WEB_PORT` is intentionally returned as `null` because ingress owns the external port. Per-device `preferred_format` defaults to `flac:44100:16:2` when omitted from stored config.

### `POST /api/config`

Save configuration. Body: JSON config object. Effective add-on track defaults (`_delivery_channel`, effective ports) are runtime-derived rather than user-settable fields.

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
  -d '{"mac": "AA:BB:CC:DD:EE:FF", "volume": 50}'

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
