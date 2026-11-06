# Speaker calibration and sync — roadmap after the 2026-11-06 audit

* **Status:** active
* **Source:** architecture and defect audit of group sync tuning, the playout engine, the air
  sensor and the timing model (four independent passes; critical findings re-verified, the
  alignment defect reproduced).
* **Vocabulary:** domain terms from `CONTEXT.md` (Speaker latency compensation, Click track,
  Sequence, Measurement round, Proposal, Listening device, Player); architecture terms from the
  codebase-design glossary (module, interface, depth, seam, adapter, locality, leverage).

Defect ids refer to the audit: **A** group sync tuning, **B** playout engine, **C** air sensor,
**D** timing model and UI. "Confirmed" means the code path was traced; "runtime" means the size
of the effect needs a measurement on real speakers before a fix is designed.

## Done (branch `fix/sync-measurement-round`)

| Id | What was wrong | What changed |
|---|---|---|
| A1 critical | A silent last member made the slot pattern shifted one member earlier win: b got a's phase, c got b's, and wrong delays were written | Alignment searches only offsets at which the sequence lies in the file; equal fits a member apart are refused; a known recorder start narrows it |
| D-C1 critical | A Settings save sent the `players` list read when the page opened, putting back an old latency compensation and dropping air calibrations and learned latencies | Settings leaves `players` out; the bridge keeps its players when a save does not mention them (pinned by a test) |
| A3 | A re-measure or skip after a proposal left the proposal in place; Apply wrote delays planned from older phases | Any change to the measurements withdraws the proposal |
| A4 | The 180 s click track ran out part-way through a sequence started late, and a long sequence could never fit | `needed_s()` counts Music Assistant's steps; the clicks start over when too few are left; too long is refused (`sequence_too_long`) |
| A6 | The bridge input recorded for the sequence's nominal length after the sequence had started, so the last solo was cut | The bridge opens the recorder, plays the sequence, stops after it has finished, and uses the recorder's start to align |
| A10, A13 | A microphone recording after a sequence mixed two clocks; a worse re-recording replaced a better one | One clock per round; a worse recording of the same sequence leaves the better one |

ADR-0005 is amended (§4 replaced, §3 limited to a constant latency). `CONTEXT.md` gains a Group
sync section.

Also unmerged: `refactor/status-read-model` (one read model for every health surface, link-state
driven device status in the UI, the Microphone hand-off owner with a card seam).

## Phase 0 — point fixes (hours each, no design)

| Id | Problem | Fix | Test |
|---|---|---|---|
| D-M3 | `PULSE_LATENCY_MSEC` saved in Settings is "applied" but daemons respawn with the value from bridge start (`os.environ` is set once at boot) | Pass it as a spawn parameter like `compensate_output_latency`, or refresh the environment in the global-restart action | A respawned daemon receives the saved value |
| D-M8 | The latency assistant looks configured delays up by player name; runtime names carry " @ bridge", so it never finds one and advises a bigger PulseAudio buffer | Key by player id | Assistant with suffixed runtime names sees the delays |
| D-M4 | The Timing panel saves a local output's latency compensation only for Bluetooth (needs a MAC); lost on restart | Persist by config id for every backend | PUT then reload config for a `pulse_sink` player |
| D-M7 | Manual tuning says "Save keeps them", but edits are written to Music Assistant and persisted live; cancel or closing the tab keeps them | Either restore delays on cancel/pagehide of a manual session, or reword to "applied live; Discard puts back" | Cancel restores (or the text no longer promises otherwise) |
| A11 | Keep-awake beds are one global set: finish, timeout and failure leave `pacat` running; one group's sync stops another's | Tie beds to the session (create/end with it) | Two sessions; beds stopped on each end path |
| A12 | One global decode gate is held for a whole bridge-input recording (up to minutes): other groups cannot measure; cancel does not stop the recorder | Per-session recorder lock; cancel stops the recorder | Cancel during recording ends the child |
| A14 | The per-click correlation normalises by unfiltered energy; DC offset or rumble rejects good chirps | Normalise on the comb-filtered signal | Estimator with DC offset and 50 Hz hum |
| C-D9 | An AAC payload with 0x9C at byte 1 is taken for SBC; the codec state flickers | Require a valid CRC on the first frame | Synthetic AAC packet |
| C-D15 | SBC with 12 blocks is refused (`max_frames_per_s=400`) | Derive the bound from blocks | 12-block stream accepted |
| C-D10 | Oversized monitor datagrams are dropped uncounted (`MSG_TRUNC` ignored) | Count truncation; 64 KiB buffer | Truncated datagram counted |
| — | `PULSE_LATENCY_MSEC` minimum is 0 in schema/model/UI but 1 on save | One bound | Schema and save agree |
| D-C1 follow-up | Only the Settings page is fixed; any other client sending a full document can still put back runtime-owned player keys | The server keeps `static_delay_*` and `air_*` keys of existing players unless the save carries the config revision it was based on (If-Match) | Stale full save leaves runtime keys; current one may change them |

