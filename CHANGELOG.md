# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.5] - 2026-03-02

### Fixed
- **Bluetooth "Since:" not shown in device card** — initial `connect_device()` on
  startup bypassed the change-detection logic and set `bluetooth_connected` directly,
  so `bluetooth_connected_at` was never populated; fixed to go through the same
  conditional assignment used by the monitor loop

## [1.6.4] - 2026-03-02

### Fixed
- **BT check interval and auto-disable settings not persisted after restart** —
  `BT_CHECK_INTERVAL` and `BT_MAX_RECONNECT_FAILS` are now included in the HA
  addon schema, read from Supervisor options in `entrypoint.sh`, and synced back
  to Supervisor options on config save; values survived addon restarts

## [1.6.3] - 2026-03-02

### Fixed
- **HA Configuration page: device enabled state not synchronized** — toggling
  Release/Reclaim on the dashboard now immediately syncs the `enabled` flag to
  HA Supervisor options, so the Configuration page reflects the correct state
- **Configuration page: device enabled state lost on save** — `enabled: false`
  is now preserved in the device row dataset when loading config, preventing it
  from being reset when live status hasn't polled yet

## [1.6.2] - 2026-03-02

### Fixed
- **Configuration page: device enabled state not preserved** — `enabled: false` for
  a device is now stored in the row's dataset when loading config, so saving the
  configuration no longer resets disabled devices to enabled when live status has
  not yet polled

## [1.6.1] - 2026-03-02

### Fixed
- **Performance: config reads on every request** — `AUTH_ENABLED` is now cached at
  startup instead of re-reading `config.json` on every HTTP request (including the
  2-second status poll)

### Changed
- **Mobile UI optimization** — responsive layout at ≤640px: device cards switch to
  2-column grid, BT device table and adapters panel scroll horizontally, header stacks
  vertically, touch targets enlarged; pause/mute buttons now respect dark mode theme

## [1.6.0] - 2026-03-02

### Added
- **Web UI authentication** — optional password protection for standalone deployments
  (`AUTH_ENABLED` setting, default off); configure via the Configuration panel
- **Set password** — new "Set / change password" form in the Configuration panel;
  stores a PBKDF2-SHA256 hash in `config.json`, never plaintext
- **HA Ingress bypass** — when accessed via Home Assistant Ingress (`X-Ingress-Path`
  header), local auth is automatically skipped (HA already authenticated the user)
- **HA Supervisor auth** — when running as HA addon with `AUTH_ENABLED=true`, login
  validates against the Home Assistant user database via the Supervisor auth API
- **Sign out button** — shown in the page header when authentication is enabled
- **`SECRET_KEY` persistence** — Flask session key generated once and persisted to
  `config.json`, so sessions survive container restarts

### Fixed
- **`BT_CHECK_INTERVAL` / `BT_MAX_RECONNECT_FAILS` not loaded** — both settings were
  missing from `allowed_keys` in `load_config()` and were never read from `config.json`;
  fixed so saved values are correctly restored on startup
- **Password hash / secret key not preserved on config save** — `AUTH_PASSWORD_HASH`
  and `SECRET_KEY` are now preserved across `/api/config` POST saves (like `LAST_VOLUMES`)
- **Sensitive fields in config GET** — `AUTH_PASSWORD_HASH` and `SECRET_KEY` are now
  filtered out of the `/api/config` GET response

## [1.5.1] - 2026-03-02

### Added
- **BT_CHECK_INTERVAL** — configurable Bluetooth connection check interval in seconds
  (default 10); exposed in Configuration UI
- **BT_MAX_RECONNECT_FAILS** — auto-set device `Enabled=False` after N consecutive failed
  reconnects (default 0 = never); exposed in Configuration UI

### Fixed
- **Configuration section** — now collapsed by default on page load
- **Removed Sendspin provider tip** — dismissed the "change audio quality in MA" disclaimer
  under the PREFER_SBC_CODEC checkbox

## [1.5.0] - 2026-03-02

### Changed
- **Major code-quality sprint** — six targeted fixes + full modular refactor:

#### Quick fixes
- **VERSION consolidation** — single source of truth in `config.py`; removed duplicate
  declarations from `sendspin_client.py` and `web_interface.py`
