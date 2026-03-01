# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