## Phase 1 — finish the Measurement round (candidate #1)

The round now has one clock and a fresh proposal. What is left makes the measurement itself
more robust and closes the loop.

1. **A2 — alignment as sensitive as the estimator.** Alignment needs a frame ~15 dB above the
   median; the estimator measures rooms far noisier (#553), so phone and input paths report "not
   heard" where a measurement is possible. Align on a soft click-band envelope correlated against
   the slot mask, or on a coherent fold of the whole file. *Test:* the sequence fixture at noise
   0.05–0.2 aligns and measures every member.
2. **A9 — recorder clock drift.** Fit one rate correction from all click times in the file (the
   period is exactly 2 s) before estimating; report the fitted ppm. *Test:* fixture recorded at
   ±100 ppm measures within 1 ms.
3. **Phone live mode prior.** The phone page starts its recorder and then asks for the sequence;
   stamp that request on the session clock and pass the same `recorder_started_at` window the
   bridge input uses. Recorder (hand-started) mode keeps the unconstrained search.
4. **A16 — measure only settled speakers.** Gate a member's solo on its playout being locked and
   past its warm-up (the first steady jump lands ~10 s into a stream); otherwise the start
   transient is baked into its delay.
5. **Verification.** After Apply, play one short sequence and measure the residual spread; show
   the measured value instead of `predicted_spread_ms` (arithmetic today).
6. **A15, A17.** Refuse a non-adjustable reference up front; `record()` checks that the member
   was the one soloed.
7. **One definition of sequence timing.** Solo, gap, lead and settle times live in Python and are
   re-implemented in the UI (6/12 s vs 7/13 s) and the phone page; publish them with the session
   and drop the copies.
8. **Split `MeasureWizard.vue`** (~1080 lines of script): flow state machine, recorder adapter,
   proposal/apply controller.

## Phase 2 — Speaker timing: one writer (candidate #2)

Eight places write a speaker's latency compensation (Timing panel, Home Assistant, Settings,
auto-fill, Music Assistant mirror, wizard and manual tuning, migration, environment), with four
persistence paths and ~14 copies of the 0–5000 clamp.

* One module per speaker owns writes: clamp, provenance, persistence by config id, the live IPC
  call, and a lock while the speaker's group is being tuned.
* **A5:** writes to a member of a group being tuned are refused (or queued), and the planner
  re-reads delays in `propose()`/`apply()` instead of using the values read at session start.
* Home Assistant writes record provenance; the PUT's `source` becomes an enum.
* *Tests:* every writer goes through the module; a write during a session is refused; a local
  output's value survives a restart.

## Phase 3 — Timing model: one meaning of a delay (candidate #3)

The planner treats delays as relative (earliest = 0), the suggestion and auto-fill as absolute
(the A2DP delay report), the air calibration as absolute against a reference nobody checks.

* A pure module answers, from compensation, air calibration at its reference delay, learned
  latency, reported delay, output-latency compensation and group membership: the expected heard
  offset; whether a suggestion or auto-fill is valid; whether a reference may anchor an absolute
  calibration.
* **D-M1:** no suggestion when an air calibration exists for the current key or the speaker was
  tuned with its group; drop the branch for `microphone_calibration`/`manual_calibration`
  sources nothing writes any more.
