# Group sync tuning: per-player delay correction inside Music Assistant groups — design

Status: design approved in conversation 2026-10-15, awaiting spec review.
Target: the v3 line (beta channel). A Music Assistant provider (approach B) follows
later and reuses the core described here unchanged.

## Goal and decisions

Players in one Music Assistant sync group drift apart audibly when their hardware
adds different latency (Bluetooth codecs, amplifiers, HDMI, sound servers). Sendspin
compensates per player with a static output delay, but today an operator sets it by
ear, one player at a time, and only bridge players have a tool for it.

This feature lets an operator align every Sendspin player of a group, bridge players
and other Sendspin players alike, from the bridge's web interface:

- **Manual** — sliders and ±1/±10 ms nudges, applied live, with a click track and
  solo / A–B listening to judge by ear.
- **Semi-automatic** — measure with the phone's microphone, review a proposal, apply.
- **Automatic** — measure and apply in one go, with a verification pass and undo.
- **Profiles** — named snapshots of a group's delays, saved and re-applied manually.

Decisions taken while designing:

| Question | Decision |
|---|---|
| What measures? | The phone's microphone, in the bridge web UI (browser). No host microphone. |
| How does the test signal reach players? | Through Music Assistant, played on the group, so the whole real audio path is measured and any Sendspin player can take part. Playback is interrupted for the session and restored afterwards. |
| How are profiles applied? | Manually only (UI and API). No Home Assistant entity, no automatic choice. |
| Where is sound aligned? | Both: by default close to each speaker (device latency only, valid across rooms); optionally from the listening position (includes distance, ~3 ms per metre), stored as a profile of kind `position`. |
| Where does the delay live? | In Music Assistant: the per-player config value `sendspin_static_delay` is the source of truth for every Sendspin player. |
| Portability | The logic is a standalone package with ports, so a Music Assistant provider can host it later without changes. |

Out of scope: players without the Sendspin protocol (Chromecast, AirPlay, DLNA,
vendor integrations). Music Assistant exposes no delay setting for them; the UI lists
them as "cannot be adjusted" and the planner leaves them out.

## Facts this design relies on (Music Assistant dev, checked 2026-10-15)

- Every player with the Sendspin protocol has the config entry
  `sendspin_static_delay` (0–5000 ms). On a universal player the key is
  `<client_id>||protocol||sendspin_static_delay`; the raw value is stored on the
  protocol player, whose id is the Sendspin `client_id`.
- When the config is loaded or updated, the Sendspin provider calls
  `_apply_static_delay()`, which sends `set_output_delay` to the client
  (`providers/sendspin/player.py`, `on_config_updated`).
- A delay change reported by the client (`OutputDelayChangedEvent`) is written back
  to the same config value. Bridge players already apply `set_output_delay`, persist
  it and report it through `client/state`, so their value and Music Assistant's stay
  in step.
- `players/cmd/play_announcement` pauses the current playback, plays a URL (on a
  group either as one in-sync stream or handed to members that can start in step)
  and resumes. For its duration it lifts every member's mute, before playback starts.
- Sendspin semantics: a player's delay is subtracted from each chunk's play time, so a
  larger delay makes that player sound **earlier**. Delays are 0–5000 ms integers.

## Architecture

```
            ┌──────────────── sendspin_sync (core, portable) ────────────────┐
            │ ClickTrack   Estimator   Planner   SyncSession   models         │
            │ ports: GroupControl · ProfileRepository · ClickTrackHost        │
            └─────────────▲──────────────────────────────▲───────────────────┘
                          │ implements                   │ implements (later)
   bridge adapters (sendspin_bridge.sync_tuning)     MA provider adapters
   GroupControl → MA WebSocket API                   GroupControl → mass.players/config
   ProfileRepository → config.json SYNC_PROFILES     ProfileRepository → provider storage
   ClickTrackHost → FastAPI route                    ClickTrackHost → MA webserver
   HTTP /api/v1/sync/*  (sync-contract v1)           same contract
                          ▲
                 Vue wizard (depends only on sync-contract v1)
```

### Core package `sendspin_sync`

Lives at `src/sendspin_sync/` (separate top-level package in the same repository and
wheel). Pure Python standard library; uses numpy for the matched filter when it is
importable, with a pure-Python fallback. A test fails the build if it imports
anything from `sendspin_bridge`.

- **Models** (dataclasses, JSON-serialisable): `Member` (`ma_player_id`,
  `client_id | None`, `name`, `adjustable`, `delay_ms | None`, `origin:
  bridge|sendspin|other`), `Recording` (member, `start_time_s`, `sample_rate`, int16
  PCM), `Measurement` (member, `phase_ms | None`, `confidence`, `clicks`,
  `spread_ms`, `status: ok|not_heard|unstable|skipped`, `reason`), `Proposal`
  (`reference`, rows `{member, before_ms, after_ms, delta_ms, clamped}`,
  `predicted_spread_ms`, `unverified: [member]`), `Profile` (`id`, `name`,
  `group_id`, `kind: device|position`, `delays: {ma_player_id: ms}`, `created_at`,
  `notes`).
