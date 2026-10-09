# API v1 — endpoint map (ADR-0001, stage 1)

Every route of the Flask API as of 2.77.0-rc.2, and where it goes in `/api/v1`.
`job` means the operation returns `202` + `Location: /api/v1/jobs/{id}`;
`/jobs/{id}` replaces every `…/result/<job_id>` route. Paths below are relative
to `/api/v1` unless marked `compat` (kept verbatim for the HA custom component)
or `public` (no authentication).

## Bridge

| Old | v1 | Notes |
|---|---|---|
| `GET /api/status` | `GET /bridge` + `GET /devices` | status split into bridge and device resources |
| `GET /api/version` | `GET /bridge` (`version`, `build_date`) | |
| `GET /api/runtime-info` | `GET /bridge/runtime` | |
| `GET /api/startup-progress` | `GET /bridge/startup` | |
| `GET /api/bridge/telemetry` | `GET /bridge/telemetry` | |
| `POST /api/restart` | `POST /bridge/restart` | |
| `POST /api/settings/log_level` | `PUT /bridge/log-level` | |
| `GET /api/health` | `GET /health` (public) | |
| `GET /api/preflight` | `GET /bridge/preflight` (public) | |
| `POST /api/sendspin/test` | `POST /bridge/sendspin-test` | job |

## Devices (bridge speakers)

`{id}` is the device's player id; the Music Assistant player it maps to is a
field of the device (`music_assistant.player_id`), not a separate resource.

| Old | v1 | Notes |
|---|---|---|
| — (`/api/status` devices) | `GET /devices`, `GET /devices/{id}` | |
| `POST /api/device/enabled` | `PATCH /devices/{id}` `{enabled}` | |
| `POST /api/volume` | `PUT /devices/{id}/volume` `{level}`; `PUT /groups/{id}/volume` | `group` / all-devices variants become group / bulk endpoints |
| `POST /api/mute` | `PUT /devices/{id}/mute` `{muted}` | |
| `POST /api/unmute_sink` | `POST /devices/{id}/unmute-sink` | |
| `POST /api/pause` | `POST /devices/{id}/playback` `{action: pause\|play}` | |
| `POST /api/pause_all` | `POST /playback/pause-all` | |
| `POST /api/transport/cmd` | `POST /devices/{id}/transport` `{command, value}` | |
| `POST /api/bt/standby` | `POST /devices/{id}/standby` | |
| `POST /api/bt/wake` | `POST /devices/{id}/wake` | |
| `POST /api/bt/power_save` | `PUT /devices/{id}/power-save` `{enabled}` | |
| `POST /api/bt/management` | `PUT /devices/{id}/management` `{enabled}` | release / reclaim |
| `POST /api/bt/claim/<mac>` | `POST /devices/{id}/claim` | |
| `POST /api/bt/reconnect` | `POST /devices/{id}/reconnect` | |
| `POST /api/bt/disconnect` | `POST /devices/{id}/disconnect` | |
| `POST /api/bt/reset_reconnect` + `/result/<id>` | `POST /devices/{id}/reset` | job |
| `GET /api/devices/<id>/latency/history` | `GET /devices/{id}/latency/history` | |

## Bluetooth

| Old | v1 | Notes |
|---|---|---|
| `GET /api/bt/adapters` | `GET /adapters` | |
| `POST /api/bt/adapter/power` | `PUT /adapters/{id}/power` `{on}` | |
| `POST /api/bt/scan` + `/result/<id>` | `POST /bluetooth/scans` | job; result = discovered devices |
| `POST /api/bt/pair` | `POST /bluetooth/pairings` `{mac, adapter, options}` | job |
| `POST /api/bt/pair_new` + `/result/<id>` | `POST /bluetooth/pairings` `{…, add_to_fleet: true}` | job; same resource |
| `POST /api/pairing/window` | `POST /bluetooth/pairing-window` | Sendspin pairing window |
| `GET /api/bt/paired` | `GET /bluetooth/devices?paired=true` | |
| `POST /api/bt/info` | `GET /bluetooth/devices/{mac}` | |
| `POST /api/bt/remove` | `DELETE /bluetooth/devices/{mac}` | |

## Groups and Music Assistant

| Old | v1 | Notes |
|---|---|---|
| `GET /api/groups` | `GET /groups` | |
| `POST /api/group/pause` | `POST /groups/{id}/playback` `{action}` | |
| `GET /api/ma/groups` | `GET /music-assistant/groups` | |
| `GET /api/ma/nowplaying` | `GET /music-assistant/now-playing` | |
| `POST /api/ma/queue/cmd` + `/result/<id>` | `POST /music-assistant/queue-commands` | job |
| `GET /api/ma/artwork` | `GET /music-assistant/artwork?url=&sig=` | binary |
| `GET /api/ma/discover` + `/result/<id>` | `POST /music-assistant/discovery` | job |
| `POST /api/ma/rediscover` + `/result/<id>` | `POST /music-assistant/discovery` `{force: true}` | job |
| `POST /api/ma/reload` | `POST /music-assistant/reload` | |
| `POST /api/ma/login` | `POST /music-assistant/session` `{url, username, password}` | |
| `POST /api/ma/ha-login` | `POST /music-assistant/session/ha` | |
| `POST /api/ma/ha-silent-auth` | `POST /music-assistant/session/ha-silent` | |
| `GET /api/ma/ha-auth-page` | `GET /music-assistant/session/ha-auth-page` | HTML popup helper |
| `GET /api/debug/ma` | `GET /diagnostics/music-assistant` | |

## Home Assistant integration

