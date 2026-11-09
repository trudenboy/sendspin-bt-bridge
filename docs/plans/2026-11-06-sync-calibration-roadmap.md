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

## Released in 3.2.0-beta.25 (`fix/sync-measurement-round`)

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

Also released in beta.25: `refactor/status-read-model` (one read model for every health surface,
link-state driven device status in the UI, the Microphone hand-off owner with a card seam).

## Released in 3.2.0-beta.26 (`fix/sync-phase0`)

| Id | Implementation and checks |
|---|---|
| D-M3 | Saved `PULSE_LATENCY_MSEC` replaces the inherited startup value in each daemon's environment; spawn regression test. Minimum is 1 ms in model, schema and UI. |
| D-M8 | Diagnostics finds compensation by Bluetooth address, then name (including bridge-suffixed names); regression tests. The normalised state still lacks a config id, so a fully id-keyed projection belongs to Phase 2. |
| D-M4 | Local outputs persist compensation and buffer settings by config id; HTTP PUT and file reload tests. |
| A14 | Per-click shape normalisation removes DC and low-frequency hum; quiet-click tests with DC and 50 Hz hum. |
| A2 | A `sequence.cut()` module owns alignment plus windows: event-based alignment first, matched-filter envelope fallback for noisy recordings. Block FFT keeps memory bounded. Regression fixtures include noise 0.05–0.2, a silent last member and pre-sequence clicks; pure noise and ambiguous fits are refused. Hardware recordings still need validation. |
| A9 (partial) | Click times feed a pooled clock-rate fit; at least two windows with three precise clicks each must agree within 40 ppm. Implausible fits above 500 ppm are ignored. Synthetic −150/+100 ppm recordings, wraparound and a single moving speaker are tested. Coherent-only and browser-live measurements remain uncorrected; fitted ppm is not yet published. |
| A11 | Beds have session owners (shared sinks stay open), released through the store's runtime-resource cleanup on restoring or any terminal state. Process shutdown is off the session lock. Finish, timeout and shared-owner tests. |
| D-M7 | All ten locales explain live persistence, including tab close; Finish tuning replaces Save, and Discard retains explicit rollback. Existing cancellation semantics are preserved. |
| C-D9 | A new unfragmented stream is identified as SBC only after parsing a frame, not from its sync byte alone. Already known SBC streams retain damaged-frame accounting. Both decoded and undecoded paths are tested. |
| C-D15 | Default work budget is 500 frames/s, enough for 12-block/8-subband SBC at 48 kHz; smaller, high-cost configurations remain refused. |
| C-D10 | Receive buffer covers the full monitor payload; `MSG_TRUNC` is discarded and counted in loss telemetry so the next record invalidates consumers' continuity. |

## Released in 3.2.0-beta.27 (`fix/sync-capture-and-config-revisions`)

| Id | Implementation and checks |
|---|---|
| A12 | The recording worker owns the per-session lock until it exits. The global decode gate covers decode and estimation only, not capture or waiting for the sequence. Session resource cleanup stops the live recorder; sequence waits check cancellation every 100 ms. Fixed-duration legacy requests also observe cancellation, and concurrent stop calls reap the recorder once. Tests cover startup cancellation, gate availability during capture, resource detachment and real child-process termination. |
| D-C1 follow-up | GET config supplies a player revision in `_players_revision` and ETag. A full save may round-trip the field or send If-Match; the revision is compared under the commit lock and stale requests fail with 412 config_conflict before writes or reconfiguration. Clients without a revision keep current timing on existing players. Settings-only saves take the latest players under that lock, including players added during validation. Imports remain explicit replacement operations. Conflict messages are translated in all ten locales. |

The revision covers persisted player settings, not volume caches, secret fields or read-only runtime
annotations. Current callers that round-trip GET config already carry the revision. A legacy caller
that intentionally changes existing player timing must fetch a revision or use the dedicated timing
endpoint; omission is no longer treated as permission to replace runtime-owned timing.

### Remaining checks for this phase

