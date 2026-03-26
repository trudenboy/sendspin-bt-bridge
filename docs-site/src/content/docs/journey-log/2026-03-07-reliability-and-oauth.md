---
title: "March 7–10: Reliability, multi-bridge, and HA OAuth"
description: "Volume architecture, deployment hardening, community engagement, and the full HA OAuth and MA API authentication wave"
---

## March 7, 2026 — Reliability & deployment (v2.10.7 → v2.12.0)

### Volume architecture overhaul (v2.10.7 – v2.10.13)

The hybrid volume path — routing volume commands through the MA WebSocket API to keep MA's UI in sync — introduced a triple feedback loop: the API, the sendspin protocol echo, and the MA monitor event all tried to set the PulseAudio sink volume simultaneously. The result was volume bouncing (set 40 → jumps to 47 → settles at 55) and unexpected jumps on track change.

The fix was architectural: **bridge_daemon became the single writer** to PulseAudio sink volume. The API no longer optimistically updates local status on the MA path — it waits for the actual echo from MA via sendspin protocol. The `_handle_player_updated` volume sync in the MA monitor was removed as a redundant third path. A new `VOLUME_VIA_MA` config option (default: `true`) allows disabling the MA proxy entirely, forcing all volume/mute changes through direct pactl.

### Observability and test infrastructure (v2.10.8)

All 27 silent `except: pass` blocks were replaced with DEBUG-level logging — issues are now visible with `LOG_LEVEL=DEBUG` without changing runtime behavior. Thread safety was hardened: `run_coroutine_threadsafe` calls got 5-second timeouts, and fire-and-forget asyncio tasks got `done_callback` for exception logging. The project gained its first automated tests: pytest with 9 unit tests covering config loading, volume persistence, MAC-to-player-ID mapping, and password hashing (later expanded to 15 tests).

### LXC installer modernization (v2.10.16)

The LXC installer was updated to download all app modules (config, state, routes, services, templates, static) instead of the original 2 files. PulseAudio configuration was fixed for PA 17+ on Ubuntu 24.04: deprecated `enable-lfe-remixing` replaced with `remixing-produce-lfe`/`remixing-consume-lfe`, the systemd unit no longer sets `User=pulse`/`Group=pulse` (PA `--system` mode requires root), and a tmpfiles.d entry ensures `/var/run/pulse` survives reboots.

### OpenWrt LXC deployment (v2.11.0)

A new `lxc/install-openwrt.sh` installer added support for OpenWrt-based routers (Turris Omnia, etc.) with procd service management — expanding deployment from 3 methods (Docker, HA addon, Proxmox LXC) to 4.

### Zombie playback watchdog and churn isolation (v2.12.0)

Two reliability features were added:

- **Zombie playback watchdog**: auto-restarts the subprocess after 15 seconds of `playing=True` with no audio data (`streaming=False`), up to 3 retries. This catches situations where the sendspin connection is alive but the audio pipeline is broken.
- **BT churn isolation** (opt-in): auto-disables BT management for devices that reconnect too often within a sliding window, configurable via `BT_CHURN_THRESHOLD` (0 = disabled, default) and `BT_CHURN_WINDOW` (default 300 s). Prevents a flaky Bluetooth device from consuming adapter time and destabilizing other speakers.

A new **stale equalizer indicator** shows frozen red bars when MA reports playing but no audio is streaming, with playback text showing "▶ No Audio".

## March 8, 2026 — Multi-bridge & community (v2.12.1 → v2.13.1)

### Caching, SSE reliability, and HA Ingress fixes (v2.12.1 → v2.12.6)

A series of quick-fire releases addressed progressive discovery of HA Ingress proxy behavior: static asset cache-busting via query string (`?v=`) was ineffective because Ingress strips query parameters — switched to path-based versioning (`/static/v2.12.5/app.js`). HTML responses gained `Cache-Control: no-cache` headers. The SSE stream got 2 KB initial padding to flush proxy buffers, and the client-side SSE reconnect logic was upgraded from "fail once → poll forever" to exponential backoff with 5 retries.

### Lazy player registration (v2.12.2)

The sendspin daemon now starts only after Bluetooth actually connects, eliminating phantom players in Music Assistant at container startup.

### Multi-bridge architecture analysis and improvements (v2.13.0)

A deep analysis of the multi-bridge scenario (multiple bridges → one MA instance, cross-bridge sync groups) identified 6 potential problems and led to two key improvements:

- **Auto-populated BRIDGE_NAME**: on first startup, the machine hostname is written to `config.json["BRIDGE_NAME"]` so users see a pre-filled value in the Web UI before adding devices. The old `BRIDGE_NAME_SUFFIX` boolean was removed — no longer needed when the name is auto-populated. This prevents duplicate player names (e.g. two "JBL Flip 6" from different hosts) which confused MA's player list.

- **Cross-bridge sync group visibility**: when players from multiple bridges belong to the same MA sync group, the group badge now shows `🔗 Kitchen Music +2` (where +2 = players from other bridges). Hovering the badge reveals the full member list with ✓ for local and 🌐 for external players. Data comes from the MA API cache (`/api/players` → sync group member lists) that the bridge already maintains.

### Production deployment fixes (v2.13.1)

Deploying v2.13.0 to two live LXC bridges (Proxmox + Turris OpenWrt) uncovered a chain of issues:

- **Waitress 3.x broke SSE**: upgrading `waitress` pulled in v3.x which strictly enforces PEP 3333 and rejects hop-by-hop headers. The `Connection: keep-alive` in the SSE response caused an `AssertionError` crash — removed the header entirely.
- **JS variable name mismatch**: both polling and SSE handlers in `app.js` referenced `data.groups` but the parsed variable is named `status` — devices never rendered. Fixed to `status.groups`.
- **Group enrichment ID mismatch**: `_build_groups_summary()` compared Sendspin's `group_id` (UUID) against MA's syncgroup ID (`syncgroup_XXX`) — different ID systems that never matched. Fixed by resolving MA syncgroup via player-name mapping.
- **Groups missing in polling response**: `/api/status` for single-device bridges omitted the `groups` field (only SSE included it), so the badge never appeared via polling.
- **LXC bluetooth.service incident**: accidentally restarting `bluetooth.service` inside the Turris container (where bluetoothd cannot run) broke PulseAudio's A2DP state, requiring device re-pair from host. Hardened: `bluetooth.service` is now **masked** (not just disabled), and `sendspin-client.service` gained `TimeoutStopSec=15` to prevent hung shutdowns.

### GitHub Issues & Discussions infrastructure (v2.13.0)

The project gained structured issue management: 3 YAML-based issue form templates (Bug Report with deployment/audio dropdowns, Bluetooth/Audio specialist form, Feature Request), 16 project labels (`type:bug`, `area:bluetooth`, `deploy:ha-addon`, etc.), and a Discussions Welcome post with routing guidance (Issues for bugs/features, Discussions for help/ideas).

### Comprehensive security hardening & code quality audit (v2.16.0)

A full-codebase code review surfaced 42 issues across security, thread safety, error handling, robustness, and test coverage. All were resolved in a single coordinated release:

**Security (5 fixes):** SSRF via `flow_id` path traversal in HA auth flow; SSE endpoint could exhaust all Waitress threads (capped at 4); unclamped volume from server could overdrive speakers at 200%+; MAC address injection into `bluetoothctl` stdin; `/api/status` leaked MACs, IPs, and player metadata without auth.

**Thread safety (6 fixes):** `_clients` list iterated without lock across ~15 API endpoints; `stop_sendspin()` bypassed SSE notification; zombie restart counter race condition; config file reads without `config_lock`; unsynchronized MA API credential writes; BT executor pool too small (2→4) for multi-device reconnect.

**Error handling & input validation (7 fixes):** `request.get_json()` crash on non-JSON POST; internal exception strings leaked in 15 error responses; IPC volume command crash on non-numeric input; path traversal via crafted `client_id`; `player_names` type confusion (string vs list); `set_log_level` accepted arbitrary `getattr` targets; `force=True` weakened CSRF protection on password endpoint.

**Test coverage (65 new tests):** from 42 to 107 tests. New test files for `services/bluetooth.py`, `services/pulse.py`, `bluetooth_manager.py`, `services/daemon_process.py`, `scripts/translate_ha_config.py`, and `routes/api.py`. Shared `conftest.py` added. `datetime.UTC` replaced with `timezone.utc` across 4 files for Python 3.9 test compatibility.

**armv7l compatibility (post-release hotfix):** PyAV 12.3.0 (the only version that compiles on armv7l) lacks `AudioLayout.nb_channels`, causing the sendspin FLAC decoder to crash with `AttributeError` — total audio silence. A monkey-patch in `services/daemon_process.py` replaces `FlacDecoder._append_frame_to_pcm` with a version using `len(frame.layout.channels)`. The patch auto-detects PyAV version at startup and is a no-op on PyAV 13+.

