# Air sensor stage B1: the engine corrects placement from the air — design

Status: draft for review, rev. 3 (2026-10-29). Rev. 2 applied the controller's round-2 rulings on
rev. 1's open questions; rev. 3 applies the pre-flight review (rulings C1, I1–I10 and the minor
fixes). Target: the v3 line (beta channel), branch `feat/air-sensor`, after stage A (validated
2026-10-28; stage A is not released yet).
Builds on [`2026-10-28-air-sensor-design.md`](2026-10-28-air-sensor-design.md) ("the stage A spec",
in particular its "Stage A validation note" and "Stage B (outline)") and on
[`2026-10-16-bt-playout-sync-design.md`](2026-10-16-bt-playout-sync-design.md) ("the playout spec":
R14, the baseline, Stage 3/4).
Plan: [`2026-10-28-air-sensor-stage-b-plan.md`](2026-10-28-air-sensor-stage-b-plan.md).

The maintainer's decisions of 2026-10-28 (rounds Q1–Q12) and the controller's rulings are binding and
quoted as **Qn**, **round 2 (n)** and **review (Cn/In)**. Rulings this design makes on its own are
marked **Ruling S-n** and collected in "Rulings" at the end, so a reviewer can accept or overturn
each one separately. File and line pointers refer to the branch at `7cc9df99`; paths are under
`src/sendspin_bridge/` unless they start with `tests/`, `ui/` or `docs/`.

## Goal and decisions

Stage A showed that the air sensor sees where a speaker's audio really leaves over the air, to
0.034 ms (p95) against an independent offline anchor, with σ(H) 0.2–0.9 ms within a stream. In
`measure` mode the start-to-start offset between two group members is still 9–60 ms (audible): the
pulsesink sensor of the playout engine cannot see where a new PulseAudio stream lands in a Bluetooth
stream that never stops, and its 20 ms dead band leaves the rest alone.

Stage **B1** closes the placement part of that gap: in `PLAYOUT_SYNC=loop` the engine learns, per
output, the **bias** between what the pulsesink clock says and what the air says, and places audio
so that the air-derived error is on its target. The rate loop (PI + resampler, the playout plan's
3.1–3.4) is **B2** and not part of this design.

