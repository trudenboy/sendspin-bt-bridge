# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.1] - 2026-03-05

### Fixed
- **Release/Reclaim button**: button text and state now update immediately in the UI on success — no more waiting for the next SSE tick
- **Released device status**: devices in "Released" state now show "Released" in the BT status indicator instead of "BT Reconnecting…"
- **BT reconnect backoff**: exponential delay between failed reconnect attempts (capped at 5 min) reduces audio interference with other A2DP speakers on the same adapter

## [2.7.0] - 2026-03-05

### Added
- **Keepalive silence stream**: per-device option to periodically send a short silence burst to the BT sink, preventing speakers from auto-disconnecting during silence. Configurable interval (10–300 s, default 30 s) — available in the addon UI

### Improved
- **Graceful shutdown**: all players are now paused via reliable IPC before the addon stops — no more abrupt cutoffs
- **Group-aware pause on BT disconnect**: solo players receive a pause before the daemon stops; grouped players are unaffected so other group members continue playing
- **SSE efficiency**: status notifications batched in 100 ms windows — reduces UI update overhead when many devices change state simultaneously
- **BT reconnect scalability**: thread pool sized to device count; D-Bus connection reused across reconnect cycles

### Fixed
- **Race condition**: `group_id` status read is now thread-safe under `_status_lock`



### Improved
- Web UI: player names now use primary text color (black/white) instead of accent blue
- Docs: web-ui.md rewritten with full feature coverage and screenshots; usage examples added to home page

## [2.6.9] - 2026-03-04

### Fixed
- Concurrent reconnect race: duplicate `configure_bluetooth_audio()` and double daemon spawns on BT reconnect eliminated via threading/asyncio locks
- Duplicate `start_sendspin()` calls dropped when daemon is already starting

### Improved
- Web UI: EQ bars moved beside player name, triggered by playing state
- Web UI: devices sorted by MA sync group within activity level; ungrouped last
- Web UI: slash-separated compilation names truncated to "Artist +N" with full tooltip; long names no longer overflow cards

## [2.6.8] - 2026-03-04

### Fixed
- Pause All: sends one command per MA group, not per client — fixes duplicate signals to grouped players

### Improved
- Web UI: hover-only details (BT MAC, server URI, sink name, WS URL) always visible on touch; icon buttons enlarged to ≥36px; toast full-width on narrow screens

## [2.6.7] - 2026-03-04

### Fixed
- Pause button now works — sends pause/play command to MA via aiosendspin controller; works for solo and group playback
- Track progress bar interpolates client-side between server updates

### Improved
- Web UI: sink name moved to bottom of Volume column, shown on card hover

## [2.6.6] - 2026-03-04

### Improved
- Web UI: track progress bar with `m:ss / m:ss` display in Playback column
- Web UI: BT MAC address and MA server URI hidden by default, revealed on card hover
- Web UI: re-anchor count in Sync column colored amber (>10) or red (>100)

## [2.6.5] - 2026-03-04

### Improved
- Web UI: action buttons (Reconnect / Re-pair / Release) hidden by default, revealed on hover
- Web UI: device cards sorted by activity — Playing first, then BT connected, then disconnected
- Web UI: EQ bars moved inline into the volume row
- Web UI: audio format shown in Volume column; adapter shown as `hciN` only

## [2.6.3] - 2026-03-04

### Fixed
- Sync column no longer gets stuck showing "Re-anchoring" — backend watcher auto-clears state 5 s after last re-anchor log; detection robust to stream restarts that reset the count
- Re-anchor display state resets correctly when device list order changes after a config edit

### Improved
- Web UI: Bluetooth + Server columns merged into Connection; track persists on pause
- Web UI: Inactive cards dimmed; playing cards show green accent border
- Web UI: Relative timestamps (HH:MM instead of full date)
- Web UI: Toast notifications replace browser alert()
- Web UI: Advanced settings collapsed by default (Latency, BT interval, Auto-disable, SBC codec)
- Web UI: Global health indicator in header (N/M playing, disconnected count)
- Web UI: Keyboard shortcuts — R refresh · P pause all · S save config

## [2.6.2] - 2026-03-04

