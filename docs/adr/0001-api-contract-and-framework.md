# ADR-0001 — One typed API as the single source of truth (FastAPI + Pydantic)

* **Status:** accepted (2026-10-09)
* **Date:** 2026-10-09
* **Plan:** [v3 — API-first rewrite](../plans/2026-10-09-v3-api-first-rewrite.md)

## Context

The bridge's HTTP surface grew around its web page, not around a contract:

* ~106 Flask endpoints across 12 blueprints (~10.9 k lines), named by verb
  (`/api/bt/pair_new`, `/api/unmute_sink`, `/api/pause_all`), several
  endpoints per long-running operation (`…` + `…/result/<job_id>`), ad-hoc
  JSON shapes and error bodies.
* No machine-readable schema. The legacy UI (`app.js`, 15.7 k lines) and the
  HA custom component each know the shapes by convention; `config.schema.json`
  and `config_validation` describe the config twice, by hand.
* Flask runs in waitress worker threads; the bridge runs on one asyncio loop.
  32 call sites cross that boundary with `run_coroutine_threadsafe`, and
  waitress cannot upgrade to WebSocket (`api_ws.py` is dormant because of it)
  and buffered SSE output until we capped it (#502).
* We now want three clients — a Vue SPA, the HA integration, an MCP server —
  and the legacy UI is to be removed rather than ported.

## Decision

### 1. Framework: FastAPI + Pydantic v2, served by uvicorn in the bridge loop

* Pydantic models are the contract. FastAPI derives OpenAPI 3.1 from them; the
  OpenAPI document is generated, checked in, and verified fresh in CI.
* uvicorn runs as a task on the bridge's own asyncio loop (`uvicorn.Server(...).serve()`),
  so handlers await bridge services directly. The thread→loop bridges disappear,
  and SSE and WebSocket are both available.
* Every blocking call (synchronous D-Bus, `pactl`, file I/O on slow storage) is
  offloaded through an explicit executor wrapper. A test fails if an async
  handler path reaches a known blocking call directly.

### 2. An application layer between the API and the domain

Use cases live in `application/` as plain async services taking and returning
Pydantic models: queries (`get_bridge_status`, `list_devices`, …) and commands
(`connect_device`, `start_pairing`, `set_volume`, `update_config`, …). They know
nothing about HTTP, sessions or MCP. REST routers, the event stream and MCP
tools are thin adapters over them. Behaviour is tested at this layer.

### 3. REST v1: resources, jobs, problems

* Base path `/api/v1`. Resources, not verbs:

  | Resource | Replaces (today) |
  |---|---|
  | `bridge` (status, version, runtime, restart, log level) | `status`, `version`, `runtime-info`, `startup-progress`, `restart`, `settings/log_level`, `health`, `preflight`, `bridge/telemetry`, `sendspin/test` |
  | `devices` (CRUD, enable, volume, mute, transport, standby, power-save, claim/release, latency) | `device/*`, `volume`, `mute`, `pause`, `pause_all`, `transport/cmd`, `bt/standby`, `bt/wake`, `bt/power_save`, `bt/claim`, `bt/management`, `unmute_sink`, `devices/*/latency/history` |
  | `adapters` (list, power) | `bt/adapters`, `bt/adapter/power` |
  | `bluetooth/scans`, `bluetooth/pairings`, `bluetooth/devices` (paired, info, remove, reconnect, reset) | `bt/scan*`, `bt/pair*`, `bt/pair_new*`, `bt/paired`, `bt/info`, `bt/remove`, `bt/reconnect`, `bt/reset_reconnect*`, `bt/disconnect`, `pairing/window` |
  | `groups` | `groups`, `group/pause` |
  | `music-assistant` (connection, sign-in, discovery, groups, now-playing, queue, artwork) | `ma/*`, `debug/ma` |
  | `ha-integration` (areas, MQTT, REST, mDNS, custom component status) | `ha/*` except the compat set |
  | `config` (get, put, validate, import, export) | `config*` |
  | `diagnostics` (report, checks, guidance, onboarding, recovery, timeline, logs, bug report) | `diagnostics*`, `checks/rerun`, `operator/guidance`, `onboarding/assistant`, `recovery/*`, `logs*`, `bugreport*` |
  | `calibration`, `latency` | `calibration/*`, `latency*` |
  | `updates` | `update/*` |
  | `auth` (session, password, tokens) | `auth/*`, `set-password`, `/login`, `/logout` |
  | `hooks` | `hooks*` |
  | `events` (SSE `GET /events`, WebSocket `/events/ws`) | `status/stream`, `status/events`, `status/ws`, `logs/stream` |
  | `jobs/{id}` | every `…/result/<job_id>` |

  The full old → v1 table, including endpoints that are dropped, is the
  deliverable of plan stage 1.
* **Jobs.** Anything that takes longer than a request (scan, pairing, update
  check/apply, reset-reconnect, MA discovery, queue commands) is
  `POST /…` → `202 Accepted` + `Location: /api/v1/jobs/{id}`; progress and
  completion also arrive as events.
* **Errors.** `application/problem+json` (RFC 9457) with a stable `code`
  taken from the existing guidance-issue registry, so clients branch on codes,
  never on wording.
* **Events.** One typed, versioned event envelope (`type`, `at`, `subject`,
  `data`), the same over SSE and WebSocket. SSE keeps the
  `Cache-Control: no-cache, no-transform` / `Content-Encoding: identity`
  headers HA ingress needs.

### 4. One authentication model for every entry point

A request is authenticated by exactly one of: the HA Supervisor ingress
identity (existing trust rules), a session cookie (SPA sign-in, PBKDF2 password
as today), or a bearer token (`AUTH_TOKENS`: HA integration, MCP, scripts).
Brute-force limiting is middleware. `/auth/ha-pair` keeps its `_is_ha_addon()`
gate.

### 5. Config as Pydantic models, schema v6

The config is defined once as Pydantic models; the JSON Schema shipped for docs
and add-on option translation is generated from them, replacing
`config.schema.json` and the hand-written validation. Schema v6 replaces
`BLUETOOTH_DEVICES` with `players[]`; v5 → v6 migrates on load. With the legacy
UI gone there is no writer of the v5 shape left to stay compatible with.

### 6. Clients

* **SPA:** TypeScript client generated from the checked-in OpenAPI
  (`openapi-typescript` + `openapi-fetch` or equivalent, chosen in stage 1);
  CI fails if the generated client is stale. The legacy UI (`templates/`,
  `static/app.js`, `routes/views.py`) is deleted in stage 3.
* **MCP:** FastMCP mounted at `/mcp`, with hand-curated tools and resources
  that call the application layer. FastMCP can turn an OpenAPI document into an
  MCP server automatically, but its authors recommend curated tools for
  anything beyond a prototype, and the application layer gives us those for
  free.
* **HA custom component:** a time-boxed compat router keeps its five current
  endpoints working until a component release on v1 is out.

## Alternatives considered

* **Keep Flask, add `apispec`/`flask-smorest`.** Gives OpenAPI, but keeps
  waitress (no WebSocket, thread→loop bridges) and makes the schema a
  by-product of decorators instead of the source.
* **Litestar.** Comparable model (msgspec, faster serialisation, DTOs).
  Rejected for ecosystem reach: OpenAPI client generators, FastMCP and
  contributor familiarity are strongest around FastAPI. The bridge's API load
  is small enough that serialisation speed does not decide it.
* **Quart (async Flask).** Smallest port, but no schema-first contract.
* **Port the beta branch's backend layer and Vue API layer as-is.** They
  target the March codebase and would be rewritten anyway.

## Consequences

* Positive: one contract for SPA, HA and MCP; generated clients; WebSocket
  possible; no thread→loop bridging; behaviour tested below HTTP.
* Negative: a big-bang change to the web layer; 41 test files move to httpx;
  every API consumer migrates; v3 cannot reach stable before the SPA covers
  the legacy UI.
* Operational: the loop is shared, so blocking calls become a correctness
  issue, guarded by tests; uvicorn and the Node build must work on armv7.

## Open questions (resolved in stage 1)

Resolved in the [endpoint map](0001-api-v1-endpoint-map.md#resolved-open-questions):
`openapi-typescript` + `openapi-fetch`; one event envelope version with a
`type` discriminator; Music Assistant players are a field of `devices`; the HA
compat router lives until one stable release after the v1 custom component.
