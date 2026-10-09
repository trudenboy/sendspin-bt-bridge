# v3 — API-first rewrite: plan

> Supersedes the 3.0.0-beta.1…beta.14 line on the `beta` branch (April 2026)
> as the route to 3.0. Architecture decisions are recorded in
> [ADR-0001](../adr/0001-api-contract-and-framework.md).

**Goal:** one typed API is the single source of truth for everything the bridge
can do. A new Vue SPA, the Home Assistant integration and, later, an MCP server
are clients of that API. The legacy Jinja/vanilla-JS web UI is removed outright,
not kept as a fallback.

## Where we start

### The `beta` branch (3.0.0-beta.14, 2026-04-01)

Forked from main at `8f0cc6f7` (2026-03-31); 64 commits of its own, main is
1 043 commits ahead. It holds two things worth salvaging and nothing that can
be merged:

| Part | Content | What happens to it |
|---|---|---|
| V3-1 backend layer | `AudioBackend` ABC, BT-A2DP/mock backends, `Player` model, `BackendOrchestrator`, `EventStore` + `/api/events`, config schema "v2" (`players[]`) — flat layout, ~1.8 k lines | Ideas only. Main already covers the roles (`SendspinClient`, `BluetoothManager`/`BluetoothDevice`, `DeviceRegistry`, `device_activation`, `bridge_state_model`, device events). Config `players[]` becomes schema **v6** (main is at v5; beta's "v2" numbering collides). |
| Epic 4 Vue SPA | Vue 3 + TS + Vite + Pinia + Tailwind 4 + vue-i18n, 21 kit components, ~280 tests, SSE status store | Ported as the starting point of the new SPA; its API layer is replaced by a client generated from the new contract. |

The HA **beta** add-on channel still serves 3.0.0-beta.14, so beta users run
April code.

### main (2.77.0-rc.2)

* Flask + waitress, 12 blueprints, ~10.9 k lines of routes, ~106 endpoints
  (`bt` 19, `ma` 14, `ha` 11, `calibration` 6, `update` 4, `status` 4, `auth` 4,
  …), 32 thread→loop bridges (`run_coroutine_threadsafe`).
* Legacy UI: `static/app.js` 15.7 k lines + `templates/index.html` 1.5 k lines.
* Other API consumers: the HA custom component (`/api/auth/ha-pair`,
  `/api/ha/command`, `/api/ha/command/bridge`, `/api/ha/state`,
  `/api/status/events`), the demo deployment (`render.yaml`, `demo/`), the
  bug-report proxy, the MQTT publisher (internal).
* 41 test files drive Flask's test client.

## Target architecture

```
domain / state     DeviceRegistry, DeviceStatus, bridge_state_model, config   (exists)
application        use cases: queries + commands, Pydantic in/out            ← single source of truth
adapters
 ├─ REST  /api/v1   thin FastAPI routers over application
 ├─ events          typed event stream: SSE + WebSocket
 ├─ MCP   /mcp      curated tools/resources over application (FastMCP)
 └─ compat          narrow router for the HA custom component, time-boxed
SPA (Vue)          TypeScript client generated from OpenAPI, freshness checked in CI
```

FastAPI + Pydantic v2 served by uvicorn inside the bridge's own asyncio loop.
Details and alternatives: ADR-0001.

## Stages

| # | Stage | Deliverable | Done when |
|---|---|---|---|
| 0 | Hygiene (main) — **done 2026-10-09** | `ui/node_modules` untracked (#518). Beta channel: decided to leave it on 3.0.0-beta.14 until the first real v3 beta (stage 4) rather than publish main as 3.0.0-beta.15 | CI green |
| 1 | Contract — **done** | ADR-0001 accepted; resource map; Pydantic models; generated OpenAPI 3.1 checked in; endpoint migration table (old → v1 / dropped) | OpenAPI reviewed; every current endpoint mapped |
| 2 | Application layer (branch `v3` from main) — **done** | business logic moved out of Flask routes into use-case services with service-level tests | behaviour parity proven by tests; routes are thin wrappers |
| 3 | FastAPI — **done** (live: local bridge; HAOS ingress and CT 107 pending) | uvicorn in the bridge loop; v1 routers; SSE + WS events; auth (ingress, session, bearer); HA ingress `root_path`; compat router; **Flask, waitress, legacy UI and views deleted**; tests on httpx | full suite; live stand: HAOS ingress, LXC (CT 107), Docker |
| 4 | SPA MVP — **done** (live: dashboard, devices, scan, config, diagnostics, MA on the local bridge) | beta `ui/` ported and updated; generated client; screens: status & devices, scan → pair → add, volume/transport, MA (sign-in, groups, queue), HA integration, diagnostics & bug report, updates, sign-in | live e2e on the stand: scan → pair → add → audio |
| 5 | Parity | calibration & latency, downloads (config/logs/diagnostics/timeline), auth tokens & password, power-save, pairing window, Sendspin test, the rest of the legacy UI's 33 features the beta SPA never had | parity checklist closed |
| 6 | Packaging | Docker multi-stage (Node build), LXC `vue-dist` release asset pinned to the version, add-on variants, armv7 | upgrades from 2.77 rc and 2.76.x on CT 107 and HAOS |
| 7 | MCP | FastMCP at `/mcp`: curated read-only resources first (status, devices, diagnostics), then safe commands; bearer auth; audit of exposed commands | MCP client session against the stand |
| 8 | Release | 3.0.0-beta.N → rc → stable; changelog: legacy UI removed, API v1, config v6 | beta channel on the stand, then users |

## Progress and deviations (2026-10-09)

* **Config stays on schema v5 for the first beta.** The v1 config models are
  generated from the v5 schema; `players[]` (v6) moves to stage 5, together
  with the settings screens that edit it. Nothing in API v1 depends on the
  file layout, so the switch is internal.
* **No `/api/events` event store.** Device events ride on each device
  (`recent_events`) and the recovery timeline (`/api/v1/diagnostics/timeline`)
  serves the history view.
* **CSRF only for cookie sessions.** With authentication off, writes from
  another site are refused by `Origin`/`Sec-Fetch-Site` instead (the HA
  integration and scripts post without a token, as before).
* **Packaging (part of stage 6) landed with stage 4:** the Docker image builds
  the SPA in a Node stage; release tarballs carry it pre-built for LXC, and
  rc/beta tags get a GitHub pre-release holding only that tarball.

## Risks

* **Feature gap.** The legacy UI goes immediately, so `v3` ships only to the beta
  channel until stage 4 (MVP) is done; stable waits for stage 5.
* **Blocking the loop.** Flask threads hide synchronous D-Bus/`pactl` calls today.
  In a shared loop one forgotten call freezes bridge and API alike. Every
  blocking call goes through an explicit executor wrapper, enforced by a test.
* **HA ingress on ASGI.** `root_path`, forwarded headers and SSE compression were
  fragile before (rc.3/rc.4); verify on the stand in stage 3, not stage 8.
* **HA custom component.** Its HACS release has to move to v1 before the compat
  router is removed.
* **armv7.** Node build stage and uvicorn wheels on 32-bit ARM; keep the pure-Python
  fallbacks and test the image.