### Fixed
- **Sync delay badge**: `delay: Xms` badge is now gray for offline/non-playing devices, orange only when playing — was showing misleading orange on disconnected devices.
- **Re-anchor transition banner**: After re-anchoring completes, the warning now stays visible at least 3 s before switching to "✓ In sync" — previously the window was shorter than the SSE update interval so the transition was invisible.

## [2.6.1] - 2026-03-04

### Performance
- **Async BT scan**: `POST /api/bt/scan` returns `{job_id}` instantly; poll `GET /api/bt/scan/result/<id>` — no longer blocks web UI for 11–17 s.
- `bluetoothctl info` calls run in parallel (8 workers) during scan.
- BT reconnect poll log messages demoted to DEBUG — reduces log noise by ~720 lines/h per device.

### Security / Stability
- **Thread-safe device status**: Added lock around all status mutations shared between asyncio loop, D-Bus callback thread, and Flask WSGI threads.
- **Adapter cache TOCTOU**: Double-checked locking prevents concurrent WSGI threads from racing on adapter name cache load.

### Fixed
- **Per-device pause button**: Was using Flask process PID instead of daemon subprocess PID — button now correctly pauses/resumes individual devices.
- **asyncio deprecation**: `get_event_loop()` → `get_running_loop()` — no DeprecationWarning in Python 3.12, no RuntimeError in Python 3.14.
- **Daemon stderr captured**: Crashes and library errors written to stderr are now logged as warnings instead of silently discarded.
- **Healthcheck**: Container no longer reported unhealthy when BT speaker is disconnected (normal idle state).

### Improved
- HA config translation extracted from `entrypoint.sh` into `scripts/translate_ha_config.py` (type-checked, linted).
- `SENDSPIN_PORT` now saved as integer (not string) in generated config.
- Volume persistence logic deduplicated; config path unified to single source of truth.
- `DeviceStatus` typed as dataclass — key typos caught at dev time.
- `assert` statements replaced with explicit `RuntimeError` — survives Python `-O` flag.
- Late import in views blueprint replaced with `app.config["AUTH_ENABLED"]`.

## [2.6.0] - 2026-03-05

### Performance
- Real-time status via Server-Sent Events (eliminates polling).
- Daemon crash detection: 20 s → ≤4 s.
- Exponential backoff (1→30 s) on daemon crash-loop.
- Status emission deduplication in daemon subprocess.
- Volume config write debounced (instant pactl, delayed disk write).

### Improved
- Sink name shown under volume slider; ⚠ warning when BT connected but no sink.
- Volume slider fades during in-flight requests.
- BT reconnecting state shows pulsing orange indicator.
- Subprocess status keys whitelisted to prevent unbounded memory growth.



### Security
- Session cookie hardening (SameSite=Lax, HttpOnly).
- Brute-force protection on /login: IP lockout after 5 failures / 60s window.
- Adapter MAC validation added to `/api/config` POST.

### Fixed
- Thread-safety: client list access under concurrent Waitress threads.
- Volume endpoint input validation: returns 400 on invalid value.
- BT scan capped at 50 results to prevent DoS in dense environments.
- Event loop resource leak in `services/pulse.py`.
- `bash -c` removed from `_resolve_adapter_select` (direct subprocess).
- `BRIDGE_NAME_SUFFIX` now applied in player name construction.

## [2.5.6] - 2026-03-04

### Added
- **Group filter for volume/pause**: Filter Group Controls by MA sync group.
- **Timed re-anchor warning**: Sync column shows warning for `abs(delay_ms)` then reverts to count.

### Fixed
- **Format column missing in config table**: `preferred_format` grid column and header added.

## [2.5.5] - 2026-03-04

### Added
- **`preferred_format` per-device config**: Set the preferred audio format to eliminate
  PulseAudio resampling. Default `flac:44100:16:2` matches SBC A2DP native rate.

## [2.5.4] - 2026-03-04

### Fixed
- **Sync status empty in device card**: re-anchor count and reanchoring flag were not
  tracked in subprocess mode. Now intercepted from log messages in real time.

## [2.5.3] - 2026-03-03

### Fixed
- **Track title and artist not shown in device cards**: METADATA role was not registered
  with the server — only PLAYER role was used. Server requires METADATA role to send track info.

## [2.5.2] - 2026-03-03