- **ClickTrack** — deterministic WAV generator (see Signal).
- **Estimator** — recording → `Measurement`.
- **Planner** — measurements + current delays → `Proposal`.
- **SyncSession** — the state machine of one tuning session (see Session).
- **Ports** (`typing.Protocol`):
  - `GroupControl`: `members(group_id)`, `delays(members)`,
    `snapshot(group_id) -> Snapshot`, `start_signal(group_id, url)`,
    `stop_signal(group_id)`, `solo(group_id, member | None)`,
    `set_delay(member, ms)`, `restore(snapshot)`.
  - `ProfileRepository`: `list(group_id)`, `get(id)`, `save(profile)`,
    `delete(id)`.
  - `ClickTrackHost`: `url_for(track) -> str` (single-use, tokenised).
- `Snapshot` holds original delays, per-member volume and mute, and an opaque
  `restore_token` owned by the adapter (queue state, announcement handle). It is
  JSON-serialisable so the adapter can persist it.

### Bridge adapters `sendspin_bridge.sync_tuning`

- `MaGroupControl` over the existing Music Assistant WebSocket client:
  group members and their protocols, `config/players/get_entries` for the current
  delay, `config/players/save` to write `sendspin_static_delay`, the signal and solo
  mechanism chosen in stage 0, restore.
- `ConfigProfileRepository`: `SYNC_PROFILES` list in `config.json` (written under
  `config_lock`, exported by config download, preserved across HA add-on option
  translation like other UI-only keys).
- `FastApiClickTrackHost`: serves the WAV at `/api/v1/sync/signal/{token}.wav`,
  token valid for the session only, reachable by Music Assistant (same address
  rules as the artwork proxy).
- `SessionStore`: one active session per group, persisted snapshot
  (`sync_snapshot.json` next to the config) so a restart can restore.
- HTTP layer `/api/v1/sync/*`, problem+json errors with stable codes.

### Future Music Assistant provider (approach B)

Same core; adapters over `mass.players` (members, mute, announcements), `mass.config`
(delay), `mass.streams`/webserver (click track) and provider storage (profiles). The
wizard UI is served or embedded by the provider and talks the same contract. A
separate spec covers it after the bridge version ships.

## Signal

- WAV, 48 kHz, 16-bit, stereo (identical channels), 180 s, −12 dBFS peak.
- Click: linear chirp 2→6 kHz, 8 ms, Hann window. Bluetooth codecs pass this band
  and phone microphones record it well; no bass content is needed.
- Period **P = 2 s**. Every fifth click is a **marker**: two chirps 20 ms apart.
  Markers extend the unambiguous range from ±1 s to ±5 s (cycle 10 s).
- The track starts with 1 s of silence; a session longer than the track starts it
  again (phases stay valid because measurements are relative within one playback,
  and a restart begins a new measurement round).

## Capture (browser)

- One `AudioContext` for the whole session; capture through an AudioWorklet. Every
  recording is stamped with `context.currentTime` at its first sample, so all
  recordings share one continuous clock. The phone's input latency is constant and
  cancels out in the differences.
- `getUserMedia` with `echoCancellation`, `noiseSuppression`, `autoGainControl`
  set to `false`.
- 6 s per member at the speaker, 12 s from the listening position.
- Low-pass and decimate to 16 kHz in the worklet; upload int16 binary
  (`application/octet-stream`, ~190 kB) with `start_time_s`, `sample_rate`,
  `member` as query parameters.

## Estimation

1. Matched filter: correlate the recording with the chirp template; take the
   envelope. Peaks above 6× the median noise floor are click candidates.
2. Each peak gets a session time `t = start_time_s + index / sample_rate` and a type
   (single or marker, by the 20 ms double peak).
3. Phase in the 10 s cycle: `φ = (t_marker − session_epoch) mod 10 s`, refined by the
   single clicks (each predicts the marker phase modulo 2 s). Result: circular mean.
4. Accept when at least 2 clicks are found, their phase spread is ≤ 3 ms and the peak
   SNR is ≥ 10 dB; otherwise `not_heard` or `unstable` with the reason.
5. Cost: about 100 k samples × 128-tap template per recording — 1–2 s in pure Python,
   milliseconds with numpy. Runs in the API worker pool, not on the event loop.

## Planning

- Reference: the first member measured `ok` (the operator can change it).
- Lag `e_i = φ_i − φ_ref`, unwrapped into (−5 s, +5 s].
- Measurements are taken with the current delays `d_i` in effect. A later player needs
  more delay (more delay plays earlier), so align everyone to the earliest:
  `d_i' = d_i + (e_i − min e)`.