- **DEFAULT_CONFIG consolidation** — moved to `config.py`; web UI now imports it
- **Removed `netifaces`** — deprecated dependency dropped; `get_ip_address()` uses
  `socket.connect()` exclusively (more reliable, works on all platforms)
- **Halved `bluetoothctl` subprocess spawning** — `update_status()` now reads the cached
  `bt_manager.connected` flag instead of calling `is_device_connected()` on every poll
- **Improved Docker HEALTHCHECK** — parses `/api/status` JSON; reports unhealthy if no
  device has `connected: true`; `start-period` extended to 60 s
- **Multi-stage Dockerfile** — builder stage compiles native extensions (dbus-python);
  runtime image contains only runtime libraries, reducing final image size
- **Adapter name cache** — `/api/status` no longer opens `config.json` on every 2-second
  poll; cache is invalidated on every `/api/config` POST save

#### Modular architecture (Phase 3)
- **`state.py`** (new) — shared `clients` list with in-place mutation + adapter name cache
- **`services/bluetooth.py`** (new) — `bt_remove_device`, `persist_device_enabled`,
  `is_audio_device`, `_AUDIO_UUIDS`
- **`routes/api.py`** (new) — all `/api/*` route handlers as Flask Blueprint (~590 lines)
- **`routes/views.py`** (new) — `index()` route as Flask Blueprint
- **`web_interface.py`** slimmed from ~1 045 lines to **57 lines** — app init, WSGI
  middleware, blueprint registration, `main()`



### Fixed
- **Home Assistant ingress CSS/JS** — `before_request` SCRIPT_NAME approach replaced with
  `_IngressMiddleware` WSGI wrapper that modifies environ before Flask creates the URL adapter;
  now `url_for()` correctly prefixes static file paths with the ingress base path
- **Missing ▶ on Diagnostics collapsible** — CSS `::before` rule was missing closing `}`,
  preventing the triangle indicator from rendering
- **No rotate animation on config/diag open** — added `transform: rotate(90deg)` to
  `.config-section[open]` and `.diag-section[open]` `summary::before` rules
- **CSS `::before` transition** — added `display: inline-block; transition: transform 0.2s`
  to all three collapsible section `::before` rules

## [1.4.1] - 2026-03-02

### Fixed
- **Home Assistant ingress** — static files (CSS/JS) failed to load when accessed
  via HA addon panel; added `X-Ingress-Path` header handling to set Flask `SCRIPT_NAME`
  so `url_for()` generates correctly-prefixed URLs
- **Broken emoji on Release/Reclaim buttons** — Python unicode escapes (`\U0001F513`)
  replaced with literal `🔓`/`🔒` characters in `app.js`
- **Broken triangle in collapsible sections** — CSS `content: '\\25B6'` (double backslash,
  rendered as literal text) corrected to `'\25B6'`



### Changed
- **Major modular refactoring** — monolithic files split into focused modules:
  - `config.py` — configuration path, shared `_config_lock`, `load_config()`,
    `_player_id_from_mac()`, `_save_device_volume()`
  - `mpris.py` — `MprisIdentityService`, `pause_all_via_mpris()`,
    `read_mpris_metadata_for()`, optional D-Bus import guard
  - `bluetooth_manager.py` — `BluetoothManager` class and `_force_sbc_codec()`
    (492 lines, with `TYPE_CHECKING` guard to avoid circular imports)
  - `sendspin_client.py` reduced from 1373 to 753 lines (core client + main only)
- **HTML/CSS/JS extracted from Python** — `web_interface.py` reduced from 2891 to
  1107 lines; markup moved to `templates/index.html`, styles to `static/style.css`,
  scripts to `static/app.js`; Flask now serves static files natively
- **Unified config lock** — `web_interface.py` now imports `_config_lock` from
  `config.py` instead of maintaining its own separate lock, eliminating cross-process
  config race conditions



### Fixed
- **Shell injection in `pair_device()`** — replaced `bash -c` f-string construction with
  a direct `bluetoothctl` `Popen` + stdin pipe; added MAC address regex validation before
  use; eliminates command injection via the `/api/bt/pair` web endpoint
- **Silent task crash** — `add_done_callback` lambdas in `monitor_and_reconnect` and
  `monitor_output` were ternary expressions evaluated at registration time (always `None`),
  so crashes were silently swallowed; replaced with proper named callback functions