* **D-M2:** no auto-fill for a new member of a tuned group (or start from the group's median).
* **A7:** an absolute air-to-sound latency only against a reference whose schedule error is
  known; otherwise label it relative and store the reference's identity, not its name.
* **A8 (runtime):** saving an air calibration after Apply retargets the air correction; offer
  it before Apply or ask for a re-measure; fix the `d_ref` fallback that reads the post-apply
  delay.
* **C-D13:** one air error (the snapshot and the jump veto use different formulas).
* *Test:* the identity "calibrate, then E = A" holds through the wizard and the air bias.

## Phase 4 — Air target (candidate #4)

* **C-D2:** a learned target needs three samples, or two that agree within the band; the median
  fallback only from three. The test that pins averaging two disagreeing samples changes with it.
* **C-D5:** bound the first jump from a learned or calibrated target (against the port error and
  the target's scatter); seed from non-overlapping windows.
* **C-D1, C-D11:** add the timestamp kind (kernel vs receive) and the audio server to the
  calibration and learned keys; restart learned samples after k disagreeing starts; age limit.
  Stamp receive-time records before the 20 ms batch sleep.

## Phase 5 — Playout correction policy (candidate #5)

* **B-D6:** the air veto compares the jump's sign with the air error and ignores air readings
  older than the last placement or event.
* **B-D7:** decide (controller proposal + veto + rate gate) before the controller resets; a
  vetoed jump neither resets the window nor marks the speaker degraded.
* **B-D4, B-D5 (opt-in rate mode):** rate is zero on every early return; inserted frames repeat
  or interpolate instead of zeros.
* **B-D8, B-D10, B-D11:** a rebase keeps the audio as documented; a delay change does not drop a
  decided jump; published p95 and error share one domain.
* A residual-domain module (baseline, bias, published EMA, placement offset — ~12 sites today)
  makes these one-place changes.

## Phase 6 — Port timeline (candidate #6; measure first)

* **Runtime:** measure how long `pa_stream_get_time` stays at 0 after the first write, on
  PulseAudio (HAOS) and PipeWire (VM 105), with and without pre-open.
* **B-D1, B-D2:** an explicit "port started" state (N advancing readings, or seeded with the
  sink latency); no sensing before format and first block; the first stream after a pre-open
  is a start, not a stall.
* **B-D3 (confirmed, can go earlier):** keep the format per chunk; switch at the write head
  instead of dropping the queue on a format change.
* `SimulatedPort` gains a start delay so tests see it.

## Phase 7 — Air sensor seam (candidate #7)

* A replay adapter at the monitor hub's seam (today btsnoop replay skips the clock mapping,
  pending queue and receive-stamp fallback).
* Emit packets at send time; completion crediting becomes telemetry (removes the up-to-1 s hold,
  C-D7, C-D8).
* **C-D4 (runtime):** check acoustically whether the send anchor follows host send lateness under
  congestion; if not, latch the anchor per epoch.
* **C-D6:** extrapolate the clock map for one tick instead of falling back to receive stamps.

## Phase 8 — Structure and leftovers

* **Candidate #8:** session-scoped resources (beds, recorder, gates); move the air calibration
  (~450 lines) out of `application/sync_tuning.py` and feed it the session's timeline events
  instead of polling `session.sequence`.
* **Candidate #9:** delete the per-speaker calibration path (`application/calibration.py`
  sessions and metronome, `estimate_relative_delay_ms`, `CalibrationMetronome` and its client
  hooks, the four routes, the unused UI registry slider). Keep the test tone.
* **Candidate #10:** one UI adapter for timing values (names from the glossary, one sign
  convention, one threshold table) used by the chip, the Timing panel and the delay rows; show
  where an air calibration is saved without "technical details".

## Validation on hardware

Needed before or after the phases marked *runtime*:

1. Bridge-input measurement on HAOS (PulseAudio, `parecord`) and VM 105 (PipeWire,
   `pw-record`): recorder start-up time stays under `RECORDER_START_TIMEOUT_S` (3 s); the whole
   sequence is in the file; a muted last speaker reads not heard and the others keep their own
   phases.
2. Port start latency with and without pre-open (Phase 6).
3. Send-anchor behaviour under link congestion against a microphone (Phase 7).
4. Whether the keep-awake bed (~31 Hz, about −46 dBFS RMS) actually keeps sleepy amplifiers awake.

## Order

Phase 0 and Phase 1 items 1–3 first: they decide whether a measurement is right at all. Then
Phase 2 with Phase 3 (where mechanisms collide and compensate twice), Phase 4, Phase 5. Phases
6 and 7 wait for their hardware measurements. Phase 8 follows whichever area is touched next.
