# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  only.
- **`no_input_no_output_agent` per-request override in
  `POST /api/bt/pair_new`** — the scan-modal toggle sends this field on
  the pair request; when present, it wins over the persisted
  `EXPERIMENTAL_PAIR_JUST_WORKS` config key. The legacy key is still
  honoured as a fallback for hand-edited config.

### Tests
- `tests/test_ui_experimental_toggles.py` — regression coverage for the
  Settings-page experimental toggles (A2DP sink-recovery dance, PA
  module reload) **and** the scan-modal NoInputNoOutput pair-agent
  toggle: asserts template checkboxes exist under the right
  `data-experimental` container, asserts the Settings toggles are
  wired into `buildConfig` and populate-on-load, and asserts the
  scan-modal toggle is passed as `no_input_no_output_agent` in the
  `pair_new` request body instead of being persisted via
  `buildConfig`. Would have caught the rc.1 omission immediately.
- `tests/test_api_endpoints.py` — three new tests covering the
  per-request override precedence (override beats config both ways) and
  endpoint forwarding of the new body field.

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

## [2.60.4] - 2026-04-21

Dependency bump — pulls in `aiosendspin` 5.1.1 upstream bugfixes. No bridge
behaviour changes.

### Changed
- **`aiosendspin[server]` 5.1.0 → 5.1.1** — upstream patch release. Relevant
  fixes for this bridge:
  - Audio stutter when the player format requires resampling
    ([aiosendspin#219](https://github.com/Sendspin/aiosendspin/pull/219)) —
    matters whenever a BT sink's native rate doesn't match the source.
  - Timestamp drift after extended playback
    ([aiosendspin#217](https://github.com/Sendspin/aiosendspin/pull/217)) —
    matters for multi-hour streaming sessions; helps keep DAC-anchored sync stable.
  - Avoid spurious reconnect when mDNS re-advertises the same endpoint
    ([aiosendspin#216](https://github.com/Sendspin/aiosendspin/pull/216)) —
    reduces churn under `SENDSPIN_SERVER=auto`.

## [2.60.3] - 2026-04-21

Opt-in pair-time adapter quiesce for single-adapter multi-speaker setups.
Follow-up to v2.60.2: addresses the failure mode where the second speaker
on the same adapter never makes it into BlueZ at all (issue #168), which
the v2.60.2 post-`Connect()` workarounds cannot rescue because they only
fire after a successful connect.

### Added
- **#168 — optional pair-time adapter quiesce** — new opt-in "Pause other
  speakers on same adapter" checkbox in the Bluetooth scan modal. When ticked,
  the bridge temporarily pauses reconnect and disconnects every active peer
  sharing the target BT adapter for the duration of the pair operation, then
  restores them automatically (bonds preserved — no unpair). Helps on
  single-adapter setups where a second speaker refuses to pair while the first
  is holding an active A2DP ACL (BlueZ 5.78–5.86 multi-A2DP/legacy-pair
  regression band, Realtek adapter exclusivity quirks). Checkbox is always
  unticked by default — strict opt-in, default pair flow unchanged.

## [2.60.2] - 2026-04-20

Targeted fix for the BlueZ 5.86 dual-role A2DP Sink regression surfaced in #166
(HMDX Jam, IKEA Kallsup and similar smart/TWS/speakerphone devices pair on
HAOS hosts running the regressed BlueZ but never expose a `bluez_card` /
`bluez_sink`, and the link drops ~30 s later). Root cause is tracked upstream
as [bluez/bluez#1922](https://github.com/bluez/bluez/issues/1922) and fixed by
commit `066a164` shipped in BlueZ 5.87 / back-ported to 5.86-4.1.

### Fixed
- **#166 — dual-role speakers pair but no audio sink appears (BlueZ 5.86
  regression)** — `BluetoothManager._connect_device_inner` now issues two
  bridge-side workarounds on every successful connect:
  1. After the generic `Connect()` returns, it explicitly calls
     `Device1.ConnectProfile(A2DP_SINK_UUID)` via the new
     `bt_dbus._dbus_connect_profile` helper so BlueZ is forced to offer the
     sink profile. Cheap no-op on a healthy stack (returns
     `org.bluez.Error.AlreadyConnected`).
  2. If no sink appears after the normal sink-discovery retry loop,
     `_a2dp_recovery_dance` performs one `disconnect → 2 s wait → reconnect`
     cycle; users report the second `Connect()` often registers the profile
     correctly. A per-connect-cycle credit (`_a2dp_dance_remaining = 1`)
     prevents loops.

### Added
- **Startup diagnostics versions** — `entrypoint.sh` now captures and prints
  `Kernel`, `Python`, `BlueZ`, and `Audio Srv` versions in the pre-launch
  diagnostics banner so future bug reports carry these in the first screenshot.
- **Troubleshooting docs (EN + RU)** — new "Speaker pairs but no audio sink
  appears (BlueZ 5.86 regression)" section documents the regression, the
  auto-applied workarounds, and two host-level fallbacks when the bridge-side
  fallbacks are not enough: `DisablePlugins=hfp,hsp` in
  `/etc/bluetooth/main.conf`, and swapping an affected Intel AX200/AX210
  controller for a CSR8510 / Realtek USB dongle.

## [2.60.1] - 2026-04-19

Stable release rolling up the 2.60.0-rc.1 line (on-line config apply, PR #164,
fixes #161) together with a targeted fix for the standalone pair flow surfaced
in #162. Headline themes:

- **On-line config apply** — saving device and global settings from the web UI
  no longer forces a full bridge restart for most fields, so a single delay
  tweak or idle-mode change costs ~0 s of audio interruption instead of the
  8–15 s a cold start used to take.
- **Standalone pair reliability** — the UI's Add+Pair flow now handles legacy
  BT 2.x devices (`LegacyPairing: yes`, e.g. HMDX JAM) and tears down stale
  agent objects before re-pairing so the next attempt actually has an
  authentication agent.
- **Auto-pair reconnect reliability** — the per-device monitor now applies the
  same stale-agent + legacy-PIN handling, and breaks out of the
  `BlueZ has no current device object` reconnect loop (KALLSUP-class) by
  purging the stale BlueZ entry after several consecutive unknown-state
  attempts so the next cycle can escalate to a full re-pair.

### Fixed
- **#162 — `Failed to register agent object` on consecutive pair attempts** —
  `routes/api_bt.py:_run_standalone_pair` now runs `agent off` as part of the
  cleanup phase before the next pair attempt. Without this, an agent object
  lingering on the system bus from a previous bluetoothctl session (or from
  HA Core's own Bluetooth integration) caused `agent on` to fail and produced
  `org.bluez.Error.ConnectionAttemptFailed` on the subsequent `pair` call.
- **#162 — Legacy PIN prompt hangs to timeout** — `_run_standalone_pair` now
  auto-answers `0000` to `Enter PIN code:` / `Enter passkey:` prompts in
  addition to the existing SSP `Confirm passkey` handling. Recovers BT 2.x
  audio sinks (HMDX JAM, IKEA KALLSUP-class) which would otherwise block the
  flow until the 15 s wait deadline.
- **#162 — Auto-pair reconnect path mirrors the standalone fixes** —
  `BluetoothManager.pair_device` now performs the same `agent off` +
  `remove {mac}` cleanup before its bluetoothctl session and auto-answers
  legacy `Enter PIN code:` / `Enter passkey:` prompts with `0000`. Brings the
  per-device monitor's auto-pair flow to parity with the UI Add+Pair flow.
- **#162 — `BlueZ has no current device object` reconnect loop** —
  `BluetoothManager.connect_device` now tracks consecutive failures where
  `is_device_paired()` returns `None` (BlueZ cache is missing the device).
  After three such attempts it forces `bluetoothctl remove {mac}` to clear
  the stale entry and surfaces an actionable `last_error` so the next
  reconnect cycle can escalate to `pair_device` instead of looping
  `Failed to connect (not connected after 5 status checks)` indefinitely.

### Changed
- **`static_delay_ms` default for new devices is now 300 ms** —
  `static/app.js:addBtDeviceRow` pre-fills the delay field with `300` when a
  device is added via the manual "Add device" button, the scan modal, or the
  paired-device list. Existing saved configs are not touched: only rows that
  arrive without an explicit `static_delay_ms` get the new default. Reflects
  field reports that 300 ms gives noticeably better A/V sync on Ubuntu +
  PipeWire two-speaker setups than the previous `0` baseline. The dirty-tracking
  baseline in `_defaultBtDeviceDirtyFields` is updated to match so freshly
  added rows aren't flagged dirty before the user touches them.

### Added
- **On-line config apply for `POST /api/config`** — saving changes in the web UI
  no longer requires a bridge restart for most fields. A new pure diff layer
  (`services/config_diff.py`) compares the previous and new config and emits an
  ordered list of `ReconfigAction`s classified as:
  - `HOT_APPLY` — applied in-place via IPC or parent-level field update for
    per-device fields `static_delay_ms`, `idle_mode`, `idle_disconnect_minutes`,
    `power_save_delay_minutes`, `keepalive_enabled`, `keepalive_interval`,
    `room_id`, `room_name`.
  - `WARM_RESTART` — single subprocess `stop_sendspin()` + `_start_sendspin_inner()`
    (~3–5 s silence on that speaker only) for `player_name`, `listen_port`,
    `listen_host`, `preferred_format`, `adapter`, `volume_controller`.
  - `GLOBAL_BROADCAST` — fan-out IPC for `LOG_LEVEL`, `VOLUME_VIA_MA`,
    `MUTE_VIA_MA`, `MA_API_URL`, `MA_API_TOKEN`, `HA_AREA_NAME_ASSIST_ENABLED`,
    `HA_ADAPTER_AREA_MAP`, `MA_AUTO_SILENT_AUTH`, `MA_WEBSOCKET_MONITOR`,
    `DUPLICATE_DEVICE_CHECK`.
  - `GLOBAL_RESTART` — warm-restart every running client for
    `SENDSPIN_SERVER`, `SENDSPIN_PORT`, `BRIDGE_NAME`, `PULSE_LATENCY_MSEC`,
    `PREFER_SBC_CODEC`, `BT_CHECK_INTERVAL`, `BT_MAX_RECONNECT_FAILS`,
    `BT_CHURN_THRESHOLD`, `BT_CHURN_WINDOW`, `DISABLE_PA_RESCUE_STREAMS`,
    `BASE_LISTEN_PORT`.
  - `RESTART_REQUIRED` — Flask-bound fields (`WEB_PORT`, `AUTH_*`,
    `SECRET_KEY`, `SESSION_TIMEOUT_HOURS`, brute-force limits, `TRUSTED_PROXIES`,
    `TZ`) still need a full bridge restart and are surfaced in the response
    so the UI can prompt.
- **`services/reconfig_orchestrator.py`** — dispatches the action list from the
  Flask request thread onto the asyncio loop via `run_coroutine_threadsafe`.
  Hot-apply waits synchronously (500 ms cap) so the HTTP response returns
  quickly; warm restarts are fire-and-forget.
- **`SendspinClient.apply_hot_config()` / `warm_restart()`** — parent-side
  orchestration methods. `warm_restart` flips `status.reloading=True` (exposed
  over SSE/`/api/status`) and preserves the bridge suffix (`" @ {bridge}"`) when
  `player_name` changes.
- **New daemon IPC command `set_static_delay_ms`** in
  `services/daemon_process.py` — delegates to the already-available
  `aiosendspin.client.set_static_delay_ms()` so the delay updates mid-stream
  without a subprocess restart. Fixes #161.
- **`reconfig` summary in save response** — `POST /api/config` now returns a
  `reconfig` object (`hot`, `warm_restarting`, `global_broadcast`,
  `global_restart`, `restart_required`, `bt_removed`, `errors`). The web UI
  renders a detailed toast (✓ applied, ↻ restarting, ⚠ restart required)
  instead of the blanket "Configuration saved — restart to apply" banner.
- **Tests** — `tests/test_config_diff.py` (29 cases covering per-field
  classification, ordering, and normalization) and
  `tests/test_reconfigure_client.py` (10 cases covering IPC envelopes, warm
  restart stop/start order, reloading flag cleanup on failure, and bridge
  suffix preservation on rename).

## [2.59.1] - 2026-04-18

Targeted fix for the AKG Y500 / BlueZ 5.82 A2DP profile regression (#159) where
a headset connects successfully but no `bluez_*` PulseAudio sink is ever
exposed — root cause is the card landing in `headset_head_unit` profile with
`a2dp_sink` never activating.

### Added
- **`pactl list cards` in diagnostics** — `services/pulse.py` gains
  `list_cards()` / `set_card_profile()` (pulsectl_asyncio fast-path +
  `pactl` subprocess fallback). `routes/api_status.py:/api/diagnostics`
  includes a new `cards` array (name, driver, active_profile, profiles)
  and the bugreport text gains a `--- PA CARDS ---` section so operators
  can see at a glance whether a BT card is stuck in the wrong profile.

### Fixed
- **Auto-switch BlueZ card to `a2dp_sink` on sink-discovery failure** —
  `bt_audio.configure_bluetooth_audio()` now, after the normal retry loop
  fails, checks `list_cards()` for a `bluez_card.{MAC}` entry with a
  non-a2dp active profile and `a2dp_sink` available, calls
  `set_card_profile(card, "a2dp_sink")`, waits one more cycle, and retries
  sink lookup. Recovers audio automatically for devices affected by the
  BlueZ 5.82/5.83 A2DP negotiation regression without requiring manual
  `pactl set-card-profile` intervention.

## [2.59.0] - 2026-04-17

Stable rollup of the rc.1 → rc.2 series. Headline themes: **operational
resilience** for PulseAudio and port-collision failure modes surfaced on
Raspberry Pi 4 / pipewire-pulse deployments, and a **CSP nonce-only
migration** completing the hardening tracked as a known issue in 2.58.0.

### Security
- **CSP `script-src` is nonce-only** — `'unsafe-inline'` dropped. Every inline
  `on*=` handler in Jinja templates and HTML strings produced by
  `static/app.js` migrated to a delegated dispatcher keyed on `data-action` /
  `data-arg`. Non-bubbling `<details>` toggle events handled via capture-phase
  listener to cover dynamically inserted DOM. Regression test scans shipped
  templates and `app.js` so future PRs can't reintroduce inline handlers.

### Added
- **`services/port_bind_probe.py`** — `is_port_available()` +
  `find_available_bind_port()` host-side TCP bind probe (SO_REUSEADDR only).
  `DEFAULT_MAX_ATTEMPTS=10`.
- **Port auto-shift on EADDRINUSE** — `SendspinClient._start_sendspin_inner`
  preflights the listen port before spawning the daemon subprocess; on
  collision auto-shifts within `DEFAULT_MAX_ATTEMPTS` and records
  `port_collision: True` + `active_listen_port` on device status. After 5
  consecutive bind failures the restart loop halts (with an `lsof -i :<port>`
  hint); halt state auto-clears once the daemon is observed alive.
- **Preflight port-collision warning** at orchestrator startup
  (`bridge_orchestrator.py`).
- **EADDRINUSE stderr classifier** — `services/subprocess_stderr.py` detects
  `errno 98` / `address already in use` / `eaddrinuse` and extracts the port
  (1–65535) so the surfaced hint names the actual port.

### Fixed
- **#156 — SinkMonitor log flood**: `services/sink_monitor.py` now diagnoses
  the PA connection failure (`socket-missing` / `permission-denied` /
  `server-not-listening` / `protocol-error` / `unknown`) with an actionable
  hint on the first WARNING, demotes subsequent attempts to DEBUG, and
  self-disables after 3 consecutive initial failures so callers fall back to
  daemon-flag idle detection. Post-success transients use exponential backoff
  5→10→20→40→60s. `start()` resets state so the monitor can be revived after
  the operator fixes PA.
- **#157 — daemon crash on port collision**: see "Port auto-shift" above.

### Notes
- `find_available_bind_port()` is called with `host="0.0.0.0"` (wildcard) to
  match the daemon's actual bind behaviour — the subprocess receives only
  `listen_port` (no `listen_host`), so probing a specific interface would miss
  collisions on other interfaces.

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

## [2.58.0] - 2026-04-17

Stable rollup of the rc.1 → rc.5 series. Headline theme: **multi-adapter correctness** across every Bluetooth flow the UI exposes, plus a security-hardening pass on the MA auth surface.

### Security
- SSRF guard on all MA auth routes (`/api/ma/login`, `/api/ma/ha-auth-page`, `/api/ma/ha-silent-auth`, `/api/ma/ha-login`) via `services.url_safety.is_safe_external_url`; `SENDSPIN_STRICT_SSRF=1` opts into stricter loopback/RFC1918 rejection
- DNS-rebinding defence on outbound HTTP (`safe_urlopen` / `safe_build_opener` re-check `socket.getpeername()` after connect)
- XSS hardening on `/api/ma/ha-auth-page` — `ma_url` is `</` → `<\\/` escaped before inline-script injection
- MA-reported `ha_url` is re-validated through `is_safe_external_url` before the server-side OAuth exchange
- Session-bound MFA state — `/api/ma/ha-login` step 2 no longer trusts `ha_url`/`client_id`/`flow_id`/`state` from the request body; only `session["_ha_oauth"]` is authoritative
- Supervisor fallback is opt-in (`ALLOW_SUPERVISOR_FALLBACK=1`) and logs a `WARNING` with "does NOT verify MFA" on each use
- Logout hardened — `POST /logout` requires CSRF and does a full `session.clear()`; `GET /logout` returns 405 with a small HTML page
- X-Forwarded-For hardening — rate-limit client ID now uses the rightmost hop that is *not* a trusted proxy
- X-Frame-Options: SAMEORIGIN in standalone mode; HA-addon mode keeps it off for Ingress framing

### Added
- **Multi-adapter paired-device management** — `/api/bt/paired` enumerates every adapter via `list_bt_adapters()` and merges results so each device carries `adapters: [<mac>, ...]`. Bonds on a non-default controller are now visible in the UI for the first time
- **Per-adapter unpair** — `/api/bt/remove` accepts optional `adapter_mac`; the "Already paired" list renders an `hciN`/MAC badge per device
- **Targeted "enable-linger" hint for headless PipeWire** — preflight audio probe distinguishes "socket path not mounted" from "socket mounted but server refused the connection", and the latter surfaces a dedicated operator-guidance issue with the exact fix (`sudo loginctl enable-linger <user>` + reboot) and a docs link. Gated by `is_ha_addon_runtime()` so HA add-on users (where Supervisor owns audio) still see the generic guidance (fixes #151)

### Fixed
- **Reset & Reconnect now honours the adapter the device is bonded with** — `/api/bt/reset_reconnect` always threaded `select <adapter>` through remove/power-cycle/pair/trust/connect, but both UI call sites invoked `resetAndReconnect` without an adapter, so bonds on a non-default radio could never be rebuilt through the UI. Fleet row now reads `.bt-adapter`; the "Already paired" list passes `d.adapters[0]`. The backend resolves `hciN` → controller MAC before any `bluetoothctl select` (HAOS/LXC reject raw `select hciN` with `Controller hciN not available`)
- **"Add & Pair" now remembers the adapter the scan used** — two layered bugs: frontend `btAdapterOptions` never matched the scan-supplied controller MAC against `a.id` (always `hciN`), so the new fleet row's `<select>` defaulted to "default"; backend `_run_standalone_pair` passed the raw adapter straight to `bluetoothctl select` and silently ran the pair on the BlueZ default controller. Dropdown now matches on both `a.id` and `a.mac`; pair backend resolves `hciN` → MAC via `_resolve_adapter_to_mac`
- **BT Info modal shows only the MAC for devices on the non-default controller** — `_get_bt_device_info` ran `bluetoothctl info` with no `select`, so Yandex mini 2 on `hci1` returned `Device … not available` and the modal fell back to the MAC-only field. The helper now accepts an adapter (resolving `hciN`), and both UI call sites forward it. When no adapter is supplied, every known controller is probed and the first response with real device fields wins — legacy call sites still work
- **"Already paired" list no longer lists ghost devices** — interactive `bluetoothctl` interleaves async discovery notifications (`[CHG]`/`[NEW]`/`[DEL] Device <mac> RSSI: …`) into the same stdout we pipe `devices Paired` through, so every nearby BLE beacon appeared as "paired" even when `bluetoothctl info` reported `Paired: no`. `_parse_paired_stdout` now strips the prompt echo and accepts only bare `Device <mac> <name>` lines
- **Preflight audio reachability is now measured by a real probe** — the previous implementation relied on `services.pulse.get_server_name()` raising on connect failure, but that helper swallows connect errors and returns `"not available"`. The preflight now performs an explicit `AF_UNIX` connect to the `PULSE_SERVER` socket: `ConnectionRefusedError` → `unreachable` (linger-specific guidance), `PermissionError`/other `OSError` → generic audio failure with the real error text
- **500 handler no longer redirects** — `_handle_500` returns plain text instead of `redirect("/")`, eliminating a potential redirect loop when `/` itself is failing
- **Subprocess stdout stall protection** — `SendspinClient._read_subprocess_output` now wraps `stdout.readline()` in `asyncio.wait_for(timeout=120)`, so a silent-but-alive daemon no longer leaves the reader task blocked forever

### Known issues
- CSP still ships with `'unsafe-inline'` because several templates use inline `onclick` handlers. The nonce plumbing is in place; full migration is tracked for a follow-up minor release

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

## [2.57.1] - 2026-04-16

### Fixed
- **HA addon fails to start after upgrade from 2.56.x** — Supervisor rejects existing negative `static_delay_ms` values (e.g. −300) against the new `int(0,5000)` schema. Relaxed HA schema to `int?` so the addon can start; `translate_ha_config.py` clamps to [0, 5000] at container startup
- **Crash on shutdown (`Event loop stopped before Future completed`)** — signal handler called `loop.stop()` which broke `asyncio.run()`. Replaced with task cancellation for clean exit
- **Daemon artwork_url not proxied** — raw cross-origin MA artwork URLs from the daemon subprocess are now wrapped via `build_artwork_proxy_url()` at the parent IPC boundary, making them same-origin safe under HA Ingress without relying on the MA fallback
- **`static_delay_ms` validation hardened** — config migration now handles >5000 (clamp) and non-numeric (remove) values; env var `SENDSPIN_STATIC_DELAY_MS` validated with try/except and [0, 5000] clamp; HA options translator clamps full range
- **Removed unused `delay_ms` from config schema** — only `static_delay_ms` is used by the codebase; the stale `delay_ms` field could silently pass validation but was ignored at runtime

## [2.57.0] - 2026-04-16

### Changed
- **Upgrade sendspin 5.9.0 → 7.0.0 and aiosendspin 4.4.0 → 5.1.0** — DAC-anchored sync (#226) auto-compensates for audio hardware latency, remote per-player delay (#185), multi-server daemon support, and several playback bugfixes
- **`static_delay_ms` now accepts only 0–5000 ms** — negative values are no longer valid. DAC-anchored sync removes the need for the old large negative offsets (−300…−600 ms). Existing negative values are auto-migrated to `0` on first load; re-tune with small positive values (e.g. 50 ms) only if needed
- Default `SENDSPIN_STATIC_DELAY_MS` environment variable changed from `-300` to `0`
- Config schema version bumped to 2 (auto-migrated from v1)
- **numpy upgraded to 2.x** — amd64 CPU baseline now requires X86_V2 (SSE3 / SSSE3 / SSE4.1 / SSE4.2). Hosts running on QEMU `cpu: qemu64` or `kvm64` fail at startup with `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`. Switch the VM CPU type to `host` (Proxmox: `qm set <vmid> --cpu host`) or a modern named model (`Haswell`, `Skylake-Client`, …)
- armv7 image may compile numpy from source on first build (piwheels has no cp312 wheels for numpy 2.x); subsequent releases reuse the cached layer

### Fixed
- **Album artwork not rendering under HA Ingress** — daemon-reported `artwork_url` points directly at the MA server and fails the same-origin check under Ingress; UI now falls back to the signed same-origin MA proxy URL when a device has MA context
- **Migration warning log spam every 15 s** — HA Supervisor rewrites `config.json` from `options.json` on each poll, so pre-existing negative `static_delay_ms` triggered the clamp warning repeatedly. Warnings are now deduplicated per MAC per process, and the options.json → config.json translator clamps negatives at source

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

## [2.56.3] - 2026-04-14

### Fixed
- **Docker build failing on amd64/arm64** — `aiosendspin-mpris` (transitive dep of sendspin) was missing because `--no-deps` blocked pip from resolving it. Now `--no-deps` is only used for armv7 where all deps are listed explicitly

## [2.56.2] - 2026-04-14

### Fixed
- **armv7 Docker build timing out in CI** — split Dockerfile pip install into two layers so heavy native deps (numpy, PyAV, dbus-python) are cached across releases and not recompiled on every version bump. Added piwheels.org as extra index for pre-built ARM wheels. Increased armv7 build timeout from 45 to 90 min for cold builds

## [2.56.1] - 2026-04-13

### Fixed
- **Sourceplugin metadata mixing MA data from wrong track** — when daemon provides track title but not artist/album/artwork (typical for sourceplugin/ynison), the UI was falling back to MA now-playing for those fields, showing metadata from a completely different song. Now suppresses MA fallback for artist, album, and artwork when daemon already has a track title, preventing cross-track metadata mixing

## [2.56.1-rc.1] - 2026-04-13

### Fixed
- **Sourceplugin metadata mixing MA data from wrong track** — when daemon provides track title but not artist/album/artwork (typical for sourceplugin/ynison), the UI was falling back to MA now-playing for those fields, showing metadata from a completely different song. Now suppresses MA fallback for artist, album, and artwork when daemon already has a track title, preventing cross-track metadata mixing

## [2.56.0] - 2026-04-13

### Fixed
- **HA addon ingress port conflict with Matter/Thread** (#138) — switched all addon channels from hardcoded `ingress_port` (8080/8081/8082) to dynamic `ingress_port: 0`. HA Supervisor now auto-assigns a free port via its REST API, eliminating conflicts with other host-network addons. Channel defaults retained as fallback for older Supervisor versions
- **Incorrect track metadata with sourceplugin providers** — when playing via sourceplugin (e.g. Yandex ynison), MA now-playing returned metadata from its own queue item instead of the actual playing track. Changed metadata priority to daemon-first with MA fallback, matching the existing correct behavior in list view. Affects track title, artist, album, and artwork in all views

## [2.56.0-rc.3] - 2026-04-13

### Fixed
- **HA addon 502 on ingress** — `INGRESS_PORT` is not an env var; Supervisor communicates the dynamic port via its REST API. Replaced env var lookup with Supervisor API query (`/addons/self/info`) to read the assigned `ingress_port`

## [2.56.0-rc.2] - 2026-04-13

### Fixed
- **Incorrect track metadata with sourceplugin providers** — when playing via sourceplugin (e.g. Yandex ynison), MA now-playing returned metadata from its own queue item instead of the actual playing track. Changed metadata priority in `_getDeviceNowPlayingState()` and `_getListTrackAlbum()` to daemon-first with MA fallback, matching the existing correct behavior in list view. Affects track title, artist, album, and artwork in all expanded/card views

## [2.56.0-rc.1] - 2026-04-13

### Fixed
- **HA addon ingress port conflict with Matter/Thread** (#138) — switched all addon channels from hardcoded `ingress_port` (8080/8081/8082) to dynamic `ingress_port: 0`. HA Supervisor now auto-assigns a free port, eliminating conflicts with other host-network addons. Channel defaults retained as fallback for older Supervisor versions

## [2.55.3] - 2026-04-09

### Added
- **Sink mute detection and recovery** (#123) — four-layer defense against system-level PA sink mute going unnoticed:
  - **UI warning**: device card shows orange "Sink muted" status with blocked hint and one-click "Unmute speaker" action when PA sink is muted but app-level mute is off
  - **Health degradation**: `compute_device_health_state()` reports `degraded` state with `sink_muted_at_system_level` reason; new `SINK_MUTED`/`SINK_UNMUTED` device events
  - **Recovery guidance**: new `sink_system_muted` issue in operator recovery assistant with "Unmute speaker" primary action; new `/api/unmute_sink` endpoint for direct PA sink unmute bypassing MA routing
  - **Auto-unmute safety net**: parent-side watchdog auto-unmutes after 30 s grace period if desync persists; daemon startup unmute retries up to 3× on failure

### Fixed
- **Mute immediately overridden by reconnect unmute sync** — `_sync_unmute_to_ma()` fired on every `sink_muted=False` status update, not just after subprocess (re)start. When mute was routed through MA, the PA sink stayed unmuted, triggering the sync which instantly reversed the user's mute. Added `_pending_reconnect_unmute_sync` flag that is set on subprocess start and consumed after the first sync
- **Auto-released devices show "Reconnect" instead of "Reclaim"** — when a device was auto-released after repeated BT connection failures, the `play_pause` and `queue_control` capability blocked reasons incorrectly recommended `reconnect` instead of `toggle_bt_management`. The device card action buttons also showed a disabled "Reconnect" instead of a "Reclaim" button
- **Grid view action button not switching to Reclaim** — the SSE update logic for grid-view cards now dynamically swaps between Reconnect/Reclaim based on `bt_management_enabled` state

## [2.55.2-rc.1] - 2026-04-07

### Fixed
- **Connection errors not surfaced in UI** (#134) — `ClientConnectorError` from daemon subprocess was logged as WARNING but never shown in device status. Added `_connection_watchdog()` in BridgeDaemon (sets `last_error` after 30 s) and consecutive error counter in `SubprocessStderrService` (surfaces after 3+ repeated failures)
- **Generic "lost bridge transport" guidance for port mismatch** (#134) — when transport is down due to connection errors, recovery assistant now shows specific `sendspin_port_unreachable` issue with guidance to check `SENDSPIN_PORT`, instead of generic "restart" advice
- **Stale metadata reconnect timeout too short** (#134) — increased `_STALE_RECONNECT_READY_TIMEOUT` from 30 s to 120 s; added retrigger task that fires reconnect once daemon eventually connects, preventing permanent volume control loss

### Added
- **Sendspin port auto-probe** (#134) — when `SENDSPIN_PORT` is default (9000) and the configured host is explicit, the bridge now TCP-probes candidate ports (9000, 8927, 8095) before connecting. If an alternative port responds, it is used automatically with a WARNING log

## [2.55.1] - 2026-04-06

### Fixed
- **WirePlumber `with-logind` endpoint churn** (#133) — on headless PipeWire systems, WirePlumber's logind integration continuously re-registers and unregisters A2DP media endpoints (~every 10 s), preventing any Bluetooth connection from stabilizing. The bridge now detects this condition and logs the fix: create `~/.config/wireplumber/bluetooth.lua.d/51-disable-logind.lua` with `bluez_monitor.properties["with-logind"] = false`
- **Sink discovery timeout too short** (#133) — increased default from 9 s to 15 s (5 retries × 3 s); configurable via `SINK_RETRY_COUNT` env var for systems where WirePlumber starts slowly after reboot
- **Event loop blocked during sink discovery** (#133) — `configure_bluetooth_audio()` was called synchronously inside the async event loop, blocking it for up to 9 s and causing `Cannot run the event loop while another loop is running` on BT reconnect; now offloaded to `run_in_executor()`
- **Silent sink failure on headless PipeWire** (#133) — when sink discovery fails and PipeWire is detected with no Bluetooth sinks visible, the bridge now logs a targeted warning identifying WirePlumber as the likely missing component and suggesting `loginctl enable-linger`

### Improved
- **WirePlumber diagnostics** — `_is_wireplumber_logind_active()` reads WirePlumber config files to detect when `with-logind` is enabled without a user override, and `_warn_wireplumber_logind()` emits actionable remediation
- **Docker troubleshooting docs** — added "WirePlumber `with-logind` endpoint churn" and "Headless PipeWire: Bluetooth sinks not appearing after reboot" troubleshooting sections with diagnostic steps and fix instructions

## [2.55.0] - 2026-04-06

### Added
- **Per-device idle mode** — new `idle_mode` enum replaces the old dual `keepalive_interval` + `idle_disconnect_minutes` settings. Four mutually-exclusive modes per device:
  - **`default`** — no action; speaker's hardware timer decides when to sleep
  - **`power_save`** — suspend PulseAudio sink after configurable delay → releases A2DP transport → speaker enters hardware sleep while Bluetooth stays connected → instant resume on next play (no reconnect latency)
  - **`auto_disconnect`** — full Bluetooth disconnect + daemon→null-sink after timeout (original standby behavior)
  - **`keep_alive`** — stream periodic infrasound bursts (2 Hz sine at −50 dB) to keep A2DP transport active; below human hearing but prevents speakers that ignore digital silence from disconnecting
- **PulseAudio sink suspend/resume** — new `asuspend_sink()` / `suspend_sink()` helpers in `services/pulse.py` for power save mode; native pulsectl API with automatic `pactl` fallback
- **SinkMonitor idle detection** — PulseAudio/PipeWire sink state events (`running` → `idle` → `suspended`) are now the primary idle detection authority; daemon playback flags serve as dual-authority fallback for PipeWire environments that don't emit sink events for BT sinks
- **WebSocket heartbeat keepalive** — 30-second ping/pong on Sendspin server-initiated connections prevents silent proxy/firewall drops

### Changed
- **Idle parameter UI redesigned** — single "Idle mode" dropdown replaces two independent numeric inputs; selecting a mode reveals only the relevant sub-field (suspend delay / disconnect timeout / keepalive interval)
- **Power save delay in minutes** — `power_save_delay_seconds` renamed to `power_save_delay_minutes` (1–60 min range, default: 1 min); existing configs auto-migrated on startup
- **Docker image −51%** (916 → ~450 MB) — removed redundant system FFmpeg on amd64/arm64 (PyAV bundles its own); force-remove transitive codec/GStreamer deps; strip .so debug symbols; clean unused Python stdlib
- **Unified branding** — all logos, favicons, and HA addon icons replaced with the landing page wave-bridge design (two pillars + three wave curves); channel color differentiation preserved (stable=teal-purple, rc=gold, beta=red); total asset size reduced from ~310 KB to ~55 KB
- **Dependency updates** — `dbus-fast` 4.0.0→4.0.4, `ruff` 0.11.13→0.15.8
- **CI updates** — `docker/build-push-action` v6→v7, `actions/download-artifact` v4→v8, `actions/upload-pages-artifact` v3→v4

### Fixed
- **Idle standby during active playback** (#120) — dual-authority model (SinkMonitor + daemon flags) prevents false standby on PipeWire where PA sink events may not be delivered for BT sinks; firing-time safety net checks all sources before entering standby
- **NumPy crash on older CPUs** — pin `numpy<2.0`; numpy 2.x requires X86_V2 baseline (POPCNT/SSE4.2) unavailable on QEMU `qemu64` and older CPUs
- **Subprocess crash on PipeWire** — keep `libasound2-plugins` (ALSA→PulseAudio bridge) required by sounddevice/PortAudio
- **Config download 404 in HA addon ingress mode** — use `API_BASE` prefix for download URLs
- **Idle mode dropdown unstyled** — added CSS rules matching existing input styling

### Improved
- **Auto-expand device detail row on CTA navigation** — clicking "Configure" from onboarding auto-expands the target device row

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

## [2.54.2] - 2026-04-06

### Fixed
- **Idle standby on PipeWire despite active playback** — PipeWire's PulseAudio compatibility layer does not emit sink state change events for Bluetooth sinks, so the SinkMonitor never fired `on_active` to cancel the idle timer. Daemon playback flags (`playing`/`audio_streaming`) now unconditionally participate in idle timer management alongside SinkMonitor callbacks, acting as a dual authority. This also fixes the reverse: when playback stops on PipeWire, the idle timer now correctly starts even though the SinkMonitor never saw the `running→idle` transition (#120)
- **Mute desync after BT reconnect** — after Bluetooth reconnect, the daemon unmuted the PulseAudio sink but never notified Music Assistant, leaving MA stuck on `muted=true` while audio was playing normally. The parent process now detects `sink_muted→false` transitions and forwards the unmute to MA via `players/cmd/volume_mute` when `MUTE_VIA_MA` is enabled (#132)
- **False idle standby during active playback** — two fixes to prevent the bridge entering standby while music is playing (#120):
  - `_sink_monitor_active()` no longer requires the BT sink name to be discovered — the fallback daemon-flag timer is suppressed as soon as the PA monitor loop is running, closing a race window where `server_connected=True` could start the timer before sink registration
  - `_idle_timeout()` safety guard now checks daemon `playing`/`audio_streaming` flags as a secondary safety net after the PA sink state check; enhanced diagnostic logging on standby entry

## [2.54.1] - 2026-04-04

### Fixed
- **Process hangs after restart** — signal handler ran `graceful_shutdown()` but never stopped the asyncio event loop; the process stayed alive in a "shutdown complete" state with no devices while S6/Docker thought it was still healthy. Now calls `loop.stop()` after shutdown so the process actually exits and gets restarted.
- **MUTE_VIA_MA default changed to `true`** — mute commands from the bridge web UI now route through the MA API by default so Music Assistant UI stays in sync (#132)
- **Bluetooth soft-blocked on Raspberry Pi** — entrypoint now runs `rfkill unblock bluetooth` automatically at startup so the on-board adapter works without manual intervention
- **Mobile action buttons overflow** — expanded device card kept the desktop 2-column grid on mobile when dark mode was applied via class instead of OS preference; action buttons were squeezed into a narrow column and text was truncated. Moved missing layout overrides to the top-level ≤640 px breakpoint and added `text-overflow: ellipsis` safety net on button labels.

### Improved
- **Update modal: copyable Docker image** — the Docker image name is now displayed as a separate copyable code block below the instruction text instead of being buried inline; **docker-compose.yml** is bold for visibility.

### Added
- **Built-in adapter docs** — new "Built-in adapters (Raspberry Pi)" section in Bluetooth Adapters guide documenting the single-stream A2DP limitation of Pi 4/5 on-board Bluetooth and recommending USB dongles for multi-speaker setups

## [2.54.0] - 2026-04-04

### Added
- **PA sink state monitoring** — PulseAudio/PipeWire sink state (`running`/`idle`/`suspended`) is now the sole authority for idle disconnect, replacing the fragile 3-tier daemon-flag + MA-monitor + event-history system (#120)
- `SinkMonitor` module: subscribes to PA sink events via `pulsectl_asyncio`, tracks state for all Bluetooth sinks, fires callbacks on `running ↔ idle` transitions; initial sink scan on PA connect/reconnect to populate state cache
- **WebSocket heartbeat for server-initiated connections** — daemon now sends 30 s ping/pong on the WebSocket server side, matching MA's client-side heartbeat; prevents idle connection drops through proxies, firewalls, and Docker bridge networks (#120, music-assistant/support#4598)

### Fixed
- **Onboarding regresses during standby** — devices in idle-standby are now treated as "logically connected" so onboarding checks and checkpoints don't show incomplete state when the bridge intentionally disconnected BT to save power
- **Idle timer not re-armed after wake** — SinkMonitor fires `on_idle` while `bt_standby` is still True during wake flow; now re-checks sink state after clearing standby and re-arms the idle timer
- **Idle standby broken** — pulsectl's `EnumValue` supports `== 'suspended'` but not `int()` or `== 2`; sink state always classified as "unknown", preventing idle timer from starting; now uses string equality with int fallback
- **Recurring speaker disconnection** during active playback — the old idle guard relied on daemon flags (`audio_streaming`, `playing`) which reset on MA-forced daemon reconnects every ~55 min (#120)
- Solo player (not in a sync group) standby/wake: `_ma_monitor_says_playing()` and `_check_group_auto_wake()` now fall back to `player_id` when `group_id` is None
- Thread safety: `_idle_timer_task` now protected by `_idle_timer_lock` — prevents leaked timers from concurrent access by asyncio event loop and Flask/Waitress threads
- Firing-time safety guard: idle timer re-checks `bt_standby`, `bt_waking`, `keepalive_enabled`, and cached PA sink state before entering standby
- SinkMonitor lifecycle: properly stopped on shutdown, startup failure, and signal handling
- **Restart banner stuck** — `sawRuntimeRestart` now set on successful `/api/restart` response; poll delayed past 500 ms kill window; 60 s safety timeout auto-clears banner
- **Restart fails under S6 overlay** — `PermissionError` when UID 1000 can't signal root PID 1; falls back to `os.kill(os.getpid())` so S6 supervise restarts the child
- **Update check fails on OpenSSL 3.5** — post-quantum ML-KEM key exchange produces oversized TLS Client Hello (1569 B) that middleboxes drop; GitHub API calls now pin `prime256v1` ECDH curve
- **Logs endpoint empty in Docker** — `from sendspin_client import _ring_log_handler` created a second module instance with empty buffer (main runs as `__main__`); now reads via `sys.modules['__main__']`
- **Docker update command** — modal now shows `docker compose pull && docker compose up -d` instead of just `docker pull` which didn't recreate the running container

### Removed
- **`handoff_mode` device option** — unused since v2.53; removed from config schema, migration, orchestrator, status snapshot, and all tests
- Dead fallback methods `_ma_monitor_says_playing()` and `_event_history_says_playing()` — `SinkMonitor` is the sole authority for idle detection

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

## [2.52.4] - 2026-04-02

### Fixed
- Idle standby still triggered during MA server reconnections despite v2.52.3 fix — daemon's `playing` and `audio_streaming` flags both reset on reconnect, bypassing all guards (GH-120)
- Added two-tier fallback guard in `_idle_timeout()`: MA WebSocket monitor group state (primary) and event history ring buffer (fallback) now prevent standby when playback is active but daemon flags were lost

## [2.52.3] - 2026-04-01

### Fixed
- Idle standby timer now reacts to Music Assistant `playing` transport state — cancels timer when playback starts, starts timer when playback stops (in addition to existing `audio_streaming` logic)
- Idle timer firing-time guard re-checks both `audio_streaming` and `playing` before entering standby; restarts timer if device is active
- Daemon connect no longer starts idle timer when `playing=True`

## [2.52.2] - 2026-04-01

### Fixed
- MA Ingress sign-in crash with non-ASCII usernames (e.g. CJK characters) — `'latin-1' codec can't encode` error when creating MA token via Ingress JSONRPC (GH-119)

## [2.52.1] - 2026-03-30

### Fixed
- LXC upgrade: removed `demo/` from sync list and added directory existence guard — fixes upgrade failure when dirs are excluded from release tarball via `.gitattributes`

### Changed
- Release tarball optimized from ~21 MB to ~800 KB via `.gitattributes` export-ignore (excludes docs-site, tests, img, marketing assets)
- Docker build context slimmed with expanded `.dockerignore` (tests, landing, img, addon variants, dev files)

### Added
- Google Translate auto-translation widget for documentation site (14 languages + 100+ via "More")
- Journey log entries for March 26–30 (NumPy crisis, mobile UI, diagnostics, bug report proxy, landing page)

## [2.52.0] - 2026-03-30

### Added
- Bug report modal: GitHub App proxy for users without GitHub accounts — submit issues directly from the bridge UI
- Backend proxy `/api/bugreport/submit` with JWT auth, per-IP (3/hr) and global (20/day) rate limiting
- New dependencies: `PyJWT>=2.8.0` + `cryptography>=3.4.0` for GitHub App RS256 JWT signing
- Stable releases now attach a source tarball as a release asset with tracked download counts
- LXC `upgrade.sh` prefers release asset download (tracked) over archive URL, with automatic fallback
- LXC upgrade: self-update mechanism — `upgrade.sh` fetches its latest version from the target ref before running
- Traffic dashboard: Total Downloads metric card, release downloads chart, per-release download column
- TDD rules for AI agents in CLAUDE.md and CONTRIBUTING.md — red/green/refactor, 5 agent constraints
- CRITICAL risk markers on 7 high-risk code zones (audio routing, thread safety, path traversal, auth, config persistence)
- CI test protection: PR warning when test files change without source changes or bulk modifications
- `config.schema.json` — machine-readable JSON Schema for config.json (all 40+ fields, device/adapter sub-schemas)
- SMM-optimized landing page with infographics, share bars, and custom language picker (14 languages + Google Translate)
- Google Translate auto-translation widget for documentation site
- Landing page deployed to Cloudflare Pages (sendspin-bt-bridge.pages.dev)

### Changed
- Bug report modal: compact dropdown + single Submit button instead of three cards
- Email required for proxy submissions; hidden for "Copy to clipboard" method

### Fixed
- Docker Compose: add `security_opt: apparmor:unconfined, seccomp:unconfined` for Bluetooth on Ubuntu/Debian (#114)
- HA Addon AppArmor profile: add `dbus,` and `network raw,` rules for HA Supervised on Ubuntu 24.04+ (#114)
- Docker build: replace `PyJWT[crypto]` with separate `PyJWT` + `cryptography` deps to fix pip constraints
- LXC upgrade: download release archive to temp file with retry instead of fragile pipe
- LXC upgrade: `warn()` output to stderr to prevent path variable pollution
- Wake button: distinct sunrise icon instead of sharing the reconnect icon
- Test audit: fixed tautological tests and added missing assertions across 6 test files

## [2.51.0] - 2026-03-29

### Added
- Diagnostics: sticky section nav strip with auto-highlighting via IntersectionObserver
- Diagnostics: Simple/Advanced toggle with localStorage persistence (76% content reduction in Simple mode)
- Diagnostics: health status pills strip (speakers, sinks, MA, adapters)
- Diagnostics: speaker filter input for quick search across devices
- Diagnostics: copy-to-clipboard for diagnostics summary and per-device support info
- Diagnostics: contextual bug report pre-fill from recovery issue cards
- Diagnostics: humanized summary cards (issue count, latency comparison, warning breakdown)
- Diagnostics: `_timeAgo()` relative timestamps in timeline and traces
- Diagnostics: Recovery center broken into 5 collapsible sub-sections with count badges
- Diagnostics: action buttons consolidated into sticky footer (Refresh, Download, Copy, Report)
- Hamburger menu, bottom navigation, and config tab scrolling for small screens
- Progressive disclosure for collapsed list items
- Safe-area inset support for notched devices

### Changed
- Grid view is now the default in mobile portrait orientation; list view is landscape-only
- Compact action bar layout: checkbox, volume slider, mute, and pause share one row; bulk actions on second row (104px vs 321px)
- Card controls reordered: volume + mute row first, transport buttons second
- Action buttons (Reconnect / Standby / Disable) fit a single row on Pixel 8 (412px)
- Diagnostics: default timeline limit reduced from 12 to 5 entries
- Diagnostics: PA sinks table mobile layout with truncation and tooltips
- Fallback player ID (no MAC) now uses UUID5 from player name — always 36 chars, no mDNS length issues (#115)

### Fixed
- Long player names no longer cause `zeroconf.BadTypeInNameException` (#115)
- Log viewer now shows actual logs inside Docker containers using an in-memory ring buffer (2000 lines) (#111)
- Artwork proxy now constructs proper MA `/imageproxy` URLs from raw metadata image paths (#112)
- Artwork proxy URL-encodes path components so unicode characters no longer trigger `InvalidURL` errors (#112)
- Eliminated horizontal scroll on mobile by fixing grid track minimums (`minmax(0, 1fr)`)
- Touch targets enlarged to 44px minimum across all interactive elements
- Dark theme contrast and readability improvements across all breakpoints
- Login page: added password visibility toggle, loading states, active tab contrast
- Removed circular "Open diagnostics" button from within diagnostics panel

## [2.50.4] - 2026-03-27

### Fixed
- Idle standby timer now starts when a device connects with no audio playing. Previously, the timer only triggered on a streaming→idle transition, so a speaker that connected but never played would never enter standby.

## [2.50.3] - 2026-03-27

### Fixed
- Fix `numpy<2.0` pin being overridden during Docker build: `pip install sendspin` (without `--no-deps`) was pulling numpy>=2.0 as a transitive dependency, ignoring the pin from requirements.txt. Now uses `-c` (constraint file) to enforce `numpy<2.0` when installing sendspin (#109).

## [2.50.2] - 2026-03-27

### Fixed
- Verify `numpy<2.0` pin is effective in published Docker images, resolving X86_V2 crash on older x86_64 CPUs (#109).

## [2.50.1] - 2026-03-27

### Fixed
- Actually pin `numpy<2.0` in requirements.txt and Dockerfile to fix X86_V2 crash on older CPUs (#109). The v2.50.0 changelog documented the fix but the pin was missing from the build.

## [2.50.0] - 2026-03-27

### Changed
- Bump websockets 13.1 → 16.0 (async API migrated to `websockets.asyncio.client`)
- Bump waitress 2.1.2 → 3.0.2
- Bump pytest-asyncio to <2.0.0
- Bump CI actions: github-script 8, setup-node 6, upload-artifact 7, deploy-pages 5, setup-buildx-action 4

### Fixed
- Fix daemon subprocess crash on older x86_64 CPUs without SSE4.2/POPCNT (X86_V2 instruction set). NumPy ≥2.0 wheels require X86_V2 baseline; pinned `numpy<2.0` to restore compatibility with legacy hardware. Fixes #109.

## [2.49.1] - 2026-03-27

### Fixed
- Fix daemon subprocess crash on older x86_64 CPUs without SSE4.2/POPCNT (X86_V2 instruction set). NumPy ≥2.0 wheels require X86_V2 baseline; pinned `numpy<2.0` to restore compatibility with legacy hardware (Celeron, Pentium, early Core). Fixes #109.

## [2.50.0-rc.1] - 2026-03-26

### Changed
- Bump websockets 13.1 → 16.0 (async API migrated to `websockets.asyncio.client`)
- Bump waitress 2.1.2 → 3.0.2
- Bump pytest-asyncio to <2.0.0
- Bump CI actions: github-script 8, setup-node 6, upload-artifact 7, deploy-pages 5, setup-buildx-action 4

## [2.49.0] - 2026-03-26

### Added
- **Phase 2: Null-sink standby with auto-wake** — daemon stays alive on a PulseAudio null sink after idle disconnect; MA player remains visible so playback auto-resumes when triggered (~5s BT reconnect latency)
- **Auto-wake on play / sync-group wake** — when MA sends play while speaker is in standby, BT reconnects automatically; sync-group members wake each other
- **Standby/Wake UI** — device card shows 💤 Standby badge, moon/sun toggle button, "Waking" transition state; standby status filter in toolbar
- **Idle disconnect standby** — per-device `idle_disconnect_minutes`: disconnect BT after silence timeout to save speaker battery
- **Mutual exclusion: keep-alive vs idle standby** — UI disables one when the other is set >0; backend skips idle timer when keep-alive is active
- **Release/Reclaim in BT tools menus** — moved to Device Fleet dropdown and Already Paired list
- **Experimental features toggle** — browser-local toggle to show/hide experimental features (room name, room ID, handoff mode)
- **Configuration UX overhaul** — reorganized General tab into focused sections; dedicated Audio tab for PulseAudio settings
- **PulseAudio sink-drift hardening** — null-sink fallback (`sendspin_fallback`) prevents orphaned streams landing on random BT speakers
- **Disable PA rescue-streams option** (`DISABLE_PA_RESCUE_STREAMS`) — unloads `module-rescue-streams` at startup to eliminate sink drift
- **Custom exception hierarchy** — `BridgeError` → `BluetoothError`, `PulseAudioError`, `MusicAssistantError`, `ConfigError`, `IPCError`
- **125+ new tests** — covering sendspin_client, web_interface, bt_monitor, bt_manager; suite now at 959 tests
- **POST `/api/bt/standby`** and **POST `/api/bt/wake`** endpoints

### Fixed
- **Standby wake audio** — multiple fixes for audio routing after BT reconnect: ALSA error recovery, bt_monitor race conditions, reroute fallback to daemon restart
- **Recovery/guidance banner ignored standby** — standby devices no longer trigger disconnect warnings
- **upgrade.sh: armv7l pip not upgrading** — added `-U` flag for range-based pip installs
- **CSP fix** — removed nonce (broke `unsafe-inline`), restored onclick handler compatibility
- **Race conditions** — TOCTOU in `update_config()`, pair cancel race, lock ordering, status lock, future cleanup
- **DISABLE_PA_RESCUE_STREAMS checkbox** — convert to boolean in config payload
- **Idle disconnect not saving** — `collectBtDevices()` now persists `idle_disconnect_minutes`
- **Null-sink leak** — reuse existing fallback sink instead of creating duplicates
- **`_handle_disconnect` compat** — fallback for `aiosendspin < 5.x` in standalone LXC deployments

### Changed
- **Fast standby wake** (~5s) — `asyncio.Event` unblocks bt_monitor instantly, IPC redirects daemon to null sink, direct `connect_device()` starts BT reconnect immediately
- **Wake fallback** — MA reconnect instead of full daemon restart when PA streams survive
- **CI/CD: unified release pipeline** — single `VERSION` file triggers lint → test → tag → Docker build → HA addon sync
- **deps**: sendspin 5.8.0→5.9.0, aiosendspin 4.3.2→4.4.0, dbus-fast 2.46.4→4.0.0

### Security
- **PBKDF2 upgrade** — 600K iterations with versioned hash format
- **Config whitelist** — POST `/api/config` filters through allowed keys
- **Artwork proxy** — Content-Type validation (image/* only)
- **XSS prevention** — `escHtmlAttr()` for dynamic onclick values
- Removed `SYS_ADMIN` capability from HA addon configs
- **Unique PA application name per subprocess**: each daemon subprocess now sets `PULSE_PROP_application.name=sendspin-<player_name>` so PulseAudio's `module-stream-restore` no longer confuses streams across different Bluetooth speakers.

## [2.48.2] - 2026-03-25

### Fixed
- Restore compatibility with current Music Assistant releases when newer `aiosendspin` builds expose the draft `visualizer@_draft_r1` role. The bridge no longer advertises the visualizer role in `ClientHello`, so Music Assistant no longer rejects the player connection during startup.
- Pin `aiosendspin==4.3.2` directly in `requirements.txt` so runtime dependency resolution stays consistent across architectures and rebuilds instead of drifting via `sendspin` transitive dependencies.

## [2.48.1] - 2026-03-25

### Fixed
- Avoid `sendspin.audio` callback crashes after ALSA underrun / re-anchor recovery. The bridge now guards against stale cached output-frame state inside the subprocess runtime so a reused frame from an older format or correction cycle is reset instead of exploding with `ValueError: memoryview assignment: lvalue and rvalue have different structures`.
- Avoid false `lost bridge transport` guidance while audio is already playing. Recovery and operator guidance now treat active audio streaming as authoritative during brief Sendspin control reconnects, so transient `server_connected=false` windows no longer raise a transport-loss warning when the speaker is still streaming.
- Avoid false recovery/disconnected UI states during planned Music Assistant metadata reconnects. Idle speakers without an active audio stream now enter a dedicated `ma_reconnecting` transition instead of surfacing `lost bridge transport` or `Music Assistant unavailable` during this expected refresh window.
- Avoid false `lost bridge transport` states after a successful replacement reconnect. The bridge now publishes `server_connected` only after the new Sendspin websocket handshake succeeds, so the old session's disconnect callback cannot overwrite the fresh connection state back to disconnected.

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

## [2.48.0] - 2026-03-25

### Added
- Native Sendspin transport commands via Controller role (`POST /api/transport/cmd`) for play, pause, stop, next, previous, shuffle, repeat, mute, and volume, with transport UI fallback to Music Assistant queue commands when native transport is unavailable.
- Extended metadata forwarding from the Sendspin protocol, including album, album artist, artwork URL, year, track number, shuffle state, repeat mode, and controller state updates such as `supported_commands`, `group_volume`, and `group_muted`.
- Cross-bridge duplicate Bluetooth device detection, startup/recovery warnings for conflicts, and BT scan confirmation prompts when another bridge instance already owns the device.
- Separate `RECOVERY_BANNER_GRACE_SECONDS` setting so recovery banners can remain hidden for a configurable delay after the startup/finalizing lockout ends.

### Changed
- Startup and audio defaults are now tuned for more reliable first-run behavior: startup grace defaults to `5` seconds, recovery-banner grace defaults to `15` seconds, `PULSE_LATENCY_MSEC` defaults to `600`, and newly added Bluetooth devices default to `static_delay_ms = -300`.
- Pin direct runtime dependencies, including `sendspin==5.8.0`, to the CI-validated versions so rebuilds do not silently pick up incompatible upstream changes.

### Fixed
- Restore compatibility with the current Sendspin audio API layout (`sendspin.audio_devices`) while preserving fallback support for legacy `sendspin.audio` installs.
- Avoid false `repair required` states when BlueZ temporarily reports a Bluetooth speaker as unavailable during restart; the bridge now distinguishes unknown pairing state from explicit `Paired: no`.
- Persist live Bluetooth sink volume during graceful shutdown so `Save & Restart` restores the previous user volume instead of falling back to the sink default.
- Avoid false Bluetooth device removal during `Save & Restart` when the default adapter is represented differently in saved config vs. web UI payload.
- Harden stale `device_info` recovery around startup by deferring automatic reconnects until the player is up, and then holding them back behind an additional startup grace window.
- Make native Sendspin `shuffle`/`repeat` buttons update immediately in the web UI after a successful command, matching the snappier Music Assistant queue-command UX.

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

## [2.47.3] - 2026-03-24

### Fixed
- Auto-correct PulseAudio sink routing when a new BT device connects. PulseAudio's `module-rescue-streams` can silently move an existing stream to a newly-appeared sink; the bridge now detects this and moves streams back to their correct sinks within 3 seconds.

### Added
- `get_subprocess_pid()` method on `BluetoothManagerHost` protocol and `SendspinClient` for safe PID access.

## [2.47.2] - 2026-03-24

### Fixed
- Fix constant SSE re-renders closing modals/popups during playback. Visualizer frames no longer trigger status notifications (arrive many times per second). Artwork frames only notify when the image actually changes.

## [2.47.1] - 2026-03-24

### Fixed
- Fix `Separator is found, but chunk is longer than limit` crash when artwork binary frames exceed asyncio's default 64 KB readline buffer. Subprocess stdout buffer raised to 1 MB; artwork frames capped at 48 KB raw to stay within IPC line budget.

## [2.47.0] - 2026-03-24

### Security
- Validate `action` parameter in pause/play endpoints before IPC dispatch (whitelist `pause`/`play`).
- Prevent session fixation by clearing session before setting authenticated state.
- Validate MAC address format in `BluetoothManager.__init__` to block bluetoothctl injection.
- Add 10 MB size cap to artwork proxy reads to prevent OOM from malicious upstream.
- Reject artwork proxy requests for non-MA-origin URLs (SSRF protection).
- Add character whitelist validation on update tag refs.
- Guard `ha_url` parameter in HA Core API against SSRF.
- Add `auth_enabled` flag and warning to diagnostics endpoints when auth is disabled.

### Changed
- **Upgrade sendspin 5.3.2 → 5.7.1**: Updated `requirements.txt` to `sendspin>=5.7.0,<6.0.0`. Includes upstream bugfixes for volume reset on reconnect, pitch shift on format change, and server/hello ordering.
- **Adapt to new volume controller protocol**: `DaemonArgs` now uses `volume_controller` kwarg (sendspin 5.5.0+) with runtime compat filter that falls back to `use_hardware_volume` on older versions.
- **BridgeDaemon skips manual sink sync when upstream handles volume**: `_has_upstream_volume_controller()` check prevents double volume commands.
- **Split `bluetooth_manager.py`** (1 226 → 669 lines): extracted `bt_audio.py` (audio sink discovery), `bt_monitor.py` (polling & D-Bus monitor loops), `bt_dbus.py` (D-Bus helpers).
- **Split `routes/api_ma.py`** (2 343 → 150 lines): extracted `routes/ma_auth.py` (OAuth / HA auth), `routes/ma_playback.py` (queue & now-playing), `routes/ma_groups.py` (discovery & groups).
- **Split `config.py`** (999 → 449 lines): extracted `config_auth.py` (password hashing), `config_migration.py` (schema migration & normalization), `config_network.py` (port resolution & HA detection).
- **Decoupled `BluetoothManager` from `SendspinClient`**: introduced `bt_types.BluetoothManagerHost` Protocol; `BluetoothManager` now depends on the protocol, not the concrete class.
- All public APIs and re-exports preserved for backward compatibility.

### Added
- **PulseVolumeController** (`services/pa_volume_controller.py`): Implements the sendspin `VolumeController` protocol for PulseAudio/PipeWire sinks — atomic volume/mute control via `pulsectl`.
- **Artwork role support**: `BridgeDaemon._create_client()` requests `ARTWORK` role with graceful fallback; monkey-patches `_handle_binary_message` to forward artwork frames as base64 in status dict.
- **Visualizer role support**: `BridgeDaemon._create_client()` requests `VISUALIZER` role with graceful fallback; `_on_visualizer_frames()` callback logs frame counts at debug level.

### Fixed
- Docker/standalone cold starts with configured speakers now wait briefly for late D-Bus, Bluetooth controller, and audio-server readiness before launching the bridge process, reducing the common “works after one manual container restart” startup race on host boots.
- Fix dropped WebSocket messages when `recv_task` and `wake_task` complete simultaneously in MA monitor.
- Validate auth response in `send_player_cmd` fallback WebSocket path.
- Defer interleaved MA events in `_poll_queues`, `_refresh_groups_via_ws`, and `_refresh_stale_player_metadata` (matching `_drain_cmd_queue` pattern).
- Log warning when `_request_command` exhausts retries without finding a matching response.
- Fix task leak on `CancelledError` in MA monitor event loop — pending tasks now cancelled in `finally` block.
- Track consecutive MA auth failures; log ERROR after 5 failures suggesting token reconfiguration.
- Fix race in `reload_credentials` — close WebSocket before clearing reference.
- Fix race condition in volume tracking — atomic read-compare-update under single lock.
- Fix TOCTOU race in `build_device_snapshot` — new atomic `snapshot()` method on `SendspinClient`.
- Fix `status["playing"] = False` bypassing `_update_status`, skipping SSE notification.
- Fix non-atomic list swap in `state.py` — use slice assignment instead of `clear()` + `extend()`.
- Wrap event publisher subscriber callbacks in `try/except` to prevent cascading failures.
- Track fire-and-forget async tasks in daemon process; cancel all on shutdown.
- Fix reader task cancellation order — send stop command before cancelling readers.
- Handle `ProcessLookupError` in subprocess stop when process exits between timeout and kill.
- Fix `parse_status_envelope` dropping all keys when `allowed_keys` is `None`.
- Replace `asyncio.run()` in WSGI threads with `ThreadPoolExecutor` + timeout.
- Add exception handling for blocking BT operations in HTTP check handler.
- Prevent `finish_async_job` from overwriting internal bookkeeping keys.
- Catch `ValueError` for non-numeric `ingress_port` in HA addon.
- Guard `int()` on HTTP rate-limit headers against `ValueError`/`TypeError`.
- Fix recovery assistant always reporting 0 custom delays (missing `static_delay_ms`).

### Improved
- Upgrade `config_lock` from `Lock` to `RLock` to prevent potential self-deadlock.
- Reduce `config_lock` scope during file I/O — parse JSON outside the lock.
- Fix migration persist to write only deltas instead of full replace (preserves concurrent writes).
- Add `shutdown()` method to `EventHookRegistry` for proper executor cleanup.
- Add `clear_subscribers()` to event publisher for shutdown cleanup.
- Add IPC message size limit (1 MB) to prevent truncated JSON from oversized metadata.
- Add threading lock for shared status dict in daemon subprocess.
- Pre-compute operator guidance checks once per snapshot instead of 3×.
- Replace `deepcopy(checklist)` with shallow copy on guidance hot path.
- Replace dynamic `type()` class creation with `@dataclass` in recovery assistant.
- Deduplicate `_device_extra()` and `_parse_timestamp()` helpers into shared `services/_helpers.py`.
- Extract `_auto_release_device()` method in Bluetooth manager to remove duplication.
- Cache config during snapshot build — single read instead of per-device.
- Use `deque(maxlen=...)` in log analysis instead of eager list materialization.
- Narrow 20 bare `except Exception:` catches to specific exception types across 10 files.

### Tests
- Add thread-safety tests for concurrent status, config, and notification operations (7 tests).
- Add auth enforcement regression tests for protected endpoints (12 tests).
- Replace timing-dependent `time.sleep()` in tests with `threading.Event` synchronization.
- Add error-path tests for malformed IPC, invalid MAC, invalid action, ProcessLookupError (12 tests).
- Add IPC protocol integration tests — roundtrip envelope correctness (13 tests).
- Strengthen weak assertions in API endpoint tests (9 tests improved).
- New test files: `test_pa_volume_controller.py` (5 tests), `test_bridge_daemon_features.py` (10 tests).

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

## [2.46.0] - 2026-03-23

### Added
- Bridge-backed Bluetooth devices can now carry stable room metadata (`room_name`, `room_id`, source/confidence) and expose it through status snapshots, making Music Assistant / Home Assistant / MassDroid room mapping much easier to reason about.
- Device snapshots now include a compact `transfer_readiness` contract so operators and automations can see whether a speaker is truly ready for a fast room handoff.

### Changed
- Docker and Raspberry Pi images now keep container init/root setup for Bluetooth and D-Bus, but automatically re-exec the bridge process as `AUDIO_UID` for user-scoped host audio sockets. This removes the common Raspberry Pi root-vs-user PulseAudio/PipeWire mismatch without requiring a global Compose `user:` override.
- ARMv7 release images now install the FFmpeg runtime libraries needed by PyAV/sendspin and the publish workflow now smoke-tests the actual daemon import path, fixing the `libavformat.so.61` runtime crash seen on older Raspberry Pi hardware.
- Per-device settings now support an explicit `handoff_mode`, with `fast_handoff` reusing the existing keepalive path to keep selected speakers warmer for transfer-heavy room workflows.
- Runtime device events are now enriched with room and readiness context, and the web UI surfaces new room / transfer badges plus manual room assignment controls in device settings.
- Home Assistant add-on config sync/translation now preserves the new room and handoff fields across supervisor round-trips and restarts.
- Startup diagnostics, the Raspberry Pi pre-flight checker, and Docker docs now distinguish init UID vs app UID, explain the split-privileges model, and make user-scoped PipeWire/PulseAudio failures much easier to diagnose.

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

## [2.45.0] - 2026-03-23

### Added
- Home Assistant ingress sessions can now fetch the HA area registry into the config UI, so `Bridge name` offers one-click room suggestions and Bluetooth adapters can surface exact area matches from the HA device registry.
- Diagnostics recovery tooling now includes a deeper retained recovery timeline with advanced severity, scope, source, and window filters for power-user trace review.

### Changed
- Music Assistant runtime can now reload after URL or token changes without forcing a full bridge restart, so MA auth refreshes and rediscovery can be applied in place.
- Operator guidance is calmer and more actionable: onboarding stays out of the notice stack on non-empty installs by default, grouped actions preview affected devices before execution, and dense recovery issue pills collapse into `+N more`.
- Home Assistant area-based naming suggestions for `Bridge name` and Bluetooth adapter names are now configurable and stay enabled by default in HA add-on mode.

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

## [2.42.3] - 2026-03-22

### Added
- The bug report dialog now pre-fills an editable description generated from attached diagnostics, summarizing recent errors, Bluetooth/device health, daemon status, and Music Assistant connectivity so issue reports start with more useful context.

### Changed
- Onboarding guidance now separates status from disclosure more clearly: the header keeps a passive setup-status badge, while checklist visibility uses an explicit `Show checklist` / `Hide checklist` control and a collapsed summary state in the notice stack instead of disappearing completely.
- The Music Assistant configuration flow is now easier to re-enter after initial setup: the connection-status card owns the `Reconfigure` action, and the sign-in/token section stays hidden until reconfiguration is explicitly requested.

### Fixed
- The Bluetooth scan modal now keeps discovered-device badges inline after the device name, making dense result lists more compact without losing badge context.
- The `Bluetooth → Paired devices` inventory layout is corrected again: the subtitle stays on one line, the inner `Already paired devices` header/count no longer collapses, and the `Info`, `Reset & Reconnect`, and remove actions stay aligned on the right side of each row.
- The onboarding checklist toggle now updates its `Show` / `Hide` state immediately when clicked instead of waiting for the next background status refresh.
- Guidance and banner CTAs that send operators back to Music Assistant token setup now open the section directly in reconfigure mode so the auth controls are visible right away.
- The `Auto-get token on UI open` Music Assistant setting is now hidden outside Home Assistant add-on mode, matching the runtime behavior where silent token bootstrap only works through HA ingress.

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

## [2.42.2] - 2026-03-22

### Added
- The Bluetooth scan modal now exposes adapter selection, an explicit audio-only filter, and a dedicated rescan action so multi-adapter discovery is easier to control.
- Onboarding now recognizes when every configured speaker has been manually released and offers direct reclaim actions so playback can be resumed without hunting through the configuration screens first.

### Changed
- The compact UI system is now much more consistent across the live app, including the login screen: primary/secondary/icon actions, media transport controls, table-like rows, empty states, badges, chips, and guidance surfaces now follow a clearer shared design language instead of mixing several older styles.
- Shared design-system foundations are now more explicit across notices, configuration, toolbars, and guidance surfaces: spacing, typography, focus-ring, layout, count-badge, action-menu, configuration-header, and notice-copy shells are reused instead of being defined as scattered local overrides.
- Bluetooth discovery and management surfaces now present richer scan metadata and a more coherent workflow, with the scan dialog and paired-device actions aligned to the shared compact modal/action system used elsewhere in the interface.

### Fixed
- Scan results now stay aligned with the selected discovery scope, non-audio Bluetooth candidates are surfaced more honestly when the audio-only filter is disabled, and the modal copy now explains the real operator workflow more clearly.
- Guidance cards that opt into `show_by_default` now auto-open consistently from the header entry point, and interactive/passive badges now use more consistent borders, hover feedback, cursor behavior, and compact control typography.
- Demo mode regains compatibility with the refreshed UI preview workflow, so local demo validation continues to work against the current Bluetooth manager behavior.
- Home Assistant login failures against Music Assistant now return the actual MA-side bootstrap reason when HA OAuth is unavailable, and the UI guidance now tells operators to switch to direct Music Assistant authentication when HA login is not configured there.
- Standalone Home Assistant login against Music Assistant add-ons now completes again after TOTP by falling back to direct HA login flow, resolving MA ingress through HA Supervisor APIs, and creating the final MA token with an `ingress_session` cookie instead of a plain HA bearer token.

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

## [2.42.1] - 2026-03-20

### Added
- The bridge now ships a fuller operator-assistance layer: onboarding checklist guidance, a recovery assistant, and richer diagnostics tools including per-section copy helpers and expandable raw payload details for expert troubleshooting.

### Changed
- The dashboard guidance flow is now much clearer across setup, recovery, and diagnostics: onboarding stays available from the header, diagnostics is split into a simpler `Overview` plus `Advanced diagnostics`, and key sections jump directly into the relevant configuration panels.
- Grid view playback cards now use larger album-art thumbnails so artwork fills the media block more effectively.

### Fixed
- Restart, startup, and update flows now stay locked to live backend progress more reliably, including the full finalizing phase and LXC update/restart cycles, with a cache-busting refresh after updates so the browser loads the new UI immediately.
- Disabled-device handling is now consistent across the dashboard and configuration flows: disabling a live device persists instantly, keeps the disabled visuals intact, and survives `Save and restart` without requiring a page refresh first.
- Guidance edge cases are corrected for `All devices disabled` installs and latency review actions, so operators are sent to the correct settings and shown copy that matches the real system state.

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

## [2.40.6] - 2026-03-19

### Changed
- GitHub Releases are now a stable-only surface: RC/Beta update discovery uses Git tags plus the tagged `CHANGELOG.md`, while Home Assistant add-on directory sync runs directly on every stable/RC/beta tag push without depending on a prerelease GitHub release object.
- High-frequency bridge control routes and long-running Music Assistant/update actions now avoid blocking Flask request threads: MA discovery/rediscovery, update checks, and queue commands use async job polling or optimistic completion flows instead of waiting synchronously in the request path.
- Home Assistant add-on mode now treats the web UI ingress port and installed delivery track as fixed channel properties, so the configuration UI presents them as read-only channel information instead of editable update-track settings.

### Fixed
- Existing LXC installs can update onto the prerelease tag-based channel flow again: runtime update checking no longer imports `scripts.release_notes`, and the LXC install/upgrade snapshot sync now copies the `scripts/` directory so staged validations keep matching the real application tree.
- Music Assistant beta transport compatibility is restored across solo players and groups: `next` / `previous` prefer player-level commands where supported, and solo `shuffle` / `repeat` now fall back to legacy `up...` queue ids while treating MA `error_code` responses as real command rejections.
- Home Assistant and standalone UI polish: add-on profile/group-settings links use ingress-safe URLs, the signed-in user link opens in a normal new tab, and the standalone `Web UI port` helper text is short enough to stay on one line.

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

## [2.40.5] - 2026-03-18

### Added
- Bridge config, web UI, and Home Assistant add-on options now support manual top-level `WEB_PORT` and `BASE_LISTEN_PORT` overrides. In Home Assistant add-on mode, `WEB_PORT` opens an additional direct host-network listener while the fixed ingress endpoint keeps using the channel default port.

### Changed
- Home Assistant release engineering now supports safer multi-track distribution: prerelease add-on variants use distinct default ingress/player port ranges, manual startup defaults, channel-specific branding, and HA-safe prerelease notices so parallel stable/RC/beta installs are easier to distinguish and safer to run on one HAOS host.
- The GitHub release workflow now builds the release body from the matching `CHANGELOG.md` section and uses GitHub-generated notes only as an optional supplement, preventing empty autogenerated releases.

### Fixed
- Music Assistant album artwork now loads correctly through Home Assistant ingress because artwork proxy URLs stay relative to the active add-on origin instead of escaping to the Home Assistant root.
- Solo-player Music Assistant transport controls now keep working when Music Assistant syncgroup discovery is empty because queue commands respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first.
- Header version/update indicators now tint only the RC/Beta version text instead of coloring the entire badge, and Home Assistant add-on info/docs now render prerelease notices correctly through HA-safe badge markdown.
- Home Assistant add-on config validation no longer treats optional manual `web_port` / `base_listen_port` overrides as required fields, because unset values are now omitted from add-on defaults and Supervisor option sync payloads instead of being sent as `null`.

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

## [2.40.5-beta.1] - 2026-03-18

### Changed
- Home Assistant add-on channel variants can now run side by side on the same HAOS host because stable, RC, and beta installs use distinct default ingress ports and Sendspin listener port ranges while still honoring explicit port overrides
- RC and beta Home Assistant add-on variants now default to manual startup and use channel-specific branding in the store/sidebar so prerelease tracks are easier to distinguish from the stable add-on

### Fixed
- Music Assistant album artwork now loads correctly through Home Assistant ingress because artwork proxy URLs stay relative to the current add-on origin instead of escaping to the Home Assistant root

## [2.40.5-rc.1] - 2026-03-18

### Fixed
- Solo-player Music Assistant transport controls now keep working on live Proxmox/LXC deployments even when MA syncgroup discovery is empty, because queue commands now respect an explicit solo queue ID instead of requiring `ma_groups` to be populated first

### Changed
- Header version badges and discovered-update badges now highlight prerelease channels directly in the UI: RC builds use yellow styling and beta builds use red styling

## [2.40.4] - 2026-03-18

### Added
- A packaged-runtime smoke checker plus CI/build workflow smoke steps now validate that released Docker and Home Assistant add-on images still contain the required runtime modules and can execute the HA options translation path before publication completes

### Changed
- Home Assistant prerelease channel publishing is now aligned around the current repository itself: the stable addon stays in `ha-addon/`, prerelease variants sync into `ha-addon-rc/` and `ha-addon-beta/` when those channel tags exist, and the stable slug remains unchanged for analytics continuity
- Home Assistant update surfaces and documentation now distinguish the installed addon track from the in-app `update_channel` preference so saving `rc` or `beta` no longer implies that the installed addon already switched tracks

### Fixed
- Music Assistant queue controls against MA `2.7.11` stable no longer fail with `no queue available` for solo Sendspin players because queue resolution now prefers the modern `player_id == queue_id` path for `sendspin-*` players while keeping the legacy `up<uuid-without-hyphens>` fallback for older queue IDs

## [2.40.3] - 2026-03-18

### Fixed
- Docker and Home Assistant add-on images now copy all top-level Python modules from the repository root instead of a hand-maintained file list, fixing the `ModuleNotFoundError: No module named 'bridge_orchestrator'` startup regression exposed by the `2.40.2` image
- HA add-on startup diagnostics now point at `/data/config.json` before the main process starts, so addon logs no longer report a misleading `/config/config.json not found` message during Home Assistant boot

## [2.40.2] - 2026-03-18

### Fixed
- Home Assistant add-on startup now works again after the `2.40.1` update because `scripts/translate_ha_config.py` restores access to the repository root before importing shared config helpers, so the addon no longer crashes with `ModuleNotFoundError: No module named 'config'` during HA options translation

## [2.40.1] - 2026-03-18

### Added
- Config, API, and update surfaces now support an explicit `UPDATE_CHANNEL` setting with `stable`, `rc`, and `beta` options so standalone installs and Home Assistant add-on configs can choose their preferred release lane without changing the main runtime contract
- The standalone web UI now exposes an update-channel selector with confirmation/warning copy for `rc` and `beta`, making prerelease opt-in explicit before operators leave the stable lane
- Home Assistant add-on options, translations, docs, and config translation now carry the same `update_channel` setting through to the runtime config

### Changed
- `services/update_checker.py` now resolves releases from the GitHub releases list by channel-aware semver matching (`stable`, `rc`, `beta`) instead of relying on the stable-only `releases/latest` endpoint
- `/api/update/check`, `/api/update/info`, and `/api/update/apply` now return channel-specific release metadata, warnings, and runtime-specific upgrade instructions for Docker, systemd/LXC, and Home Assistant environments
- Docker publish and GitHub release workflows now split stable, rc, and beta distribution lanes: stable continues from `vX.Y.Z`, rc comes from `vX.Y.Z-rc.N` tags on `main`, and beta comes from the `beta` branch or `vX.Y.Z-beta.N` prerelease tags

### Fixed
- Home Assistant sign-in for external Music Assistant servers now works again against current Music Assistant stable and beta builds because the bridge accepts the newer redirect-based `/auth/authorize` flow and the newer JSON-RPC `auth/authorization_url` response shape instead of assuming the legacy auth bootstrap payload only

## [2.40.0] - 2026-03-18

### Added
- Repository-level `ROADMAP.md` and execution backlog documentation so the multi-release architecture plan from `2.33.x` through `2.40.x` is captured alongside the shipped implementation waves
- Shared status/read-model snapshots for bridge status surfaces, including per-device `health_summary`, `recent_events`, and a centralized snapshot service used by status, groups, SSE, and later diagnostics surfaces
- Bridge-wide startup progress snapshots plus additive `/api/startup-progress`, `/api/status`, `/api/diagnostics`, and SSE exposure so operators can see startup phase transitions instead of waiting for an opaque ready/not-ready flip
- Explicit runtime-info and mock/demo runtime snapshots, including metadata about simulated layers registered by `demo.install()`, so diagnostics can explain when Bluetooth/runtime behavior is mocked
- Explicit `CONFIG_SCHEMA_VERSION` handling in `config.py`, including legacy-config backfill so loaded configs are transparently persisted with the current schema version for future migration work
- Shared `services.ipc_protocol` helpers with `IPC_PROTOCOL_VERSION` so parent↔subprocess JSON-line messages and daemon bootstrap params now carry an explicit protocol contract
- Internal contract versions are now exposed through shared bridge system info, `/api/version`, and diagnostics payloads so operators can see the active config schema and IPC protocol surfaces at runtime
- New `services.lifecycle_state` helpers so bridge-wide startup progress, MA integration publication, main-loop publication, and startup completion now have an explicit service seam instead of being scattered across `BridgeOrchestrator`
- New `services.ma_integration_service` helpers so Music Assistant URL/token resolution, syncgroup discovery, and optional monitor startup can evolve independently from orchestrator lifecycle wiring
- New `services.playback_health` helpers so zombie-playback watchdog state and restart thresholds are owned by a focused monitor instead of living directly on `SendspinClient`
- New `services.subprocess_stderr` helpers so daemon `stderr` classification and crash-like status publication can evolve independently from `SendspinClient`
- New `services.subprocess_ipc` helpers so daemon stdout parsing, protocol-version warning policy, and log/status message dispatch can evolve independently from `SendspinClient`
- New `services.subprocess_command` helpers so daemon stdin command serialization and protocol-version envelopes can evolve independently from `SendspinClient`
- New `services.subprocess_stop` helpers so daemon reader-task cancellation and graceful stop/kill flow can evolve independently from `SendspinClient`
- New `services.status_event_builder` helpers so structured device-event derivation can evolve independently from `SendspinClient`
- New `services.internal_events` publisher so bridge-internal runtime events can be routed through a lightweight in-process event bus before persistence or diagnostics consumers observe them
- New `services.config_validation` helpers so uploaded config payloads can be validated through an explicit service that reports structured errors, warnings, and additive normalization before persistence
- New `services.onboarding_assistant` helpers so operator-facing setup guidance can be derived from preflight checks, configured devices, sink state, MA auth status, and latency settings

### Changed
- Routine Bluetooth reconnect warnings are now ignored by log analysis so diagnostics and issue-reporting surfaces stay focused on actionable failures during normal reconnect churn
- `services.bluetooth` now persists `device enabled/released` state through the bound config path consistently, removing a path-mismatch edge case between runtime persistence and tests
- Parent↔subprocess IPC is now versioned end-to-end: daemon status/log envelopes, parent command envelopes, and daemon startup params all include `protocol_version` while remaining backward-compatible with legacy messages that omit it
- Bridge bootstrapping now runs through the dedicated `BridgeOrchestrator`, which incrementally took ownership of runtime initialization, web startup, shutdown, Music Assistant bootstrap, task assembly, runtime execution, device initialization, option normalization, and final lifecycle sequencing without changing external behavior
- Added a new `services.device_registry` read-side snapshot service and moved low-risk status/helper routes onto it so the UI surfaces depend less on direct global client-list access
- `routes/api_config.py` now uses the shared device-registry snapshot service for config enrichment, adapter-removal lookups, and log-level propagation instead of reading the global client list directly
- `routes/api_ma.py` now builds MA host inference, rediscovery player payloads, queue-target inference, and debug client dumps from shared device-registry snapshots, also fixing one rediscovery path that previously passed only player names instead of MA discovery payload objects
- `routes/api.py` now resolves volume, mute, group pause, and per-player pause/play targets from shared device-registry snapshots instead of reading the global client list directly
- `services/ma_monitor.py` now reads active bridge clients through the shared device-registry snapshot service for syncgroup queue discovery, solo queue discovery, stale identity reconciliation, and WS group refresh payload assembly
- `BridgeOrchestrator` now delegates startup-state publication to `BridgeLifecycleState` for config startup, executor readiness, web client publication, runtime/device inventory, MA integration publication, and final startup completion without changing external behavior
- Added focused lifecycle-state and orchestrator delegation coverage so the new service seam is locked down before larger `2.37.x` lifecycle extractions
- `BridgeOrchestrator` now delegates MA bootstrap work to `BridgeMaIntegrationService`, keeping lifecycle publication in the orchestrator while moving MA-specific URL autodetect, syncgroup discovery, and monitor startup into a dedicated service
- Added focused `ma_integration_service` coverage and orchestration delegation tests so the new MA service seam is validated before deeper `2.37.x` runtime extractions
- `SendspinClient` now delegates zombie-playback session tracking and restart-threshold logic to `PlaybackHealthMonitor`, while keeping compatibility through temporary proxy properties so existing diagnostics and callers keep working unchanged
- Added focused playback-health tests plus runtime regression coverage to lock down the new watchdog seam before larger daemon/process extractions
- `SendspinClient` now delegates subprocess `stderr` reading and line classification to `SubprocessStderrService`, while keeping a thin compatibility proxy for existing tests and internal call sites
- Added focused subprocess-stderr tests and client delegation coverage to lock down the new daemon logging seam before deeper process-lifecycle extractions
- `SendspinClient` now delegates daemon stdout/IPC parsing, protocol warning policy, and log/status message dispatch to `SubprocessIpcService`, while retaining local volume-persist handling around status updates
- Added focused subprocess-IPC tests and client delegation coverage to lock down the new daemon stdout seam before deeper process-lifecycle extractions
- `SendspinClient` now delegates daemon stdin command writing to `SubprocessCommandService`, while keeping `_send_subprocess_command()` as a thin compatibility proxy for routes, Bluetooth hooks, and tests
- Added focused subprocess-command tests and client delegation coverage to lock down the new daemon stdin seam before larger lifecycle extractions
- `SendspinClient` now delegates reader-task cancellation and graceful subprocess stop/kill logic to `SubprocessStopService`, while keeping `stop_sendspin()` as a thin coordinator that still owns the final status reset
- Added focused subprocess-stop tests and client delegation coverage to lock down the new stop/shutdown seam before extracting higher-level restart lifecycle flows
- `SendspinClient` now delegates pure status-transition event derivation to `StatusEventBuilder`, keeping `_update_status()` responsible only for state mutation and event persistence
- Added focused status-event builder tests while preserving existing runtime/API regression coverage for recent events and health summaries
- Device operational events can now be published through a shared internal event bus via `state.publish_device_event()`, with the default subscriber persisting them into the existing per-device diagnostics ring buffer
- `SendspinClient` now publishes its structured device events through the internal event bus instead of writing directly to the ring buffer, laying the groundwork for broader 2.38.x event consumers without changing diagnostics output
- Daemon subprocesses can now emit explicit `type: "error"` IPC envelopes for fatal startup/parameter failures, in addition to the legacy log output path
- Parent-side IPC handling now consumes structured daemon error envelopes by updating `last_error` / `last_error_at` directly from stdout messages, while preserving compatibility with the older stderr/log-based behavior
- `/api/config/upload` now delegates validation to the shared config-validation service, returns structured `errors`/`warnings` payloads for invalid imports, and applies additive normalization such as schema-version backfill and uppercase MAC canonicalization before saving
- `POST /api/config` now also delegates baseline schema validation to the shared config-validation service, persists `CONFIG_SCHEMA_VERSION`, and returns structured validation warnings/errors consistently with the upload path before continuing route-specific coercion and save logic
- New dry-run `POST /api/config/validate` surface now exposes explicit config validation results, warnings, and normalized preview payloads without persisting changes, giving the upcoming 2.39.x operator/UI work a stable reporting contract
- New `GET /api/onboarding/assistant` endpoint now exposes actionable operator guidance for Bluetooth availability, audio availability, sink verification, Music Assistant auth state, and latency calibration using a dedicated service layer, while `/api/preflight` now reuses a shared collector instead of duplicating the same runtime checks inline
- `/api/diagnostics`, `/api/bugreport`, and diagnostics text exports now include the onboarding assistant payload so setup guidance travels with the richer support/bugreport surfaces instead of being isolated to a standalone endpoint

### Fixed
- Added focused demo-runtime helper coverage so mock/demo support paths are regression-tested alongside the production runtime contracts introduced in `2.34.x`

## [2.32.12] - 2026-03-17

### Fixed
- GitHub Actions lint runs no longer fail on a stale `noqa` mismatch between local `pre-commit` Ruff settings and the repository CI `ruff check` configuration
- Release Docker images built with an explicit `SENDSPIN_VERSION` now install `sendspin` with its runtime dependencies, so built-image smoke tests no longer fail on missing `aiosendspin` or `av`

### Changed
- Local `pre-commit` Ruff now ignores `UP038` in the hook itself instead of relying on source-level suppression, keeping local Python 3.9 development compatible with the repository CI lint path

## [2.32.11] - 2026-03-17

### Changed
- The dashboard now defaults to `list view` for new sessions, while still honoring any previously saved user layout preference from `localStorage`

### Fixed
- GitHub Actions release preparation now installs the required D-Bus development packages before resolving the packaged `sendspin` version, avoiding `dbus-python` metadata failures
- The CI smoke-test job now installs the PortAudio runtime library before checking `sendspin` compatibility, preventing false failures on runners without audio libraries preinstalled
- The Docker build dependency branch now uses a hadolint-friendly `elif` flow for release-specific `sendspin` installs without changing the resulting package set

## [2.32.10] - 2026-03-17

### Changed
- GitHub releases are now created by a dedicated manual workflow that defaults to the latest tag, can target any tag explicitly, syncs `ha-addon/config.yaml` only during release, and generates cumulative release notes from the previous published release
- The dashboard filter toolbar now stays visible even when only a single player is present, while bulk selection/actions remain hidden until multiple players are available
- Card-view `repeat one` now uses an inline icon variant with the numeral inside the repeat symbol instead of a separate visual badge overlay

### Fixed
- Crash-like subprocess `stderr` is no longer silently downgraded to plain warnings; bugreports, diagnostics, and the `Report an Issue` indicator now share the same issue-severity model
- Card-view `shuffle` / `repeat` controls now visibly reflect their active state, and the repeat icon updates in place when switching between `off` / `all` / `one`

### Added
- Runtime dependency fingerprints for `sendspin`, `aiosendspin`, `av`, and related packages in startup logs, diagnostics, bugreports, and `/api/version`
- Real `sendspin` compatibility smoke checks and prerelease Docker/CI gates so release images are validated against the installed runtime dependency set before publication

## [2.32.9] - 2026-03-17

### Fixed
- Home Assistant add-on startup no longer crashes on environments where the installed `sendspin` package removed the `use_hardware_volume` argument from `DaemonArgs`
- The daemon subprocess now filters its startup kwargs against the installed `DaemonArgs` signature, so the bridge remains compatible across `sendspin` builds instead of failing before any player comes up

### Added
- Regression coverage for daemon-argument compatibility filtering so unsupported kwargs are dropped while still preserving supported ones

## [2.32.8] - 2026-03-17

### Changed
- Music Assistant queue routing now separates the dashboard/cache state key from the real MA target queue ID, so grouped players keep using syncgroup queues while solo universal-player bridges target their own `up...` queue correctly
- Queue-control apply state now disables transport buttons temporarily without the extra pending highlight, keeping both card and list views calmer while a command is in flight

### Fixed
- Solo-player transport controls on live deployments now work again even when the page still holds stale MA syncgroup metadata; the backend can fall back to the active local player queue instead of sending commands into the wrong MA target
- Proxmox hotfix rollouts no longer leave MA queue commands broken because the `routes/api_ma.py` and `state.py` pending-state contract is aligned again for `accepted_at` / `ack_latency_ms`
- Repeat no longer trips the queue-command path by eagerly evaluating seek-specific integer conversion while building the MA action payload

### Added
- Regression coverage for solo-player queue resolution, stale syncgroup fallback, non-seek repeat routing, and the updated backend queue-command response flow

## [2.32.7] - 2026-03-16

### Changed
- Music Assistant queue commands now return structured backend-driven responses with `op_id`, `syncgroup_id`, pending-state metadata, and predicted `ma_now_playing` snapshots instead of relying on frontend-only optimistic mutations
- MA queue commands now use the persistent monitor connection as the authoritative hot path; interleaved `player_queue_updated` / `player_updated` events are deferred and flushed after command acknowledgements instead of being silently dropped
- armv7 Docker publishing now treats GitHub Actions cache-export failures as non-fatal so a successfully pushed image does not turn the release workflow red during the post-build cache step

### Fixed
- MA now-playing state is no longer cleared on short monitor disconnects; the backend keeps the last confirmed snapshot, marks it stale/disconnected, and preserves pending/error metadata for the UI
- Queue-control responsiveness and reconciliation are more deterministic because the UI now consumes backend-predicted MA state instead of mutating `dev.ma_now_playing` locally and waiting for a later poll to catch up

### Added
- Regression coverage for MA pending-state transitions, structured `/api/ma/queue/cmd` responses, monitor-unavailable fast-fail behavior, and deferred processing of interleaved MA queue events

## [2.32.6] - 2026-03-16

### Changed
- Card view now places playback progress below the current-track metadata instead of beside it, giving track details more room and aligning the information stack more closely with Music Assistant
- Expanded list rows now add a text-only `Now playing` badge, larger artwork, and a slower Music Assistant-style equalizer for clearer live playback context
- Dashboard bulk `Reconnect all` / `Release all` actions now use the same action-button styling as the per-device reconnect/release controls

## [2.32.5] - 2026-03-16

### Changed
- Expanded list playback now uses a tighter two-block layout: artwork + current-track metadata stay compact on the left, while queue neighbors, transport controls, and progress align in a dedicated playback rail
- Previous/next queue previews in expanded list rows now render track, artist, and album on separate lines for easier scanning

### Fixed
- Shuffle/repeat controls now update immediately in the UI, and repeat exposes distinct `off` / `all` / `one` visual states instead of collapsing active modes together
- Transport, queue, mute, and volume controls are now disabled consistently when Sendspin, Music Assistant, or the audio sink state does not support the action, with matching frontend guards against stale clicks

## [2.32.2] - 2026-03-16

### Changed
- LXC install and upgrade scripts now pull a GitHub archive snapshot and sync the runtime tree recursively instead of relying on hard-coded file download lists that can drift from the repo layout
- One-click update and background auto-update on native LXC/systemd installs now launch `upgrade.sh` through `systemd-run --no-block` and pass the detected release ref, so upgrades are pinned to the intended tag instead of defaulting silently to `main`
- GitHub traffic archival now captures richer repository/release statistics, and the docs site now exposes that archived data on a simple public stats dashboard in English and Russian

### Fixed
- LXC upgrades now stage the new application tree, validate imports before swap, restart the service, run local smoke checks, and automatically roll back if the upgraded service fails to come back cleanly
- Existing native LXC update scripts now include the runtime modules that previously caused post-update crashes such as missing `services.ma_artwork` / `services.ma_discovery`
- The enlarged album-art preview now consistently renders above filter/group-action chrome in both card and list views, including the previously clipped list layout
- The armv7 Docker build path now installs an `av` version compatible with the current `aiosendspin` dependency set, fixing the GitHub Actions armv7 image build

### Added
- Regression coverage for detached upgrade launch, release-tag forwarding, and guardrails that ensure the LXC scripts keep using archive-based recursive sync logic
- A lightweight GitHub Pages stats dashboard for archived traffic, release downloads, referrers, popular paths, and current repository snapshot metrics

## [2.32.0] - 2026-03-16

### Changed
- Dashboard playback UI now aligns card and list views around shared Music Assistant-style helpers, including tighter equalizer/metadata layout, hover-reveal card actions, slimmer volume sliders, left-aligned card selection, and cleaner numeric volume labels
- Expanded list rows now mirror Music Assistant more closely with artwork-adjacent current-track metadata plus queue-neighbor previews and transport/shuffle/repeat controls arranged around the active track context

### Fixed
- MA queue neighbor metadata is now backfilled via `player_queues/items` when `player_queues/all` omits previous/next items, so the UI no longer falls back to false `Queue start` / `Queue end` placeholders while real neighbors exist
- Card and list playback progress now initialize deterministically and reject stale elapsed snapshots for the same track, eliminating the full-width flash and backward jumps in progress/time displays
- Diagnostics now expose playing state together with parsed PulseAudio sink-input `application_*` / `media_*` metadata, making live audio-routing issues easier to inspect

### Added
- Regression coverage for queue-neighbor hydration, artwork proxy signing/origin validation, and diagnostics sink-input parsing

## [2.31.11] - 2026-03-16

### Fixed
- Music Assistant album art now loads through a bridge-hosted same-origin proxy route, so covers render again in the dashboard without weakening the frontend URL safety checks
- Artwork proxying resolves relative MA image paths against the configured MA base URL, forwards the MA bearer token when needed, and rejects foreign origins instead of turning the bridge into an open fetch proxy

### Added
- Regression tests for now-playing artwork URL wrapping and the `/api/ma/artwork` proxy happy-path / origin-rejection behavior

## [2.31.10] - 2026-03-16

### Changed
- Config loading now normalizes key integer and boolean settings on read, canonicalizes configured device MACs, and prunes orphaned `LAST_VOLUMES` entries so runtime state stays consistent with the current device list
- The web UI and README now document degraded-state behavior for duplicate MACs, unresolved Bluetooth adapters, and corrupt `config.json` recovery

### Fixed
- Bluetooth adapter resolution no longer silently falls back to `hci0`; when adapter lookup fails, the bridge keeps D-Bus disabled for that device and falls back to bluetoothctl polling instead of targeting the wrong controller
- Duplicate Bluetooth MAC entries in `BLUETOOTH_DEVICES` are now filtered before runtime startup so one physical speaker cannot spawn two competing clients
- Zombie playback detection now resets per playback session, so “playing without audio” can still be recovered even after a previous successful stream in the same subprocess lifetime
- Corrupt `config.json` files now produce a best-effort `config.json.corrupt-*` backup before defaults are used, and delayed volume persistence no longer writes state for removed devices

### Added
- Regression tests for unresolved adapter fallback, duplicate MAC filtering, zombie watchdog session resets, corrupt config backup handling, and volume/config normalization edge cases

## [2.31.9] - 2026-03-16

### Changed
- **Config API hardening** — normalized known numeric config fields on save, added reusable config-response helpers, and split the `/api/config` GET response assembly into a dedicated helper to keep the route logic smaller and safer
- **Configuration UX** — clarified in the web UI and README which settings apply immediately and which still require `Save & Restart`

### Fixed
- **Diagnostics parsing** — made `pactl`, `bluetoothctl`, and `/proc/meminfo` parsing defensive so malformed or truncated external command output no longer risks `IndexError` during diagnostics/preflight collection
- **Config export secrecy** — `/api/config/download` now produces a share-safe export with password hashes, app secrets, and MA tokens removed instead of returning the raw secret-bearing file
- **Subprocess shutdown race** — `SendspinClient` now snapshots the daemon process/stdin handle before sending commands and uses a thread-safe client snapshot during graceful shutdown
- **Bluetooth churn tracking** — reconnect timestamps are now guarded by a lock so churn-window pruning and threshold checks cannot observe partially updated state

### Added
- **Regression coverage** — added focused tests for defensive diagnostics parsing, config export redaction, numeric config normalization, subprocess command TOCTOU handling, and Bluetooth churn isolation

## [2.31.8] - 2026-03-14

### Fixed
- **Empty-state adapter shortcut** — fixed the redesigned dashboard CTA so `No Bluetooth adapter detected` now jumps to `Configuration → Bluetooth`, opens the adapters card, and prepares a manual adapter row instead of relying on the pre-redesign layout
- **Empty-state scan shortcut** — fixed the `No Bluetooth devices configured` CTA so it now opens `Configuration → Devices → Discovery & import` and starts Bluetooth scanning from the correct redesigned section

## [2.31.7] - 2026-03-14

### Fixed
- **HA login with MFA/TOTP** — fixed the direct Home Assistant login flow so the second-step authenticator form now preserves a valid CSRF token; entering the TOTP code no longer fails with `Invalid session. Please try again.`
- **Regression coverage** — added an auth test that exercises the full `credentials -> MFA -> success` flow to prevent the TOTP step from regressing again

## [2.31.6] - 2026-03-14

### Added
- **Configuration controls** — added `Cancel` to discard unsaved changes, plus new security/runtime settings for session timeout, brute-force protection, and the Music Assistant WebSocket monitor
- **Device management shortcuts** — adapter badges now jump straight to `Configuration → Bluetooth`, custom adapter names are editable, and Music Assistant sync-group badges open the matching MA settings page in a new tab
- **Runtime visibility** — surfaced per-device delay in both card and list views and expanded the list view to expose the same key runtime badge family as the card view

### Changed
- **Configuration UI** — refactored the settings area into a consistent card-based layout with unified headers, helper text, section actions, and a cleaner devices / Bluetooth / MA hierarchy
- **Dashboard polish** — aligned header, cards, list rows, badges, icons, hover actions, and transport/media blocks with the redesign mockup and a shared badge system
- **List view behavior** — default to list view when more than 6 devices are present, remember the user's view choice, add sorting by adapter, and reuse the same adapter/status badge styling as card view

### Fixed
- **Badge consistency** — resolved list/card group badge drift, row-level badge misalignment, duplicated status indicators, and empty placeholder dash badges when sync/runtime data is unavailable
- **Interaction regressions** — fixed card hover squeeze, muted-state color feedback, MA links opening in the same tab, settings gear placement/visibility, and list-row media/action separation
- **List layout** — reduced duplicate routing text, removed redundant `sink ready` noise, narrowed the list volume slider, and kept delay/status/adapter/group badges visually aligned without overlap

## [2.31.0] - 2026-03-14

### Changed
- **UI redesign** — complete visual overhaul of device cards, header, and toolbar aligned with Home Assistant / Music Assistant design guidelines
- **Device cards** — new card layout with speaker icon, SVG chip indicators (BT rune, MA house logo, chain-link sync), single-row transport controls + volume slider, now-playing section with album art
- **Header** — user icon + username before sign-out (exit-door SVG icon), BT/MA SVG icons in health indicator pills
- **Toolbar** — split into Filter Bar (group/adapter/status filters + grid/list view toggle) and Action Bar (select-all, group volume, mute/pause/reconnect/release)
- **Icons** — replaced all Unicode/emoji icons with consistent MDI-style SVG icons throughout the interface
- **Volume slider** — MA-style colored track fill via CSS custom property `--slider-fill`

### Added
- **View toggle** — switch between grid and list layout for device cards
- **Adapter filter** — filter devices by BT adapter (hci0, hci1, etc.)
- **Status filter** — filter devices by status (playing, idle, reconnecting, released, error)
- **Group actions** — bulk Reconnect / Release buttons in action bar

## [2.30.8] - 2026-03-13

### Fixed
- **CI: pytest dependency** — added `libdbus-1-dev` to the pytest CI job so `dbus-python` builds successfully in the GitHub Actions runner

## [2.30.7] - 2026-03-13

### Added
- **CSRF protection** — login form now includes a per-session CSRF token validated on every POST; timing-safe comparison via `hmac.compare_digest`
- **CSP headers** — `Content-Security-Policy` and `X-Content-Type-Options: nosniff` added to all responses; restricts script/style/image/connect sources to `'self'`
- **Adapter input validation** — `validate_adapter()` helper rejects command-injection payloads (newlines, shell metacharacters) before any `bluetoothctl` interaction
- **Config upload size limit** — uploaded config files are capped at 1 MB (returns 413 if exceeded)
- **pytest in CI** — test suite now runs automatically in GitHub Actions alongside lint checks

### Fixed
- **XSS in HA auth page** — `ma_url` query parameter is now escaped via `json.dumps()` and validated for safe URL schemes before injection into the inline JavaScript template
- **MA monitor event loss** — `_drain_cmd_queue`, `_send_queue_cmd`, and `_refresh_stale_player_metadata` no longer silently discard interleaved WebSocket events; non-matching messages are logged at DEBUG level
- **mDNS discovery thread safety** — replaced `asyncio.ensure_future()` with `run_coroutine_threadsafe()` in zeroconf callback (was called from wrong thread)
- **Volume race condition** — `prev_volume` and `new_volume` reads are now atomic within the same `_status_lock` scope in `_read_subprocess_output`
- **Scan job reference leak** — `get_scan_job()` now returns a shallow copy instead of a mutable reference to internal state
- **MA monitor stop delay** — `MaMonitor.stop()` now closes the WebSocket connection to unblock pending `ws.recv()` calls
- **Deprecated asyncio API** — replaced `asyncio.get_event_loop()` with `get_running_loop()` in MA monitor
- **Error message leakage** — 18 API endpoints no longer expose internal exception details (`str(e)`) in error responses; errors are logged server-side via `logger.exception()`
- **DEFAULT_CONFIG shared references** — `load_config()` now uses `copy.deepcopy()` to prevent mutation of default `BLUETOOTH_DEVICES` list across config instances
- **Dead code cleanup** — removed 8 unused regex patterns from `routes/api.py` (duplicated from `api_bt.py`)

### Changed
- **Dependency pinning** — added upper bounds: `zeroconf<1.0`, `ruff<1.0`, `mypy<2.0`

## [2.30.6] - 2026-03-13

### Added
- **Version badge → release notes** — clicking the version badge in the header opens the corresponding GitHub release page
- **Username → profile link** — clickable username in the header links to the user's MA profile (when MA is connected) or HA profile (based on auth method); in standalone mode the username is displayed in the status bar row

### Fixed
- **Empty state full-width** — "No Bluetooth devices configured" block now spans the full grid width instead of occupying a single column
- **Card hover isolation** — hovering a device card no longer causes all cards in the same row to expand; each card has independent hover behavior
- **Album art popup clipped** — album art popup on track name hover was hidden by `overflow: hidden` on parent containers; removed the clipping so the popup renders correctly

## [2.30.5] - 2026-03-13

### Added
- **BT Info modal** — device info now opens in a styled modal dialog with Copy button instead of a plain `alert()`
- **BT adapter reboot** — ↻ Reboot button on each adapter row (power off → 3s delay → power on)
- **Scan cooldown countdown** — Scan button shows remaining seconds during cooldown period; server returns `retry_after` in 429 response
- **Config download/upload** — ⬇ Download and ⬆ Upload buttons in the config section; download filename includes bridge name and timestamp (`{bridge}_SBB_Config_{datetime}.json`); upload validates JSON and preserves sensitive keys (passwords, tokens)
- **BT device info in bugreport** — bug report now includes paired/trusted/connected status from `bluetoothctl info` for each configured device

### Fixed
- **Mute indicator stuck after startup** — `_startup_unmute_watcher` now emits a status update after unmuting the PA sink, so the UI correctly reflects the unmuted state
- **Startup unmute timeout** — reduced from 60s to 15s for faster mute indicator update when no audio is playing
- **Adapter power ANSI stripping** — `bluetoothctl` output is now cleaned of ANSI escape codes before checking for success indicators

### Changed
- **Paired devices button layout** — Add button first, then MAC/Name, then grouped action buttons (BT Info, Reset & Reconnect, ✕) with hover isolation
- **Scan results button order** — Add before Add & Pair (renamed from Pair & Add)
- **Toolbar button order** — Adapters: + Add Adapter before ↺ Refresh; Devices: + Add Device before 🔍 Scan
- **Config save bar layout** — left group (Save, Save & Restart), right group (⬇ Download, ⬆ Upload)

## [2.30.0] - 2026-03-13

### Added
- **Smooth restart** — optional mute-before-restart flow with a 6-step progress bar; enabled by default (`SMOOTH_RESTART` config key). Non-smooth mode still shows a 5-step progress bar (skips mute step)
- **Buffering state indicator** — when MA reports the group is playing but the bridge isn't receiving audio yet, the card shows `▶ Buffering` with a pulsing orange dot instead of misleading `⏸ Stopped`
- **3-column card layout** — device cards now arrange in up to 3 columns on wide screens

### Fixed
- **Mute button console error** — removed orphaned `onclick="toggleMute(i)"` attribute that threw `ReferenceError` (actual handler was already attached via `addEventListener`)
- **Eq-bars always visible** — broken CSS comment caused equalizer bars to display permanently; now correctly hidden when not playing
- **Eq-bars min-width** — equalizer animation no longer overflows narrow card layouts
- **Log level detection** — error/critical highlighting in the report link now uses structured log format instead of fragile substring matching

## [2.29.0] - 2026-03-13

### Added
- **Disable button on device card** — `⛔ Disable` button in the card actions row for quick device disable/enable directly from the dashboard
- **Report error highlighting** — Report link in the header turns yellow when the last 20 log entries contain ERROR or CRITICAL messages

### Fixed
- **Released → disabled persistence bug** — devices in "BT Released" state were incorrectly persisted as `enabled: false` on restart, causing them to be fully skipped on next boot; startup sync now only writes `enabled: true` for non-released devices
- **Player-id group matching** — MA group badge now uses stable `player_id` (UUID) for matching instead of fuzzy player name comparison, fixing group display on hosts where the bridge suffix differs

### Changed
- **Device card redesign** — cards restructured from 5-column grid to a row-based layout: status dots with color classes (green/red/orange/grey), chip-style sync display, `±Nms` delay format, `⏸` pause symbol, shuffle/repeat always visible
- **Bug report modal yellow accent** — bug report modal header and primary button changed from blue to amber (`#f59e0b`) for visual distinction from the green update modal

## [2.28.2] - 2026-03-13

### Fixed
- **Released state persistence** — device "Release" state now persists across service restarts via `released` flag in config.json; previously was runtime-only
- **Player name matching with bridge suffix** — `persist_device_enabled` and `persist_device_released` now correctly match runtime names like `"ENEBY Portable @ LXC"` against config entries `"ENEBY Portable"` using prefix matching

### Changed
- **Playback progress inline** — track duration (`1:23 / 4:56`) moved inline with the progress bar (same flex row, to the right)
- **Volume slider aligned** — volume column reordered (audio format info first, slider below) so the slider visually aligns with the playback progress bar; slider height matched at 3px
- **Sink name removed** — sink name display removed from the volume column
- **Delay always visible** — `static_delay_ms` value in the Sync column is now always shown (was hover-only)
- **Shuffle/repeat always visible** — shuffle and repeat buttons in the Playback column are now always visible when MA is active (were hover-only)

## [2.28.1] - 2026-03-13

### Changed
- **Update modal redesign** — green (`--success-color`) accent header with SVG arrow icon and ✕ close, version comparison row (`v2.28.0 → v2.28.1`), SVG icons on all buttons replacing emoji, Escape key support, fade-in/slide-up animation, theme CSS variables throughout
- **Adapter badge** — BT adapter name (`hci0`) in connection column now styled as a neutral badge matching the `api` badge pattern
- **Equalizer placement** — eq-bars now sit immediately after the player name instead of being pushed to the right edge
- **Column labels removed** — Playback, Volume, and Sync column headers removed (content is self-evident)

## [2.28.0] - 2026-03-13

### Fixed
- **BT remove endpoint crash** — `POST /api/bt/remove` returned a bare `bool` from `validate_mac()` instead of a Flask response, causing 500 on Proxmox/LXC deployments
- **HA addon username display** — read `X-Remote-User-Display-Name` / `X-Remote-User-Name` headers from the HA Supervisor Ingress proxy instead of showing generic "HA User"

### Changed
- **Bug report modal redesign** — accent header bar with `--primary-color`, SVG icons (bug, GitHub, copy, info) replacing emoji, CSS spinner for loading, inline validation, Escape key support, fade-in/slide-up animation, dark-themed diagnostic preview
- **Compact connection column** — status text hidden by default (colored dots with native tooltips are sufficient); MAC and server URI hover details removed; column shrunk from ~176px to 85px fixed width, giving ~100px more to the identity column
- **Identity column optimization** — restructured into clean two rows: player name (with ellipsis truncation) + eq-bars on top, badges (released, battery, group) in a compact meta row below; MAC address and WebSocket URL removed from dashboard display

## [2.27.1] - 2026-03-12

### Added
- **Remove from BT stack** — "Already paired" devices in Configuration now have a ✕ button to unpair from BlueZ without needing `bluetoothctl` manually

### Changed
- **Restart indicator redesign** — moved from a standalone full-width banner into the header card; emoji icons replaced with CSS spinner/checkmark/warning SVGs; uses theme-native white-on-primary colors (no more hardcoded pastel backgrounds that break dark mode); eliminates layout shift

## [2.27.0] - 2026-03-12

### Added
- **Two-tier device enabled/disabled** — global `enabled` flag fully removes a device from the BT stack, PulseAudio, and Music Assistant (player unregistered); separate BT Release/Reclaim remains for Bluetooth-only control
- **Config checkbox** — enable/disable toggle moved to a checkbox in Configuration → Devices for immediate effect without a separate UI section
- **Smart health indicators** — manually released devices (grey) are excluded from health totals; auto-disabled devices (churn/reconnect threshold) show as amber "needs attention" with warning badge

### Fixed
- **MA player cleanup on disable** — disabling a device now stops its daemon subprocess, which disconnects from MA WebSocket and removes the player registration

## [2.26.5] - 2026-03-12

### Fixed
- **PA sink routing correction** — after audio starts, each subprocess now verifies and corrects its sink-input routing via `move-sink-input`; fixes silent speakers when PulseAudio ignores `PULSE_SINK` and routes to the default sink instead (especially with multiple BT speakers)
- **Equalizer indicator accuracy** — `audio_streaming` flag is now set on stream `start` event (not only on format change), so re-anchors and track changes with the same codec no longer leave the equalizer stuck red while audio is playing

## [2.26.4] - 2026-03-12

### Fixed
- **Server-side graceful shutdown** — on SIGTERM the bridge now mutes PA sinks directly instead of pausing MA players; works correctly for all restart triggers (systemd, Docker, HA auto-update, CLI) not just the web UI
- **False zombie-playback restarts** — zombie detection (red equalizer → subprocess restart) now only triggers when audio has never arrived in the current subprocess session; brief streaming gaps during re-anchor, group sync, or track changes no longer cause unnecessary restarts

## [2.26.3] - 2026-03-12

### Fixed
- **Daemon crash after 60 s idle** — the startup unmute watcher task was included in `asyncio.wait(FIRST_COMPLETED)`, so its completion after the 60 s timeout killed the entire daemon subprocess; now runs as fire-and-forget
- **Spurious unmute on timeout** — unmute on timeout is now skipped if audio never started streaming, preventing unnecessary PA operations on idle players

## [2.26.2] - 2026-03-12

### Fixed
- **Ingress username display** — HA Ingress sessions now show the actual username (from `MA_USERNAME` in config) instead of generic "HA User"; the previous approach used `SUPERVISOR_TOKEN` against `core/api/auth/current_user` which returns 401 for addon tokens

## [2.26.1] - 2026-03-12

### Improved
- **Smooth restart** — all local PA sinks are muted before restart and auto-unmuted after audio streaming stabilises (1.5 s settling window, 60 s safety timeout); eliminates audible glitches during service restarts
- **Sink name cache** — `LAST_SINKS[mac]` persisted to config.json; on restart the cached sink is tried first, skipping the 3 s A2DP profile delay and retry loop when the sink is still valid

### Removed
- **Legacy move-sink-input routing** — `_ensure_sink_routing()` and `_sink_routed` flag removed from `BridgeDaemon`; the `PULSE_SINK` subprocess architecture makes reactive sink-input moves unnecessary (and they caused PA glitches triggering re-anchoring)

## [2.26.0] - 2026-03-12

### Added
- **HA username in header** — Ingress sessions now resolve and display the HA owner's display name via the Core API, cached per session
- **Re-check button in update dialog** — clicking the version badge opens the update dialog with a 🔄 Re-check button to re-query GitHub for the latest version

### Fixed
- **SSP passkey auto-confirm** — `pair_device()` now reads `bluetoothctl` stdout in real-time and auto-sends `yes` on "Confirm passkey" / "Request confirmation" prompts, enabling pairing for TWS earbuds (e.g. HUAWEI FreeClip) that require SSP confirmation
- **TWS earbuds D-Bus resilience** — widened exception handling in D-Bus property/method calls to catch `DBusException` from stale BlueZ objects (TWS in charging case); added auto-reconnect when earbuds reconnect externally but player isn't running

## [2.25.1] - 2026-03-12

### Fixed
- **LXC install/upgrade scripts synced** — `install.sh` was missing 5 route modules (`api_bt`, `api_config`, `api_ma`, `api_status`, `_helpers`), `update_checker.py`, the `demo/` module, and 3 logo files; `upgrade.sh` was missing the same logo files — fresh LXC installs and upgrades now deploy all files correctly

## [2.25.0] - 2026-03-12

### Security
- **Session variable leak fixed** — `_ha_login_user` MFA session key is now cleared after every successful auth path and on GET /login, preventing cross-user session leakage
- **MAC address validation** — all Bluetooth MAC inputs validated against strict regex before passing to `bluetoothctl`, preventing command injection
- **Fallback patterns removed** — 3 API endpoints no longer silently fall back to the first device when `player_name` is missing; return proper 400 errors instead

### Improved
- **Login handler split** — monolithic 260-line `login()` refactored into 4 per-flow handlers (`_handle_ma_login`, `_handle_ha_via_ma_login`, `_handle_ha_direct_login`, `_handle_local_password_login`) for maintainability
- **Client lookup helper** — shared `get_client_or_error()` and `validate_mac()` in `routes/_helpers.py` eliminate duplicated device lookup logic across BT endpoints
- **Exception handling narrowed** — top-20 broad `except Exception` clauses replaced with specific types (`OSError`, `subprocess.SubprocessError`, `json.JSONDecodeError`, `ValueError`) across 6 modules; silent `debug` upgraded to `warning` for user-visible failures
- **Atomic config writes** — `update_config()` uses `tempfile.NamedTemporaryFile` + rename for crash-safe persistence
- **BT scan cooldown** — 30-second cooldown between scans prevents adapter churn (HTTP 429)
- **Named constants** — 7 magic numbers in `bluetooth_manager.py` replaced with descriptive constants
- **IP detection deduplicated** — single `get_local_ip()` in `config.py` replaces 3 inline implementations
- **Module-level imports** — `ThreadPoolExecutor` and `run_update_checker` moved from inside functions to module scope
- **Type annotations** — added generic type parameters to client lists in `state.py` and `sendspin_client.py`
- **Config scoping fix** — `_needs_migration` initialized before try block in `load_config()` to prevent `UnboundLocalError`

### Tests
- Added `tests/test_client_lookup.py` — 17 tests for `get_client_or_error()` and `validate_mac()` (multi-device lookup, injection attempts)
- Added `tests/test_mfa_session.py` — 9 tests for MFA session lifecycle (variable cleanup, cross-user leak prevention)
- Added `tests/test_scan_cooldown.py` — 4 tests for BT scan cooldown (429 during cooldown, 409 concurrent, timestamp update)

## [2.24.4] - 2026-03-12

### Fixed
- **AUTO_UPDATE setting now persisted** — was silently stripped on save due to missing whitelist entry
- **Auth requires password** — enabling authentication without setting a password first is now blocked with a clear error (server-side validation + client-side prompt); skipped in HA addon mode where HA login is used

## [2.24.3] - 2026-03-12

### Added
- **Dual issue templates** — separate templates for manual filing (`bug_report.yml` with dropdowns) and auto-fill from Report button (`bug_report_auto.yml` with pre-filled fields)
- **Enriched auto-fill diagnostics** — Report button now pre-fills runtime status (uptime, RAM, device states, MA integration), environment details (Python, BlueZ, kernel, audio server), and last 3 log errors/warnings
- **Bluetooth SVG icon** in header — replaced music note emoji with monochrome BT icon

### Changed
- **Unified System Info field** — merged Environment, Runtime Status, and Device Count into a single `System Info` code block with clean key: value format
- **Issue template field mapping** — fixed prefill parameter names to match GitHub template field IDs; added GitHub auth hint in modal
- Additional Context field changed from textarea to single-line input in auto-fill template

## [2.24.0] - 2026-03-12

### Added
- **Bug report button** — one-click diagnostics: auto-collects system info, masks sensitive data, opens a pre-filled GitHub issue with a short summary, and downloads a detailed diagnostics file for attachment
- **Auth warning banner** — yellow banner when web UI authentication is disabled, with a direct link to enable it in Configuration
- **Enriched diagnostics** — version, runtime, uptime, Python, platform, BlueZ, audio server, memory (RSS), MA version, and subprocess status now shown in the Diagnostics section
- **Diagnostics download** — "Download report" button in Diagnostics exports a full plain-text report (environment, devices, subprocesses, MA integration, config, and recent logs)
- **Log file download** — "Download" button in the Logs section exports last 500 lines as a text file
- **MA server version** — displayed in both short and full bug reports, sourced from WS handshake

### Changed
- **Restart banner redesigned** — replaced compact status counters (BT/PA/SS/MA) and expandable details with sequential action steps and a progress bar; device status is already visible in device cards
- **Header icons** — Report, Docs, and GitHub links now use monochrome inline SVG icons
- **Bug report format** — full downloadable report switched from Markdown to plain text; short report includes last 3 warnings/errors
- **Bug report validation** — submit button disabled until title and description are filled; empty fields highlighted on click

### Internal
- Extracted `_build_full_text_report()` for reuse between bug report and diagnostics download
- Extracted `_read_log_lines()` for reuse between logs API and download endpoint

## [2.23.12] - 2026-03-12

### Added
- **Auto-update for LXC** — new `AUTO_UPDATE` toggle in Configuration → Updates; when enabled, the hourly update checker automatically applies new versions via `upgrade.sh` (LXC/systemd only, off by default)

## [2.23.11] - 2026-03-12

### Added
- **Update modal dialog** — clicking the update badge opens a modal with release notes preview and platform-aware action buttons instead of a plain browser confirm()
- **"📋 Release Notes"** button links to the GitHub release page
- **"⬆ Update Now"** for LXC/systemd one-click apply; **"🏠 Update in HA"** for addon mode; **"📋 Show Instructions"** for Docker
- **One-click update** — `/api/update/apply` endpoint triggers `upgrade.sh` on LXC/systemd, service restarts automatically

### Changed
- Release notes in modal are cleaned from markdown (headers, bold) and displayed with bullet-point formatting

## [2.23.10] - 2026-03-12

### Added
- **Runtime type badge** — header shows LXC, Docker, or HA Addon pill with contrasting style
- **Update check button** — "⟳ up to date" pill acts as manual check trigger with spinner animation; morphs to green "⬆ vX.Y.Z" link when update found
- **Health indicators** — BT x/n · MA x/n with color-coded dots (green/yellow/red) and ▶ playback count

### Changed
- Header redesigned to compact 2-row layout; Docs/GitHub links moved to header actions area
- Version and system info text brightened for better contrast on blue header

## [2.23.9] - 2026-03-12

### Changed
- **HA addon auth refactoring** — authentication is always enforced in addon mode (no `auth_enabled` toggle); only HA Core login_flow offered (with 2FA/MFA support); Ingress auto-auth unchanged
- **HA username in session** — logged-in HA username stored in session and displayed next to the "Sign out" link
- **Addon config** — added `tmpfs`, `backup_exclude`, `auth_api`, `panel_admin` capabilities; removed `auth_enabled` option

### Fixed
- **AppArmor enforce mode** — rewrote profile with blanket `file,` + `signal,` rules (matching Music Assistant addon pattern); granular path rules broke on Docker overlayfs

## [2.23.6] - 2026-03-12

### Fixed
- **AppArmor enforce mode** — switched from complain to enforce; fixed `ix` → `rix` for shell script execution (kernel needs `read` for shebang parsing), added `signal (send)` rule for S6 process management, broadened `/run/` and `/tmp/` rules to match official HA addon profile
- **`/s6-init` wrapper** — removed runtime `chmod +x /init` (already set at build time); avoids AppArmor write-deny on `/init`

## [2.23.1] - 2026-03-11

### Added
- **S6 overlay process supervision** — proper PID 1 (zombie reaping, signal forwarding) via S6 overlay v3.2.0.2; replaces Docker `--init` flag
- **AppArmor profile re-enabled** — security profile enabled in complain mode for safe initial testing; was disabled since v2.15.5

### Changed
- **Dockerfile** — ENTRYPOINT changed from `/app/entrypoint.sh` to `/init` (S6 manages lifecycle); added `curl`/`xz-utils` to runtime deps
- **HA addon Dockerfile** — simplified to thin wrapper (removed redundant `run.sh`)
- **HA addon config** — `init: false` (S6 is in the image), `apparmor: true`

### Removed
- **`ha-addon/run.sh`** — redundant; `entrypoint.sh` already handles HA addon detection and config translation

## [2.23.0] - 2026-03-11

### Added
- **Demo Mode** — full UI demo with emulated BT devices and MA playback; no hardware needed. Enable with `DEMO_MODE=true` or try at [sendspin-bt-bridge.onrender.com](https://sendspin-bt-bridge.onrender.com)
- **Render.com deployment** — one-click deploy button for live demo hosting (`render.yaml` Blueprint)
- **Universal version update checker** — background task polls GitHub releases API hourly; shows green update badge in UI header linking to release notes
- **Update API endpoints** — `POST /api/update/check` (force check), `GET /api/update/info` (cached info + platform instructions), `POST /api/update/apply` (one-click LXC upgrade via `upgrade.sh`)
- **Platform-aware update instructions** — LXC: one-click "Update Now" button; Docker: `docker compose pull` command; HA addon: directs to Supervisor

### Fixed
- **Demo playback controls** — stateful pulse mocks, `is_running()` sentinel, MA command patches for realistic demo behavior
- **LXC upgrade.sh** — added missing route modules (`api_bt.py`, `api_config.py`, `api_ma.py`, `api_status.py`), `update_checker.py`, and `demo/` module

## [2.22.3] - 2026-03-11

### Fixed
- **Auto-detect MA auth provider** — standalone bridges now auto-set `MA_AUTH_PROVIDER=ha` when MA reports `homeassistant_addon=true`, showing the correct HA login flow instead of failing MA username/password form
- **mDNS discovery enrichment** — `homeassistant_addon` flag now included in mDNS-discovered servers (consistency with `validate_ma_url`)

### Changed
- **Config section dividers** — softened from bright blue 3px to subtle 2px semi-transparent lines to avoid confusion with interactive elements
- **Config section heading size** — reduced from 20px to 16px for clearer visual hierarchy between module headers and field labels
- **Paired devices auto-collapse** — list auto-collapses when more than 5 devices, with click-to-expand toggle

## [2.22.2] - 2026-03-11

### Added
- **Music Assistant as web UI auth provider** — authenticate the bridge web UI using MA credentials (direct MA login or HA-via-MA flow)
- **HA-via-MA authentication** — new auth provider lets users authenticate against Home Assistant through the MA API endpoint; password auth always remains available as fallback
- **Separate MUTE_VIA_MA setting** — independent toggle for routing mute/unmute through Music Assistant API, separate from volume routing (`VOLUME_VIA_MA`)
- **Phased restart progress** — Save & Restart shows real-time initialization status per subsystem (BT · PA · SS · MA) with expandable per-device details on click

### Fixed
- **HA login flow_id format** — accept both dashed UUID and plain 32-char hex formats from HA Core
- **Mute via MA now syncs PulseAudio sink** — previously only sent command to MA, leaving PA sink in the old mute state
- **SSE mute state race condition** — optimistic mute UI no longer reverted by SSE updates (2 s debounce window)
- **Config footer padding** — removed excessive spacing between last config group and footer

### Changed
- **Log Level selector** moved from Configuration section to Logs toolbar for quicker access
- **Mute performance** — optimistic UI update (instant icon toggle) with reduced API timeouts (5 → 2 s, 10 → 3 s)

## [2.22.0] - 2026-03-11

### Added
- **MA beta 2.8+ authentication** — direct HTTP login fallback supports both stable (`{"username", "password"}`) and beta (`{"credentials": {...}, "provider_id": "builtin"}`) API formats; always falls through to direct HTTP when the library login fails
- **Context-aware empty state** — when no devices are configured, the CTA detects whether a Bluetooth adapter is present: if not, links to the Adapters section with auto-refresh; if present, links to Devices and launches a scan
- **Static Save button** in the configuration section footer (in addition to the sticky bar)
- **Empty-state call-to-action** when no Bluetooth devices are configured

### Fixed
- **Token persistence after MA login** — `loadConfig()` is now called after successful login before marking the form dirty, preventing "Save & Restart" from overwriting the new token with the old form value
- **"Unsaved changes" after MA login** — the dirty indicator now appears after all 5 login success paths (builtin, HA OAuth, HA credentials, silent auth, addon)
- **Phantom player card** when bridge has zero configured clients
- **System info API** now returns data even with zero configured devices
- **Config dirty state** now triggers when adding or removing BT devices via scan/manual add
- **JWT details toggle arrow** stuck in rotated state (CSS fix)

### Changed
- **MA auth panel redesigned** — API URL and token fields moved under the Reconfigure link; URL field removed (hidden input, no longer duplicated); "Sign in with Home Assistant" renamed to "🔑 Get token automatically"
- **Config section headings** restyled with bolder text and accent dividers

## [2.21.0] - 2026-03-11

### Changed
- **`BLUETOOTH_MAC` fully deprecated** — legacy single-device `BLUETOOTH_MAC` config key auto-migrates to `BLUETOOTH_DEVICES` array on startup; removed from config schema, API whitelist, web UI, entrypoint, Docker Compose, install scripts, and all documentation (23 files updated)
- **Legacy config keys removed** — `BRIDGE_NAME_SUFFIX` (unused since v2.13.0), `LAST_VOLUME` (singular, superseded by per-MAC `LAST_VOLUMES`), `keepalive_silence` (boolean, replaced by `keepalive_interval` integer), and `port` (renamed to `listen_port`) all cleaned up with auto-migration where needed
- **Dead code removed** — `get_client_status()` backward-compat function, unused re-exports from the v2.20.3 API modularization, `_save_device_volume` internal alias
- **Config schema completed** — `TRUSTED_PROXIES` and `MA_USERNAME` added to `allowed_keys` and `DEFAULT_CONFIG` so they survive config round-trips

## [2.20.5] - 2026-03-11

### Fixed
- **"Show all" checkbox overflow** — moved label text before the checkbox and added right margin to align the checkbox above the "Add" buttons in the paired-devices list

### Changed
- **Documentation update** — refreshed all docs to v2.20.4: 6 screenshots recaptured, version refs updated across 14 files, API split and test count reflected, web-ui.md rewritten (dissolved Advanced Settings, added battery badge docs)

## [2.20.4] - 2026-03-11

### Fixed
- **JWT token section folding** — hide native disclosure marker on `<details>` summary; add CSS `::before` ▶ with rotation on open (consistent with other collapsible sections)
- **MA API token hint** — corrected path from "Settings → API Tokens" to "Settings → Profile → Long-lived access tokens"

## [2.20.3] - 2026-03-11

### Changed
- **api.py monolith split** — extracted 3 178-line `routes/api.py` into 5 focused modules: `api.py` (581, core volume/mute/pause), `api_bt.py` (396, BT scan/pair/reconnect), `api_ma.py` (1 216, MA integration & OAuth), `api_config.py` (502, config & settings), `api_status.py` (647, status/SSE/diagnostics)
- **Thread-safe `_clients` access** — added `state.get_clients_snapshot()` helper; fixed 6 unprotected iterations in `api_config.py`, `api_ma.py`, and `ma_monitor.py`
- **Thread-safe `MaMonitor._msg_id`** — replaced bare `int` counter with `itertools.count(1)` (atomic under CPython)
- **Shared MAC validation** — extracted `is_valid_mac()` and canonical `_MAC_RE` into `services/bluetooth.py`; removed duplicate regex in config routes
- **Class docstrings** — added design-rationale docstrings to `DeviceStatus`, `SendspinClient`, and `BluetoothManager`
- **Return type hints** — added missing `-> None` annotations on `SendspinClient.run()` and `.stop()`

### Fixed
- **Dead reconnect endpoint** — `api_bt_reconnect()` was missing its `@api_bp.route("/api/bt/reconnect")` decorator; endpoint was unreachable
- **`postMessage('*')` security** — HA OAuth popup callback now uses `window.location.origin` instead of wildcard origin

## [2.20.2] - 2026-03-10

### Fixed
- **MA addon mode auto-detection** — detect `homeassistant_addon` from MA server info, not just the bridge's own addon flag; fixes "Sign in with Home Assistant" button not appearing when bridge runs standalone but MA is an HA addon
- **HA OAuth fallback on builtin login failure** — when builtin username/password login returns 401, automatically attempt HA OAuth flow with the same credentials; prompts for TOTP if MFA is required
- **Reconfigure addon detection** — opening the Reconfigure form now probes the MA `/info` endpoint to detect addon mode dynamically

## [2.20.1] - 2026-03-10

### Changed
- **Delay replaces Format in device row** — Delay (ms) shown in the main device row; Format moved to the expandable detail sub-row
- **Adapters panel restyled** — bordered card with hover highlight, consistent with paired/scan device lists
- **Adapters toolbar** — Refresh and +Add buttons moved below the table (consistent with Devices section)
- **Static Save & Restart button** — always-visible button at the bottom of the Configuration form as a safety net

## [2.20.0] - 2026-03-10

### Changed
- **Advanced settings dissolved** — moved Listen Address, Port, Delay, and Keep-alive fields out of the separate Advanced panel into their proper sections (General, Bluetooth, Music Assistant); Advanced section removed entirely
- **MA form auto-collapse** — when Music Assistant is connected, the configuration form collapses to a summary with a "Reconfigure" link; reduces visual noise for the common case
- **Auth fields auto-hide** — password/secret fields hidden when authentication is disabled; shown only when toggled on
- **BT device rows collapsed by default** — advanced detail sub-row starts hidden regardless of field values; expand chevron moved to the left side of each row for conventional tree-style interaction
- **Scan/paired device list UX** — entire row is clickable (not just the Add button); hover highlight on rows; Add buttons aligned in a vertical column independent of device name length
- **Toolbar button order** — Scan button moved before +Add Device for discovery-first workflow
- **Adapter tooltip** — BT adapter select shows "Powered on/off" status on hover

### Fixed
- **False dirty state on page load** — config form no longer shows unsaved-changes indicator immediately after loading; `_configLoading` guard prevents programmatic field population from triggering dirty tracking
- **Duplicate save buttons removed** — kept only the sticky bottom bar; added `padding-bottom: 80px` to prevent overlap with the last form field

## [2.19.0] - 2026-03-10

### Changed
- **Configuration UI overhaul** — restructured into clearly labeled sections (General, Bluetooth, Music Assistant, Advanced, Authentication) with icon headings and visual separation
- **Music Assistant Integration promoted** — moved out of Advanced settings into its own top-level section for immediate discoverability
- **Save buttons moved to bottom** of the form (standard UX pattern)
- **Sticky save bar** — fixed bottom bar appears when config has unsaved changes, visible regardless of scroll position
- **BT Devices table simplified** — main row shows Name, MAC, Adapter, Format; advanced fields (Listen Address, Port, Delay, Keep-alive) in expandable detail sub-row that auto-opens when non-default values exist
- **Shortened labels** — concise primary text with secondary hint descriptions instead of verbose inline paragraphs

## [2.18.3] - 2026-03-10

### Fixed
- **MA Ingress JSONRPC response parsing** — handle both raw string and `{"result": "..."}` response formats from MA's `auth/token/create` endpoint; added debug logging for raw response

## [2.18.2] - 2026-03-10

### Fixed
- **MA Ingress connectivity** — in HAOS, the bridge addon can't reach MA's Ingress port via `localhost:8094` because each addon runs in its own Docker container; now discovers the MA addon's Docker hostname via the Supervisor API and connects via Docker DNS (e.g. `http://d5369777-music-assistant:8094`)
- Added `hassio_api` and `homeassistant_api` permissions to addon config

## [2.18.1] - 2026-03-10

### Fixed
- **websockets compatibility** — fixed `proxy` kwarg error on older websockets versions (<14) in HAOS addon container; all WebSocket connections now use a compatibility wrapper

## [2.18.0] - 2026-03-10

### Added
- **Passwordless MA auth in addon mode** — when running as an HA addon, clicking "Sign in with Home Assistant" now creates an MA API token silently via Ingress JSONRPC — no credentials popup needed; the bridge reads the HA access token from the browser, verifies the user via HA WebSocket, and calls MA's Ingress endpoint to create a long-lived token

### Removed
- Removed broken `_ha_authorize_with_token()` — HA's `/auth/authorize` is GET-only and returns 405 for POST requests

## [2.17.12] - 2026-03-10

### Fixed
- **MA auth in Ingress** — silent auth falls back to popup when HA rejects programmatic authorize (405); popup flow works reliably in both Ingress and direct access modes

## [2.17.11] - 2026-03-10

### Fixed
- **Addon MA discovery** — try `localhost:8095` before `homeassistant.local:8095`; host-network HAOS addons can reach MA via localhost since both run on the host network

## [2.17.10] - 2026-03-10

### Changed
- **Simplified MA discovery in addon mode** — in HA addon mode, the bridge now tries `homeassistant.local:8095` first (Supervisor internal DNS) instead of mDNS scan; non-addon discovery chain unchanged
- **Addon mode detection from bridge** — the `/api/ma/discover` response now includes `is_addon` flag based on the bridge's own runtime detection, instead of relying on MA server's `homeassistant_addon` field which was missing via mDNS path
- **Semi-automatic MA auth** — removed automatic silent auth on page load; user now clicks "Sign in with Home Assistant" button explicitly; in Ingress mode this performs one-click silent auth, outside Ingress opens OAuth popup

## [2.17.9] - 2026-03-10

### Fixed
- **MA server discovery in auto mode** — when `SENDSPIN_SERVER=auto`, the discover endpoint now extracts the MA server host from the resolved sendspin WebSocket connection (`connected_server_url`) instead of relying solely on mDNS; this makes silent auth work reliably in HA addon mode
- **zeroconf compatibility** — fixed `TypeError` crash in mDNS discovery callback caused by newer zeroconf versions (0.131+) passing parameters as keyword arguments

## [2.17.8] - 2026-03-10

### Fixed
- **Token reset via web UI** — clearing MA API token and URL fields in the config form now correctly persists empty values; previously the save logic silently restored the old token

## [2.17.7] - 2026-03-10

### Fixed
- **Long-lived MA API token** — the OAuth flow now exchanges the short-lived session JWT (30-day, sliding) for a proper long-lived MA API token (10-year) via WebSocket `auth/token/create` command; previously the bridge stored the session token which expired and could not be used for MA API calls
- **Token regex corruption** — fixed regex in `_ma_callback_exchange` that captured Vue Router hash fragment (`#/`) as part of the JWT token, corrupting it
- **Idempotent token acquisition** — silent auth checks if an existing valid token already matches the target MA URL before initiating OAuth, preventing duplicate long-lived tokens on page reload or addon restart

## [2.17.6] - 2026-03-10

### Fixed
- **MA monitor token refresh** — `MaMonitor` now re-reads credentials from shared state before each reconnect attempt; previously it used the token from init time, so silent auth or manual HA login could not fix a stale/missing token at runtime
- **Backoff reset on new token** — reconnect backoff resets immediately when a new MA token is detected, instead of waiting up to 60 s

## [2.17.5] - 2026-03-10

### Fixed
- **MA addon mode UI** — `maDiscover()` now runs on every page load so the addon-mode UI (HA login button) is shown even when MA token is already saved

## [2.17.4] - 2026-03-10

### Added
- **Silent MA authentication in Ingress mode** — when accessed via HA Ingress, the bridge automatically reads the HA session token from `localStorage` and exchanges it for an MA token without any user interaction
- **`/api/ma/ha-silent-auth`** — new endpoint that accepts an HA access token and performs the full OAuth exchange server-side
- **Auto-discover on page load** — MA server discovery runs automatically when the page loads (no need to click "Discover" first)

### Changed
- **Refactored MA↔HA OAuth helpers** — extracted shared helpers (`_get_ma_oauth_params`, `_ha_login_flow_start`, `_ha_login_flow_step`, `_ma_callback_exchange`, `_save_ma_token_and_rediscover`) to module level for reuse across endpoints

## [2.17.3] - 2026-03-10

### Added
- **Sign in with Home Assistant** — when MA runs as HA addon, a popup-based OAuth flow lets users authenticate with HA credentials (including 2FA/TOTP) to get an MA token; no manual JWT copy needed
- **`/api/ma/ha-login`** — multi-step API endpoint for programmatic HA → MA OAuth token exchange
- **`/api/ma/ha-auth-page`** — self-contained dark-themed popup page for the HA login flow with postMessage communication back to the opener

### Changed
- **MA addon mode UX** — replaced static "use manual JWT" hint with a prominent "Sign in with Home Assistant" button that opens the auth popup

## [2.17.2] - 2026-03-10

### Fixed
- **MA discover reuses known URL** — discover endpoint now checks existing MA_API_URL, SENDSPIN_SERVER config, and connected client hosts before falling back to mDNS scan; instant response instead of 5 s timeout

## [2.17.1] - 2026-03-10

### Fixed
- **MA mDNS discovery** — changed service type from `_music-assistant._tcp` to `_mass._tcp` (matches what Music Assistant actually advertises); discovery now works on HAOS and other installations
- **MA login hint** — clarified that Music Assistant credentials are required (not Home Assistant login); added hint pointing to MA → Settings → Users

## [2.17.0] - 2026-03-10

### Added
- **MA auto-discovery** — `GET /api/ma/discover` finds Music Assistant servers on local network via mDNS (`_mass._tcp`)
- **MA auto-login** — `POST /api/ma/login` with username/password creates long-lived token automatically (no manual JWT copy needed)
- **MA connect UI** — new "Music Assistant Integration" panel in web UI with Discover button, username/password login, connection status; manual JWT token entry preserved as collapsible fallback
- **`services/ma_discovery.py`** — mDNS discovery module using zeroconf; `discover_ma_servers()` and `validate_ma_url()` helpers
- **`MA_USERNAME`** config field — stores connected username for display (password is never stored)
- **6 new tests** — `tests/test_ma_discovery.py` covering discovery, validation, URL normalization, config defaults

### Changed
- **MA auth failure message** — improved guidance in `ma_monitor.py`: points user to web UI for reconfiguration
- **Config POST** — preserves `MA_USERNAME` on form save (same pattern as `MA_API_TOKEN`)

## [2.16.3] - 2026-03-10

### Added
- **`ha-addon/logo.png`** — wide-format logo for HA addon store listing
- **`scripts/rpi-install.sh`** — one-liner Raspberry Pi installer: installs Docker, downloads docker-compose.yml, generates `.env`, interactive BT pairing, starts container
- **Hadolint Dockerfile linting** — `.hadolint.yaml` config + CI job linting both Dockerfiles

### Changed
- **TODO.md restructured** — expanded Done section (v2.15.0–v2.16.3), reorganized remaining items into Next / Future priorities

## [2.16.2] - 2026-03-10

### Added
- **`scripts/rpi-check.sh`** — pre-flight diagnostic script for Docker hosts: checks Docker, Bluetooth, audio system, UID, RAM, architecture; outputs recommended `.env` values
- **`/api/preflight` endpoint** — auth-free JSON endpoint returning platform, audio, Bluetooth, D-Bus, and memory status for setup verification
- **Raspberry Pi installation guide** — dedicated docs page (en/ru) with model-specific instructions, prerequisites, and troubleshooting
- **Startup diagnostics table** — `entrypoint.sh` now prints a structured status summary (platform, audio, BT, D-Bus, config) visible in `docker logs`

### Fixed
- **Docker docs: stale `SYS_ADMIN`** — removed from capabilities table and docker-compose example (was removed from repo in v2.16.0 but docs still listed it)
- **Docker docs: missing env vars** — added `PULSE_SERVER`, `XDG_RUNTIME_DIR`, and `AUDIO_UID` documentation
- **Docker docs: no pre-pairing step** — added explicit "pair speaker on host first" instruction before `docker compose up`

## [2.16.1] - 2026-03-09

### Fixed
- **PyAV armv7l compatibility** — monkey-patch `FlacDecoder._append_frame_to_pcm` to use `len(frame.layout.channels)` instead of `frame.layout.nb_channels` (missing in PyAV <13); fixes silent playback on ARM 32-bit systems with `av==12.3.0`
- **LXC scripts updated** — `install.sh` and `upgrade.sh` document the monkey-patch; `openwrt/README.md` Known Issues section expanded
- **Troubleshooting docs** — added "No sound on armv7l" section (en/ru) with symptom, cause, and fix

## [2.16.0] - 2026-03-09

### Security
- **SSRF prevention in HA auth flow** — `flow_id` is now validated as UUID format before interpolation into HA Core URLs, preventing path traversal to arbitrary endpoints
- **SSE connection limit** — Server-Sent Events endpoint now caps at 4 concurrent connections (was unlimited) with 30-minute max lifetime; prevents Waitress thread pool exhaustion
- **Volume clamping** — all volume entry points (server command, IPC, PulseAudio fallback) now clamp to 0–100 range, preventing speaker damage from malformed payloads
- **MAC address validation** — `bt_remove_device()` and `is_audio_device()` now validate MAC format via regex before passing to `bluetoothctl`, preventing command injection via crafted MAC strings
- **`/api/status` removed from public paths** — endpoint now requires authentication when `AUTH_ENABLED=True`; replaced with minimal `/api/health` for Docker healthcheck

### Fixed
- **SSE not notified on stop** — `stop_sendspin()` now calls `_update_status()` instead of direct dict mutation, so the web UI reflects player stop immediately
- **`_clients` race condition** — all ~15 endpoints iterating the client list now take a snapshot under `_clients_lock`, preventing inconsistent reads during device add/remove
- **Zombie counter race** — `_zombie_restart_count` increment moved inside `_status_lock` to prevent TOCTOU race between concurrent checks
- **Config read without lock** — config file reads in `main()` now use `config_lock` to prevent reading partially-written files
- **`request.get_json()` crash** — `set_volume` endpoint now uses `or {}` fallback (matching other endpoints), preventing `AttributeError` on non-JSON requests
- **Error response info leak** — 15 endpoints replaced `str(e)` in error responses with generic `"Internal error"` message; full details logged server-side
- **`int(cmd["value"])` crash** — invalid volume values in IPC no longer crash the command reader task; wrapped in `try/except` with warning
- **`set_log_level` injection** — now validated against an allowlist of valid Python log level names
- **`client_id` path traversal** — subprocess settings directory path is sanitized and verified to stay under `/tmp/`
- **`player_names` type validation** — `set_volume` and `set_mute` now reject non-list `player_names` with 400 error
- **`force=True` removed from `set_password`** — Content-Type bypass removed, strengthening CSRF defense
- **`0`-as-falsy in HA config translation** — `pulse_latency_msec=0` and `bt_check_interval=0` no longer silently replaced with defaults
- **`datetime.UTC` compatibility** — replaced with `timezone.utc` across 4 files for Python 3.9+ compatibility

### Changed
- **`_bt_executor` pool size** — increased from 2 to 4 threads to handle concurrent multi-device reconnections
- **Config write durability** — added `fsync()` before atomic rename for power-failure safety on Raspberry Pi
- **D-Bus monitor loop** — now checks `_running` flag for clean shutdown instead of running indefinitely
- **Fire-and-forget tasks** — `BridgeDaemon` now retains references in a `_background_tasks` set to prevent GC collection before completion
- **Callback error logging** — `on_status_change` failures raised from `debug` to `warning` level
- **`postMessage` origin check** — theme injection listener now validates message origin
- **`escHtml()` hardened** — added `"` and `'` encoding for attribute context safety
- **Thread-local event loop cleanup** — `atexit` handler closes leaked asyncio loops in PulseAudio helper threads
- **Brute-force dict capped** — `_failed` rate-limit dictionary limited to 1000 entries with oldest eviction
- **`AUDIO_UID` env var** — PulseAudio socket detection in `entrypoint.sh` now uses `${AUDIO_UID:-1000}` instead of hardcoded UID
- **`SYS_ADMIN` capability removed** — `docker-compose.yml` no longer requests unnecessary capability
- **Dependency bounds tightened** — `waitress<3.0.0` (was `<4`), `websockets<14.0` (was `<16`)
- **mypy checks enabled** — `check_untyped_defs` and `warn_return_any` now active
- **MA API credentials lock** — `set_ma_api_credentials()` now protected by dedicated mutex

### Added
- **`/api/health` endpoint** — lightweight, auth-free healthcheck returning `{"ok": true}`
- **`dev-requirements.txt`** — declares test/lint dependencies (pytest, pytest-asyncio, ruff, mypy)
- **65 new tests** (42 → 107 total) — new test files for `services/bluetooth.py`, `services/pulse.py`, `bluetooth_manager.py`, `services/daemon_process.py`, `scripts/translate_ha_config.py`, `routes/api.py`; shared `conftest.py` with `tmp_config` fixture

## [2.15.8] - 2026-03-09

### Fixed
- **websockets proxy compatibility** — `proxy=None` parameter is now version-gated (websockets ≥15 only); fixes `unexpected keyword argument 'proxy'` error on systems with websockets 14.x

## [2.15.7] - 2026-03-09

### Changed
- **Updated websockets to v15** — raised upper bound from `<15` to `<16`; added explicit `proxy=None` to all `websockets.connect()` calls to prevent unexpected proxy usage (websockets 15 auto-detects system proxies)
- **LXC: added `gcc` and `python3-dev`** to system packages in `install.sh` — enables compilation of sendspin's C volume-scaling extension (`_volume.c`) on platforms without pre-built wheels
- **LXC: bumped sendspin version** in `install.sh` and `upgrade.sh` from `>=5.1.3` to `>=5.3.0` to match `requirements.txt`

## [2.15.6] - 2026-03-09

### Fixed
- **Auto-unmute BT sink on connect** — PulseAudio may silently mute Bluetooth sinks on reconnect; the bridge now ensures the sink is unmuted in `configure_bluetooth_audio()` before restoring volume

### Changed
- **Switched to official PyPI sendspin package** (`>=5.3.0,<6`) — replaced the temporary git fork (`sendspin-cli@5.1.4`); upstream 5.3.2 includes the re-anchor fix and other improvements
- **Removed `git` from Docker builder stage** — no longer needed after switching from git-based pip dependency to PyPI

## [2.15.5] - 2026-03-09

### Fixed
- **Disable custom AppArmor profile** — the restrictive profile still blocked Python module imports on HAOS; temporarily disabled (`apparmor: false`) until a properly tested profile is ready

## [2.15.4] - 2026-03-09

### Fixed
- **AppArmor profile blocking Python startup on HAOS** — the custom AppArmor profile only allowed `/usr/local/lib/python3*/**` which covers site-packages but not `libpython3.12.so.1.0`. Broadened to `/usr/local/lib/** mr` and `/usr/local/bin/** ix`

## [2.15.3] - 2026-03-09

### Fixed
- **Re-anchor loop on stream start** — upgraded to sendspin-cli 5.1.4 which preserves the re-anchor cooldown timer across `clear()` calls, preventing rapid back-to-back re-anchors that caused audio dropouts on Bluetooth speakers

### Changed
- **Split armv7 CI into separate parallel workflow** — amd64/arm64 images publish immediately; armv7 builds via QEMU independently and appends to the manifest
- **Added git to Docker builder stage** — required for git-based pip dependencies

## [2.15.2] - 2026-03-09

### Fixed
- **ARM64 (aarch64) Docker build** — CI now builds multi-platform images for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`, enabling installation on HA Green, Raspberry Pi 4/5, and other ARM/ARM64 devices
- **Parallel CI builds** — each architecture builds in its own runner (arm64 natively on `ubuntu-24.04-arm`), then manifests are merged — faster than sequential QEMU builds
- **Removed and re-added armv7** — `ha-addon/config.yaml` now correctly declares all three supported architectures

## [2.15.1] - 2026-03-09

### Fixed
- **mypy type error** — added `str | None` annotation to `bluetooth_sink_name` in `SendspinClient`, resolving the pre-existing mypy assignment error
- **ruff format** — wrapped long line in diagnostics endpoint

## [2.15.0] - 2026-03-09

### Added
- **Group player list in Diagnostics** — `/api/diagnostics` now returns full per-member details in `syncgroups[].members[]`: player state, volume, availability, and now-playing info per group
- **Bridge player enrichment in group diagnostics** — bridge members show BT connection, server connection, playing status, audio sink name, and BT MAC address
- **Enabled/Disabled status in Diagnostics** — `devices[]` and group members show `enabled` flag reflecting `bt_management_enabled` state; UI shows amber "Disabled" label for disabled devices
- **Diagnostics UI member list** — group members rendered with status icons (▶ playing, ✓ connected, ⚡ disconnected, 🌐 external, ⊘ disabled/unavailable) and now-playing track info
- **35 new unit tests** — `test_device_status.py` (11), `test_auth.py` (11), `test_state.py` (7), `test_ingress_middleware.py` (5). Total: 53 tests

### Fixed
- **TOCTOU race in zombie playback detection** — `_playing_since` and `_zombie_restart_count` were read outside `_status_lock`, risking `TypeError` on concurrent status updates
- **MA WebSocket command/response mismatch** — `_drain_cmd_queue` now matches responses by `message_id` instead of assuming the next message is the response (interleaved events could be consumed as responses)

### Changed
- **Timezone-aware timestamps** — all `datetime.now()` calls replaced with `datetime.now(tz=timezone.utc)` for consistent, unambiguous ISO timestamps
- **SSE state encapsulated** — `routes/api.py` no longer accesses private `state._status_version`/`_status_condition`; uses new public `get_status_version()` and `wait_for_status_change()` accessors
- **`_TRUSTED_PROXIES` configurable** — `TRUSTED_PROXIES` list in `config.json` extends the default set (127.0.0.1, ::1, 172.30.32.2)
- **`save_device_volume` readability** — replaced chained `__setitem__` lambda with named inner function
- **`DeviceStatus.copy()` return annotation** — now typed as `-> dict[str, object]`
- **Shared `list_bt_adapters()` helper** — extracted `bluetoothctl list` parsing into `services/bluetooth.py`, replacing inline duplicates

### Removed
- **Unused dependencies** — `flask-cors`, `psutil`, `python-dotenv` removed from `requirements.txt` (never imported)

## [2.14.1] - 2026-03-08

### Fixed
- **`DeviceStatus.copy()` included internal `_field_names` frozenset** — the `frozenset` added for fast `__contains__` lookups was included in `copy()` output, causing `TypeError: Object of type frozenset is not JSON serializable` on SSE status stream and API responses. Now excluded from serialization

### Added
- **Player status icons in group tooltip** — group badge tooltip now shows per-member status: ▶ playing, ✓ idle, ⚡ BT disconnected, ✕ offline. External members show ⊘ when unavailable (previously always 🌐)

## [2.14.0] - 2026-03-08

### Fixed
- **`VOLUME_VIA_MA` config silently lost on reload** — key was missing from `load_config()` allowed list, so the setting was dropped every time config was re-read from disk

### Added
- **`update_config()` helper** — atomic read-modify-write pattern extracted into a reusable function, replacing 6 duplicated implementations across `config.py`, `routes/api.py`, and `services/bluetooth.py`
- **MA WebSocket connection reuse** — player and queue commands now prefer the persistent MA monitor WebSocket instead of opening a fresh connection per command (halves latency), with automatic fallback to fresh connection on failure
- **Type hints on `BluetoothManager.__init__`** — `on_sink_found` and `client` parameters now have proper type annotations

### Changed
- **`VOLUME_VIA_MA` cached at module level** — eliminates per-request disk I/O on volume slider drags
- **`DeviceStatus.__contains__`** checks only declared dataclass fields via cached `frozenset` (was using `hasattr` which matched methods and dunders)
- **`asyncio.ensure_future` → `create_task`** — replaces deprecated API
- **`CancelledError` handling in `_keepalive_loop`** — clean shutdown without traceback
- **MA auth failure raises exception** — enables proper exponential backoff instead of rapid 2 s retries
- **`_run_bt_scan` split into 4 helpers** — `_run_bluetoothctl_scan`, `_parse_scan_output`, `_resolve_unnamed_devices`, `_enrich_audio_device` for testability
- **`_enrich_status_with_ma` extracted** from `get_client_status_for` — reduces function complexity
- **`_ma_syncgroup_id` temp key removed** — replaced with index-based `entry_syncgroup` mapping in `_build_groups_summary`
- **`pathlib` import moved to top level** in `bluetooth_manager.py` (was lazy-imported inside method)

## [2.13.3] - 2026-03-08

### Added
- **Battery level indicator** — displays battery percentage next to device name for Bluetooth devices that report battery via `org.bluez.Battery1` (e.g. headphones, portable speakers). SVG battery icon fills proportionally to charge level with color coding: green (>25%), yellow (≤25%), red (≤15%). Mains-powered devices show nothing. Polled every monitoring heartbeat (~10-30s). Requires `Experimental = true` in `/etc/bluetooth/main.conf` on the host for classic BT devices (HFP)

## [2.13.2] - 2026-03-08

### Fixed
- **Local devices shown as external in group tooltip** — on multi-device bridges (e.g. HAOS with 3 speakers), Sendspin assigns unique UUID `group_id` per session even when devices share the same MA sync group. Each local device appeared as 🌐 (external) to the others. Now merges group entries that resolve to the same MA syncgroup before computing external members
- **JS group lookup mismatch after merge** — group badge lookup now matches by `player_name` membership instead of `group_id` equality, since merged groups use one arbitrary Sendspin UUID

## [2.13.1] - 2026-03-08

### Fixed
- **Cross-bridge group badge not showing** — `_build_groups_summary()` compared Sendspin's `group_id` (UUID) against MA's `syncgroup_id` (`syncgroup_XXX`), which are different ID systems and never matched. Now resolves MA syncgroup via player-name mapping first
- **Group badge missing on single-device bridges** — `/api/status` didn't include `groups` in the response for single-device setups (only SSE did). The polling handler in `app.js` reads `status.groups`, so the badge never rendered
- **`group_name` always null in groups API** — Sendspin sends `group_name=None`; now enriched from MA API syncgroup name in `_build_groups_summary()`
- **Waitress 3.x SSE crash** — removed `Connection: keep-alive` hop-by-hop header from SSE response that caused `AssertionError` in Waitress 3.x (strict PEP 3333 enforcement)
- **Devices not rendering in Web UI** — fixed `ReferenceError: data is not defined` in `app.js` where both polling and SSE handlers referenced `data.groups` instead of `status.groups`

### Changed
- **LXC: mask bluetooth.service** — container's `bluetooth.service` is now masked (not just disabled) to prevent accidental restarts that crash `bluetoothd` and break PulseAudio A2DP state
- **LXC: service stop timeout** — `sendspin-client.service` now has `TimeoutStopSec=15` and `KillMode=mixed` to prevent indefinite hang during service stop

## [2.13.0] - 2026-03-08

### Added
- **Auto-populate BRIDGE_NAME** — on first startup, `BRIDGE_NAME` is automatically set to the machine hostname and persisted to `config.json`. Users can see and modify it in the Web UI before adding any Bluetooth devices, eliminating duplicate player names in multi-bridge setups
- **Cross-bridge sync group visibility** — group badge now shows `🔗 GroupName +N` where N is the count of players from other bridges in the same MA sync group. Hovering reveals the full member list with ✓ (local) and 🌐 (external) markers
- **GitHub Issues infrastructure** — 3 structured issue templates (Bug Report, Bluetooth/Audio, Feature Request), 16 project labels, and Discussions Welcome post for community support routing

### Changed
- Bridge name config label updated to indicate auto-fill behavior
- Group badge stays permanently visible (not hover-only) when cross-bridge members exist in the sync group
- `_build_groups_summary()` now enriches group data with external member info from MA API cache

### Removed
- **`BRIDGE_NAME_SUFFIX`** option — no longer needed since `BRIDGE_NAME` is auto-populated with hostname. Existing configs with this option are silently ignored

## [2.12.6] - 2026-03-08

### Fixed
- **SSE through HA Ingress** — Added 2 KB initial padding to flush proxy buffers (Nginx, HA Ingress, Cloudflare) so SSE events stream in real-time instead of arriving in buffered batches
- **SSE reconnect** — Instead of permanently falling back to polling after the first SSE error, the client now retries with exponential backoff (1 s → 16 s, up to 5 attempts) and polls only while reconnecting

## [2.12.5] - 2026-03-08

### Fixed
- **HA Ingress cache bypass** — Static assets (JS/CSS) now use path-based versioning (`/static/v2.12.5/app.js`) instead of query-string (`?v=`). HA Ingress proxy strips query parameters, causing stale cached assets to be served; embedding the version in the URL path guarantees a fresh fetch on every upgrade

## [2.12.4] - 2026-03-08

### Fixed
- **HA Ingress HTML caching** — Added `Cache-Control: no-cache, no-store, must-revalidate` headers to HTML responses. Prevents HA Ingress proxy and browsers from serving stale HTML pages with old static asset references

## [2.12.3] - 2026-03-07

### Fixed
- **Static asset cache-busting** — CSS/JS files now include `?v=<VERSION>` query string to force browser reload on upgrade. Fixes stale UI rendering through HA Ingress proxy

### Changed
- `VERSION` is now injected into all templates via Flask context processor (previously only passed to `index.html`)

## [2.12.2] - 2026-03-07

### Changed
- **Lazy player registration** — sendspin daemon now starts only after Bluetooth actually connects, eliminating phantom players in Music Assistant and unnecessary double-restart at container startup
- Devices without Bluetooth (no MAC configured) still start immediately on default audio

## [2.12.1] - 2026-03-07

### Removed
- **MPRIS module** (`mpris.py`): deleted `MprisIdentityService`, `pause_all_via_mpris()`, `play_via_mpris()` — all were dead code with zero call sites. MA discovers players via sendspin WebSocket, not D-Bus MPRIS
- D-Bus session bus startup from `entrypoint.sh` (was only needed for MPRIS)
- `aiosendspin-mpris` dependency from LXC install scripts

### Changed
- Documentation fully synchronized with v2.12.0 codebase: rewrote `.github/copilot-instructions.md`, updated `CLAUDE.md`, `CONTRIBUTING.md`, README features (categorized into 6 groups), docs-site API/architecture/configuration/contributing pages
- Auto-reconnect description clarified: instant disconnect detection via D-Bus, 10s polling only as fallback

## [2.12.0] - 2026-03-07

### Added
- **Zombie playback watchdog**: auto-restarts subprocess after 15s of `playing=True` with no audio data (`streaming=False`), up to 3 retries
- **BT churn isolation** (opt-in): auto-disables BT management for devices that reconnect too often within a sliding window; configurable via `BT_CHURN_THRESHOLD` (0=disabled, default) and `BT_CHURN_WINDOW` (default 300s)
- **Stale equalizer indicator**: frozen red equalizer bars when MA reports playing but no audio is streaming; playback text shows "▶ No Audio"

### Fixed
- **Playback state cleanup**: clear playing/streaming status when subprocess is stopped, preventing stale indicators after manual stop

## [2.11.0] - 2026-03-07

### Added
- **OpenWrt LXC deployment**: new `lxc/install-openwrt.sh` installer for OpenWrt-based routers (Turris Omnia, etc.) with procd service management
- **Documentation**: English and Russian infographics added to README and docs-site

### Fixed
- **SSE (HA ingress)**: send current status immediately on SSE connect and reduce heartbeat from 30s to 15s — fixes delayed updates when opening the web UI through Home Assistant menu

## [2.10.16] - 2026-03-06

### Fixed
- **LXC installer**: download all app modules (config, state, routes, services, templates, static) instead of only 2 files
- **LXC PulseAudio**: replace deprecated `enable-lfe-remixing` with `remixing-produce-lfe`/`remixing-consume-lfe` for PA 17+ (Ubuntu 24.04)
- **LXC PulseAudio**: remove `User=pulse`/`Group=pulse` from systemd unit — PA `--system` mode requires root
- **LXC PulseAudio**: add tmpfiles.d entry for `/var/run/pulse` persistence across reboots
- **Config save**: empty string values for numeric fields (`SENDSPIN_PORT`, `PULSE_LATENCY_MSEC`, `BT_CHECK_INTERVAL`, `BT_MAX_RECONNECT_FAILS`) no longer crash with `ValueError`
- **Config save**: `VOLUME_VIA_MA` added to POST whitelist — setting was silently dropped on save

## [2.10.15] - 2026-03-06

### Fixed
- **Group volume with multiple sync groups**: `group_volume` is now sent once per unique MA sync group among the selected targets, instead of only the first. Devices not in any sync group still get local pactl fallback.

## [2.10.14] - 2026-03-06

### Fixed
- **Group volume ignoring devices not in MA sync group**: devices like OpenMove that are selected in the UI but not part of a Music Assistant sync group now receive volume changes via local pactl fallback when group volume is set through the MA path.

## [2.10.13] - 2026-03-06

### Fixed
- **Volume/mute status not updating on MA path**: bridge_daemon now emits status to parent process after receiving VolumeChanged/Mute echo from MA via sendspin protocol. Previously only pactl was updated but the parent process status remained stale.

## [2.10.12] - 2026-03-06

### Changed
- **Single-writer volume architecture**: bridge_daemon (subprocess) is now the sole writer to PulseAudio sink volume. API no longer optimistically updates local status on MA path — waits for the actual echo from MA via sendspin protocol. Eliminates all feedback loops, bouncing, and group volume desync.
- **Removed `_handle_player_updated` volume sync** from MA monitor — was a redundant third path causing stale volume overwrites.

### Added
- **`VOLUME_VIA_MA` config option** (default: `true`): toggle to disable MA API volume proxy entirely, forcing all volume/mute changes through direct pactl. Available in web UI Settings and HA addon config.

### Fixed
- **Group volume bounce**: setting group volume to 40 no longer jumps to 47 then 55 — MA's proportional recalculation now flows cleanly through a single path.
- **Volume jump on track change**: eliminated competing volume writers that caused speakers to briefly change volume when a new track starts.

## [2.10.11] - 2026-03-06

### Fixed
- **Volume bounce/jump on track change and individual adjustment**: eliminated triple feedback loop where API, sendspin protocol echo, and MA monitor event all set PA sink volume simultaneously. Now bridge_daemon is the single writer to pactl.

## [2.10.10] - 2026-03-06

### Fixed
- **MA volume/mute path missing local update**: hybrid volume/mute via MA API now updates local status, syncs subprocess, and persists to config — UI no longer shows stale values after MA-proxied changes.
- **Progress timer runaway**: elapsed time kept incrementing after playback stopped (e.g. 95:53 / 2:57) because the MA progress snapshot lacked a `state === 'playing'` guard. Added state check and capped elapsed at duration.

## [2.10.9] - 2026-03-06

### Added
- **Hybrid volume control**: volume and mute commands are now routed through the MA WebSocket API when connected, keeping MA's UI in sync. Falls back to direct pactl when MA is unavailable.
- **Delta-based group volume**: group volume slider uses MA's `players/cmd/group_volume` (preserves relative speaker proportions) instead of flat value.
- **`force_local` API parameter**: bypass MA proxy and use direct pactl for volume/mute when needed.
- 6 new unit tests for hybrid volume routing logic (15 total).

### Fixed
- **Volume subprocess desync**: web UI volume changes now sync into the daemon subprocess, preventing reverts on next status emit.
- **Group slider lock**: `_userTouched` flag on group volume slider now resets after 3 seconds, restoring auto-sync with average device volume.
- **MA metadata stale on restart**: added 3-second delay between disconnect and reconnect to allow MA to process `ClientRemovedEvent` before re-registration (ref: music-assistant/support#5049).

### Improved
- Adaptive ThreadPool for volume: ≤3 devices use simple loop, 4+ use ThreadPoolExecutor.
- Unified debounce to 300ms for both individual and group volume sliders.

## [2.10.8] - 2026-03-06

### Improved
- **Observability**: all 27 silent `except: pass` blocks now log exception details at DEBUG level — issues are visible with `LOG_LEVEL=DEBUG` without changing runtime behavior.
- **Thread safety**: `run_coroutine_threadsafe` calls now have a 5-second timeout to prevent deadlocks; all fire-and-forget asyncio tasks have `done_callback` for exception logging.
- **Test infrastructure**: added pytest with 9 unit tests covering config loading, volume persistence, MAC-to-player-ID mapping, and password hashing.

## [2.10.7] - 2026-03-06

### Fixed
- **MA→BT volume sync broken**: `_sync_bt_volume()` accessed non-existent attribute `_bt_sink_name` on `BluetoothManager` instead of `bluetooth_sink_name` on the client — volume sync from Music Assistant to Bluetooth speakers was completely non-functional.
- **Volume restore at 0% ignored**: falsy check `if not restored_volume` treated volume=0 as "no saved volume"; now uses `if restored_volume is None`.
- **`/api/pause` endpoint returning 404**: `pause_player()` function was defined but missing the `@api_bp.route` decorator, causing per-device pause to fail for solo players and filtered Pause All.
- **Thread safety**: `set_bt_management_enabled()` now stops the daemon subprocess via `asyncio.run_coroutine_threadsafe()` instead of calling `kill()` directly from a Flask WSGI thread.
- **Config read consistency**: GET `/api/config` now acquires `config_lock` to prevent torn reads during concurrent writes.

### Removed
- Dead code: `_bridge_daemon` attribute (always None), `volume_restore_done` (never read), backward-compat aliases (`_save_device_volume`, `_config_lock`, `_clients_lock`).
- Legacy shell-based volume restore in `entrypoint.sh` (Python handles this).
- Dead anti-feedback attributes `_volume_sync_pending` and `_last_bt_volume` in MA volume sync (never set anywhere).

### Changed
- Normalized `CONFIG_FILE` imports across all modules (removed inconsistent aliases `_CONFIG_PATH`, `_CONFIG_FILE`).
- Misleading log "Failed to connect after 5 attempts" → "Failed to connect (not connected after 5 status checks)".
- `ThreadPoolExecutor` import moved from module level to `main()` where it's used.
- Added design note to `services/pulse.py` explaining per-call PA connection trade-off.

## [2.10.6] - 2026-03-06

### Fixed
- Device sorting: group members now always appear adjacent. Sort order: group score → group_id → individual score (previously score-first could split same-group devices apart).

## [2.10.5] - 2026-03-05

### Fixed
- Code style: simplified equality check in `_find_solo_player_queues` per ruff SIM109.

## [2.10.4] - 2026-03-05

### Fixed
- **MA integration for WH-1000XM4 (solo player)**: `_find_solo_player_queues` now matches MA's internal queue ID format `"up<uuid_no_hyphens>"` in addition to the raw UUID. MA uses this format for individual (non-syncgroup) player queues, so solo players now correctly receive now-playing, track, and transport control data from MA API.
- Fixed `websockets.connect` call in `/api/debug/ma` to use `additional_headers` instead of deprecated `extra_headers`.

## [2.10.3] - 2026-03-05

### Added
- `/api/debug/ma` endpoint: dumps MA now-playing cache keys, discovered groups, per-client player IDs, and live queue IDs fetched from MA WebSocket. Useful for diagnosing MA integration issues.

## [2.10.2] - 2026-03-05

### Fixed
- **MA integration for WH-1000XM4**: name matching in `discover_ma_groups` now normalizes both names to alphanumeric-only before substring comparison. `"wh1000"` now correctly matches `"WH-1000XM4"` (previously the hyphen broke the match).

## [2.10.1] - 2026-03-05

### Added
- **MA API badge**: small "api" badge appears next to the MA connection indicator when MA API integration is active and delivering track data for a device.

### Fixed
- **Mute state reset on muteall/unmute**: daemon subprocess now receives `set_mute` command after each mute/unmute operation so its internal state stays in sync; prevents subsequent status emits from reverting muted state to `false`.
- **MA data for WH-1000XM4 and similar devices**: MA now-playing lookup now uses the Sendspin-reported `group_id` (which is the MA syncgroup ID) as a fallback when name-matching in `discover_ma_groups` didn't produce a match. The MA monitor also now picks up syncgroup queues reported live by bridge devices, not only those discovered at startup.
- **Group badge hover-only for solo players**: fixed condition — now based on whether any other device shares the same `group_id`, rather than the absence of `group_id` (every Sendspin player has a `group_id`).
- **Transport button sizes**: all 5 buttons (◀◀ ▮▮ ▶▶ ⇄ ↻) now render at uniform size via `inline-flex` with fixed `min-width`/`height`.


### Added
- **MA data for solo (ungrouped) players**: devices not in any Sendspin syncgroup now receive track info, progress bar, and transport controls (prev/next/shuffle/repeat) from their own MA queue. Previously only grouped devices had access to MA metadata.

### Changed
- **Shuffle ⇄ / Repeat ↻ in transport row**: buttons moved from secondary hover row into the main control row alongside ◀◀ ▮▮ ▶▶, hover-only, matching the same button style. Replaced emoji with Unicode text symbols.
- **Group badge hover-only for solo players**: devices without a syncgroup show the group/player label only on card hover.
- **Delay badge hover-only**: static delay indicator in the Sync column is now hidden by default and appears only on card hover.

### Fixed
- **Mute state reset on BT reconnect**: daemon subprocess now receives the current muted state on start instead of always emitting `muted: false`, preventing the mute icon from resetting after a Bluetooth reconnect or re-anchor.

## [2.9.9] - 2026-03-05

### Changed
- **Multi-syncgroup MA now-playing**: MA track/progress/controls are now scoped per syncgroup — each device only shows data from its own MA group; devices not in any group show no MA data.
- **Shuffle/repeat in transport row**: ⇄ and ↻ buttons moved into the main controls row alongside ◀◀ ▮▮ ▶▶, hover-only, same visual style.
- **Volume column wider**: volume column widened (1.25fr) at the expense of sync column (0.5fr) so long sink names don't wrap.

### Fixed
- **Per-device MA scoping**: disconnected/Released devices no longer show progress bar or track info from another device's MA group.

## [2.9.8] - 2026-03-05

### Changed
- **Connection column layout**: BT MAC and MA host:port now appear below their respective rows on hover instead of inline to the right; connection column narrowed (0.75fr), playback column widened (1.5fr).
- **Transport controls**: ◀◀ ▮▮ ▶▶ stay in the status row — now fit comfortably in the wider playback column.

### Fixed
- **Album art tooltip**: removed inline `style="display:none"` blocking CSS hover rule; `overflow:visible` on playback column prevents clipping of the absolute-positioned popup.
- **Released devices**: prev/next/shuffle/repeat MA controls no longer shown on Released (no-sink) devices — visibility now gated on `has_sink` per device.

## [2.9.7] - 2026-03-06

### Added
- **Album art tooltip**: hovering over the track name row shows a 120×120 album cover popup (from MA now-playing `image_url`).
- **MA group name in UI**: device cards and group filter dropdown now show the human-readable MA syncgroup name (e.g. "Sendspin BT") instead of the raw Sendspin session UUID tail.

### Changed
- **Unified progress bar**: single bar per card — MA data takes priority when connected, Sendspin native data used as fallback. Separate MA progress bar removed.
- **Transport controls redesign**: ◀◀ ▮▮/▶ ▶▶ now appear in a single row with consistent style; prev/next hidden when MA not connected. Shuffle/repeat moved to hover-only secondary row (appear on card hover).

### Fixed
- `HISTORY.md` added — non-technical project evolution overview.



### Fixed
- **Web UI broken** (`app.js`): duplicate closing brace in `renderDiagnostics()` caused a JavaScript syntax error that prevented the entire UI from loading.
- **MA monitor disabled**: `websockets` package was missing from `requirements.txt`, causing the MA WebSocket monitor to be permanently disabled on startup (`websockets not installed — MA monitor disabled`).



### Added
- **MA Monitor** (`services/ma_monitor.py`): persistent WebSocket connection to Music Assistant that subscribes to `player_queue_updated` / `player_updated` events in real time. Falls back to polling every 15 s if event subscription is unavailable. Auto-reconnects with exponential backoff.
- **MA now-playing API**: `GET /api/ma/nowplaying` — returns current track, artist, album, image URL, elapsed time, shuffle/repeat state and queue position.
- **MA queue commands API**: `POST /api/ma/queue/cmd` — accepts `next`, `previous`, `shuffle`, `repeat`, `seek` actions forwarded to the active MA syncgroup.
- **SSE now-playing**: each SSE status event now includes a `nowplaying` field with the full MA metadata (only when MA is connected).
- **UI playback controls**: when MA is connected, the Playback column shows ⏮ ⏭ 🔀 🔁 control buttons, MA track/artist metadata (overriding Sendspin MPRIS data), and a live progress bar.
- **Automatic player metadata refresh**: on each MA monitor connect, fetches all MA players, compares `device_info.product_name` / `manufacturer` against current version and hostname; if stale and not playing, triggers a reconnect so MA receives updated device info.
- **Diagnostics — MA status**: `GET /api/diagnostics` now includes an `ma_integration` field (`configured`, `connected`, `url`, syncgroups list). The Diagnostics page shows an MA API row with green/red indicator and each syncgroup as a sub-row.
- `state.py`: `is_ma_connected()`, `set_ma_connected()`, `get_ma_now_playing()`, `set_ma_now_playing()`.
- `sendspin_client.py`: `send_reconnect()` public method to trigger subprocess reconnect.
- `services/daemon_process.py`: new `reconnect` stdin command handler.

### Fixed
- **Critical**: `MA_API_URL` and `MA_API_TOKEN` were not in the `allowed_keys` whitelist in `load_config()`, so they were silently filtered out and never passed to the MA API discovery code. MA group discovery was always skipped even when credentials were correctly saved in `config.json`.
- **`POST /api/pause_all` (play/unpause)**: grouped players now resume via `ma_group_play()` (MA REST API) instead of individual Sendspin session-group commands. Using session-group `play` commands caused MA to break group sync. Solo players continue to use direct subprocess commands.

## [2.9.3] - 2026-03-05

### Fixed
- HA addon mode: `MA_API_URL` and `MA_API_TOKEN` set via the web UI are now preserved across addon restarts. Previously `translate_ha_config.py` overwrote them with empty values from `options.json` on every restart.

## [2.9.2] - 2026-03-05

### Fixed
- MA API URL: auto-prepend `http://` scheme if user enters `localhost:8095` without scheme — `music-assistant-client` requires a full URL.
- Saving config via web UI now normalizes `MA_API_URL` (adds `http://` if missing) before writing `config.json`.
- **`POST /api/ma/rediscover`** — new endpoint to re-run MA syncgroup discovery without restarting the addon. Useful after changing MA API credentials via the web UI.


### Fixed
- MA API URL auto-detection in HA addon mode now uses `localhost:8095` instead of `homeassistant.local:8095` (both addons share host networking, mDNS may not resolve inside the container).
- `SUPERVISOR_TOKEN` (HA auth token) is no longer incorrectly used as MA API token — a clear warning is now logged instructing the user to create a long-lived token in MA → Settings → API Tokens.
- `MA_API_TOKEN` form field: empty submit no longer overwrites an existing saved token.

### Added
- `MA_API_URL` and `MA_API_TOKEN` fields are now visible in the web UI Configuration → Advanced settings section, so users can configure MA API integration without editing `config.json` manually.

## [2.9.0] - 2026-03-05

### Added
- **Music Assistant API integration** (`MA_API_URL` + `MA_API_TOKEN` config options): when configured, the bridge connects to the MA WebSocket API at startup to discover persistent MA syncgroup players.
- **`GET /api/ma/groups`** — returns all MA syncgroup players with full member info (id, name, playback state, volume, availability) from the MA API.
- **Correct group resume**: `POST /api/group/pause` with `action=play` now sends play to the **persistent MA syncgroup player** (`syncgroup_*`) via MA API instead of the transient Sendspin session group. This correctly restores all group members in sync, matching the behaviour of the MA UI resume button. Falls back to Sendspin session group command if MA API is not configured.
- `music-assistant-client` dependency added.

### Changed
- MA group names in bridge UI now reflect the persistent MA group name (e.g. "Sendspin BT") when MA API is configured.

## [2.8.2] - 2026-03-05

### Fixed
- **Per-device pause incorrectly fell back to `/api/pause` for grouped players**: `onDevicePause()` checked local `groupSize > 1` to decide between `/api/group/pause` and `/api/pause`. After MA restructures groups (e.g. post-pause), `groupSize` could drop to 1 causing the wrong endpoint. Now checks `group_id != null` directly — if a player has a `group_id` it is always in an MA group and `/api/group/pause` is always used.

## [2.8.1] - 2026-03-05

### Fixed
- **`/api/pause` endpoint returning 404**: `pause_player()` function was defined but missing the `@api_bp.route` decorator, causing per-device pause to fail for solo players and filtered Pause All.

## [2.8.0] - 2026-03-05

### Added
- **`GET /api/groups`** — new endpoint returning a consolidated list of MA player groups. Players sharing the same `group_id` are merged into one entry with `members`, `avg_volume`, and `playing` state. Solo players appear as single-member entries with `group_id: null`.
- **`POST /api/group/pause`** — pause or resume a specific MA sync group by `group_id`. Sends the command to one member only; MA propagates it to the whole group (sending to each member individually would break sync).
- **`group_id` filter for `POST /api/volume`** — set volume for all members of a specific MA sync group in one call (alongside existing `player_name`/`player_names` filters).
- **`groups` field in SSE stream** — every status push now includes an aggregated group summary alongside per-device status, so clients can render group structure without computing it from individual devices.

### Fixed
- **Group filter dropdown broken**: `onGroupFilterChange()` referenced `inGroup` before it was declared — selecting a group in the filter had no effect.
- **Per-device pause button sent to wrong group**: `onDevicePause()` called `/api/pause_all` for grouped devices (affected all groups), now correctly calls `/api/group/pause` with the specific `group_id`.

### Removed
- Unused `play_via_mpris` import in `routes/api.py` and its `__all__` export in `mpris.py` — play/resume now goes through `send_group_command` (same path as pause).

## [2.7.17] - 2026-03-05

### Fixed
- **Group resume via MPRIS**: pressing ▶ on a grouped player now sends MPRIS Play (MA is the initiator) instead of a direct IPC command — this preserves group sync after pause. Previously MA would create a separate session for the individual player instead of restoring the group.

## [2.7.16] - 2026-03-05

### Fixed
- **Individual pause button in sync groups**: clicking ▮▮/▶ on a grouped device now pauses/unpauses the whole group via `/api/pause_all` — previously PLAY from a single group member caused MA to break the group into separate sessions

## [2.7.15] - 2026-03-05

### Fixed
- **Intermittent volume lag**: `/api/volume` now sets sink volume for all target devices concurrently via `ThreadPoolExecutor` instead of serially — with 3 devices the worst case dropped from ~15 s to ~5 s

### Improved
- Default `WEB_THREADS` bumped from 4 → 8 to reduce Waitress thread starvation when multiple browser tabs hold SSE connections

## [2.7.14] - 2026-03-05

### Fixed
- **KA s column header misaligned** — header grid had 8 columns vs 9 in device rows; "KA s" appeared over the delete button column
- **"Show all" label overflow** in paired devices section — title span now shrinks with ellipsis, label stays visible
- **Paired devices sort order** — bridge-configured devices (active and inactive) listed first, then others alphabetically

### Improved
- **PulseAudio performance**: restored thread-local event loop reuse in sync PA wrappers (reverted in 2.7.12 was misdiagnosis; actual root cause was the deadlock fixed in 2.7.13)

## [2.7.13] - 2026-03-05

### Fixed
- **Deadlock** in `load_adapter_name_cache()` — `get_adapter_name()` acquired `_adapter_cache_lock` then called `load_adapter_name_cache()` which tried the same non-reentrant lock; every `/api/status` request deadlocked, starving Waitress threads (root cause of web UI hanging since v2.7.10)

## [2.7.12] - 2026-03-05

### Fixed
- **Web UI unresponsive**: reverted thread-local event loop in PulseAudio sync wrappers — `pulsectl_asyncio` leaves resources on the loop causing `run_until_complete()` to block, starving all Waitress worker threads

## [2.7.11] - 2026-03-05

### Improved
- **PulseAudio performance**: reuse thread-local event loop in sync PA wrappers instead of creating/destroying per call
- **BT thread isolation**: dedicated `ThreadPoolExecutor(2)` for long-running Bluetooth operations (pair, connect, configure) — prevents default pool starvation on low-core systems
- **Shallow copy**: `DeviceStatus.copy()` uses dict comprehension over `fields()` instead of deep `asdict()` (all fields are immutable)
- **Scan guard**: concurrent BT scans rejected with HTTP 409 — prevents overlapping `bluetoothctl scan on` interference

### Refactored
- **D-Bus monitor**: extracted inner monitoring loop into `_inner_dbus_monitor()` method, replaced `restart_outer` flag with clean `return` flow

## [2.7.10] - 2026-03-05

### Fixed
- **Socket leak**: fixed file descriptor leak in `get_ip_address()` — socket now uses context manager
- **Thread safety**: `options.json` writes in `persist_device_enabled()` now held under `config_lock`; added missing locks to `load_config()` and `load_adapter_name_cache()`; protected all unprotected config reads with `_config_lock`
- **Config validation**: `api_config` POST now rejects non-string values for `SENDSPIN_SERVER`, `BRIDGE_NAME`, `TZ`, and `LOG_LEVEL`
- **Keepalive buffer**: clarified silence buffer calculation for readability (result unchanged: 88200 bytes = 500 ms)
- **Adapter resolution**: use sysfs for `hciN`↔MAC resolution, fall back to `bluetoothctl` ordering
- **Volume control**: `set_volume` default now targets all clients, not just the first
- **Daemon logging**: removed debug comment and raw payload repr from group update log
- **Status serialization**: moved `_last_status_json` before `_emit_status`; replaced O(N) serialize loop

### Improved
- **Runtime detection**: replaced mutable global cache with `functools.lru_cache` for thread-safe caching
- **Pulse helpers**: deduplicated `get_sink_input_ids()` by delegating to the existing async implementation
- **Code cleanup**: removed redundant `import re` inside `pair_device()` (already imported at module level)

### Refactored
- **Architecture**: decoupled `BluetoothManager` from `SendspinClient` via `on_sink_found` callback
- **Adapter cache**: replaced `_adapter_cache_loaded` bool with `threading.Event`
- **Logging**: replaced f-string log calls with lazy `%s` format throughout
- **Naming**: renamed `_config_lock`/`_clients_lock` to public names
- **Modules**: added `__all__` to public modules for explicit API surface
- **Imports**: moved `import time` and `import asyncio` from function bodies to module top
- **BT availability**: cached `check_bluetooth_available()`; log unknown `DeviceStatus` keys

## [2.7.9] - 2026-03-05

### Fixed
- **HA auth**: removed `mfa_module_id` from MFA step payload — HA schema only accepts `code`, causing HTTP 400 "User input malformed"

## [2.7.8] - 2026-03-05

### Fixed
- **HA auth**: `client_id` now included in every login_flow step submission — without it HA Core returned HTTP 400 and authentication always failed

## [2.7.7] - 2026-03-05

### Fixed
- **HA auth**: improved error logging in HA login_flow steps to diagnose authentication failures

## [2.7.6] - 2026-03-05

### Added
- **HA addon — 2FA authentication**: login now uses the HA Core `/auth/login_flow` API instead of the Supervisor shortcut, enabling full two-factor authentication (TOTP). A second form step appears when 2FA is configured. Falls back to Supervisor auth only when HA Core is unreachable (network failure), never on HTTP errors, preventing MFA bypass.

### Improved
- **Configuration — Keepalive**: replaced the checkbox + hidden interval sub-row with an inline number field in the main device row. Set to `0` to disable (default); minimum non-zero value is 30 s; maximum 3600 s.

## [2.7.5] - 2026-03-05

### Fixed
- **Web UI**: fixed JS syntax error (`missing ) after argument list`) in `_updateGroupFilter()` that prevented device cards from rendering

## [2.7.4] - 2026-03-05

### Added
- **Log level control**: debug logging can now be enabled via HA addon option `log_level` (info/debug) or toggled at runtime from the **Advanced settings** panel in the web UI without a container restart. The selected level is applied immediately to the main process and all running device subprocesses, and is persisted to `config.json`.

## [2.7.3] - 2026-03-05

### Fixed
- **Web UI — Group badge**: group filter and badge now show the last segment of the UUID when `group_name` is not provided by MA (e.g. `332984a9c660`); full UUID available in tooltip on hover

### Improved
- **Diagnostics — Group name**: added detailed logging of raw `GroupUpdateServerPayload` to diagnose whether MA sends a human-readable group name — will drive future implementation of MA REST API group name resolution

## [2.7.2] - 2026-03-05

### Improved
- **Web UI — Header**: Docs and GitHub links moved out of `<h1>` into a proper nav row below the title; keyboard shortcuts hint relocated to the header right column (always visible)
- **Web UI — Device cards**: "Released" badge displayed in device card header when BT management is disabled, so state is immediately obvious without reading the BT status row
- **Web UI — Group badge**: raw hex UUID hashes (e.g. `332984a9c660`) no longer shown as group name — badge hidden when MA hasn't assigned a human-readable name
- **Web UI — Group volume**: slider initialises from the average volume of active/connected devices instead of a fixed 50%
- **Web UI — Sync column**: "Re-anchors" count now has a tooltip explaining the term; colour coding for high counts (>10 yellow, >100 red)
- **Web UI — Diagnostics**: BT audio sink names rendered as individual `<code>` blocks with word-break, replacing the unreadable comma-joined string
- **Web UI — Mute button**: state tracked via CSS class `.card-icon-btn.muted` instead of inline `style.background`, fixing the broken appearance in dark theme
- **Web UI — Auto-refresh button**: active state uses CSS class `.auto-on` instead of hardcoded `#10b981` colour
- **Web UI — Config dirty state**: unsaved changes show a dot indicator on the Configuration summary and trigger a browser `beforeunload` warning
- **Web UI — Advanced settings**: button label shows count of non-default fields (e.g. "Advanced settings (2)") after config loads and on any change
- **Web UI — BT scan**: animated CSS spinner shown while scan is in progress instead of static "Scanning…" text
- **Web UI — Paired devices**: RSSI-only device names (e.g. `RSSI: 0xff…`) replaced with "Unknown device" in the add-device list
- **Web UI — Re-pair confirm**: confirmation dialog before executing Re-pair to prevent accidental 25-second interruption
- **Web UI — Tooltips**: descriptive `title` attributes on Release and Reclaim buttons explaining what each action does
- **Web UI — Error toasts**: display for 6 s instead of 3 s to give time to read error messages
- **Web UI — Progress time**: font-size increased from 10 px to 12 px for readability
- **Web UI — BT config table**: horizontal scroll shadow on `.bt-table-wrap` indicates overflow content on narrow viewports
- **Web UI — Keepalive layout**: keepalive controls extracted from the 8-column grid into a sub-row, fixing the "orphaned 30 ×" row artifact visible in previous releases
- **Web UI — Mobile actions**: action buttons on device cards now wrap in a `flex-row` layout instead of stacking vertically, fitting more actions per row on small screens

## [2.7.1] - 2026-03-05

### Fixed
- **Release/Reclaim button**: button text and class now update immediately on API success without waiting for SSE — previously showed stale label until the next server-sent event
- **Released device BT status**: devices with `bt_management_enabled=false` now correctly display "Released" in the web UI instead of "BT Reconnecting…"
- **Exponential backoff for BT reconnect**: consecutive failed reconnect attempts now use increasing delays (10 s × 2^(attempt-3), capped at 300 s), reducing radio interference with other A2DP devices on the same adapter during prolonged disconnection

## [2.7.0] - 2026-03-05

### Added
- **Keepalive silence stream**: per-device opt-in feature that periodically sends a short PCM silence burst to the Bluetooth sink to prevent speakers from auto-disconnecting during silence. Configurable interval (10–300 s, default 30 s). Available in web UI config and HA addon.
- **`WEB_THREADS` env var**: configurable Waitress HTTP thread count (default 4, recommend 16 for deployments with 20+ devices)

### Improved
- **Graceful shutdown**: players are now paused via subprocess stdin IPC (`{"cmd":"pause"}`) in parallel before stopping, replacing the fragile MPRIS D-Bus approach. Works reliably even when `dbus-python` is not available
- **Group-aware BT disconnect pause**: when a BT disconnect is detected, solo players receive a pause signal before the daemon stops; grouped players skip it so other group members continue uninterrupted
- **Scalability — SSE batching**: `notify_status_changed()` now batches notifications within a 100 ms window, preventing SSE storms when many devices update status simultaneously (reduces events ~10× under mass-reconnect)
- **Scalability — ThreadPoolExecutor**: explicit pool sized to `min(64, N_devices×2+4)` workers, preventing BT reconnect queue starvation at 100+ devices
- **Scalability — D-Bus bus reuse**: `MessageBus` connection is reused across BT reconnect iterations per device (was re-created each loop); reconnects only when the bus is unresponsive
- **Scalability — keepalive jitter**: random startup delay (0..interval) staggers initial silence bursts across devices
- **Scalability — status monitor**: `_status_monitor_loop` sleep increased from 2 s to 5 s, reducing asyncio wakeups from 50/s to 20/s at 100 devices

### Fixed
- **Race condition — group_id read**: `group_id` status field is now read under `_status_lock` before the BT-disconnect pause decision, preventing stale reads from concurrent daemon reader threads



### Improved
- **Web UI — Player name color**: device names now use `--primary-text-color` (black/white depending on theme) instead of `--primary-color` (blue), improving readability in both light and dark themes
- **Docs**: comprehensive web-ui.md rewrite documenting all v2.6.x features with new screenshots; usage examples and HA automation scenarios added to the home page

## [2.6.9] - 2026-03-04

### Fixed
- **Concurrent reconnect race**: `BluetoothManager.connect_device()` now uses a `threading.Lock` — a second concurrent call waits for the first and returns its result, eliminating duplicate `configure_bluetooth_audio()` runs and double subprocess spawns on reconnect
- **Duplicate `start_sendspin()` guard**: `asyncio.Lock` on `SendspinClient.start_sendspin()` drops concurrent calls that arrive while a daemon is already starting

### Improved
- **Web UI — EQ bars**: animated EQ bars moved from the Volume column to beside the player name (like Music Assistant); triggered by `playing` state instead of `audio_streaming`
- **Web UI — Device sort**: within the same activity level (playing / connected / inactive), devices are grouped by MA sync group; ungrouped devices appear last
- **Web UI — Long track/artist names**: slash-separated compilation names (e.g. `"A/B/C"`) truncated to `"A +2"` with full text in tooltip; column ellipsis fixed via `min-width: 0` on flex children

## [2.6.8] - 2026-03-04

### Fixed
- **Pause All — group playback**: `pause_all` now sends one command per MA group instead of one per client, preventing duplicate pause/play signals to grouped players

### Improved
- **Web UI — Mobile**: Hover-only details (BT MAC, server URI, sink name, WS URL) are always visible on touch devices; icon buttons enlarged to ≥36px touch targets; toast notifications span full screen width on narrow viewports

## [2.6.7] - 2026-03-04

### Fixed
- **Pause button**: Replaced broken MPRIS D-Bus approach (daemon runs with `use_mpris=False`, so no interface existed) with stdin IPC — sends `MediaCommand.PAUSE/PLAY` to MA via the aiosendspin websocket client; works for both solo and group playback
- **Track progress bar**: Progress now interpolates client-side every second between server updates (MA sends `Progress` only on state changes, not continuously)

### Improved
- **Web UI — Volume column**: Sink name moved to bottom of column, now revealed on card hover instead of column hover — consistent with other hover-revealed details

## [2.6.6] - 2026-03-04

### Improved
- **Web UI — Track progress bar**: Playback column now shows a thin progress bar with `m:ss / m:ss` time display during playback; hidden when stopped
- **Web UI — MAC on hover**: BT MAC address hidden by default in the Connection column, revealed on card hover
- **Web UI — Server URI on hover**: MA server address hidden by default, revealed on card hover — reduces visual noise
- **Web UI — Re-anchor count coloring**: Sync column re-anchor count turns amber when >10 and red when >100
- **Backend**: `track_progress_ms` / `track_duration_ms` extracted from `metadata.progress` in `BridgeDaemon` and propagated through the subprocess status pipeline

## [2.6.5] - 2026-03-04

### Improved
- **Web UI — Action buttons on hover**: Reconnect / Re-pair / Release buttons are hidden by default and revealed on card hover (always visible on mobile), reducing visual clutter
- **Web UI — MAC and URL on hover**: device MAC address and WebSocket URL in the identity column are hidden by default and revealed on hover
- **Web UI — Sort disconnected to bottom**: device cards sorted by activity — Playing first, then BT connected, then disconnected
- **Web UI — EQ bars inline**: animated EQ bars moved inside the volume row (before the slider), visible only during active audio streaming
- **Web UI — Audio format in Volume column**: stream format moved from Playback column to Volume column, shown in secondary text color
- **Web UI — Adapter tooltip**: BT adapter shown as `hciN` only; full controller MAC visible in tooltip on hover
- **Web UI — Delay color**: `delay: Xms` badge uses secondary text color; amber only when `|delay| > 1000 ms`
- **Web UI — Secondary text colors**: audio format, ws:// URL, and delay badge use `--secondary-text-color` instead of the accent blue
- **Web UI — Pause button hidden on No Sink**: pause/play button hidden when BT is connected but audio sink is not yet configured
- **Web UI — Inactive devices deselected**: group-control checkbox auto-unchecked for disconnected/inactive devices
- **Configuration form order**: Bridge Name and Timezone promoted to top; MA server/port moved into Advanced settings (alongside latency, BT intervals, codec)

## [2.6.3] - 2026-03-04

### Fixed
- **Sync column stuck in Re-anchoring**: backend periodic watcher auto-clears `reanchoring` 5 s after the last re-anchor log line; `last_reanchor_at` timestamp used as co-trigger alongside `reanchor_count` delta — warning fires reliably even when `reanchor_count` resets to 0 on stream restart and the UI misses the intermediate zero value
- **Re-anchor state leaks across device list changes**: per-index maps `lastReanchorCount`, `reanchorShownAt`, and `lastReanchorAt` are now cleared in the same block as `_groupSelected` whenever device list length or order changes, preventing stale state being applied to the wrong device after a config edit

### Improved
- **Web UI — Connection column**: Bluetooth + Server merged into a single column; frees a column for a wider Playback cell
- **Web UI — Track display**: current track moved into the Playback column; persists on pause/stop (cleared only when server sends empty artist + track)
- **Web UI — Card visual states**: inactive devices (not connected and not playing) dimmed to 60 % opacity with a weaker shadow; actively playing device cards show a 3 px green left-border accent; smooth CSS transitions on state changes
- **Web UI — Relative timestamps**: all "Since:" fields now show `HH:MM` (today) / `yesterday HH:MM` / `Nd ago HH:MM` instead of the full locale datetime string
- **Web UI — Toast notifications**: `showToast()` replaces browser `alert()` for save-config and reconnect results; toasts slide in from the bottom-right and auto-dismiss after 3 s
- **Web UI — Button hierarchy**: Re-pair → warning-color outline (less visually dominant); Release → ghost border (turns red on hover); Reconnect retains filled primary style
- **Web UI — Sink name**: hidden by default below the volume slider; revealed on hover/focus of the Volume column — reduces visual noise
- **Web UI — Global health indicator**: header now shows `● N/M playing · ● N disconnected` summary dots updated on every status push
- **Web UI — Advanced settings toggle**: Latency, BT check interval, Auto-disable on N fails, and SBC codec preference collapsed behind a `▶ Advanced settings` toggle; basic form shows Server, Port, Bridge Name, and Timezone only
- **Web UI — Delay badge**: only rendered when the device is actively playing (was shown in grey for disconnected/idle devices — misleading)
- **Web UI — Keyboard shortcuts**: `R` refresh status · `P` pause all · `S` save config; shortcut hint shown in page footer

## [2.6.2] - 2026-03-04

### Fixed
- **Sync delay badge color**: `delay: Xms` badge is now gray for non-playing/offline devices and orange only when actively playing — previously showed misleading orange on disconnected devices
- **Re-anchor warning duration**: `Math.max(abs(delay), 3000)` instead of `abs(delay) || 3000` — for `-600ms` delay the 600ms window was shorter than a typical SSE update interval, so the post-re-anchor "Re-anchoring" banner was invisible in practice; now minimum 3 s

## [2.6.1] - 2026-03-04

### Fixed
- **Per-device pause button**: Used `os.getpid()` (Flask process) instead of daemon subprocess PID for D-Bus lookup — button now correctly matches the sendspin subprocess and sends pause/play (m2)
- **asyncio deprecation**: Replaced 5 uses of `asyncio.get_event_loop()` with `asyncio.get_running_loop()` — eliminates DeprecationWarning in Python 3.12, prevents RuntimeError in Python 3.14 (C2)
- **Daemon stderr silenced**: Changed `stderr=DEVNULL` to `stderr=PIPE` with `_read_subprocess_stderr()` task — crashes and library errors written to stderr are now logged as warnings (M4)
- **assert in production**: Replaced `assert` statements in `bluetooth_manager.py` and `services/bridge_daemon.py` with explicit `RuntimeError` raises — survives Python `-O` optimization flag (M3)

### Security / Stability
- **Thread-safe device status**: Added `threading.Lock` (`_status_lock`) to `SendspinClient` and `_update_status()` helper — eliminates data races between asyncio loop, D-Bus callback thread, and Flask WSGI threads (C1)
- **Adapter cache TOCTOU**: Added `_adapter_cache_lock` with double-checked locking in `state.py` — prevents concurrent WSGI threads from double-loading the adapter name cache (M5)

### Performance
- **Async BT scan**: `POST /api/bt/scan` now returns `{"job_id": "..."}` immediately; poll `GET /api/bt/scan/result/<id>` for result. Scan runs in a background thread — no longer blocks WSGI workers for 11–17 s (M1)
- **Parallel bluetoothctl info**: Device enrichment during scan uses `ThreadPoolExecutor(max_workers=8)` — 50 devices enriched in parallel instead of sequentially (M1)
- **BT poll logs to DEBUG**: Reduced log verbosity from INFO to DEBUG for polling loop entries — eliminates ~720 log lines/hour/device at steady state (M6)

### Code Quality
- **Duplicate volume persistence**: Removed duplicated `open/json.load/dump/os.replace` in `routes/api.py`; now delegates to `config.save_device_volume()` (m7)
- **Config path consolidation**: Removed `_CONFIG_PATH` string variable; all code uses `CONFIG_FILE: Path` from `config.py` (TD2)
- **Reconnect retry deduplication**: Extracted `_handle_reconnect_failure()` method in `BluetoothManager` — eliminates duplicated auto-disable logic between `_monitor_polling` and `_monitor_dbus` (TD1)
- **HA config translation script**: Moved 85-line Python heredoc from `entrypoint.sh` to `scripts/translate_ha_config.py` — now lintable, typed, and `sendspin_port` saved as `int` instead of `str` (TD3)
- **DeviceStatus dataclass**: `SendspinClient.status` is now a typed `@dataclass` instead of a plain dict — prevents unbounded key growth, enables static type checking (TD4)
- **Late import removed**: `routes/views.py` no longer imports from `web_interface`; reads `current_app.config["AUTH_ENABLED"]` instead (m1)
- **Healthcheck fix**: Dockerfile healthcheck now checks web UI reachability only — no longer marks container unhealthy when BT speaker is disconnected (normal state on startup) (minor)
- **XSS prevention**: System info display uses `textContent` instead of `innerHTML` in app.js (m5)

## [2.6.0] - 2026-03-05

### Performance
- **Real-time status via SSE**: Added `GET /api/status/stream` Server-Sent Events endpoint.
  Browser now receives status pushes instantly on change instead of polling every 2 s (~300 req/min → ~0).
  Falls back to 2 s polling automatically if SSE is not supported.
- **Daemon crash recovery time**: Reduced `_status_monitor_loop` sleep from 10 s → 2 s.
  Crash detection and restart now takes ≤4 s (was up to 20 s).
- **Exponential backoff on daemon restart**: Crash-loop delay grows 1→2→4→8→30 s (max).
  Resets to 1 s after a successful run. Prevents CPU spin on persistent errors.
- **Status emission deduplication**: `daemon_process.py` now skips stdout writes when
  status hasn't changed, reducing IPC noise by ~10× during steady-state playback.
- **Volume config write debounce**: `/api/volume` now applies `pactl` change instantly and
  schedules the `config.json` write 1 s after the last call. Prevents disk I/O storm when
  dragging the volume slider.

### Improved
- **Sink name in UI**: Audio sink name (e.g. `bluez_output.FC_...`) now shown under the
  volume slider. Shows ⚠ warning when BT is connected but no sink is detected.
- **Volume slider pending state**: Slider fades to 55% opacity while a volume request is in
  flight, giving clear visual feedback that the action was received.
- **BT reconnecting animation**: Status indicator pulses orange during reconnection attempts
  instead of showing a static red/inactive dot.
- **Status keys whitelist**: `_read_subprocess_output()` now only merges known status keys
  from the daemon subprocess, preventing unbounded dict growth from future subprocess bugs.



### Security
- **Session cookie hardening**: Set `SESSION_COOKIE_SAMESITE=Lax` and `SESSION_COOKIE_HTTPONLY=True`
  as CSRF defence-in-depth (all POST endpoints already reject form-encoded bodies via `get_json()`).
- **Brute-force protection on /login**: In-memory IP-based lockout after 5 failed attempts within
  60 seconds; locked out for 5 minutes. No external dependencies.
- **Adapter MAC validation**: `/api/config` POST now validates MAC addresses in `BLUETOOTH_ADAPTERS`
  entries (previously only `BLUETOOTH_DEVICES` were validated), preventing injection into
  `bluetoothctl select` command.

### Fixed
- **Thread-safety in client list**: `state.py::set_clients()` now holds a lock during
  `clear()+extend()` and `/api/status` snapshots the list before iteration, eliminating
  potential `IndexError` under concurrent Waitress threads.
- **Volume endpoint input validation**: `int()` conversion on `/api/volume` now wrapped in
  `try/except`, returns HTTP 400 on invalid input instead of unhandled 500.
- **Bluetooth scan DoS**: `/api/bt/scan` now caps discovered devices at 50 before running
  individual `bluetoothctl info` subprocesses (prevents multi-minute hangs in dense BT environments).
- **Event loop resource leak**: `services/pulse.py::_run()` now initialises the event loop
  inside the `try` block, preventing fd/memory leak if loop creation raises.
- **Remove `bash -c` in adapter resolution**: `bluetooth_manager.py::_resolve_adapter_select()`
  now calls `["bluetoothctl", "list"]` directly (no shell invocation).
- **BRIDGE_NAME_SUFFIX implemented**: Config field was stored/synced but never applied.
  Now: when `BRIDGE_NAME_SUFFIX=True` and no explicit `BRIDGE_NAME` is set, the hostname
  is appended to player names as `@ <hostname>`.

### Maintenance
- Store `setInterval` references in `app.js` and clear them on `beforeunload`.

## [2.5.6] - 2026-03-04

### Added
- **Group filter for volume/pause controls**: New dropdown in the Group Controls panel
  to filter operations by MA sync group. Selecting a group auto-checks only devices
  in that group; volume, mute, and pause/unpause apply only to the selection.
  Pause with a filtered selection uses per-player `/api/pause` calls for reliability
  in subprocess mode.
- **Timed re-anchor warning in Sync column**: Re-anchoring alert (with error ms) now
  stays visible for `abs(static_delay_ms)` ms after the event, then reverts to
  "✓ In sync — Re-anchors: N". Fallback is 3 s when no delay is configured.

### Fixed
- **Format column missing from device config table**: `preferred_format` input was added
  in v2.5.5 but the `bt-header` grid had 7 columns instead of 8; added "Format" header
  and updated `grid-template-columns` in both `.bt-header` and `.bt-device-row`.

## [2.5.5] - 2026-03-04

### Added
- **`preferred_format` per-device config**: New field to control the audio format
  advertised to Music Assistant. Default `flac:44100:16:2` matches the native SBC A2DP
  Bluetooth sink rate (44100 Hz / 16-bit), eliminating unnecessary PulseAudio resampling
  from 48000 Hz / 24-bit. Set to `flac:48000:24:2` to restore the previous behavior.
  Configurable via the web UI device form and HA addon config schema.

## [2.5.4] - 2026-03-04

### Fixed
- **Sync status empty in device card (re-anchor count, reanchoring flag)**: The subprocess
  status dict was missing `reanchor_count`, `reanchoring`, and `last_sync_error_ms` fields.
  Added tracking via `_JsonLineHandler` — re-anchor log messages from `sendspin/audio.py`
  (`"Sync error … re-anchoring"`) are intercepted and update the status dict in real time.
  `reanchoring` flag is cleared when the stream restarts successfully. Counter resets on
  new stream (`_handle_format_change`).

## [2.5.3] - 2026-03-03

### Fixed
- **Track title and artist not displayed in device cards**: The bridge was only
  registering the `PLAYER` role with the MA server (not `METADATA` or `CONTROLLER`).
  The server only sends metadata (title/artist) to clients with the `METADATA` role.
  Fixed by always including `METADATA` and `CONTROLLER` roles regardless of MPRIS
  availability — MPRIS is a D-Bus feature irrelevant to metadata role assignment.

## [2.5.2] - 2026-03-03

### Fixed
- **Re-anchoring loop caused by move-sink-input**: `_ensure_sink_routing()` was called
  on every `Stream STARTED` event, including re-anchor events triggered by PA stream
  glitches. Moving a sink-input causes a brief PA interruption → sendspin detects sync
  error → re-anchor → `Stream STARTED` → move again → infinite loop of re-anchors at
  playback start.
  Fixed with `_sink_routed` flag: routing correction runs **once per stream** (reset in
  `_handle_format_change` on new codec/format, set after first move). Re-anchor events
  no longer trigger redundant `move-sink-input` calls.

## [2.5.1] - 2026-03-03

### Fixed
- **PulseAudio module-rescue-streams override**: when a BT sink disappears (speaker
  disconnects), PulseAudio's `module-rescue-streams` moves any active stream on that
  sink to the default sink. If another subprocess's stream is on the default sink at
  that moment, all audio can end up on the same speaker. On reconnect, the stream isn't
  automatically moved back to its correct sink.
  Fix: on every `Stream STARTED` event in `BridgeDaemon`, `_ensure_sink_routing()` runs
  `amove_pid_sink_inputs(os.getpid(), sink_name)` — moves all sink-inputs belonging to
  this subprocess's PID back to the correct BT sink. With one subprocess per speaker
  (v2.5.0), there is exactly one sink-input per process — no race conditions, no
  claimed-ID tracking.
- Added `amove_pid_sink_inputs(pid, sink_name)` to `services/pulse.py`: finds
  sink-inputs by `application.process.id` property via pulsectl (pactl fallback).

## [2.5.0] - 2026-03-03

### Changed
- **Subprocess isolation for sink routing**: each `SendspinClient` now spawns a
  dedicated subprocess (`services/daemon_process.py`) with `PULSE_SINK` set to the
  target Bluetooth sink in the subprocess environment.  Because PortAudio creates one
  PA context per Python process, subprocess isolation is the only reliable way to
  guarantee that every audio stream goes to the correct speaker from the first sample —
  no `move-sink-input`, no polling, no delay.
- Removed all reactive routing code that was introduced in v2.2.x–v2.3.x:
  `_routing_lock`, `_claimed_sink_inputs`, `_route_stream_to_sink()`,
  `_routing_task`, `_pre_start_sink_input_ids`, inline PULSE_SINK attempts (~200 lines total removed).
- New `services/daemon_process.py`: subprocess entry point with JSON IPC over
  stdin/stdout.  Parent sends `{"cmd":"set_volume","value":N}` / `{"cmd":"stop"}`;
  subprocess emits `{"type":"status",...}` and `{"type":"log",...}` lines.
- `SendspinClient` updated: `_daemon_proc` (asyncio.subprocess.Process), `_read_subprocess_output()`,
  `_send_subprocess_command()` replace the former in-process daemon task.
- Volume changes from Music Assistant server (inside subprocess) are now detected in
  `_read_subprocess_output` and persisted via `_save_device_volume`.

## [2.4.0] - 2026-03-03

### Changed
- **Proactive sink routing via `PULSE_SINK`**: replaced reactive `pactl move-sink-input`
  with a proactive approach that sets `PULSE_SINK` immediately before `_handle_format_change`
  opens the PortAudio/PA stream. The stream now connects directly to the target BT sink
  from the very first sample — no polling, no claiming, no delay.
- Removed all reactive routing code: `_routing_lock`, `_claimed_sink_inputs`,
  `_route_stream_to_sink()`, `_routing_task`, `_pre_start_sink_input_ids` (~150 lines removed).
- asyncio's single-threaded execution guarantees that no other daemon can interleave
  between the env-var set and the stream open, making this race-condition-free.

## [2.3.6] - 2026-03-03

### Fixed
- **Stale routing tasks on rapid stop/play**: each `Stream STARTED` event created
  a new `_route_stream_to_sink()` task. With rapid stop/play clicks, tasks piled up
  (~20 for 4 devices) and competed for sink-input IDs that had already been replaced
  by PipeWire, causing `sink-input N not found` failures and retries.
  Fixed by tracking the current routing task per daemon and cancelling it on new
  stream start. The `CancelledError` handler releases any already-claimed ID.

## [2.3.5] - 2026-03-03

### Improved
- **Sink routing latency**: split routing into two phases — claim (under lock)
  and route (outside lock, parallel). Previously the asyncio lock was held during
  `pactl move-sink-input`, causing 4 devices to route sequentially (~1 s each = ~4 s
  total). All daemons now route concurrently.
- **Fast path for repeated play**: if the previous sink-input ID is still live
  (same PortAudio stream across stop/play cycles), it is re-claimed immediately
  with zero sleep. Audio reaches the correct speaker almost instantly on repeat play.
- **Adaptive polling for new streams**: replaced fixed `sleep(0.3)` with a
  50 ms poll loop (max 300 ms) that breaks as soon as the sink-input appears.

## [2.3.4] - 2026-03-03

### Fixed
- **Sink routing on repeated group play (root cause)**: routing was only triggered from
  `_handle_format_change`, which fires only when codec/sample-rate changes. On subsequent
  play cycles with the same format (e.g. FLAC 48kHz), it never fired — all streams piled
  up on the default PipeWire sink. Fixed by triggering re-routing from
  `_on_stream_event("start")`, which fires on every stream activation.
- **Stream stealing between daemons**: when all daemons re-routed simultaneously, `max(unclaimed)`
  could pick up another daemon's sink-input. Fixed by preferring to re-claim the daemon's own
  previous sink-input ID if it is still live.

## [2.3.3] - 2026-03-03

### Fixed
- **Sink routing on repeated group play**: `_routed = True` was never reset between
  playback sessions. When a group stopped and restarted, sounddevice recreated the
  PortAudio stream with a new PulseAudio sink-input ID, but routing was skipped because
  `_routed` was already `True` — all streams fell back to the default PA sink (whichever
  BT device was default at the time, typically the last active one).
  Fixed by resetting `_routed = False` on every format change and releasing the previously
  claimed sink-input ID before re-claiming in `_route_stream_to_sink`.

## [2.3.2] - 2026-03-03

### Fixed
- **D-Bus monitor callback signature**: `dbus-fast` requires exactly 3 positional
  parameters for `on_properties_changed` callback, but the handler had 4 (with default).
  This caused `reply_notify must be a function with 3 positional parameters` error
  every 10 seconds for all devices. Fixed via closure factory pattern.

### Added
- GitHub repository link (🛠 GitHub) in web UI header
- Sidebar navigation on documentation homepage (removed splash template)
- Configuration link in docs hero actions (RU/EN)

## [2.3.1] - 2026-03-03

### Security
- **Open redirect fixed**: login redirect target (`?next=`) now validated to be a local
  path — rejects absolute URLs, `//host` and scheme-relative redirects

### Fixed
- **asyncio.shield() misuse**: `stop_sendspin()` used `shield()` inside `wait_for()`,
  preventing cancellation from propagating — timeout always expired; shield removed
- **`_GLib` never imported**: MPRIS identity registration silently failed because
  `GLib` was never assigned from `gi.repository`; now properly imported in `mpris.py`
- **`_routed_sink_input_id` uninitialized**: attribute was dynamically created on first
  routing success; now initialized to `None` in `BridgeDaemon.__init__`
- **Missing HTTP status codes**: error responses in `/api/status` (503) and
  `/api/logs` (500) now return proper status codes instead of implicit 200
- **Stale comment**: removed outdated "monkey-patch" comment in `bridge_daemon.py`
- **ha-addon version**: reverted `config.yaml` version to let CI auto-sync on tag push

### Removed
- Dead code: `_detect_server_url_from_proc()`, `self.process`, `read_mpris_metadata_for()`
- Redundant `or None` in `state.py` (`.get()` already returns `None`)

### Changed
- 10 regex `re.compile()` calls moved from per-request to module-level constants
  in `routes/api.py` for better performance
- Added Flask `@errorhandler(404)` and `@errorhandler(500)` with JSON responses
  for `/api/` routes and redirect-to-home for page routes
- Added documentation link (📖 Docs) to web UI header
- Added `_GLib is not None` guard before starting GLib main loop thread

## [2.3.0] - 2026-03-03

### Security
- **Auth bypass fixed**: `X-Ingress-Path` header now trusted only from localhost IPs
  (`127.0.0.1`, `::1`, `172.30.32.2`) — prevents LAN clients from spoofing the header
  to bypass authentication on port 8080
- **Wildcard CORS removed**: `CORS(app)` with `Access-Control-Allow-Origin: *` removed
  entirely — UI and API are same-origin, cross-origin access is no longer permitted
- **Timing-safe password comparison**: `check_password()` now uses `hmac.compare_digest()`
  instead of `==` to prevent timing side-channel attacks
- **Config POST validation**: MAC addresses validated with regex, port numbers checked for
  valid range (1024–65535), top-level keys whitelisted — prevents arbitrary JSON injection

### Fixed
- **PID 1 signal handling**: HA addon now sets `init: true` so container signals (SIGTERM)
  are properly forwarded to the Python process instead of falling through to SIGKILL after 10s
- **Sink-input routing retry**: `_route_stream_to_sink()` now retries up to 3 times with
  0.5s/1.0s/1.5s backoff on `amove_sink_input` failure
- **Stale claimed IDs**: `_claimed_sink_inputs` is pruned against live sink-inputs before
  each routing attempt — prevents stale entries from blocking re-routing after daemon crash
- **dbus.mainloop.glib NameError**: MPRIS Identity service registration now correctly imports
  `dbus.mainloop.glib` instead of referencing undefined `dbus` variable
- **BT scan process leak**: `bluetoothctl` Popen in `/api/bt/scan` now wrapped in
  try/except with `proc.kill()` on timeout — prevents orphaned processes
- **Config write .tmp cleanup**: if `json.dump()` fails mid-write, the temporary file is
  removed instead of being left on disk
- **Shell variable quoting**: `$BLUETOOTH_MAC` in `entrypoint.sh` properly quoted to prevent
  word splitting
- **D-Bus warning improved**: entrypoint now logs "MPRIS will not be available" when
  `dbus-daemon` fails to start

### Changed
- **Dead code removed**: unused `update_status()`/`get_status()` methods with `threading.Lock`
  removed from `SendspinClient` — all status mutations use direct dict access (safe under GIL)
- **Silent exceptions logged**: bare `except: pass` blocks in MPRIS D-Bus calls, adapter cache
  loading, and config merge now log at `DEBUG` level for diagnostics
- **Healthcheck port**: Dockerfile `HEALTHCHECK` now uses `WEB_PORT` env var instead of
  hardcoded 8080

## [2.2.3] - 2026-03-03

### Fixed
- **Sink-input dedup**: added `_claimed_sink_inputs` class-level set to `BridgeDaemon` so
  each daemon claims a unique sink-input ID. Previously both daemons could route the same
  sink-input (worked by luck, not design). An `asyncio.Lock` serializes routing to prevent
  two daemons from claiming the same ID simultaneously.

## [2.2.2] - 2026-03-03

### Changed
- **Audio routing overhaul**: replaced null-sink + loopback approach with `pactl move-sink-input`.
  After `sounddevice.RawOutputStream` creates a PA sink-input (triggered by `_handle_format_change`),
  the daemon diffs current vs pre-start sink-input IDs and moves the new one to the correct BT sink.
  This works on PipeWire's PA-compat layer where `pactl load-module` always fails.

### Removed
- Removed `load_null_sink()`, `load_loopback()`, `unload_module()` and their async wrappers
  from `services/pulse.py` (~80 lines) — all incompatible with PipeWire on HAOS

## [2.2.0] - 2026-03-03

### Added
- **Multi-speaker null-sink routing** (attempt): each daemon creates a `module-null-sink` +
  `module-loopback` pair to route audio through a per-device bridge sink to the BT sink.
  This approach failed on HAOS because PipeWire's PA-compat layer does not support
  `pactl load-module`. Superseded by v2.2.2's `move-sink-input` approach.

## [2.1.8] - 2026-03-03

### Fixed
- **Group audio routing (v2)**: replaced fixed 6 s PULSE_SINK hold with event-driven
  `_claim_sink_input()` — polls for new PA sink-input to appear, then explicitly moves
  it to the correct BT sink via `pactl move-sink-input`. Lock is released as soon as
  the stream appears (~4 s average) instead of after a fixed sleep.
  Scales to any number of devices; routing is guaranteed correct regardless of PULSE_SINK
  timing. Added `alist_sink_input_ids()` and `amove_sink_input()` to `services/pulse.py`
  with pulsectl_asyncio native API and `pactl` subprocess fallback.

## [2.1.7] - 2026-03-03

### Fixed
- **Group audio routing**: increased PULSE_SINK hold from 3 s to 6 s — Music Assistant connects
  ~4 s after daemon start, so the PA stream now opens while the correct BT sink is still set

### Changed
- **Diagnostics**: `/api/diagnostics` now includes `sink_inputs` (PA stream properties incl.
  `application.name`) and `portaudio_devices` list for audio routing debugging

## [2.1.5] - 2026-03-03

### Fixed
- **Enabled toggle sync**: bridge UI `enabled` toggle and HA addon config page now stay in sync
  - `persist_device_enabled()` now writes to both `config.json` and `/data/options.json`, so toggling
    in the bridge UI is immediately reflected on the HA config page
  - On startup, each device's actual `enabled` state is synced to `options.json` (fixes devices showing
    as disabled in HA config page when they are enabled in the bridge)
  - `entrypoint.sh` no longer overrides `enabled` from old `config.json` on restart — `options.json`
    is now the authoritative source; devices without explicit `enabled` in `options.json` default to `true`

## [2.1.4] - 2026-03-03

### Added
- **DeviceInfo in MA**: players now register with `product_name = "Sendspin BT Bridge v2.1.4"` and
  `manufacturer = <hostname>` — visible in MA player details; updates automatically on reconnect
- **Server column**: always shows server address (`host:port` or `auto:9000`) regardless of connection state;
  populates real host from WebSocket URL after connect
- **Group badge**: shows last UUID segment of `group_id` (e.g. `🔗 855be80925d3`) when `group_name` is null,
  allowing different groups to be distinguished
- Group badge moved above MAC address in device card

## [2.1.3] - 2026-03-03

### Fixed
- **Group audio routing**: each daemon now routes to its own BT speaker via `PULSE_SINK` env var.
  sounddevice/PortAudio in the container only exposes a single `default` device regardless of
  BT sink names. The real routing mechanism is PortAudio's PulseAudio backend reading
  `PULSE_SINK` at `pa_stream_connect_playback()` time (~1–2 s after daemon start).
  A class-level `asyncio.Lock` serialises daemon startup so only one daemon at a time
  holds its `PULSE_SINK` value, preventing race conditions between concurrent instances.

## [2.1.2] - 2026-03-03

### Fixed
- `Cannot run the event loop while another loop is running` crash on daemon restart after BT connect:
  `resolve_audio_device_for_sink` is now `async` and uses `await aget_sink_description()` directly
  instead of calling the sync wrapper `get_sink_description()` (which creates a new event loop —
  illegal inside an already-running asyncio loop)
- `coroutine 'aget_sink_description' was never awaited` warning eliminated

## [2.1.1] - 2026-03-03

### Fixed
- Corrected `pulsectl-asyncio` version constraint from `>=0.8.0,<1.0.0` to `>=1.0.0,<2.0.0` (versions 0.8.x do not exist; latest stable is 1.2.2)

## [2.1.0] - 2026-03-03

### Added
- `pulsectl-asyncio>=1.0.0,<2.0.0` to `requirements.txt`.
- New module `services/pulse.py` — sync + async wrappers for all PulseAudio operations
  with graceful fallback to `pactl` subprocess if `pulsectl_asyncio`/`libpulse0` unavailable.

### Changed
- **PulseAudio: migrate from subprocess `pactl` to `pulsectl_asyncio` library.**
  All PA operations (sink discovery, volume, mute, diagnostics) now use the native
  `pulsectl_asyncio` API instead of spawning `pactl` subprocesses.
  Benefits: no fork+exec overhead, typed objects, direct `sink.description` access
  (fixes audio device resolution for group playback).

## [2.0.6] - 2026-03-03

### Fixed
- **Group audio still routes to single device** — `resolve_audio_device_for_sink` was
  matching PA sink names (e.g. `bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink`) against
  sounddevice device names, but PortAudio/PulseAudio exposes sinks by their *description*
  (human-readable name like "ENEBY20"), not by their PA identifier.
  Added `_get_sink_description()` which queries `pactl list sinks` for the friendly name,
  then matches it against sounddevice devices. Falls back to MAC segment and prefix
  heuristics as before.

## [2.0.5] - 2026-03-03

### Fixed
- **Group badge never shown** — MA's `group/update` message sends `group_id` but
  leaves `group_name` null (`omit_none = True`). UI now shows "🔗 In group" when
  `group_id` is set but `group_name` is absent.

## [2.0.4] - 2026-03-03

### Fixed
- **Group playback: audio routes to only one device** — daemon was started before
  Bluetooth connected, so it bound to the default audio device (no BT sink known yet).
  After initial BT connect, daemon now restarts with the correct per-device BT sink,
  ensuring each player in a group outputs to its own Bluetooth speaker.
- **`update_status` name collision** — async status-monitor loop had the same name as
  the sync thread-safe helper, silently shadowing it. Renamed to `_status_monitor_loop`.

## [2.0.3] - 2026-03-03

### Fixed
- **Track metadata never populated** — `_on_metadata_update` callback receives
  `ServerStatePayload` (with nested `metadata: SessionUpdateMetadata`), not
  `SessionUpdateMetadata` directly. Fixed to access `payload.metadata.title`/`.artist`
  instead of `payload.title`/`.artist`. Track and artist now display correctly during playback.

## [2.0.2] - 2026-03-02

### Fixed
- `_on_metadata_update` raised `AttributeError` when metadata listener received
  a `ServerStatePayload` (no `title` attribute); guard with `hasattr` check.
- `_monitor_dbus` looped forever on D-Bus policy errors (`add match request failed`)
  in restricted container environments; now raises `RuntimeError` after 3 consecutive
  failures so `monitor_and_reconnect` falls back to bluetoothctl polling.

## [2.0.1] - 2026-03-02

### Fixed
- Restore missing `class BluetoothManager:` declaration lost during D-Bus refactor edit
  (`ImportError: cannot import name 'BluetoothManager'` on container startup).

## [2.0.0] - 2026-03-02

### Changed
- **D-Bus Bluetooth monitor** — replaced `bluetoothctl` polling in `monitor_and_reconnect()`
  with a `dbus-fast` (asyncio-native) `PropertiesChanged` signal subscription. Disconnects
  are detected **instantly** instead of waiting for the next check interval (default 10 s).
  Falls back to `bluetoothctl` polling if `dbus-fast` is unavailable.
- **`is_device_connected()`** — now queries BlueZ `Device1.Connected` property via
  `dbus-python` synchronously (~10× faster than spawning a `bluetoothctl` subprocess).
  Retains bluetoothctl fallback for environments without D-Bus access.
- **`is_device_paired()`** — same D-Bus-first approach as `is_device_connected()`.
- **`disconnect_device()`** — calls `org.bluez.Device1.Disconnect` via D-Bus directly;
  falls back to `bluetoothctl disconnect`.
- **In-process sendspin daemon** — replaced `subprocess + stdout-parsing` architecture
  with direct in-process `BridgeDaemon(SendspinDaemon)` subclass. Status updates
  (play/stop, audio format, volume, group, metadata) are now delivered via typed
  callbacks instead of fragile log-line parsing. Removed ~230 lines of parsing code.
- **Track metadata** now delivered by `add_metadata_listener` callback instead of
  periodic MPRIS polling; eliminates the 10-second metadata lag.
- **BT reconnect** now calls `client.stop_sendspin()` / `client.start_sendspin()` instead
  of `process.terminate()` / `start_sendspin_process()`.

### Added
- `dbus-fast>=2.22.0,<3.0.0` to `requirements.txt`.
- Explicit `import asyncio` to `bluetooth_manager.py`.
- `_dbus_device_path` cached in `BluetoothManager.__init__`.
- Module-level `_dbus_get_device_property()` and `_dbus_call_device_method()` helpers.
- **MA player grouping** — `group_name` and `group_id` tracked in player status and shown
  as a badge in the device card when the player is part of a Music Assistant group.

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

[2.6.3]: https://github.com/trudenboy/sendspin-bt-bridge/compare/v2.6.2...v2.6.3
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