- **NameError in `main()`** — `config_file` (local to `load_config()`) was referenced in
  `main()`, silently caught by `except Exception: pass`; replaced with `_CONFIG_PATH`;
  per-device volume pre-fill now works correctly on startup
- **Dropped config keys on reload** — `LAST_VOLUMES`, `BLUETOOTH_ADAPTERS`, and
  `BRIDGE_NAME_SUFFIX` were missing from `load_config()` `allowed_keys` and stripped on
  every config reload; all three keys are now preserved
- **Premature `server_connected=True`** — flag was set immediately after `Popen()` before
  the sendspin process connected to Music Assistant; removed; state is now set by log
  parsing and the `update_status()` polling loop as before
- **100% volume blast on BT connect** — `configure_bluetooth_audio()` no longer forces
  the sink to 100% before restoring the saved volume, preventing an audible blast
- **Blocking `process.wait()` in async context** — both termination paths in
  `start_sendspin_process()` and the shutdown cleanup now wrap `process.wait()` in
  `run_in_executor()` to avoid stalling the asyncio event loop
- **`_pause_all_via_mpris` blocking event loop** — converted from `async def` to a
  regular function; called via `run_in_executor()` during graceful shutdown

### Security
- **`pair_device()` shell injection** — see Fixed above
- **Docker: removed `privileged: true`** — `cap_add` (NET_ADMIN, NET_RAW, SYS_ADMIN) is
  sufficient; `privileged: true` granted unrestricted host access unnecessarily
- **Docker: removed hardcoded developer MAC** — `BLUETOOTH_MAC` placeholder now uses
  `${BLUETOOTH_MAC:-}` env var substitution

### Changed
- **Config file writes are now atomic** — all `config.json` read-modify-write operations
  in both `sendspin_client.py` and `web_interface.py` are serialised with a
  `threading.Lock` and written via a temporary file + `os.replace()` to prevent data
  corruption from concurrent Flask/asyncio writes
- **Thread-safe status dict** — `SendspinClient` now exposes `update_status(**kwargs)`
  and `get_status()` methods backed by a `threading.Lock`
- **Docker: configurable audio UID** — hardcoded `/run/user/1000/pulse` paths replaced
  with `${AUDIO_UID:-1000}` to support systems where the primary user is not UID 1000
- **Replaced all `bash -c` subprocess wrappers** in `web_interface.py` BT API endpoints
  with direct `bluetoothctl` invocations using stdin pipe (no shell, no injection risk)
- **`dbus-python` version pinned** to `>=1.3.2,<2.0.0` in `requirements.txt`

## [1.3.32] - 2026-03-02

### Fixed
- **Server column shows `host:port`** — URI in device card Server column now taken from
  config settings (`server_host:server_port`) instead of the full `ws://…/sendspin`
  string detected from `/proc/net/tcp`; for `auto`-discovery mode the host is extracted
  from the resolved URL

## [1.3.31] - 2026-03-02

### Fixed
- **`--audio-device` crash on PipeWire** — `start_sendspin_process()` now uses the sink
  name confirmed by `configure_bluetooth_audio()` instead of always hardcoding
  `bluez_sink.{MAC}.a2dp_sink`; on PipeWire systems the actual sink is `bluez_output.*`
  so the hardcoded name caused an immediate "Specified audio device not found" crash and
  immediate process restart loop; when no sink has been confirmed yet `--audio-device` is
  omitted entirely and `PULSE_SINK` alone is used (pre-v1.3.29 fallback behaviour)

## [1.3.30] - 2026-03-02

### Fixed
- **Stale playback state** — `update_status()` now polls MPRIS `PlaybackStatus`
  unconditionally (not only when `playing=True`); `PlaybackStatus` overrides log-based
  state detection when D-Bus responds, so pausing in MA is reflected in the bridge UI
  within ≤10 s without relying on fragile log parsing
- **Stale track metadata** — track/artist are kept on pause instead of cleared; last
  known values remain visible while paused; `_read_mpris_metadata_for()` extended to
  return `(artist, track, playback_status)` instead of `(artist, track)`

## [1.3.29] - 2026-03-02

### Fixed
- **sendspin 5.x compatibility** — `requirements.txt` now pins `sendspin>=5.1.3,<6`;
  `--audio-device bluez_sink.{MAC}.a2dp_sink` passed explicitly alongside `PULSE_SINK`
  for reliable sink routing under sendspin 5.x; `--hardware-volume false` prevents
  sendspin's native volume control from conflicting with bridge volume sync