**Raspberry Pi & Docker UX (v2.16.2):** After the first community user tried Docker on a Raspberry Pi and hit configuration issues, we added: a pre-flight diagnostic script (`scripts/rpi-check.sh`) that checks Docker, Bluetooth, audio, UID, and architecture before `docker compose up`; an auth-free `/api/preflight` endpoint for programmatic setup verification; a structured startup diagnostics table in `entrypoint.sh` (visible in `docker logs`); a dedicated Raspberry Pi installation guide (en/ru); and fixed stale Docker docs that still listed removed `SYS_ADMIN` capability and were missing `PULSE_SERVER`/`XDG_RUNTIME_DIR` env vars.

---

## March 10, 2026 — HA OAuth & MA API authentication (v2.17.0–v2.20.0, ~45 commits)

### HA OAuth popup flow for MA addon (v2.17.3)

In addon mode, MA is on a private Docker network — unreachable from the user's browser. The bridge added an HA OAuth popup flow: the web UI opens a popup to the HA OAuth authorize endpoint, HA authenticates the user (including 2FA/TOTP), and the bridge exchanges the resulting code for an MA session token via server-side HTTP calls through HA Ingress. This eliminates the need for users to manually configure `MA_API_TOKEN`.

### Silent MA auth via Ingress (v2.17.4)

The popup flow required user interaction. In Ingress mode the HA session token is already available in `localStorage` (`hassTokens`). The bridge now reads it automatically on page load, calls `/api/ma/ha-silent-auth` which performs the full OAuth exchange server-side — zero clicks. Auto-discover also runs on page load, so the MA connection is established transparently.

### Long-lived MA API token (v2.17.7)

Investigation of persistent "authentication failed" errors in MA monitor revealed a fundamental issue: the OAuth callback returns a short-lived session JWT (30-day sliding expiry, `is_long_lived=False`), not an API token. Additionally, a regex bug captured `#/` (Vue Router hash fragment) as part of the JWT, corrupting it.

The fix: after obtaining the session JWT via OAuth, the bridge connects to MA's WebSocket API, authenticates with the session token, and calls `auth/token/create` to obtain a proper long-lived JWT (10-year expiry). The session token is never persisted.

Idempotency: before initiating OAuth, `_validate_ma_token()` checks if the existing token is still valid for the target MA URL — preventing duplicate long-lived tokens on page reload or addon restart.

### MA server discovery from sendspin connection (v2.17.9)

In addon mode with `SENDSPIN_SERVER=auto`, the MA server discovery relied on mDNS as a last resort — but a zeroconf API change (kwargs vs positional args) broke the callback. The fix: before falling back to mDNS, the bridge now extracts the MA server host from the resolved sendspin WebSocket connection (`connected_server_url`). Since sendspin already discovered the MA server via its own mDNS, the bridge reuses that resolved address for the MA API endpoint (same host, port 8095). This eliminates the need for a separate mDNS scan in most cases.

### Simplified addon discovery and semi-auto auth (v2.17.10)

The previous approach had a fundamental problem: addon mode detection depended on the MA server's `homeassistant_addon` field from its `/info` endpoint — but when discovery used the mDNS path (via `_enrich_with_server_info` instead of `validate_ma_url`), this field was missing, so addon mode was never detected and silent auth never triggered.

The fix simplified the entire flow. The bridge now reports its own `is_addon` flag (from `_detect_runtime()`) in the discover response — no dependency on MA server metadata. In addon mode, discovery tries `http://homeassistant.local:8095` first (Supervisor internal DNS — nearly instant), skipping SENDSPIN_SERVER heuristics and mDNS entirely. The fully-automatic silent auth on page load was replaced with a semi-automatic approach: the "Sign in with Home Assistant" button is shown after discover detects addon mode, and the user clicks it explicitly. In Ingress mode this performs one-click silent auth (no popup); outside Ingress it opens the OAuth popup.

### Passwordless MA auth via Ingress JSONRPC (v2.18.0)

The silent auth in v2.17.4–v2.17.12 attempted to POST to HA's `/auth/authorize` with a Bearer token to obtain an OAuth code — but HA's authorize endpoint is GET-only (it serves an HTML consent page) and returns HTTP 405. The popup fallback worked but required entering credentials.

The v2.18.0 approach bypasses HA OAuth entirely. MA's Ingress server (port 8094) auto-authenticates requests via `X-Remote-User-ID` / `X-Remote-User-Name` headers — the same mechanism HA uses internally for Ingress traffic. Since both addons use `host_network: true`, the bridge can reach MA's Ingress port at `localhost:8094`. The flow: (1) frontend sends the HA access token from `hassTokens` in localStorage; (2) backend connects to HA's WebSocket API and calls `auth/current_user` to get the user's ID and username; (3) backend POSTs a JSONRPC request to MA's Ingress endpoint (`http://localhost:8094/api`) with the user headers, calling `auth/token/create`; (4) MA auto-authenticates the Ingress request and creates a long-lived 10-year JWT. The entire flow is invisible to the user — one button click, no credentials, no popup.

