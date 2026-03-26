---
title: "March 2–7: Architectural overhaul and v2 foundation"
description: "Four architecture iterations in two days, subprocess isolation, modularisation, MA integration, and project statistics"
---

## March 2–3, 2026 — Architectural overhaul: 4 iterations in 2 days (v2.0–v2.5)

This is the most technically intensive period. One problem was being solved — **deterministic audio routing in PulseAudio with multiple speakers** — and four fundamentally different architectural approaches were explored.

### Switching from the sendspin CLI to in-process aiosendspin (v2.0, March 2) — the root of the problem

Before v2.0, each BT speaker was managed by a separate **system process** running `sendspin`:

```
main process
    ├── subprocess: sendspin (PID A, env PULSE_SINK=bt_sink_A) → Speaker A
    └── subprocess: sendspin (PID B, env PULSE_SINK=bt_sink_B) → Speaker B
```

Each `sendspin` process had **its own PulseAudio context** and its own `PULSE_SINK` variable. Routing worked — but at the cost of fragility: playback status was parsed from stdout via regular expressions (~230 lines of parsing), and track metadata was polled through MPRIS with up to 10 seconds of lag.

In v2.0 (March 2), the `sendspin` CLI is replaced with a direct Python library call:

```python
# Before v2.0: subprocess + stdout parsing
process = subprocess.Popen(['sendspin', '--headless', ...])
# ~230 lines of stdout parsing via regex

# From v2.0: in-process BridgeDaemon
class BridgeDaemon(SendspinDaemon):  # from the aiosendspin package
    def on_stream_start(self, ...): ...  # typed callback
    def on_volume_change(self, ...): ...
```

`SendspinDaemon` is an asyncio class from the `sendspin` PyPI package (internally `aiosendspin`). All events come through typed callbacks, no parsing required. ~230 lines of fragile code removed; track metadata now arrives instantly.

**However:** now all `BridgeDaemon` instances live **in a single Python process** with a single PulseAudio context. `PULSE_SINK` is a process environment variable: setting different values for different daemons inside the same process is impossible.

```
main process (single PA context)
    ├── BridgeDaemon A → PA stream → default sink → Speaker ???
    └── BridgeDaemon B → PA stream → default sink → Speaker ???
```

PA picks the **default sink** — typically the most recently connected BT speaker. There is no guarantee: a stream could end up in either speaker. This became the root of all subsequent problems.

### Iteration 1: reactive `move-sink-input` (v2.1, March 3)

```
sendspin process
    └─► PA stream ──(move-sink-input on stream event)──► correct BT sink
```

`BridgeDaemon` subscribes to PA stream events. As soon as a new sink-input appears it is moved via `pactl move-sink-input` to the correct sink.

**Problem:** race condition. Between the stream appearing and being moved, 0.5–2 seconds of audio could play through the wrong speaker. Unstable under rapid track changes.

### Iteration 2: null-sink + loopback (v2.2, March 3)

```
sendspin ──► PA null-sink (virtual) ──(loopback module)──► real BT sink
```

A virtual sink is created via `module-null-sink`, and `module-loopback` connects it to the real BT sink. `sendspin` is directed to the virtual sink — always stable.

**Problem:** `module-loopback` adds extra buffer latency. Synchronisation in a multiroom group breaks. Additionally fragile: PA could drop the module on BT reconnect.

### Iteration 3: proactive `PULSE_SINK` (v2.4, March 3)

Key insight: instead of reacting to a misrouted stream, set the direction **before** it is created.

```bash
PULSE_SINK=bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink sendspin ...
```

The `PULSE_SINK` environment variable tells the PA client to use a specific sink when creating any stream. No reactivity, no race conditions.

**Problem:** still a single process. With multiple `sendspin` subprocesses the environment variable was not inherited as expected.

### Iteration 4: subprocess isolation (v2.5, March 3) — the final solution

```
main process
    ├── subprocess (env: PULSE_SINK=bluez_sink.AA...) → daemon_process.py
    │       └── BridgeDaemon → sendspin CLI → PA stream → Speaker A
    ├── subprocess (env: PULSE_SINK=bluez_sink.BB...) → daemon_process.py
    │       └── BridgeDaemon → sendspin CLI → PA stream → Speaker B
    └── ...
```

