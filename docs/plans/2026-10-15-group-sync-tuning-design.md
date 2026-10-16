# Group sync tuning: per-player delay correction inside Music Assistant groups — design

Status: design approved in conversation 2026-10-15; reviewed against the sources
2026-10-15 (see "Review notes" at the end).
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
- **Semi-automatic** ("Measure with a microphone") — measure with a microphone (this
  device's, or a phone through the capture page), then the measured delays are applied
  and the same session continues as the manual editor (listen, adjust, finish).
- ~~**Automatic**~~ — removed in the UX round (2026-10-16, see "UX round" below).
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
vendor integrations) and Sendspin players whose client does not accept
`set_output_delay`. Music Assistant exposes no delay setting for them; the UI lists
them as "cannot be adjusted" and the planner leaves them out. Groups in v1 are
Music Assistant's persistent sync groups (player `type` `group`, provider
`sync_group`), the ones the Groups page already lists; a temporary sync (a leader
with players synced to it) is not offered as a group.

## UX round (2026-10-16) — supersedes the parts below where they differ

User decisions (ledger "UX round"): fix the UX audit; remove the automatic mode;
restore the volumes from before calibration; semi-automatic measuring can use a
phone through a link + QR code; after a measurement the session switches to the
manual editor with the measured delays applied (one editing surface).

- **Modes.** `manual` and `semi` only. No automatic apply, check rounds, undo window,
  `verify`/`undo` endpoints, `round`/`undo_until_s` fields or `verifying` state.
- **Semi ends in the editor.** "Apply the measured delays and listen" computes the
  proposal and applies it (explicit consent in the button; ruling: the editor is live,
  so the measured values are written on entry rather than shown as pending values).
  The session is then `tuning`: solo, A–B and live writes as in manual tuning; each
  measured row shows the measured value and "Reset to measured"; members not heard or
  skipped keep their delay; **Finish** (`POST .../finish`, `done`) keeps the delays;
  **Cancel** writes back the delays from before the measurement, then ends.
- **Volumes.** The first `set_volume` of a member records the volume it had (session
  field `volumes_before`, persisted in the snapshot entry as `volumes`); restore puts
  them back first, then unmutes, then stops our clicker. Failures retry; a player MA
  no longer lists is given up after `RESTORE_GIVE_UP_S`.
- **Queue.** The clicker starts with `option: play` (queue kept) and its items are
  deleted from the group's queue at the end (best effort). The WAV carries a
  LIST/INFO title "Sendspin sync clicks" (a title without artist/album keeps it a
  sound effect in MA).
- **Readiness.** Members carry `can_mute`, `available`, `volume`, `muted`; the Group
  tab and step 1 warn before the music is stopped.
- **Countdown.** `clicker_left_s` (3-minute track) is shown on the clicks step, the
  members step and in Manual tuning.
- **Phone capture.** See "Phone capture" below.

### Phone capture