### Fixed
- **Re-anchoring loop at playback start**: `move-sink-input` was firing on every
  stream re-anchor, causing a feedback loop. Now runs once per stream session only.

## [2.5.1] - 2026-03-03

### Fixed
- **PulseAudio module-rescue-streams**: when a BT sink disappears, PA moves streams
  to the default sink. Added PID-based `move-sink-input` correction on every
  Stream STARTED event to restore correct routing without race conditions.

## [2.5.0] - 2026-03-03

### Changed
- **Subprocess isolation for sink routing**: each speaker now runs in its own subprocess
  with `PULSE_SINK` set in the environment, guaranteeing every audio stream is routed
  to the correct Bluetooth speaker from the first sample — no delays, no move-sink-input.

## [2.4.0] - 2026-03-03

### Changed
- **Proactive sink routing**: replaced reactive `pactl move-sink-input` with `PULSE_SINK`
  set immediately before the PortAudio stream opens. Audio reaches the correct BT speaker
  from the very first sample with zero polling or delay.

## [2.3.6] - 2026-03-03

### Fixed
- **Stale routing tasks on rapid stop/play**: tasks from previous play cycles
  are now cancelled on new stream start, preventing sink-input ID conflicts
  and routing failures under rapid stop/play clicks.

## [2.3.5] - 2026-03-03

### Improved
- **Sink routing latency**: parallel routing — all devices route concurrently
  instead of sequentially. Audio now reaches the correct speaker almost
  instantly on repeated play (zero sleep fast path for known sink-input IDs).

## [2.3.4] - 2026-03-03

### Fixed
- **Sink routing on repeated play (root cause fix)**: routing is now triggered from
  `_on_stream_event("start")`, which fires on every stream activation. Previously it
  relied on `_handle_format_change`, which only fires when the codec/sample-rate changes —
  causing all streams to pile up on the default PipeWire sink on repeated group play.
- Prefer re-claiming own previous sink-input ID to prevent stream stealing between daemons.

## [2.3.3] - 2026-03-03

### Fixed
- **Sink routing on repeated play**: all streams went to the same speaker after the first
  stop/start cycle. Each new group play now correctly routes each stream to its own BT sink.

## [2.3.2] - 2026-03-03

### Fixed
- D-Bus monitor callback: `dbus-fast` requires exactly 3 params — fixed via closure factory
- Log spam eliminated (`reply_notify` error every 10s for all devices)

### Added
- GitHub link in web UI header
- Sidebar navigation on docs homepage

## [2.3.1] - 2026-03-03

### Security
- Open redirect fixed: login `?next=` validated to local paths only

### Fixed
- `asyncio.shield()` misuse in `stop_sendspin()` — always timed out; removed
- `_GLib` never imported — MPRIS identity now works when GLib available
- `_routed_sink_input_id` initialized in `BridgeDaemon.__init__`
- Missing HTTP status codes on error responses (503, 500)
- ha-addon `config.yaml` version reverted — CI auto-syncs on tag push

### Removed
- Dead code: `_detect_server_url_from_proc()`, `self.process`, `read_mpris_metadata_for()`

### Changed
- Regex compiled once at module level (10 patterns in `routes/api.py`)
- Flask 404/500 error handlers with JSON for API routes
- Docs link added to web UI header

## [2.3.0] - 2026-03-03

### Security
- Auth bypass: X-Ingress-Path trusted only from localhost (prevents LAN spoofing)
- Wildcard CORS removed (same-origin only)
- Timing-safe password comparison via `hmac.compare_digest()`
- Config POST: MAC validation, port range check, key whitelist

### Fixed
- PID 1 signals: `init: true` for proper SIGTERM forwarding
- Routing retry: 3 attempts with backoff on `move-sink-input` failure
- Stale claimed sink-inputs pruned before routing
- dbus.mainloop.glib NameError in MPRIS Identity registration
- BT scan process leak on timeout
- Config .tmp cleanup on write failure
- Shell variable quoting in entrypoint.sh

### Changed
- Removed unused thread-safe status methods (dead code)
- Silent exceptions now logged at DEBUG level
- Healthcheck uses WEB_PORT env var

## [2.2.3] - 2026-03-03

### Fixed
- Sink-input dedup: `_claimed_sink_inputs` set + asyncio.Lock prevents two daemons
  from routing the same sink-input