- **Per-instance config isolation** — deprecated `--settings-dir` replaced with
  per-instance `HOME=/tmp/sendspin-{id}` to isolate `~/.config/sendspin/` across
  daemon instances

## [1.3.28] - 2026-03-02

### Fixed
- **PULSE_LATENCY_MSEC and PREFER_SBC_CODEC reset on restart** — `entrypoint.sh` was
  regenerating `/data/config.json` from `options.json` without mapping
  `pulse_latency_msec` and `prefer_sbc_codec`, causing both settings to always revert
  to defaults (200 ms / false) on every container restart

## [1.3.27] - 2026-03-02

### Added
- **Prefer SBC codec** — new `PREFER_SBC_CODEC` config option; when enabled, forces the
  A2DP codec to SBC immediately after each Bluetooth connect via
  `pactl send-message … bluez5/set_codec a2dp_sink SBC` (requires PulseAudio 15+);
  SBC is the simplest mandatory A2DP codec and reduces PA encoder CPU load; exposed in
  the web UI config form and HA addon native Config tab
- **LXC CPU-optimal PulseAudio config** — `lxc/pulse-daemon.conf` installed to
  `/etc/pulse/daemon.conf` by `install.sh`; sets `resample-method=trivial`,
  `default-sample-rate=48000`, `default-sample-format=s16le`

## [1.3.26] - 2026-03-02

### Added
- **PULSE_LATENCY_MSEC setting** — configurable PulseAudio buffer latency (default
  200 ms); increase to 400–600 ms to reduce audio dropouts on slow or overloaded
  hardware; exposed in the web UI config form and HA addon native Config tab
- **Sendspin process nice priority** — sendspin daemons launched with `nice -5` so audio
  threads are scheduled ahead of lower-priority background tasks under load

### Fixed
- **MPRIS track per player** — `_read_mpris_metadata_for()` now queries
  `org.mpris.MediaPlayer2.Sendspin.instance{PID}` directly instead of returning
  metadata from the first MPRIS service found; each player now shows its own current
  track

## [1.3.25] - 2026-03-02

### Fixed
- **BT scan covers all adapters** — scan now sends `select + scan on` for every adapter
  so devices visible only on a secondary adapter (e.g. hci0 while hci1 is busy) are found
- **Adapter auto-selected on Add** — after scan, per-adapter device lists are queried
  within the same bluetoothctl session (before cache is evicted) so the correct adapter
  is pre-filled when clicking Add in scan results
- **Device name from `bluetoothctl info`** — Classic BT devices in pairing mode often
  resolve their name after scan ends; name is now extracted from the post-scan
  `bluetoothctl info` call and used in scan results
- **Audio filter relaxed for pairing-mode devices** — devices with Name but no UUID
  (not yet paired, no profile cache) are now included; only excluded when UUID list
  is present but contains no audio profiles
- **Already Paired filter** — simplified to name-only filter (hides MAC-only entries
  by default); removed HA device registry and audio-class checks

## [1.3.24] - 2026-03-01

### Changed
- **Bridge name field** — removed misleading `auto` hint; placeholder is now `e.g. Living Room`
  (`auto` resolves to the addon slug hostname which is not useful)

### Removed
- **BRIDGE_NAME_SUFFIX** — dead field removed from config form, JS, and backend; the `@ Name`
  suffix has been implicit since v1.3.21 whenever Bridge name is non-empty

### Fixed
- **Server URI display** — rewritten to use `/proc/{pid}/fd` socket inodes + `/proc/net/tcp`
  since `ss` is not available in the container; detects MA's IP from the inbound connection to
  sendspin's listen port
- **Sub-text style** — unified adapter MAC, server URI, and audio format lines via `.ts-sub`
  class (`11px, var(--primary-color)`); removed all hardcoded inline colors

## [1.3.23] - 2026-03-01

### Added
- **BT adapter shown as hciN MAC** — adapter column now displays `hci0 C0:FB:F9:62:D6:9D` format
  instead of user-defined name; hci index resolved by matching effective adapter MAC against
  `bluetoothctl list` output
- **Playback color indicator** — green dot (Playing), yellow dot (Stopped), red dot (No Sink);
  mirrors the BT/Server indicator pattern