Each speaker gets **its own Python process** with `PULSE_SINK` in `os.environ`. Each process creates an independent PA context. Streams are physically isolated — it is impossible for audio to go to the wrong place.

IPC: subprocess → main via JSON lines on stdout; main → subprocess via JSON on stdin (`set_volume`, `stop`).

**Follow-up work at the same stage:**

- **v2.5.1**: PA `module-rescue-streams` — on BT device reconnect, PA moves orphaned streams to a fallback sink. A correction was added: detect the move and return the stream via `pactl move-sink-input` by PID.
- **v2.5.2**: protection against a feedback loop — the corrective `move-sink-input` itself generates a stream event which triggers the correction again. A `_sink_routed` flag was added to block re-entry.

---

## March 4, 2026 — Modularisation and UI (v2.5.5–2.6.10, ~77 commits)

After solving the routing problem — a period of polish and expansion.

### Code split into modules

| Module | Contents |
|--------|----------|
| `services/daemon_process.py` | Subprocess entry point |
| `services/bridge_daemon.py` | `BridgeDaemon` — Sendspin + PA events |
| `services/pulse.py` | Async PulseAudio helpers |
| `services/bluetooth.py` | BT utilities |
| `services/ma_monitor.py` | MA WebSocket monitor |
| `services/ma_client.py` | MA REST API client |
| `routes/api.py` | REST API Flask blueprint |
| `routes/views.py` | HTML pages |
| `routes/auth.py` | Authentication |
| `state.py` | Shared runtime state, SSE |
| `config.py` | Configuration, `VERSION` |

### User interface

- **Preferred audio format per device** (v2.5.5): `preferred_format` field in the device config. MA can attempt resampling during multiroom sync — pinning the format eliminates resampling.
- **Track progress bar** (v2.6.6): progress bar with client-side interpolation (JS). Track position from MPRIS metadata.
- **Sync status**: re-anchor event counter, warning on frequent switches.
- **Sink name** in the Volume column on hover — for diagnostics without `/api/diagnostics`.

### Security and performance

- **v2.6.0–2.6.1**: security audit — input validation, protection against path traversal in config, correct Flask session invalidation.
- `pause_all` sends the command once per MA group, not per client.

### Scaling and multi-bridge support (v2.7.x)

By this point two requirements had accumulated that matter for non-trivial deployments:

**1. Support for 100+ speakers in a single bridge**

With a large number of devices the single-process model began running into concurrency issues. The targeted refactor (v2.7.x) included several changes:

- **SSE batching**: `notify_status_changed()` accumulates updates in a 100 ms window before pushing to clients. On a mass reconnect (e.g. all 50 speakers coming back after the night) without batching a storm of 50 SSE events fires in rapid succession — browsers couldn't keep up. Batching reduces the event count roughly tenfold.
- **ThreadPoolExecutor** with an explicit pool size: `min(64, N_devices×2+4)` workers. With 100+ devices Python's default pool (`os.cpu_count()*5`) could queue BT operations — reconnecting one device blocked others.
- **D-Bus MessageBus reuse**: previously each iteration of the outer reconnect loop created and destroyed a new bus object. With 100 devices that is 100 parallel bus connections to the D-Bus daemon — excessive. The connection is now reused; a new one is created only when the bus stops responding.
- **Keepalive jitter**: at startup all devices could simultaneously run `paplay silence.wav` — CPU spike. A random start offset in the range 0..interval seconds was added.
- **`_status_monitor_loop` sleep** increased from 2 s to 5 s: with 100 devices, 50 asyncio wake-ups per second (2 s × 100 / … = load) with no real benefit — D-Bus signals catch a disconnect instantly.
- **`WEB_THREADS`**: configurable number of Waitress workers (default 8, recommended 16 with 20+ devices). Each browser holds an SSE connection on its own worker — with multiple tabs the pool runs out.

**2. Multiple bridges against a single MA server**

Scenario: a large home, several Bluetooth coverage zones. A single bridge physically can't reach all speakers. The solution — multiple bridge instances (Docker containers, LXC containers, HA addons) against one MA server.