* Validate recorder cancellation on both `parecord` (HAOS) and `pw-record` (PipeWire).
* The per-session resource owner is process-local; a process crash still relies on the child's
  process-exit behaviour and the existing persisted group-restore mechanism.


## Phase 1 — finish the Measurement round (candidate #1)

The round now has one clock and a fresh proposal. What is left makes the measurement itself
more robust and closes the loop.

1. **A2 — validate real-room recordings.** The synthetic noise fixtures now pass through the
   matched-filter fallback. Validate speech, reverberation and impulsive room sounds on real
   recordings, including a silent first/last member and the shorter position-mode gaps.
2. **A9 — finish recorder clock drift.** The precise per-click path now supplies a consensus
   fit. Extend it to coherent-only and browser-live measurements, and publish the fitted ppm.
3. **Phone live mode prior.** The phone page starts its recorder and then asks for the sequence;
   stamp that request on the session clock and pass the same `recorder_started_at` window the
   bridge input uses. Recorder (hand-started) mode keeps the unconstrained search.
4. **A16 — measure only settled speakers.** Gate a member's solo on its playout being locked and
   past its warm-up (the first steady jump lands ~10 s into a stream); otherwise the start
   transient is baked into its delay.
5. **Verification — implemented.** The Tune step starts a fresh phone/bridge-input recording round,
   reports measured residual spread and leaves compensation unchanged. A partial check cannot pass;
   the original proposal prediction remains explicitly labelled. Remaining: settled-playout gating
   and acoustic validation during warm-up and reconnection.
6. **A15, A17.** Refuse a non-adjustable reference up front; `record()` checks that the member
   was the one soloed.
7. **One definition of sequence timing.** Solo, gap, lead and settle times live in Python and are
   re-implemented in the UI (6/12 s vs 7/13 s) and the phone page; publish them with the session
   and drop the copies.
8. **Split `MeasureWizard.vue`** (~1080 lines of script): flow state machine, recorder adapter,
   proposal/apply controller.

## Phase 2 — Speaker timing: one writer (candidate #2)

**Released in beta.27:** `SpeakerTiming` owns runtime timing application,
explicit persistence and provenance for HTTP/HA/initialization; server observations use the
same owner without echoing IPC. Config reconfiguration delegates runtime commands to it and
full saves/imports respect the active-session fence before writing. Compensation writes are
serialized with generation checks so an older debounce cannot replace a newer command, including
a configuration commit. The protocol-facing clamps remain defensive checks in the adapters.
Tests cover IPC failure, non-finite values, HA provenance, active-session rejection and racing
server observations. External MA edits are re-read before propose/apply and invalidate the round
rather than planning against an old delay. HTTP provenance is now a finite set of supported sources.

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

**Implemented:** recommendation and initialization share one context policy. Grouped, manually
confirmed, MA-configured or air-targeted speakers do not receive absolute Bluetooth suggestions.
Unknown group context is not treated as standalone: automatic suggestions wait for an MA player
mapping and connected account. Air targets include the current matching learned target. An explicit
manual change remains available; a stale recommendation cannot bypass this policy through HTTP.

**Post-Apply verification implemented:** a new recording round measures residual spread, keeps
the applied values and original proposal, and returns to listening. Phone and bridge-input adapters
are exposed in the Tune screen. Required members are fixed at verification start; partial recordings,
new skips and delays changed during the round cannot pass. Manual adjustment or air calibration
invalidates the old verification result. The Tune/Done screens distinguish measured spread from
predicted residual. This is not yet the dynamic settled-playout gate described in Phase 1 item 4;
real-room and warm-up validation remains required.

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

Next: the settled-playout gate for measurement/verification, trusted-reference checks before saving
absolute air-to-sound calibration, and Phase 4 (learned target confidence and bounded first jumps).
The common timing owner and post-Apply verification are implemented above. Validate A2 against real
room recordings while finishing A9's coherent/live path and the phone's clock prior. Then Phase 5.
Phases 6 and 7 wait for their hardware measurements. Phase 8 follows whichever area is touched next.