- **Playback "Since:" moved above audio format** — more logical reading order
- **Per-device Pause/Unpause button** — ⏸⏸ button in each device's Playback row toggles
  pause/play for that specific player via MPRIS D-Bus; synced with status poll every 2 seconds
- **Pause All ↔ Unpause All toggle** — Pause All button is now stateful; turns blue and shows
  "▶ Unpause All" after pausing; click again to resume all players

### Fixed
- **Unmute All reliability** — `onGroupMute()` now uses the button's own `.muted` class to
  determine current state instead of potentially stale `lastDevices` data, eliminating the race
  condition where clicking quickly would mute again instead of unmuting

## [1.3.22] - 2026-03-01

### Added
- **Pause All button** — new button in the control bar pauses all active Sendspin players via
  MPRIS D-Bus (companion to "Mute All")
- **Actual Bluetooth adapter shown** — device cards now display the real controller MAC even
  when the device uses the default adapter (auto-detected via `bluetoothctl show`)
- **Real server URL** — Server column shows the actual resolved `ws://ip:port/sendspin` instead
  of blank when server is set to `auto`; captured from sendspin output or via `ss` socket lookup
- **Playback "Since:" timestamp** — a "Since: date/time" line appears below Stopped/Playing
  state showing when the current state began

### Changed
- **Audio format display** — removed "Transport: " label prefix; format shows stream details
  only (e.g. `48000Hz/24-bit/2ch` instead of `Transport: flac 48000Hz/24-bit/2ch`)

## [1.3.21] - 2026-03-01

### Fixed
- **Bridge name now works** — setting `bridge_name` appends `@ {name}` to every player name
  visible in MA without needing `bridge_name_suffix`; removed non-functional
  `SENDSPIN_BRIDGE_*` env vars that the sendspin binary silently ignored
- **Volume persists across addon updates** — config now stored in `/data` (HA Supervisor
  persistent volume) instead of ephemeral container filesystem; `LAST_VOLUMES` and device
  `enabled` flags survive container image recreations (addon updates)

## [1.3.20] - 2026-03-01

### Added
- **Graceful pause on shutdown** — on SIGTERM/SIGINT, the bridge now sends an MPRIS `Pause`
  command to every active sendspin player before terminating, so Music Assistant pauses
  the queue cleanly instead of losing the player unexpectedly; waits 500 ms after pausing
  to allow the command to propagate before disconnecting

## [1.3.19] - 2026-03-01

### Added
- **Bridge name identification** — new `BRIDGE_NAME` global config field identifies this bridge
  instance; always updates the MA device info Model field to `BT Bridge @ {name}` when set;
  set to `auto` to use the system hostname automatically
- **Optional player name suffix** — `BRIDGE_NAME_SUFFIX` bool (default off) appends `@ {name}`
  to every player's display name in the MA player list
- **MPRIS Identity service** — when `dbus`/`gi` are available, registers
  `org.mpris.MediaPlayer2.SendspinBridge.*` on the session bus with Identity = effective name

## [1.3.18] - 2026-03-01

### Changed
- **Device card converted to CSS Grid** — identity + 5 status columns share a single grid, so the action row (buttons + track) uses subgrid for pixel-perfect column alignment
- **Delay badge moved to Sync column** — shown in amber below sync detail instead of in the identity section
- **Bluetooth column shows adapter name/MAC** — reads adapter `name` from config and displays `name / MAC` below the "Since:" timestamp
- **Server column shows WebSocket URI** — `ws://host:port/sendspin` in purple below "Since:" timestamp; status text simplified to "Connected"
- **Track/artist moved to action row** — same line as Reconnect/Re-pair/Release buttons, aligned under Playback column via CSS subgrid; single line, full text, 13 px italic

## [1.3.17] - 2026-03-01

### Fixed
- **MPRIS service identity** — D-Bus service name is now always `'Sendspin'` (not the dynamic player name) so MPRIS clients find the correct interface after player restarts

## [1.3.16] - 2026-03-01

### Added
- **MPRIS track/artist metadata via D-Bus** — `sendspin_client.py` exposes `org.mpris.MediaPlayer2.Player` on the session bus; current track title and artist are reflected in MPRIS `Metadata` so media-key applets and home automation can read them

## [1.3.15] - 2026-03-01

