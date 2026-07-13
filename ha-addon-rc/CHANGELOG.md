# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.72.0-rc.2] - 2026-07-13

### Changed

- **Routine dependency refresh in the frozen lockfile.** `requests` 2.34.1 → 2.34.2, `cffi` 2.0.0 → 2.1.0, `charset-normalizer` 3.4.7 → 3.4.9, `click` 8.3.3 → 8.4.2 (all transitive patch/minor updates), plus `pyobjc-framework-libdispatch` 12.1 → 12.2.1 (macOS-only). The dependency audit stays clean. ([#371](https://github.com/trudenboy/sendspin-bt-bridge/pull/371), [#372](https://github.com/trudenboy/sendspin-bt-bridge/pull/372), [#373](https://github.com/trudenboy/sendspin-bt-bridge/pull/373), [#374](https://github.com/trudenboy/sendspin-bt-bridge/pull/374), [#375](https://github.com/trudenboy/sendspin-bt-bridge/pull/375))
- **The Home Assistant integration re-pairs once after this upgrade.** API access tokens for the Home Assistant custom component now use a faster verification scheme that also removes a per-request processing cost on every authenticated call. Existing tokens are retired as part of the upgrade, so the integration prompts you to re-pair a single time — a click in the add-on's Home Assistant panel, after which everything reconnects normally.

### Fixed

- **A Home Assistant MQTT command no longer briefly freezes the whole bridge.** Pressing a button or moving a slider exposed through the MQTT integration could stall every speaker's audio sync and status updates for several seconds — and in the worst case dead-lock the bridge — while the command was handled. Commands now run off the main processing loop, so playback and status stay responsive.
- **Music Assistant volume ramps no longer trigger a burst of disk writes that can stutter audio.** Rapid volume and delay changes coming from the daemon are now coalesced into a single debounced save that runs off the audio path, instead of rewriting the config file on every step.
- **One speaker's slow Bluetooth stack no longer hitches the others.** Blocking system operations — adapter recovery, battery-level reads, device-name lookups, standby-sink creation, network name resolution and MQTT/config file access — were moved off the main loop, so they can't momentarily stall playback or status updates for unrelated speakers.
- **Background audio-routing corrections and monitor-loop errors are now logged with full detail** instead of failing silently, making intermittent Bluetooth issues easier to diagnose from the logs.
- **A malformed value in an uploaded or hand-edited configuration file no longer stops the bridge from starting.** Non-numeric values in legacy numeric fields fall back to a safe default with a warning instead of crashing the load in a boot loop.
- **The status and diagnostics views now correctly report whether Music Assistant is configured and which update channel is selected**, instead of always showing "not configured" and the stable channel.
- **Changing the Music Assistant server address or token now correctly asks for a restart to take effect**, rather than reporting the change as applied while the bridge kept using the old connection.
- **A speaker process that can't be stopped gracefully is now reliably force-stopped and reaped**, so its audio port is freed for the next start (fixes occasional "address already in use" after a restart).
- **Hardened the status channel between the bridge and each speaker process** so heavy log/status traffic can't interleave and corrupt a message, and a status update mid-change can no longer crash the reporter.
- **Bluetooth reconnect handling no longer accumulates duplicate signal handlers across reconnects** (which caused repeated reconnect and Home Assistant re-registration work), and audio is now configured immediately after a reconnect instead of after a fixed delay.
- **A forced reconnect or reset now waits behind an in-progress scan or pairing** instead of driving the Bluetooth adapter at the same time, and returns a clear "operation already in progress" response.
- **Saved per-speaker volumes are no longer lost when the stored address differs only in letter case** from the configured device.
- **Unrecognised on/off payloads sent to a switch entity are now rejected instead of defaulting to "on".**
- **Corrected Home Assistant entity categories and internal status labels** that an earlier refactor had mangled, so the custom component's configuration entities are categorised correctly again.
- **The Home Assistant integration now recovers on its own after the bridge restarts** — it pulls a fresh snapshot as soon as the event stream reconnects and polls on a slow safety-net interval, instead of showing stale entity states until the next event.
- **Rotating or invalidating the bridge's API token now prompts Home Assistant to re-authenticate** instead of the integration silently freezing until a restart.
- **Adding the integration is more robust to a slow or unreachable bridge** — connection timeouts and non-JSON responses during setup now surface a clean error instead of an unhandled failure.
- **A malformed or partially-written addon configuration can no longer prevent startup**: the option translator preserves prior settings on a best-effort basis and writes the generated configuration atomically, so an interrupted run can't leave a truncated file.
- **A Bluetooth adapter's saved name is no longer lost (or duplicated) when its address is stored in a different letter case** than the detected hardware.

### Security

- **The web-UI password can no longer be bypassed on standalone (non-Home-Assistant) installs.** The automatic sign-in used for Home Assistant's ingress is now honored only when the bridge actually runs as a Home Assistant add-on. On plain Docker / LXC deployments a local process on the host can no longer present an ingress marker to skip the login prompt.
- **A local process on a standalone host can no longer mint a full-access API token.** The one-shot Home Assistant pairing endpoint now operates only when running as a Home Assistant add-on, matching where legitimate pairing actually happens.
- **Revoking an API token now requires the same anti-forgery token as creating one**, so a malicious web page can no longer silently delete your tokens.
- **The MQTT "Test connection" button no longer sends your saved broker password to a different broker.** When the password field keeps its masked placeholder, the saved password is now reused only if the host and port match the saved broker; testing against any other broker requires entering the password explicitly.
- **Outgoing event webhooks are re-checked at delivery time, not just when they are added.** Delivery now verifies the address actually connected to, blocking a webhook host that later resolves to a loopback, private-network, or cloud-metadata address (DNS-rebinding).
- **Server-side link checks now correctly handle IPv4-mapped IPv6 addresses**, closing a path that could have reached loopback or cloud-metadata endpoints through a crafted address.
- **The bug-report submission rate limit can no longer be bypassed by spoofing a forwarding header** — the client address is trusted from a forwarding header only when the request arrives through a trusted proxy.
- **Authentication and token exchange responses are no longer written to debug logs**, so a long-lived token or authorization code can't leak into captured log output.
- **Image library `pillow` updated 12.2.0 → 12.3.0**, clearing five advisories flagged by the dependency audit. The CVE audit reports a clean tree again.

## [2.72.0-rc.1] - 2026-07-05

### Added

- **Auto-released speakers now reclaim themselves when they reconnect on their own.** Previously, once a device hit the auto-release threshold ("N consecutive failed reconnects") or the churn guard, it stayed released until someone pressed Reclaim in the web UI — so a speaker that was simply switched off overnight needed a manual click every morning. The bridge now watches released devices: when the speaker itself re-establishes the Bluetooth link, management is reclaimed automatically, audio is configured and the player restarts. Only automatic releases are eligible — a release made by the operator stays released, including across restarts. A one-minute quiet period after each auto-release prevents management flapping on speakers that are still bouncing. ([#349](https://github.com/trudenboy/sendspin-bt-bridge/issues/349), [#350](https://github.com/trudenboy/sendspin-bt-bridge/issues/350))
- **WirePlumber 0.5 headless systems (Raspberry Pi OS Trixie) no longer silently fail to create Bluetooth sinks.** WirePlumber 0.5 gates Bluetooth node creation on an active logind seat by default, so on a headless box a speaker would connect at the BlueZ level while no sink ever appeared. The bridge now detects the condition and logs the exact fix, the Raspberry Pi install script ships the disabling drop-in automatically, and the troubleshooting guide gained a dedicated section covering the new configuration format (the old Lua-based override only applies to WirePlumber 0.4). ([#331](https://github.com/trudenboy/sendspin-bt-bridge/issues/331), [#347](https://github.com/trudenboy/sendspin-bt-bridge/issues/347))
- **Pairing failures caused by HFP-default speakers now explain themselves.** When BlueZ reports that no audio profile could be brought up (typical for mic-equipped speakers that insist on the headset profile first, on hosts without an HFP backend — LXC containers especially), the failure reason shown in logs and the web UI now names the cause and links the recipe, instead of surfacing a bare BlueZ error code. The troubleshooting guide gained a dedicated section with the host-side fix, and the community speaker list documents the Tribit StormBox 2 fingerprint. ([#355](https://github.com/trudenboy/sendspin-bt-bridge/issues/355))

### Changed

- **Routine Bluetooth dependency bumps refreshed in the frozen lockfile.** `bluetooth-adapters` 2.1.1 → 2.4.0 and `usb-devices` 0.4.5 → 0.5.1 (both on the Linux Bluetooth-recovery path), plus `pyobjc-core` 12.1 → 12.2.1 (macOS-only, no effect on Raspberry Pi / Home Assistant / LXC deployments). ([#334](https://github.com/trudenboy/sendspin-bt-bridge/pull/334), [#335](https://github.com/trudenboy/sendspin-bt-bridge/pull/335), [#337](https://github.com/trudenboy/sendspin-bt-bridge/pull/337))
- **PulseAudio client library updated to `pulsectl-asyncio` 1.3.2.** Brings official Python 3.13 support, a fix for leaked asyncio file-descriptor registrations on unclean loop shutdown, and a fix for a spurious assertion crash at teardown while event subscriptions are active — both directly relevant to the bridge's sink-state monitoring. Also bumps transitive `idna` 3.15 → 3.18. ([#358](https://github.com/trudenboy/sendspin-bt-bridge/pull/358), [#359](https://github.com/trudenboy/sendspin-bt-bridge/pull/359))

### Fixed

- **Bluetooth scan now runs on — and labels results with — the adapter actually selected in the UI.** On hosts with several adapters where the kernel's hci numbering differs from BlueZ's registration order (typically after USB hotplug), selecting an adapter for a scan could silently scan a different physical adapter and show discovered devices under the wrong adapter name. Adapter selection now resolves through the same kernel mapping the adapter list uses, and per-adapter device attribution runs in dedicated sessions so discovery notifications can no longer corrupt it. ([#340](https://github.com/trudenboy/sendspin-bt-bridge/issues/340))
- **Crackling / "sandy" noise in the first seconds after starting playback is no longer audible.** Every fresh stream start triggers the sendspin sync corrector, which drops or duplicates frames for up to two seconds while converging on the DAC anchor — audible as a brief burst of crackle. The startup mute window now covers that entire settle window (raised from 1.5 s to 2.5 s), so the correction burst stays inaudible. Gapless track-to-track transitions were never affected. ([#341](https://github.com/trudenboy/sendspin-bt-bridge/issues/341))
- **A sick speaker daemon can no longer flood the bridge with tens of thousands of log lines per second.** When a daemon subprocess entered a pathological state (such as file-descriptor exhaustion after long disconnected periods), the bridge faithfully re-emitted every log line it produced — reported at ~22 000 lines/s, maxing out a CPU core and drowning the log ring. Forwarded daemon logs are now rate-limited (50 lines/s sustained, 200-line burst — every legitimate startup or reconnect flurry passes untouched) with a periodic "N lines suppressed" summary; status and error messages are never dropped. The underlying file-descriptor leak was fixed separately by the `pulsectl-asyncio` 1.3.2 upgrade in this release. ([#345](https://github.com/trudenboy/sendspin-bt-bridge/issues/345))

### Security

- **Security dependency bumps closing every CVE flagged by the dependency audit.** `aiohttp` 3.13.5 → 3.14.1 (eleven advisories spanning request-smuggling, header-parsing and resource-exhaustion fixes), `cryptography` 48.0.0 → 49.0.0 (GHSA-537c-gmf6-5ccf), and `PyJWT` 2.12.1 → 2.13.0 (five PYSEC-2026 advisories in the token-verification path). The CVE audit now reports a clean tree. `dbus-fast` is also bumped 5.0.3 → 5.0.17, carrying further patch-level hardening on top of the 5.x D-Bus message-parser denial-of-service bounds introduced in 2.71.2. ([#333](https://github.com/trudenboy/sendspin-bt-bridge/pull/333), [#336](https://github.com/trudenboy/sendspin-bt-bridge/pull/336))

## [2.65.1-rc.1] - 2026-04-29

### Added — Per-adapter Class of Device override (Samsung Q-series workaround)

Samsung HW-Q910B / Q990B / similar Q-series soundbars reject incoming BR/EDR connections whose initiator Class of Device they don't recognise — see [bluez/bluez#1025](https://github.com/bluez/bluez/issues/1025) and tracking [#210](https://github.com/trudenboy/sendspin-bt-bridge/issues/210). The on-the-wire signature is `HCI Connect Complete status=0x0d` → `MGMT No Resources (0x07)` → `org.bluez.Error.AuthenticationCanceled`, identical across different USB BT adapter chipsets.

This release adds an opt-in CoD override per adapter:

- **Configuration → Bluetooth → adapter row → Class of Device dropdown.** Choose **Computer/Laptop (0x00010c) — Samsung-compat** (the value documented in the BlueZ thread) or pick **Custom hex** for a different override. Default leaves the kernel/bluetoothd value untouched.
- The override is applied at bridge startup via the kernel mgmt API (`MGMT_OP_SET_DEV_CLASS`, opcode `0x002C`). Runtime-only, non-persistent across host reboots — the bridge reapplies it on every start.
- New helper `services/bt_class_of_device.py` shares the raw mgmt-socket transport with `services/bt_rssi_mgmt`. No new capability beyond the `CAP_NET_ADMIN` the bridge already holds.
- New config field `BLUETOOTH_ADAPTERS[].device_class` (six-hex-digit form, e.g. `"0x00010c"`). Empty default. Migration accepts existing configs without injecting the field.

### Added — Pair-failure fingerprint surfaced as operator guidance

When a pair attempt fails, the bridge now classifies the captured `bluetoothctl` output and `PairingAgent.telemetry`. A confident match against the Samsung Q-series fingerprint (AuthenticationCanceled + "No Resources" / `status 0x0d` + zero pairing-agent method calls) writes `pair_failure_kind="samsung_cod_filter"` to the device status and surfaces a targeted recovery card in the operator-guidance panel:

> Pair rejected by Class of Device filter — set `device_class` to `0x00010c` on this adapter in Settings → Bluetooth and re-pair.

The card takes precedence over the generic `repair_required` / `disconnected` cards because the operator action is different (set CoD, not re-pair).

### Docs

- New troubleshooting section: **Samsung Q-series soundbar refuses to pair** in `docs-site/src/content/docs/troubleshooting.md`.

### Code-review polish

- `bluetooth_manager.pair_device` now clears `pair_failure_kind` / `pair_failure_adapter_mac` / `pair_failure_at` at the start of every attempt, so a previous run's `samsung_cod_filter` fingerprint never outlives a successful re-pair or a different failure shape.
- `recovery_assistant` additionally gates the Samsung CoD card on the device being currently disconnected, so a stale fingerprint from before the operator applied the workaround can't keep the banner lit on a working speaker.
- Adapter-row "?" help affordance is a real `<button type="button">` with `aria-label`, reachable from keyboard and announced by screen readers.

## [2.63.2-rc.1] - 2026-04-27

### Review follow-ups (Copilot on PR #206)

- ``services/recovery_assistant._build_config_writable_issue`` now
  accepts an optional ``preflight`` parameter and is called with the
  already-collected payload from
  ``routes/api_status._build_recovery_assistant_payload`` — avoids
  rerunning the bluetoothctl + audio probes a second time per
  ``/api/status`` request.
- ``services/preflight_status._build_config_writable_payload`` no
  longer silently returns ``ok`` when ``$CONFIG_DIR`` doesn't exist;
  attempts ``mkdir(parents=True, exist_ok=True)`` and classifies any
  resulting ``OSError`` via ``collection_error_payload`` so non-
  container deployments without a pre-created config dir surface
  the issue instead of hiding it.
- ``routes/_helpers.config_write_error_response`` now derives the
  reported path from ``exc.filename`` (most accurate) with a fallback
  to ``config.CONFIG_DIR`` (live, monkey-patchable) instead of
  ``os.environ["CONFIG_DIR"]`` — keeps the response consistent with
  the actual config location across HA addon mode and tests.
- ``entrypoint.sh`` chown-failure ERROR line updated to say "fail
  with 500 responses because config cannot be persisted" instead of
  the now-misleading "return generic 500" — the new structured 500
  is exactly what Layer 2 ships.
- ``routes/ma_auth._save_ma_token_and_rediscover`` gains an explicit
  ``None | tuple[Response, int]`` return annotation so the new
  contract is grep-able.

One new test in ``tests/test_preflight_config_writable.py``
(mkdir-failure classification) + autouse fixture in
``tests/test_recovery_assistant.py`` to neutralise the global
preflight collector so existing tests don't surface a false-positive
``config_dir_not_writable`` card on dev machines.

### Fixed — defense in depth: detect & surface non-writable ``$CONFIG_DIR``

Issue [#190](https://github.com/trudenboy/sendspin-bt-bridge/issues/190)
spent significant time at "MA OAuth returns Internal Server Error" with
no actionable diagnostic — root cause was the bind-mount target left
as ``root:root`` while the bridge process runs as UID 1000, so the
first config write (``_save_ma_token_and_rediscover`` →
``update_config``) raised ``PermissionError`` and Flask defaulted to
the generic 500 HTML page.

Three layers of defense so the next operator gets a single-glance
diagnosis:

1. **Startup banner** — entrypoint now probes ``$CONFIG_DIR`` with
   touch/unlink as the *runtime* UID (via ``setpriv`` / ``gosu``) and
   adds a ``Config write: ✓ writable`` / ``✗ NOT writable`` row to the
   banner.  On failure, also emits an ``ERROR:`` line in journald with
   the exact ``chown`` command operators can copy verbatim.
2. **Actionable 500** — every Flask handler that writes to the config
   directory now wraps the write in ``try/except OSError`` and returns
   a structured JSON 500 with a ``remediation`` block (chown for
   ``EACCES``, remount for ``EROFS``, generic for the rest) instead of
   Flask's default ``Internal Server Error`` HTML.  Wrapped sites:
   ``_save_ma_token_and_rediscover`` (5 OAuth callers), ``POST
   /api/config``, ``POST /api/config/upload``, ``POST /api/set-password``,
   ``POST /api/settings/log_level``.
3. **Diagnostics surface** — ``services/preflight_status`` adds a
   ``config_writable`` slice (status / writable / config_dir / uid /
   remediation).  ``services/recovery_assistant`` reads it and renders
   a ``config_dir_not_writable`` recovery card with the chown command
   in the summary so it appears in the UI Diagnostics panel without
   operators reading container logs.  ``services/operator_check_runner``
   adds a ``config_writable`` re-runnable check so the "Re-run check"
   button flips the card green as soon as the operator runs the chown.
   ``services/guidance_issue_registry`` registers the new issue at
   priority 15 (between ``runtime_access`` and ``bluetooth``).

Helper: ``routes/_helpers.config_write_error_response(exc, context=...)``
builds the structured response.  Distinguishes ``EACCES`` /
``EROFS`` / unknown ``OSError`` so each gets the correct hint;
non-OSError exceptions still raise so real bugs aren't masked.

``docker-compose.yml`` gains a comment block above the ``/config``
volume mount documenting the pre-start ``chown -R 1000:1000`` step.

Tests: 5 + 4 + 5 + 2 + 2 new in
``tests/test_config_write_error_response.py``,
``tests/test_ma_auth_config_write.py``,
``tests/test_preflight_config_writable.py``,
``tests/test_operator_check_runner.py``, and
``tests/test_recovery_assistant.py``.  Full suite 1719 passing.

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

## [2.55.2-rc.1] - 2026-04-07

### Fixed
- **Connection errors not surfaced in UI** (#134) — `ClientConnectorError` from daemon subprocess was logged as WARNING but never shown in device status. Added `_connection_watchdog()` in BridgeDaemon (sets `last_error` after 30 s) and consecutive error counter in `SubprocessStderrService` (surfaces after 3+ repeated failures)
- **Generic "lost bridge transport" guidance for port mismatch** (#134) — when transport is down due to connection errors, recovery assistant now shows specific `sendspin_port_unreachable` issue with guidance to check `SENDSPIN_PORT`, instead of generic "restart" advice
- **Stale metadata reconnect timeout too short** (#134) — increased `_STALE_RECONNECT_READY_TIMEOUT` from 30 s to 120 s; added retrigger task that fires reconnect once daemon eventually connects, preventing permanent volume control loss

### Added
- **Sendspin port auto-probe** (#134) — when `SENDSPIN_PORT` is default (9000) and the configured host is explicit, the bridge now TCP-probes candidate ports (9000, 8927, 8095) before connecting. If an alternative port responds, it is used automatically with a WARNING log

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
