---
title: 2026-03-25 — Native transport and null-sink standby
description: March 25–26 delivered native Sendspin transport commands, cross-bridge duplicate detection, ALSA crash recovery, Phase 2 null-sink standby with auto-wake, 125+ new tests, and dependency upgrades across v2.48.0 through v2.50.0-rc.1
---

The final push of the week shipped the two features that had been building toward all along: native Sendspin transport control and null-sink standby with auto-wake. Twenty-one RC releases across four stable tags and one RC delivered these alongside ALSA crash recovery, cross-bridge duplicate detection, a security hardening pass, and over 125 new tests.

## What shipped

### Native Sendspin transport commands (v2.48.0)

The bridge can now send play, pause, stop, next, previous, shuffle, repeat, mute, and volume commands natively through the Sendspin Controller role via `POST /api/transport/cmd`. Previously, all transport control went through Music Assistant's queue API, adding latency and requiring an active MA connection.

The transport UI falls back to MA queue commands automatically when native transport is unavailable, so operators with older MA installations see no change in behavior. Shuffle and repeat buttons update immediately in the web UI after a successful native command, matching the snappier feel of direct control.

### Extended metadata forwarding

The Sendspin protocol connection now forwards album, album artist, artwork URL, year, track number, shuffle state, repeat mode, and controller state updates including `supported_commands`, `group_volume`, and `group_muted`. This metadata was always available in the protocol but had never been surfaced in the bridge's status model.

### Cross-bridge duplicate detection

When multiple bridge instances are running (common in setups with both HAOS and standalone LXC deployments), the bridge now detects when another instance already owns a Bluetooth device. Startup and recovery flows surface conflict warnings, and the BT scan modal prompts for confirmation before pairing a device that is already claimed elsewhere.

### Startup and audio defaults tuning

First-run behavior became more reliable with tuned defaults: startup grace defaults to 5 seconds, recovery-banner grace to 15 seconds, `PULSE_LATENCY_MSEC` to 600, and newly added devices default to `static_delay_ms = -300`. These values were chosen based on production HAOS telemetry and eliminate the most common "works after one manual restart" reports.

### ALSA underrun crash recovery (v2.48.1)

A `ValueError: memoryview assignment: lvalue and rvalue have different structures` crash could occur after ALSA underrun and re-anchor recovery inside the subprocess runtime. The bridge now guards against stale cached output-frame state so a reused frame from an older format or correction cycle is reset instead of crashing.

### False transport-loss recovery (v2.48.1)

Several false-positive guidance states were eliminated:

- Active audio streaming is now treated as authoritative during brief Sendspin control reconnects, so transient `server_connected=false` windows no longer trigger transport-loss warnings while the speaker is still playing.
- Idle speakers enter a dedicated `ma_reconnecting` transition during planned MA metadata reconnects instead of surfacing disconnect warnings.
- After a successful replacement reconnect, the bridge publishes `server_connected` only after the new WebSocket handshake succeeds, preventing the old session's disconnect callback from overwriting fresh connection state.

### Visualizer role compatibility fix (v2.48.2)

Newer `aiosendspin` builds exposed the draft `visualizer@_draft_r1` role, which caused Music Assistant to reject the player connection during startup. The bridge no longer advertises the visualizer role in `ClientHello`, and `aiosendspin` is now pinned to `4.3.2` directly in `requirements.txt` to prevent transitive dependency drift.

### Phase 2: null-sink standby with auto-wake (v2.49.0)

This was the headline feature of the week. When a speaker goes idle, the daemon stays alive on a PulseAudio null sink instead of shutting down. The Music Assistant player remains visible, so playback auto-resumes when triggered — with approximately 5-second Bluetooth reconnect latency instead of the 30+ seconds required for a cold daemon start.

The standby system includes:

- **Auto-wake on play** — when MA sends play while a speaker is in standby, Bluetooth reconnects automatically.
- **Sync-group wake** — group members wake each other, so starting playback on one speaker in a sync group brings the others online.
- **Idle disconnect** — per-device `idle_disconnect_minutes` disconnects Bluetooth after a silence timeout to save speaker battery.
- **Mutual exclusion** — keep-alive and idle standby are mutually exclusive in both the UI and the backend; enabling one disables the other.
- **Null-sink fallback** — a `sendspin_fallback` null sink prevents orphaned streams from landing on random Bluetooth speakers.
- **Disable rescue-streams option** — `DISABLE_PA_RESCUE_STREAMS` unloads PulseAudio's `module-rescue-streams` at startup for environments where sink drift is persistent.

The standby UI shows a 💤 badge on device cards, a moon/sun toggle button, and a "Waking" transition state. A standby status filter in the toolbar lets operators focus on active or sleeping devices.

### Custom exception hierarchy

Error handling moved from bare `Exception` catches to a structured hierarchy: `BridgeError` → `BluetoothError`, `PulseAudioError`, `MusicAssistantError`, `ConfigError`, `IPCError`. This made error-path testing meaningful and allowed callers to catch specific failure modes.

### Security hardening (v2.49.0)

The standby release included a security pass: PBKDF2 iterations upgraded to 600K with a versioned hash format, `POST /api/config` now filters through an allowed-keys whitelist, artwork proxy validates Content-Type (image/* only), dynamic `onclick` values use `escHtmlAttr()` for XSS prevention, `SYS_ADMIN` capability was removed from HA addon configs, and each daemon subprocess now sets a unique PulseAudio application name to prevent `module-stream-restore` from confusing streams across speakers.

### 125+ new tests and CI unification

The test suite grew from ~830 to 959 tests, covering sendspin_client, web_interface, bt_monitor, and bt_manager. The CI/CD pipeline was unified into a single release workflow: a single `VERSION` file triggers lint → test → tag → Docker build → HA addon sync.

### Configuration UX overhaul

The General settings tab was reorganized into focused sections, and a dedicated Audio tab was added for PulseAudio settings. An experimental features toggle (browser-local) shows or hides room name, room ID, and handoff mode fields. Release and Reclaim actions moved to the Device Fleet dropdown and Already Paired list.

### Dependency upgrades (v2.50.0-rc.1)

The first RC of the next cycle bumped core dependencies: websockets 13.1 → 16.0 (async API migrated to `websockets.asyncio.client`), waitress 2.1.2 → 3.0.2, and CI actions updated across the board.

## Why this matters

Native transport and null-sink standby together transformed the bridge from a playback relay into something closer to a first-class Music Assistant player. Transport commands execute in milliseconds instead of round-tripping through MA's queue API. Standby means speakers wake in 5 seconds instead of 30, making multi-room handoffs feel responsive instead of broken.

The security hardening, custom exceptions, and 125+ new tests gave the project the reliability foundation needed to support these more complex runtime behaviors. And the cross-bridge duplicate detection addressed a real pain point for operators running both HAOS and LXC deployments.

## Follow-up

`v2.50.0-rc.1` marks the start of the next development cycle, focused on dependency modernization and preparing the runtime for the v3 backend abstraction work outlined in the updated roadmap.