### Hardening and HAOS networking fixes (v2.18.1–v2.18.3)

Three rapid-fire patches addressed real-world deployment issues discovered during HAOS verification:

**v2.18.1 — websockets compatibility.** The HAOS addon Docker image ships an older `websockets` library (<14) that doesn't accept the `proxy=None` keyword argument. A `_ws_connect()` compatibility wrapper was added that tries with `proxy=None` first, catches `TypeError`, and retries without it.

**v2.18.2 — HAOS addon networking.** In HAOS each addon runs in its own Docker container with its own network namespace — `localhost:8094` from the bridge addon does *not* reach MA's Ingress port. The fix: `_find_ma_ingress_url()` queries the HA Supervisor API (`http://supervisor/addons/{slug}/info`) to discover the MA addon's Docker hostname and Ingress port, then connects via Docker DNS (e.g. `http://d5369777-music-assistant:8094`). Known MA addon slugs (`d5369777_music_assistant`, `_beta`, `_dev`) are tried in order. The addon config gained `hassio_api: true` and `homeassistant_api: true` permissions.

**v2.18.3 — JSONRPC response format.** MA's `auth/token/create` returns the token as a raw JSON string when called via the Ingress port, not wrapped in `{"result": "..."}`. The response parser now handles both formats and logs the raw response for diagnostics.

### Configuration UI overhaul (v2.19.0)

The Configuration section had grown organically and needed restructuring. Save buttons were in the middle of the form, Music Assistant Integration was buried inside Advanced settings (two clicks deep), the BT Devices table had 9 columns with 700px horizontal scroll on mobile, and labels were verbose paragraphs.

The overhaul reorganized the form into clearly labeled sections — General, Bluetooth, Music Assistant (promoted to top level), Advanced, and Authentication — each with icon headings and visual separation. A sticky save bar now appears at the bottom when config has unsaved changes. The BT Devices table was split into a main row (Name, MAC, Adapter, Format) and an expandable detail sub-row for advanced fields (Listen Address, Port, Delay, Keep-alive) that auto-opens when non-default values exist.

### Configuration UX polish and community feedback (v2.20.0)

Community feedback on the v2.19.0 release drove a second round of polish. Users noted that the Add button in the scan/paired device list was too far from the device name, making it hard to target. The Advanced settings panel (which now contained only 4 fields) was dissolved entirely — fields were moved into their respective sections and the extra panel removed.

Key changes: the MA form now auto-collapses to a summary when connected (a "Reconfigure" link expands it); auth fields hide when disabled; BT device expand chevron was moved to the left side of the row for conventional tree-style interaction; devices start collapsed by default; scan/paired device rows became fully clickable with hover highlight; the Scan button was moved before +Add Device for a discovery-first workflow. A `_configLoading` guard was added to prevent programmatic field population from triggering the dirty-state indicator on page load.

### Code audit and internal refactoring (v2.20.3)

A comprehensive code review of the entire codebase (~10 700 lines across 35 Python files) exposed two critical issues: a dead `/api/bt/reconnect` endpoint (the function existed but lacked a `@route` decorator — no HTTP request could reach it) and a `postMessage('*')` wildcard in the HA OAuth popup callback, which violated the same-origin principle. Both were fixed immediately.

The bigger outcome was splitting the 3 178-line `routes/api.py` monolith — the single largest file in the project — into five focused modules: core volume/mute/pause routes stayed in `api.py` (581 lines); Bluetooth scan/pair/reconnect moved to `api_bt.py` (396); Music Assistant integration and OAuth flow to `api_ma.py` (1 216); config and settings to `api_config.py` (502); status, SSE streaming, and diagnostics to `api_status.py` (647). Each module registers its own Flask Blueprint; `web_interface.py` wires all five. Backward-compatible re-exports were added so existing tests and external callers continue to work without changes.

Thread-safety received targeted fixes: six places that iterated the global `_clients` list without acquiring `_clients_lock` were patched — three in `ma_monitor.py` via a new `state.get_clients_snapshot()` helper, two in config and MA routes. The `MaMonitor._msg_id` counter, previously a bare `int` incremented across threads, was replaced with `itertools.count(1)` — atomic under CPython. A duplicate MAC-address regex was consolidated into `services/bluetooth.py` as the canonical `is_valid_mac()` helper.

All 138 tests passed after the refactoring; `ruff check` stayed clean throughout.