| Old | v1 | Notes |
|---|---|---|
| `POST /api/ha/areas` | `GET /ha-integration/areas` | read-only lookup |
| `GET /api/ha/mqtt/status` | `GET /ha-integration/mqtt` | |
| `GET /api/ha/mqtt/probe` | `POST /ha-integration/mqtt/probe` | |
| `POST /api/ha/mqtt/test` | `POST /ha-integration/mqtt/test` | |
| `GET /api/ha/mosquitto/status` | `GET /ha-integration/mosquitto` | |
| `GET /api/ha/rest/probe` | `POST /ha-integration/rest/probe` | |
| `GET /api/ha/mdns/status` | `GET /ha-integration/mdns` | |
| `GET /api/ha/custom_component/status` | `GET /ha-integration/custom-component` | |
| `GET /api/ha/state` | compat, then `GET /bridge` + `GET /devices` | HA custom component |
| `POST /api/ha/command` | compat, then device endpoints | HA custom component |
| `POST /api/ha/command/bridge` | compat, then bridge endpoints | HA custom component |
| `GET /api/status/events` | compat, then `GET /events` | HA custom component |
| `POST /api/auth/ha-pair` | compat (public, `_is_ha_addon()` gate), then `POST /auth/ha-pair` | HA custom component |

## Config

| Old | v1 | Notes |
|---|---|---|
| `GET /api/config` | `GET /config` | schema v6 model |
| `POST /api/config` | `PUT /config` | response carries the reconfig summary |
| `POST /api/config/validate` | `POST /config/validate` | |
| `GET /api/config/download` | `GET /config/export` | file |
| `POST /api/config/upload` | `POST /config/import` | |

## Diagnostics

| Old | v1 | Notes |
|---|---|---|
| `GET /api/diagnostics` | `GET /diagnostics` | |
| `GET /api/diagnostics/download` | `GET /diagnostics/report` | text file |
| `GET /api/bugreport` | `GET /diagnostics/bug-report` | prefilled report |
| `GET /api/bugreport/proxy-available` | `GET /diagnostics/bug-report/proxy` | |
| `POST /api/bugreport/submit` | `POST /diagnostics/bug-report` | |
| `GET /api/onboarding/assistant` | `GET /diagnostics/onboarding` | |
| `GET /api/recovery/assistant` | `GET /diagnostics/recovery` | |
| `GET /api/recovery/timeline` | `GET /diagnostics/timeline` | |
| `GET /api/recovery/timeline/download` | `GET /diagnostics/timeline.csv` | |
| `GET /api/operator/guidance` | `GET /diagnostics/guidance` | |
| `POST /api/checks/rerun` | `POST /diagnostics/checks/{key}/run` | |
| `GET /api/logs` | `GET /diagnostics/logs` | |
| `GET /api/logs/download` | `GET /diagnostics/logs.txt` | |
| `GET /api/logs/stream` | `GET /events?types=log` | event stream |

## Latency and calibration

| Old | v1 | Notes |
|---|---|---|
| `POST /api/latency` | `PUT /devices/{id}/latency` | |
| `GET /api/latency/recommendations` | `GET /latency/recommendations` | |
| `POST /api/latency/apply` | `POST /latency/recommendations/apply` | |
| `GET /api/calibration/tone.wav` | `GET /calibration/tone.wav` | |
| `POST /api/calibration/play` | `POST /calibration/play` | |
| `POST /api/calibration/metronome` | `POST /calibration/metronome` | |
| `POST /api/calibration/sessions` | `POST /calibration/sessions` | |
| `POST /api/calibration/sessions/<id>/audio` | `POST /calibration/sessions/{id}/audio` | |
| `DELETE /api/calibration/sessions/<id>` | `DELETE /calibration/sessions/{id}` | |

## Updates, auth, hooks, events

| Old | v1 | Notes |
|---|---|---|
| `GET /api/update/info` | `GET /updates` | |
| `POST /api/update/check` + `/result/<id>` | `POST /updates/check` | job |
| `POST /api/update/apply` | `POST /updates/apply` | job |
| `GET/POST /login` | `POST /auth/session` (public), `GET /auth/session` | the SPA renders the sign-in form |
| `GET/POST /logout` | `DELETE /auth/session` | |
| `POST /api/set-password` | `PUT /auth/password` | |
| `GET /api/auth/tokens` | `GET /auth/tokens` | |
| `POST /api/auth/tokens` | `POST /auth/tokens` | |
| `DELETE /api/auth/tokens/<id>` | `DELETE /auth/tokens/{id}` | |
| `GET /api/hooks` | `GET /hooks` | |
| `POST /api/hooks` | `POST /hooks` | |
| `DELETE /api/hooks/<id>` | `DELETE /hooks/{id}` | |
| `GET /api/status/stream` | `GET /events` (SSE) | typed envelope |
| `GET /api/status/ws` | `GET /events/ws` (WebSocket) | |
| every `…/result/<job_id>` | `GET /jobs/{id}` | |

## Dropped

| Old | Why |
|---|---|
| `/`, `/static/v<version>/…` (legacy UI) | replaced by the SPA, served from `/` |
| `GET /api/status/ws` scaffold in `api_ws.py` | replaced by `/events/ws` |

## Resolved open questions

1. **TypeScript client:** `openapi-typescript` (types) + `openapi-fetch` (runtime);
   no generated runtime code to review, types regenerate from `openapi.json`.
2. **Event versioning:** one envelope version (`v: 1`) and a `type`
   discriminator; event payload schemas live in OpenAPI as a union.
3. **Devices vs. Music Assistant players:** one `devices` resource; the MA
   player id and state are fields of it.
4. **Compat router lifetime:** until one stable release after the HA custom
   component ships on v1.