Problem: if two bridges register a player with the same name → MA treats them as the same player and resets the queue.

Solution (v1.3.0, designed in advance): **UUID5 from the MAC address** as `player_id` in Sendspin:

```python
player_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, mac_address))
```

The UUID is deterministic (identical on every restart), globally unique (the MAC is unique), and independent of the name. Two bridges with different MACs → two different `player_id` values → MA sees two independent players even with identical names.

The default bridge name — `Sendspin-<hostname>` — is also made unique so it's immediately clear in MA which machine the player came from.

---

## March 5, 2026 — Groups and MA integration (v2.7–v2.10, ~141 commits)

### Keepalive silence (v2.7.x)

BT speakers automatically disconnect after ~30 seconds of silence. In a multiroom scenario this is critical: if there is a gap between queue tracks, the speaker goes to sleep and the next track starts with a reconnect delay of 2–5 seconds — the group goes out of sync.

Solution: generate a silent PCM signal (`silence_stream`) via PulseAudio while the speaker is considered "active". This keeps the A2DP connection alive without real audio.

### Group pause via MPRIS (v2.7.x)

The first pause implementation sent a stop command directly to each device's subprocess. Problem: MA was unaware of the pause — player status in MA remained "Playing" and the syncgroup didn't synchronise.

The correct solution: pause via the MPRIS D-Bus interface (`org.mpris.MediaPlayer2.Player.Pause`). MA is the initiator through MPRIS → MA correctly updates the status of the entire group.

The pause button for a device that is a group member (2+ participants) controls **the entire group**, not just the one player — so it's impossible to accidentally desync the group from the web interface.

### Group API (v2.8.0)

REST API for group management: `POST /api/group/pause`, `POST /api/group/play`, `POST /api/group/volume`. Group controls in the web UI — set volume and mute on all group members simultaneously.

### Native MA REST API integration (v2.9.0)

Before this version, the bridge "didn't know" about MA groups — it only saw its own players. If MA merged them into a syncgroup, resuming playback after a pause could cause desync because the bridge tried to resume each player independently.

From v2.9.0 the bridge connects to the MA REST API:
- Finds the syncgroup containing its players via fuzzy name matching.
- On playback resume calls `POST /api/players/player_queues/{group_id}/play` — MA resumes the group as a whole.
- `MA_API_URL` and `MA_API_TOKEN` are new configuration fields.

