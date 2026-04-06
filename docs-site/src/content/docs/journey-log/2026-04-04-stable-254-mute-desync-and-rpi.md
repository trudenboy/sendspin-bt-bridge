---
title: 2026-04-04 — v2.54.0 stable, mute desync, and Raspberry Pi
description: The v2.54.0 stable release consolidated the SinkMonitor architecture, then rapid RC cycles fixed restart hangs, mute desync, Raspberry Pi rfkill, and PipeWire dual-authority idle detection
---

April 3–5 was a dense RC cycle that hardened the new SinkMonitor-based idle detection, fixed long-standing restart issues, resolved a mute synchronization bug from a community report, and added Raspberry Pi support — culminating in v2.54.0, v2.54.1, and v2.54.2 stable releases.

## What shipped

### v2.54.0 RC chain (April 3)

Six RC iterations shipped in a single day, each fixing an issue discovered during production testing:

**rc.1 — Restart fixes and TLS compatibility:**
- Restart banner stuck in "restarting…" state — the frontend `sawRuntimeRestart` flag was never set on successful `/api/restart` response; poll was hitting the 500 ms kill window; added 60 s safety timeout for auto-clear
- Restart fails under S6 overlay — when the bridge runs as UID 1000, it can't `os.kill(1, SIGTERM)` the root PID 1; now falls back to `os.kill(os.getpid())` so S6 supervise restarts the child
- Update check fails on OpenSSL 3.5 — post-quantum ML-KEM key exchange produces an oversized TLS Client Hello (1569 bytes) that middleboxes drop; GitHub API calls now pin `prime256v1` ECDH curve
- `handoff_mode` device option removed — unused since v2.53, cleaned from config schema, migration, orchestrator, status snapshot, and all tests

**rc.2 — Logs endpoint empty in Docker:**
`from sendspin_client import _ring_log_handler` created a second module instance because the main process runs as `__main__`, not `sendspin_client`. Fixed by reading via `sys.modules['__main__']`.

**rc.3 — Idle standby broken (pulsectl EnumValue):**
The SinkMonitor's sink state classification used `int(state)` and `== 2` for "suspended" — but pulsectl's `EnumValue` only supports string equality (`== 'suspended'`), not integer casting. Sink state was always "unknown", preventing the idle timer from ever starting. Fixed to use string equality with integer fallback.

**rc.4 — Docker update command:**
The update modal showed `docker pull` but that doesn't recreate the container. Fixed to `docker compose pull && docker compose up -d`.

**rc.5/rc.6 — Idle timer wake race:**
SinkMonitor fires `on_idle` while `bt_standby` is still `True` during the wake flow, so the timer never restarts. Fixed by re-checking sink state after clearing standby and re-arming the timer. Also fixed onboarding regression during standby — devices in idle-standby are now treated as "logically connected".

### v2.54.0 stable (April 4)

The stable release consolidated all SinkMonitor work from v2.53.0 through v2.54.0-rc.6. PR #131 was created by the Copilot coding agent, promoting the RC to stable with a consolidated changelog.

### Issue #132: mute desync after BT reconnect

User @mrtoy-me (one of the project's most active community members) reported a subtle mute state inconsistency: after Bluetooth reconnects, the bridge web UI showed mute Off and audio played normally, but Music Assistant still showed mute On. The cause:

1. During BT reconnect, the daemon temporarily mutes the PA sink
2. After reconnection, the daemon unmutes the sink (PA level)
3. But the `sink_muted → false` transition was never forwarded to MA

**Fix (v2.54.1):** The parent process now detects `sink_muted → false` transitions and forwards the unmute to MA via `players/cmd/volume_mute` when `MUTE_VIA_MA` is enabled.

Additionally, `MUTE_VIA_MA` was changed from `false` to `true` by default, since any bridge user with MA integration expects mute state to stay synchronized.

### v2.54.1 (April 4–5)

Four RC iterations, then stable:

- **Process hangs after restart** — `graceful_shutdown()` ran successfully but never called `loop.stop()`, leaving the process alive in a "shutdown complete" state while S6/Docker thought it was still healthy
- **Bluetooth soft-blocked on Raspberry Pi** — `entrypoint.sh` now runs `rfkill unblock bluetooth` at startup so the on-board BT adapter works without manual intervention
- **Mobile action buttons overflow** — when dark mode was applied via CSS class (not OS preference), the desktop 2-column grid leaked to mobile; moved missing breakpoint overrides
- **Update modal: copyable Docker image** — displayed as a separate code block instead of inline text
- **Built-in adapter docs** — new section in the Bluetooth Adapters guide documenting RPi 4/5's single-stream A2DP limitation

### v2.54.2: PipeWire dual-authority (April 5–6)

Issue #120 surfaced one more time: @mdorchain confirmed that on PipeWire 1.0.5, the SinkMonitor never received sink state events for BT sinks. PipeWire's PulseAudio compatibility layer simply doesn't emit them.

**Fix:** Daemon playback flags (`playing`, `audio_streaming`) now unconditionally participate in idle timer management alongside SinkMonitor callbacks, forming a dual-authority model. On PulseAudio, SinkMonitor is primary and daemon flags are a safety net. On PipeWire, daemon flags are the primary authority since SinkMonitor events never arrive.

### MA bluetooth_audio provider analysis

An `rnd/` research document analyzed Music Assistant PR #3585 (Local Audio Out provider) — a new `local_audio` player provider that registers local soundcards as Sendspin players directly inside the MA server process. The analysis explored feasibility of implementing a similar `bluetooth_audio` provider with remote bridge orchestration. Conclusion: the bridge's subprocess-isolation architecture and PA sink management are complementary to, not competitive with, this approach.

## Release timeline

| Version | Date | Key change |
|---------|------|------------|
| 2.54.0-rc.1 | Apr 3 | Restart fixes, TLS compat, remove handoff_mode |
| 2.54.0-rc.2 | Apr 3 | Logs endpoint empty in Docker |
| 2.54.0-rc.3 | Apr 3 | pulsectl EnumValue string equality |
| 2.54.0-rc.4 | Apr 3 | Docker update command |
| 2.54.0-rc.5/6 | Apr 4 | Idle timer wake race, onboarding standby |
| **2.54.0** | **Apr 4** | **SinkMonitor stable** |
| 2.54.1 | Apr 4 | Process hang, mute desync, RPi rfkill |
| 2.54.2 | Apr 5 | PipeWire dual-authority idle detection |

## Why this matters

This period demonstrated the classic "last mile" hardening pattern: the SinkMonitor architecture was sound, but edge cases in pulsectl's type system, PipeWire's event model, S6's process supervision, and OpenSSL's post-quantum handshake all needed individual fixes. Each RC was triggered by real-world testing on different environments (HAOS, Docker/PipeWire, Docker/RPi).

The mute desync fix (#132) is notable because it exposed an asymmetry in the MA↔bridge mute path: muting from MA propagated to the bridge, but unmuting from the bridge (especially the automatic unmute after BT reconnect) didn't propagate back to MA. This kind of bidirectional state sync issue is characteristic of multi-component audio systems.

The PipeWire dual-authority model is a pragmatic compromise: rather than trying to make PipeWire emit events it doesn't emit, the bridge maintains two independent signals and trusts whichever one is available.