### Added
- **Full bidirectional sync** — `listen_host`, `listen_port`, `enabled` fields added to `bluetooth_devices` schema and preserved across Supervisor options sync; adapter `name` preserved similarly
- **Ingress form shows SENDSPIN_PORT** — port input added to the config form and populated from saved config
- **Device card shows artist — track during playback** — `dtrack` element now rendered; delay badge shows when `static_delay_ms ≠ 0`; server status includes connected host:port

## [1.3.14] - 2026-03-01

### Fixed
- **HA addon: release/reclaim state lost on restart** — `entrypoint.sh` now preserves `enabled` flags from the previous `config.json` when regenerating it from `options.json`; device that was released stays released after restart
- **Volume slider shows 100% after restart** — `sendspin_client.py` now pre-fills `status['volume']` from `LAST_VOLUMES` at startup so the UI displays the saved volume immediately, before Bluetooth reconnects

## [1.3.13] - 2026-03-01

### Added
- **HA addon: auto-detect Bluetooth adapters on startup** — `entrypoint.sh` now runs `bluetoothctl list` at startup and populates `BLUETOOTH_ADAPTERS` in `config.json` with discovered adapters (`hci0`, `hci1`, …), merged with any manual entries from `options.bluetooth_adapters`
- **Ingress UI: save auto-detected adapters to native Config tab** — on Save & Restart, all adapters (auto-detected + manual) are included in the Supervisor options POST, so the native HA Config tab «Bluetooth adapters» field is populated automatically after the first save

## [1.3.12] - 2026-03-01

### Fixed
- **HA addon: timezone auto-detect used forbidden Supervisor API** — `/host/info` returns 403 from inside the addon container; HA Supervisor already injects the correct `TZ` env var, so the fallback now uses `os.environ['TZ']` instead of an API call (simpler and always works)

## [1.3.11] - 2026-03-01

### Fixed
- **HA addon: TZ and BLUETOOTH_ADAPTERS not applied** — the `image:` field in `config.yaml` causes HA Supervisor to pull the GHCR image directly, so `ha-addon/run.sh` is never executed; the actual entry point is `entrypoint.sh` which had the old (incomplete) config generation; updated `entrypoint.sh` with TZ auto-detection from Supervisor `/host/info` and `BLUETOOTH_ADAPTERS` support

## [1.3.10] - 2026-03-01

### Fixed
- **HA addon: entrypoint.sh overwrote run.sh config** — `entrypoint.sh` was re-generating `/config/config.json` from `options.json` after `run.sh` had already done so (correctly), stripping `BLUETOOTH_ADAPTERS` and using the raw (empty) `tz` value instead of the auto-detected timezone; fixed by skipping the duplicate config generation in `entrypoint.sh` when `run.sh` already ran it (`HA_ADDON_CONFIG_DONE` env flag)

## [1.3.9] - 2026-03-01

### Added
- **HA addon: Timezone in native Config tab** — new `tz` option in addon schema; leave empty to auto-detect from Home Assistant system timezone via Supervisor `/host/info` API
- **HA addon: Bluetooth adapters in native Config tab** — new `bluetooth_adapters` option (`[{id, mac?}]`) in addon schema; populates adapter dropdowns in the Ingress web UI without opening it first
- **run.sh: timezone auto-detection** — if `tz` is empty, fetches timezone from `http://supervisor/host/info` at startup; falls back to `UTC`
- **web_interface.py: sync tz and bluetooth_adapters** — Ingress UI save now includes `tz` and `bluetooth_adapters` in the Supervisor options POST so settings persist across restarts

## [1.3.8] - 2026-03-01

### Fixed
- **HA addon: config persistence** — saving via the web UI now syncs settings to Supervisor options (`POST /addons/self/options`) so that `run.sh` does not overwrite them on the next container start
- **HA addon: Save & Restart** — restart is now performed via Supervisor API (`POST /addons/self/restart`) instead of `SIGTERM` to PID 1, which was stopping the addon without restarting it
- **HA addon: logs endpoint** — added `Accept: text/plain` header required by Supervisor 2.7+ `advanced_logs_handler` (previously caused HTTP 500)

## [1.3.7] - 2026-03-01