A phone opens `http://<bridge LAN address>:<port>/sync/capture/<token>?lang=xx` (the
same base URL Music Assistant fetches the clicks from). Separate recordings share no
clock, so the phone records once while the bridge plays a **sequence**: everyone
muted for 3 s, each member alone (7 s at the speakers / 13 s from the listening
position) with everyone muted in between (5 s to walk / 2 s). The upload (any audio
file; at most 20 MB and the sequence's length + 60 s) is recognised by its magic
bytes (mp4/m4a/mov, wav, ogg, matroska/webm, mp3, ADTS AAC, flac; anything else is
refused before FFmpeg sees it), decoded from memory with PyAV (forced demuxer,
`format_whitelist` of those demuxers, `protocol_whitelist` none) to 16 kHz mono;
click events (at most 6 per click period, at most 4000) are aligned against the
solos as the bridge timed them, and each member's stretch is estimated with its file
offset as the start time. At most 16 members per sequence.

Security: the token is `secrets.token_urlsafe(32)`, one per session (a new link
revokes the old), valid 30 minutes, refused as soon as the session restores or ends
and dropped from the table when it finishes; it is accepted only by the two
`/api/v1/sync/capture/{token}` endpoints and the page; the rest of the API ignores it
(not a bearer token, no cookie). The state it reads has short names, statuses and
the sequence progress — no delays, no session id. The page is standalone HTML with a
per-response CSP nonce, `no-store`, `no-referrer`, `noindex`. In a secure context it
records with MediaRecorder and uploads when the sequence ends; on plain HTTP it uses
`<input type=file accept="audio/*" capture>` (the phone's recorder) or a picked file.

## Facts this design relies on (Music Assistant dev `d3258305`, checked 2026-10-15)

- A Sendspin player gets the config entry `sendspin_static_delay` (integer,
  0–5000 ms, `immediate_apply`) only when its client lists `set_output_delay` (or the
  legacy `set_static_delay`) among the supported commands of `client/state`
  (`providers/sendspin/player.py`, `_OUTPUT_DELAY_COMMANDS`). Bridge players do.
  Whether a member is adjustable is therefore read from
  `config/players/get_entries`, never assumed from the protocol.
- The protocol player's id is the Sendspin `client_id` (`SendspinBasePlayer` looks
  its client up by `player_id`). On a universal player the same value is reached
  through the key `<client_id>||protocol||sendspin_static_delay`
  (`CONF_PROTOCOL_KEY_SPLITTER`); `config/players/save` on the universal player
  forwards such keys to the protocol player and never stores a copy. Writing the
  plain key with `config/players/save` on the protocol player itself is equivalent.
  Protocol players are hidden from `players/all` unless it is called with
  `return_protocol_players: true`; a universal player names its Sendspin child in
  `output_protocols[]` (`output_protocol_id`, `protocol_domain == "sendspin"`).
- `config/players/save` runs `on_player_config_change`, which calls the player's
  `on_config_updated()` → `_apply_static_delay()` → `set_output_delay` on the
  client; the change is live without a reconnect. It also emits
  `PLAYER_CONFIG_UPDATED`.
- A delay change reported by the client (`OutputDelayChangedEvent`) is written back
  to the same config value with `set_raw_player_config_value`. Bridge players
  already apply `set_output_delay`, persist it and report it through `client/state`,
  so their value and Music Assistant's stay in step.
- Reads: `config/players/get_value` `{player_id, key}` returns one value;
  `config/players/get_entries` `{player_id}` returns the entries (and so tells
  whether the delay entry exists). Scopes: `CONFIG_PLAYERS_READ` for reads,
  `CONFIG_PLAYERS_WRITE` for `config/players/save`, `PLAYERS_READ` for
  `players/all`, `PLAYERS_CONTROL` for `players/cmd/*` including
  `volume_mute`, `volume_set` and `stop`; `player_queues/play_media` for the
  clicker (option C).
- *Historical (option A, superseded by option C — see "Signal path and solo"; the
  bridge no longer sends announcements):* `players/cmd/play_announcement`
  `{player_id, url, pre_announce?, volume_level?}`
  (`controllers/players/announcements.py`). Sendspin players announce natively only
  through a Home Assistant relay, so for a sync group Music Assistant uses its
  default implementation on the group player: it records power, volume, mute,
  playback state and queue position, lifts the mute of members that were muted,
  applies the announcement volume, plays the URL as media on the group (one
  in-sync stream to every member), waits for the player to report playing and then
  idle, and restores what it recorded (previously muted members are muted again;
  playback resumes if it was playing). The command **answers only when the
  announcement has finished**, so the adapter must not send it through a channel
  with a short reply timeout. A clip may be at most 300 s (`MAX_ANNOUNCEMENT_SECONDS`);
  the renderer transcodes the URL with ffmpeg (44.1 kHz), which keeps the timing.
  `pre_announce` defaults to on when the URL contains `tts`, so the adapter passes
  `pre_announce: false` and keeps `tts` out of the path. There is no "stop
  announcement" command (stage 0: `players/cmd/stop` does **not** end it, it blocks
  until the clip ends). The default implementation reads `players/cmd/play` and the
  queue as busy while `announcement_in_progress` is set; that flag lives in the
  player's `extra_data`, which is not on the API.
- *Historical (option A):* a mute set on a member **during** the announcement is not undone by Music
  Assistant's restore (it only re-mutes the members that were muted before), so the
  adapter's own restore must unmute what it muted. Whether such a mute is honoured
  by the group stream while it plays is confirmed in stage 0.
- Sendspin semantics (aiosendspin 10.0.2, `compute_play_time`): a player's output
  delay is subtracted from each chunk's play time, so a larger delay makes that
  player sound **earlier**. The server sends audio ahead by at least the output
  delay. Delays are 0–5000 ms; the client reports `round(output_delay_ms)` as an
  integer in `client/state`.

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
wheel). It ships everywhere the bridge ships once it is listed next to
`src/sendspin_bridge` in `[tool.hatch.build.targets.wheel] packages` of
`pyproject.toml`: the Docker image installs the wheel from `/build` (and the HA
add-on is that image), the LXC install and upgrade run `pip install -e` on the same
`pyproject.toml`, and the release tarball is `git archive` of the tree. A packaging
test pins the hatch list. It also joins `known-first-party` for ruff's isort and the
mypy invocation. Pure Python standard library; uses numpy for the matched filter
when it is importable, with a pure-Python fallback — numpy is not a dependency and
is installed in none of the shipped environments, so the pure-Python path is the
production path and is sized accordingly (see Estimation). A test fails the build
if it imports anything from `sendspin_bridge`.

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
    `snapshot(group_id) -> Snapshot`, `playback(group_id) -> GroupPlayback`,
    `stop_group(group_id)`, `start_clicker(group_id, url)`,
    `mute(member, muted)`, `set_volume(member, level)`, `set_delay(member, ms)`.
  - `ProfileRepository`: `list(group_id)`, `get(id)`, `save(profile)`,
    `delete(id)`.
  - `ClickTrackHost`: `url_for(track) -> str` (tokenised; the token is valid for
    the session, not single-use — Music Assistant's renderer may fetch the URL more
    than once).