## [2.2.2] - 2026-03-03

### Changed
- Audio routing: replaced null-sink/loopback with `pactl move-sink-input`
  (PipeWire-compatible). Removed ~80 lines of incompatible module-loading code.

## [2.2.0] - 2026-03-03

### Added
- Multi-speaker null-sink routing attempt (failed on PipeWire — superseded by v2.2.2)

## [2.1.8] - 2026-03-03

### Fixed
- Group audio routing: event-driven stream claim replaces fixed 6 s sleep.
  Each daemon's PA stream is explicitly moved to the correct BT sink as soon
  as it appears. Correct for any number of devices; lock released faster.

## [2.1.7] - 2026-03-03

### Fixed
- Group audio routing: PULSE_SINK hold increased to 6 s to ensure correct BT sink assignment
  when Music Assistant connects ~4 s after daemon start

## [2.1.5] - 2026-03-03

### Fixed
- Enabled toggle in bridge UI now syncs to `options.json` so HA config page stays in sync
- Startup sync: device enabled state written to `options.json` on boot (fixes "disabled" display for active devices)
- `entrypoint.sh`: `options.json` is now authoritative for `enabled`; absent field defaults to `true`

## [2.1.4] - 2026-03-03

### Added
- DeviceInfo: `product_name = "Sendspin BT Bridge v2.1.4"`, `manufacturer = hostname`
- Server column always shows host:port; real URL captured from WebSocket on connect
- Group badge shows last UUID segment of group_id (e.g. `855be80925d3`); moved above MAC

## [2.1.3] - 2026-03-03

### Fixed
- **Group audio routing**: each daemon sets `PULSE_SINK` to its BT sink before starting.
  A class-level `asyncio.Lock` serialises startup to prevent PULSE_SINK race conditions
  between concurrent daemon instances (each holds the lock for 3 s while PA stream opens).

## [2.1.2] - 2026-03-03

### Fixed
- Crash `Cannot run the event loop while another loop is running` on daemon restart after BT connect
- `resolve_audio_device_for_sink` is now `async` — uses `await aget_sink_description()` directly

## [2.1.1] - 2026-03-03

### Fixed
- Corrected `pulsectl-asyncio` version constraint from `>=0.8.0,<1.0.0` to `>=1.0.0,<2.0.0`

## [2.1.0] - 2026-03-03

### Added
- `pulsectl-asyncio>=1.0.0,<2.0.0` dependency; new `services/pulse.py` module.

### Changed
- **PulseAudio: migrate from subprocess `pactl` to `pulsectl_asyncio` library.**
  All PA operations (sink discovery, volume, mute, diagnostics) use native API.
  Fixes audio device resolution for group playback. Graceful fallback to `pactl` if unavailable.

## [2.0.6] - 2026-03-03

### Fixed
- Audio device resolution now uses PA sink description (friendly name) via `pactl list sinks`
  to correctly match each BT speaker in sounddevice, fixing group playback routing.

## [2.0.5] - 2026-03-03

### Fixed
- Group badge now shows "🔗 In group" when MA sends `group_id` but no `group_name`
  (MA omits `group_name` in `group/update` messages).

## [2.0.4] - 2026-03-03

### Fixed
- Group playback: each BT device now routes audio to its own speaker. Daemon was
  starting before BT connected (no sink known) → all players used the default device.
  Daemon now restarts after initial BT connect with the correct audio sink.
- `update_status` name collision between sync helper and async monitoring loop; renamed
  async loop to `_status_monitor_loop`.

## [2.0.3] - 2026-03-03

### Fixed
- Track metadata (title/artist) never populated — `_on_metadata_update` receives
  `ServerStatePayload` but was accessing `payload.title` directly instead of
  `payload.metadata.title`. Fixed field access path.

## [2.0.2] - 2026-03-02

### Fixed
- `AttributeError` in metadata callback when receiving `ServerStatePayload` (no `title` attr).
- `_monitor_dbus` infinite retry loop in restricted D-Bus environments (HA container policy);
  now falls back to bluetoothctl polling after 3 consecutive `add match request` failures.

## [2.0.1] - 2026-03-02

### Fixed
- `ImportError: cannot import name 'BluetoothManager'` on container startup — missing
  `class BluetoothManager:` declaration was dropped during D-Bus refactor edit.

