# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.61.0-rc.7] - 2026-04-22

### Added
- **UI toggle for `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY`** — the flag
  added in rc.5 was only settable by hand-editing `config.json`. The
  Settings tab now exposes it as a standard experimental row (gated
  behind the "Show experimental features" master switch) with the full
  recovery-ladder description in its tooltip.

### Changed
- **Red visual treatment for experimental toggles** — rows marked
  `data-experimental` (both `.config-setting-row` in Settings and
  `.bt-scan-toggle` in the scan modal) now render with a red tinted
  background, red inset border, and an "EXPERIMENTAL" badge in the
  top-right corner. Mirrors the amber dirty-row pattern but uses red
  so unsupported/volatile toggles are distinguishable from merely
  unsaved settings; the text badge keeps the signal legible for
  colour-blind and high-contrast users.

## [2.61.0-rc.6] - 2026-04-22

### Changed
- **Explicit A2DP Sink profile request right after pair succeeds** —
  `pair_device` now issues an explicit
  `org.bluez.Device1.ConnectProfile(A2DP_SINK_UUID)` via D-Bus
  immediately after bluetoothctl reports `Pairing successful`, before
  returning to the connect loop. On BlueZ 5.86 the generic `Connect()`
  that follows can auto-negotiate the wrong profile under the dual-role
  regression (bluez/bluez#1922), leaving the device bonded but with no
  A2DP sink published. Calling ConnectProfile while the device is still
  fresh from pair narrows that window — on a healthy stack an
  `org.bluez.Error.AlreadyConnected` response from the underlying D-Bus
  call is treated as benign, so the helper is effectively a cheap no-op.
  Best-effort: if the D-Bus call fails, the pair result is still
  reported as success and `_connect_device_inner` will retry the same
  hint after its own `Connect()`.

## [2.61.0-rc.5] - 2026-04-22

### Added
- **Experimental adapter auto-recovery ladder (opt-in)** — new
  `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY` flag (default off). When the
  reconnect loop hits `BT_MAX_RECONNECT_FAILS` consecutive failures
  and the flag is on, the bridge now runs the
  [`bluetooth-auto-recovery`](https://github.com/bluetooth-devices/bluetooth-auto-recovery)
  ladder (mgmt reset → rfkill unblock → USB unbind/rebind) on the
  adapter as a last-ditch before auto-releasing BT management. If
  recovery succeeds, management stays enabled and the reconnect loop
  continues. A per-adapter 60 s cooldown prevents thrashing when
  multiple devices on the same controller hit the threshold together.
  Requires `CAP_NET_ADMIN`, `/dev/rfkill`, and `/sys/bus/usb` access
  (Docker privileged or matching capabilities) — the USB step briefly
  disconnects every device on that controller, hence opt-in.

## [2.61.0-rc.4] - 2026-04-22

### Added
- **Popular-PIN retry for legacy BT pairing** — when a BT 2.x device asks
  for a numeric PIN and rejects the bridge's default `0000` with
  `AuthenticationFailed`, the standalone pair flow
  (`POST /api/bt/pair_new`) now re-runs with the next popular PIN
  (`0000, 1234, 1111, 8888, 1212, 9999`) before giving up. Non-PIN
  failures (connection errors, timeouts) still stop the loop
  immediately — retrying against an unreachable device wasted ~20s per
  attempt. The list is intentionally short: each extra attempt adds a
  BlueZ auth-fail timeout to total pair time.

### Changed
- **Clearer pairing-failure logs** — both the scan-modal pair flow and
  the long-running reconnect pair flow now annotate the failure log
  with the rejected PIN when the device auto-prompted for one and
  `AuthenticationFailed` was seen (`… — device rejected PIN 0000`). A
  new `describe_pair_failure()` helper centralises the rule so
  operators see the root cause without grepping for
  `AuthenticationFailed`. Non-auth failures are logged verbatim as
  before.
- **Scan narrowed to BR/EDR during pairing** — `bluetoothctl scan on`
  replaced with `scan bredr` at all five pair/scan sites (reset &
  reconnect, standalone pair, background BT scan, runtime pair-device
  loop). Excluding LE-only advertisers keeps the scan window
  responsive on adapters shared with BLE traffic and avoids
  interleaved BR/EDR discovery delays seen on BlueZ 5.85
  (bluez/bluez#826). Safe on bluetoothctl ≥ 5.65.

### Fixed
- **Stale BlueZ device cache cleared on remove** — after
  `bluetoothctl remove`, `bt_remove_device` now also deletes
  `/var/lib/bluetooth/<adapter>/cache/<device>` when an adapter MAC is
  known. BlueZ leaves stale `ServiceRecords` / `Endpoints` entries in
  that file, which on re-pair surface as
  `org.bluez.Error.Failed — Protocol not available` on A2DP sinks
  (bluez/bluez#191, #348, #698). Silent if the file is absent; cleanup
  only runs when the adapter is known to avoid walking the BlueZ tree
  blindly.

## [2.61.0-rc.3] - 2026-04-22

UI follow-up to the `2.61.0-rc.1` experimental flags. No Bluetooth
pairing behaviour changes.

### Added
- **Scan-modal toggle for the NoInputNoOutput pair agent** — the
  `EXPERIMENTAL_PAIR_JUST_WORKS` config flag shipped in rc.1 with full
  config/schema/diff support, but the UI had no control for it, so
  users had to hand-edit `config.json` or `options.json` to try
  Just-Works SSP pairing. A new "NoInputNoOutput pair agent
  (experimental)" switch now appears in the scan-modal toolbar next to
  "Pause other speakers on same adapter", guarded by "Show experimental
  features". Because registering the BlueZ agent is a per-pair runtime
  decision (not a persisted setting), it lives with scan/pair context
  rather than under Settings and takes effect on the next pair attempt
  only. The toggle is only included in the `pair_new` POST body when
  the user explicitly ticks it — an unchecked toggle falls through to
  the persisted `EXPERIMENTAL_PAIR_JUST_WORKS` config key, which
  remains a usable fallback for hand-edited `config.json` /
  `options.json`.
- **`no_input_no_output_agent` per-request override in
  `POST /api/bt/pair_new`** — the scan-modal toggle sends this field on
  the pair request; when present, it wins over the persisted
  `EXPERIMENTAL_PAIR_JUST_WORKS` config key. The server accepts only
  JSON booleans here — non-bool payloads (e.g. the string `"false"`)
  are ignored rather than being coerced via `bool()`, so they fall
  through to the config key instead of silently forcing
  NoInputNoOutput.

### Tests
- `tests/test_ui_experimental_toggles.py` — regression coverage for the
  Settings-page experimental toggles (A2DP sink-recovery dance, PA
  module reload) **and** the scan-modal NoInputNoOutput pair-agent
  toggle: asserts template checkboxes exist under the right
  `data-experimental` container, asserts the Settings toggles are
  wired into `buildConfig` and populate-on-load, and asserts the
  scan-modal toggle is passed as `no_input_no_output_agent` in the
  `pair_new` request body only when the checkbox is ticked (i.e. not
  baked into the body literal unconditionally) and is never persisted
  via `buildConfig`. Would have caught the rc.1 omission immediately.
- `tests/test_api_endpoints.py` — five new tests covering the
  per-request override precedence (override beats config both ways),
  endpoint forwarding of the new body field, `None`-fallback when the
  field is omitted, and strict bool validation (non-bool payloads do
  not get coerced).

## [2.61.0-rc.2] - 2026-04-22

Build-hygiene follow-up to `2.61.0-rc.1`. No runtime behaviour changes.

### Changed
- **Docker build context trimmed** — `.dockerignore` now excludes the
  `ui/` dev UI source (215 MB of `node_modules`), `sendspin-cli/`,
  `rnd/`, every `__pycache__/`, `*.pyc`/`*.pyo`, the usual linter/test
  caches, and the dev-screenshot PNG families that weren't already
  covered (`stats-*`, `ru-*`, `ghpages-*`, `social-*`, `landing-*`,
  `config-*`, `mobile-nav-*`). Fresh CI runners no longer pay to ship
  the UI dev tree into the builder.
- **Image payload narrowed** — `Dockerfile` replaces the blanket
  `COPY scripts/ scripts/` with an explicit list of the three scripts
  that actually run inside the container: `translate_ha_config.py`
  (called by `entrypoint.sh` in HA addon mode) and
  `check_sendspin_compat.py` / `check_container_runtime.py` (invoked
  by `release.yml` post-build smoke tests). Eight dev-only scripts
  (`rpi-*.sh`, `proxmox-vm-*.sh`, `generate_ha_addon_variants.py`,
  `release_notes.py`, `translate_landing.py`) are no longer packaged.

### Fixed
- **`__pycache__` no longer leaks into the image** — `/app/routes/`,
  `/app/services/`, and `/app/scripts/` previously shipped stale
  bytecode from the developer's local interpreter runs. Addressed via
  the `.dockerignore` additions above.

## [2.61.0-rc.1] - 2026-04-22

Opt-in experimental sink-recovery flags, connect-path hardening, and
reliability improvements for the standalone-pair flow surfaced by the
Synergy 65 S `AuthenticationCanceled` report (issue #168). Supersedes
the 2.60.5-rc line.

### Added
- **`EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE`** — opt-in flag gating the
  disconnect→2 s wait→reconnect dance in `BluetoothManager` when no sink
  appears after a successful connect. Previously unconditional; the dance
  helps on some headless PipeWire/BlueZ 5.86 setups but hurts others (see
  forum #78, related to #174), so it's now opt-in.
- **`EXPERIMENTAL_PA_MODULE_RELOAD`** — opt-in flag gating the last-resort
  `pactl unload-module / load-module module-bluez5-discover` escalation
  when `bluez_card.*` fails to register. Disruptive (drops every other
  active BT sink), globally throttled to once per 60 s across the bridge,
  and now serialized so two concurrent callers can never run the reload
  back-to-back.
- **`EXPERIMENTAL_PAIR_JUST_WORKS`** — opt-in flag (issue #168) that
  registers bluetoothctl's agent as `NoInputNoOutput` so Secure Simple
  Pairing runs Just-Works (no passkey exchange). Workaround for audio
  sinks that cancel authentication under the default `KeyboardDisplay`
  agent. Read via `load_config()` on every pair attempt — no restart.
- **Post-pair audio-profile sanity check** — if a freshly paired device
  advertises no audio UUIDs (`A2DP`, `HFP`, `Headset`), the bridge now
  surfaces `last_error = "no_audio_profiles_advertised"` on device
  status so the UI can show a targeted banner instead of a generic
  sink-not-found error. Backed by new `bt_dbus._dbus_get_device_uuids`
  and `AUDIO_SINK_UUIDS` constant.
- **Scan-filter drop reasons** — `_classify_audio_capability` in
  `routes/api_bt.py` now returns a machine-readable `reason` label
  (`audio_class_of_device` / `non_audio_class_of_device` / `audio_uuid`
  / `no_audio_class_no_uuid` / `no_class_info_defaults_audio`). Scan
  telemetry aggregates the drop reasons so support can answer "why
  doesn't my speaker show up" without guessing.
- **`services.pulse.cycle_card_profile` / `acycle_card_profile`** —
  helper that cycles `bluez_card.*` `off → a2dp_sink` to force PA to
  re-publish a missing sink without kicking other active BT streams.
  Milder than the module reload, no flag needed.

### Fixed
- **#168 — standalone pair unreliable on slow SSP speakers** — three
  improvements to `_run_standalone_pair_inner`:
  - **Event-driven pair trigger**: `pair <mac>` fires as soon as
    `[NEW] Device <mac>` shows up on scan (typical 1–3 s), replacing
    the fixed 12 s sleep so the peer is still accepting when `pair`
    lands. Falls back to the hard cap if the device never advertises.
  - **Full stdout on FAIL** in debug log (was `out[-800:]`, which
    routinely cut off the passkey/agent prompt needed to diagnose).
  - Optional Just-Works SSP agent (see Added).
- **`_dbus_wait_services_resolved` pre-audio gate** — polls BlueZ
  `Device1.ServicesResolved` (≤10 s) after `Connect()` returns, so
  downstream profile/sink work doesn't race an uninitialized Device1.
  Tri-state return (`True` / `False` / `None`): `None` means "could
  not check" (dbus-python missing or no device path) and the caller
  skips the misleading "did not reach True within 10s" warning.
- **`areload_bluez5_discover_module` — asyncio.CancelledError**
  propagation: the helper now catches `OSError` only, so task
  cancellation unwinds cleanly on shutdown/restart (previously
  suppressed alongside OSError).
- **`areload_bluez5_discover_module` — cooldown burn on failure**:
  `_LAST_BLUEZ5_RELOAD_TS` is now written only after a full
  successful `unload-module` + `load-module`. Trivial failures
  (pactl unavailable, non-zero rc, module not loaded) no longer
  block a later healthy attempt.
- **`areload_bluez5_discover_module` — concurrent caller race**:
  added `_BLUEZ5_RELOAD_IN_PROGRESS` flag under the existing
  `threading.Lock` + `try/finally` so two concurrent callers can't
  both pass the cooldown check and run the reload back-to-back.
- **`_dbus_wait_services_resolved` wait_with_cancel contract**
  (03c4d8a0): the helper now treats `wait_with_cancel` returning
  `True` as "waited uninterrupted" and keeps polling, matching
  `BluetoothManager._wait_with_cancel`'s convention. Previously
  the contract was inverted and the helper exited after the first
  non-True property read.

### Changed
- `_cycle_card_profile_for_mac` docstring now states True only when
  the full off → `a2dp_sink` cycle (including the final switch)
  completes successfully.

### Tests
965+ → 1452 passing. New coverage: scan-filter reasons
(`test_api_bt_scan_filter.py`), event-driven pair + Just-Works agent
+ full-stdout-on-fail, tri-state dbus wait, cooldown-on-success-only,
concurrent reload serialization, cancellation propagation.

## [2.60.5-rc.1] - 2026-04-21

Small UX follow-up to v2.60.3: the opt-in pair-time adapter quiesce checkbox is
now hidden by default behind the experimental-features toggle, since the feature
only helps a narrow corner case (single-adapter + BlueZ 5.78–5.86 regression).

### Changed
- **Pair-time adapter quiesce is now gated behind "Show experimental features"** —
  the "Pause other speakers on same adapter" checkbox in the Bluetooth scan modal
  is hidden by default and only appears when the experimental-features toggle in
  General settings is enabled. No change to the underlying pair flow or API — the
  `quiesce_adapter` flag on `POST /api/bt/pair_new` and `/api/bt/pair` continues
  to work unchanged.

## [2.59.0-rc.2] - 2026-04-17

Second review round on top of rc.1. Feedback from Copilot on PR #158:

### Fixed
- **`services/subprocess_stderr.py`** — `_PORT_NUMBER_RE` widened to `\d{1,5}`
  with an explicit `1..65535` range check so low-range ports (80, 443, …)
  appear in the `lsof -i :<port>` hint and out-of-range numbers fall back to
  the generic hint.
- **`sendspin_client.py`** — `DEFAULT_MAX_ATTEMPTS` imported from
  `services.port_bind_probe` and used for both the probe call and the error
  hint range so tuning the constant in one place keeps them in sync.

## [2.59.0-rc.1] - 2026-04-17

Operational-resilience and security-hardening rollup for issues surfaced from
Raspberry Pi 4 / pipewire-pulse reports (#156, #157) plus the CSP nonce-only
migration tracked as a follow-up from 2.58.0.

### Security
- **CSP `script-src` is nonce-only** — `'unsafe-inline'` removed from the
  `Content-Security-Policy` header. Every inline `on*=` event handler in Jinja
  templates *and* HTML strings produced by `static/app.js` migrated to a
  delegated dispatcher keyed on `data-action` / `data-arg`. `<details>` toggle
  events are handled on the capture phase (non-bubbling, but captures traverse)
  to cover dynamically inserted DOM. New regression test scans shipped
  templates and `app.js` so future PRs can't reintroduce inline handlers.

### Added
- **`services/port_bind_probe.py`** — `is_port_available()` +
  `find_available_bind_port()` host-side TCP bind probe (SO_REUSEADDR, no
  SO_REUSEPORT to avoid false positives). `DEFAULT_MAX_ATTEMPTS=10`.
- **Port auto-shift on EADDRINUSE** — `SendspinClient._start_sendspin_inner`
  preflights the listen port before spawning the daemon subprocess; on
  collision it auto-shifts up to `DEFAULT_MAX_ATTEMPTS` ports and records
  `port_collision: True` + `active_listen_port` on device status. After
  `_MAX_BIND_FAILURES=5` consecutive bind failures the restart loop halts
  (with an `lsof -i :<port>` hint) instead of spinning. Halt state auto-clears
  once the daemon is observed alive.
- **Preflight port-collision warning** at orchestrator startup
  (`bridge_orchestrator.py`).
- **EADDRINUSE stderr classifier** — `services/subprocess_stderr.py` detects
  `errno 98` / `address already in use` / `eaddrinuse` markers and extracts
  the port (1–65535) so the surfaced hint names the actual port.

### Fixed
- **#156 — SinkMonitor log flood**: `services/sink_monitor.py` now diagnoses
  the PA connection failure (`socket-missing` / `permission-denied` /
  `server-not-listening` / `protocol-error` / `unknown`) with an actionable
  hint on the first WARNING, demotes subsequent attempts to DEBUG, and
  self-disables after 3 consecutive initial failures so callers fall back to
  daemon-flag idle detection. Post-success transients use exponential backoff
  5→10→20→40→60s (mirrors `MaMonitor`). `start()` resets state so the monitor
  can be revived after the operator fixes PA.
- **#157 — daemon crash on port collision**: see "Port auto-shift" above.

### Notes
- `find_available_bind_port()` is called with `host="0.0.0.0"` (wildcard) to
  match the daemon's actual bind behaviour — the subprocess receives only
  `listen_port` (no `listen_host`), so probing a specific interface would miss
  collisions on other interfaces.

## [2.58.0-rc.5] - 2026-04-17

### Fixed
- **"Add & Pair" now remembers the adapter the scan used** — after a successful post-scan pair, the new fleet row was rendered with `adapter = default` instead of the controller the pairing actually ran against, so the next restart re-pointed the bond at whichever radio BlueZ happened to consider default. Two layered bugs: (a) the frontend `btAdapterOptions` compared the scan-supplied controller MAC against `a.id` (always `hciN`) and never matched, leaving the `<select>` on "default"; (b) the backend `_run_standalone_pair` passed the raw adapter (`hci0`/`hci1` from the scan result) straight to `bluetoothctl select`, which HAOS and LXC reject with `Controller hci1 not available`, so the pair itself silently ran on the default radio. The dropdown now matches against both `a.id` and `a.mac`, and the pair backend resolves `hciN` → MAC via `_resolve_adapter_to_mac` before any `select` — matching the reset/reconnect fix from rc.4
- **BT Info modal shows only the MAC for devices on the non-default controller** — `/api/bt/info` ran `bluetoothctl info <mac>` with no `select`, so on HAOS / LXC with two adapters (`hci0` + `hci1`) the query went to the BlueZ default. Bonds living on the non-default radio (Yandex mini 2 on `hci1` in prod) returned `Device … not available`, so every field except the MAC was empty in the modal. The helper now accepts an adapter (resolving `hciN` → controller MAC), and both UI call sites forward it — the fleet row reads `.bt-adapter`, the "Already paired" list passes `d.adapters[0]`. When the caller can't supply an adapter, the helper probes every controller in turn and returns the first response with real device fields, so legacy call sites still work

## [2.58.0-rc.4] - 2026-04-17

### Fixed
- **Reset & Reconnect now honours the adapter the device is bonded with** — the `/api/bt/reset_reconnect` backend has always threaded `select <adapter>` through the `remove`, power-cycle, and `pair`/`trust`/`connect` bluetoothctl sessions, but both UI call sites (the configured-fleet row and the "Already paired" list) were calling `resetAndReconnect` without an adapter. On hosts with more than one controller (e.g. `hci0`+`hci1` on the production HAOS VM) the full reset sequence therefore ran against the BlueZ default controller, so bonds living on a non-default radio could never be rebuilt through the UI. The fleet row now reads the adapter from its `<select>`; the paired list passes `d.adapters[0]`. The backend also resolves `hciN` → controller MAC before any `bluetoothctl select`, because HAOS and LXC reject `select hci1` with `Controller hci1 not available` — only the MAC is accepted there
- **"Already paired" list no longer lists ghost devices** — interactive `bluetoothctl` interleaves async discovery notifications (`[CHG] Device <mac> RSSI: …`, `[NEW]/[DEL] Device …`, `[CHG] Device <mac> ManufacturerData.*`) into the same stdout we pipe `devices Paired` through, so the parser was picking up every nearby BLE beacon and showing it as "paired" even when `bluetoothctl info` reported `Paired: no`. `_parse_paired_stdout` now strips the bluetoothctl prompt echo and accepts only bare `Device <mac> <name>` lines; anything behind a `[CHG]`/`[NEW]`/`[DEL]` bracket is treated as noise

## [2.58.0-rc.3] - 2026-04-17

### Added
- **Targeted "enable-linger" hint for headless PipeWire** — preflight audio probe now distinguishes "socket path not mounted" from "socket mounted but server refused the connection". The latter (classic headless Docker/LXC where the user-session PipeWire stopped once SSH disconnected) surfaces a dedicated operator-guidance issue **"Audio server unreachable — enable user lingering"** with the exact fix (`sudo loginctl enable-linger <user>` + reboot) and a link to the docs. The linger hint is gated by `is_ha_addon_runtime()` so HA add-on users — where Supervisor owns audio — still see the generic guidance (fixes #151)

### Fixed
- **Preflight audio reachability is now measured by a real probe** — the previous implementation relied on `services.pulse.get_server_name()` raising on connect failure, but that helper swallows connect errors and returns `"not available"`, so the `system="unreachable"` signal never fired in production. The preflight now performs an explicit `AF_UNIX` connect to the `PULSE_SERVER` socket: `ConnectionRefusedError` → `unreachable` (routes to the linger-specific guidance), `PermissionError`/other `OSError` → generic audio failure with the real error text, so the linger remediation is only offered when it actually applies

## [2.58.0-rc.2] - 2026-04-17

### Added
- **Multi-adapter paired-device management** — `/api/bt/paired` now enumerates every known adapter via `list_bt_adapters()` and queries each with `select <mac>\ndevices Paired`, merging results so each device carries `adapters: [<mac>, ...]`. Previously bonds on a non-default controller were invisible in the UI
- **Per-adapter unpair from the UI** — `/api/bt/remove` accepts optional `adapter_mac` (validated) and, when absent, iterates every adapter so bonds on secondary controllers can finally be removed. The "Already paired" list renders an `hciN`/MAC badge per device so it's clear which controller owns each bond

## [2.58.0-rc.1] - 2026-04-16

### Security
- **SSRF guard on MA auth routes** — `/api/ma/login`, `/api/ma/ha-auth-page`, `/api/ma/ha-silent-auth`, and `/api/ma/ha-login` now validate every user-supplied `ma_url`/`ha_url` through the new `services.url_safety.is_safe_external_url`, which resolves the host via DNS and rejects link-local (cloud metadata / APIPA), reserved, multicast, unspecified addresses, and non-`http(s)` schemes. Loopback and RFC1918 are allowed by default because the bridge is intended to run on home LANs and HAOS — set `SENDSPIN_STRICT_SSRF=1` to also block them (recommended when the bridge is exposed on an untrusted network). In HA addon mode the Supervisor proxy network (`172.30.32.0/23`) and the internal `supervisor`/`hassio`/`homeassistant` hostnames remain allowlisted even in strict mode
- **DNS-rebinding defence** — outbound HTTP from MA auth code now goes through `services.url_safety.safe_urlopen` / `safe_build_opener`, which use `SafeHTTPConnection`/`SafeHTTPSConnection` subclasses that re-check `socket.getpeername()` after the socket connects. Rebinders that return a public IP at validate-time and a link-local/metadata IP at connect-time are rejected before any bytes are sent
- **XSS hardening on `/api/ma/ha-auth-page`** — `ma_url` is injected into an inline `<script>` block; `json.dumps` alone does not escape `</script>`, so a payload containing `</script><script>alert(1)</script>` could have broken out. The server now post-processes the JSON literal with `.replace("</", "<\\/")` before injection
- **MA-reported `ha_url` is re-validated** — `_get_ma_oauth_bootstrap` used to trust the `ha_base` host parsed out of the Music Assistant server's `authorization_url`; a compromised MA could have redirected the browser-less server-side exchange at an internal HA. The parsed `ha_base` now goes through `is_safe_external_url` before any further use
- **Session-bound MFA state** — the second step of `/api/ma/ha-login` (OAuth MFA) no longer trusts `ha_url`, `client_id`, `flow_id`, or `state` from the request body; the server-side `session["_ha_oauth"]` entry stored at step `init` is the only source of truth and is cleared once the flow completes or aborts
- **Supervisor fallback is now opt-in** — when HA Core's `login_flow` is unreachable, the bridge no longer silently falls back to `/auth/login` against the Supervisor API (which does not verify MFA). The fallback must be enabled explicitly with `ALLOW_SUPERVISOR_FALLBACK=1`; when enabled, each use is logged at `WARNING` with "does NOT verify MFA"
- **Logout hardened** — `POST /logout` now requires a valid CSRF token and performs a full `session.clear()` (only `_lockout_client_id` is preserved so brute-force buckets survive). `GET /logout` returns 405 with a small HTML page linking to `/login` so bookmarks and CSRF GETs cannot drop sessions
- **X-Forwarded-For hardening** — rate-limit client identification now picks the rightmost hop that is *not* in `_get_trusted_proxies()`, instead of the spoofable leftmost hop
- **X-Frame-Options: SAMEORIGIN** in standalone (non-HA-addon) mode; HA addon mode still omits it because Ingress needs to frame the UI (CSP `frame-ancestors 'self'` covers that case)

### Fixed
- **500 handler no longer redirects** — `web_interface._handle_500` returns a plain-text `Internal Server Error` response instead of `redirect("/")`, eliminating a potential redirect loop when `/` is itself failing
- **Subprocess stdout stall protection** — `SendspinClient._read_subprocess_output` now wraps `stdout.readline()` in `asyncio.wait_for(timeout=120)`, so a silent-but-alive daemon no longer leaves the reader task blocked forever. Timeouts log at DEBUG and keep polling; a dead subprocess (`returncode != None`) exits the loop cleanly

### Known issues
- CSP still ships with `'unsafe-inline'` because several templates use inline `onclick` handlers. The nonce plumbing is already in place; full migration to `addEventListener` is tracked for a follow-up minor release

## [2.57.0-rc.4] - 2026-04-16

### Changed
- **numpy upgraded to 2.x (no upper pin)** — dropped the previous `numpy<2.0` compatibility cap. sendspin 7 only requires `numpy>=1.26`, but pip now resolves numpy 2.x and a hard compatibility pin would have required a constraint file with `[extras]`, which pip rejects
- **amd64 CPU baseline raised to X86_V2** — numpy 2.x wheels are built with the X86_V2 baseline (SSE3 / SSSE3 / SSE4.1 / SSE4.2). Hosts without these extensions (e.g. QEMU VMs using `cpu: qemu64` or `kvm64`) now fail at startup with `RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support: (X86_V2)`. Fix by switching the VM CPU type to `host` (Proxmox: `qm set <vmid> --cpu host`) or any modern named model (e.g. `Haswell`, `Skylake-Client`)
- **armv7 may build numpy from source** — piwheels has no cp312 wheels for numpy 2.x, so the armv7 image will compile it under QEMU. Builds take significantly longer; subsequent releases reuse the cached layer

## [2.57.0-rc.2] - 2026-04-16

### Fixed
- **Album artwork not rendering under HA Ingress** — daemon-reported `artwork_url` points directly at the MA server and fails the same-origin check under `https://ha.example/<slug>_sendspin_bt_bridge_rc/`. UI now runs `artwork_url` through `_getSafeArtworkUrl()` first and falls back to the same-origin signed MA proxy URL (`/api/ma/artwork?...&sig=...`) when a device has MA context
- **Migration warning log spam every 15 s** — HA Supervisor rewrites `/data/config.json` from `options.json` on each restart/poll, so devices with pre-existing negative `static_delay_ms` triggered the "clamping to 0" warning on every `load_config()`. Warnings are now deduplicated per MAC per process, and `scripts/translate_ha_config.py` clamps negatives at the options.json → config.json translation step so the underlying value is fixed at source

## [2.57.0-rc.1] - 2026-04-16

### Changed
- **Upgrade sendspin 5.9.0 → 7.0.0 and aiosendspin 4.4.0 → 5.1.0** — gains DAC-anchored sync (#226), remote per-player delay (#185), multi-server daemon support, and several playback bugfixes
- **`static_delay_ms` now accepts only 0–5000 ms** — negative values are no longer valid. DAC-anchored sync in sendspin 7.0 automatically compensates for audio hardware latency, making the old large negative offsets (−300…−600 ms) unnecessary. Existing negative values are migrated to `0` on first load. Users may fine-tune with small positive values (e.g. 50 ms) if needed
- Default `SENDSPIN_STATIC_DELAY_MS` environment variable changed from `-300` to `0`
- Config schema version bumped to 2 (auto-migrated from v1)

### Fixed
- **Dependency conflict blocking sendspin 7.0.0** — `aiosendspin` updated from 4.4.0 to 5.1.0 (`[server]` extra) to satisfy sendspin 7's `aiosendspin~=5.1` requirement

## [2.56.1-rc.1] - 2026-04-13

### Fixed
- **Sourceplugin metadata mixing MA data from wrong track** — when daemon provides track title but not artist/album/artwork (typical for sourceplugin/ynison), the UI was falling back to MA now-playing for those fields, showing metadata from a completely different song. Now suppresses MA fallback for artist, album, and artwork when daemon already has a track title, preventing cross-track metadata mixing

## [2.56.0-rc.3] - 2026-04-13

### Fixed
- **HA addon 502 on ingress** — `INGRESS_PORT` is not an env var; Supervisor communicates the dynamic port via its REST API. Replaced env var lookup with Supervisor API query (`/addons/self/info`) to read the assigned `ingress_port`

## [2.56.0-rc.2] - 2026-04-13

### Fixed
- **Incorrect track metadata with sourceplugin providers** — when playing via sourceplugin (e.g. Yandex ynison), MA now-playing returned metadata from its own queue item instead of the actual playing track. Changed metadata priority in `_getDeviceNowPlayingState()` and `_getListTrackAlbum()` to daemon-first with MA fallback, matching the existing correct behavior in list view. Affects track title, artist, album, and artwork in all expanded/card views

## [2.56.0-rc.1] - 2026-04-13

### Fixed
- **HA addon ingress port conflict with Matter/Thread** (#138) — switched all addon channels from hardcoded `ingress_port` (8080/8081/8082) to dynamic `ingress_port: 0`. HA Supervisor now auto-assigns a free port, eliminating conflicts with other host-network addons. Channel defaults retained as fallback for older Supervisor versions

## [2.55.2-rc.1] - 2026-04-07

### Fixed
- **Connection errors not surfaced in UI** (#134) — `ClientConnectorError` from daemon subprocess was logged as WARNING but never shown in device status. Added `_connection_watchdog()` in BridgeDaemon (sets `last_error` after 30 s) and consecutive error counter in `SubprocessStderrService` (surfaces after 3+ repeated failures)
- **Generic "lost bridge transport" guidance for port mismatch** (#134) — when transport is down due to connection errors, recovery assistant now shows specific `sendspin_port_unreachable` issue with guidance to check `SENDSPIN_PORT`, instead of generic "restart" advice
- **Stale metadata reconnect timeout too short** (#134) — increased `_STALE_RECONNECT_READY_TIMEOUT` from 30 s to 120 s; added retrigger task that fires reconnect once daemon eventually connects, preventing permanent volume control loss

### Added
- **Sendspin port auto-probe** (#134) — when `SENDSPIN_PORT` is default (9000) and the configured host is explicit, the bridge now TCP-probes candidate ports (9000, 8927, 8095) before connecting. If an alternative port responds, it is used automatically with a WARNING log

## [2.55.0-rc.12] - 2026-04-06

### Changed
- **Unified branding** — all logos, favicons, and addon assets replaced with the landing page wave-bridge design (two pillars + three wave curves); color differentiation preserved across channels; total asset size reduced from ~310 KB to ~55 KB

## [2.55.0-rc.11] - 2026-04-06

### Changed
- **HA addon icons redesigned** — replaced bridge+equalizer icon with landing page logo (two pillars with three wave curves); color differentiation preserved: stable=teal-purple, rc=gold, beta=red; total icon size reduced from 316 KB to 80 KB

## [2.55.0-rc.10] - 2026-04-06

### Fixed
- **Subprocess crash on PipeWire** — keep `libasound2-plugins` (ALSA→PulseAudio bridge) which provides `libasound_module_pcm_pulse.so` required by sounddevice/PortAudio to discover audio sinks; removing it caused "No audio output device found" crash loop

## [2.55.0-rc.9] - 2026-04-06

### Changed
- **Docker image −51%** (916 → ~450 MB) — force-remove transitive FFmpeg/GStreamer/codec deps pulled by PulseAudio on amd64/arm64 (pactl works without them); strip debug symbols from native .so files; remove unused Python stdlib modules (ensurepip, idlelib, lib2to3, pydoc_data, turtledemo, test)

## [2.55.0-rc.7] - 2026-04-06

### Changed
- **Docker image size −37%** (916 → ~580 MB) — removed redundant system FFmpeg libraries on amd64/arm64; PyAV wheels bundle their own FFmpeg in `av.libs/`. System FFmpeg retained for armv7 only (compiled from source)
- **pip package cleanup** — strip `__pycache__`, numpy test suite, pygments, pip from runtime image

## [2.55.0-rc.6] - 2026-04-06

### Fixed
- **NumPy crash on older CPUs** — reverted numpy constraint from `<3.0` back to `<2.0`; numpy 2.x requires X86_V2 baseline (POPCNT/SSE4.2) which is unavailable on QEMU `qemu64` and older physical CPUs, causing `RuntimeError: NumPy was built with baseline optimizations (X86_V2)` in daemon subprocess

### Changed
- **Dependency updates** — `dbus-fast` 4.0.0→4.0.4 (D-Bus performance improvements), `ruff` 0.11.13→0.15.8 (linter update)
- **CI updates** — `docker/build-push-action` v6→v7 (Node 24), `actions/download-artifact` v4→v8 (hash enforcement), `actions/upload-pages-artifact` v3→v4

## [2.55.0-rc.5] - 2026-04-06

### Changed
- **Dependency updates** — `dbus-fast` 4.0.0→4.0.4 (D-Bus performance improvements), `numpy` <2.0→<3.0 (widen compatibility), `ruff` 0.11.13→0.15.8 (linter update)
- **CI updates** — `docker/build-push-action` v6→v7 (Node 24), `actions/download-artifact` v4→v8 (hash enforcement), `actions/upload-pages-artifact` v3→v4

## [2.55.0-rc.4] - 2026-04-06

### Fixed
- **Config download 404 in HA addon ingress mode** — hardcoded `/api/config/download` path in the download button bypassed the ingress `SCRIPT_NAME` prefix; now uses `API_BASE` like all other download endpoints

### Improved
- **Auto-expand device detail row on CTA navigation** — clicking a "Configure" link from onboarding or guidance now auto-expands the device detail row before highlighting it

## [2.55.0-rc.3] - 2026-04-06

### Changed
- **Power save delay in minutes** — `power_save_delay_seconds` renamed to `power_save_delay_minutes` across config, UI, API, and HA addon schemas. Default: 1 min (was 30 s), max: 60 min. Auto-migration converts existing seconds values to minutes on startup.

## [2.55.0-rc.2] - 2026-04-06

### Fixed
- **Idle mode dropdown unstyled** — added `.bt-detail-row select` CSS rules matching existing input styling (base, focus, disabled, mobile breakpoints)

## [2.55.0-rc.1] - 2026-04-07

### Added
- **Per-device idle mode** — new `idle_mode` enum per Bluetooth device replaces the two independent `keepalive_interval` / `idle_disconnect_minutes` settings. Four modes:
  - `default` — no action when idle; speaker's own hardware timer decides
  - `power_save` — suspend PA sink after configurable delay (`power_save_delay_minutes`, 0-60, default 1); releases A2DP transport so speaker can sleep while BT stays connected; auto-resumes on next play
  - `auto_disconnect` — full BT disconnect + daemon→null-sink after `idle_disconnect_minutes` (existing standby behavior)
  - `keep_alive` — stream periodic infrasound bursts at configurable interval (existing keepalive)
- **Infrasound keepalive** — keepalive bursts now use a 2 Hz sine wave at -50 dB instead of pure digital silence. Below human hearing threshold but non-zero PCM data keeps A2DP transport active on speakers that ignore digital silence.
- **PA sink suspend/resume** — new `asuspend_sink()` / `suspend_sink()` helpers in `services/pulse.py` for the power_save mode, with pulsectl + pactl fallback.
- **Status API** — `idle_mode` and `bt_power_save` fields are now exposed in `/api/status` per-device responses.

### Changed
- **Legacy UI** — device detail row now shows a single "Idle mode" dropdown instead of two separate numeric inputs; mode-specific fields (delay, standby minutes, keepalive interval) are shown/hidden based on selected mode.
- **HA addon schemas** — `idle_mode` and `power_save_delay_minutes` options added to all three addon configs (stable, beta, rc).
- **Config migration** — old configs with `keepalive_interval > 0` auto-migrate to `idle_mode: keep_alive`; `idle_disconnect_minutes > 0` to `auto_disconnect`; both zero to `default`. Explicit `idle_mode` values are never overwritten.

## [2.54.0-rc.6] - 2026-04-04

### Fixed
- **Onboarding regresses during standby** — devices in idle-standby are now treated as "logically connected" so onboarding checks and checkpoints don't show incomplete state when the bridge intentionally disconnected BT to save power
- **Idle timer not re-armed after wake** — SinkMonitor fires `on_idle` while `bt_standby` is still True during wake flow; now re-checks sink state after clearing standby and re-arms the idle timer

## [2.54.0-rc.5] - 2026-04-04

### Fixed
- **Idle timer not re-armed after wake** — SinkMonitor fires `on_idle` while `bt_standby` is still True during wake flow; `_on_sink_idle()` returns early and the timer never restarts. Now re-checks sink state after clearing standby and re-arms the idle timer.

## [2.54.0-rc.4] - 2026-04-03

### Fixed
- **Docker update command** — modal now shows `docker compose pull && docker compose up -d` instead of just `docker pull` which didn't recreate the running container

## [2.54.0-rc.3] - 2026-04-03

### Fixed
- **Idle standby broken** — pulsectl's `EnumValue` supports `== 'suspended'` but not `int()` or `== 2`; sink state always classified as "unknown", preventing idle timer from starting. Now uses string equality with int fallback.

## [2.54.0-rc.2] - 2026-04-03

### Fixed
- **Logs endpoint empty in Docker** — `from sendspin_client import _ring_log_handler` created a second module instance with empty buffer (main runs as `__main__`); now reads via `sys.modules['__main__']`

## [2.54.0-rc.1] - 2026-04-03

### Fixed
- **Restart banner stuck** — `sawRuntimeRestart` now set on successful `/api/restart` response; poll delayed past 500 ms kill window; 60 s safety timeout auto-clears banner
- **Restart fails under S6 overlay** — `PermissionError` when UID 1000 can't signal root PID 1; falls back to `os.kill(os.getpid())` so S6 supervise restarts the child
- **Update check fails on OpenSSL 3.5** — post-quantum ML-KEM key exchange produces oversized TLS Client Hello (1569 B) that middleboxes drop; GitHub API calls now pin `prime256v1` ECDH curve

### Removed
- **`handoff_mode` device option** — unused since v2.53; removed from config schema, migration, orchestrator, status snapshot, and all tests

## [2.53.0-rc.2] - 2026-04-03

### Added
- **WebSocket heartbeat for server-initiated connections** — daemon now sends 30 s ping/pong on the WebSocket server side, matching MA's client-side heartbeat; prevents idle connection drops through proxies, firewalls, and Docker bridge networks (#120, music-assistant/support#4598)

### Removed
- Dead fallback methods `_ma_monitor_says_playing()` and `_event_history_says_playing()` — defined and tested but never called from production code; `SinkMonitor` is the sole authority for idle detection since 2.53.0

## [2.53.0-rc.1] - 2026-04-02

### Added
- **PA sink state monitoring** — PulseAudio/PipeWire sink state (`running`/`idle`/`suspended`) is now the sole authority for idle disconnect, replacing the fragile 3-tier daemon-flag + MA-monitor + event-history system (#120)
- `SinkMonitor` module: subscribes to PA sink events via `pulsectl_asyncio`, tracks state for all Bluetooth sinks, fires callbacks on `running ↔ idle` transitions
- Initial sink scan on PA connect/reconnect to populate state cache — prevents stale data after PA connection loss

### Fixed
- **Recurring speaker disconnection** during active playback — the old idle guard relied on daemon flags (`audio_streaming`, `playing`) which reset on MA-forced daemon reconnects every ~55 min (#120)
- Solo player (not in a sync group) standby/wake: `_ma_monitor_says_playing()` and `_check_group_auto_wake()` now fall back to `player_id` when `group_id` is None
- Thread safety: `_idle_timer_task` now protected by `_idle_timer_lock` — prevents leaked timers from concurrent access by asyncio event loop and Flask/Waitress threads
- Firing-time safety guard: idle timer re-checks `bt_standby`, `bt_waking`, `keepalive_enabled`, and cached PA sink state before entering standby
- SinkMonitor lifecycle: properly stopped on shutdown, startup failure, and signal handling

## [2.52.5-rc.1] - 2026-04-02

### Fixed
- Solo player (not in a sync group) standby/wake: `_ma_monitor_says_playing()` and `_check_group_auto_wake()` now fall back to `player_id` when `group_id` is None, fixing idle-standby during active playback and auto-wake for ungrouped devices

## [2.50.0-rc.1] - 2026-03-26

### Changed
- Bump websockets 13.1 → 16.0 (async API migrated to `websockets.asyncio.client`)
- Bump waitress 2.1.2 → 3.0.2
- Bump pytest-asyncio to <2.0.0
- Bump CI actions: github-script 8, setup-node 6, upload-artifact 7, deploy-pages 5, setup-buildx-action 4

## [2.48.1-rc.4] - 2026-03-25

### Fixed
- Avoid false `lost bridge transport` states after a successful replacement reconnect. The bridge now publishes `server_connected` only after the new Sendspin websocket handshake succeeds, so the old session's disconnect callback cannot overwrite the fresh connection state back to disconnected.

## [2.48.1-rc.3] - 2026-03-25

### Fixed
- Avoid false recovery/disconnected UI states during planned Music Assistant metadata reconnects. The bridge now marks this as a dedicated `ma_reconnecting` transition, so idle speakers without an active audio stream show a benign reconnecting state instead of `lost bridge transport` or `Music Assistant unavailable`.

## [2.48.1-rc.2] - 2026-03-25

### Fixed
- Avoid false `lost bridge transport` guidance while audio is already playing. Recovery and operator guidance now treat active audio streaming as authoritative during brief Sendspin control reconnects, so transient `server_connected=false` windows no longer raise a transport-loss warning when the speaker is still streaming.

## [2.48.1-rc.1] - 2026-03-25

### Fixed
- Avoid `sendspin.audio` callback crashes after ALSA underrun / re-anchor recovery. The bridge now guards against stale cached output-frame state inside the subprocess runtime so a reused frame from an older format or correction cycle is reset instead of exploding with `ValueError: memoryview assignment: lvalue and rvalue have different structures`.

## [2.48.0-rc.11] - 2026-03-25

### Fixed
- Make native Sendspin `shuffle`/`repeat` buttons feel immediate again. The web UI now applies the same kind of optimistic local state update it already used for Music Assistant queue commands, instead of waiting several seconds for the backend status round-trip before changing the button state.

## [2.48.0-rc.10] - 2026-03-25

### Fixed
- Avoid the remaining startup race in stale `device_info` recovery. Automatic metadata reconnects are now held back for an additional startup grace window after the player first becomes ready, so they do not interrupt the initial post-restart handshake and leave the speaker idling.

## [2.48.0-rc.9] - 2026-03-25

### Fixed
- Avoid intermittent post-restart idle/stuck players when Music Assistant reports stale `device_info` before the Sendspin subprocess is fully ready. Stale-metadata reconnects are now deferred until the player subprocess is running and connected, instead of being sent too early and getting lost during startup.

## [2.48.0-rc.8] - 2026-03-25

### Fixed
- Avoid false Bluetooth device removal during `Save & Restart` when the default adapter is represented as a missing value in the saved config but as an empty string in the web UI payload. Default adapter values are now normalized before the config save path decides whether a device was moved to another adapter.

## [2.48.0-rc.7] - 2026-03-25

### Fixed
- Persist the live Bluetooth sink volume during graceful shutdown, so `Save & Restart` restores the last user-set speaker volume instead of falling back to the sink default on the next startup.

## [2.48.0-rc.6] - 2026-03-25

### Fixed
- Avoid false `repair required` states after bridge restarts when BlueZ temporarily reports the speaker device as unavailable. The bridge now treats the pairing state as unknown in that window, retries a normal reconnect first, and only falls back to re-pair when BlueZ explicitly reports `Paired: no`.

## [2.48.0-rc.5] - 2026-03-25

### Fixed
- Complete sendspin 5.8.0 audio API compatibility. The bridge now resolves `query_devices`, `parse_audio_format`, and `detect_supported_audio_formats` from either `sendspin.audio_devices` (new layout) or legacy `sendspin.audio`, and adapts to the new `detect_supported_audio_formats(audio_device)` signature.
- Restore diagnostics and demo-mode PortAudio device reporting with the new sendspin audio module layout.
- Make sendspin compatibility tests order-independent by cleaning up mocked audio modules consistently.

### Changed
- Pin direct runtime dependencies in `requirements.txt` to the CI-validated versions so future upstream releases do not silently change the runtime API surface on new installs or image rebuilds.

## [2.48.0-rc.4] - 2026-03-25

### Fixed
- Restore daemon startup with sendspin builds that no longer export `parse_audio_format` from `sendspin.audio`. Preferred format resolution now falls back to `detect_supported_audio_formats()` instead of crashing the subprocess on import.

## [2.48.0-rc.3] - 2026-03-25

### Added
- Separate `RECOVERY_BANNER_GRACE_SECONDS` setting to keep recovery banners hidden for a configurable delay after the startup lockout/finalizing page is lifted.

### Changed
- Startup finalizing grace now defaults to `5` seconds, and the new recovery-banner grace defaults to `15` seconds.
- Default `PULSE_LATENCY_MSEC` for new installs is now `600`, and newly added Bluetooth devices default to `static_delay_ms = -300`.

## [2.48.0-rc.2] - 2026-03-25

### Added
- Cross-bridge duplicate device detection. When multiple bridge instances (e.g. stable + RC addons) share the same host, the bridge now detects devices already registered under another instance at startup and during BT scans.
- Startup warning + recovery banner when a configured device conflicts with another bridge (via existing RecoveryIssue / operator guidance system).
- BT scan results annotated with ⚠ warning chip when a discovered device is already registered on another bridge. Add/Pair buttons show a confirmation prompt.
- `DUPLICATE_DEVICE_CHECK` config option (default: enabled) to control cross-bridge detection.

## [2.48.0-rc.1] - 2026-03-25

### Added
- Native Sendspin transport commands via Controller role (`POST /api/transport/cmd`). Play, pause, stop, next, previous, shuffle, repeat, and volume commands are sent directly over the Sendspin WebSocket — bypassing the Music Assistant REST API for lower latency.
- Extended metadata forwarding from Sendspin protocol: album, album artist, artwork URL, year, track number, shuffle state, and repeat mode are now included in device status.
- Controller state listener: `supported_commands`, `group_volume`, and `group_muted` are forwarded from the MA server's controller role updates.
- Web UI uses native shuffle/repeat/album/artwork as fallback when Music Assistant API is unavailable.
- Web UI transport buttons prefer native Sendspin commands when supported, falling back to MA queue commands for seek and when native transport is unavailable.

## [2.46.1-rc.7] - 2026-03-24

### Fixed
- Demo-mode restart emulation now marks startup progress as `stopping` before resetting runtime config, and the restart regression test now waits for the explicit `Demo restart complete` state, removing the CI race that could leave the test observing `stopping` instead of `ready`.
- The lint workflow now pins Ruff to the same formatter line used by the repository hooks, avoiding spurious CI-only `ruff format --check` drift.

## [2.46.1-rc.6] - 2026-03-24

### Changed
- The restart finalizing grace period now defaults to `10` seconds and is configurable from the bridge settings (and Home Assistant add-on options), so the UI can unlock sooner after the backend reports ready.

### Fixed
- Disconnected-device recovery banners now promote `Release Bluetooth` to the primary action while auto-reconnect is already in progress, keeping the top banner aligned with the inline recommendation.

## [2.46.1-rc.5] - 2026-03-24

### Changed
- Docker update dialogs now show the manual pull/redeploy guidance directly inside the modal, including a one-click copy action for the exact `docker pull` command.
- Restart banners and zero-device startup placeholders now turn the long `Startup 90%` tail into a live device-restore summary, showing how many speakers are ready and how many are still reconnecting or waiting for Bluetooth, a sink, or Sendspin.

### Fixed
- Demo mode now keeps temporary config writes inside a writable demo-only config path and uses an explicit fake restart hook, so `Save and Restart` really resets temporary fleet additions instead of trying to touch `/config`.
- Demo Bluetooth scans now hide MAC addresses that are already present in the configured fleet or already paired inventory, so scan results only show genuinely new demo devices.

## [2.46.1-rc.4] - 2026-03-24

### Changed
- Bluetooth scan empty states now suggest a practical recovery path when the target device does not appear: retry the scan, reboot the Bluetooth adapter, and finally reboot the host if needed.

## [2.46.1-rc.3] - 2026-03-24

### Changed
- Music Assistant syncgroup cache refreshes now log at `INFO` only when the cached group mapping actually changes, while unchanged periodic refreshes stay in `DEBUG`.
- Latency tuning guidance now sends multi-device setups without per-device static delays straight to the device fleet delay settings instead of the global PulseAudio latency control.
- List-view secondary actions (`Reconnect`, `Release`, `Disable`) now reuse the shared action-button styling, stay vertically centered against the player card, and remain visually muted until hover.

### Fixed
- Bluetooth standalone pairing/reconnect flows now clear stale device state, wait to `trust` a device until pairing really succeeds, and serialize scan/pair/reset operations so BlueZ agent registration conflicts no longer break pairing.
- List-view cards now hide row-level transport controls when sendspin transport is unavailable, suppress empty playback rails, and keep the remaining playback rail centered when Music Assistant data is unavailable.

## [2.46.1-rc.2] - 2026-03-24

### Changed
- Bluetooth pairing failure logs now surface the most useful `bluetoothctl` / BlueZ reason (for example `Failed to pair: org.bluez.Error.ConnectionAttemptFailed`) instead of only a truncated output tail, while keeping the longer raw output in `DEBUG`.

## [2.46.1-rc.1] - 2026-03-24

### Changed
- Header runtime/version badges are now visually distinct again: runtime uses a standard passive badge, while the current version keeps a standard interactive badge without reading like a full button.
- Routine config reloads are now quieter in logs. Successful `load_config()` calls only log at `INFO` on first startup load, while later reloads and runtime-state-only config writes stay in `DEBUG`.

## [2.46.0-rc.3] - 2026-03-23

### Changed
- Docker and Raspberry Pi images now keep container init/root setup for Bluetooth and D-Bus, but automatically re-exec the bridge process as `AUDIO_UID` for user-scoped host audio sockets. This fixes the common Raspberry Pi root-vs-user PulseAudio/PipeWire mismatch without requiring a global Compose `user:` override.
- Startup diagnostics, the Raspberry Pi pre-flight checker, and Docker docs now distinguish init UID vs app UID, explain the new split-privileges model, and treat a global Compose `user:` override as an older-image diagnostic fallback instead of the preferred deployment path.

## [2.46.0-rc.2] - 2026-03-23

### Changed
- Docker/Raspberry Pi startup diagnostics now report the runtime UID/GID, selected host audio socket path, socket ownership/mode, and a live `pactl info` probe result so PipeWire/PulseAudio access problems are much easier to diagnose from container logs.
- The Raspberry Pi pre-flight checker and Docker docs now explain `AUDIO_UID` more clearly, include copy-paste audio troubleshooting commands, and document a temporary `user:` override test for confirming user-scoped PipeWire/PulseAudio UID mismatches.

## [2.46.0-rc.1] - 2026-03-23

### Added
- Bridge-backed Bluetooth devices can now carry stable room metadata (`room_name`, `room_id`, source/confidence) and expose it through status snapshots, making Music Assistant / Home Assistant / MassDroid room mapping much easier to reason about.
- Device snapshots now include a compact `transfer_readiness` contract so operators and automations can see whether a speaker is truly ready for a fast room handoff.

### Changed
- Per-device settings now support an explicit `handoff_mode`, with `fast_handoff` reusing the existing keepalive path to keep selected speakers warmer for transfer-heavy room workflows.
- Runtime device events are now enriched with room and readiness context, and the web UI surfaces new room / transfer badges plus manual room assignment controls in device settings.
- Home Assistant add-on config sync/translation now preserves the new room and handoff fields across supervisor round-trips and restarts.

## [2.45.0-rc.3] - 2026-03-23

### Added
- Diagnostics recovery timeline now retains a deeper event window and exposes advanced severity, scope, source, and window filters for power-user trace review.

### Changed
- Home Assistant area-based naming suggestions for `Bridge name` and Bluetooth adapter names are now toggleable, while still defaulting to enabled in HA add-on mode.

## [2.45.0-rc.2] - 2026-03-23

### Changed
- The onboarding checklist now stays out of the main notice stack on non-empty installs until the operator expands it, so recovery guidance owns the top-level next-action surface during day-to-day runtime issues.
- Grouped guidance actions now show an affected-device preview before bulk reconnect, Bluetooth-management, or safe-check reruns are queued.
- Recovery issue pills now collapse dense attention states into a calmer `+N more` summary, and row-level blocked hints suppress duplicate remediation copy when the same action is already explained by top-level guidance.

## [2.45.0-rc.1] - 2026-03-23

### Added
- Home Assistant ingress sessions can now fetch the HA area registry into the config UI, so `Bridge name` offers one-click room suggestions instead of requiring manual retyping.
- Bluetooth adapter settings now support optional HA area mapping by adapter MAC, including exact device-registry matches and a `Use area name` shortcut for adapter custom names without touching existing names automatically.

## [2.44.0-rc.2] - 2026-03-23

### Added
- Music Assistant runtime can now be reloaded without restarting the whole bridge: saving a new MA URL/token reuses the running process, reloads monitor credentials, and re-runs MA group discovery through the new `POST /api/ma/reload` path.

## [2.44.0-rc.1] - 2026-03-23

### Changed
- Diagnostics downloads and bugreport text now include a plain-text recovery timeline summary, so support bundles capture the recent reconnect/sink history without requiring the separate CSV export.
- Music Assistant discovery now prioritizes Home Assistant add-on candidates, preserves the discovery source/summary in the API payload, and steers missing-URL onboarding toward retrying discovery before manual MA setup.
- Device capability metadata now exposes dependency chains and recommended actions, letting onboarding, recovery guidance, and blocked controls reuse the same remediation contract.

### Fixed
- Blocked device controls no longer rely on hover-only titles: cards and expanded list rows now render visible compact hints with dependency copy and inline remediation actions for touch/mobile operators.
- Latency guidance can now offer the recommended PulseAudio setting directly from onboarding/operator guidance instead of forcing a detour into full diagnostics first.

## [2.43.0-rc.5] - 2026-03-23

### Fixed
- Onboarding step indicators now stay circular in the responsive/mobile layout too, instead of reverting to rounded-square markers under the compact CSS override.

## [2.43.0-rc.4] - 2026-03-23

### Fixed
- The expanded onboarding banner now renders the full checklist instead of truncating it to five visible items, so the step list matches the seven-step progress indicator shown to operators.

## [2.43.0-rc.3] - 2026-03-23

### Changed
- Onboarding now exposes a staged `foundation → first speaker → Music Assistant → tuning` journey in addition to the dependency-ordered checklist, so first-room setup reads as a clearer guided flow instead of only a flat status list.
- Recovery diagnostics now include rerunnable safe checks, richer latency guidance with current/recommended values and presets, and a chronological recovery timeline with CSV export.
- Roadmap and TODO docs were synced with the real v2 state, retiring the stale standalone/LXC auto-update backlog item and narrowing the remaining pre-v3 gaps to the true UX/productization work.

## [2.43.0-rc.2] - 2026-03-23

### Changed
- Refined the onboarding checklist flow connector so the line cleanly links step indicators, feels closer to the rest of the UI chrome, and no longer shows through the step indicator itself.

## [2.43.0-rc.1] - 2026-03-23

### Changed
- Added a normalized bridge/device state model across `/api/status`, device snapshots, onboarding, recovery, and operator guidance so runtime substrate, configuration intent, transport/sink health, and recovery hints are derived once and exposed consistently.
- Extracted shared device health and capability derivation into reusable services, including machine-readable blocked-reason metadata and guidance issue context (`layer`, `priority`, `reason_codes`) for future UI/status extensions.

## [2.42.4-rc.5] - 2026-03-23

### Fixed
- Mixed onboarding states are now explained more clearly when a saved speaker is disabled and no paired Bluetooth speaker is available: the UI now prioritizes pairing/rediscovery first, surfaces a visible `Scan for speakers` action, and avoids the misleading `All devices disabled` summary for that case.

## [2.42.4-rc.4] - 2026-03-23

### Changed
- Onboarding now follows the real bridge dependency hierarchy: runtime host access, Bluetooth control, audio backend health, bridge-managed device availability, sink readiness, Music Assistant integration, and only then latency tuning.

### Fixed
- Neutral operator states like `all devices disabled` or `all devices released` no longer demote higher-priority infra failures; if runtime, Bluetooth, or audio access is broken, guidance keeps that layer as the current recovery step instead of pushing operators to lower-level device actions first.

## [2.42.4-rc.3] - 2026-03-23

### Fixed
- Standalone/LXC installs now persist the exact installed release ref and expose it as the runtime version, so RC-channel deployments continue to see newer RC builds instead of collapsing to the stable release line after an update.

## [2.42.4-rc.2] - 2026-03-23

### Changed
- Operator guidance now treats Bluetooth adapter access as a top-level dependency: when preflight cannot see a controller, the header, banner, and onboarding card all push operators to restore adapter access before trying to re-enable speakers.

### Fixed
- Standalone RC updates now finish cleanly in the UI when the backend reports the upgraded release line (`2.42.4`) instead of the full prerelease ref (`2.42.4-rc.2`), preventing `Update in progress` from getting stuck after a successful upgrade.
- The Bluetooth scan flow no longer crashes while rendering scan outcomes, and the guidance/tests around disabled devices are now deterministic across hosts with different local Bluetooth preflight state.

## [2.42.4-rc.1] - 2026-03-23

### Changed
- The Bluetooth scan modal now keeps active scans explicit even after dismissal: closing the dialog leaves the scan running in the background, the main launcher switches into an `Open active scan` state, and reopening the modal rehydrates the current progress/results instead of silently starting over.

### Fixed
- The Bluetooth scan modal now behaves like a real dialog for keyboard users by trapping Tab navigation inside the overlay, moving focus into the modal on open, and restoring focus to the opener on close.
- Scan and pair job polling now share the same hardened async path, so non-OK responses surface cleanly in the UI and pair failures use in-app toast/status feedback instead of blocking browser alerts.
- Scan result rows no longer advertise false whole-row click affordances; interaction stays button-driven and passive rows read as informational rather than broken.

## [2.42.3-rc.3] - 2026-03-22

### Added
- The bug report dialog now pre-fills an editable description generated from attached diagnostics, summarizing recent errors, Bluetooth/device health, daemon status, and Music Assistant connectivity so issue reports start with more useful context.

### Fixed
- The `Auto-get token on UI open` Music Assistant setting is now hidden outside Home Assistant add-on mode, matching the runtime behavior where silent token bootstrap only works through HA ingress.

## [2.42.3-rc.2] - 2026-03-22

### Changed
- Onboarding guidance now separates status from disclosure more clearly: the header keeps a passive setup-status badge, while checklist visibility uses an explicit `Show checklist` / `Hide checklist` control and a collapsed summary state in the notice stack instead of disappearing completely.
- The Music Assistant configuration flow is now easier to re-enter after initial setup: the connection-status card owns the `Reconfigure` action, and the sign-in/token section stays hidden until reconfiguration is explicitly requested.

### Fixed
- The onboarding checklist toggle now updates its `Show` / `Hide` state immediately when clicked instead of waiting for the next background status refresh.
- Guidance and banner CTAs that send operators back to Music Assistant token setup now open the section directly in reconfigure mode so the auth controls are visible right away.

## [2.42.3-rc.1] - 2026-03-22

### Fixed
- The Bluetooth scan modal now keeps discovered-device badges inline after the device name, making dense result lists more compact without losing badge context.
- The `Bluetooth → Paired devices` inventory layout is corrected again: the subtitle stays on one line, the inner `Already paired devices` header/count no longer collapses, and the `Info`, `Reset & Reconnect`, and remove actions stay aligned on the right side of each row.

## [2.42.2-rc.7] - 2026-03-21

### Fixed
- Standalone Home Assistant login against Music Assistant add-ons now completes again after TOTP by falling back to direct HA login flow, resolving MA ingress through HA Supervisor APIs, and creating the final MA token with an `ingress_session` cookie instead of a plain HA bearer token.

## [2.42.2-rc.6] - 2026-03-21

### Fixed
- Home Assistant login failures against Music Assistant now return the actual MA-side bootstrap reason when HA OAuth is unavailable, and the UI guidance now tells operators to switch to direct Music Assistant authentication when HA login is not configured there.

## [2.42.2-rc.5] - 2026-03-21

### Changed
- The Bluetooth scan dialog now follows the shared compact modal system instead of the older bug-report shell, with a more consistent accent header, modal layout, scan controls, progress section, and results framing.
- Bluetooth scan and paired-device actions now speak the same design language as the rest of the interface, including the bluetooth-icon `Tools` trigger in device rows and a simpler static paired-devices header without leftover disclosure styling.

### Fixed
- The scan modal copy now explains the actual operator workflow — choose an adapter, scan nearby devices, then add or pair speakers — instead of describing the internal implementation of the page.

## [2.42.2-rc.4] - 2026-03-21

### Added
- Onboarding now recognizes when every configured speaker has been manually released and offers direct reclaim actions so playback can be resumed without hunting through the configuration screens first.

### Changed
- The compact UI now exposes a clearer shared design-system layer: spacing, typography, focus-ring, layout, count-badge, and action-menu primitives are reused across notice, configuration, toolbar, and guidance surfaces instead of being defined as scattered local overrides.
- Configuration headers, notice copy blocks, and unsaved-count indicators now share the same structural shells, improving hierarchy and reducing visual drift across dashboard and settings flows.

### Fixed
- Guidance cards that opt into `show_by_default` now auto-open consistently from the header entry point instead of only doing so for the empty-state scenario.

## [2.42.2-rc.3] - 2026-03-21

### Changed
- Badge and chip styling now follows a much more unified system across the live dashboard, device fleet, scan progress, onboarding, and recovery surfaces, reducing visual drift between list, grid, and configuration views.

### Fixed
- Interactive and passive badges now use more consistent borders, hover feedback, and cursor behavior throughout the interface, and the `BT tools` menu now matches the compact control typography used elsewhere.

## [2.42.2-rc.2] - 2026-03-21

### Added
- The Bluetooth scan modal now exposes adapter selection, an explicit audio-only filter, and a dedicated rescan action so multi-adapter discovery is easier to control.

### Changed
- Bluetooth discovery now reports richer scan metadata to the frontend, letting the modal show timed progress, countdown state, and clearer result context without turning the workflow into a permanent page block.

### Fixed
- Scan modal results now stay aligned with the selected discovery scope, and non-audio Bluetooth candidates are surfaced more honestly when the audio-only filter is disabled.

## [2.42.2-rc.1] - 2026-03-20

### Changed
- The compact UI system is now much more consistent across the live app: primary/secondary/icon actions, media transport controls, table-like rows, and empty states now follow a shared visual language instead of mixing several older styles.
- Configuration, diagnostics, discovery, and device list surfaces now use denser data-row and placeholder treatments, keeping the current information architecture while making the interface feel more coherent and Home Assistant-aligned.
- The login screen now follows the same refreshed compact styling as the main application, reducing the visual jump between authentication and the dashboard.

### Fixed
- Demo mode regains compatibility with the refreshed UI preview workflow, so local demo validation continues to work against the current Bluetooth manager behavior.

## [2.42.0-rc.23] - 2026-03-20

### Added
- Diagnostics cards can now copy their section content to the clipboard for support workflows and reveal raw payload details on demand for expert troubleshooting.

### Changed
- Grid view playback cards now use larger now-playing artwork thumbnails so album art fills more of the media block instead of leaving extra empty space above and below.
- Diagnostics now opens with a clearer `Overview` layer and a separate collapsible `Advanced diagnostics` layer, promoting `Recovery center` as the primary entry point for action.
- Diagnostics copy, card hierarchy, and section density are now tuned for mixed-skill operators: summary cards jump to the relevant section, key cards lead with playback impact before raw telemetry, and direct shortcuts open the relevant configuration surfaces for devices, Bluetooth, Music Assistant, and latency.

## [2.42.0-rc.22] - 2026-03-20

### Fixed
- LXC one-click updates now keep the backend lockout active for the full apply/restart/startup cycle instead of briefly returning to the normal dashboard before the restart begins.
- After the updated bridge comes back on the new version, the web UI now performs a cache-busting page refresh so the browser reloads the latest HTML, JavaScript, and CSS immediately.

## [2.42.0-rc.21] - 2026-03-20

### Fixed
- Disabling a device from the dashboard now also updates the `Configuration → Devices` enabled toggle immediately, so `Save and restart` keeps the device disabled without requiring a page refresh first.
- The `All devices disabled` state now opens onboarding by default again and replaces the generic “Attach your first speaker” copy with guidance for re-enabling a configured device from `Configuration → Devices`.
- The onboarding `Review latency tuning` step now jumps to `Configuration → General`, highlights `PULSE_LATENCY_MSEC`, and focuses the correct field instead of sending operators to device settings.

## [2.42.0-rc.20] - 2026-03-20

### Changed
- Startup lockout copy is now clearer during the final startup grace period: `Finalizing startup` is shown as `Startup 90%`, and the follow-up message uses `Finalizing Startup` instead of `Startup complete`.

### Fixed
- Runtime status snapshots now include each device's global `enabled` flag, so disabling a live device no longer collapses into a plain `Released` state on the next status refresh.
- Disabled cards now keep their disabled status/sink labels and grayscale treatment instead of reverting after the runtime client is torn down.

## [2.42.0-rc.19] - 2026-03-20

### Changed
- The onboarding checklist is now toggleable from the header status badge in every guidance mode, while still opening by default only when no bridge devices are configured.
- Even healthy bridges keep the onboarding checklist available as an on-demand reference instead of dropping it entirely from the guidance payload.

### Fixed
- Completed onboarding steps once again render a visible checkmark inside their success indicator instead of showing only a green circle.

## [2.42.0-rc.18] - 2026-03-20

### Changed
- The onboarding checklist now uses clearer step circles with visible checkmarks for completed steps and ordinal numbers for the remaining steps.
- The header setup/status pill now opens the onboarding checklist directly, so operators can jump into pending setup work from the compact header state.

### Fixed
- Disabled device cards no longer lose their grayscale/inert state on the next live status refresh when `/api/status` omits `enabled` for active runtime devices.
- When configured devices exist but all of them are globally disabled, the dashboard now shows an explicit `All devices disabled` guidance state with a direct path to `Configuration → Devices`.

## [2.42.0-rc.17] - 2026-03-20

### Changed
- Disabled device cards and list rows now render in full grayscale, making the disabled state much more obvious across album art, icons, badges, and controls.
- Backend lockout artwork is now animated, with subtle motion during startup/restart and a gentler pulse for warning/unavailable states.

### Fixed
- HA add-on ingress refreshes no longer get stuck behind a frontend-only `Restoring bridge state` lockout after backend startup has already settled.

## [2.42.0-rc.16] - 2026-03-20

### Fixed
- Restart/startup lockout now stays active for the full live startup path, including single-device status payloads, so the dashboard no longer drops back to the normal UI while startup is still running or during `Finalizing startup`.

## [2.42.0-rc.15] - 2026-03-20

### Fixed
- Backend restart lockout now clears based on the live `Finalizing startup` phase instead of a generic frontend delay, so a normal page refresh no longer looks artificially locked while restart flows still stay protected until startup really settles.
- Devices become immediately inactive after `Disable`: their cards/rows stop reacting to clicks, sliders, transport controls, and settings actions as soon as the operator disables them.
- The Devices Bluetooth scan cooldown is now 10 seconds instead of 30, so operators can retry discovery much sooner.

## [2.42.0-rc.14] - 2026-03-20

### Fixed
- Backend restart/unavailable lockout now stays active for five extra seconds after status would normally clear it, giving the dashboard a short settle time before the full UI becomes interactive again.

## [2.42.0-rc.13] - 2026-03-20

### Fixed
- Restart/runtime lockout now also overrides the onboarding empty-state path, so the main UI is hidden correctly during restart even when the bridge is still in first-run onboarding mode.

## [2.42.0-rc.12] - 2026-03-20

### Fixed
- `More actions` dropdowns used by onboarding guidance, top-level banners, and diagnostics recovery actions now close when the operator clicks elsewhere on the page or presses `Escape`, matching normal menu behavior.

## [2.42.0-rc.11] - 2026-03-20

### Fixed
- Restart progress in the header now follows live backend startup/runtime state instead of a frontend-only scripted sequence, so `Restart complete` is shown only after the bridge is actually usable again.
- While restart/backend lockout is active, the page now keeps a centered runtime-status card in the main content area instead of leaving the body visually empty.

## [2.42.0-rc.10] - 2026-03-20

### Fixed
- Restart and backend-unavailable states now use a true top-level runtime lockout: the dashboard short-circuits normal rendering, clears stale device state, and hides everything except the header while the bridge is still starting or restoring.
- Runtime restore states no longer reuse misleading empty/setup copy such as `Waiting for setup`; the header now reports bridge startup/restoring state explicitly instead.

## [2.42.0-rc.9] - 2026-03-20

### Fixed
- During backend restart or temporary unavailability, the dashboard now hides stale onboarding/recovery content and locks the main UI so only the header plus the backend status banner remain visible until a usable status payload returns.
- Recovery/problem banners are now delayed briefly after startup completes, preventing noisy false alarms while adapters, Bluetooth links, and per-device startup tasks are still settling.

## [2.42.0-rc.8] - 2026-03-20

### Fixed
- HA ingress setups with zero configured bridge devices no longer show a false `Bridge backend is unavailable` banner just because the status payload still carries the legacy `No clients` marker; onboarding/setup guidance stays visible instead of being replaced by a backend-outage warning.
- Onboarding no longer duplicates its primary CTA in the top-right banner actions, keeping step-specific actions inside the expanded checklist cards where the operator is already working.

### Changed
- The Bluetooth `Adapters` configuration card now explicitly explains that it expects local controllers visible inside the bridge runtime, not MAC addresses of remote ESPHome Bluetooth Proxy nodes.
- When onboarding sends the operator into Bluetooth discovery, the `Already paired` section is now loaded and forced open as well, so existing paired speakers are visible immediately alongside the active scan flow.

## [2.42.0-rc.7] - 2026-03-20

### Added
- Empty-state onboarding is now action-oriented instead of read-only: unfinished checklist steps expand into concrete runtime details, targeted guidance, and per-step recommended actions that take operators directly to the relevant setup flow.

### Changed
- Adapter-present but no-device installs now stay in the onboarding empty/setup state, so the dashboard shows `Add first speaker` guidance instead of falling back to the generic waiting screen while setup is still incomplete.
- Recovery Center issue actions, top-level guidance banners, and backend-unavailable placeholders now share a more explicit operator UX model, reducing false empty-state messaging during backend restarts and keeping the same action language across the dashboard.

## [2.42.0-rc.6] - 2026-03-20

### Fixed
- Bluetooth release is now available even while a reconnect is in progress: releasing a speaker safely cancels the in-flight reconnect attempt before stopping the daemon and disconnecting Bluetooth, so operators can intentionally stop recovery without racing the background reconnect loop.
- User-released speakers are now treated as an intentional neutral state instead of a recovery problem, while auto-released speakers remain actionable attention items; the top-level guidance banner also keeps secondary recovery actions behind a compact `More actions` menu.

## [2.42.0-rc.5] - 2026-03-20

### Fixed
- Bluetooth recovery guidance now distinguishes “disconnected but still pairable” from “no longer paired”: reconnecting/unpaired devices recommend re-pair instead of reconnect, and the top-level recovery banner now includes reconnect attempt counts plus remaining attempts before auto-release when a threshold is configured.
- Auto-released devices are now labeled consistently as `Auto-released` in the UI, and release persistence is kept separate from global `enabled=false`, so BT-released devices no longer come back after restart as globally disabled devices.

## [2.42.0-rc.4] - 2026-03-20

### Added
- Added a unified operator-guidance contract and `/api/operator/guidance` endpoint, and embedded the same guidance payload into `/api/status`, SSE status updates, `/api/diagnostics`, and bugreport exports so the dashboard, diagnostics, and support flows all speak the same top-level guidance language.

### Changed
- Phase 2.1 is now live in the web UI: the large onboarding checklist only stays visible in the true empty state, non-empty installs surface setup/recovery progress through header status plus one primary attention banner, repeated issue groups now offer bulk reconnect/reclaim actions, and both onboarding/recovery guidance can be dismissed and restored from General settings without touching `config.json`.

## [2.42.0-rc.3] - 2026-03-20

### Added
- Added a recovery assistant contract and a new `/api/recovery/assistant` surface that group active issues by severity, recommended action, recovery traces, latency guidance, and a known-good test path derived from live bridge state.
- The web UI now shows a live recovery banner and a dedicated diagnostics recovery center with safe rerun actions, per-device recovery traces, latency-assistant hints, and guided “known-good” checks for isolating routing versus Music Assistant problems.

### Changed
- `/api/diagnostics` and bugreport full-text exports now embed recovery-assistant data alongside onboarding and device health, so downloaded reports start with actionable issue summaries instead of only raw status tables.
- Phase 2’s recovery UX is now additive and snapshot-driven: the frontend consumes explicit backend recovery data rather than inferring recovery guidance from scattered flags and event fragments.

## [2.42.0-rc.2] - 2026-03-20

### Added
- Device status payloads now include an explicit capability model grouped by operator-facing domains, with `supported`, `currently_available`, `blocked_reason`, and `safe_actions` for key bridge controls.

### Changed
- Core playback and recovery controls in the web UI now prefer backend-derived capabilities over ad-hoc frontend guesses, so reconnect, release/reclaim, play/pause, volume, mute, and queue gating explain themselves more consistently.
- Diagnostics device entries now include capability data alongside health summaries and recent events, so support flows can reason about “what is possible right now” instead of only current raw state.

## [2.42.0-rc.1] - 2026-03-20

### Added
- The web UI now shows a persistent onboarding checklist card with ordered setup steps, live progress, success checkpoints, and direct links into the relevant Bluetooth, device, Music Assistant, and diagnostics surfaces.

### Changed
- `/api/onboarding/assistant` now exposes a richer checklist-oriented payload, so onboarding and diagnostics can explain the current blocker, the next best action, and which first-playback milestones have already been reached.
- Operator setup guidance now follows the first Phase 2 UX model: setup is framed as an explicit “finish these steps” flow instead of leaving operators to infer readiness from scattered status widgets alone.

## [2.41.0-rc.2] - 2026-03-20

### Changed
- ROADMAP Phase 1 integration cleanup is now complete on `main`: route modules read runtime state through dedicated bridge/MA/job/adapter services, while `state.py` remains as a compatibility facade instead of the practical ownership center.
- Bridge lifecycle contracts are now locked down more explicitly with startup/shutdown integration coverage and README-level operator documentation for lifecycle events, diagnostics/telemetry surfaces, IPC protocol guarantees, and runtime hook behavior.

### Fixed
- Adapter-name caching now follows the active `config.CONFIG_FILE` path at load time and avoids repeated disk reads when the configured adapter-name set is legitimately empty.

## [2.41.0-rc.1] - 2026-03-20

### Added
- New runtime telemetry and event hook surfaces: `/api/bridge/telemetry` exposes bridge/subprocess resource data, and `/api/hooks` lets operators register runtime-scoped webhooks with delivery history for internal bridge/device events.
- Device event normalization now captures recent Bluetooth/runtime/MA transitions more consistently, so diagnostics and health summaries can explain degraded and recovering devices from recent event history instead of only current flags.

### Changed
- ROADMAP Phase 1 and Phase 2 runtime foundation work is now live on `main`: route read paths are snapshot-first, device inventory is owned by the canonical `DeviceRegistry`, startup/shutdown publication is tightened around `BridgeOrchestrator`, and parent/daemon communication now uses explicit IPC envelopes.
- Config lifecycle handling is now schema-aware end-to-end across load/save/import/export/Home Assistant translation paths, with shared migration/write helpers and safer preservation of persisted MA credentials plus runtime state.
- Diagnostics, onboarding, and status-adjacent APIs now reuse normalized snapshot/telemetry surfaces more consistently instead of mixing direct raw-state reads with duplicated enrichment logic.

### Fixed
- `/api/diagnostics` no longer re-runs expensive environment/subprocess collection when embedding telemetry, reducing duplicate `ps`/subprocess probing on lower-power systems.
- Bug reports now redact persisted OAuth tokens and runtime-state fields using the shared sensitive-key policy, preventing newly added config secrets from leaking into generated reports.
- Runtime hook registration now rejects loopback/private/link-local targets and invalid non-numeric timeout payloads, closing SSRF-prone and 500-shaped failure paths.
- Persisted `LAST_SINKS` entries now normalize MAC keys consistently during write/load pruning, so cached Bluetooth sink mappings no longer disappear because of lowercase or whitespace-padded MAC keys.
- Device-event helper annotations now accept canonical `DeviceEventType` values directly, aligning typing with the runtime call sites used by Bluetooth and Music Assistant event publishers.

## [2.40.6-rc.7] - 2026-03-19

### Fixed
- Music Assistant beta queue mode controls now work again for solo bridge players: `shuffle` / `repeat` treat MA `error_code` replies as real rejections and fall back from modern solo player ids to legacy `up...` queue ids when that is the actual queue target.
- Standalone Configuration now uses a shorter `Web UI port` helper so the port description fits on one line without wrapping.

## [2.40.6-rc.6] - 2026-03-19

### Fixed
- Existing LXC installs can once again update onto the new prerelease tag-based channel flow: runtime update checking no longer imports `scripts.release_notes`, and the LXC install/upgrade snapshot sync now copies the `scripts/` directory so staged validations keep matching the real application tree.

## [2.40.6-rc.5] - 2026-03-19

### Changed
- Release engineering now treats GitHub Releases as a stable-only surface: prerelease update discovery switches to Git tags plus the tagged `CHANGELOG.md`, and Home Assistant add-on variant sync now runs directly on every stable/RC/beta tag push without depending on the manual GitHub release workflow.

### Fixed
- Music Assistant beta transport skip controls now prefer player-level `next` / `previous` commands for normal player IDs while keeping the legacy queue fallback, so solo-player skip actions work again against newer MA beta builds.
- Home Assistant add-on polish: the ingress port field is now clearly read-only/shaded, its helper copy is shorter, and clicking the signed-in username opens the profile in a normal new browser tab instead of a popup-style window.

## [2.40.6-rc.4] - 2026-03-19

### Changed
- High-frequency bridge control routes and long-running Music Assistant/update actions now avoid blocking request threads: MA discovery/rediscovery, update checks, and queue commands use async job polling or optimistic completion flows instead of waiting synchronously in the Flask request path.
- Home Assistant add-on update track selection is now tied to the installed add-on slug, so the add-on options no longer expose `update_channel` switching and the bridge UI treats track/update guidance as read-only information.
- Home Assistant add-on mode now treats the web UI port as a fixed ingress property of the installed track and shows that port as read-only in Configuration, while leaving `base_listen_port` configurable for Sendspin player listeners.

### Fixed
- Password and backend log-level settings no longer report success when config persistence fails; runtime log-level propagation is only attempted after the config write succeeds.
- Login rate-limiting behind trusted Home Assistant ingress proxies now uses validated forwarded client identity instead of collapsing all users into the proxy IP bucket.
- Home Assistant add-on sessions now hide the logout button and route Music Assistant profile/group-settings links through add-on ingress instead of direct host/IP URLs.

## [2.40.6-rc.3] - 2026-03-19

### Changed
- The local demo now defaults to a more realistic signed-in header state, showing a user/logout block plus a Music Assistant token notice so preview screenshots better reflect the intended top-bar layout and onboarding guidance.

### Fixed
- Hidden notice cards now stay truly hidden even when the shared notice layout applies `display: grid`, preventing duplicate Music Assistant notices from appearing in demo.
- The header utility area now includes a visible divider between the theme toggle and the user/logout controls, so the top-right actions read as distinct groups again.
- The update-available badge no longer reuses RC/beta channel tinting; prerelease text coloring remains on the current-version badge only.

## [2.40.6-rc.2] - 2026-03-19

### Changed
- Top-of-page warnings now use a shared stacked notice-card layout with consistent icon/title/body/CTA structure, so security and Music Assistant notices match the rest of the dashboard card system and stack cleanly on mobile.

### Fixed
- The Music Assistant warning notice no longer appears when the runtime bridge integration is already connected, even if the saved-token validation probe disagrees.
- Header action links in the top-right corner once again keep visible spacing between their icons and labels.
- The theme switcher's `Auto` icon now renders as a visible circled `A` instead of collapsing into a filled circle in the header button.

## [2.40.6-rc.1] - 2026-03-19

### Added
- Home Assistant add-on ingress sessions can now try to obtain a long-lived Music Assistant token automatically when the UI opens, with a default-enabled opt-out toggle in Configuration → Music Assistant.
- The web UI now shows a warning banner when Music Assistant is discoverable but the bridge integration is still missing or using an invalid token, with a shortcut into the Music Assistant configuration section.

### Changed
- The theme switcher now has an explicit three-mode cycle (`Auto`, `Light`, `Dark`) instead of only manual light/dark toggling, and both the login page and the main dashboard now bootstrap the same saved theme mode consistently.

## [2.40.5-rc.3] - 2026-03-18

### Fixed
- Home Assistant add-on config validation no longer treats optional manual `web_port` / `base_listen_port` overrides as required fields, because unset values are now omitted from addon defaults and Supervisor option sync payloads instead of being sent as `null`.

## [2.40.5-rc.2] - 2026-03-18

### Added
- Bridge config, web UI, and Home Assistant addon options now support manual top-level `WEB_PORT` and `BASE_LISTEN_PORT` overrides. In Home Assistant addon mode, `WEB_PORT` opens an additional direct host-network listener while the fixed ingress endpoint keeps using the channel default port.

### Changed
- Home Assistant prerelease addon variants now combine distinct default ingress/player port ranges, manual startup defaults, channel-specific branding, and HA-safe prerelease notices so parallel stable/RC/beta installs are easier to distinguish and safer to run on one HAOS host.
- The GitHub release workflow now builds the release body from the matching `CHANGELOG.md` section and uses GitHub-generated notes only as an optional supplement, preventing empty autogenerated releases.

### Fixed
- Music Assistant album artwork now loads correctly through Home Assistant ingress because artwork proxy URLs stay relative to the active addon origin instead of escaping to the Home Assistant root.
- Solo-player Music Assistant transport controls now keep working when Music Assistant syncgroup discovery is empty because queue commands respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first.
- Header version/update indicators now tint only the RC/Beta version text instead of coloring the entire badge, and Home Assistant add-on info/docs now render prerelease notices correctly through HA-safe badge markdown.

## [2.40.5-rc.1] - 2026-03-18

### Fixed
- Solo-player Music Assistant transport controls now keep working on live Proxmox/LXC deployments even when MA syncgroup discovery is empty, because queue commands now respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first

### Changed
- Header version badges and discovered-update badges now highlight prerelease channels directly in the UI: RC builds use yellow styling and beta builds use red styling