- `Snapshot` holds the original delays (for reset and undo) and which members were
  muted before the session (never touched). `GroupPlayback` is one reading of the
  group: its playback state, the current media's uri and every member's volume;
  `plays(url)` tells whether the group plays the session's click track.

### Bridge adapters `sendspin_bridge.sync_tuning`

- `MaGroupControl` over Music Assistant's WebSocket API with the credentials the
  bridge already keeps (`get_ma_api_credentials()`): group members from the cached
  sync groups plus `players/all` with `return_protocol_players: true` for their
  protocols, `config/players/get_entries` / `get_value` for the current delay,
  `config/players/save` to write `sendspin_static_delay`, and the signal path and
  solo decided after stage 0 (option C, see "Signal path and solo"): the group's
  playback (`players/all`: state, current media, member volumes), `players/cmd/stop`
  on the group, the click track queued with `player_queues/play_media`,
  `players/cmd/volume_mute` per member, `players/cmd/volume_set` for the volume
  step. Each session owns its own connection.
- `ConfigProfileRepository`: `SYNC_PROFILES` list in `config.json`. The key must be
  registered in three places or it is silently dropped: `DEFAULT_CONFIG` (which
  defines `CONFIG_ALLOWED_KEYS`; unknown keys are filtered on load), `config/schema.json`
  (`additionalProperties: false`), and the `web_ui_only_keys` tuple of
  `scripts/translate_ha_config.py` (preservation across add-on restarts is an
  explicit list, not automatic). Written under `config_lock` through
  `update_config()`; included in the config export/import round trip; not part of
  the settings form payload (`PUT /api/v1/config` ignores it — profiles change only
  through the sync API).
- `FastApiClickTrackHost`: serves the WAV at `/api/v1/sync/signal/{token}.wav`.
  Music Assistant fetches it with ffmpeg, without a session or token header, so the
  route lives on a **public** router (no `require_principal`, like `bridge.public`)
  and the random token is its only guard. The URL is built for Music Assistant's
  side of the network: `http://<bridge LAN address>:<WEB_PORT>/…` from
  `config.network.get_local_ip()` and the resolved web port — never the ingress
  or browser-facing address. (The existing `/api/v1/calibration/tone.wav` sits on
  the authenticated diagnostics router and cannot serve this purpose when
  authentication is on.)
- `SessionStore`: one active session per group, persisted snapshot
  (`sync_snapshot.json` next to the config) so a restart can restore.
- HTTP layer `/api/v1/sync/*`, problem+json errors with stable codes.

### Future Music Assistant provider (approach B)

Same core; adapters over `mass.players` (members, mute, playback), `mass.player_queues` (the clicker), `mass.config`
(delay), `mass.streams`/webserver (click track) and provider storage (profiles). The
wizard UI is served or embedded by the provider and talks the same contract. A
separate spec covers it after the bridge version ships.

## Signal

- WAV, 48 kHz, 16-bit, stereo (identical channels), 180 s, −12 dBFS peak.
- Chirp: linear 2→6 kHz, 8 ms, Hann window. Bluetooth codecs pass this band and
  phone microphones record it well; no bass content is needed.
- Period **P = 2 s**. Every click is a **pair** of chirps; the gap between the two
  encodes the click's index in a 5-click cycle: `gap = 40 + 20·k ms`, `k = 0…4`
  (40, 60, 80, 100, 120 ms). The cycle is therefore 10 s and the unambiguous lag
  range ±5 s, and **every** click fixes both the fine phase (its first chirp) and
  the coarse phase (its index), so a 6 s recording with its three clicks is enough.
  (A single marker every 10 s would be missed by 40 % of 6 s recordings.) Gaps
  start at 40 ms so that room echoes of the first chirp, which arrive within the
  first ~20 ms, cannot be mistaken for the second one.
- The track starts with 1 s of silence. Music Assistant plays it once from the
  group's queue (the "clicker", see "Signal path and solo"); 180 s covers a round,
  and the operator starts it again whenever it stopped. Phases are only comparable
  within one playback: a clicker started again in the middle of a round starts the
  round again (every member is measured once more).

## Capture (browser)