| Q | Decision (binding) |
|---|---|
| Q1 | Scope B1: placement correction from the air sensor only (bias-aware jumps and placements). The rate loop is deferred to B2. |
| Q2 | Lives in `PLAYOUT_SYNC=loop` (loop = measure + air correction). The air sensor runs in `loop`; `loop_not_supported_yet` and the measure-only gate in the daemon go. `measure` stays observe-only. |
| Q3 | Target: calibrated → `E = 0` (at the calibration's delay, round 2 (1)). Uncalibrated → the learned target (Q7). No data → `loop` behaves as today (`measure` semantics with `J = 10 ms`). |
| Q4 | A slow bias filter `b = e_pulse − E_equiv`, seeded per output from the median of the first three confirmed windows. The controller input and every placement target use `e_pulse − b`. Thread-safe handoff sensor (daemon loop) → engine (feeder thread) through a lock-protected shared object; `e_pulse` is matched to the window's audio, not to the window's arrival time. |
| Q5 | Lock lost: hold the last `b` for the rest of the output; reset only on reopen. |
| Q6 | First task: the measure-mode implicit snap. Placements keep the current residual instead of zeroing it, so a delay change moves the audio by exactly its delta. Also covers the baseline that placements ignore. |
| Q7 | Learned target: `X = D_out − H` per start from the first three confirmed windows; persisted per player keyed (codec, rate, adapter) next to the air latency entries, source `learned`; active from the 3rd start; a calibration wins; Forget clears both. (Estimator replaced by round 2 (2).) |
| Q8 | Seed: when the three windows agree within 2 ms, full correction, no 50 ms clamp. Dead band 2 ms. Then `b` tracks slowly (τ ≈ 30 s). |
| Q9 | The pulsesink fast loop stays, in `loop`, on the corrected error `e_pulse − b`, `J = 10 ms`. |
| Q10 | Telemetry/UI: the playout snapshot adds `air_bias_ms`, `air_target` (calibrated / learned / null), `air_corrections`; timing panel line "Air correction: calibrated / learned (N starts) / no data"; a guidance issue when a speaker is in `loop` but the air sensor is off, not effective or not locked (it works as `measure`). |
| Q11 | `loop` correction requires `EXPERIMENTAL_AIR_SENSOR`; without it `loop` is today's behaviour plus guidance. Per speaker, default off. |
| Q12 | Acceptance on the stand: 8 cold starts of ENEBY20 in `loop` against a warm (not restarted) reference on the playout engine; `sd(M) ≤ 2 ms` (measure today 4.8); calibrated `|median M| ≤ 3 ms`; uncalibrated `sd(M) ≤ 2 ms` from the 4th start; CPU not noticeably higher. Only ENEBY20's stream is restarted (via MA), so the reference keeps running. |

Controller rulings (round 1): a format change resets `b` and reseeds (the sensor reopens anyway);
`stream/clear` keeps `b`; the sensor's `E` takes `d_out` as the median of 5 windows and lags 3 of 5
windows after a change, so `b` never uses the snapshot `E`. (Round 1 also ruled "a delay change
offsets `b` by `+ΔD`"; round 2 replaced it.)

Controller rulings, round 2 (on rev. 1's open questions):

1. A delay change moves the sound by exactly `ΔD` in `loop` too, as in `measure` after the Q6 fix
   (the user's slider and the wizard's applied static delays alike): the static delay is a trim on
   the target, `b` unchanged; `E_target = −(D_out − D_ref)` with `D_ref` saved alongside the
   calibration or learned entry.
2. Learned estimator: the mean of the samples within ±15 ms of their median.
3. No new per-player key; the existing `playout_sync_enabled=false` opt-out applies.
4. After `held`, tracking resumes on the next accepted window, without a jump or a reset.
5. Learning runs in `loop` only.

Controller rulings on the pre-flight review (rev. 3):

- **C1** When `b` has a value, `β := 0` and the R14 baseline collection stops (the bias absorbs reading
  artefacts). A `β` measured while `held` is folded into `b` on resume. Before the first seed today's
  baseline rules apply; at the seed `β` is folded into `b` (`b` computed from `ê − β`, then `+β`,
  then `β := 0`).
- **I1** A delay change is applied as a frame-exact jump of `ΔD` at the write head against the
  existing schedule, without a re-placement, in every mode: exact, keeps every residual.
- **I2** `held` keeps a pending reseed; when the lock returns it goes to `seeding`.
- **I3** A learned value is accepted until this output has published its own sample.
- **I4–I10** and the minor fixes: applied below (daemon arg filter, pure learned module, MAD over
  accepted `b_i`, guidance condition, one guard with re-base, residual-domain formulas, fold changes
  with their consumer).

Stand facts used below (stage A note): σ(H) within a stream 0.2–0.9 ms; start-to-start offsets
9–60 ms in `measure`; live `H` tracks engine jumps to the millisecond; windows lag real time by
≈ 1.5 s (half a window) plus the host latency (PulseAudio 200–800 ms) plus batching; the sensor
acquires its first lag one window plus one hop after the air starts (two agreeing windows); ≈ 2.6 % of
a core per streaming speaker on the 2-vCPU VM for the sensor.

## Goals and non-goals

Goals (B1):

- In `loop` with the air sensor effective and locked: a cold start converges to the air target in
  one correction, within 10 s of the first audio on HAOS (800 ms PulseAudio latency); the
  start-to-start spread of the speaker against a warm reference drops to `sd ≤ 2 ms`.
- A calibrated speaker plays on the schedule (`|E| ≤ 3 ms` by microphone) at the delay it was
  calibrated with; an uncalibrated one plays where it played on average in its previous starts.
- A delay change moves the sound by exactly `ΔD` in every mode (`measure`, `loop` with or without a
  target), so the slider and the wizard keep their meaning (Q6, round 2 (1), I1).
- In `measure` (and in `loop` without air data) a placement keeps the residual (Q6).
- No new privilege, no extra CPU worth measuring, no new failure mode for playback: anything the air
  path does wrong disables the correction for that output, never the engine.

Non-goals (B1): the rate loop and frame-level corrections (B2); speakers without an effective air
sensor (they keep today's `loop`); per-speaker offsets of the playout plan's Stage 4 (the learned
target and the calibration replace them for Bluetooth speakers with the sensor); a change to how
`measure` reports or corrects beyond Q6 and I1; changing host audio settings.

## Definitions

Times are `CLOCK_MONOTONIC_RAW` µs, signs as in the stage A spec (positive error = late).

| Symbol | Meaning |
|---|---|
| `x` | Output stream time of the playout engine (ns written since the output opened); monotonic within one output, restarts at every reopen. |
| `s(x)` | The engine's schedule (`ScheduleMap`): RAW time at which `x` should leave the PulseAudio port; `s = t_client − D_out`. |
| `D_out` | The player's output delay (the daemon's `_player_delay_ms`). `d_out_i` = the delay a window's audio was written with (`T − s` stored per tap cell). |
| `e` (`e_pulse`) | The pulsesink error of one reading: `t − s(x_port)` (engine.py:293-296). |
| `e_w` | The error predicted at the write head (engine.py:302-306); equal to `e` in steady state, minus corrections already written. |
| `H` | Host placement of a confirmed air window: `t_air − s` at the window's middle (stage A). |
| `L_eff` | The target latency: the calibrated `L_as` when one exists for the current key, else the learned value once it is active, else none. |
| `D_ref` | The output delay `L_eff` refers to (round 2 (1)): stored with the calibration (`d_ref_ms`) or the learned entry. |
| `E_equiv` | The error against the target: `E_equiv = H + L_eff − D_ref = E + (D_out − D_ref)`, the stage A air error `E` measured against `E_target = −(D_out − D_ref)`. It does not contain `D_out`. |
| `b` | The bias: `b = e − E_equiv` for the same audio. Physically `b = D_ref − L_eff − P` (`P` the hidden path port → air); it does not change with `D_out`. |
| `β` | The engine's error baseline after an underrun or stall (R14), today's `_baseline_us`. `0` whenever `b` has a value, except in `held` (C1). |
| `EMA` | The controller's filtered input, in its own domain: `e_w − β` without a bias, `c = e_w − b − β` with one. |
| `r` | The raw residual: the engine's current estimate of `e_w`: `EMA + β` without a bias, `EMA + β + b` with one. |
| `o` | The placement offset: a placement aligns the next frame to `audible − o`, so that `e = o` right after it. |
| `X_i`, `d_i` | Learned sample of one output start: `X_i = median(d_out_i − h_i)`, `d_i = median(d_out_i)` over the seed triple; `X_i − d_i = −H_i`, the start's raw placement. |

## Why a bias and not the air error directly

The air error arrives late and slowly: a window covers 3 s, its middle is 1.5 s old when it closes,
the audio was written a host latency (≤ 800 ms) before it reached the port, and windows come once per
second. A controller acting on `E` directly would act on what happened 2–4 s ago and would be blind
to its own corrections for that long. The pulsesink reading comes every 100 ms and sees every
placement and jump at once, but it is wrong by a per-start amount (the very variance stage A found).
So the fast loop keeps `e` and the air sensor estimates how wrong `e` is: `b`. `b` changes only when
the hidden part of the path or the reading changes (a new PulseAudio stream, an underrun, a reading
artefact), so a slow filter with a fast seed is enough. A delay change does not change it (I1, "Time
bases under a delay change").

`b` does **not** change when the engine moves audio: a placement or a jump moves `e` and `E_equiv`
by the same amount. That is what makes the windows that come in after a correction still usable for
`b`, as long as one window does not mix audio from before and after it (S-3).

## Architecture

```
 daemon asyncio loop                                        playout-feeder thread (per output)
 ───────────────────────────────────────────────────       ─────────────────────────────────────────────────
 AirSensor (stage A, 1 Hz step, windows in executor)          PlayoutEngine (loop mode, an AirFeed given)
   _accept(win) ──► on_window(AirSample)  ─┐                     sense() every 100 ms (inside one guard):
   lock state, confidence, key per step ───┤                       EHistory.add(t, x_port, e, usable)
 set_air_calibration (IPC) ─► target ──────┤                       action = AirBias.update(t, hist)  ───┐
 output delay (spawn, delay change) ───────┤                       c = e_w − b − β → LoopController(J=10)│
                                           ▼                     next_block():                        │
                                  AirFeed (threading.Lock)         audible − o  (o = b + β | r)  ◄──────┘
                                    samples (≤ 32, drop oldest)    pending jump (delay/air_seed/air_target)
                                    lock, target, key, d_out, seq snapshot(): air_bias_ms, air_target, …
                                    min_tap_gen (reopen)         air_learned {seq, x_ms, d_out_ms, key} ─► parent
                                           ▲
 PlayoutPlayer._reopen_air_tap ─ reopen(tap.gen) ┘
```

- **The bias state lives on the feeder thread (Ruling S-1).** The engine, its controller and its
  placements are feeder-only today (gst_output.py:8-15). `AirBias` and `EHistory` live there too; the
  daemon loop only hands immutable samples and four small values (lock, target, key, output delay)
  across, through `AirFeed`, whose lock is held for a deque append or a swap. No callback runs across
  threads, nothing blocks the feeder.
- **Matching by `x` (Ruling S-2).** Each confirmed window carries the `x` span of the written cells it
  matched (the tap gains an `x` per cell; air_tap.py stores `s`, `T`, kind today). The engine keeps a
  30 s history of its readings `(t, x_port, e, usable)` and takes `ê` for a window as the median of the
  usable readings with `x_port` in the window's span. `x` is unique and monotonic within an output;
  `s` is not (placements and inserts step it back), and RAW arrival time is off by the window and host
  lag. **A reading is usable** when no stall or underrun is in progress and `s(x_port)` exists;
  readings between an event and its settle point are kept, because they describe the audio before the
  event and S-3 already excludes windows that span the event. A window needs at least 50 % of the
  readings its span should hold (`W · 10 Hz · 0.5` = 15), else it is skipped (counted).
- **Reopen.** `adopt_output` builds a new engine (gst_output.py:309-351), so a new `AirBias` starts
  in `none`. `_reopen_air_tap` calls `feed.reopen(tap.gen)` after the tap reset: queued samples are
  dropped and samples from an older tap generation are refused at `push`, so a window computed on the
  old output and accepted after the reopen never reaches the new engine (the sensor already drops
  windows whose tap generation changed while they ran, air_sensor.py:654-657).
- **Measure stays observe-only (Q2).** `AirBias` exists only when the engine runs in `loop` and the
  daemon built an `AirFeed`; in `measure` the sensor runs exactly as in stage A and nothing feeds back.

### The engine's changes

1. **Controller input.** With a bias value, `c = e_w − b − β` instead of `e_rel = e_w − β`
   (engine.py:312). R14 is unchanged: persistence 5 s, median of 5, 90 % one sign, one steady jump per
   10 s, clamp 50 ms, `J = 10 ms` in `loop` (controller.py:20, Q9).
2. **Placement target.** Every user of `_audible()` that places or anchors (the scheduler's placements
   `start`, `underrun`, `stall`, `clear`, `gap`, `starved`, `format`, and the silence re-anchors of
   `reanchor`) gets `audible − o` (engine.py:190, 196, 207, 213). `o` is (Ruling S-5):
   - `b + β` when a bias value exists (`active`, `held`, or a provisional value while reseeding; `β = 0`
     outside `held`): right after the placement `c = 0`;
   - otherwise the raw residual `r` (Q6), so the placement keeps the audio where it is relative to its
     old schedule and only the event itself (the gap, the clear) moves it;
   - `0` for the first placement of an output (`start`: nothing is known yet, `EMA` is `None`).

   A placement that supersedes a decided steady jump keeps `r` as well: the controller resets its EMA
   to the post-jump value when it decides the jump (controller.py:130), so `r` already excludes it.
3. **Delay changes are jumps (I1, Ruling S-23).** "Delay changes: a frame-exact jump", below.
4. **Air jumps.** `AirBias` returns at most one action per update: a jump of `jump_us` with reason
   `air_seed` (first seed of an output or a reseed) or `air_target` (the target changed). The engine
   executes it like a steady jump (frame-exact at the write head, scheduler.py:193-198, 361-382) but
   bypassing the 50 ms clamp and the 10 s interval (Q8); it is dropped when a placement is pending
   (engine.py:200-211), because that placement already targets `c = 0`. Logged as
   `playout jump: air_seed, 37.2 ms`, counted in `air_corrections`.
5. **One guard (I8, Ruling S-20).** Every call into `AirBias` and `EHistory` on the feeder goes through
   one engine helper. An exception disables the bias for the rest of the output (`air_bias_state:
   none`, `air_bias_reason: internal_error`), re-bases the controller and the EMA to the raw domain
   (the "bias → none" transition below, so the audio stays where it is), logs a WARNING with the
   traceback at most once per 10 min (`RateLimitedLog`), and never reaches the feeder (an exception
   there would trigger `fall_back`, gst_output.py:508-519).

### Residual domains (I9, Ruling S-24)

The engine keeps one EMA in the controller's input domain and publishes `error_ms` from it. The
domains and what a placement does in each:

| | without a bias value | with a bias value |
|---|---|---|
| controller input | `e_rel = e_w − β` | `c = e_w − b − β` |
| `β` | today's R14 rules | `0`; in `held` today's rules on `c` (C1) |
| placement offset `o` | `r = EMA + β` (`0` before the first reading) | `b + β` |
| controller residual after a placement (`placed(t, residual)`) | `o − β = EMA` | `o − β − b = 0` |
| EMA after a placement | `o − β` | `0` |
| published `error_ms` | `EMA + β` (raw) | `EMA + β` (≈ the air error; raw = `error_ms + air_bias_ms`, S-18) |
| R14 re-measure branch (engine.py:326-339) | as today: `wanted + β` against `J` | the same expression in the `c` domain; it can fire only in `held` (`β = 0` elsewhere) and restarts the baseline collection on `c` |

Order inside `_record_placement` (B0): (1) `o` is the value the render used; (2) if the reason is
`underrun`/`stall` and no bias value exists, `β := 0` and a fresh baseline collection starts (today's
rule); otherwise `β` is kept; (3) `controller.placed(t, residual = o − β − (b or 0), quiet_wait=True)`
with the `β` after step 2; (4) the published EMA restarts at `o − (b or 0)` (the published value is
then `o − (b or 0)` plus `β`, i.e. `o` in the raw domain).

Transitions between the domains:

- **Seed** (`none`/`seeding` without a value → `active`; C1): `b₀ = median(ê_i − β − E_i) + β`
  (`ê` is raw, so this is `median(ê_i − E_i)`), then `β := 0` and the baseline collection stops. With
  an `air_seed` jump the jump's `_record_event` re-bases (`placed(0)`, EMA 0). Without one the
  controller is re-based to `placed(t, residual = EMA + β_old − b₀)`, EMA restarted there.
- **Resume from `held`** (C1): `b := b + β`, `β := 0`. `c = e_w − b − β` is unchanged by this, so the
  controller's EMA is kept; the published `error_ms` drops the `β` it reported.
- **Bias → none** (calibration forgotten without a learned fallback, or the guard's internal error):
  `r = EMA + β + b`; `β := 0`; `placed(t, residual = r)`, EMA `r`; the controller is on `e_rel = e_w`
  again; the audio does not move.

### Q6: the measure-mode implicit snap (first task)

Today `_record_placement` restarts the error at 0 (engine.py:497-506) and the scheduler aligns the next
frame to `audible` exactly (scheduler.py:330-359): every placement removes whatever residual the
pulsesink saw, below or above the dead band. A delay change of `+30 ms` therefore moved ENEBY20 by
`−30 − 13.4 = −43.6 ms` on the stand (the delay is applied as a placement, engine.py:149-153, and the
residual `e_w = +13.4 ms` since the start was zeroed), and a gap or clear after an underrun moves the
audio by the baseline `β` that the engine had just declared a reading artefact (the R14 baseline is
zeroed at engine.py:500).

Fix (B0; `measure` and `loop` without a bias):

- every placement uses `o = r` and the domain rules above, so `e` stays at `r` after it;
- `β` survives placements other than `underrun`/`stall`; `_baseline_degraded` likewise;
- a delay change is a frame-exact jump (next section), not a placement.

The `start` placement still aligns to `audible` (`o = 0`).

### Delay changes: a frame-exact jump (I1, Ruling S-23)

`resync(shift_us)` with `shift_us = old − new = −ΔD` (bridge_daemon.py:1288-1300) does, in every mode:

1. shift every queued chunk's play time and the scheduler's `_expect_us` by `shift_us`, **without**
   requesting a placement (a new scheduler method beside `shift`, scheduler.py:126-137);
2. if a placement is already pending: nothing else (that placement aligns the shifted next frame to
   `audible − o`, which keeps the residual, so the result is the same);
3. else, if program audio is queued: a pending jump of `ΔD = −shift_us` with reason `delay`, executed
   by the scheduler's jump machinery at the write head (`ΔD > 0` drops that much audio, `ΔD < 0`
   inserts that much silence; scheduler.py:193-198, 361-388), not clamped; it replaces a decided steady
   jump that is not yet rendered (the controller decides again after the settle);
4. else (nothing queued): nothing; the next chunk is placed (`starved`) with `o`.

Why it is exact: before the change the next frame at the write head `x_w` has play time `p`
(`s(x_w) = p`). With a delay `ΔD` higher, the queue's play times are `p − ΔD`, … ; dropping `ΔD` of
audio brings the frame that had play time `p + ΔD` (now `p`) to `x_w`, where the existing schedule
continues with `p`: the schedule is continuous, `e` is unchanged, and every frame after it is heard
`ΔD` earlier than it would have been. For `ΔD < 0` the inserted silence moves the frame with play time
`p − |ΔD|` (now `p`) by `|ΔD|` later along the same continuous schedule. No `audible` reading is used,
so pulsesink noise does not enter (a placement would align to the 9-reading median, ±1.3 ms). A drop
longer than the queued program drops what is queued; the next chunk is then placed with `o`.

`resync(0)` keeps today's behaviour (a `delay` placement): the existing engine tests use it as an
engine-requested placement (test_engine.py:159, 292-303, 419-423) and the daemon never sends a zero
shift (`_apply_output_delay` returns when old == new).

### Time bases under a delay change (round 2 (1), Ruling S-14)

What moves when `D_out` changes by `ΔD` (from the code):

- **Play time.** aiosendspin's `compute_play_time` returns `client_time − D_out` (bridge_daemon.py:1265:
  "a larger delay plays earlier (play time = client time − delay)"); the scheduler records a program
  frame's play time as its schedule (scheduler.py:390-398), so `s(x) = t_client(x) − D_out`.
- **The change.** The daemon applies `shift_us = −ΔD` to the queued chunks and the pending play times
  and calls `player.resync(shift)` (above); new chunks get the new play time from
  `compute_play_time`. For audio of the same client time: `s' = s − ΔD`.
- **The tap** stores `T = s + D_out` per cell: `T' = T = t_client`, `d_out' = d_out + ΔD`.
- **The air.** A frame leaves the port at `s + e` and the air at `t_air = s + e + P`, so
  `H = t_air − s = e + P`; the sound comes `L_true` later. Stage A's `E = H + L_as − D_out =
  t_sound − t_client` (with `L_as = L_true`).

The ruling requires `t_sound' = t_sound − ΔD` for every frame. Since `s' = s − ΔD`, that holds iff
`e' = e`, i.e. iff `H' = H`. The jump above keeps `e` (the schedule stays continuous), so the target
must hold `H`, not `E`, and must not contain `D_out`:

```
E_equiv = H + L_eff − D_ref = E + (D_out − D_ref)        target E_equiv = 0  ⇔  E = −(D_out − D_ref)
b = e − E_equiv = (D_ref − L_eff) − P                    independent of D_out: unchanged by ΔD
```

After the jump `c` is unchanged (≈ 0 in `active`), `E_equiv` is unchanged, no air jump follows and
the windows after the change confirm `b`. Windows that straddle the jump's `x` are dropped (S-3), as
for any event. Per-window `d_out` does not enter `b` (it is still recorded for the learned samples and
for the stage A telemetry `E`). With `D_out = D_ref` the target is `E = 0` (Q3).

Where `D_ref` comes from and where the trim lives (Ruling S-22):

- **The trim is the static delay itself.** Nothing else is stored per output or reset at a reopen:
  `D_out` is already persisted (the player's `static_delay_ms`, MA's `sendspin_static_delay`), and at
  every start the target is `E = −(D_out − D_ref)` again. Raising the slider by 30 ms after a
  calibration makes the speaker play 30 ms earlier than the schedule, now and at every later start.
- **Telemetry:** `air_trim_ms = D_out,latched + ΣΔD − D_ref`, with `D_out,latched` the output delay the
  feed holds at the bias's first update (the daemon sets it at spawn and at every applied change) and
  `ΣΔD` the delay jumps of this output; it never waits for windows.
- **Calibrated:** `D_ref` = the output delay the wizard's written fold was measured with (`d_ref_ms`
  in the calibration entry). The daemon's fold already works on `T = s + D_out` per cell; its answer
  gains `d_out_ms` (the median `T − s` of the folded cells) and a fold whose cells carry two delays
  answers `air_delay_changed` (a new ineligibility reason). `L_as = A − H_cal` is unchanged (the delay
  cancels in it, stage A). These fold changes ship with their consumer (Task B8b-2, I10).
- **Learned:** `D_ref = d_ref_ms = median(d_i)` with `latency_ms = d_ref_ms + off_ref` (S-21), so
  `L_eff − D_ref = −H_ref` whatever the delays during learning.
- **Stage A entries without `d_ref_ms`** (the stand has one): the config migration fills `d_ref_ms`
  from the player's `static_delay_ms` (logged at INFO once per entry; `0` with no static delay). An
  entry the daemon receives without `d_ref` latches `D_ref = D_out,latched`. Adding the field to the
  strict `AirLatencyEntry` is safe because stage A is not released.
- **The wizard** keeps working unchanged: a static delay it applies to a `loop` member moves the sound
  by exactly that delay; a calibration it saves applies live (S-11), and from then on the delay in
  force at the fold is the reference.

The bridge logs `Output delay changed … ms; air target trim −(D_out − D_ref) = … ms` at INFO for a
change in `loop` with a target.

### The `AirBias` state machine (per output)

States (Q10's `air_bias_state`): `none` (no target, or the correction cannot run), `seeding`
(a target is known, collecting the seed triple; with or without a provisional `b`), `active`
(`b` known and tracked), `held` (`b` frozen: the sensor lost its lock; may carry a pending reseed).

```
                      target known                      agreeing triple (full seed jump if |E|>2 ms)
   reopen ─► [none] ───────────────► [seeding] ────────────────────────────────────────────► [active]
               ▲  target forgotten,     ▲  │ 12 windows, no agreement: b = median(last 5), no jump ─┘│ ▲
               │  no learned fallback   │  │                                                        │ │
               │  (audio kept)          │  └─ lock lost (initial: stays; provisional: → held,       │ │
               │                        │      reseed pending) ─────────────────────────────────┐   │ │
               │                        │ format / underrun / stall / pa_step (b → provisional)  │   │ │
               │                        │◄── lock back with reseed pending ──────────────┐       │   │ │
               └────────────────────────┴────────────────────────────────────────────────┼───────┼───┘ │
                                                                                          │       ▼     │
                                                                                     [held] ── lock back, no reseed pending:
                                                                                      next accepted window → active (no jump)
```

| Event | `none` | `seeding` | `active` | `held` |
|---|---|---|---|---|
| reopen (new engine) | — (start) | → `none` | → `none` | → `none` |
| target appears (a calibration at any time; a learned value with ≥ 2 samples until this output has published its own sample, I3) | → `seeding` | target updated | `ΔL` applied (row below) | `ΔL` applied |
| accepted window (confidence ≥ 0.5, clean span, coverage) | learning triple only (S-12) | into the triple; agreement → `active` (seed, C1) + `air_seed` jump if `|E_med| > 2 ms` | tracking update (S-9) | lock back: no reseed pending → `active`, tracking update, no jump; reseed pending → `seeding` with the provisional `b` (I2); `β` folded into `b` first (C1) |
| no agreement after 12 windows | — | `b` = median `b_i` of the last 5 → `active`, no jump (R14 corrects `c`) | — | — |
| format change | — | triple restarts | → `seeding`, `b` provisional | `b` provisional, reseed pending |
| `stream/clear` | — | — | `b` kept; the clear placement uses `o = b` | `o = b + β` |
| delay change `ΔD` (a jump, I1) | jump; residual kept | jump; windows across it dropped (S-3) | jump; `b` unchanged, sound moves `−ΔD` (S-14) | same |
| underrun / stall / `pa_step` | today's baseline | initial: triple restarts, today's baseline; provisional: triple restarts, controller frozen ≤ 15 s, no baseline | → `seeding`, `b` provisional, controller frozen ≤ 15 s, no baseline (C1) | reseed pending; today's baseline on `c` (C1) |
| reseed timeout (15 s frozen) | — | sensor locked: → `active` with the provisional `b`, unfrozen, no baseline (tracking and the step rule absorb what changed); lock lost: → `held`, reseed pending | — | — |
| lock lost (sensor not `locked`/`holding`) | — | initial: waits; provisional: → `held`, reseed pending | → `held` | — |
| calibration set / changed (`L_eff − D_ref` moves by `ΔL`) | → `seeding` | target updated | `b −= ΔL`; `air_target` jump `+ΔL` if `|ΔL| > 2 ms` | `b −= ΔL`; jump as in `active` |
| calibration forgotten | — | falls back to learned (target updated) or → `none` | to learned: `ΔL` as above; no learned: → `none`, bias → none transition (audio kept) | same |
| learned value changes after this output published its sample | ignored until reopen (I3) | ignored | ignored | ignored |
| 15 consecutive gated windows within 3 ms | — | — | `b` = their median, logged, no jump | — |
| internal error (the guard) | → `none` (`internal_error`), bias → none transition | same | same | same |

Notes on the rows:

- **Seed triple (Ruling S-8).** The seed uses the newest three eligible windows that lie wholly after
  the last engine event's `x` and the last change of `b`'s definition (S-3). They agree when
  `max(E_equiv) − min(E_equiv) ≤ 2 ms` (Q8). Then `b₀` (C1, above) and, if `|median(E_equiv)| > 2 ms`
  (dead band, Q8), an `air_seed` jump of `+median(E_equiv)`: positive = late = drop. If three do not
  agree, the oldest is dropped and the next window joins (sliding); after 12 windows without agreement
  `b` is seeded from the last five without a full correction and R14 corrects `c` under its normal
  rules. The seed `b_i` start the gate's MAD history (I6).
- **Why the jump uses `E` and not `c`.** `median(E_equiv)` has σ ≈ 0.2–0.9 ms/√3; `c` carries the
  pulsesink noise (σ ≈ 4 ms per reading on PulseAudio 17, a sawtooth up to 30 ms on PipeWire). No event
  happened since the triple's audio (by construction), so the error now equals the triple's.
- **Reseed (Ruling S-7).** "Reset + reseed" (round 1, format change) is seeding with the old `b` as a
  provisional value: placements and the controller use it until the new triple replaces it (no
  filtering across the two); the new triple applies its own seed jump when the air error is beyond the
  dead band. After an underrun, stall or `pa_step` the controller is frozen until the reseed (≤ 15 s)
  and no baseline is measured: `b` from the air absorbs a reading offset and a hidden change alike
  (C1). A reseed that cannot happen because the lock is lost stays pending through `held` and runs when
  the lock returns (I2).
- **Held (Ruling S-10, round 2 (4)).** `b` is frozen, never reset or decayed. An underrun in `held`
  measures today's baseline `β` on `c`; when the lock returns `β` is folded into `b` (C1) and the
  state goes to `active` (tracking resumes, no jump) or, with a reseed pending, to `seeding`.
- **Target changes (Ruling S-11).** `E_equiv = H + L_eff − D_ref`, so a change of `L_eff − D_ref` by
  `ΔL` changes every `E_equiv` by `+ΔL` and `b` by `−ΔL`: the audio is `ΔL` late against the new target
  and gets one `air_target` jump of `+ΔL` (no clamp, Q8) when beyond the dead band. A calibration saved
  in the wizard takes effect at once. A learned value is accepted until this output has published its
  own learned sample and ignored after that until the next reopen (I3): the parent resends after every
  sample, including the one this output produced, which must not activate the learned target in the
  same start. A calibration forgotten mid-output falls back to the learned value in force.

### Tracking (Ruling S-9)

For every eligible window in `active` (and the first one after `held`):

```
b_i  = ê(x_lo, x_hi) − (h_i + L_eff − D_ref)            ê = median e over the window's x span
gate = max(10 ms, 4 · 1.4826 · MAD(accepted b_i, last 20))      history starts with the seed's b_i (I6)
|b_i − b| > gate  → not used, not added to the history (air_bias_gated += 1);
                    15 consecutive gated within 3 ms of each other → b = their median, history restarts with them
else              → b += (1 − exp(−Δt / 30 s)) · (b_i − b),  Δt = at_i − at_prev clamped to [0, 5 s]; b_i joins the history
```

Δt is the air time between window middles (≈ 1 s), so a burst of windows after a pause does not count
as more evidence than the time it covers. The gate bounds the effect of a wrong lag that passed the
sensor's gate and continuity rule (one 50 ms outlier would move an unguarded `b` by 1.7 ms); taking
the MAD over accepted values only keeps a run of outliers from widening the gate for itself. The MAD
term lets PipeWire's sawtooth (window medians spread by several ms) through. The step rule absorbs a
persistent change (a PulseAudio skip that moved the audio, a link path change, a reading artefact
after an underrun whose reseed timed out) after 15 s; a congested link whose `H` swings for ≈ 10 s
and comes back (#562) is not chased.

### Learned target (Q7, Ruling S-12, S-21)

- **Sample.** Once per output, from the first agreeing triple after the reopen (not after a reseed):
  `X_i = median(d_out_i − h_i)`, `d_i = median(d_out_i)`. The triple precedes any seed jump, so
  `X_i − d_i = −H_i` is the raw start placement whatever the target. Learning runs in `loop` only
  (round 2 (5)), also while a calibration exists (so Forget of the calibration can fall back to it).
- **Publish.** The engine snapshot carries `air_learned = {"seq", "x_ms", "d_out_ms", "codec",
  "rate", "adapter"}`; `seq` is a per-daemon counter kept in the `AirFeed` (it survives engine
  rebuilds), the key is the feed's key at the seed. No key → no sample.
- **Persist.** The parent records a new `(daemon process, seq)` once: validates it (`d_out_ms`
  0…5000, `|x_ms − d_out_ms| ≤ 1000`, the key complete), appends the pair to the entry for that key
  (≤ 8 pairs, oldest dropped), recomputes the reference, counts `starts`, stamps `measured_at`, writes
  the config off the loop and resends `set_air_calibration` with the learned value. Writes come at
  most once per output start (a seed needs ≈ 7 s of audio).
- **Estimator (round 2 (2)).** With `off_i = X_i − d_i`: `off_ref = mean{ off_i : |off_i −
  median(off)| ≤ 15 ms }`, `d_ref_ms = median(d_i)`, `latency_ms = d_ref_ms + off_ref`. It lives in a
  pure module (`services/audio/playout/air_learned.py`, stdlib only) that the config layer and the
  tests import (I5). On stage A's cold-start numbers (`H` = −45.2, −32.4, −31.4, −44.5, −44.9, −36.3,
  −44.7, −36.4 ms) the target used at starts 4–8 moves by `sd = 1.4 ms` between starts (all samples lie
  within ±15 ms of the median); the plain median of Q7 would move by 5.0 ms (two clusters 10 ms apart,
  the median flips between them). The closed-loop simulation prints both.
- **Use.** The parent sends the learned value only with ≥ 2 samples ("active from the 3rd start").
  The daemon's target is the calibration for the key when one exists, else the learned value, else
  none (Q7).

## Constants

| Name | Value | Why |
|---|---|---|
| `J` (`loop`) | 10 ms (unchanged, controller.py:20) | Q9 |
| `SEED_WINDOWS` | 3 | Q4, Q8 |
| `SEED_AGREE_US` | 2 000 | Q8; three windows with σ(H) ≤ 0.9 ms have an expected range of ≈ 1.5 ms |
| `SEED_MAX_WINDOWS` | 12 | ≈ 12 s of windows; past this the triple is unlikely to agree (a congested link) and R14 takes over |
| `DEAD_BAND_US` | 2 000 | Q8; below Q12's 2 ms target and above σ(H)/√3 |
| `MIN_CONFIDENCE` | 0.5 | stage A outline; R-11 gives 1.0 for the stand's PSR (p10 1.93) |
| `BIAS_TAU_S` | 30 | Q8 |
| `GATE_MIN_US`, `GATE_MAD_K` | 10 000, 4 | PulseAudio 17: per-window `b_i` noise ≈ 0.7 ms (σ 4 ms over ≈ 30 readings) plus σ(H); PipeWire's sawtooth spreads window medians by several ms, the MAD term widens the gate there |
| `GATE_HISTORY` | 20 accepted windows | the MAD of the last ≈ 20 s of accepted `b_i` (I6) |
| `STEP_WINDOWS`, `STEP_AGREE_US` | 15, 3 000 | a change persisting 15 s is real; #562's swings last ≈ 10 s |
| `RESEED_TIMEOUT_S` | 15 | ≥ host latency (0.8 s) + one window (3 s) + three hops + margin |
| `DT_MAX_S` | 5 | a filter step never counts more than 5 s of evidence |
| `E_HISTORY_S` | 30 | windows reach back ≤ 6 s (3 s span + 1.5 s + host latency + batching); 30 s covers a busy executor |
| `E_MIN_COVERAGE` | 0.5 | ≥ 15 of the ≈ 30 readings a 3 s span holds |
| `FEED_MAX` | 32 samples | ≈ 30 s of windows; drop-oldest |
| `LEARN_MIN_SAMPLES`, `LEARN_KEEP` | 2, 8 | Q7 |
| `LEARN_BAND_MS` | 15 | round 2 (2) |
| learned offset `X − d` | within ±1 000 ms | ±500 ms acquisition range (stage A R-15) plus margin; `d` 0…5 000 ms like the static delay |

## Telemetry, API and UI

### Playout snapshot (engine, `DeviceStatus.playout`, API `PlayoutTiming`)

| Key | Type | Meaning |
|---|---|---|
| `air_bias_state` | `none`/`seeding`/`active`/`held` or null | null when the engine has no `AirBias` (measure, off, switch off) |
| `air_bias_ms` | float or null | `b` (provisional values included); null without a value |
| `air_bias_reason` | str or null | `none`: `no_target` or `internal_error`; `held`: `not_locked`; else null |
| `air_target` | `calibrated`/`learned` or null | Q10 |
| `air_target_ms` | float or null | `L_eff` in use |
| `air_d_ref_ms` | float or null | `D_ref` in use |
| `air_trim_ms` | float or null | `D_out,latched + ΣΔD − D_ref`; the target is `E = −air_trim_ms` |
| `air_learned_starts` | int or null | samples behind the learned value in use |
| `air_corrections` | int | `air_seed` + `air_target` jumps in this output |
| `air_last_correction_ms` | float or null | the last one's size |
| `air_bias_gated` | int | windows the gate refused |
| `air_bias_skipped` | int | windows not eligible (confidence, coverage, event span) |
| `air_learned` | object or null | status only (parent persistence); not in the API model |

`error_ms` in `loop` with a bias value is `EMA + β` in the `c` domain (≈ the air error); the raw
pulsesink error is `error_ms + air_bias_ms` (Ruling S-18). The `PlayoutTiming` description says so.

### Device API

- `GET /api/v1/devices/{id}`: `timing.playout` gains the keys above; `audio.air_learned_latency`
  lists the learned entries; `audio.air_to_sound_latency[].d_ref_ms` is added.
- `DELETE /api/v1/devices/{id}/air-calibrations/{codec}/{rate}/{adapter}` and
  `DELETE /api/v1/devices/{id}/air-calibrations` remove the calibration **and** the learned entry
  for the key (or all of both), Q7; 404 `unknown_calibration` only when neither existed. Applied live.

### UI

- Timing panel, air block, when `timing.playout.mode == "loop"` and `timing.air_sensor` is not null:
  "Air correction: calibrated (228.7 ms)" / "learned (6 starts)" / "no data (learning: 1 of 2
  starts)" / "inactive: not locked"; then "Air bias +12.3 ms", "Air corrections 1" and, when
  `air_trim_ms` is not 0, "Delay trim −30.0 ms (target 30.0 ms early)".
- The calibrations list shows learned entries too: "Learned: 219.4 ms at 184 ms delay · SBC · hci1 · 6
  starts" with the same Forget (which forgets both).
- Settings: the `EXPERIMENTAL_AIR_SENSOR` help text says it runs in `measure` (observe) and `loop`
  (corrects).
- en and ru.

### Logs

| Level | Message |
|---|---|
| INFO | `playout jump: delay, 30.0 ms` / `air_seed, 37.2 ms` / `air_target`; `air bias seeded at 12.3 ms (E −37.2 ms, 3 windows within 0.6 ms)`; `air bias held (sensor no_lock)`; `air bias step: 12.3 → 25.1 ms`; `Output delay changed 184→214 ms; air target trim −(D_out − D_ref) = −30.0 ms`; learned sample recorded (parent: value, key, samples) |
| WARNING (once per 10 min per kind) | `AirBias` internal error (with traceback); learned sample not persisted (config write failed) |
| DEBUG | per window with `SENDSPIN_AIR_DEBUG=1`: `b_i`, `ê`, coverage, gated; the engine's 10 s summary gains `b` and the state |

### Guidance (Q10, I7, Ruling S-16)

New registry entry `air_correction_inactive` (layer `audio`, severity `info`) with a producer in
`recovery_assistant.py`, for a device whose `playout.mode` is `loop`, evaluated in this order:

| Reason code | When |
|---|---|
| `air_switch_off` | the device's `air_sensor` is null (switch off) |
| `air_not_effective` | `air_sensor.state == "off"` (e.g. not Bluetooth) |
| `air_unavailable` | `air_sensor.state` is `unavailable`, `unsupported_codec` or `unsupported_input`, or `playout.air_bias_reason == "internal_error"` |
| `air_not_locked` | `playout.warm_s ≥ 60`, `playout.air_bias_ms` is null and `air_sensor.state` is not `locked`/`holding`: the bias never got a value in this output (covers `seeding` that never locks and `none` whose learning never locks) |

No issue for `active`, `held` (it corrects with the held `b`), `seeding` with a provisional value, or
`none` with `no_target` while the sensor is locked (learning; the panel shows it). Text: "This speaker
is in loop mode but the air correction is not running; it plays as in measure mode."

## Configuration and persistence

### Effective rule (Q2, Q11, Ruling S-15)

- `air_sensor_effective(player_cfg, cfg)`: on when the switch is on, the effective playout mode is
  `measure` **or `loop`**, and the backend is Bluetooth. Reasons `switch_off`, `needs_measure` (mode
  `off`; the code stays for API/UI compatibility, its text becomes "needs playout sync in measure or
  loop mode"), `not_bluetooth`. `loop_not_supported_yet` goes.
- The correction runs where the sensor runs and the mode is `loop`; there is no separate config
  function for it (rev. 2's `air_correction_effective` had no consumer and is dropped). Q11's "per
  speaker, default off": both inputs default off and a speaker opts out with the existing
  `playout_sync_enabled=false` (round 2 (3)).
- The daemon builds the sensor in `measure` and `loop` (bridge_daemon.py:257 gate goes) and the
  `AirFeed` in `loop` only.
- **Spawn param** `air_sensor` (validated by `daemon_process._air_sensor_args`, daemon_process.py:373-381,
  which today keeps only `enabled` and `latency_ms` and must pass the new keys through, I4):

  | Key | Type | Sent |
  |---|---|---|
  | `enabled` | `true` | always (as stage A) |
  | `latency_ms` | float or null | always (as stage A) |
  | `d_ref_ms` | float 0…5000 | when the calibration has one |
  | `learned_ms`, `learned_d_ref_ms`, `learned_starts` | float, float 0…5000, int ≥ 2 | together, when a learned value with ≥ 2 samples exists |

  The existing spawn test's exact dict (no calibration, no learned value) is unchanged.

### IPC (Ruling S-17)

`set_air_calibration` (payload `v: 1`, no protocol bump) gains the same optional fields as the spawn
param, sent only when known: `d_ref_ms` (0…5000), `learned_ms`, `learned_d_ref_ms` (0…5000) and
`learned_starts` (int ≥ 2), the three learned fields together and with
`|learned_ms − learned_d_ref_ms| ≤ 1000`. An older daemon ignores them (decoders read named fields
only, commands.py:417-423). `SetAirCalibration` becomes `(latency_ms, d_ref_ms=None, learned_ms=None,
learned_d_ref_ms=None, learned_starts=None)`. The daemon also sets the feed's output delay at build
and in `_apply_output_delay`.

### Learned entries (Ruling S-13)

A sibling per-player field `air_learned_latency`, not entries inside `air_to_sound_latency`:

```json
"air_learned_latency": [
  {"codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6", "source": "learned",
   "latency_ms": 219.4, "d_ref_ms": 184.0,
   "samples_ms": [229.0, 216.4, 215.5], "d_out_ms": [184.0, 184.0, 184.0], "starts": 3,
   "measured_at": "2026-10-29T10:12:00+00:00"}
]
```

The calibration entry (`air_to_sound_latency`, stage A) gains `d_ref_ms: float | null` (0…5000;
filled by the migration for older entries, S-22).

- Model `AirLearnedEntry` (strict, `extra="forbid"`): `codec: "sbc"`, `rate` (16000/32000/44100/48000),
  `adapter` (MAC pattern), `source: "learned"`, `samples_ms` and `d_out_ms` (1–8 finite floats each,
  the same length; each `d_out_ms` 0…5000; each pair `|X − d| ≤ 1000`), `latency_ms` and `d_ref_ms`
  (= `learned_reference(samples_ms, d_out_ms)` within 0.05 ms, validated), `starts` (int ≥
  `len(samples_ms)`), `measured_at`. At most 8 entries per player, one per key.
- Why a sibling field: `Player` is `extra="allow"`, so an older v3 beta keeps an unknown field on
  downgrade, while a `source: "learned"` entry inside `air_to_sound_latency` would fail the strict
  `AirLatencyEntry` and the two kinds would share one 8-entry cap and one uniqueness key.
- Helpers in `config/players.py` (importing `learned_reference` from the pure module):
  `valid_air_learned_entries`, `resolve_air_learned(player_cfg, codec, rate, adapter) -> tuple[float,
  float, int] | None` (`(latency_ms, d_ref_ms, starts)`, None below 2 samples),
  `record_air_learned_sample(entries, key, x_ms, d_out_ms, now_iso)`, `forget_air_learned(entries,
  codec=None, rate=None, adapter=None)`.
- Migration: `config/migration.py` gains `_clean_air_learned` (invalid entries dropped one by one,
  never the player), and `_clean_air_latency` fills a missing `d_ref_ms` from the player's
  `static_delay_ms` (else 0), logged once per entry. No schema version bump.
- `config/schema.json` player definition, `scripts/translate_ha_config.py` preserved per-device fields
  (next to `air_to_sound_latency`, line 375), `services/infrastructure/config_diff.py` hot-apply set
  (next to `air_to_sound_latency`, line 43).

## Failure containment

| Failure | Behaviour |
|---|---|
| `AirBias` or `EHistory` raises (any call) | the guard (I8): `none`/`internal_error` for the output, controller and EMA re-based to the raw domain (audio kept), WARNING with traceback once per 10 min; never reaches the feeder |
| `AirFeed` misuse or overflow | `push` drops the oldest beyond 32; a push with an old tap generation is refused; all methods are O(1) under the lock |
| The daemon's `on_window` hook raises | caught in the sensor (logged at DEBUG); the sensor keeps running |
| A wrong window (wrong lag) | the sensor's PSR gate and continuity rule first; then the bias gate (S-9) |
| Sensor loses its lock | `held`: `b` frozen, the speaker keeps its corrected placement; guidance only when the bias never got a value |
| Learned sample persistence fails | WARNING once per 10 min; the target keeps the previous value |
| Learned key changes (adapter or codec) | a reconnect reopens the output; the parent resolves the new key's entries |
| PipeWire sawtooth | R14 persistence on `c` (unchanged), the gate's MAD term; acceptance in simulation |

## Test strategy

- **Unit, pure** (`air_learned`, `AirFeed`, `EHistory`, `AirBias`): the estimator; seed agreement and
  sliding; seed jump size and dead band; seed folds `β` (C1); no-agreement fallback after 12 windows;
  tracking with τ = 30 s on window-middle time; gate (fixed and MAD over accepted values seeded by the
  seed), step rule; every event in the table (each case on a freshly seeded instance unless the test
  says it is a sequence); learned sample once per output from the initial triple; learned acceptance
  until the own sample (I3); `held` with a pending reseed going to `seeding` (I2); window exclusion by
  event `x` and by coverage; feed reopen and generation filter; a two-thread smoke test.
- **Engine** with the existing simulation (`tests/unit/services/audio/playout/sim.py`) and a fake or
  real feed: Q6 and I1 (a delay change moves a marker by exactly `ΔD` with the residual kept, in
  `measure` and `loop`; a gap and a clear after an underrun baseline keep the audio; `resync(0)` still a
  placement); in `loop` with a bias: placements target `c = 0`, the controller sees `c`, the residual
  domains, `β = 0` with a value, air jumps bypass the clamp and are dropped under a pending placement,
  the guard re-bases, snapshot keys.
- **Closed-loop simulation** (`tests/unit/services/audio/playout/test_air_closed_loop.py`): a
  `SimAir` produces `AirSample`s from the simulation's truth (`true_port_time_us(x)` of the simulated
  port plus a per-start hidden offset `h0`, σ(H) Gaussian, windows of 3 s every 1 s delivered 0.25 s
  after their newest air, no windows until 4 s of air have flowed (acquisition: one window plus one
  hop), `s` and `d_out` recorded per block at write time like the tap), fed through a real `AirFeed`
  into the real engine in `loop`. Parameters and tolerances:

  | Case | Port / sensor | Pass |
  |---|---|---|
  | Calibrated cold starts (fast, host latency 800 ms; slow, also 200 ms) | `noise_us = 4000`, σ(H) 500 µs, `h0` ∈ {9, 16, 23, 30, 38, 45, 52, 60} ms, `L_true = L_as = 228.7`, `D_out = D_ref = 184` | per start `|median heard error| ≤ 1.0 ms` over the audio written 20–30 s after the start; `sd ≤ 0.5 ms` over starts; one `air_seed` per start; the seed jump within 10 s of the first audio |
  | Measure control (fast) | same starts in `measure` | `sd ≥ 15 ms` (the harness is not trivially passing) |
  | Delay change `+30 ms` in `active` (fast) | `noise_us = 4000`, σ(H) 500 µs | heard error moves by `−30 ± 0.5 ms`; `air_bias_ms` within 0.2 ms; `air_trim_ms == 30.0`; one `delay` jump, no air jump in 20 s; a new start at `D_out = 214` lands at `−30 ± 1 ms` |
  | Reading slew after an underrun before the seed, C1 (fast) | `noise_us = 4000`; `starve(800)` at 1.5 s, then `slew(−30, 300)` | after the seed: heard error `≤ 1 ms`, `β == 0`, no steady jump in 30 s |
  | Learned (slow) | `noise_us = 4000`, σ(H) 500 µs, `h0` = stage A's sequence | starts 1–2 no correction; 3–8 one each, each within 1 ms of `H_ref` at that start; prints sd of starts 4–8 for the robust mean and the plain median; robust mean `≤ 2.0 ms`; a `+30 ms` delay after start 4 moves start 5 by `−30 ± 1 ms` |
  | PipeWire (slow) | sawtooth 30 ms / 5 s and 27 ms / 15 s, `noise_us = 1000`, σ(H) 500 µs | after the seed `|median heard error| ≤ 2 ms`; no `steady` jump in 60 s |
  | Lock lost (slow) | windows stopped 60 s | `held`, `air_bias_ms` constant, no jump; windows back: `active`, no jump |
  | Format change (slow) | `h0 += 5 ms` at the format change | reseed: exactly one `air_seed` of `5 ± 0.5 ms`; without the change: `seeding` → `active` with no jump |
  | Underrun with a hidden change (slow) | `starve(800)`, `h0 += 12 ms` | heard error within 1 ms again within 15 s; no "error baseline" line |
  | Calibration set mid-stream (slow) | learned value in force, calibration 20 ms above it | one `air_target` of `+20 ± 0.5 ms` |
  | Hidden step (slow) | `h0 += 25 ms` mid-stream | after ≥ 15 windows `air_bias_ms` −25 ± 0.5; then one steady jump of ≈ 25 ms within 10 s |

  The fast subset runs in ≤ 60 s (`step_ms = 2.0` where the tolerances allow); the rest is marked
  `slow` and kept under 3 min in total (CI has no per-test timeout).
- **Config/IPC/daemon/parent/API/UI**: effective rule (the four stage A tests that pinned
  `loop_not_supported_yet` change with the requirement, Q2), learned entry model and helpers,
  migration, HA translate preserve, config diff class, `set_air_calibration` encode/decode with and
  without the new fields, `_air_sensor_args` passing them, the daemon builds the feed only in `loop`,
  the sensor's windows reach the feed, lock/key/target/output delay propagate, the parent records a
  sample once per `(proc, seq)` and resends, Forget clears both, the fold's `d_out_ms` and
  `air_delay_changed`, the API model serialises the new keys, the panel line and the learned entries
  render, guidance reasons.
- **Switch off and `measure`:** explicit tests that `measure` builds no feed and no `AirBias`, the
  engine snapshot has `air_bias_state: null`, and with the switch off nothing of stage B exists.

## Stand acceptance (Q12)

Stand: HAOS VM (PulseAudio 17), ENEBY20 on hci1 (SBC 44.1 kHz), `PLAYOUT_SYNC=loop`,
`EXPERIMENTAL_AIR_SENSOR=true`, `SENDSPIN_AIR_DEBUG=1`; the reference is the desktop bridge's
headphone output on the playout engine in `measure`, playing continuously (never restarted, only
muted/unmuted by the solo alternation); headset microphone; the coherent click estimator; the A17
protocol for solos (MA mutes alternating).

- **Restarting only ENEBY20's stream.** Candidates, tried in this order, each checked by the logs
  (ENEBY20's daemon logs a new `playout output open`, the reference's does not): MA
  `players/cmd/power` off/on of the ENEBY20 member; un-sync and re-sync (`players/cmd/ungroup`, then
  `players/cmd/group` to the leader); member `stop` + `play`. The MA sendspin provider is never
  reloaded.
- **Series 1, calibrated:** first re-calibrate ENEBY20 in the wizard against the playout reference
  (the entry then carries a measured `d_ref_ms`; the migrated A17 entry's is an estimate). 8 restarts;
  per restart wait 20 s, then ≥ 20 s of clicks; `M_i` = ENEBY20 − reference. Pass: `sd(M) ≤ 2 ms`
  and `|median M| ≤ 3 ms`; one `air_seed` per start, within 10 s.
- **Series 2, uncalibrated:** Forget (calibration and learned), 8 restarts; starts 1–2 learn, 3+
  corrected. Pass: `sd(M_4…M_8) ≤ 2 ms`.
- **Q6/I1 check:** in `measure`, a delay change of `+30 ms` moves ENEBY20 by `−30 ± 1 ms` (stage A saw
  −43.6) and the log shows `playout jump: delay, 30.0 ms`.
- **Slider check in `loop`:** after series 1, `+30 ms`: ENEBY20 moves by `−30 ± 1.5 ms`,
  `air_bias_ms` unchanged, no air jump; one more restart at the new delay lands at `−30 ± 2 ms`; then
  the delay goes back.
- **CPU:** `pidstat` of ENEBY20's daemon over 60 s of music, `loop` vs `measure` (switch on in both):
  ≤ +0.3 % of one core.
- **Recorded:** `b` per start, seed times, gated windows, reseeds; the guidance with the switch off in
  `loop` (fires) and with ENEBY20 in `loop` locked (does not).

Results go into a "Stage B1 validation note" appended to this spec.

## Risks

- **The learned estimator on few samples.** Even the robust mean moves by ≈ 1.4 ms between starts
  4–8 on stage A's data, close to Q12's 2 ms. Mitigation: the simulation prints it; the stand measures it.
- **A wrong `D_ref`.** A stage A calibration whose `d_ref_ms` the migration fills from today's static
  delay is wrong by any delay change made since it was measured. Mitigation: logged; the stand
  re-calibrates first; re-calibrating replaces it.
- **PipeWire's sawtooth** biases per-window `ê` by several ms. Mitigation: the MAD gate, R14 on `c`,
  simulation cases; the Q12 stand is PulseAudio 17, PipeWire is validated in simulation only in B1.
- **The seed jump is audible** (up to 60 ms dropped or inserted once per cold start), as today's start
  and steady jumps; a delay change is a jump of the delay's size (before it was a placement of about
  the same size).
- **A congested link (#562)** moves `H` by up to hundreds of ms for ≈ 10 s; the seed may not agree
  (fallback after 12 windows), the step rule needs 15 s. Run Q12 on an uncongested link.
- **A bad calibration reference** makes `L_as` wrong (stage A: 209 vs 228.7 ms). Mitigation as stage A.
- **A learned value from a wrong tuning** is only as good as where the speaker played on average.
  Mitigation: calibration wins; Forget.
- **Config write races** between a learned write and a concurrent full config save from the UI.
  Mitigation: the parent writes through the existing locked config update; a lost sample is re-learned.

## Open questions

None open. Rev. 2's one point is settled in the design: a fold whose cells carry two delays is not
offered (`air_delay_changed`), as stage A refuses a fold across an air epoch.

## Rulings

| # | Ruling | Why |
|---|---|---|
| S-1 | `AirBias` and `EHistory` live in the engine on the feeder thread; the daemon loop hands immutable samples, lock state, target, key and output delay through a lock-protected `AirFeed` | the engine is feeder-only today; no shared mutable state (Q4) |
| S-2 | `e` is matched to a window by written stream time `x` (median of usable readings in the span, ≥ 50 % coverage); usable = no stall/underrun in progress and `s(x_port)` known | `x` is unique and monotonic within an output; S-3 handles events |
| S-3 | A window whose span contains an engine event's `x` (placement, delay/steady/air jump) or precedes the last change of `b`'s definition (target change, reseed) is not used for `b` | `e` and `E` of such a window refer to different audio states |
| S-4 | One target formula `E_equiv = H + L_eff − D_ref`; `D_out` does not enter `b` | Q4, Q7, round 2 (1) |
| S-5 | Placement offset `o = b + β` with a bias value, else the raw residual `r`, `0` for `start` | Q4 and Q6 in one rule |
| S-6 | Without a bias value `β` survives placements other than underrun/stall; with one `β = 0` and no collection except in `held`; at the seed and on resume `β` is folded into `b` | C1; the baseline-ignored risk (Q6) |
| S-7 | Reseed = seeding with the old `b` as a provisional value (format, underrun, stall, `pa_step`); the controller is frozen ≤ 15 s after a reading break; a reseed survives `held` | round 1; C1; I2 |
| S-8 | Seed: sliding triple, agreement within 2 ms → `b₀` and an `air_seed` jump of `+median E` beyond 2 ms; 12 windows without agreement → `b` from the last 5, no jump | Q8 |
| S-9 | Tracking: EMA τ 30 s on window-middle time (Δt ≤ 5 s); gate `max(10 ms, 4·1.4826·MAD)` over the last 20 accepted `b_i`, seeded with the seed's; 15 consecutive gated windows within 3 ms set `b` | Q8; I6 |
| S-10 | `held` freezes `b`; the next accepted window resumes tracking (no jump) or reseeds when one is pending; reset only on reopen | Q5; round 2 (4); I2 |
| S-11 | Calibration changes apply live (`b −= ΔL`, `air_target` jump beyond 2 ms); a learned value is accepted until this output published its own sample | I3 |
| S-12 | One learned sample per output from the first agreeing triple after reopen (`X_i`, `d_i`), in `loop` only, with a per-daemon `seq` and the key; the parent persists it | Q7; round 2 (5) |
| S-13 | Learned entries in a sibling per-player field `air_learned_latency`, one per key, ≤ 8 pairs | downgrade-safe, no cap or uniqueness interplay |
| S-14 | A delay change moves the sound by exactly `ΔD` in every mode; in `loop` with a target the static delay is a trim (`E = −(D_out − D_ref)`, `b` unchanged) | round 2 (1) |
| S-15 | Air sensor effective in `measure` and `loop`; the correction runs where the sensor runs in `loop`; no new key, no separate config function; `needs_measure` kept | Q2, Q11, round 2 (3) |
| S-16 | Guidance `air_correction_inactive` (info) with the four reasons, `air_not_locked` = never got a value after 60 s warm | Q10; I7 |
| S-17 | `set_air_calibration` and the spawn param gain optional `d_ref_ms`, `learned_ms`, `learned_d_ref_ms`, `learned_starts`, sent only when known; `_air_sensor_args` passes them | additive; I4 |
| S-18 | With a bias `error_ms` is `EMA + β` in the `c` domain; raw = `error_ms + air_bias_ms` | the number shown is the one acted on |
| S-19 | `air_seed`/`air_target` jumps bypass the 50 ms clamp and the 10 s interval and are dropped under a pending placement | Q8 |
| S-20 | One guard around every `AirBias`/`EHistory` call; an exception disables the bias for the output with a re-base to the raw domain, WARNING once per 10 min | I8 |
| S-21 | Learned reference: `off_ref` = mean of `X_i − d_i` within ±15 ms of their median, `d_ref = median(d_i)`, `latency = d_ref + off_ref`, in a pure module | round 2 (2); I5 |
| S-22 | The trim is the static delay itself; `D_ref` stored with each calibration (from the fold's `d_out_ms`) and learned entry; older calibrations migrated from `static_delay_ms`; without one, `D_out,latched` | round 2 (1); I10 |
| S-23 | A delay change (`resync(shift ≠ 0)`) is a frame-exact jump of `ΔD` against the existing schedule, no placement, in every mode; `resync(0)` stays a placement | I1 |
| S-24 | Residual domains: placement residual `o − β − (b or 0)`, EMA restart `o − (b or 0)`, the re-measure branch in the controller's domain; explicit transitions seed, resume, bias → none | I9 |