## [2.0.0] - 2026-03-02

### Changed
- **D-Bus Bluetooth monitor** — instant disconnect detection via `dbus-fast`
  `PropertiesChanged` signals instead of periodic polling. Falls back to bluetoothctl polling
  if D-Bus unavailable (e.g. restricted container environment).
- `is_device_connected()` / `is_device_paired()` / `disconnect_device()` now use
  BlueZ D-Bus API directly (~10× faster); bluetoothctl retained as fallback.
- **In-process sendspin daemon** — `BridgeDaemon(SendspinDaemon)` subclass replaces
  subprocess + stdout-parsing. All events (play/stop, volume, format, group, metadata)
  delivered via typed callbacks. Removed ~230 lines of parsing code.
- Track metadata delivered instantly via `add_metadata_listener` (eliminates 10 s MPRIS poll lag).
- BT reconnect calls `start_sendspin()` / `stop_sendspin()` instead of process management.

### Added
- `dbus-fast>=2.22.0,<3.0.0` dependency for async D-Bus signal support.
- MA player grouping — device card shows "🔗 group" badge; `group_name` and `group_id`
  tracked in player status.

## [1.6.5] - 2026-03-02

### Fixed
- **Bluetooth "Since:" not shown in device card** — `bluetooth_connected_at`
  was never set on initial connect; fixed by routing through change-detection

## [1.6.4] - 2026-03-02

### Fixed
- **BT check interval and auto-disable not persisted** — `bt_check_interval` and
  `bt_max_reconnect_fails` added to addon schema and now survive restarts

## [1.6.3] - 2026-03-02

### Fixed
- **HA Configuration page: device enabled state not synchronized** — Release/Reclaim
  now immediately syncs `enabled` to HA Supervisor options
- **Configuration page: device enabled state lost on save** — `enabled: false` is
  preserved in the device row and no longer reset on config save

## [1.6.2] - 2026-03-02

### Fixed
- **Configuration page: device enabled state not preserved** — saving configuration
  no longer resets disabled devices to enabled when the status poll hasn't run yet

## [1.6.1] - 2026-03-02

### Fixed
- **Performance: config reads on every request** — `AUTH_ENABLED` cached at startup,
  no longer re-read from disk on every HTTP request

### Changed
- **Mobile UI optimization** — responsive layout at ≤640px with 2-column device cards,
  horizontal scrolling tables, and correctly themed icon buttons

## [1.6.0] - 2026-03-02

### Added
- Optional web UI authentication (`AUTH_ENABLED`); disabled by default
- Set-password form in Configuration panel (PBKDF2-SHA256, no plaintext)
- HA Ingress bypass — auth skipped when accessed via HA Ingress
- HA Supervisor auth integration — validates against HA user database when `AUTH_ENABLED=true`
- Sign out button in page header when auth is active

### Fixed
- BT_CHECK_INTERVAL and BT_MAX_RECONNECT_FAILS not loaded from saved config
- Password hash and secret key preserved across config saves

## [1.5.1] - 2026-03-02

### Added
- BT_CHECK_INTERVAL: configurable BT probe interval (default 10s)
- BT_MAX_RECONNECT_FAILS: auto-disable device after N consecutive failed reconnects (0 = never)

### Fixed
- Configuration section collapsed by default
- Removed Sendspin provider tip disclaimer



### Changed
- Code quality sprint: VERSION/config consolidation, removed `netifaces`, halved bluetoothctl
  subprocess calls, improved HEALTHCHECK, multi-stage Dockerfile, adapter name cache
- Modular architecture: `state.py`, `services/bluetooth.py`, `routes/api.py`, `routes/views.py`;
  `web_interface.py` reduced from ~1 045 to 57 lines



### Fixed
- HA ingress CSS/JS: replaced `before_request` with WSGI middleware — static files now load correctly
- Missing ▶ on Diagnostics collapsible section (broken CSS `::before` rule)
- Collapsible arrow animation for Configuration and Diagnostics sections

## [1.4.1] - 2026-03-02

### Fixed
- Home Assistant ingress: static files (CSS/JS) now load correctly via HA addon panel (`X-Ingress-Path` → Flask `SCRIPT_NAME`)
- Broken emoji on Release/Reclaim buttons (`\U0001F513` → literal 🔓/🔒 in `app.js`)
- Broken triangle in collapsible sections (`'\\25B6'` → `'\25B6'` in `style.css`)

