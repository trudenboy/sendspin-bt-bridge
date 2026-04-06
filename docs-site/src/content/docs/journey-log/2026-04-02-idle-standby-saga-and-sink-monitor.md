---
title: 2026-04-02 — The idle standby saga and SinkMonitor
description: A community-reported bug where speakers disconnected during active playback led to four iterative fixes, culminating in a new PulseAudio SinkMonitor architecture that replaced the fragile daemon-flag idle guard
---

Issue #120 — "Recurring speaker disconnection" — arrived on April 1 from a Docker Compose user on PipeWire. What looked like a simple timer bug turned into a four-day architectural rearchitecture of the idle detection system, progressing from quick patches (v2.52.2–2.52.4) to a complete replacement of the idle guard with PulseAudio sink state monitoring (v2.53.0).

## The bug

User @mdorchain reported that their Samsung Soundbar disconnected and went to standby every ~15 minutes even while music was actively playing. The logs told the story:

```
17:42:41 — Stream STARTED (audio actively playing)
17:57:42 — Idle for 15 min — entering standby
```

The idle timer fired exactly 15 minutes after it was last reset — completely ignoring that audio was streaming.

## The investigation

### Root cause 1: timer only checked `audio_streaming` (v2.52.2–2.52.3)

The idle timer only looked at the `audio_streaming` flag, which tracks raw PCM data flow. It didn't consider the Music Assistant `playing` transport state. When MA forces a daemon reconnect (which happens every ~55 minutes for server-initiated connections), both `audio_streaming` and `playing` flags briefly reset to False — just long enough for the timer to start counting down from zero.

**Fix (v2.52.3, PR #121):** Timer now reacts to `playing` transitions and re-checks both flags at firing time. This was the first PR created by the GitHub Copilot coding agent, assigned to @trudenboy for review.

### Root cause 2: flags reset on MA reconnect (v2.52.4)

The v2.52.3 fix wasn't sufficient. On every MA-forced reconnect, daemon flags reset simultaneously, creating a window where neither guard prevented the timer from firing. A two-tier fallback was added: MA WebSocket monitor group state (primary) and an event history ring buffer (fallback).

### Root cause 3: daemon flags are fundamentally unreliable (v2.53.0)

The deeper issue: daemon playback flags are a proxy for "is audio playing". They get reset on reconnects, they don't cover edge cases (pause vs stop), and they're coupled to the daemon subprocess lifecycle. The real authority should be the PulseAudio sink itself — if audio samples are flowing into the BT sink, the sink is in `running` state; if not, it's `idle` or `suspended`.

**The SinkMonitor architecture (v2.53.0-rc.1):**

```
PulseAudio/PipeWire
  └── pulsectl_asyncio subscription
        └── SinkMonitor
              ├── on_active(sink_name) → cancel idle timer
              └── on_idle(sink_name)  → start idle timer
```

`SinkMonitor` subscribes to PA sink events via `pulsectl_asyncio`, tracks state for all Bluetooth sinks, and fires callbacks on `running ↔ idle` transitions. Initial sink scan on PA connect/reconnect populates the state cache — preventing stale data after PA connection loss.

Key design decisions:
- **Per-client gate**: each `SendspinClient` registers its BT sink name; events for other sinks are ignored
- **Thread safety**: `_idle_timer_lock` protects the timer task from concurrent access by the asyncio loop and Flask/Waitress threads
- **Firing-time safety guard**: before entering standby, the timer re-checks `bt_standby`, `bt_waking`, `keepalive_enabled`, and cached PA sink state
- **Lifecycle management**: SinkMonitor is properly stopped on shutdown, startup failure, and signal handling

Dead code cleanup: `_ma_monitor_says_playing()` and `_event_history_says_playing()` were removed — defined and tested but never actually called from production code after the SinkMonitor became the sole authority.

### WebSocket heartbeat (v2.53.0-rc.2)

While investigating #120, a separate silent failure was discovered: the Sendspin server-initiated WebSocket connection had no heartbeat, so proxies and firewalls would silently drop idle connections after their own timeout (typically 60–120 seconds). The daemon now sends 30-second ping/pong frames, matching MA's client-side heartbeat pattern.

## Also fixed: non-ASCII MA auth (Issue #119, v2.52.2)

A Chinese user (@geniusliang) reported that MA Ingress sign-in crashed with `'latin-1' codec can't encode characters` when their MA username contained CJK characters. The fix was to percent-encode non-ASCII characters in the Ingress JSONRPC headers. Shipped same-day as v2.52.2.

## Release timeline

| Version | Date | Key change |
|---------|------|------------|
| 2.52.2 | Apr 1 | Non-ASCII MA auth fix (#119) |
| 2.52.3 | Apr 1 | Idle timer reacts to `playing` state (#120) |
| 2.52.4 | Apr 2 | Two-tier idle guard fallback (#120) |
| 2.52.5-rc.1 | Apr 2 | Solo player standby/wake fix |
| 2.53.0-rc.1 | Apr 2 | SinkMonitor replaces 3-tier guard (#120) |
| 2.53.0-rc.2 | Apr 3 | WebSocket heartbeat + dead code removal |

## Why this matters

This was the first real-world bug report that exposed a fundamental architectural weakness. The original idle detection relied on daemon flags that were designed for a different purpose (status display, not timer control). Every fix that patched the flags was a bandaid — the actual audio pipeline state was never consulted.

The SinkMonitor is a qualitative improvement: it queries the audio system directly rather than inferring state from subprocess flags. It's also the foundation for the power save idle mode that would ship in v2.55.0 — without PA sink state awareness, suspending sinks on idle would have been impossible.

The issue also demonstrated the value of the community reporting workflow. @mdorchain provided detailed logs, configuration, and diagnostics files that made reproduction straightforward. Four versions shipped in two days to resolve it.

## Follow-up

The SinkMonitor worked on PulseAudio but had a gap on PipeWire: PipeWire's PA compatibility layer doesn't emit sink state change events for BT sinks. This would surface again and be fixed with the dual-authority model in v2.54.2.
