# Investigation 2026-10-30: ingress "connection lost" + playout warm-up

Code read at origin/v3 (1ae13b4c, 3.1.0-beta.19), worktree /mnt/data/Projects/sbb-inv.
Paths: `ui/src/...` and `src/sendspin_bridge/...`.

## Q1. "Connection to the bridge lost" never recovers behind HA ingress

### What the SPA does
- The SPA uses SSE only (`new EventSource`), no WebSocket client:
  `ui/src/composables/useEventStream.ts:50`. The WS endpoint (Origin + `csrf` query) is unused by the UI, so
  WS CSRF/Origin is not involved.
- `EventSource` gets no CSRF token or custom header. Auth is the browser's cookies plus the ingress path.
  The bridge trusts ingress by `X-Ingress-Path` plus the peer IP (`api/auth.py:98-111`), per request, never via
  its own cookie. So the bridge itself never answers 401/403 to an ingress request.
- The bridge SSE: 15 s `: heartbeat` (`api/events.py:36,223`), forced end after 1800 s (`:37,216`),
  2 KiB padding (`:213`), and a hard cap of 16 listeners (`:38,136`, answers 503).
- Banner: `components/layout/ConnectionBanner.vue:27-37`: `lost` after 4 s of `bridge.sseConnected=false`.
  `sseConnected` flips true only in `onConnect` (`stores/bridge.ts:52`) which is `source.onopen`.

### Root cause
`EventSource` reconnects by itself only for network errors or a clean end of an HTTP 200 stream. For a
non-200 answer or a wrong Content-Type (401/403/404/502/503/504), the spec fails the connection:
`readyState = CLOSED` and the browser never retries. `useEventStream` assumes it always retries:
- `onerror` (`useEventStream.ts:57-62`) just counts `failures` and only starts the polling fallback at
  `failures >= 3` (`:61`). A CLOSED source fires `onerror` exactly once, so `failures` stays 1 and polling
  is never started either.
- Nothing ever checks `source.readyState === EventSource.CLOSED` and nothing calls `connect()` again
  (only `router/index.ts:76`, once, on first navigation).
- Result: `sseConnected=false` forever, banner stays, no polling, state really is stale. A reload builds a fresh
  EventSource.

Why the server answers non-200 after idle (the trigger): Supervisor ingress validates the `ingress_session`
cookie on every proxied request (session lifetime about 1 h, extended on each validated request). The HA frontend
refreshes that cookie itself (`hassio/ingress/session`) only when it opens/reloads the panel. A long-lived SSE
stream validates once at connect; heartbeats do not extend it. After a background tab / idle gap, the next
(re)connect carries an expired cookie, the Supervisor answers 401 (or 502/504 if the add-on restarted/proxy
dropped), and the EventSource dies permanently. A page reload makes the HA frontend request a fresh session cookie,
which is why reload works. This is inferred from code plus Supervisor behaviour; HAOS add-on logs (last 3 h)
showed no event-stream errors, which is expected because a 401 from the Supervisor never reaches the bridge.
(The only bridge-side non-200 is the 503 `too_many_listeners` at 16 listeners; not seen.)
Also, a failed polling `refresh()` swallows errors (`stores/bridge.ts:95-101`) and a 401 from it would route to
the login page (`main.ts:15-18`) instead of re-auth.

Secondary: `_MAX_LIFETIME_S=1800` closes the stream every 30 min cleanly; the reconnect after that is the
first request after a possibly expired window, so it is the usual victim.

### Fix
1. `useEventStream.ts` (core fix): in `onerror`, if `source.readyState === EventSource.CLOSED`, own the
   reconnect: `close()`, start polling immediately (do not wait for 3 failures), and schedule `connect()` with
   exponential backoff (1 s, 2 s ... capped 30 s, jitter). Also reconnect on `visibilitychange` (visible) and
   `online` events, and when `!connected` for more than N s.
2. Probe before reconnect: `fetch(apiUrl('/api/v1/status'))`. On 401/403 (expired ingress session) there is
   nothing the SPA can refresh by itself (cookie owned by HA frontend, iframe is cross-origin from the HA
   page). Options, in order: (a) `window.location.reload()` once (guard with sessionStorage timestamp to avoid
   loops), same effect as the manual reload that is known to work; (b) if embedded, `parent.postMessage` is not
   supported by the HA frontend for this, so skip; (c) otherwise show the banner with a "Reload" button
   (`connection.lost` i18n + action) instead of the non-actionable text.
