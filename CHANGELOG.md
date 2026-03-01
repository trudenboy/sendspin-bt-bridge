# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