- Normalise: subtract `min(d')` from every value, so the smallest delay is 0 and the
  alignment is unchanged. Round to 1 ms, clamp to 0–5000; a clamped row is marked and
  the predicted residual reported.
- Members that were skipped or failed keep their delay and are listed as unverified.
- Verification: a repeat round after applying. Automatic mode runs it itself and, if
  the residual spread is above 5 ms, applies one refinement (at most two rounds in
  total). The before/after spread is stored in the session history and the profile's
  notes.

## Session

States: `created → signal_starting → measuring → proposed → applying → verifying →
restoring → done`, plus `cancelled` and `failed` (both go through `restoring`).

- **created**: members resolved; snapshot taken and persisted.
- **signal_starting**: click track started on the group; a level check plays the
  whole group so the operator can position the phone.
- **measuring**: for each member in the operator's order: `solo(member)`, prompt
  ("hold the phone 30–50 cm from Kitchen" or "stay where you are"), receive the
  recording, estimate. A step can be repeated or skipped; members can be reordered.
- **proposed**: proposal computed (semi-automatic waits here for the operator).
- **applying**: `set_delay` for each changed member; automatic mode keeps an undo for
  30 s.
- **verifying**: optional repeat round (automatic: always).
- **restoring**: `solo(None)`, `stop_signal`, `restore(snapshot)`; retried with
  backoff until it succeeds. Also triggered by cancel, an error, a 15-minute
  inactivity timeout and, after a bridge restart, by a persisted snapshot.

Only one session per group. A second client sees "tuning in progress" and gets a
read-only view. Manual mode is a session without measuring: it can start the signal
and solo/A–B on demand and writes delays live (debounced 300 ms).

## Signal path and solo (decided in stage 0)

The core only calls `start_signal`, `solo`, `stop_signal`, `restore`. Candidates the
spike evaluates on Music Assistant dev:

- **(a) Announcement + mute after start.** `play_announcement(url)` on the group;
  Music Assistant pauses and resumes the current playback and keeps the clip in sync.
  Solo mutes the other members after playback has started (announcements lift mutes
  only at the start). Open question: whether a mute set mid-announcement holds and
  how the end-of-announcement restore treats it.
- **(b) Queue playback + mute.** Play the track through the group's queue; the
  adapter snapshots and restores queue items, index, position, shuffle and repeat.
  Fragile with large queues.
- **(c) Hybrid.** Bridge players are soloed by the bridge itself (sink mute, no Music
  Assistant round trip); other players through Music Assistant's mute.

The spike also confirms that a config write reaches a bridge player and the web kiosk
live, that restore leaves the group as it was, and the estimator's accuracy on real
phone recordings. Its outcome is a short decision note; the spike code is thrown away.

## API (sync-contract v1)

JSON Schema in `docs/contracts/sync-contract-v1.json`; the OpenAPI models of the
bridge are generated to match it.

- `GET /api/v1/sync/groups` — Music Assistant groups with members (`origin`,
  `adjustable`, `delay_ms`, `last_measured_at`).
- `GET /api/v1/sync/groups/{group_id}` — one group.
- `PUT /api/v1/sync/groups/{group_id}/delays` — manual write `{ma_player_id: ms}`.
- `POST /api/v1/sync/sessions` `{group_id, mode: manual|semi|auto, point:
  device|position}` → session; `GET`/`DELETE /api/v1/sync/sessions/{id}`.
- `POST /api/v1/sync/sessions/{id}/signal` `{action: start|stop}`;
  `POST .../solo` `{member | null}`; `POST .../ab` `{a, b}`.
- `POST /api/v1/sync/sessions/{id}/recordings?member=…&start_time_s=…&sample_rate=…`
  (binary body) → measurement.
- `GET .../proposal`; `POST .../apply` `{rows?}`; `POST .../undo`;
  `POST .../verify`.
- `GET/POST /api/v1/sync/profiles?group_id=…`; `PATCH`/`DELETE
  /api/v1/sync/profiles/{id}`; `POST /api/v1/sync/profiles/{id}/apply`;
  `POST /api/v1/sync/groups/{group_id}/capture` `{name, kind}` → profile from the
  current delays.
- Session state changes are pushed as `sync` events on the existing event stream
  (`/api/v1/events`), so the wizard does not poll.

Error codes: `ma_unavailable`, `ma_forbidden` (token cannot change player config),
`session_active`, `session_expired`, `member_gone`, `not_adjustable`,
`recording_invalid`, `restore_pending`.

## UX

Entry points: Groups page → group card → **Sync**; for a bridge player, also its
drawer's Timing tab. Route `/sync/:groupId`, designed for a phone in portrait.