## [1.4.0] - 2026-03-02

### Changed
- Modular refactoring: `config.py`, `mpris.py`, `bluetooth_manager.py` extracted from `sendspin_client.py`
- HTML/CSS/JS moved to `templates/` and `static/`; `web_interface.py` reduced from 2891 to 1107 lines
- Unified `_config_lock` shared across all modules via `config.py`

## [1.3.33] - 2026-03-02

### Fixed
- Shell injection in `pair_device()` — replaced `bash -c` f-string with stdin pipe + MAC validation
- Silent task crashes — broken `add_done_callback` lambdas replaced with named callbacks
- NameError in `main()` — per-device volume pre-fill now works correctly on startup
- Dropped config keys (`LAST_VOLUMES`, `BLUETOOTH_ADAPTERS`, `BRIDGE_NAME_SUFFIX`) on reload
- Premature `server_connected=True` set immediately after process start
- 100% volume blast before saved-volume restore on BT connect
- Blocking `process.wait()` calls in async context wrapped in `run_in_executor()`
- `_pause_all_via_mpris` blocking event loop — converted to sync, called via executor

### Security
- Removed `privileged: true` from Docker compose — `cap_add` is sufficient
- Removed hardcoded developer MAC address from `docker-compose.yml`

### Changed
- Config file writes are now atomic and serialised with `threading.Lock` + `os.replace()`
- Thread-safe status dict via `update_status()` / `get_status()` with `threading.Lock`
- Docker audio paths use `${AUDIO_UID:-1000}` instead of hardcoded UID 1000
- All `bash -c` BT API wrappers replaced with direct stdin pipe calls
- `dbus-python` pinned to `>=1.3.2,<2.0.0`

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
- **Prefer SBC codec** — new `PREFER_SBC_CODEC` config option; when enabled, forces
  the A2DP codec to SBC immediately after each Bluetooth connect via
  `pactl send-message … bluez5/set_codec a2dp_sink SBC` (requires PulseAudio 15+);
  SBC is the simplest mandatory codec and reduces PA encoder CPU load; exposed in
  the web UI config form and HA addon native Config tab
- **LXC CPU-optimal PulseAudio config** — `lxc/pulse-daemon.conf` installed to
  `/etc/pulse/daemon.conf` by `install.sh`; sets `resample-method=trivial`,
  `default-sample-rate=48000` (matches MA output, zero resampling), `default-sample-format=s16le`

## [1.3.26] - 2026-03-02

### Added
- **PULSE_LATENCY_MSEC setting** — configurable PulseAudio buffer latency (default 200 ms);
  increase to 400–600 ms to reduce audio dropouts on slow/overloaded hardware; exposed in
  the web UI config form and HA addon native Config tab

### Fixed
- **MPRIS track per player** — `_read_mpris_metadata_for()` now queries
  `org.mpris.MediaPlayer2.Sendspin.instance{PID}` directly instead of returning
  metadata from the first MPRIS service found; each player now shows its own track

### Changed
- **Sendspin process priority** — launched with `nice -5` so audio threads are scheduled
  ahead of lower-priority background tasks when the system is under load

## [1.3.25] - 2026-03-02

### Fixed
- **BT scan covers all adapters** — scans hci0 and hci1 simultaneously; devices
  only visible on a secondary adapter are no longer missed
- **Adapter auto-selected on Add** — correct adapter pre-filled when adding a
  device from scan results
- **Device name in scan results** — Classic BT devices in pairing mode now show
  their name (resolved via `bluetoothctl info` after scan)
- **Audio filter** — devices with a name but no UUID (pairing mode, unpaired)
  are included; only excluded when non-audio UUIDs are present
- **Already Paired filter** — shows named devices only by default; "Show all"
  checkbox reveals unnamed (MAC-only) entries

## [1.3.24] - 2026-03-01

### Changed
- **Bridge name field** — removed misleading `auto` hint from config form

### Removed
- **BRIDGE_NAME_SUFFIX** — dead config field removed

### Fixed
- **Server URI display** — uses `/proc/net/tcp` instead of missing `ss` tool
- **Sub-text style** — unified via `.ts-sub` class, no hardcoded colors