A series of fixes in v2.9.1–2.9.4: API settings were not persisted across addon restarts (`translate_ha_config.py` didn't carry over the keys), the URL wasn't normalised, and configuration wasn't included in `allowed_keys`.

### Persistent MA WebSocket monitor (v2.9.5)

The most significant feature addition since subprocess isolation.

`services/ma_monitor.py` establishes a **persistent WebSocket connection** to MA (`/api/ws`) and subscribes to `player_queue_updated` events. When a player's queue changes, the bridge receives the update immediately.

What this enables:
- **Now-playing** in the web interface: track, artist, album, album art, queue position.
- **Transport controls**: prev/next/shuffle/repeat buttons on the device card — via MA REST API.
- **Album art**: tooltip on hovering over the track name.
- **Progress bar**: synchronised with the position from MA.
- **Metadata auto-refresh**: when the monitor connects, current data for all active players is fetched.

### Multi-group support (v2.9.9–2.10.x)

In the initial MA integration implementation the now-playing cache was global — a single object for the whole bridge. If the bridge managed two MA syncgroups (e.g. "Living Room + Kitchen" and "Bedroom"), data from the second group would overwrite the first.

Refactored to a per-group cache: `dict[group_id, NowPlayingData]`. Each device card shows its own group's metadata.

Solo players (not part of any syncgroup) get their own queue_id in the format `up<uuid_without_hyphens>`.

---

## Architecture transformation timeline

| Version | Date | Architectural decision | Problem it solved |
|---------|------|----------------------|-------------------|
| v0 (origin) | Jan 1 | Single process, single BT, polling | — |
| v1.3.0 | Mar 1 | UUID player_id from MAC | Multiple bridges against one MA + stable ID across restarts |
| v1.3.16 | Mar 1 | MPRIS D-Bus MediaPlayer2 | No standard interface between MA ↔ bridge |
| v1.4.0 | Mar 2 | Monolith split into modules | Unmanageable growth of a single file |
| v1.7.0 | Mar 2 | D-Bus event BT monitor | 10-second delay in disconnect detection |
| **v2.0** | Mar 2 | **sendspin CLI → in-process aiosendspin** | Fragile stdout parsing, metadata lag — **introduced the default sink problem** |
| v2.1 | Mar 3 | Reactive `move-sink-input` | Audio went to default sink (wrong speaker) |
| v2.2 | Mar 3 | null-sink + loopback | Race condition on `move-sink-input` |
| v2.4 | Mar 3 | Proactive `PULSE_SINK` env | Loopback latency broke synchronisation |
| **v2.5** | Mar 3 | **Subprocess isolation per speaker** | **PULSE_SINK not applicable inside a single process** |
| v2.5.1 | Mar 3 | PA rescue-streams correction | BT reconnect moved streams to fallback |
| v2.5.5 | Mar 4 | `preferred_format` per device | Resampling in multiroom groups |
| v2.6.0 | Mar 4 | routes/, services/, state.py | Monolithic web_interface.py |
| v2.7.x | Mar 5 | Keepalive silence stream | BT disconnects during silence between tracks |
| v2.7.x | Mar 5 | Group pause via MPRIS | MA unaware of pause, group not synced |
| v2.8.0 | Mar 5 | Group REST API | No API for group management |
| v2.9.0 | Mar 5 | MA REST API integration | Group resume without MA as initiator |
| v2.9.5 | Mar 5 | Persistent MA WebSocket monitor | No real-time playback data |
| v2.9.9 | Mar 5 | Per-group MA now-playing cache | Single global cache broke multiple syncgroups |

---

## Overview

| Period | Commits | Main focus |
|--------|---------|------------|
| January 1 | 14 | loryanstrant's service created and published (+1100 AEDT) |
| Feb 27–28 | ~80 | First personal commits (+0300 MSK): Proxmox LXC, multi-device, HA addon |
| March 1 | ~49 | MA identification, MPRIS, HA Ingress, authentication, detach from upstream |
| March 2 | ~55 | Modularisation, D-Bus BT monitor, first audio routing attempts |
| March 3 | ~91 | 4 audio routing iterations → subprocess isolation |
| March 4 | ~77 | Polish, preferred_format, UI, security |
| March 5 | ~141 | Keepalive, groups, MA API, real-time monitor |
| March 6 | ~41 | MA multi-syncgroup, solo players, documentation |

Over 7 days of active development the project went from a single-file script for one speaker to a production-ready solution with per-subprocess audio isolation, native integration into the Music Assistant and Home Assistant ecosystems, and multiroom support with MA syncgroup synchronisation.

---

## Project statistics

### Git and releases

| Metric | Value |
|--------|-------|
| Total commits | ~466 |
| Author (Mikhail Nevskiy) | ~414 commits |
| Loryan Strant (foundation) | 14 commits |
| GitHub Actions (CI/CD) | 38 commits |
| Active development days | 9 (Feb 27 – Mar 6, 2026) |
| Versions released | ~135 (v1.0.0 → v2.13.1) |
| Pull Requests | 54 |
| Busiest day | March 5: 119 commits |

### Codebase

| Metric | Value |
|--------|-------|
| Python files | 44 |
| Lines of Python code | ~12 700 |
| Most-modified file | `sendspin_client.py` (108 revisions) |
| Next most modified | `config.py` (102), `web_interface.py` (100) |
| Python dependencies | 11 |

### Python dependencies

| Package | Purpose |
|---------|---------|
| `sendspin` / `aiosendspin` | Sendspin protocol, `BridgeDaemon`, `SendspinDaemon` |
| `music-assistant-client` | MA REST API and WebSocket |
| `flask` + `waitress` | Web interface and HTTP server |
| `pulsectl-asyncio` | PulseAudio management from asyncio |
| `dbus-fast` + `dbus-python` | D-Bus: BT monitoring and MPRIS |
| `websockets` | Communication with MA WebSocket |
| `psutil` | System information |
| `python-dotenv` | Environment variables |