### Changed
- **Web UI redesigned** to match Home Assistant / Music Assistant visual language
  - CSS custom properties (`:root` design tokens) replace all hardcoded colors
  - `@media (prefers-color-scheme: dark)` dark theme with HA dark palette
  - Header styled as HA app-toolbar (`--app-header-background-color`)
  - Primary color changed from purple (`#667eea`) to HA blue (`#03a9f4`)
  - Status/action colors mapped to `--success-color`, `--error-color`, `--warning-color`
  - Cards use `--ha-card-border-radius` (12px) and `--ha-card-box-shadow`
  - Buttons: `border-radius: 4px`, uppercase, HA letter-spacing and font-weight
  - Font changed to Roboto (Google Fonts) with `-apple-system` fallback
  - HA Ingress `setTheme` postMessage listener — live theme injection when opened in HA sidebar

## [1.3.6] - 2026-02-28

### Fixed
- HA addon runtime detection: `_detect_runtime()` now checks `/data/options.json` before falling through to `docker`, preventing `api_logs()` from trying to run `docker logs` inside the addon container
- Logs endpoint in HA addon mode now fetches from Supervisor API (`GET /addons/self/logs`) using `SUPERVISOR_TOKEN`

## [1.3.5] - 2026-02-28

### Fixed
- All `fetch()` calls in the web UI now use `API_BASE` prefix — fixes JSON parse errors when accessed via HA Ingress (where the page URL contains a token path segment and bare `/api/...` resolved against HA Core instead of the addon)

## [1.3.4] - 2026-02-28

### Fixed
- `pipefail` crash in `entrypoint.sh`: `bluetoothctl show | head -10` caused `bluetoothctl` to receive SIGPIPE and exit non-zero under `set -euo pipefail`; suppressed with `|| true`

## [1.3.3] - 2026-02-28

### Fixed
- `entrypoint.sh` now detects HA addon mode via `/data/options.json` and translates it to `/config/config.json` before startup, matching the Docker Compose flow

## [1.2.3] - 2026-02-28

### Changed
- New device default `static_delay_ms` changed from `-500` to `0`
- New devices added via the web UI now have their initial volume set to the current group volume slider value, restored on first service start

## [1.2.2] - 2026-02-28

### Fixed
- Adapter change in config no longer causes "device not paired" reconnect loop on restart — `POST /api/config` now runs `bluetoothctl remove` for devices whose `adapter` field changed or that were deleted, cleaning up stale pairings from the old adapter before the service restarts

## [1.2.1] - 2026-02-28

### Fixed
- Shell injection risk in `_run_bluetoothctl` — replaced string-formatted bash command with stdin pipe
- XSS vulnerability in web UI — HTML attribute positions now use `escHtmlAttr()` instead of `escHtml()`
- `monitor_output` task not cancelled when sendspin process restarts, causing duplicate log readers
- Signal handler used `asyncio.create_task` which could leave orphaned tasks on shutdown
- Per-player audio format cache was a module-level global, causing wrong format shown for second device in multi-device setups
- Removed dead code: `ClientHolder` class and `get_client_instance()` function
- LXC: `module-bluetooth-policy auto_switch=never` added to `pulse-system.pa` — fixes A2DP connection failure for devices that advertise HFP/HSP profiles (e.g. ENEBY Portable); SCO sockets required by HFP are unavailable in LXC kernel namespaces, causing `br-connection-unknown` disconnect before PulseAudio could create the A2DP sink

## [1.2.0] - 2026-02-28

### Added
- **Multi-device support** — bridge multiple Bluetooth speakers simultaneously, each appearing as a separate player in Music Assistant; configure via `BLUETOOTH_DEVICES` array in `config.json`
- **Home Assistant addon** (`ha-addon/`) — native HA addon with Ingress support; web UI appears directly in the HA sidebar
- **Proxmox LXC deployment** (`lxc/`) — fully headless deployment without Docker:
  - `lxc/proxmox-create.sh` — one-command LXC container creation on Proxmox host with Bluetooth D-Bus passthrough and system-mode PulseAudio
  - `lxc/install.sh` — in-container installer for dependencies and systemd units
  - `btctl` wrapper for Bluetooth control via host D-Bus socket