**Group screen**
- Members: name, origin badge (bridge / Sendspin / cannot be adjusted), current
  delay, last measurement and its age.
- Profiles: list, **Apply**, **Save current as…**, rename, delete. Applying shows the
  diff first and skips members no longer in the group (listed).
- Actions: **Manual tuning**, **Measure and suggest**, **Measure and apply**.

**Manual tuning**
- Per member: slider 0–5000, −10/−1/+1/+10 buttons, number field; applied live.
- Listening bar: **Play clicks** on the group, **Solo** per member, **A/B** (two
  members alternate every two clicks).
- **Reset to session start**; on leaving, offer to save as a profile.

**Measurement wizard**
1. Preparation: warning that playback is interrupted and restored; measuring point
   (at each speaker — default; from the listening position); microphone permission;
   tip to hold the phone still.
2. Level check: clicks on the whole group with a level meter; too quiet / too loud
   hints.
3. Member by member: prompt, solo, 6 s (12 s) recording with progress, result
   (✓ with confidence, or not heard / unstable with **Repeat** / **Skip**). Steps
   can be redone; members reordered.
4. Proposal: table before → after (Δ), before/after spread chart; **Apply**,
   **Apply and save as profile…**, **Cancel**. Automatic mode applies and shows
   **Undo (30 s)**.
5. Done: group restored; optional quick listen with clicks on the whole group.

**Failure handling in the UI**
- A member leaves the group or disconnects mid-session: its step is marked; the plan
  goes on without it.
- Music Assistant unreachable: restore keeps retrying; a banner "Could not restore
  the group" with **Retry** stays until it succeeds.
- Missing admin rights: proposal shown with values to copy, plus a hint to reconnect
  Music Assistant with an administrator.
- Clamped values: warning with the predicted residual.
- Keyboard accessible; step status in an aria-live region; en and ru.

## Persistence

- `SYNC_PROFILES` in `config.json`: list of profile objects (see Models). Config
  schema gets the key; migration not needed (absent = empty list).
- `sync_snapshot.json` in the config directory while a session holds a snapshot;
  removed after a successful restore.
- Recordings are kept in memory for the session only, never written to disk.

## Security

- `/api/v1/sync/*` uses the bridge's normal authentication and CSRF rules.
- The click-track URL carries a random single-session token; the content is fixed
  and contains no user data.
- Writing player config needs a Music Assistant token with admin rights; the bridge
  never stores Music Assistant credentials beyond the token it already keeps.

## Testing

- **Core**: synthetic recordings (offsets, noise, room echo, band-limiting, AGC,
  dropped clicks, phase wrap at the cycle edge); property tests for the planner
  (non-negative, alignment preserved, normalised minimum 0, clamping reported);
  `SyncSession` against a fake `GroupControl`, including failure and restore paths;
  the import-boundary test.
- **Bridge**: adapter tests against a fake Music Assistant client; HTTP contract tests
  validated against `sync-contract-v1.json`; profile persistence and HA option
  translation; snapshot restore after restart.
- **UI**: vitest for each wizard step with a fake API, capture with a fake
  AudioWorklet, manual mode debounce, profiles.
- **Live**: test stand (Music Assistant dev on HAOS, bridge local outputs, web
  kiosk): add a known +120 ms to one member and confirm the wizard brings it back.

## Stages

0. **Spike** (throwaway) on Music Assistant dev: signal path and solo (a/b/c), live
   config write to a bridge player and the web kiosk, restore, estimator accuracy on
   real phone recordings. Output: decision note appended to this spec.
1. **Core `sendspin_sync`**: click track, estimator, planner, models, session state
   machine, ports, tests.
2. **Bridge adapters and API**: `MaGroupControl`, profiles in config, click-track
   route, `/api/v1/sync/*`, contract schema, restore after restart.
3. **UI part 1**: group sync screen, manual tuning, profiles.
4. **UI part 2**: measurement wizard (semi-automatic and automatic), verification,
   undo.
5. **Docs, live check on the stand, beta release.** Then a separate spec for the
   Music Assistant provider.

## Risks

- Music Assistant changes the announcement or config behaviour this relies on (dev
  branch). Mitigation: everything Music Assistant-specific sits in one adapter;
  stage 0 pins the behaviour with a test against the real server.
- Bluetooth latency varies between connections and re-anchors; a measurement can be
  stale next day. Mitigation: show the measurement age and spread; recommend
  re-measuring when repeated measurements differ by more than 10 ms.
- Phone browsers differ (iOS Safari AudioWorklet, permission prompts, sample rates).
  Mitigation: capture tested on Chrome Android and Safari iOS during stage 4; manual
  mode works without a microphone.
- Large delays require the server to send audio further ahead; Sendspin accounts for
  the reported output delay, but players with small buffers may underrun near 5000 ms.
  Mitigation: proposal warns above 2000 ms.