## [1.3.23] - 2026-03-01

### Added
- **BT adapter shown as hciN MAC** — adapter column now displays `hci0 C0:FB:F9:62:D6:9D` format
- **Playback color indicator** — green/yellow/red dot in Playback column
- **Per-device Pause/Unpause button** — ⏸⏸ button in each device's Playback row
- **Pause All ↔ Unpause All toggle** — Pause All button is now stateful

### Fixed
- **Unmute All reliability** — fixed race condition where clicking quickly would mute again

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
- **Bridge name identification** — new `bridge_name` option identifies this bridge instance in MA
  device info (Model field shows `BT Bridge @ {name}`); set to `auto` for hostname
- **Optional player name suffix** — `bridge_name_suffix` bool (default off) appends `@ {name}`
  to every player's display name in the MA player list
- **MPRIS Identity service** — registers `org.mpris.MediaPlayer2.SendspinBridge.*` on the
  session bus with Identity = effective player name

## [1.3.18] - 2026-03-01

### Changed
- **Device card uses CSS Grid** — action row with buttons and track info is pixel-aligned to the status columns via subgrid
- **Delay badge in Sync column** — delay shown in amber next to sync status instead of in the device name area
- **Bluetooth column shows adapter name/MAC** — adapter identity visible at a glance below the connection timestamp
- **Server column shows WebSocket URI** — full `ws://host:port/sendspin` address displayed in purple
- **Now-playing in action row** — track and artist shown on the same line as buttons, aligned under Playback column; single line, full text

## [1.3.17] - 2026-03-01

### Fixed
- **MPRIS service name** — fixed D-Bus service identity so media-key clients reliably find the player interface

## [1.3.16] - 2026-03-01

### Added
- **MPRIS metadata support** — track title and artist exposed via D-Bus `org.mpris.MediaPlayer2.Player` for integration with media-key applets and home automation

## [1.3.15] - 2026-03-01

### Added
- **Bidirectional config sync** — `listen_host`, `listen_port`, `enabled`, and adapter `name` fields now survive container restarts via Supervisor options round-trip
- **SENDSPIN_PORT in Ingress config form** — port field added and pre-populated from saved config

## [1.3.14] - 2026-03-01

### Fixed
- **Release/reclaim state lost on restart** — released device now stays released after container restart
- **Volume slider shows 100% after restart** — UI now shows the saved volume immediately on startup

## [1.3.13] - 2026-03-01

### Added
- **Auto-detect Bluetooth adapters on startup** — discovers adapters via `bluetoothctl list` at container start; no need to manually configure adapter IDs
- **Native Config tab shows adapters after first Save** — Ingress UI now writes auto-detected adapters back to Supervisor options on save

## [1.3.12] - 2026-03-01

### Fixed
- **Timezone auto-detect** — HA Supervisor injects the correct `TZ` env var into the addon container; use it as fallback instead of calling `/host/info` (which returns 403)

## [1.3.11] - 2026-03-01

### Fixed
- **TZ and Bluetooth adapters not applied at runtime** — the GHCR image entrypoint (`entrypoint.sh`) was missing TZ auto-detection and `BLUETOOTH_ADAPTERS` support; now auto-detects timezone from Supervisor `/host/info` when `tz` is empty

## [1.3.10] - 2026-03-01

### Fixed
- **Config generation conflict** — `entrypoint.sh` was overwriting the config generated by `run.sh`, stripping `BLUETOOTH_ADAPTERS` and reverting timezone to the raw (empty) options value; fixed via `HA_ADDON_CONFIG_DONE` flag

## [1.3.9] - 2026-03-01

### Added
- **Timezone in native Config tab** — new `tz` option; leave empty to auto-detect from Home Assistant system timezone via Supervisor `/host/info` API
- **Bluetooth adapters in native Config tab** — new `bluetooth_adapters` option (`[{id, mac?}]`); populates adapter dropdowns in the Ingress web UI without opening it first
- **Timezone auto-detection** — if `tz` is empty at startup, timezone is fetched from the Supervisor host info; falls back to `UTC`
- **Config sync** — saving via Ingress UI now persists `tz` and `bluetooth_adapters` to Supervisor options

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