- **Multi-adapter support** — `adapter` field in device config pins a speaker to a specific Bluetooth controller (`hci0`, `hci1`, …)
- **Per-device latency compensation** — `static_delay_ms` field compensates for A2DP + PulseAudio buffer latency (default `-500ms`)
- **Per-device listen port/host** — `listen_port` and `listen_host` fields control per-player Sendspin daemon binding
- **Volume persistence per device** — volume saved per MAC address under `LAST_VOLUMES` in `config.json`, restored on reconnect
- **Group volume/mute controls** — control all players simultaneously from the web UI
- **Reconnect and Re-pair buttons** — per-device controls in the status dashboard
- **Bluetooth scan filtering** — scan results filtered to audio-capable devices only (by BT device class / A2DP UUID)
- **BT adapter management panel** — auto-detect adapters with manual override support
- **`/api/diagnostics` endpoint** — structured health info: adapters, sinks, D-Bus availability, per-device status
- **Audio format display** — codec, sample rate, and bit depth shown in device status cards (e.g. `flac 48000Hz/24-bit/2ch`)
- **Sync status tracking** — re-anchor count and last sync error shown in device cards
- **Timezone autocomplete** — IANA timezone list in configuration UI
- **Per-player WebSocket URL** — displayed in device cards for debugging

### Changed
- `BLUETOOTH_MAC` env var superseded by `BLUETOOTH_DEVICES` array (backward compatible — single MAC still supported)
- `SENDSPIN_NAME` used as player name prefix
- Device info reported to Music Assistant set to `Sendspin / Bluetooth Bridge`
- `PULSE_SINK` set per-process for isolated audio routing per device
- Audio route configured without changing system default sink (per-process via `PULSE_SINK`)
- Removed Player Name Prefix field from configuration UI

### Fixed
- Bluetooth disconnect detection reliability improvements
- `bluetooth_sink_name` not set when sendspin process restarts after unexpected death
- Volume/mute controls disabled when audio sink not yet configured
- Bluetooth `AF_BLUETOOTH` kernel namespace limitation in LXC resolved via host D-Bus bridge
- Playing status detection updated for actual sendspin log output format
- BT scan: stdin kept open so bluetoothctl has time to discover devices
- Volume sync: parse `Server set player volume` log format from Music Assistant
- `LAST_VOLUMES` preserved when saving configuration via web UI

## [1.1.0] - 2026-01-27 (origin: loryanstrant/Sendspin-client)

### Fixed
- Bluetooth connection status monitoring reliability
- Bluetooth disconnect detection with real-time status polling

## [1.0.0] - 2026-01-01 (origin: loryanstrant/Sendspin-client)

### Added
- Initial release: Dockerized Sendspin client with Bluetooth speaker management
- Flask web UI served by Waitress on port 8080
- Auto-reconnect to Bluetooth device every 10 seconds
- PipeWire and PulseAudio sink detection and routing
- Volume sync from Music Assistant to Bluetooth speaker via `pactl`
- mDNS auto-discovery for Music Assistant server (`SENDSPIN_SERVER=auto`)
- Config persistence via `/config/config.json`

[1.3.32]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.31...v1.3.32
[1.3.31]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.30...v1.3.31
[1.3.30]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.29...v1.3.30
[1.3.29]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.28...v1.3.29
[1.3.28]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.27...v1.3.28
[1.3.27]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.26...v1.3.27
[1.3.26]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.25...v1.3.26
[1.3.25]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.24...v1.3.25
[1.3.24]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.23...v1.3.24
[1.3.23]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.22...v1.3.23
[1.3.22]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.21...v1.3.22
[1.3.21]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.20...v1.3.21
[1.3.20]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.19...v1.3.20
[1.3.19]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.18...v1.3.19
[1.3.18]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.17...v1.3.18
[1.3.17]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.16...v1.3.17
[1.3.16]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.15...v1.3.16
[1.3.15]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.14...v1.3.15
[1.3.14]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.13...v1.3.14
[1.3.13]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.12...v1.3.13
[1.3.12]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.11...v1.3.12
[1.3.11]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.10...v1.3.11
[1.3.10]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.9...v1.3.10
[1.3.9]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.8...v1.3.9
[1.3.8]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.7...v1.3.8
[1.3.7]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.6...v1.3.7
[1.3.6]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.5...v1.3.6
[1.3.5]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.4...v1.3.5
[1.3.4]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.3.3...v1.3.4
[1.3.3]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.2.3...v1.3.3
[1.2.3]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/loryanstrant/Sendspin-client/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/loryanstrant/Sendspin-client/releases/tag/v1.0.0