- One `AudioContext` for the whole session; capture through an AudioWorklet. Every
  recording is stamped with the worklet's `currentFrame / sampleRate` at its first
  block (the context's clock as seen inside the processor), so all recordings share
  one continuous clock. The phone's input latency is constant and cancels out in
  the differences — as long as the context keeps running: a suspended context
  stops its clock. The context is created after `getUserMedia` (iOS switches the
  hardware rate when the microphone opens) and from a user gesture (iOS refuses to
  start it otherwise); a silent gain node keeps it running between recordings; any
  `statechange` to `suspended`/`interrupted` (page backgrounded, call) invalidates
  the epoch, and the wizard restarts the measurement round.
- The worklet module is a same-origin file bundled by Vite
  (`new URL('./sync-capture.worklet.ts', import.meta.url)`) and loaded through the
  ingress-aware base path, because the CSP is `default-src 'self'; script-src
  'self'` (a `blob:` module would be refused) and under HA ingress the page lives
  below a prefix. This is why the existing per-device calibration still uses a
  ScriptProcessor; the worklet replaces it here.
- `getUserMedia` with `echoCancellation`, `noiseSuppression`, `autoGainControl`
  set to `false`. iOS Safari ignores these for the built-in microphone (voice
  processing stays on); the chirp band and the per-click normalisation tolerate it,
  and the result is simply reported with its confidence.
- 6 s per member at the speaker, 12 s from the listening position.
- Low-pass and decimate by an integer factor to the lowest rate ≥ 16 kHz in the
  worklet (48 kHz → 16 kHz, 44.1 kHz → 22.05 kHz); upload int16 little-endian
  binary (`application/octet-stream`, ~190 kB for 6 s at 16 kHz) with
  `start_time_s`, `sample_rate`, `member` as query parameters. The upload is a raw
  `fetch`, not the generated client, so it sets `X-CSRF-Token` itself
  (`getCsrfToken()`) and uses the ingress-aware `apiUrl()`.

## Estimation

1. Coarse pass: a band-energy envelope (2–6 kHz band-pass, 1 ms frames) over the
   whole recording; frames above 6× the median are click candidates. This is O(N)
   and cheap.
2. Fine pass: the matched filter (chirp template at the recording's rate, 128 taps
   at 16 kHz) runs only inside ±15 ms of each candidate; its envelope peak is the
   chirp's arrival. The second chirp of a pair is the strongest matched-filter peak
   in the windows 40/60/80/100/120 ms (±5 ms) after the first; the window that wins
   gives the click's index `k`, and a first chirp without a credible second one is
   discarded.
3. Each accepted click gets a session time `t = start_time_s + index / sample_rate`
   of its first chirp and a phase in the 10 s cycle
   `φ = (t − 2 s·k − session_epoch) mod 10 s`. Result: the circular mean of the
   clicks' phases; the spread is the circular standard deviation.
4. Accept when at least 2 clicks are found, their phase spread is ≤ 3 ms and the peak
   SNR is ≥ 10 dB; otherwise `not_heard` or `unstable` with the reason.
5. Cost: the envelope is ~100 k frames of simple arithmetic; the matched filter
   touches at most ~500 samples × 128 taps per chirp, under 1 M multiply-adds per
   recording — well under a second in pure Python even on a Raspberry Pi, and
   milliseconds with numpy. (A full-length pure-Python matched filter would be
   ~12 M multiply-adds — several seconds on a PC and tens of seconds on a Pi.)
   Runs in the API worker pool, not on the event loop.

## Planning

- Reference: the first member measured `ok`; the operator does not choose it (results do not depend on it).
- Lag `e_i = φ_i − φ_ref`, unwrapped into (−5 s, +5 s].
- Measurements are taken with the current delays `d_i` in effect. A later player needs
  more delay (more delay plays earlier), so align everyone to the earliest:
  `d_i' = d_i + (e_i − min e)`.
- Normalise: subtract `min(d')` from every value, so the smallest delay is 0 and the
  alignment is unchanged (a common offset shifts every member's play time alike).
  Normalise only when every adjustable member was measured: a skipped or failed
  member keeps its delay, and shifting the others would move them relative to it.
  Round to 1 ms, clamp to 0–5000; a clamped row is marked and the predicted
  residual reported.
- Members that were skipped or failed keep their delay and are listed as unverified.
- Verification: a repeat round after applying. Automatic mode runs it itself and, if
  the residual spread is above 5 ms, applies one refinement (at most two rounds in
  total). The before/after spread is stored in the session history and the profile's
  notes.

## Session

States (UX round): `created → measuring → proposed → applying → tuning → restoring →
done`, plus `cancelled` and `failed` (both go through `restoring`). A manual session
starts in `tuning`. `verifying`, rounds and the undo window are gone.

- **created**: members resolved; snapshot (delays, and which members were muted
  before) taken. Starting a session reads the group and writes nothing to it.
- **measuring**: first the preparation, driven by the operator: stop the group's
  playback, start the clicker (refused with `group_playing` while the group plays
  anything else), set the volumes. The session reads the group's playback every
  2 s (`group_state`, `clicker_playing`, `volumes` in the session resource). Then,
  for each member in the operator's order: `solo(member)`, prompt ("hold the phone
  30–50 cm from Kitchen" or "stay where you are"), receive the recording, estimate.
  Solo and recordings need the clicker playing (`clicker_stopped` otherwise). A
  step can be repeated or skipped; members can be reordered.
- **proposed**: proposal computed. Recordings, skips and proposals are still allowed
  (a member measured again before applying).
- **applying**: `set_delay` for each changed member (all rows validated first, a
  failing write rolls back what this apply wrote).
- **tuning**: the manual editor (solo, A–B, live writes, reset to the delays from
  before); recordings are refused. `POST .../finish` ends it `done` (delays kept);
  `DELETE` ends it `cancelled`.
- **restoring**: the volumes the session changed are put back (while our clicker may
  still play), every member the session muted is unmuted, then the group is
  stopped if this session's clicker is what it plays (music someone started
  meanwhile plays on). Retried with backoff until it succeeds; a member already
  unmuted is not unmuted again. Also triggered by cancel, an error, a 15-minute
  inactivity timeout and, after a bridge restart, by the persisted entry (the
  members the session muted and its clicker URL). Restore never touches the delays
  (applied delays are the point) and does not start the music again.

Only one session per group. A second client sees "tuning in progress" and gets a
read-only view. Manual mode is a session without measuring: the same preparation,
then the clicker, solo and A–B on demand, and delays written live (debounced 300 ms).

## Signal path and solo (decided after stage 0)

The core calls `playback`, `stop_group`, `start_clicker`, `mute`, `set_volume` (and
`set_delay`); everything Music Assistant-specific sits in the adapter.

**Stage 0 findings (Music Assistant dev, 2026-10-16).** `play_announcement` on the
sync group plays the bridge-served click track on every member and Music Assistant
restores music and volumes at the clip's end; `players/cmd/stop` does **not** end an
announcement (it blocks until the clip ends); `players/cmd/volume_mute` is
unsupported on Web Kiosk players (`UnsupportedFeaturedException`, `volume_muted`
null); `players/cmd/volume_set` 0 during the announcement silences a member and Music
Assistant restores the pre-announcement volume at the clip's end, but never a
member's Sendspin protocol player's (E5). Later the same day:
`player_queues/play_media` with the click track's URL on the sync group plays it
(media type `sound_effect`, the file's duration) and `players/cmd/stop` stops it at
once (the group goes idle).

**Decision (2026-10-16, option C; supersedes option A).** Music must not play during
calibration, and the announcement path was too complex. So:

- **Stop the group.** The operator stops the group's playback first
  (`players/cmd/stop` on the group, "Stop group" in the UI); the session refuses to
  start the clicker (`group_playing`) while the group plays anything else.
- **The clicker.** `player_queues/play_media` (`queue_id` = the group, `media` = the
  click-track URL, `option` = `play` since the UX round, was `replace`; the item is
  inserted after the current one and played, the queue kept, and deleted from the
  queue at the end): Music Assistant plays it as a normal item
  on every member. "Playing" means the group's `playback_state` is `playing` and its
  `current_media.uri` contains the click-track URL or its token. Starting it again
  (while it plays, or after it ended) starts it over; in a measuring round that
  starts the round again. The group's queue is replaced; the music does not come
  back by itself.
- **Volumes.** The operator sets each member's volume (`players/cmd/volume_set`)
  until the clicks are clearly audible where they measure. Since the UX round the
  first change of a member records its volume from before (persisted) and the end
  puts it back.
- **Solo by mute, no fallback.** `solo(member)` mutes every other member with
  `players/cmd/volume_mute` (never a member that was muted before the session) and
  unmutes the member if the session had muted it; `solo(None)` unmutes every member
  the session muted. A member whose `supported_features` lacks `volume_mute`, or
  whose mute Music Assistant refuses, fails the solo with `sync_mute_unsupported`
  naming it (problem field `member`); the mutes that solo already sent are taken
  back. There is no volume fallback: a group with a member that cannot be muted can
  only solo that member.
- **Mutes and the operator's own changes.** The session tracks only the members it
  muted. A member that is muted when a solo needs it silent (by the operator, before
  or during the session) is never muted or unmuted by the session; an unmute is sent
  only to a player that reports itself muted, so a member the operator unmuted
  meanwhile gets no extra command. A mute that fails stays tracked (it may have taken
  effect and answered late), unless the player cannot be muted at all. Soloing a
  member the operator muted leaves it muted (it is then "not heard"); a member the
  operator unmuted mid-session is muted again by the next solo.
- **Stop.** "Stop group" in the preparation stops whatever the group plays (`signal`
  `stop`); "Stop clicks" in the Manual tab and every end stop the group only while it
  plays the session's click track (`signal` `stop_clicker`, the restore), so music
  someone started meanwhile — or a quick listen refused with `group_playing` — plays
  on.
- **One URL per session.** The click-track URL (its token) stays the same for the
  session and is recorded (and persisted) before `play_media` is sent, so a start that
  times out but plays is still stopped at the end, and starting it again never
  invalidates the URL Music Assistant is streaming.
- **The end.** Finish, cancel, an error, the inactivity timeout and shutdown unmute
  every member the session muted that is still muted, then stop the group only if the
  session's clicker is what it plays. A player Music Assistant does not list (after a
  restart, before its daemon registers again) keeps its unmute owed and is retried
  with backoff; 10 minutes after the restore began it is given up and the restore
  finishes. The members the session muted (added before the mute is sent)
  and its clicker URL are persisted, so a bridge restart unmutes them and stops the
  click track if the group still plays it.

Option A (one announcement per round, solo and stop by volume, the restore waiting
for the clip's end, "music returns in N s") was implemented and dropped: it left
the speakers silent for up to three minutes after every session and needed the
protocol players' volumes fixed by hand.

Candidates the spike evaluated on Music Assistant dev:

- **(a) Announcement + mute after start.** `play_announcement(url,
  pre_announce=false)` on the group player, sent without waiting for the reply;
  Music Assistant pauses and resumes the current playback and streams the clip to
  the group in sync. Solo mutes the other members with `players/cmd/volume_mute`
  after the group reports playing (announcements lift mutes only at the start).
  Open question for the spike: whether a mute set mid-announcement is honoured by
  the stream (by code reading it is, and the end-of-announcement restore leaves it
  in place, so the adapter unmutes). `stop_signal` is `players/cmd/stop` on the
  group.
- **(b) Queue playback + mute.** Play the track through the group's queue; the
  adapter snapshots and restores queue items, index, position, shuffle and repeat.
  Fragile with large queues.
- **(c) Hybrid.** Bridge players are soloed by the bridge itself (the existing
  `set_device_mute` use case: sink mute plus the `set_mute` IPC, no Music
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
- `POST /api/v1/sync/sessions` `{group_id, mode: manual|semi, point:
  device|position}` → session; `GET`/`DELETE /api/v1/sync/sessions/{id}`.
- `POST /api/v1/sync/sessions/{id}/signal` `{action: start|stop|stop_clicker}`
  (start: the clicker on the group; stop: stop whatever the group plays;
  stop_clicker: stop it only while it plays the session's click track);
  `PUT .../volumes/{member}` `{level: 0–100}`; `POST .../solo` `{member | null}`;
  `POST .../ab` `{a, b}`.
- `POST /api/v1/sync/sessions/{id}/recordings?member=…&start_time_s=…&sample_rate=…`
  (binary body) → measurement.
- `GET .../proposal` (read-only: the current proposal, or `no_measurement`);
  `POST .../proposal` computes it (a write: auth + CSRF); `POST .../apply`
  `{rows?}` (then `tuning`); `POST .../finish`. (`undo` and `verify` removed.)
- Phone capture: `POST .../capture-link` `{lang?}` → `{url, token, expires_in_s}`;
  `POST .../sequence` `{members}` (202); public with the capture token only:
  `GET /api/v1/sync/capture/{token}` and `POST /api/v1/sync/capture/{token}/recording`
  (any audio file); the page `GET /sync/capture/{token}`.
- `GET/POST /api/v1/sync/profiles?group_id=…`; `PATCH`/`DELETE
  /api/v1/sync/profiles/{id}`; `POST /api/v1/sync/profiles/{id}/apply`;
  `POST /api/v1/sync/groups/{group_id}/capture` `{name, kind}` → profile from the
  current delays.
- Session state changes are pushed as `sync` events on the existing event stream
  (`/api/v1/events`), so the wizard does not poll. `sync` is a new event type: it
  joins `ALL_TYPES` and `DEFAULT_TYPES` in the event hub (`_parse_types` answers
  400 to unknown types) and the `EventEnvelope['type']` union of the SPA; the
  wizard subscribes with `types=status,job,sync`. The payload is the session
  resource as `GET /api/v1/sync/sessions/{id}` returns it.
- `GET /api/v1/sync/groups` is fed by the sync groups the bridge already caches
  (`GET /api/v1/music-assistant/groups`), enriched per member.

Error codes: `ma_unavailable`, `ma_forbidden` (the token lacks a required scope,
typically `CONFIG_PLAYERS_WRITE`), `session_active`, `session_expired`,
`member_gone`, `not_adjustable`, `recording_invalid`, `restore_pending`, and for the
signal path `group_playing` (stop the group before the clicker), `clicker_stopped`
(solo or a recording without the clicker) and `sync_mute_unsupported` (with
`member`: the player that cannot be muted).

## UX

Entry points: Groups page → group card → **Sync**; for a bridge player, also its
drawer's Timing tab. Route `/sync/:groupId`, designed for a phone in portrait.

**Group screen**
- Members: name, origin badge (bridge / Sendspin / cannot be adjusted), current
  delay, last measurement and its age.
- Profiles: list, **Apply**, **Save current as…**, rename, delete. Applying shows the
  diff first and skips members no longer in the group (listed).
- Actions (UX round): **Manual tuning**, **Measure with a microphone**, each with a
  "when to choose" line; warnings before anything is touched (cannot be muted,
  unavailable, volume 0 %, muted) with Skip / Manual tuning.

**Manual tuning**
- Per member: slider 0–5000, −10/−1/+1/+10 buttons, number field; applied live.
- First the same preparation as the wizard (stop the group, start the clicker, set
  the volumes); skipped when the clicker already plays.
- Listening bar: **Play clicks** (the clicker) and **Stop clicks** (stops the
  group), **Solo** per member, **A/B** (two members alternate every two clicks).
- **Reset to session start**; on leaving, offer to save as a profile.

**Measurement wizard**
1. Preparation: nothing plays while measuring (the group's queue is replaced by the
   click track; the music does not come back by itself); measuring point (at each
   speaker — default; from the listening position); microphone permission; tip to
   hold the phone still.
2. Stop playback: the group's live state and **Stop group**; Next once the group no
   longer plays.
3. Start the clicker: **Start the clicker**; Next once the bridge sees it playing.
4. Set volume: a volume slider per member with "clicks should be clearly audible
   where you measure", and the microphone's level meter (too quiet / too loud).
5. Member by member: prompt, solo, 6 s (12 s) recording with progress, result
   (✓ with confidence, or not heard / unstable with **Repeat** / **Skip**). Steps
   can be redone; members reordered. When the clicker stopped: **Start the clicker
   again** (every member is redone). A member that cannot be muted is named inline.
6. (UX round) Tune: the measured delays are applied; the manual editor with
   "Measured: X ms (+Δ)" and "Reset to measured" per row; **Finish**, **Finish and
   save as profile…**, **Cancel** (puts back the delays from before).
7. Done: volumes back, speakers unmuted, the clicks stopped; optional quick listen
   with clicks on the whole group for 10 s.
   (The steps are grouped into four stages: Prepare · Measure · Tune · Done.)

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

- `SYNC_PROFILES` in `config.json`: list of profile objects (see Models). Registered
  in `DEFAULT_CONFIG` (`[]`), `config/schema.json` and the HA option translation's
  preserved keys (see Bridge adapters); migration not needed (absent = empty list).
- `sync_snapshot.json` in the config directory while a session has something to put
  back (members it muted, its clicker URL); removed after a successful restore.
- Recordings are kept in memory for the session only, never written to disk.

## Security

- `/api/v1/sync/*` uses the bridge's normal authentication and CSRF rules, with one
  exception: `GET /api/v1/sync/signal/{token}.wav` is public (Music Assistant fetches
  it without credentials). It answers only while a session owns the token, with a
  fixed body that contains no user data; the token is 32 random bytes, compared in
  constant time, and a wrong token is a 404.
- Writing player config needs a Music Assistant token whose role carries
  `CONFIG_PLAYERS_WRITE` (the administrator role; which other roles have it is
  checked in stage 0 against the running server). The bridge never stores Music
  Assistant credentials beyond the token it already keeps, and a permission error
  surfaces as `ma_forbidden` with the proposal still shown.

## Testing

- **Core**: synthetic recordings (offsets, noise, room echo, band-limiting, AGC,
  dropped clicks, phase wrap at the cycle edge); property tests for the planner
  (non-negative, alignment preserved, normalised minimum 0, clamping reported);
  `SyncSession` against a fake `GroupControl`, including failure and restore paths;
  the import-boundary test.
- **Bridge**: adapter tests against a fake Music Assistant client (including the
  universal-player key form and a member without the delay entry); HTTP contract
  tests validated against `sync-contract-v1.json`; the signal route answers
  without a session and 404s a wrong token; `sync` events reach a listener;
  profile persistence, schema and HA option translation; snapshot restore after
  restart; the packaging test for the `sendspin_sync` wheel entry.
- **UI**: vitest for each wizard step with a fake API, capture with a fake
  AudioWorklet, manual mode debounce, profiles.
- **Live**: test stand (Music Assistant dev on HAOS, bridge local outputs, web
  kiosk): add a known +120 ms to one member and confirm the wizard brings it back.

## Stages

0. **Spike** (throwaway) on Music Assistant dev: signal path and solo (a/b/c), live
   config write to a bridge player and the web kiosk, restore, which token roles
   may write player config, estimator accuracy on real phone recordings. Output:
   decision note appended to this spec.
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

- Music Assistant changes the queue, mute or config behaviour this relies on (dev
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

## Review notes (2026-10-15)

Checked against this repository, Music Assistant dev `d3258305` (2026-10-15) and
aiosendspin 10.0.2. Decisions in the table above are unchanged. Changes:

- **Adjustable members.** The delay entry exists only for clients that declare
  `set_output_delay` in `client/state` (`_OUTPUT_DELAY_COMMANDS`), not for every
  Sendspin player; `adjustable` is now read from `config/players/get_entries`.
  Protocol players are hidden from `players/all` unless `return_protocol_players`
  is set, and a universal player's Sendspin child is found in `output_protocols[]`.
- **Announcement path.** Sendspin players announce natively only via a Home
  Assistant relay, so a sync group plays the clip through Music Assistant's default
  implementation; the command answers only after the clip ends (so it cannot go
  through `MaMonitor.execute_cmd`'s 3 s timeout), `pre_announce` must be forced off,
  `players/cmd/stop` ends it, and Music Assistant's restore re-mutes only members
  muted beforehand — the adapter unmutes what it muted. Clip limit 300 s.
- **Signal.** A marker every 10 s is absent from 40 % of 6 s recordings. Every
  click is now a chirp pair whose gap (40–120 ms) encodes its index in the 10 s
  cycle, so each click resolves the coarse phase; estimation steps rewritten.
- **Estimator cost.** A full-length pure-Python matched filter is ~12 M
  multiply-adds (tens of seconds on a Pi, and numpy ships nowhere); replaced by an
  O(N) band-energy pass plus matched filtering around candidates.
- **Click-track URL.** It is fetched by Music Assistant, not by the bridge, so the
  "artwork proxy address rules" did not apply: the route is public (the existing
  `tone.wav` sits behind authentication), the URL uses the bridge's LAN address and
  web port, and the token is session-scoped rather than single-use because the
  renderer may fetch twice.
- **Capture.** Worklet timing uses `currentFrame / sampleRate`; the module must be
  a same-origin, ingress-prefixed file under the `script-src 'self'` CSP; a
  suspended context stops the shared clock (new epoch); iOS ignores the
  voice-processing constraints and needs a user gesture; decimation is by an
  integer factor (44.1 kHz → 22.05 kHz); the binary upload sets the CSRF header
  itself.
- **Persistence and events.** `SYNC_PROFILES` must be added to `DEFAULT_CONFIG`
  (the key allowlist), `schema.json` (`additionalProperties: false`) and the
  translation script's preserved-key tuple — none of this is automatic. `sync` is a
  new event type that the hub and the SPA type union must learn.
- **Packaging.** `sendspin_sync` must be listed in the hatch wheel `packages`;
  Docker, LXC, tarball and the HA add-on all derive from that list.
- **Planner.** Normalisation only when every adjustable member was measured, so a
  skipped member is not moved relative to the others. Sign convention verified
  (`compute_play_time = client_time − output_delay`).
- **Security.** "Admin rights" made precise: scope `CONFIG_PLAYERS_WRITE`; which
  roles carry it could not be verified offline (music-assistant-models 1.1.224 is
  not available locally) and is on the stage 0 list.
- **Scope note.** v1 groups are Music Assistant's persistent sync groups, as the
  Groups page lists them.
- **Reference speaker.** The wizard has no reference choice: the planner's result is independent of the reference, so the line "the operator can change it" was dropped.
- **Stage 0 (2026-10-16): signal and solo, option A.** `players/cmd/stop` does not end
  an announcement and web kiosks refuse `volume_mute`, so mute-based solo and
  stop-the-group are dropped: one announcement per round, solo and stop by volume
  (0 / snapshot level), restore only after the clip ended (then check the volumes),
  a check round waits for the previous clip, a new session waits with
  `signal_playing`, and the UI says when the music returns ("Signal path and solo").
  The persisted snapshot carries the clip's wall-clock end so a restart waits too.
- **Option A review (2026-10-16).** Solo, stop and restore set the Sendspin protocol
  player's volume too (E5); a clip that ends mid-session gets the volume check at
  once; the undo window survives a check clip that fails to start; the clip's end
  waits for Music Assistant's announcement (bounded) and the volumes are checked
  again 5 s after a restore; a shutdown mid-clip hands the levels back.
- **Option C (2026-10-16, user decision; supersedes option A).** No music during a
  session: the operator stops the group, starts the clicker (`play_media` with
  `option=replace` on the group's queue) and sets the volumes; solo mutes the other
  members and a member without mute is a visible `sync_mute_unsupported` error (no
  volume fallback); the end unmutes what the session muted and stops our clicker.
  Removed with option A: the announcement, the volume snapshot/restore (members and
  protocol players), the clip clock and settle time, `signal_waiting`,
  `music_returns_in_s`, the post-restore recheck, the restart restore that waited
  for the clip, `announcement_active` and `signal_playing`. The auto-mode undo
  window is a plain 30 s from the apply again.
- **Option C review (2026-10-16).** A quick listen ends through the session only (no
  unconditional stop); "Stop clicks" is `stop_clicker`; a failed mute stays tracked
  unless the player has no mute; an unmute goes to any player reporting itself muted,
  and an absent player is retried for up to 10 minutes; the clicker URL is one per
  session and recorded before `play_media`; the live uri form
  `builtin://sound_effect/<URL>` is matched; `MaApi.fire` is gone.
