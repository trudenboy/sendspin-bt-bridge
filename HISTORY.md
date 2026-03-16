# Project History

A history of the architectural and functional evolution of sendspin-bt-bridge — for readers familiar with Home Assistant, Music Assistant, and multiroom audio setups.

**Period:** January 1 – March 16, 2026 · **Total commits:** ~956 · **Versions:** 1.0.0 → 2.31.11

---

## March 16, 2026 — Safe Music Assistant album-art recovery (v2.31.11)

The `2.31.11` release is a narrow but high-value follow-up to `2.31.10`. It fixes a visible regression in the redesigned dashboard: Music Assistant was already providing artwork metadata, but the web UI correctly refused to render most cover URLs because they pointed at a different origin or arrived as raw relative MA paths. In other words, the bug sat exactly at the boundary between “frontend safety” and “backend contract quality.”

This release fixes that boundary instead of weakening it:

- **Same-origin artwork delivery** — album covers now flow through a bridge-owned `/api/ma/artwork` endpoint, so the browser receives a URL from the same origin as the dashboard itself and the existing frontend safety guard can remain intact.
- **Correct MA URL resolution** — raw artwork paths from Music Assistant are wrapped before they reach the UI. Relative paths are resolved against the configured MA base URL, while absolute paths are allowed only if they still point back to that same MA origin.
- **Token-aware proxying without becoming an open proxy** — when Music Assistant requires authentication, the bridge forwards the stored MA bearer token for the artwork fetch. At the same time, foreign hosts are explicitly rejected so the new route cannot be abused as a generic fetch tunnel.

This is also a deliberately test-backed hotfix. Regression coverage was added for artwork URL wrapping in now-playing metadata and for the new proxy route's successful and rejected request paths. `2.31.11` is therefore best understood as a small release that restores a user-facing feature while preserving the stricter security posture introduced by the redesign.

---

## March 16, 2026 — Fail-safe runtime recovery and config hygiene (v2.31.10)

The `2.31.10` release is the next stabilization step after `2.31.9`: same broad theme of “make the bridge safer at the edges,” but this time focused more directly on lifecycle correctness for real fleets — adapter targeting, duplicate device declarations, zombie playback recovery, and the long-term hygiene of persisted config state.

Four practical themes define this release:

- **Fail-safe adapter handling** — the bridge no longer guesses `hci0` when adapter resolution fails. That sounds small, but on multi-adapter systems it is the difference between “degraded but understandable” and “quietly talking to the wrong controller.” The new behavior disables D-Bus monitoring for that device and relies on the existing bluetoothctl polling fallback instead of manufacturing a wrong path.
- **Safer startup identity** — duplicate Bluetooth MAC entries are now filtered before runtime objects are created. This protects the bridge from an easy configuration mistake that could otherwise launch two competing clients against one speaker, with all the usual side effects: conflicting reconnects, ambiguous sink ownership, and confusing UI state.
- **Playback-session aware watchdogs** — zombie playback recovery now tracks the current play session instead of permanently considering a subprocess “safe” after its first successful stream. In practical terms, a speaker that successfully played once can still be auto-recovered later if it re-enters a “playing but silent” state.
- **Config hygiene over time** — corrupt `config.json` files now leave behind a recovery copy (`config.json.corrupt-*`) before defaults are used, and stale `LAST_VOLUMES` state is pruned so removed devices do not keep dragging obsolete persistence forward.

This is also a strengthening release for correctness rather than scope. Regression tests were added for unresolved adapter fallback, duplicate MAC filtering, zombie watchdog session resets, corrupt config backup handling, and config/volume normalization paths. `2.31.10` is therefore best read as a release about making the bridge fail more honestly, recover more predictably, and age more cleanly under real operator workflows.

---

## March 16, 2026 — Runtime hardening and release-safety pass (v2.31.9)

The `2.31.9` release is a classic stabilization follow-up: no new flagship feature, but a concentrated pass over the places where a mature bridge most often fails in practice — diagnostics against messy host output, config export safety, shutdown races, and Bluetooth reconnect bookkeeping. It is the release that makes the already-expanded UI/configuration surface safer to operate and easier to trust.

Four threads define this release:

- **Defensive diagnostics** — parsers that read `pactl`, `bluetoothctl`, and `/proc/meminfo` no longer assume perfectly shaped output. Instead of letting one truncated line crash a diagnostics/preflight path, the bridge now degrades gracefully and keeps the endpoint usable.
- **Safer config handling** — downloading `config.json` from the web UI now produces a share-safe export with password hashes, secret keys, and MA tokens removed. At the same time, the config-save path normalizes known numeric fields before writing them back, reducing long-term drift between UI input types and on-disk types.
- **Cleaner runtime edges** — subprocess command delivery now snapshots the daemon handle before use, and graceful shutdown works from a stable client snapshot instead of iterating a live shared list. These are small code changes with outsized impact on “hard to reproduce” restart/shutdown bugs.
- **Reconnect-churn reliability** — Bluetooth reconnect timestamps are now synchronized behind a lock, so churn pruning and threshold checks operate on one coherent window instead of racing with each other.

This is also a test-strengthening release. Focused regression tests were added for defensive diagnostics parsing, config export redaction, numeric normalization, subprocess TOCTOU handling, and Bluetooth churn isolation. In other words, `2.31.9` is less about expanding scope and more about making the bridge's operational edges production-friendlier.

---

## March 14, 2026 — Empty-state navigation hotfix (v2.31.8)

The `2.31.8` release is a narrow UI follow-up shipped after the larger redesign work from `2.31.6` and the auth hotfix in `2.31.7`. It fixes the two dashboard empty-state actions that were still wired to assumptions from the pre-redesign layout.

When no Bluetooth adapter is present, the empty-state CTA now opens `Configuration → Bluetooth`, lands on the adapters card, and prepares a manual adapter row so the user can act immediately. When adapters exist but no devices are configured yet, the scan CTA now opens `Configuration → Devices → Discovery & import` and launches the Bluetooth scan from the correct redesigned section. In short: the empty dashboard is once again an actionable starting point instead of a dead-end hint.

---

## March 14, 2026 — MFA/TOTP login hotfix (v2.31.7)

The `2.31.7` release is a focused auth hotfix shipped immediately after `2.31.6`. It fixes a regression in the direct Home Assistant login flow: when a user entered their TOTP code on the second MFA step, the bridge rendered that form without a valid CSRF token, so the verification POST was rejected as an invalid session.

This release restores the intended HA login-flow behavior by preserving the CSRF token across the MFA step and adds a regression test that walks the full `username/password -> MFA -> successful sign-in` path. In practice, that means Home Assistant users can again complete sign-in normally when TOTP is enabled.

---

## March 14, 2026 — UI system consolidation (v2.31.6)

The `2.31.6` release completes the first full polish pass after the major `2.31.0` redesign. The work focused less on introducing new primitives and more on making the new UI internally consistent: the Configuration section was rebuilt as a card-based settings surface, dashboard badges were normalized into a shared chip system, and list/card views were brought back into functional and visual parity.

Three themes define this release:

- **Configuration maturity** — `Cancel` now restores the last saved state, security/runtime controls were expanded (session timeout, brute-force protection, MA WebSocket monitor), and the information hierarchy across General / Security / Bluetooth / Devices / Music Assistant was tightened.
- **Device-management ergonomics** — adapter badges link directly into `Configuration → Bluetooth`, custom adapter names are editable, MA sync-group badges deep-link to the correct Music Assistant settings view, and view-mode behavior now defaults to list mode on larger fleets while remembering the user's choice.
- **Badge/runtime cleanup** — delay is visible in both list and card views, list rows expose the same key runtime context as cards, empty placeholder badges were removed, overlapping/misaligned chips were fixed, and list sorting now includes adapters while reusing the same adapter/status chip language as cards.

This release is best understood as the “consistency” release for the redesign: fewer conceptual changes than `2.31.0`, but a much stronger match between mockup, runtime behavior, and the final shipped UI.

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

### AI agents

The entire project was developed by a human in collaboration with AI agents — from architectural decisions and debugging through to documentation.

| AI agent | Role | Commits (Co-authored-by) |
|----------|------|--------------------------|
| **GitHub Copilot** (Claude Sonnet 4.6) | Primary working agent: refactoring, code, code review, documentation | ~540 |
| **Claude Code** (Anthropic, Claude Sonnet 4.6) | Architectural design, complex debugging, audio routing iterations | ~168 |

Copilot was used as an interactive CLI agent directly in the terminal (`gh copilot`); Claude Code was used for deep refactoring and diagnostic sessions. The phrase "with a certain AI buddy" in the first announcement in the MA discussion refers precisely to this workflow.

Some commits carry both tags simultaneously — in sessions where the solution was worked out in Claude Code and the final PR was reviewed in Copilot CLI.
