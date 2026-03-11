# Project History

A history of the architectural and functional evolution of sendspin-bt-bridge — for readers familiar with Home Assistant, Music Assistant, and multiroom audio setups.

**Period:** January 1 – March 11, 2026 · **Total commits:** ~650 · **Versions:** 1.0.0 → 2.20.5

---

## Background: where the project came from

### loryanstrant/Sendspin-client — service created and published (January 1, 2026)

**January 1, 2026** Loryan Strant (Australia, AEDT +1100) created and published [SendspinClient](https://github.com/loryanstrant/Sendspin-client) — a Docker container bridging Music Assistant (via the Sendspin protocol) to a Bluetooth speaker on a Linux machine.

The motivation was entirely personal: an infrared sauna with Bluetooth speakers and a Surface Pro 4 running Ubuntu nearby — a familiar home-automation scenario. Loryan tried Squeezelite, but ESP32 + WiFi + A2DP gave an unstable connection. The idea was elegant: since MA can stream via Sendspin (WebSocket + FLAC/RAW) and the `sendspin` binary plays back to any PulseAudio device, all that's needed is a bridge in a container.

The original source was a single Python script, `sendspin` as a child process, `bluetoothctl` for connecting to the speaker, and a simple HTML status page. Loryan published the project in the MA Community thread [#4677](https://github.com/orgs/music-assistant/discussions/4677) and merged 4 PRs in a single day — improving BT disconnect detection and expanding the README.

### My involvement and the fork

I found thread #4677. My use case was similar — connecting Bluetooth speakers through Proxmox LXC, which loryanstrant's Docker approach didn't support: LXC containers have no access to AF_BLUETOOTH sockets due to kernel namespace restrictions.

**February 27, 2026** I left my first comment in the discussion describing the solution and submitted [PR #6](https://github.com/loryanstrant/Sendspin-client/pull/6) to the original repository: a new `lxc/` directory with `proxmox-create.sh` (runs on the PVE host — creates the LXC, bind-mounts the D-Bus socket, configures Bluetooth passthrough) and `install.sh` for setup inside the container. Tested on PVE 8.4.16 with a Sony WH-1000XM4.

**February 28, 2026** I published an extended fork, `sendspin-bt-bridge`, with fundamental new capabilities and asked Loryan in the same thread whether he minded me developing the project independently and publishing it as a standalone HA addon, with the commitment that he would always be credited as the founding author. New in the first fork release:

- **Multi-device**: multiple Bluetooth speakers simultaneously, each as a separate player in MA
- **Home Assistant addon** with Ingress (web UI in the HA sidebar without port forwarding)
- **`static_delay_ms`** — per-device A2DP latency compensation
- **`/api/diagnostics`** — structured healthcheck for adapters, sinks, and D-Bus
- **Audio format** in status (codec, sample rate, bit depth — e.g. `flac 48000Hz/24-bit/2ch`)
- **Volume persistence** per MAC in `LAST_VOLUMES` with automatic restore on reconnect

The explicit break from upstream was recorded in a commit dated March 1, 2026:
```
chore: detach from loryanstrant/Sendspin-client upstream
```

From that point the project develops entirely independently. The commit history from January 1 is inherited — loryanstrant's 14 commits remain part of the repository's git history.

---

## January 2026 — Architecture v0: one file, one speaker

**Code state:** a single `sendspin_client.py` file ≈ 400 lines.

The scheme is as simple as it gets:

```
MA Server ──(WebSocket/Sendspin)──► sendspin CLI ──(PulseAudio)──► bluetoothctl ──► BT Speaker
```

The Bluetooth manager polls the connection once every 10 seconds via `bluetoothctl info <MAC>`. A disconnect is detected with up to 10 s of lag. The web interface shows minimal status; nothing is configurable.

The first PRs from the parent repository add real D-Bus monitoring to replace the timer-based ping — BT status updates instantly on a system event.

**The key limitation of this phase:** no support for multiple speakers. In PulseAudio there is a single `PULSE_SINK` — wherever `sendspin` sends its audio is where it goes. Two speakers = ambiguity.

---

## February 27 – March 1, 2026 — Feature explosion (v1.0–1.7, ~130 commits in 3 days)

The most rapid period of development. 73 commits on February 28 alone.

### Multi-device support and the HA addon (February 28)

**Repository renamed** from `sendspin-client` to `sendspin-bt-bridge` — the name reflects the new role: not a client, but a bridge.

Key additions in a single day:

- **Multi-device**: each entry in `BLUETOOTH_DEVICES` in the config launches its own `BluetoothManager` + `SendspinClient` pair. Multiple independent players appear in MA.
- **Home Assistant addon** (`ha-addon/`): manifest, Dockerfile, `run.sh`. The bridge integrates into the HA Ingress panel; the theme is injected via the postMessage API.
- **Proxmox LXC**: `proxmox-create.sh` deploys a native container in a single command. Inside — its own `bluetoothd` via D-Bus bridge, `pulseaudio --system`, `avahi-daemon`.
- **Full-featured web interface**: device cards, BT scanning, volume control, reconnect/re-pair buttons, diagnostics.
- **BT adapter management**: auto-detection, manual selection, binding a speaker to a specific `hci`.

### Player identification in MA (March 1, v1.3.x)

The primary goal was supporting multiple bridge instances connected to a single MA server. When two bridges register a player with the same name — for example `"Living Room"` — MA cannot tell them apart by name: when the second one appeared it would reset the queue of the first or confuse them. The `player_id` must be globally unique and stable regardless of the player name.

Solution: **UUID5 from the MAC address** (`v1.3.0`). The UUID is deterministic (identical on every restart), globally unique (the MAC is physically unique), and independent of the player name. Two bridges with different speakers → two different `player_id` values → MA sees them as completely independent players, even if their names are identical.

This also solved a secondary but equally noticeable problem: previously MA would lose the player on bridge restart or rename — queues and groups would reset. After v1.3.0 the `player_id` never changes.

In parallel — **MPRIS D-Bus integration** (v1.3.16): the bridge registers itself as a MediaPlayer2 object on the session bus. MA can read playback status and control the player via the standard interface. When the service stops, an MPRIS `Pause` is sent first — MA correctly stops the group before the player disappears from the network.

**Player identification in MA groups** (v1.3.19): the problem is that MA builds syncgroups by player name. Logic was added to ensure `BRIDGE_NAME` + suffix + MPRIS Identity match — so the player name in MA matches the MPRIS object name; otherwise the group doesn't form.

### Redesigned UI in HA/MA style with theme support (March 1, v1.3.7)

Before this version, the web interface looked like a generic dashboard: purple gradient header (`#667eea`), hard-coded HSL colours, system font. When opened through HA Ingress it was visually out of place in the ecosystem.

In v1.3.7 the UI is fully rewritten to match the visual language of Home Assistant and Music Assistant:

**CSS custom properties instead of hard-coded values**

```css
/* before */
background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
color: #28a745;

/* after */
background: var(--app-header-background-color, #03a9f4);
color: var(--success-color, #4CAF50);
```

All colours go through HA design tokens (`--primary-color`, `--error-color`, `--success-color`, `--warning-color`, `--ha-card-border-radius`, `--ha-card-box-shadow`). The header is styled as an HA `app-toolbar`. Font: Roboto (the same one HA uses).

**Dual theming: media query + Ingress postMessage**

```css
/* static theme — works everywhere */
@media (prefers-color-scheme: dark) {
  :root { --primary-background-color: #111; ... }
}
```

```javascript
// live theme injection from HA — Ingress only
window.addEventListener('message', (e) => {
  if (e.data?.type === 'setTheme') applyTheme(e.data.theme);
});
```

When the user opens the UI through the HA sidebar, HA sends a `postMessage` with the current theme. Switching the theme in HA → instantly reflected in the web UI without a page reload. If the UI is opened directly (not through Ingress) — the theme is determined by the system `prefers-color-scheme`.

**Result:** from v1.3.7 the web interface is visually indistinguishable from native HA panels. Users who add the bridge to the HA sidebar see a consistent design.

Subsequent UI iterations (v2.6.5, v2.6.6, v2.7.x) continued polishing: track progress bar, transport controls, album art, hover actions, animated BT scan, mobile adaptation, UX audit with 20 improvements (v2.10.x).

### Security and reliability (March 1–2, v1.4–1.7)

- **Modularisation** (v1.4.0): monolithic `sendspin_client.py` split into `config.py`, `mpris.py`, `bluetooth_manager.py`.
- **Documentation site** (v1.4.2): Astro Starlight, bilingual (EN/RU), deployed to GitHub Pages.
- **Web interface authentication** (v1.6.0): PBKDF2-SHA256 for standalone mode; in the HA addon — proxied through HA Core login_flow with 2FA/TOTP support.
- **D-Bus BT monitor** (v1.7.0): switched from polling to event-driven Bluetooth monitoring — the bridge learns of a disconnect at the moment of the event, not after 10 seconds.
- **Configurable BT check interval** and auto-disable after N failed reconnect attempts.

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
| `flask-cors` | CORS for the REST API |

## March 7, 2026 — Reliability & deployment (v2.10.7 → v2.12.0)

### Volume architecture overhaul (v2.10.7 – v2.10.13)

The hybrid volume path — routing volume commands through the MA WebSocket API to keep MA's UI in sync — introduced a triple feedback loop: the API, the sendspin protocol echo, and the MA monitor event all tried to set the PulseAudio sink volume simultaneously. The result was volume bouncing (set 40 → jumps to 47 → settles at 55) and unexpected jumps on track change.

The fix was architectural: **bridge_daemon became the single writer** to PulseAudio sink volume. The API no longer optimistically updates local status on the MA path — it waits for the actual echo from MA via sendspin protocol. The `_handle_player_updated` volume sync in the MA monitor was removed as a redundant third path. A new `VOLUME_VIA_MA` config option (default: `true`) allows disabling the MA proxy entirely, forcing all volume/mute changes through direct pactl.

### Observability and test infrastructure (v2.10.8)

All 27 silent `except: pass` blocks were replaced with DEBUG-level logging — issues are now visible with `LOG_LEVEL=DEBUG` without changing runtime behavior. Thread safety was hardened: `run_coroutine_threadsafe` calls got 5-second timeouts, and fire-and-forget asyncio tasks got `done_callback` for exception logging. The project gained its first automated tests: pytest with 9 unit tests covering config loading, volume persistence, MAC-to-player-ID mapping, and password hashing (later expanded to 15 tests).

### LXC installer modernization (v2.10.16)

The LXC installer was updated to download all app modules (config, state, routes, services, templates, static) instead of the original 2 files. PulseAudio configuration was fixed for PA 17+ on Ubuntu 24.04: deprecated `enable-lfe-remixing` replaced with `remixing-produce-lfe`/`remixing-consume-lfe`, the systemd unit no longer sets `User=pulse`/`Group=pulse` (PA `--system` mode requires root), and a tmpfiles.d entry ensures `/var/run/pulse` survives reboots.

### OpenWrt LXC deployment (v2.11.0)

A new `lxc/install-openwrt.sh` installer added support for OpenWrt-based routers (Turris Omnia, etc.) with procd service management — expanding deployment from 3 methods (Docker, HA addon, Proxmox LXC) to 4.

### Zombie playback watchdog and churn isolation (v2.12.0)

Two reliability features were added:

- **Zombie playback watchdog**: auto-restarts the subprocess after 15 seconds of `playing=True` with no audio data (`streaming=False`), up to 3 retries. This catches situations where the sendspin connection is alive but the audio pipeline is broken.
- **BT churn isolation** (opt-in): auto-disables BT management for devices that reconnect too often within a sliding window, configurable via `BT_CHURN_THRESHOLD` (0 = disabled, default) and `BT_CHURN_WINDOW` (default 300 s). Prevents a flaky Bluetooth device from consuming adapter time and destabilizing other speakers.

A new **stale equalizer indicator** shows frozen red bars when MA reports playing but no audio is streaming, with playback text showing "▶ No Audio".

## March 8, 2026 — Multi-bridge & community (v2.12.1 → v2.13.1)

### Caching, SSE reliability, and HA Ingress fixes (v2.12.1 → v2.12.6)

A series of quick-fire releases addressed progressive discovery of HA Ingress proxy behavior: static asset cache-busting via query string (`?v=`) was ineffective because Ingress strips query parameters — switched to path-based versioning (`/static/v2.12.5/app.js`). HTML responses gained `Cache-Control: no-cache` headers. The SSE stream got 2 KB initial padding to flush proxy buffers, and the client-side SSE reconnect logic was upgraded from "fail once → poll forever" to exponential backoff with 5 retries.

### Lazy player registration (v2.12.2)

The sendspin daemon now starts only after Bluetooth actually connects, eliminating phantom players in Music Assistant at container startup.

### Multi-bridge architecture analysis and improvements (v2.13.0)

A deep analysis of the multi-bridge scenario (multiple bridges → one MA instance, cross-bridge sync groups) identified 6 potential problems and led to two key improvements:

- **Auto-populated BRIDGE_NAME**: on first startup, the machine hostname is written to `config.json["BRIDGE_NAME"]` so users see a pre-filled value in the Web UI before adding devices. The old `BRIDGE_NAME_SUFFIX` boolean was removed — no longer needed when the name is auto-populated. This prevents duplicate player names (e.g. two "JBL Flip 6" from different hosts) which confused MA's player list.

- **Cross-bridge sync group visibility**: when players from multiple bridges belong to the same MA sync group, the group badge now shows `🔗 Kitchen Music +2` (where +2 = players from other bridges). Hovering the badge reveals the full member list with ✓ for local and 🌐 for external players. Data comes from the MA API cache (`/api/players` → sync group member lists) that the bridge already maintains.

### Production deployment fixes (v2.13.1)

Deploying v2.13.0 to two live LXC bridges (Proxmox + Turris OpenWrt) uncovered a chain of issues:

- **Waitress 3.x broke SSE**: upgrading `waitress` pulled in v3.x which strictly enforces PEP 3333 and rejects hop-by-hop headers. The `Connection: keep-alive` in the SSE response caused an `AssertionError` crash — removed the header entirely.
- **JS variable name mismatch**: both polling and SSE handlers in `app.js` referenced `data.groups` but the parsed variable is named `status` — devices never rendered. Fixed to `status.groups`.
- **Group enrichment ID mismatch**: `_build_groups_summary()` compared Sendspin's `group_id` (UUID) against MA's syncgroup ID (`syncgroup_XXX`) — different ID systems that never matched. Fixed by resolving MA syncgroup via player-name mapping.
- **Groups missing in polling response**: `/api/status` for single-device bridges omitted the `groups` field (only SSE included it), so the badge never appeared via polling.
- **LXC bluetooth.service incident**: accidentally restarting `bluetooth.service` inside the Turris container (where bluetoothd cannot run) broke PulseAudio's A2DP state, requiring device re-pair from host. Hardened: `bluetooth.service` is now **masked** (not just disabled), and `sendspin-client.service` gained `TimeoutStopSec=15` to prevent hung shutdowns.

### GitHub Issues & Discussions infrastructure (v2.13.0)

The project gained structured issue management: 3 YAML-based issue form templates (Bug Report with deployment/audio dropdowns, Bluetooth/Audio specialist form, Feature Request), 16 project labels (`type:bug`, `area:bluetooth`, `deploy:ha-addon`, etc.), and a Discussions Welcome post with routing guidance (Issues for bugs/features, Discussions for help/ideas).

### Comprehensive security hardening & code quality audit (v2.16.0)

A full-codebase code review surfaced 42 issues across security, thread safety, error handling, robustness, and test coverage. All were resolved in a single coordinated release:

**Security (5 fixes):** SSRF via `flow_id` path traversal in HA auth flow; SSE endpoint could exhaust all Waitress threads (capped at 4); unclamped volume from server could overdrive speakers at 200%+; MAC address injection into `bluetoothctl` stdin; `/api/status` leaked MACs, IPs, and player metadata without auth.

**Thread safety (6 fixes):** `_clients` list iterated without lock across ~15 API endpoints; `stop_sendspin()` bypassed SSE notification; zombie restart counter race condition; config file reads without `config_lock`; unsynchronized MA API credential writes; BT executor pool too small (2→4) for multi-device reconnect.

**Error handling & input validation (7 fixes):** `request.get_json()` crash on non-JSON POST; internal exception strings leaked in 15 error responses; IPC volume command crash on non-numeric input; path traversal via crafted `client_id`; `player_names` type confusion (string vs list); `set_log_level` accepted arbitrary `getattr` targets; `force=True` weakened CSRF protection on password endpoint.

**Test coverage (65 new tests):** from 42 to 107 tests. New test files for `services/bluetooth.py`, `services/pulse.py`, `bluetooth_manager.py`, `services/daemon_process.py`, `scripts/translate_ha_config.py`, and `routes/api.py`. Shared `conftest.py` added. `datetime.UTC` replaced with `timezone.utc` across 4 files for Python 3.9 test compatibility.

**armv7l compatibility (post-release hotfix):** PyAV 12.3.0 (the only version that compiles on armv7l) lacks `AudioLayout.nb_channels`, causing the sendspin FLAC decoder to crash with `AttributeError` — total audio silence. A monkey-patch in `services/daemon_process.py` replaces `FlacDecoder._append_frame_to_pcm` with a version using `len(frame.layout.channels)`. The patch auto-detects PyAV version at startup and is a no-op on PyAV 13+.

**Raspberry Pi & Docker UX (v2.16.2):** After the first community user tried Docker on a Raspberry Pi and hit configuration issues, we added: a pre-flight diagnostic script (`scripts/rpi-check.sh`) that checks Docker, Bluetooth, audio, UID, and architecture before `docker compose up`; an auth-free `/api/preflight` endpoint for programmatic setup verification; a structured startup diagnostics table in `entrypoint.sh` (visible in `docker logs`); a dedicated Raspberry Pi installation guide (en/ru); and fixed stale Docker docs that still listed removed `SYS_ADMIN` capability and were missing `PULSE_SERVER`/`XDG_RUNTIME_DIR` env vars.

---

## March 10, 2026 — HA OAuth & MA API authentication (v2.17.0–v2.20.0, ~45 commits)

### HA OAuth popup flow for MA addon (v2.17.3)

In addon mode, MA is on a private Docker network — unreachable from the user's browser. The bridge added an HA OAuth popup flow: the web UI opens a popup to the HA OAuth authorize endpoint, HA authenticates the user (including 2FA/TOTP), and the bridge exchanges the resulting code for an MA session token via server-side HTTP calls through HA Ingress. This eliminates the need for users to manually configure `MA_API_TOKEN`.

### Silent MA auth via Ingress (v2.17.4)

The popup flow required user interaction. In Ingress mode the HA session token is already available in `localStorage` (`hassTokens`). The bridge now reads it automatically on page load, calls `/api/ma/ha-silent-auth` which performs the full OAuth exchange server-side — zero clicks. Auto-discover also runs on page load, so the MA connection is established transparently.

### Long-lived MA API token (v2.17.7)

Investigation of persistent "authentication failed" errors in MA monitor revealed a fundamental issue: the OAuth callback returns a short-lived session JWT (30-day sliding expiry, `is_long_lived=False`), not an API token. Additionally, a regex bug captured `#/` (Vue Router hash fragment) as part of the JWT, corrupting it.

The fix: after obtaining the session JWT via OAuth, the bridge connects to MA's WebSocket API, authenticates with the session token, and calls `auth/token/create` to obtain a proper long-lived JWT (10-year expiry). The session token is never persisted.

Idempotency: before initiating OAuth, `_validate_ma_token()` checks if the existing token is still valid for the target MA URL — preventing duplicate long-lived tokens on page reload or addon restart.

### MA server discovery from sendspin connection (v2.17.9)

In addon mode with `SENDSPIN_SERVER=auto`, the MA server discovery relied on mDNS as a last resort — but a zeroconf API change (kwargs vs positional args) broke the callback. The fix: before falling back to mDNS, the bridge now extracts the MA server host from the resolved sendspin WebSocket connection (`connected_server_url`). Since sendspin already discovered the MA server via its own mDNS, the bridge reuses that resolved address for the MA API endpoint (same host, port 8095). This eliminates the need for a separate mDNS scan in most cases.

### Simplified addon discovery and semi-auto auth (v2.17.10)

The previous approach had a fundamental problem: addon mode detection depended on the MA server's `homeassistant_addon` field from its `/info` endpoint — but when discovery used the mDNS path (via `_enrich_with_server_info` instead of `validate_ma_url`), this field was missing, so addon mode was never detected and silent auth never triggered.

The fix simplified the entire flow. The bridge now reports its own `is_addon` flag (from `_detect_runtime()`) in the discover response — no dependency on MA server metadata. In addon mode, discovery tries `http://homeassistant.local:8095` first (Supervisor internal DNS — nearly instant), skipping SENDSPIN_SERVER heuristics and mDNS entirely. The fully-automatic silent auth on page load was replaced with a semi-automatic approach: the "Sign in with Home Assistant" button is shown after discover detects addon mode, and the user clicks it explicitly. In Ingress mode this performs one-click silent auth (no popup); outside Ingress it opens the OAuth popup.

### Passwordless MA auth via Ingress JSONRPC (v2.18.0)

The silent auth in v2.17.4–v2.17.12 attempted to POST to HA's `/auth/authorize` with a Bearer token to obtain an OAuth code — but HA's authorize endpoint is GET-only (it serves an HTML consent page) and returns HTTP 405. The popup fallback worked but required entering credentials.

The v2.18.0 approach bypasses HA OAuth entirely. MA's Ingress server (port 8094) auto-authenticates requests via `X-Remote-User-ID` / `X-Remote-User-Name` headers — the same mechanism HA uses internally for Ingress traffic. Since both addons use `host_network: true`, the bridge can reach MA's Ingress port at `localhost:8094`. The flow: (1) frontend sends the HA access token from `hassTokens` in localStorage; (2) backend connects to HA's WebSocket API and calls `auth/current_user` to get the user's ID and username; (3) backend POSTs a JSONRPC request to MA's Ingress endpoint (`http://localhost:8094/api`) with the user headers, calling `auth/token/create`; (4) MA auto-authenticates the Ingress request and creates a long-lived 10-year JWT. The entire flow is invisible to the user — one button click, no credentials, no popup.

### Hardening and HAOS networking fixes (v2.18.1–v2.18.3)

Three rapid-fire patches addressed real-world deployment issues discovered during HAOS verification:

**v2.18.1 — websockets compatibility.** The HAOS addon Docker image ships an older `websockets` library (<14) that doesn't accept the `proxy=None` keyword argument. A `_ws_connect()` compatibility wrapper was added that tries with `proxy=None` first, catches `TypeError`, and retries without it.

**v2.18.2 — HAOS addon networking.** In HAOS each addon runs in its own Docker container with its own network namespace — `localhost:8094` from the bridge addon does *not* reach MA's Ingress port. The fix: `_find_ma_ingress_url()` queries the HA Supervisor API (`http://supervisor/addons/{slug}/info`) to discover the MA addon's Docker hostname and Ingress port, then connects via Docker DNS (e.g. `http://d5369777-music-assistant:8094`). Known MA addon slugs (`d5369777_music_assistant`, `_beta`, `_dev`) are tried in order. The addon config gained `hassio_api: true` and `homeassistant_api: true` permissions.

**v2.18.3 — JSONRPC response format.** MA's `auth/token/create` returns the token as a raw JSON string when called via the Ingress port, not wrapped in `{"result": "..."}`. The response parser now handles both formats and logs the raw response for diagnostics.

### Configuration UI overhaul (v2.19.0)

The Configuration section had grown organically and needed restructuring. Save buttons were in the middle of the form, Music Assistant Integration was buried inside Advanced settings (two clicks deep), the BT Devices table had 9 columns with 700px horizontal scroll on mobile, and labels were verbose paragraphs.

The overhaul reorganized the form into clearly labeled sections — General, Bluetooth, Music Assistant (promoted to top level), Advanced, and Authentication — each with icon headings and visual separation. A sticky save bar now appears at the bottom when config has unsaved changes. The BT Devices table was split into a main row (Name, MAC, Adapter, Format) and an expandable detail sub-row for advanced fields (Listen Address, Port, Delay, Keep-alive) that auto-opens when non-default values exist.

### Configuration UX polish and community feedback (v2.20.0)

Community feedback on the v2.19.0 release drove a second round of polish. Users noted that the Add button in the scan/paired device list was too far from the device name, making it hard to target. The Advanced settings panel (which now contained only 4 fields) was dissolved entirely — fields were moved into their respective sections and the extra panel removed.

Key changes: the MA form now auto-collapses to a summary when connected (a "Reconfigure" link expands it); auth fields hide when disabled; BT device expand chevron was moved to the left side of the row for conventional tree-style interaction; devices start collapsed by default; scan/paired device rows became fully clickable with hover highlight; the Scan button was moved before +Add Device for a discovery-first workflow. A `_configLoading` guard was added to prevent programmatic field population from triggering the dirty-state indicator on page load.

### Code audit and internal refactoring (v2.20.3)

A comprehensive code review of the entire codebase (~10 700 lines across 35 Python files) exposed two critical issues: a dead `/api/bt/reconnect` endpoint (the function existed but lacked a `@route` decorator — no HTTP request could reach it) and a `postMessage('*')` wildcard in the HA OAuth popup callback, which violated the same-origin principle. Both were fixed immediately.

The bigger outcome was splitting the 3 178-line `routes/api.py` monolith — the single largest file in the project — into five focused modules: core volume/mute/pause routes stayed in `api.py` (581 lines); Bluetooth scan/pair/reconnect moved to `api_bt.py` (396); Music Assistant integration and OAuth flow to `api_ma.py` (1 216); config and settings to `api_config.py` (502); status, SSE streaming, and diagnostics to `api_status.py` (647). Each module registers its own Flask Blueprint; `web_interface.py` wires all five. Backward-compatible re-exports were added so existing tests and external callers continue to work without changes.

Thread-safety received targeted fixes: six places that iterated the global `_clients` list without acquiring `_clients_lock` were patched — three in `ma_monitor.py` via a new `state.get_clients_snapshot()` helper, two in config and MA routes. The `MaMonitor._msg_id` counter, previously a bare `int` incremented across threads, was replaced with `itertools.count(1)` — atomic under CPython. A duplicate MAC-address regex was consolidated into `services/bluetooth.py` as the canonical `is_valid_mac()` helper.

All 138 tests passed after the refactoring; `ruff check` stayed clean throughout.

A follow-up patch (v2.20.4) fixed the JWT token `<details>` section's disclosure marker — the native ▼ was replaced with a CSS `::before` ▶ that rotates on open, matching other collapsible sections — and corrected the Music Assistant API token hint to point to "Settings → Profile → Long-lived access tokens".

A documentation audit (v2.20.5) refreshed the entire doc corpus: version references updated from 2.10.6/2.12.2 to 2.20.4, the API route split reflected in CLAUDE.md, READMEs, and contributing guides, web-ui.md rewritten to dissolve the obsolete "Advanced Settings" section, and 6 screenshots recaptured from the live HAOS UI (battery badges, restructured config panels, diagnostics). A "Show all" checkbox in the paired-devices header that overflowed the container boundary was also fixed by repositioning the label before the checkbox with proper margin alignment.

---

### AI agents

The entire project was developed by a human in collaboration with AI agents — from architectural decisions and debugging through to documentation.

| AI agent | Role | Commits (Co-authored-by) |
|----------|------|--------------------------|
| **GitHub Copilot** (Claude Sonnet 4.6) | Primary working agent: refactoring, code, code review, documentation | ~286 |
| **Claude Code** (Anthropic, Claude Sonnet 4.6) | Architectural design, complex debugging, audio routing iterations | ~168 |

Copilot was used as an interactive CLI agent directly in the terminal (`gh copilot`); Claude Code was used for deep refactoring and diagnostic sessions. The phrase "with a certain AI buddy" in the first announcement in the MA discussion refers precisely to this workflow.

Some commits carry both tags simultaneously — in sessions where the solution was worked out in Claude Code and the final PR was reviewed in Copilot CLI.