3. Keep the ingress session alive: while visible, a cheap authenticated request every ~10 min
   (poll `/api/v1/status` or a new `/api/v1/ping`) so the Supervisor extends the session. Cheapest: reduce
   `_MAX_LIFETIME_S` from 1800 to about 600 so the server-driven reconnect keeps extending the session
   (`api/events.py:37`). Keep the heartbeat at 15 s (below typical 60 s proxy idle).
4. Banner: add a "Reload" button after e.g. 30 s of loss (`ConnectionBanner.vue`).

### Test
- vitest for `useEventStream` with a fake EventSource: `onerror` with `readyState=CLOSED` must (a) start the
  poll, (b) call `connect()` again after backoff; after `onopen` stops polling. Red today: no reconnect.
- Store test: `sseConnected` returns true after a simulated CLOSED then successful reconnect.
- Manual: through ingress, delete/expire the `ingress_session` cookie (devtools) or `ha supervisor`
  session timeout, wait for the 1800 s end (or lower `_MAX_LIFETIME_S` locally), confirm reload-on-401 / banner
  with button and that the normal path recovers. Back-end: pytest that the SSE ends at lifetime with a clean
  close (already implied) and that `/events` still answers 200 with `X-Ingress-Path`.

## Q2. Playout "warm-up" (warm / warm_s / warmup_s = 60)

### What it is
It is a display-only timer; it changes no audio behaviour.
- `PlayoutSettings.warmup_s = 60.0` (`services/audio/playout/engine.py:118`, overridable via the `playout`
  args dict, `services/ipc/bridge_daemon.py:693-697`). `warm_hold_s`, `warm_in_group`, `warm_group_max_s`
  (`:115-117`) are declared but unused (Stage 2 "hold the output warm" not implemented;
  `gst_output.py:532` comment says so).
- Start: `_warm_from_us` is set at the first `next_block` after the output opens (`engine.py:267-268`).
  A new `PlayoutEngine` is built on every `adopt_output` (`gst_output.py:416`), and every end of stream /
  stop closes the output (`gst_output.py:531-533, 545+`). So warm-up restarts at: every new stream/track-start
  output open, seek or stop/play that reopens the output, and device reconnect/daemon restart.
- Restart inside a running engine: on a port stall or underrun (`engine.py:460-461`) and again when the port
  recovers (`:461-462`), i.e. warm-up restarts after each underrun/stall.
- Snapshot: `warm_s = now - _warm_from`, `warm = warm_s >= warmup_s` (`:737-758`).
- Effect (UI only): `ui/src/utils/deviceChips.ts:46-50` shows a pulsing neutral "warming" chip with a
  countdown and hides the error value (`errorMs: null`) until warm; `components/devices/DeviceTimingPanel.vue:102`
  shows "warming Ns". `SyncChip.vue:49,65` text. Nothing else (no suppressed corrections/jumps, no guidance,
  no API gating) reads `warm`. Controller, jumps, air sensor and corrections run normally during warm-up
  (the start placement is `_request_placement("start")`, `engine.py:466-469`; baseline collection
  `BASELINE_*` ~1-6 s, `:100-104`).

### Optimisations (top 3 first)
1. Do not hide the number: show the error with a "settling" tone instead of replacing it with a countdown, and
   hide the warming chip entirely unless the error is out of tolerance (or there is >0 jumps). Warm-up currently
   hides a value the engine already computes. (`deviceChips.ts:46`, `DeviceTimingPanel.vue:102`.)
2. Make `warm` convergence-based instead of 60 s fixed: warm when baseline established (`engine.py:667-685`),
   at least one steady controller window passed and `|error_ema|` within the chip tolerance (or air bias state
   seeded/`held` via `air_bias_state`), with 60 s only as the timeout; skip it when the air sensor already
   seeded (`air_bias.py` states `seeding` to seeded).
3. Carry warm state across a quick restart (Stage 2): honour `warm_hold_s` by keeping the output/engine alive
   between tracks (`end_stream` holds instead of `stop`, `gst_output.py:530-533`) so track changes within the
   hold do not reopen the output; at minimum preserve `_warm_from_us` across `adopt_output` when reopen is within
   a few seconds on the same sink, and do not reset it on short underruns (`engine.py:460-462`, reset only if
   the stall exceeded the baseline).
Other: do not restart warm on `recovered` (only on `stalled`); expose `warm_reason` for diagnostics only.
