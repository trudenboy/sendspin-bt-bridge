---
title: "March 12: Observability, UX polish, and security hardening"
description: "Bug reports, diagnostics, demo mode, S6 overlay, code review, TWS earbuds, card redesign, modals, and the v2.30.7 security audit"
---

## March 12, 2026 — Observability & UX polish (v2.23.12 → v2.24.0, ~21 commits)

### Bug report with auto-diagnostics (v2.23.13–v2.23.16)

A Report button was added to the header that automates the entire bug reporting flow. On click it calls `/api/bugreport`, which collects all diagnostic data (devices, adapters, sinks, subprocesses, MA integration, environment, config, logs), masks sensitive data (MAC addresses partially, IPs, tokens), and returns two artifacts: a short Markdown summary (<4 KB, suitable for a GitHub issue URL's `?body=` parameter) and a detailed plain-text file for manual attachment.

The initial implementation went through several iterations: the full report started as a Markdown-formatted file with tables and collapsible sections, but was simplified to plain text with aligned columns for universal readability. The short summary gained last 3 WARNING/ERROR/CRITICAL log lines and MA server version (extracted from the WS handshake `server_info`). Form validation was added — the submit button stays disabled until both title and description are filled, and empty fields get red border highlights on click.

### Diagnostics enrichment (v2.23.17–v2.24.0)

The Diagnostics section previously showed only live connection status (bluetoothd, D-Bus, sinks, devices, MA groups). It now includes a version/environment header (bridge version, runtime type, uptime, Python version, platform, BlueZ, audio server, RSS memory, MA version) and per-subprocess status (pid, alive, zombie restarts, last error). The full-text report assembly was extracted from the bug report endpoint into `_build_full_text_report()` and reused for a new `/api/diagnostics/download` endpoint. Similarly, log reading was extracted into `_read_log_lines()` and reused for a `/api/logs/download` endpoint (500 lines as a timestamped text file).

### UX improvements

The restart banner was redesigned from a compact status counter (BT ✓ · PA ✓ · SS ✓ · MA …) with expandable per-device details to a sequential step display with a progress bar — since device statuses are already visible in the main UI cards, the banner now focuses on what's happening (saving → stopping → starting → connecting devices → connecting MA → done). An auth warning banner was added when authentication is disabled — a yellow bar with a direct link that scrolls to and highlights the auth checkbox in Configuration. Header links (Report, Docs, GitHub) received monochrome inline SVG icons using `currentColor` for theme compatibility.

A follow-up patch (v2.20.4) fixed the JWT token `<details>` section's disclosure marker — the native ▼ was replaced with a CSS `::before` ▶ that rotates on open, matching other collapsible sections — and corrected the Music Assistant API token hint to point to "Settings → Profile → Long-lived access tokens".

A documentation audit (v2.20.5) refreshed the entire doc corpus: version references updated from 2.10.6/2.12.2 to 2.20.4, the API route split reflected in CLAUDE.md, READMEs, and contributing guides, web-ui.md rewritten to dissolve the obsolete "Advanced Settings" section, and 6 screenshots recaptured from the live HAOS UI (battery badges, restructured config panels, diagnostics). A "Show all" checkbox in the paired-devices header that overflowed the container boundary was also fixed by repositioning the label before the checkbox with proper margin alignment.

### Legacy cleanup (v2.21.0)

A systematic audit of historical config keys and dead code removed accumulated legacy from 20+ versions of organic growth.

The `BLUETOOTH_MAC` single-device config key — the project's original parameter from its first commit — was fully deprecated. An auto-migration in `load_config()` converts it to a `BLUETOOTH_DEVICES` array entry on startup, then removes the old key from `config.json`. The migration was propagated across 23 files: config schema, API whitelist, web UI JavaScript, Docker Compose, entrypoint script, install scripts (RPi, LXC, OpenWrt), and all documentation in both English and Russian.

Five additional legacy keys were removed: `BRIDGE_NAME_SUFFIX` (dead since v2.13.0 when `BRIDGE_NAME` auto-population replaced it), `LAST_VOLUME` (the old single-integer volume, superseded by per-MAC `LAST_VOLUMES` dict), `keepalive_silence` (boolean toggle, replaced by `keepalive_interval > 0`), and the `port` device key (renamed to `listen_port`). Each removal included auto-migration where old configs could still exist.

Dead code was cleaned up: `get_client_status()` (a backward-compat wrapper from the v2.20.3 API modularization that was never called externally), unused re-exports in `routes/api.py`, and the `_save_device_volume` internal alias. The config schema was completed by adding `TRUSTED_PROXIES` and `MA_USERNAME` to `allowed_keys` — both were already read at runtime but could be silently dropped during config round-trips.

### MA beta authentication and UI polish (v2.22.0)

Music Assistant beta 2.8.0b19 changed its `/auth/login` API from a flat `{"username", "password"}` format to a nested `{"credentials": {"username", "password"}, "provider_id": "builtin"}` structure — breaking the bridge's login flow. A new `_ma_http_login()` helper in `routes/api_ma.py` tries the old format first (stable MA compatibility), then the new nested format, handling both `access_token` and `token` response fields. A critical bug was also fixed where the `music_assistant_client` library's generic "Invalid username or password" error message for any 401 caused a short-circuit that prevented the direct HTTP fallback from ever executing.

The web UI received several fixes: a token persistence race condition (the form's hidden `MA_API_TOKEN` field still held the old value after login, so "Save & Restart" overwrote the new token) was fixed by calling `loadConfig()` after login success before marking the form dirty. The "unsaved changes" indicator now appears after all five login success paths. The MA auth panel was redesigned — API URL and token fields were moved from a separate "Advanced" collapsible into the Reconfigure section, the duplicate URL field was removed (kept as a hidden input), and the buttons were renamed to "🔑 Get token" and "🔑 Get token automatically".

Additional UX improvements: a context-aware empty state that detects whether a BT adapter is present (linking to Adapters with auto-refresh if not, or launching a device scan if so), a static Save button in the config footer, a fix for phantom player cards with zero clients, and config dirty-state tracking for device add/remove operations.

---

### Demo Mode and update checker (v2.23.0)

A fully functional **Demo Mode** was added — setting `DEMO_MODE=true` launches the bridge with emulated BT devices and simulated MA playback, no hardware required. Five realistic devices (JBL Flip 6, Sony WH-1000XM4, Marshall Stanmore, Bose SoundLink, Harman Kardon Onyx) cycle through real track metadata from a curated playlist with proper durations and progress. The demo is deployed on Render.com as a one-click live demo: [sendspin-bt-bridge.onrender.com](https://sendspin-bt-bridge.onrender.com).

A **universal version update checker** runs as a background asyncio task, polling the GitHub releases API every hour. When a newer version is detected, a green badge appears in the UI header linking to the release notes. Three new API endpoints (`/api/update/check`, `/api/update/info`, `/api/update/apply`) provide platform-aware update instructions: LXC installations get a one-click "Update Now" button that executes `upgrade.sh`; Docker shows the `docker compose pull` command; HA addon directs users to the Supervisor.

The LXC `upgrade.sh` was fixed to download all route sub-modules (`api_bt.py`, `api_config.py`, `api_ma.py`, `api_status.py`) and new files (`update_checker.py`, `demo/` module) that had been added since the script was last updated.

### S6 overlay, AppArmor enforce, and auth refactoring (v2.23.1–v2.23.10)

**S6 overlay** (v2.23.1) replaced Docker's `--init` with proper PID 1 process supervision via S6 overlay v3.2.0.2 — zombie reaping, signal forwarding, and automatic restart on crash. The HA addon Dockerfile was simplified to a thin wrapper, and the standalone `run.sh` was removed.

**AppArmor enforce mode** (v2.23.6–v2.23.8) proved trickier than expected. The initial attempt used granular path rules (`/app/** rixm`, `/bin/** rix`) — standard AppArmor practice — but Docker's overlayfs made them unreliable: AppArmor silently blocked execution without any audit log on HAOS. Three releases were needed to diagnose the issue (no `dmesg` access, error looked like a filesystem permission problem). The fix came from studying the Music Assistant addon's AppArmor profile: blanket `file,` + `signal,` rules instead of granular paths. Security boundaries are enforced via capabilities, network rules, and signal restrictions. This pattern works reliably on all container runtimes.

**Auth refactoring** (v2.23.9) simplified authentication for the HA addon. In addon mode, authentication is now always enforced — the `auth_enabled` toggle was removed from the addon options. Only HA Core login_flow is offered (with full 2FA/MFA support); MA credentials and local password methods are hidden. The logged-in HA username is stored in the session and displayed next to the "Sign out" link. Ingress auto-auth (bypass via `X-Ingress-Path` from trusted proxies) continues unchanged. Docker/standalone mode retains the full set of auth methods.

**Header redesign** (v2.23.10) overhauled the web UI header into a compact 2-row layout. Row 1 shows the title, inline version with build date tooltip, an interactive update badge (gray "⟳ up to date" check button that morphs to green "⬆ vX.Y.Z" link when an update is found), and Docs/GitHub/Sign out links. Row 2 shows a runtime type badge (LXC / Docker / HA Addon), hostname, IP, uptime, and color-coded health indicators (BT x/n · MA x/n with green/yellow/red dots, plus ▶ playback count).

**Update modal** (v2.23.11) replaced the browser `confirm()` with a custom modal dialog. Clicking the update badge now shows a card with release notes preview (markdown stripped to clean bullet points) and two action buttons: "📋 Release Notes" (opens GitHub release) and a platform-aware apply button — "⬆ Update Now" for LXC/systemd (calls `/api/update/apply`, service restarts automatically), "🏠 Update in HA" for addon mode, or "📋 Show Instructions" for Docker.

**Auto-update** (v2.23.12) added `AUTO_UPDATE` option for LXC installations. Toggle in Configuration → Updates (off by default). When enabled, the hourly update checker automatically runs `upgrade.sh` upon detecting a new version — the service updates and restarts without user intervention. Only works on LXC/systemd (not Docker or HA addon).

The addon config gained `tmpfs: true` (in-memory temp for better SD card longevity), `backup_exclude` (omits logs and cache from HA snapshots), `auth_api: true` (formal auth API access declaration), and `panel_admin: false`.

---

### Code review and hardening (v2.25.0)

**Comprehensive expert code review** of the entire codebase (~17K lines, 30+ files) identified 3 critical security issues, 7 major improvements, and 7 minor cleanups. All recommendations were implemented in a single session using fleet-mode parallelism (17 tasks across 4 waves).

**Security fixes** (v2.25.0): the MFA session variable `_ha_login_user` was leaked between users on the same browser — now cleared at all 7 auth success paths and on GET /login. MAC addresses from `bluetoothctl` scan output were passed back to subprocess calls without re-validation — added strict `_MAC_RE` regex. Three API endpoints silently fell back to the first device when `player_name` was missing in multi-device setups — replaced with proper 400 errors.

**Architecture improvements**: the monolithic 260-line `login()` handler was split into 4 per-flow functions (`_handle_ma_login`, `_handle_ha_via_ma_login`, `_handle_ha_direct_login`, `_handle_local_password_login`). Duplicated client lookup logic across BT endpoints was extracted into shared `get_client_or_error()` and `validate_mac()` helpers in `routes/_helpers.py`. Config writes gained atomic tempfile+rename. 27 broad `except Exception` clauses were narrowed to specific types across 6 modules.

**Test coverage expanded**: 30 new tests covering client lookup (multi-device, injection attempts), MFA session lifecycle (variable cleanup, cross-user leak), and BT scan cooldown (429/409 codes). Total test count grew from 150 to 180.

### TWS earbuds and UX improvements (v2.25.1 → v2.26.0)

**SSP passkey auto-confirm** (v2.26.0): TWS earbuds like HUAWEI FreeClip require Simple Secure Pairing (SSP) confirmation — a "Confirm passkey XXXXXX?" prompt from `bluetoothctl` that must be answered with "yes". The `pair_device()` function was rewritten to read `bluetoothctl` stdout in real-time using `selectors`, detect passkey confirmation prompts, and auto-send "yes". Early exit on "Pairing successful" for faster completion.

**D-Bus resilience for TWS** (v2.26.0): TWS earbuds going into their charging case leave stale BlueZ D-Bus objects that throw `DBusException` on property reads. Exception handling was widened in `_dbus_get_device_property()`, `_dbus_get_battery_level()`, `_dbus_call_device_method()`, and `is_device_connected()`. An auto-reconnect path was added: when the polling loop detects a device connected externally (e.g. earbuds taken out of case) but the player isn't running, it automatically configures audio and starts the player.

**HA username in header** (v2.26.0): Ingress sessions (HA sidebar) previously showed no username — the Supervisor doesn't pass identity headers. Now `_check_auth` resolves the HA owner's display name on first Ingress request and caches it in the session. The initial implementation (v2.26.0) tried `core/api/auth/current_user` via `SUPERVISOR_TOKEN`, but addon tokens get 401 on that endpoint — fixed in v2.26.2 to read `MA_USERNAME` from config.json (saved during the HA login flow).

**Update dialog re-check** (v2.26.0): the version badge in the header now opens the update dialog with a 🔄 Re-check button — useful after applying an update or when a newer version has been released since the last hourly check.

### Smooth restart and sink routing cleanup (v2.26.0 → v2.26.1)

**Smooth restart** (v2.26.1): restarting the bridge previously caused audible glitches — PA sinks were destroyed and recreated, sendspin re-anchored streams, and audio would stutter for several seconds. Three improvements eliminate the disruption:

1. **Pre-restart mute**: `saveAndRestart()` in the web UI mutes all local PA sinks via a `force_local` flag before triggering the restart. This doesn't touch MA (so sync group members on other bridges keep playing) — it's a PA-level mute only.
2. **Startup mute + auto-unmute**: `daemon_process.py` mutes the PA sink immediately after creating `BridgeDaemon`. A `_startup_unmute_watcher` coroutine polls for `audio_streaming=True`, waits an additional 1.5 s for stabilisation, then unmutes. If no audio streams within 60 s, unmute is skipped (v2.26.3 fix — previously the watcher's completion after timeout killed the daemon via `FIRST_COMPLETED`).
3. **Sink name cache**: `LAST_SINKS[mac]` is persisted to `config.json` (parallel to `LAST_VOLUMES[mac]`). On restart, `configure_bluetooth_audio()` tries the cached sink first via a `get_sink_volume()` probe — if valid, it skips the 3 s A2DP profile delay and the multi-pattern retry loop.

**Server-side graceful shutdown** (v2.26.4): `_graceful_shutdown()` previously sent `{"cmd": "pause"}` to subprocess stdin, which paused the player in MA — affecting sync group members on other bridges. Now it mutes PA sinks directly via `aset_sink_mute()` before stopping subprocesses. This works for all restart triggers (systemd, Docker restart, HA auto-update, CLI), not just the web UI's `saveAndRestart()`.

**Zombie-playback detection rework** (v2.26.4): the zombie watchdog (red equalizer state → subprocess restart) previously triggered whenever `playing=True` and `audio_streaming=False` persisted for 15 s. This caused false restarts during re-anchor, group sync calibration, or track changes — PA buffers were still playing audio while the flag was momentarily `False`. Now the watchdog tracks `_has_streamed` per subprocess session: it only triggers when audio has *never* arrived in the current session, catching genuinely stuck subprocesses without disrupting normal playback gaps.

**Legacy move-sink-input removal** (v2.26.1): `_ensure_sink_routing()` and the `_sink_routed` flag were removed from `BridgeDaemon`. This code was a leftover from the pre-`PULSE_SINK` architecture (Iteration 1, v2.1) where streams had to be reactively moved to the correct sink. With the subprocess-per-speaker design (each process has `PULSE_SINK` in env), PA routes new sink-inputs to the correct sink from the first sample. The move-sink-input call was not only unnecessary but harmful — it caused a PA glitch that triggered re-anchoring, creating a potential feedback loop (guarded by `_sink_routed`, but still adding latency). `amove_pid_sink_inputs()` remains in `services/pulse.py` as a diagnostic utility.

**Post-start sink routing correction** (v2.26.5): despite `PULSE_SINK` being correctly set in the subprocess environment, PulseAudio can still route sink-inputs to the default sink. This happens because all subprocesses share the same `application.name` (`ALSA plug-in [python3.12]`), and PA's `module-stream-restore` remembers the last sink used for that application name — even with `restore_device=false`. The fix re-introduces `amove_pid_sink_inputs()` as a one-shot correction in `_startup_unmute_watcher`: after `audio_streaming=True`, the subprocess moves its own sink-inputs to the correct sink before unmuting. Unlike the removed `_ensure_sink_routing()` (which ran reactively on every format change inside BridgeDaemon), this runs once at startup in the watcher, after audio is confirmed flowing.

**Equalizer indicator accuracy** (v2.26.5): `audio_streaming` was only set to `True` in `_handle_format_change()`, which fires when the first audio chunk arrives with codec/rate/depth/channels metadata. On re-anchor or track change with the same format, `_handle_format_change` is not called again — but `_on_stream_event("stop")` had already reset `audio_streaming=False`. Result: playing audio with a red (stale) equalizer indicator. Fixed by also setting `audio_streaming=True` in `_on_stream_event("start")` when `audio_format` is already configured.

### Two-tier enabled/disabled and smart health (v2.26.5 → v2.27.0)

**Global device enabled/disabled** (v2.27.0): the `enabled` flag was redesigned from a BT-only hint into a full device lifecycle control. When `enabled=false`, the device is completely removed from all stacks: no `SendspinClient` created, no `BluetoothManager`, no subprocess, no MA player registration. The device's metadata (name, MAC, adapter) is preserved in config and shown as a dimmed checkbox in Configuration → Devices. Re-enabling requires a container restart to re-create the full stack.

This is distinct from BT Release/Reclaim (`set_bt_management_enabled`), which only affects the Bluetooth layer — the client object stays alive in memory, can be reclaimed without restart, and the device remains visible in the dashboard.

**MA player cleanup on disable** (v2.27.0): when a device is disabled via the config checkbox, the API handler calls `set_bt_management_enabled(False)` on the active client before marking it disabled. This stops the daemon subprocess, which disconnects its WebSocket to MA, triggering MA's `ClientRemovedEvent` — the player is unregistered immediately rather than lingering as "unavailable" until MA's next cleanup cycle.

**Smart health indicators** (v2.27.0): a new `bt_released_by` field in `DeviceStatus` tracks *why* a device was released — `"user"` for manual Release button, `"auto"` for churn detection (`_check_reconnect_churn`) or reconnect threshold (`_handle_reconnect_failure`), `null` when enabled. The health indicator in the header now excludes manually released devices from BT/MA totals entirely (they're shown as a separate grey count — "N released"). Auto-disabled devices still count as unhealthy, keeping the indicator yellow/red to signal that attention is needed. The device card badge changes accordingly: grey "Released" for manual, orange "Auto-disabled" for automatic.

### UX polish (v2.27.0 → v2.27.1)

**BT unpair from UI** (v2.27.1): the "Already paired" device list in Configuration now has a ✕ Remove button on each row. Clicking it calls `POST /api/bt/remove` → `bt_remove_device()` → `bluetoothctl remove <MAC>`. The row fades out and the list refreshes after 1.5 s. Previously, removing stale pairings required SSH access to run `bluetoothctl remove` manually.

**Restart indicator redesign** (v2.27.1): the restart progress indicator was moved from a standalone full-width banner (between header and content) into the header card itself, as a third row. Visual changes: emoji status icons (💾🔇🔄⏳🔗🎵✅⚠️) replaced with CSS-styled elements — a spinning border-radius spinner during progress, an SVG checkmark on success, an SVG warning icon on failure. Background colors changed from hardcoded pastel values (`#fef3c7`, `#d1fae5`, `#fee2e2`) to theme-native white-on-primary, which works correctly in both light and dark modes. The progress bar uses `rgba(255,255,255,0.15)` track with `rgba(255,255,255,0.7)` fill — subtle but visible on the blue header. No layout shift for page content since the banner grows inside the header card.

### Fixes and device card redesign (v2.27.1 → v2.28.0)

**BT remove endpoint fix** (v2.28.0): `POST /api/bt/remove` crashed with a 500 error on Proxmox LXC because `validate_mac()` in `routes/_helpers.py` returns a `bool`, but the endpoint code used the `err = validate_mac(mac); if err: return err` pattern — returning `True` as a Flask response, which Flask rejected with `TypeError: return type must be a string, dict, list...`. Fixed to `if not validate_mac(mac): return jsonify({"error": "Invalid MAC address"}), 400`.

**HA username from Ingress headers** (v2.28.0): the HA addon always showed "HA User" instead of the actual logged-in user's display name. Root cause: `_resolve_ingress_user()` fell back to the hardcoded string when `MA_USERNAME` wasn't in config. Fix: `_check_auth()` now reads `X-Remote-User-Display-Name` and `X-Remote-User-Name` headers that the HA Supervisor Ingress proxy sends since HA 2024.x (set in `supervisor/api/ingress.py:_init_header()` from `IngressSessionData`). Headers are only trusted from the Supervisor proxy IP (172.30.32.2/127.0.0.1/::1) — spoofed versions from external clients are stripped.

**Bug report modal redesign** (v2.28.0): the bug report modal was visually inconsistent with the rest of the UI — emoji icons (⚠, 📋, ⟳), hardcoded `#1a73e8` blue that didn't match HA themes, no close button, no keyboard support. Redesigned with: `--primary-color` accent header bar with ✕ close button, inline SVG icons for bug/GitHub/copy/info using `currentColor` for theme compatibility, CSS border-radius spinner replacing the ⟳ emoji during loading, inline validation error messages (red border + text instead of alert box), Escape key to dismiss, fade-in/slide-up animation, and a dark-themed code-block for the diagnostic data preview.

**Connection column compaction** (v2.28.0): the Connection column consumed ~176px with redundant "Connected"/"Disconnected" text that duplicated the colored status dots. Redesigned: status text hidden by default via `.conn-text { display: none }` — the dots (green/red/amber/grey) are self-explanatory, with full text available via native `title` tooltip (`btInd.title = 'BT: ' + text`). MAC address and server URI hover-sub elements removed entirely. Column shrunk to 85px fixed width, freeing ~100px for the identity column. The "Connection" label hidden (BT/MA tags self-describe). On mobile (≤840px), text and label are always visible since touch devices can't hover.

**Identity column optimization** (v2.28.0): the identity column had all elements (checkbox, player name, released badge, eq-bars, battery) crammed into a single flex row that wrapped awkwardly with long names, plus group badge on its own line and hover-only MAC/URL. Restructured into clean two rows: Row 1 (`identity-title-row`) — checkbox + player name (with `flex:1; text-overflow:ellipsis` for clean truncation) + eq-bars; Row 2 (`identity-meta-row`) — released badge, battery badge, and group badge inline. MAC address and WebSocket URL removed from the dashboard entirely — MAC is visible in Configuration, and the WS URL was debug information.

### UI polish (v2.28.0 → v2.28.1)

**Update modal redesign** (v2.28.1): the update dialog was visually inconsistent — emoji icons (🔄, 📋, ⬆, 🏠), hardcoded `#2e7d32` green, no close button, no keyboard support, no animation. Redesigned to match the bug report modal pattern: green (`--success-color`) accent header bar with SVG arrow-up icon and ✕ close button, version comparison row showing `v2.28.0 → v2.28.1`, SVG icons on all buttons (refresh, notes, arrow, home), Escape key to dismiss, `brFadeIn`/`brSlideUp` animations, and theme CSS variables throughout.

**Adapter badge** (v2.28.1): the BT adapter name (`hci0`) in the connection column was plain 11px text. Restyled as a compact neutral badge — 9px uppercase, `--divider-color` background and border, 3px radius — matching the purple `api` badge pattern but in grey/white.

**Equalizer placement** (v2.28.1): eq-bars were pushed to the far right edge of the identity column because `device-card-title` had `flex:1`. Removed `flex:1` so the eq-bars sit immediately after the player name text, which is the natural reading order.

**Column labels removed** (v2.28.1): the Playback, Volume, and Sync column headers were removed — their content (transport controls, volume slider, sync offset) is self-evident without labels. The Connection column label was already hidden via CSS in v2.28.0.

### Card redesign and player-id refactor (v2.28.2 → v2.29.0)

**Player-id group matching** (v2.29.0): MA group badges were matched by fuzzy player name comparison — `"ENEBY 30 @ Proxmox"` matched against `"ENEBY 30"` — which broke on hosts with different bridge suffixes or when MA reported the full qualified name. Refactored to use the stable `player_id` (UUID generated from MAC) for matching: `state.py` stores `player_id` per client, `api_ma.py` resolves groups by player_id instead of name substring. The player_id is deterministic (`_player_id_from_mac()` in `config.py`) and never changes for a given device.

**Device card redesign** (v2.29.0): cards restructured from a 5-column CSS grid to a row-based layout. Status indicators changed from `status-indicator` divs with CSS classes to compact `status-dot` spans with color classes (`green`/`red`/`orange`/`grey`). Sync group display changed to chip format. Delay format changed to `±Nms`. Pause button changed to `⏸` symbol. Shuffle and repeat buttons made always-visible when MA is active (were hover-only).

**Report error highlighting** (v2.29.0): the Report link in the header now turns yellow (`#f59e0b`) when the last 20 log entries contain ERROR or CRITICAL level messages. The `.has-errors` CSS class is toggled in `renderLogs()` on the `#report-link` element, matching the amber warning pattern.

**Bug report modal yellow accent** (v2.29.0): the bug report modal header was changed from blue (`--primary-color`) to amber (`#f59e0b`), and the primary submit button from blue to amber with `#d97706` hover. This visually distinguishes it from the green update modal — yellow for "attention/warning" vs green for "positive action".

**Released → disabled persistence bug** (v2.29.0): on restart, the startup sync loop called `persist_device_enabled(name, bt_management_enabled)` for all clients. For "released" devices, `bt_management_enabled=False` was written as `enabled: false` to config.json, causing the device to be fully skipped on the next restart. Fixed: the sync loop now only writes `enabled=true` for non-released devices, preserving the distinction between "BT released" (loads but doesn't manage BT) and "globally disabled" (completely skipped).

**Disable button** (v2.29.0): added `⛔ Disable` button to the device card actions row (after Release), calling `confirmDisableDevice()` with a confirmation dialog before toggling the device's enabled state via the existing `/api/device/enabled` endpoint.

### Modals, config portability, and mute fix (v2.30.0 → v2.30.6)

**BT Info modal** (v2.30.0): `showBtDeviceInfo()` previously called `bluetoothctl info <MAC>` and dumped the raw text output into a browser `alert()` — functional but ugly, unselectable, and inconsistent with the rest of the UI. Replaced with a styled modal dialog reusing the bugreport modal CSS classes (`.br-overlay`, `.br-modal`, accent header bar with ✕ close button). The raw output is rendered in a preformatted code block with a Copy button. The modal is keyboard-dismissible (Escape) and accessible.

**BT adapter reboot** (v2.30.0): added a ↻ Reboot button next to each detected BT adapter in Configuration. The initial design was a pair of On/Off toggle buttons, but `BluetoothManager`'s reconnect loop automatically powers adapters back on after a power-off — making the Off button effectively useless. Settled on a single Reboot action (power off → 3 s delay → power on) with the button locked during the operation. This is the UI equivalent of `bluetoothctl power off && sleep 3 && bluetoothctl power on` — useful for recovering from stuck BT stacks without SSH access.

**Scan cooldown countdown** (v2.30.1): the 30 s BT scan cooldown previously gave no feedback — the Scan button just returned a 429 with a generic message. Now the backend includes `retry_after` seconds in the 429 response body, and the frontend starts a visible countdown on the button label (`🔍 Scan (28s)` → `🔍 Scan (27s)` → ... → `🔍 Scan`). The countdown also kicks in on a rejected scan attempt so the user always sees how long to wait, even if they missed the original scan trigger.

**Config download/upload** (v2.30.2): two new buttons in the Configuration section footer enable config portability. ⬇ Download saves the raw `config.json` with a timestamped filename (`{bridge_name}_SBB_Config_{YYYYMMDD_HHMMSS}.json`) — useful for backups before risky changes or for cloning a setup to another host. ⬆ Upload replaces the current config from a JSON file, but preserves security-sensitive keys (`AUTH_PASSWORD_HASH`, `SECRET_KEY`, `MA_ACCESS_TOKEN`, `MA_REFRESH_TOKEN`) from the running config — so uploading a backup from a different instance doesn't wipe credentials. The upload endpoint validates JSON structure, MAC address format, and port ranges before writing.

**Mute indicator fix** (v2.30.3): after the smooth-restart work (v2.26.1), the `_startup_unmute_watcher` in `daemon_process.py` mutes the PA sink on subprocess startup (to hide re-anchor clicks), then unmutes after audio stabilises or after a timeout. Bug: after unmuting, the watcher set `status["sink_muted"] = False` but never called `_on_status_change()` to emit the updated status to the parent process via the JSON-line IPC. The parent kept the stale `sink_muted=True` from startup, so the web UI showed all players as muted indefinitely — the mute icon never cleared. Fixed by passing the `_on_status_change` callback to the watcher and calling it after unmute, which emits the corrected status to the parent and triggers an SSE push to the browser.

**Startup unmute timeout reduced** (v2.30.3): the `_startup_unmute_watcher` timeout was reduced from 60 s to 15 s. The 60 s value was carried over from early development when BT audio setup was unreliable. In practice, idle players (not actively streaming) would sit in a muted state for a full minute after every restart before the watcher gave up and unmuted. 15 s is more than enough for audio to begin flowing if it's going to.

**UI reorganisation** (v2.30.4): button ordering was inconsistent across sections — some had the primary action first, others had it last. Standardised: Adapters section: `+ Add Adapter` before `↺ Refresh`. Devices section: `+ Add Device` before `🔍 Scan`. Scan results: Add before Add & Pair (renamed from "Pair & Add" to match the actual operation order). Paired devices: Add button first, then action buttons (BT Info, Reset & Reconnect, ✕) grouped on the right with CSS `:has()` hover isolation so hovering one button doesn't highlight the whole row. Config footer: left group (Save, Save & Restart), right group (⬇ Download, ⬆ Upload).

**BT device info in bug report** (v2.30.5): `_collect_bt_device_info()` now runs `bluetoothctl info <MAC>` for each configured device and appends the paired/trusted/connected/bonded/blocked status flags to the bug report diagnostic text. Previously, debugging BT issues from a bug report required asking the user to SSH in and run `bluetoothctl info` manually — the report now includes everything needed for remote triage.

**Dashboard layout fixes** (v2.30.6): three CSS issues addressed — the "No Bluetooth devices configured" empty-state block only occupied one grid column instead of spanning the full width (`grid-column: 1 / -1`); hovering any device card caused all cards in the same row to expand because CSS Grid's default `align-items: stretch` makes rows the height of the tallest card (fixed with `align-items: start`); and the album art popup on track name hover was clipped by `overflow: hidden` on parent containers.

**Version badge → release notes** (v2.30.6): the version badge in the header (e.g. `v2.30.6`) is now an `<a>` tag linking to the corresponding GitHub release page — a quick way to check what changed in the running version without navigating to GitHub manually.

**Username → profile link** (v2.30.6): the username in the header is now clickable, linking to the user's profile page. In HA addon mode it links to the HA profile (`/profile`). In standalone mode the username moves from the header icons row to the status bar (alongside `BT 3/3 · MA 3/3`), and links to the MA profile when MA is connected, or to the HA profile when authenticated via HA. The auth method (`ma`, `ha`, `ha_via_ma`, `password`) is tracked in the Flask session and passed to the template as `data-auth-method`, which the JS status handler uses to compute the correct profile URL.

### Security hardening and code review fixes (v2.30.7)

**Comprehensive code review** (v2.30.7): a full code review of the entire codebase (~22K lines, 71 files) identified 66 potential issues. After verification against actual code, 53 were confirmed true — 13 were false positives or already mitigated. The confirmed findings were grouped into 15 implementation tasks covering security, concurrency, data integrity, and infrastructure.

**XSS fix in HA auth page** (v2.30.7): the `api_ma_ha_auth_page` endpoint substituted the `ma_url` query parameter directly into an inline JavaScript template via string replacement — a classic reflected XSS. An attacker could craft a URL with `ma_url=';alert(document.cookie)//` to execute arbitrary JS in the auth popup context. Fixed by escaping through `json.dumps()` and adding URL scheme validation (only `http`/`https` or empty allowed; `javascript:` and other dangerous schemes are rejected with 400).

**Command injection via adapter parameter** (v2.30.7): five endpoints in `api_bt.py` passed the `adapter` field from user input directly into `bluetoothctl` stdin commands without validation. Since `bluetoothctl` processes commands separated by newlines on stdin, a value like `hci0\nremove AA:BB:CC:DD:EE:FF` would inject extra commands. Fixed with a `validate_adapter()` helper in `_helpers.py` that enforces a strict regex (`^(hci\d+|MAC_FORMAT)$`) and rejects anything containing newlines, semicolons, or shell metacharacters.

**CSRF protection** (v2.30.7): the login form (password, HA login flow, MFA — five `<form>` tags total in `login.html`) submitted POST requests without CSRF tokens. While JSON API endpoints have implicit protection (browsers won't send `Content-Type: application/json` cross-origin without CORS preflight), the HTML form was vulnerable to cross-site form submission. Added per-session CSRF token generation (`secrets.token_hex(32)`) stored in the Flask session, a hidden `<input>` in every form, and timing-safe validation via `hmac.compare_digest()` on POST. Invalid or missing tokens return 403.

**Content Security Policy** (v2.30.7): no CSP header was set, meaning any XSS vulnerability could load external scripts, exfiltrate data, or modify the page arbitrarily. Added `Content-Security-Policy` restricting `default-src` to `'self'`, with `script-src` and `style-src` allowing `'unsafe-inline'` (necessary due to inline `onclick` handlers in `app.js`), `img-src` allowing `data:` URIs (for inline SVG icons), and `connect-src` allowing `ws:`/`wss:` (for SSE and WebSocket connections). Also added `X-Content-Type-Options: nosniff` on all responses to prevent MIME-type sniffing.

**MA monitor event loss** (v2.30.7): three methods in `ma_monitor.py` — `_drain_cmd_queue`, `_send_queue_cmd`, and `_refresh_stale_player_metadata` — read WebSocket messages in a loop looking for a response matching a specific `message_id`. Non-matching messages (real-time events from MA: playback state changes, queue updates, player status) were silently discarded. In a busy MA instance this could lose seconds of real-time updates. Fixed by logging non-matching messages at DEBUG level instead of discarding silently. A more complete solution would buffer and re-process them as events, but that requires deeper protocol analysis.

**mDNS discovery thread safety** (v2.30.7): the zeroconf `_on_service_state_change` callback used `asyncio.ensure_future()` to schedule async resolution work. This callback runs on zeroconf's internal thread, not the asyncio event loop thread — `ensure_future` requires a running loop in the current thread. Replaced with `asyncio.run_coroutine_threadsafe(coro, loop)` where `loop` is captured before zeroconf starts.

**Concurrency fixes** (v2.30.7): two thread-safety issues fixed. In `sendspin_client.py`, `_read_subprocess_output` read `prev_volume` inside `_status_lock` but read `new_volume` outside it — between the two reads, another thread could change volume, making the comparison invalid. Both reads are now inside the same lock scope. In `state.py`, `get_scan_job()` returned a direct reference to the internal dict instead of a copy — callers could mutate internal state after the lock was released. Now returns `dict(job)`.

**Error message sanitisation** (v2.30.7): 18 API endpoints across `api_bt.py`, `api_status.py`, `api_config.py`, and `auth.py` returned `str(e)` in error JSON responses, exposing internal file paths, subprocess command details, and Python tracebacks to API clients. Replaced with generic context-appropriate messages (e.g. "Failed to list adapters", "Bluetooth operation failed"); actual exceptions are logged server-side via `logger.exception()`.

**Infrastructure** (v2.30.7): added `pytest` execution to the CI pipeline (previously only ruff and mypy ran — 178 tests existed but were never enforced in CI). Pinned dependency upper bounds (`zeroconf<1.0`, `ruff<1.0`, `mypy<2.0`) to prevent unexpected breakage from major version bumps. Fixed `asyncio.get_event_loop()` deprecation warning (Python 3.12+) with `get_running_loop()`. Fixed `DEFAULT_CONFIG` shallow copy that could cause shared mutable references across config instances. Removed 8 dead regex patterns from `routes/api.py` (copy-paste artifacts from `api_bt.py`). Added 1 MB size limit on config file uploads.

---
