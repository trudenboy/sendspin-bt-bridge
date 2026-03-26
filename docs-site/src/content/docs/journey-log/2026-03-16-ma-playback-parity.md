---
title: "March 16: MA playback parity and runtime hardening"
description: "Music Assistant-style playback controls, fail-safe recovery, LXC updates, and backend-authoritative MA control sync"
---

## March 16, 2026 — Backend-authoritative MA control sync and armv7 CI release hardening (v2.32.7)

The `2.32.7` release turns the latest Music Assistant control work from “UI feels faster” into “backend state is actually more trustworthy.” The main theme is authority: shuffle/repeat/queue actions should not depend on ad-hoc frontend mutations or on a fresh per-request WebSocket that races the long-lived monitor. Instead, the bridge now treats the backend cache and the persistent MA monitor as the source of truth and pushes a predicted-but-typed state model outward to the dashboard.

Four threads define the release:

- **Backend-owned pending state** — `/api/ma/queue/cmd` now returns structured command results with `op_id`, `syncgroup_id`, pending metadata, and a backend-generated predicted snapshot. That means the UI can respond immediately without inventing its own private version of `ma_now_playing`.
- **Monitor-first command flow** — MA queue commands now go through the persistent monitor connection as the authoritative hot path. Interleaved `player_queue_updated` / `player_updated` events are no longer silently swallowed while waiting for command acknowledgements; they are deferred and reconciled right after the ack lands.
- **Reconnect state retention** — short MA monitor disconnects no longer erase visible playback state. The bridge keeps the last confirmed snapshot, marks it stale/disconnected, and preserves command/error metadata so the dashboard remains intelligible while reconnect is in progress.
- **Release-pipeline honesty for armv7** — the dedicated armv7 Docker workflow no longer fails the whole release if GitHub Actions cache export breaks after the image has already been built and pushed. Cache export is now treated as best-effort instead of a false-red release gate.

This is the kind of release where the most important effect is reduced ambiguity: fewer split-brain control states between frontend and backend, fewer races between command ack and MA events, less visible state loss during reconnects, and less noise from CI failures that are not actually image-build failures.

---

## March 16, 2026 — Playback UI polish across cards, lists, and bulk actions (v2.32.6)

The `2.32.6` release is a small dashboard follow-up, but it finishes several interaction details that were left uneven after the larger playback redesigns. The theme here is consistency: the card mini-player, the expanded list row, and the dashboard-wide action bar should all read like parts of the same Music Assistant-inspired surface rather than separate iterations that merely coexist on the same page.

Four threads define the release:

- **Cleaner card playback flow** — the card progress indicator now sits below the track metadata instead of fighting for horizontal space beside it. That gives long track/artist strings more room to breathe and makes the elapsed-time stack read in the same top-to-bottom order as the rest of the mini-player.
- **Richer expanded-list context** — expanded list rows now add a text-only `Now playing` badge above the active title, enlarge the artwork to use the available space better, and slow the equalizer animation into a more Music Assistant-like rhythm. Together those changes make the live state feel clearer without adding extra chrome.
- **Bulk-action visual parity** — `Reconnect all` and `Release all` in the dashboard toolbar now use the same action-button language as the per-device `Reconnect` / `Release` controls. That removes another small but noticeable inconsistency in how safe/reversible Bluetooth actions are presented.
- **Live deployment hardening** — the UI polish was verified on the Proxmox target itself, including a cache-busting follow-up that confirmed the updated runtime was correct once stale HTML was flushed.

This is not a headline architectural release, but it is the kind of polish that keeps the dashboard feeling trustworthy. Playback hierarchy is easier to read, status affordances are more explicit, and the same action patterns now repeat more consistently across the whole UI.

---

## March 16, 2026 — Truthful playback controls and a tighter list mini-player (v2.32.5)

The `2.32.5` release is a UI follow-up to `2.32.2`, but the real theme is not cosmetics alone. It is about making the dashboard's playback controls tell the truth about current runtime state while continuing the list-view move toward a more compact Music Assistant-style mini-player. In practice, that means less dead horizontal space, clearer queue context, and fewer cases where the UI looks actionable even though the underlying Sendspin / MA / sink path cannot actually perform the requested action.

Three threads define the release:

- **Stateful transport controls** — shuffle/repeat now update optimistically in the UI, repeat exposes separate `off` / `all` / `one` states, and transport/queue/mute/volume actions are disabled unless the required Sendspin, Music Assistant, or sink dependency is really available. Matching handler guards close the stale-click race window instead of relying on button styling alone.
- **Tighter expanded-list playback geometry** — artwork and current-track metadata now sit in one compact left block, while previous/current/next playback context and progress live in a second left-aligned block. The expanded row therefore reads more like a deliberate mini-player and less like two unrelated chunks with extra reserved whitespace between them.
- **Richer queue context at a glance** — previous and next queue items now render track, artist, and album on separate lines rather than collapsing secondary metadata into one combined string. That small presentation change makes long queue neighbors much easier to parse during live playback.

This is a small release, but it improves operator confidence in exactly the places that matter for a dashboard: controls look available only when they truly are, repeat communicates its actual mode, and the expanded list player exposes more context with less visual waste.

---

## March 16, 2026 — Fail-safe LXC updates and recovery rails (v2.32.2)

The `2.32.2` release is first and foremost an operational hardening release. It was triggered by a real native-LXC failure mode: an auto-update brought in Python code that referenced new modules, but the local updater still downloaded a stale hand-maintained file list and left the service in a restart loop. This release closes that gap at the updater architecture level instead of treating the missing-file incident as a one-off.

Five themes define the release:

- **Public visibility into repository health** — the GitHub traffic archiver now records richer repository and release statistics, and the docs site publishes that archive as a simple stats dashboard. That turns internal release/traffic bookkeeping into something operators and contributors can inspect without digging through workflow artifacts.
- **Release snapshots instead of file drift** — native `lxc/install.sh` and `lxc/upgrade.sh` now download a GitHub archive snapshot and sync the runtime tree recursively. That removes the brittle “remember to append every new file to two shell loops” maintenance pattern that caused the Turris outage.
- **Detached updates that survive restart** — one-click updates and background auto-updates are now launched through `systemd-run --no-block`, outside the `sendspin-client` service cgroup. That matters because restart, smoke-check, and rollback logic can now complete even while the main service is being restarted underneath them.
- **Transactional upgrade behavior** — the LXC updater now stages the new tree, validates imports before swap, restarts the service, performs local health checks, and rolls back automatically if the upgraded runtime does not come back cleanly. In other words, the update path now has a recovery story instead of just a replacement story.
- **Small but practical follow-ups around the release edge** — the armv7 Docker build path now matches the current `aiosendspin`/`av` dependency contract again, and the enlarged album-art preview no longer disappears under toolbar/group-action chrome in either dashboard view.

This is the kind of release that operators mostly notice by *not* having to notice it later: fewer updater assumptions, less script drift, and a much safer path for unattended native-LXC refreshes.

---

## March 16, 2026 — MA-style playback parity and progress stability (v2.32.0)

The `2.32.0` release turns the redesign work from “visually closer to Music Assistant” into “behaviorally closer too.” The main theme is parity: card and list views now share more of the same playback semantics instead of acting like two independent dashboards that merely look related. That shows up in the obvious UI polish — tighter track/equalizer grouping, cleaner card headers, hover-only secondary card actions, slimmer sliders, numeric volume labels — but the more important part is that queue and progress behavior is now being normalized across both views.

Four threads define this release:

- **Card/list playback convergence** — the expanded list row now behaves much more like an MA mini-player, with artwork-adjacent current-track metadata, queue-neighbor previews, and transport/shuffle/repeat controls arranged around the active track context. The card view was polished in parallel: selection checkbox moved to the far-left edge, secondary actions now reveal on hover instead of permanently consuming space, and the compact equalizer treatment is reused consistently across views.
- **Queue-context correctness** — MA queue metadata is no longer trusted blindly when `player_queues/all` omits neighboring items. The bridge now hydrates missing previous/next entries via `player_queues/items`, which removes the false `Queue start` / `Queue end` placeholders that previously appeared even when real neighboring tracks existed.
- **Progress stability instead of progress theatrics** — playback progress now initializes deterministically and merges stale MA elapsed snapshots instead of regressing to them. In practice, that removes both classes of UI bug seen during this cycle: the bar flashing full-width on first render and the elapsed-time/progress display jumping backwards when a slower MA payload arrives after local interpolation has already advanced.
- **Richer runtime inspection** — diagnostics now expose whether a device is actively playing and parse additional PulseAudio sink-input metadata (`application_*`, `media_*`). That is not a headline feature, but it makes live routing/debugging much more actionable when operators need to understand what is actually feeding a sink.

This is also a release about reuse as a maintenance strategy. Shared helpers now drive more of the equalizer/progress/queue behavior across card and list surfaces, which matters because the payoff is not just less code duplication — it is less UI drift and fewer “fixed here, still broken there” regressions in later polish passes.

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
