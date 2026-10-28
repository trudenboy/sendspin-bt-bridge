# Air sensor stage B1: the engine corrects placement from the air — design

Status: draft for review, rev. 2 (2026-10-28: the controller's round-2 rulings on rev. 1's open questions). Target: the v3 line (beta channel), branch `feat/air-sensor`,
after stage A (validated 2026-10-28).
Builds on [`2026-10-28-air-sensor-design.md`](2026-10-28-air-sensor-design.md) ("the stage A spec",
in particular its "Stage A validation note" and "Stage B (outline)") and on
[`2026-10-16-bt-playout-sync-design.md`](2026-10-16-bt-playout-sync-design.md) ("the playout spec":
R14, the baseline, Stage 3/4).
Plan: [`2026-10-28-air-sensor-stage-b-plan.md`](2026-10-28-air-sensor-stage-b-plan.md).

The maintainer's decisions of 2026-10-28 (rounds Q1–Q12) and the controller's rulings on them are
binding and quoted as **Qn**. Rulings this design makes on its own are marked **Ruling S-n** and
collected in "Rulings" at the end, so a reviewer can accept or overturn each one separately. File and
line pointers refer to the branch at `7cc9df99`; paths are under `src/sendspin_bridge/` unless they
start with `tests/`, `ui/` or `docs/`.

## Goal and decisions

Stage A showed that the air sensor sees where a speaker's audio really leaves over the air, to
0.034 ms (p95) against an independent offline anchor, with σ(H) 0.2–0.9 ms within a stream. In
`measure` mode the start-to-start offset between two group members is still 9–60 ms (audible): the
pulsesink sensor of the playout engine cannot see where a new PulseAudio stream lands in a Bluetooth
stream that never stops, and its 20 ms dead band leaves the rest alone.

Stage **B1** closes the placement part of that gap: in `PLAYOUT_SYNC=loop` the engine learns, per
output, the **bias** between what the pulsesink clock says and what the air says, and places audio
so that the air-derived error is zero. The rate loop (PI + resampler, the playout plan's 3.1–3.4)
is **B2** and not part of this design.

| Q | Decision (binding) |
|---|---|
| Q1 | Scope B1: placement correction from the air sensor only (bias-aware jumps and placements). The rate loop is deferred to B2. |
| Q2 | Lives in `PLAYOUT_SYNC=loop` (loop = measure + air correction). The air sensor runs in `loop`; `loop_not_supported_yet` and the measure-only gate in the daemon go. `measure` stays observe-only. |
| Q3 | Target: calibrated → `E = 0`. Uncalibrated → the learned target (Q7). No data → `loop` behaves as today (`measure` semantics with `J = 10 ms`). |
| Q4 | A slow bias filter `b = e_pulse − E_equiv` (`E_equiv = E` when calibrated, `H − H_target` when learned), seeded per output from the median of the first three confirmed windows. The controller input and every placement target use `e_pulse − b`. Thread-safe handoff sensor (daemon loop) → engine (feeder thread) through a lock-protected shared object; `e_pulse` is matched to the window's audio, not to the window's arrival time. |
| Q5 | Lock lost: hold the last `b` for the rest of the output; reset only on reopen. |
| Q6 | First task: the measure-mode implicit snap. Placements keep the current residual instead of zeroing it, so a delay change moves the audio by exactly its delta. Also covers the baseline that placements ignore. |
| Q7 | Learned target (estimator replaced by round-2 ruling 2): invariant `X = D_out − H`; per start `X_i` = median of the first three confirmed windows; `X_ref` = median of the last 8 starts; persisted per player keyed (codec, rate, adapter) next to the air latency entries, source `learned`; active from the 3rd start; a calibration (`X_ref := L_as`) wins; Forget clears both. |
| Q8 | Seed: when the three windows agree within 2 ms, full correction, no 50 ms clamp. Dead band 2 ms. Then `b` tracks slowly (τ ≈ 30 s). |
| Q9 | The pulsesink fast loop stays, in `loop`, on the corrected error `e_pulse − b`, `J = 10 ms`. |
| Q10 | Telemetry/UI: the playout snapshot adds `air_bias_ms`, `air_target` (calibrated / learned / null), `air_corrections`; timing panel line "Air correction: calibrated / learned (N starts) / no data"; a guidance issue when a speaker is in `loop` but the air sensor is off, not effective or not locked (it works as `measure`). |
| Q11 | `loop` correction requires `EXPERIMENTAL_AIR_SENSOR`; without it `loop` is today's behaviour plus guidance. Per speaker, default off. |
| Q12 | Acceptance on the stand: 8 cold starts of ENEBY20 in `loop` against a warm (not restarted) reference on the playout engine; `sd(M) ≤ 2 ms` (measure today 4.8); calibrated `|median M| ≤ 3 ms`; uncalibrated `sd(M) ≤ 2 ms` from the 4th start; CPU not noticeably higher. Only ENEBY20's stream is restarted (via MA), so the reference keeps running. |

Controller rulings (binding): a format change resets `b` and reseeds (the sensor reopens anyway);
`stream/clear` keeps `b`; the sensor's `E` takes `d_out` as the median of 5 windows and lags 3 of 5
windows after a change, so `b` never uses the snapshot `E`. (The first round also ruled "a delay
change offsets `b` by `+ΔD`"; round 2 replaced it, below.)

Controller rulings, round 2 (2026-10-28, on rev. 1's open questions; binding):

1. **A delay change moves the sound by exactly `ΔD` in `loop` too**, as in `measure` after the Q6 fix
   (the user's slider and the wizard's applied static delays alike). In `loop` the static delay acts as
   a trim on the target and `b` is unchanged. The target is defined relative to the delay at which
   `L_eff` was measured or learned: `E_target = −(D_out − D_ref)`, with `D_ref` saved alongside the
   calibration or learned entry. Formalised in "Time bases under a delay change".
2. **Learned estimator:** the mean of the samples within ±15 ms of their median (not the plain median).
3. **No new per-player key;** the existing `playout_sync_enabled=false` opt-out applies.
4. **After `held`,** tracking resumes on the next accepted window, without a jump or a reset.
5. **Learning runs in `loop` only.**

Stand facts used below (stage A note): σ(H) within a stream 0.2–0.9 ms; start-to-start offsets
9–60 ms in `measure`; live `H` tracks engine jumps to the millisecond; windows lag real time by
≈ 1.5 s (half a window) plus the host latency (PulseAudio 200–800 ms) plus batching; ≈ 2.6 % of a
core per streaming speaker on the 2-vCPU VM for the sensor.

## Goals and non-goals

Goals (B1):

- In `loop` with the air sensor effective and locked: a cold start converges to the air target in
  one correction, within ≈ 10 s of the first audio on HAOS (800 ms PulseAudio latency); the
  start-to-start spread of the speaker against a warm reference drops to `sd ≤ 2 ms`.
- A calibrated speaker plays on the schedule (`|E| ≤ 3 ms` by microphone) at the delay it was
  calibrated with; an uncalibrated one plays where it played on average in its previous starts (the
  learned target).
- A delay change moves the sound by exactly `ΔD` in every mode (`measure`, `loop` with or without a
  target), so the slider and the wizard keep their meaning.
- In `measure` (and in `loop` without air data): a placement keeps the residual; a delay change moves
  the audio by exactly the delta (Q6).
- No new privilege, no extra CPU worth measuring, no new failure mode for playback: anything the air
  path does wrong disables the correction for that output, never the engine.

Non-goals (B1): the rate loop and frame-level corrections (B2: the playout plan's 3.1–3.4); speakers
without an effective air sensor (they keep today's `loop`); per-speaker offsets of the playout plan's
Stage 4 (the learned target and the calibration replace them for Bluetooth speakers with the sensor);
a change to how `measure` reports or corrects beyond Q6; changing host audio settings; the sync wizard
(it keeps working, see "Interaction with the delay slider and the wizard").

## Definitions

Times are `CLOCK_MONOTONIC_RAW` µs, signs as in the stage A spec (positive error = late).

| Symbol | Meaning |
|---|---|
| `x` | Output stream time of the playout engine (ns written since the output opened); monotonic within one output, restarts at every reopen. |
| `s(x)` | The engine's schedule (`ScheduleMap`): RAW time at which `x` should leave the PulseAudio port; the output delay is already subtracted. |
| `D_out` | The player's output delay (the daemon's `_player_delay_ms`). `d_out_i` = the delay a window's audio was written with (`T − s` stored per tap cell). |
| `e` (`e_pulse`) | The pulsesink error of one reading: `t − s(x_port)` (engine.py:293-296). |
| `e_w` | The error predicted at the write head (engine.py:302-306); equal to `e` in steady state, minus corrections already written. |
| `H` | Host placement of a confirmed air window: `t_air − s` at the window's middle (stage A). |
| `L_eff` | The target latency: the calibrated `L_as` when one exists for the current key, else the learned `X_ref` once it is active, else none. |
| `D_ref` | The output delay `L_eff` refers to: the delay in force when the calibration was measured, or the reference delay of the learned samples (round-2 ruling 1). |
| `E_equiv` | The error against the target: `E_equiv = H + L_eff − D_ref = E + (D_out − D_ref)`, i.e. the stage A air error `E` measured against `E_target = −(D_out − D_ref)`. Equal to `E` when `D_out = D_ref`; for the learned target it is `H − H_ref` (Q4's `H − H_target`). It does not contain `D_out`. |
| `b` | The bias: `b = e − E_equiv` for the same audio. Physically `b = D_ref − (true latency beyond the port)`; it does not change with `D_out`. |
| `c` | The corrected error: `c = e_w − b − β` (the controller input in `loop` with a bias). |
| `β` | The engine's error baseline after an underrun or stall (R14), today's `_baseline_us`. |
| `r` | The raw residual the engine currently reports: its error EMA plus `β` (`_error_ema_us`), plus `b` when a bias exists. |
| `o` | The placement offset: a placement aligns the next frame to `audible − o`, so that `e = o` right after it. |
| `X_i`, `d_i` | Learned sample of one output start: `X_i = median(d_out_i − h_i)` and the delay `d_i = median(d_out_i)` over the seed triple; `X_i − d_i = −H_i`, the start's raw placement. |

## Why a bias and not the air error directly

The air error arrives late and slowly: a window covers 3 s, its middle is 1.5 s old when it closes,
the audio was written a host latency (≤ 800 ms) before it reached the port, and windows come once per
second. A controller acting on `E` directly would act on what happened 2–4 s ago and would be blind
to its own corrections for that long. The pulsesink reading comes every 100 ms and sees every
placement and jump at once, but it is wrong by a per-start amount (the very variance stage A found).
So the fast loop keeps `e` and the air sensor estimates how wrong `e` is: `b`. `b` changes only when
the hidden part of the path changes (a new PulseAudio stream, an underrun), so a slow filter with a
fast seed is enough. A delay change does not change it (next section).

`b` does **not** change when the engine moves audio: a placement or a jump moves `e` and `E_equiv`
by the same amount. That is what makes the windows that come in after a correction still usable for
`b`, as long as one window does not mix audio from before and after it (S-3).

## Architecture

```
 daemon asyncio loop                                        playout-feeder thread (per output)
 ───────────────────────────────────────────────────       ─────────────────────────────────────────────────
 AirSensor (stage A, 1 Hz step, windows in executor)          PlayoutEngine (loop mode, an AirFeed given)
   _accept(win) ──► on_window(AirSample)  ─┐                     sense() every 100 ms:
   lock state, confidence, key per step ───┤                       EHistory.add(x_port, e, usable)
 set_air_calibration (IPC) ─► target ──────┤                       view = AirFeed.take()  ──────────────┐
                                           ▼                       action = AirBias.update(view, hist)  │
                                  AirFeed (threading.Lock)         c = e_w − b − β → LoopController(J=10)│
                                    samples (≤ 32, drop oldest)    next_block():                        │
                                    lock, target, key, seq         audible − o  (o = b + β | r)  ◄──────┘
                                    min_tap_gen (reopen)           pending air jump (air_seed/air_target)
                                           ▲                     snapshot(): air_bias_ms, air_target, …
 PlayoutPlayer._reopen_air_tap ─ reopen(tap.gen) ┘               air_learned {seq, x_ms, key} ─► status ─► parent
```

- **The bias state lives on the feeder thread (Ruling S-1).** The engine, its controller and its
  placements are feeder-only today (gst_output.py:8-15). Keeping `AirBias` there means its state is
  never shared: the daemon loop only hands immutable samples and three small values (lock, target,
  key) across, through `AirFeed`, whose lock is held for a deque append or a swap. No callback runs
  across threads, nothing blocks the feeder.
- **Matching by `x` (Ruling S-2).** Each confirmed window carries the `x` span of the written cells it
  matched (the tap gains an `x` per cell; air_tap.py stores `s`, `T`, kind today). The engine keeps a
  30 s history of its readings `(x_port, e, usable)` and takes `e` for a window as the median of the
  usable readings with `x_port` inside the window's span. `x` is unique and monotonic within an
  output; `s` is not (placements and inserts step it back), and RAW arrival time is off by the
  window and host lag. A reading is usable when the port has passed the last event's settle point
  (`_settle_x_ns`), no stall or underrun is in progress, and `s(x_port)` exists. A window needs at
  least 50 % of the readings its span should hold (`W · 10 Hz · 0.5` = 15), else it is not used for
  `b` (counted).
- **Reopen.** `adopt_output` builds a new engine (gst_output.py:309-351), so a new `AirBias` starts
  in `none`. `_reopen_air_tap` calls `feed.reopen(tap.gen)` after the tap reset: queued samples are
  dropped and samples from an older tap generation are refused at `push`, so a window computed on the
  old output and accepted after the reopen never reaches the new engine (the sensor already drops
  windows whose tap generation changed while they ran, air_sensor.py:654-657).
- **Measure stays observe-only (Q2).** `AirBias` exists only when the engine runs in `loop` and the
  daemon built an `AirFeed`; in `measure` the sensor runs exactly as in stage A and nothing feeds back.

### The engine's three changes

1. **Controller input.** With a bias value, `c = e_w − b − β` instead of `e_rel = e_w − β`
   (engine.py:312). R14 is unchanged: persistence 5 s, median of 5, 90 % one sign, one steady jump per
   10 s, clamp 50 ms, `J = 10 ms` in `loop` (controller.py:20, Q9).
2. **Placement target.** Every user of `_audible()` that places or anchors (the scheduler's placements
   `start`, `underrun`, `stall`, `clear`, `delay`, `gap`, `starved`, `format`, and the silence
   re-anchors of `reanchor`) gets `audible − o` (engine.py:190, 196, 207, 213). `o` is (Ruling S-5):
   - `b + β` when a bias value exists (active, held, or a provisional value while reseeding): right
     after the placement `c = 0`, the air error is zero;
   - otherwise the raw residual `r` (Q6), so the placement keeps the audio where it is relative to its
     old schedule and only the event itself (the delay's shift, the gap) moves it;
   - `0` for the first placement of an output (`start`: nothing is known yet).

   A placement that supersedes a decided steady jump keeps `r` as well: the controller resets its EMA
   to the post-jump value when it decides the jump (controller.py:130), so `r` already excludes it.
3. **Air jumps.** `AirBias` returns at most one action per update: a jump of `jump_us` with reason
   `air_seed` (first seed of an output or a reseed) or `air_target` (the target changed). The engine
   executes it like a steady jump (frame-exact at the write head, scheduler.py:193-198, 361-382) but
   bypassing the 50 ms clamp and the 10 s interval (Q8); it is dropped when a placement is pending
   (engine.py:200-211), because that placement already targets `c = 0`. Logged as
   `playout jump: air_seed, 37.2 ms`, counted in `air_corrections`.

### Q6: the measure-mode implicit snap (first task)

Today `_record_placement` restarts the error at 0 (engine.py:497-506) and the scheduler aligns the next
frame to `audible` exactly (scheduler.py:330-359): every placement removes whatever residual the
pulsesink saw, below or above the dead band. A delay change of `+30 ms` therefore moved ENEBY20 by
`−30 − 13.4 = −43.6 ms` on the stand (the residual `e_w = +13.4 ms` since the start was zeroed), and a
gap or clear after an underrun moves the audio by the baseline `β` that the engine had just declared
a reading artefact (the R14 baseline is zeroed at engine.py:500).

Fix (applies to `measure` and to `loop` without a bias):

- the placement offset `o = r` (above), so `e` stays at `r` after the placement;
- `_record_event` sets the published EMA to `o` instead of 0, and `_record_placement` calls
  `controller.placed(t, residual_us = o − β, quiet_wait=True)` (the existing signature,
  controller.py:72);
- `β` survives placements other than `underrun`/`stall` (those still start a fresh baseline
  measurement); `_baseline_degraded` likewise.

The `start` placement still aligns to `audible` (`o = 0`). A delay change then moves the audio by
exactly `−ΔD` (the shift), the engine still logs `playout jump: delay, <size>` with
`size = (audible − o) − s(x_w)`.

### The `AirBias` state machine (per output)

States (Q10's `air_bias_state`): `none` (no target, or the correction cannot run), `seeding`
(a target is known, collecting the seed triple; with or without a provisional `b`), `active`
(`b` known and tracked), `held` (`b` frozen: the sensor lost its lock).

```
                      target known                      agreeing triple (full seed jump if |E|>2 ms)
   reopen ─► [none] ───────────────► [seeding] ────────────────────────────────────────────► [active]
               ▲  target forgotten,     ▲  │ 12 windows, no agreement: b = median(last 5), no jump ─┘│ ▲
               │  no learned fallback   │  │                                                        │ │
               │  (audio kept)          │  └─ lock lost (initial: stays; provisional: → held) ──┐   │ │
               │                        │ format / underrun / stall / pa_step (b → provisional)  │   │ │
               └────────────────────────┴────────────────────────────────────────────────────────┼───┘ │
                                                                                                  ▼     │
                                                                          [held] ── next accepted window┘
                                                          (b frozen; underrun/stall → today's baseline on c)
```

| Event | `none` | `seeding` | `active` | `held` |
|---|---|---|---|---|
| reopen (new engine) | — (start) | → `none` | → `none` | → `none` |
| target appears (a calibration at any time; a learned value with ≥ 2 samples only at the output's first update) | → `seeding` | target updated | `ΔL` applied (row below) | `ΔL` applied |
| accepted window (confidence ≥ 0.5, clean span, coverage) | learning triple only (S-12) | into the triple; agreement → `active` + `air_seed` jump if `|E_med| > 2 ms` | tracking update (S-9) | → `active`, tracking update, no jump (S-10) |
| no agreement after 12 windows | — | `b` = median `b_i` of the last 5 → `active`, no jump (R14 corrects `c`) | — | — |
| format change | — | triple restarts | → `seeding`, `b` provisional | `b` provisional, stays `held` |
| `stream/clear` | — | — | `b` kept; the clear placement uses `o = b` | same |
| delay change `ΔD` | — (the placement keeps `r`, Q6) | windows across the event dropped (S-3); `b` unchanged | `b` unchanged; the placement keeps `e = b`, the sound moves by `−ΔD` (S-14) | same |
| underrun / stall / `pa_step` | today's baseline | triple restarts; controller frozen ≤ 15 s if `b` provisional | → `seeding`, `b` provisional, controller frozen ≤ 15 s, no baseline | stays `held`; today's baseline measured on `c` |
| reseed timeout (15 s frozen) | — | → `active` with the provisional `b`; baseline measured on `c` | — | — |
| lock lost (sensor not `locked`/`holding`) | — | initial: waits; provisional: → `held` | → `held` | — |
| calibration set / changed (`L_eff − D_ref` moves by `ΔL`) | → `seeding` | target updated | `b −= ΔL`; `air_target` jump `+ΔL` if `|ΔL| > 2 ms` | `b −= ΔL`; jump as in `active` |
| calibration forgotten | — | falls back to learned (target updated) or → `none` | to learned: `ΔL` as above; no learned: → `none`, audio kept (`o = r`) | same |
| learned value changes (parent resend) | ignored until reopen (S-11) | ignored | ignored | ignored |
| 15 consecutive gated windows within 3 ms | — | — | `b` = their median, logged, no jump | — |
| internal error | → `none` for the output (`reason: internal_error`) | same | same | same |

Notes on the rows:

- **Seed triple (Ruling S-8).** The seed uses the newest three accepted windows that lie wholly after
  the last engine event's settle point and the last change of `b`'s definition (S-3). They agree when
  `max(E_equiv) − min(E_equiv) ≤ 2 ms` (Q8). Then `b₀ = median(b_i)` and, if
  `|median(E_equiv)| > 2 ms` (dead band, Q8), an `air_seed` jump of `+median(E_equiv)`: positive =
  late = drop, the controller's convention. If three do not agree, the oldest is dropped and the next
  window joins (sliding); after 12 windows without agreement `b` is seeded from the last five without a
  full correction and R14 corrects `c` under its normal rules.
- **Why the jump uses `E` and not `c`.** `median(E_equiv)` has σ ≈ 0.2–0.9 ms/√3; `c` carries the
  pulsesink noise (σ ≈ 4 ms per reading on PulseAudio 17, a sawtooth up to 30 ms on PipeWire). No event
  happened since the triple's audio (by construction), so the error now equals the triple's.
- **Reseed (Ruling S-7).** "Reset + reseed" (controller ruling for a format change) is implemented as
  seeding with the old `b` as a provisional value: placements and the controller use it until the new
  triple replaces it (no filtering across the two). The new triple applies its own seed jump when the
  air error is beyond the dead band. After an underrun, stall or `pa_step` the controller is frozen
  until the reseed (≤ 15 s, `RESEED_TIMEOUT_S`) instead of measuring the R14 baseline: `b` from the
  air absorbs a reading offset and a hidden change alike. If the reseed does not come in time (no lock,
  windows that disagree), the controller resumes on the provisional `b` and the R14 baseline is
  measured on `c`.
- **Held (Ruling S-10).** Q5 says hold the last `b` and reset only on reopen. The design reads
  "hold" as "never reset or decay while the lock is lost": when the sensor locks again, the next
  accepted window resumes tracking through the gate (no jump, no reseed). An underrun in `held` uses
  today's baseline on `c`.
- **Target changes (Ruling S-11).** `E_equiv = H + L_eff − D_ref`, so a change of `L_eff − D_ref` by
  `ΔL` changes every `E_equiv` by `+ΔL` and `b` by `−ΔL`: the audio is now `ΔL` late against the new target
  and gets one `air_target` jump of `+ΔL` (no clamp, Q8) when beyond the dead band. A calibration saved
  in the wizard therefore takes effect at once. The learned value is latched when the output opens
  (the value the feed holds at the engine's first update); later learned values are ignored until the
  next reopen, because the parent resends after every new sample, including the one this very output
  produced (otherwise the 2nd start's own sample would activate the learned target in the 2nd start,
  not the 3rd). A calibration forgotten mid-output falls back to the latched learned value.

### Tracking (Ruling S-9)

For every accepted window in `active` (and the first one after `held`):

```
b_i  = ê(x_lo, x_hi) − (h_i + L_eff − D_ref)            ê = median e over the window's x span
gate = max(10 ms, 4 · 1.4826 · MAD(last 20 b_i))
|b_i − b| > gate  → not used (air_bias_gated += 1); 15 consecutive gated within 3 ms of each other → b = their median
else              → b += (1 − exp(−Δt / 30 s)) · (b_i − b),  Δt = at_i − at_prev clamped to [0, 5 s]
```

Δt is the air time between window middles (≈ 1 s), so a burst of windows after a pause does not count
as more evidence than the time it covers. The gate bounds the effect of a wrong lag that passed the
sensor's gate and continuity rule (one 50 ms outlier would move an unguarded `b` by 1.7 ms). Its MAD
term lets PipeWire's sawtooth (window medians spread by several ms) through. The step rule absorbs a
persistent hidden change (a PulseAudio skip that moved the audio, a link path change) after 15 s; a
congested link whose `H` swings for ≈ 10 s and comes back (#562) is not chased.

### Learned target (Q7, Ruling S-12)

- **Sample.** Once per output, from the first agreeing triple after the reopen (not after a reseed):
  `X_i = median(d_out_i − h_i)` and `d_i = median(d_out_i)`, i.e. the `L_eff` that would have made that
  start's raw placement the target, and the delay it refers to. The triple precedes any seed jump, so
  `X_i − d_i = −H_i` is the raw start placement whatever the target. Learning runs in `loop` only
  (round-2 ruling 5), also while a calibration exists (so Forget of the calibration can fall back to it).
- **Publish.** The engine snapshot carries `air_learned = {"seq", "x_ms", "d_out_ms", "codec", "rate", "adapter"}`;
  `seq` is a per-daemon counter kept in the `AirFeed` (it survives engine rebuilds), the key is the
  feed's key at the seed (codec and adapter from the sensor's source, rate from the newest batch). No
  key → no sample.
- **Persist.** The parent records a new `(daemon process, seq)` once: appends `(x_ms, d_out_ms)` to the
  entry for that key (≤ 8 samples, oldest dropped), recomputes `d_ref_ms` and `latency_ms` (below), counts
  `starts`, stamps `measured_at`, writes the config off the loop, and resends `set_air_calibration`
  with the learned value. Writes come at most once per output start (a seed needs ≈ 7 s of audio), so
  no further debounce.
- **Use.** The parent sends the learned value only with ≥ 2 samples ("active from the 3rd start").
  The daemon's target is `L_as` when a calibration exists for the key, else the learned value, else
  none (Q7: calibration wins).

- **Estimator (round-2 ruling 2, Ruling S-21).** With the raw placements `−H_i = X_i − d_i`:
  `off_ref = mean{ o ∈ (X_i − d_i) : |o − median(X_j − d_j)| ≤ 15 ms }`, `d_ref_ms = median(d_i)`,
  `latency_ms = d_ref_ms + off_ref`. Then `L_eff − D_ref = off_ref = −H_ref`: the target is the robust
  mean raw placement, independent of the delays the samples were taken at (a delay change in a learning
  start moves the sound and leaves `H` unchanged, next section). On stage A's own cold-start numbers
  (`H` = −45.2, −32.4, −31.4, −44.5, −44.9, −36.3, −44.7, −36.4 ms) the target used at starts 4–8 moves
  by `sd = 1.4 ms` between starts (all samples lie within ±15 ms of the median); the plain median of
  Q7 would move by 5.0 ms (the starts form two clusters 10 ms apart and the median flips between them),
  which alone would fail Q12's uncalibrated `sd(M) ≤ 2 ms`. The closed-loop simulation prints both.

### Time bases under a delay change (round-2 ruling 1, Ruling S-14)

What moves when `D_out` changes by `ΔD` (from the code):

- **Play time.** aiosendspin's `compute_play_time` returns `client_time − D_out` (bridge_daemon.py:1265:
  "a larger delay plays earlier (play time = client time − delay)"). The scheduler records a program
  frame's play time as its schedule (`_start_program`/`_continue_program`, scheduler.py:390-398), so
  `s(x) = t_client(x) − D_out`.
- **The change.** The daemon applies `shift_us = old − new = −ΔD` to the queued chunks and to the
  pending play times (bridge_daemon.py:1288-1300) and calls `player.resync(shift)`; the engine shifts
  the queue and requests a `delay` placement (engine.py:149-153, scheduler.py:126-137). New chunks get
  the new play time from `compute_play_time`. So for audio of the same client time: `s' = s − ΔD`.
- **The tap** stores `T = s + D_out` per cell (`d_out = T − s`, air_tap.py): `T' = T = t_client`,
  `d_out' = d_out + ΔD`.
- **The air.** A frame leaves the port at `s + e` and the air at `t_air = s + e + P` (`P` the hidden
  path), so `H = t_air − s = e + P`; the sound comes `L_true` later. Stage A's air error
  `E = H + L_as − D_out = t_sound − t_client` (with `L_as = L_true`).

What the ruling requires: after the change the sound of every frame is `ΔD` earlier than before,
`t_sound' = t_sound − ΔD`, exactly as in `measure` after Q6 (the placement keeps `e`). Since
`s' = s − ΔD`, that holds iff `e' = e`, i.e. iff `H' = H`. So the correction must hold `H` (not `E`)
across a delay change, and its target must not contain `D_out`:

```
E_equiv = H + L_eff − D_ref = E + (D_out − D_ref)        target E_equiv = 0  ⇔  E = −(D_out − D_ref)
b = e − E_equiv = (D_ref − L_eff) − P                    independent of D_out: unchanged by ΔD
```

The delay placement uses `o = b`, so `e' = b = e` (in `active`, `c ≈ 0` before and after): the frame
written at `s'` leaves at `s' + b`, the sound moves by exactly `−ΔD`; `E_equiv` is unchanged, so no
air jump follows and the windows after the change confirm `b`. Windows that straddle the `delay`
placement are dropped (S-3), as for any event; per-window `d_out` no longer enters `b` (it is still
recorded for the learned samples and the stage A telemetry `E`). With `D_out = D_ref` the target is
`E = 0` (Q3).

Where `D_ref` comes from and where the trim lives (Ruling S-22):

- **The trim is the static delay itself.** Nothing else is stored per output or reset at a reopen:
  `D_out` is already persisted (the player's `static_delay_ms`, MA's `sendspin_static_delay`), and at
  every start the target is `E = −(D_out − D_ref)` again. Raising the slider by 30 ms after a calibration
  makes the speaker play 30 ms earlier than the schedule, now and at every later start, until the
  slider is moved back.
- **Calibrated:** `D_ref` = the output delay the wizard's fold was measured with, `d_ref_ms` in the
  calibration entry. The daemon's written fold already works on `T = s + D_out` per cell; its answer
  gains `d_out_ms` (the median `T − s` of the folded cells), and the parent stores it with the entry.
  `L_as = A − H_cal` itself is unchanged (stage A: the delay cancels in it).
- **Learned:** `D_ref = d_ref_ms = median(d_i)` with `latency_ms = d_ref_ms + off_ref` (above), so
  `L_eff − D_ref = −H_ref` whatever the delays during learning.
- **Stage A entries without `d_ref_ms`** (calibrations saved before this stage; stage A is unreleased,
  the stand has one): the config migration fills `d_ref_ms` from the player's `static_delay_ms` at the
  time of the migration (logged at INFO once per entry); with no static delay stored, `0`. An entry the
  daemon receives without `d_ref` (an older parent) latches `D_ref = D_out` at the output's first update.
- **The wizard** keeps working unchanged: a static delay it applies to a `loop` member moves the sound
  by exactly that delay, as for a `measure` member; a calibration it saves applies live (S-11) and from
  then on the delay in force at the fold is the reference.

The bridge logs `Output delay changed … ms; air target trim −(D_out − D_ref) = … ms` at INFO for a
change in `loop` with a target.

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
| `GATE_HISTORY` | 20 windows | the MAD of the last 20 s |
| `STEP_WINDOWS`, `STEP_AGREE_US` | 15, 3 000 | a hidden change persisting 15 s is real; #562's swings last ≈ 10 s |
| `RESEED_TIMEOUT_S` | 15 | ≥ host latency (0.8 s) + one window (3 s) + three hops + margin; the R14 baseline's own timeout is 6 s of readings |
| `DT_MAX_S` | 5 | a filter step never counts more than 5 s of evidence |
| `E_HISTORY_S` | 30 | windows reach back ≤ 6 s (3 s span + 1.5 s + host latency + batching); 30 s covers a busy executor |
| `E_MIN_COVERAGE` | 0.5 | ≥ 15 of the ≈ 30 readings a 3 s span holds |
| `FEED_MAX` | 32 samples | ≈ 30 s of windows; drop-oldest |
| `LEARN_MIN_SAMPLES` | 2 | Q7 "active from the 3rd start" |
| `LEARN_KEEP` | 8 | Q7 |
| `X` range | 0 … 2 000 ms | as `latency_ms` (stage A) |

## Telemetry, API and UI

### Playout snapshot (engine, `DeviceStatus.playout`, API `PlayoutTiming`)

| Key | Type | Meaning |
|---|---|---|
| `air_bias_state` | `none`/`seeding`/`active`/`held` or null | null when the engine has no `AirBias` (measure, off, switch off) |
| `air_bias_ms` | float or null | `b` (provisional values included); null without a value |
| `air_target` | `calibrated`/`learned` or null | Q10 |
| `air_target_ms` | float or null | `L_eff` in use |
| `air_d_ref_ms` | float or null | `D_ref` in use |
| `air_trim_ms` | float or null | `D_out − D_ref`: the delay trim; the target is `E = −air_trim_ms` |
| `air_learned_starts` | int or null | samples behind the learned value in use |
| `air_corrections` | int | `air_seed` + `air_target` jumps in this output |
| `air_last_correction_ms` | float or null | the last one's size |
| `air_bias_gated` | int | windows the gate refused (diagnostics) |
| `air_bias_reason` | str or null | why `none`/`held`: `no_target`, `not_locked`, `internal_error` |
| `air_learned` | object or null | status only (parent persistence); not in the API model |

`error_ms` in `loop` with a bias value is the EMA of `c` (the error the controller acts on, ≈ the air
error); the raw pulsesink error is `error_ms + air_bias_ms` (Ruling S-18). The `PlayoutTiming`
description says so.

### Device API

- `GET /api/v1/devices/{id}`: `timing.playout` gains the keys above; `audio.air_learned_latency`
  lists the learned entries (as `audio.air_to_sound_latency` lists the calibrations).
- `DELETE /api/v1/devices/{id}/air-calibrations/{codec}/{rate}/{adapter}` and
  `DELETE /api/v1/devices/{id}/air-calibrations` remove the calibration **and** the learned entry
  for the key (or all of both), Q7; 404 `unknown_calibration` only when neither existed. Applied live.

### UI

- Timing panel, air block, when `timing.playout.mode == "loop"` and `timing.air_sensor` is not null:
  "Air correction: calibrated (228.7 ms)" / "learned (6 starts)" / "no data (learning: 1 of 2
  starts)" / "inactive: not locked"; then "Air bias +12.3 ms", "Air corrections 1" and, when `air_trim_ms` is
  not 0, "Delay trim −30.0 ms (target 30.0 ms early)".
- The calibrations list shows learned entries too: "Learned: 228.1 ms · SBC · hci1 · 6 starts" with
  the same Forget (which forgets both).
- Settings: the `EXPERIMENTAL_AIR_SENSOR` help text says it runs in `measure` (observe) and `loop`
  (corrects).
- en and ru.

### Logs

| Level | Message |
|---|---|
| INFO | `playout jump: air_seed, 37.2 ms` / `air_target`; `air bias seeded at 12.3 ms (E −37.2 ms, 3 windows within 0.6 ms)`; `air bias held (sensor no_lock)`; `air bias step: 12.3 → 25.1 ms`; `Output delay changed 184→214 ms; air target trim −(D_out − D_ref) = −30.0 ms`; learned sample recorded (parent: value, key, samples) |
| WARNING (once per 10 min) | `AirBias` internal error (first with traceback); learned sample not persisted (config write failed) |
| DEBUG | per window with `SENDSPIN_AIR_DEBUG=1`: `b_i`, `ê`, coverage, gated; the engine's 10 s summary gains `b` and the state |

### Guidance (Q10, Ruling S-16)

New registry entry `air_correction_inactive` (layer `audio`, severity `info`) with a producer in
`recovery_assistant.py`, for a device whose effective playout mode is `loop`:

| Reason code | When |
|---|---|
| `air_switch_off` | `EXPERIMENTAL_AIR_SENSOR` is off (the device's `air_sensor` is null) |
| `air_not_effective` | the sensor is off for this player (`air_sensor.state == "off"`, e.g. not Bluetooth) |
| `air_unavailable` | `unavailable`, `unsupported_codec` or `unsupported_input` (the existing air issues carry the detail) |
| `air_not_locked` | warm playback ≥ 60 s (`playout.warm_s`) and `air_bias_state` is `none` with reason `not_locked`, or still `seeding` without a provisional value |

Text: "This speaker is in loop mode but the air correction is not running; it plays as in measure
mode." Learning without a target is not an issue (the panel shows it).

## Configuration and persistence

### Effective rules (Q2, Q11, Ruling S-15)

- `air_sensor_effective(player_cfg, cfg)`: on when the switch is on, the effective playout mode is
  `measure` **or `loop`**, and the backend is Bluetooth. Reasons `switch_off`, `needs_measure` (mode
  `off`; the code stays for API/UI compatibility, its text becomes "needs playout sync in measure or
  loop mode"), `not_bluetooth`. `loop_not_supported_yet` goes.
- `air_correction_effective(player_cfg, cfg) -> (bool, reason)`: on when the effective mode is `loop`
  and the air sensor is effective; reasons `not_loop` (observe-only), `switch_off`, `needs_measure`,
  `not_bluetooth`. Q11's "per speaker, default off" is read as: the rule is evaluated per speaker and
  both inputs default off (`PLAYOUT_SYNC=off`, `EXPERIMENTAL_AIR_SENSOR=false`; a speaker opts out with
  the existing `playout_sync_enabled=false`). No new per-player key (round-2 ruling 3).
- The daemon builds the sensor in `measure` and `loop` (bridge_daemon.py:257 gate goes) and the
  `AirFeed` in `loop` only. Spawn params: unchanged `playout: {"mode"}`; `air_sensor` gains
  `learned_ms`/`learned_starts` **only when a learned value exists** (the existing spawn test keeps its
  exact dict).

### IPC (Ruling S-17)

`set_air_calibration` (payload `v: 1`, no protocol bump) gains optional fields, sent only when
known: `d_ref_ms` (the calibration's reference delay, 0…5000), `learned_ms` (0…2000, clamped like
`latency_ms`), `learned_d_ref_ms` (0…5000) and `learned_starts` (int ≥ 2; the three learned fields come
together). An older daemon ignores them (decoders read named fields only, commands.py:417-423).
`SetAirCalibration` becomes `(latency_ms, d_ref_ms=None, learned_ms=None, learned_d_ref_ms=None,
learned_starts=None)`. The written fold's status answer gains `d_out_ms` (S-22).

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
  `adapter` (MAC pattern), `source: "learned"`, `samples_ms` (1–8 floats, each 0…2000), `d_out_ms`
  (the same length, each 0…5000), `d_ref_ms` and `latency_ms` (= `learned_reference(samples_ms,
  d_out_ms)` within 0.05 ms, validated), `starts` (int ≥ `len(samples_ms)`), `measured_at`. At most 8
  entries per player, one per key.
- Why a sibling field: `Player` is `extra="allow"`, so an older v3 beta that does not know the field
  keeps it untouched on downgrade, whereas a `source: "learned"` entry inside `air_to_sound_latency`
  would fail that version's strict `AirLatencyEntry` (`source` is a closed `Literal`) and be dropped;
  and the two kinds would share one 8-entry cap and one uniqueness key, so frequent learned writes
  would evict calibrations. "Next to the air latency entries" (Q7) holds: same player, same key.
- Helpers in `config/players.py`: `valid_air_learned_entries`, `resolve_air_learned(player_cfg, codec,
  rate, adapter) -> tuple[float, float, int] | None` (`(latency_ms, d_ref_ms, starts)`, None below 2
  samples), `record_air_learned_sample(entries, key, x_ms, d_out_ms, now_iso)`, `forget_air_learned(entries,
  codec=None, rate=None, adapter=None)`, `learned_reference(samples_ms, d_out_ms) -> (latency_ms, d_ref_ms)`
  (S-21).
- Migration: `config/migration.py` gains `_clean_air_learned` (invalid entries dropped one by one,
  never the player, as `_clean_air_latency`), and `_clean_air_latency` fills a missing `d_ref_ms` from
  the player's `static_delay_ms` (else 0), logged once per entry. No schema version bump (additive
  optional fields).
- `config/schema.json` player definition, `scripts/translate_ha_config.py` preserved per-device fields
  (next to `air_to_sound_latency`, line 375), `services/infrastructure/config_diff.py` hot-apply set
  (next to `air_to_sound_latency`, line 43) — a UI save that changes it resends the target, the
  parent's own writes go straight to the config file.

## Failure containment

| Failure | Behaviour |
|---|---|
| `AirBias` raises (any method) | the engine catches it, sets `air_bias_state = none` with `air_bias_reason = internal_error` for the rest of the output, uses `o = r` and `e_rel` as today, logs once with the traceback; never reaches the feeder (an exception there would otherwise trigger `fall_back`, gst_output.py:508-519) |
| `AirFeed` misuse or overflow | `push` drops the oldest beyond 32; a push with an old tap generation is refused; all methods are O(1) under the lock |
| The daemon's `on_window` hook raises | caught in the sensor (logged at DEBUG); the sensor keeps running |
| A wrong window (wrong lag) | the sensor's PSR gate and continuity rule first; then the bias gate (S-9) |
| Sensor loses its lock | `held`: `b` frozen, the speaker keeps its corrected placement; guidance after 60 s if never seeded |
| Learned sample persistence fails | WARNING once per 10 min; the target keeps the previous value |
| Learned key changes (adapter or codec) | a reconnect reopens the output; the parent resolves the new key's entries |
| Daemon too old for learned fields | ignores them (calibrated target only) |
| PipeWire sawtooth | the controller's R14 persistence on `c` (unchanged), the gate's MAD term; acceptance in simulation |

## Test strategy

- **Unit, pure** (`AirBias`, `AirFeed`, `EHistory`): seed agreement and sliding; seed jump size and
  dead band; no-agreement fallback after 12 windows; tracking with τ = 30 s on window-middle time;
  gate (fixed and MAD term), step rule; every event in the table (format, clear, delay `ΔD`,
  underrun/stall/`pa_step` with freeze and 15 s timeout, lock lost and regained, calibration set /
  changed / forgotten with and without a learned fallback, learned latch); learned sample once per
  output and only from the initial triple; window exclusion by event `x` and by coverage; feed reopen
  and generation filter; a thread test pushing from one thread while another takes.
- **Engine** with the existing simulation (`tests/unit/services/audio/playout/sim.py`) and a fake feed:
  the Q6 tests (delay change keeps the residual: a marker heard exactly `ΔD` earlier with the 13.4 ms
  residual kept; a gap after an underrun baseline does not move the audio by `β`; error after a
  placement = the residual); in `loop` with a bias: placements target `c = 0`, the controller sees `c`,
  air jumps bypass the clamp and are dropped under a pending placement, baseline suppressed in
  `active`, snapshot keys.
- **Closed-loop simulation** (`tests/unit/services/audio/playout/test_air_closed_loop.py`): a
  `SimAir` produces `AirSample`s from the simulation's truth (`true_port_time_us(x)` of the simulated
  port plus a per-start hidden offset `h0`, σ(H) = 0.5 ms Gaussian, windows of 3 s every 1 s delivered
  `0.25 s` after their newest air, `s` and `d_out` recorded per block at write time like the tap), fed
  through a real `AirFeed` into the real engine in `loop`. Cases, each with the stand's numbers:
  - **Calibrated cold starts:** 8 starts with `h0` ∈ {9, 16, 23, 30, 38, 45, 52, 60} ms, host latency
    800 ms, pulsesink noise σ 4 ms; true heard error 20–30 s after the start: `|median| ≤ 1 ms` per
    start, `sd ≤ 0.5 ms` over starts, exactly one `air_seed` per start, seed within 10 s of the first
    audio. Repeated with host latency 200 ms.
  - **Learned:** stage A's `H` sequence (above), uncalibrated: starts 1–2 uncorrected, from start 3 the
    speaker lands on the learned placement; the test prints the start-to-start sd of starts 4–8 for the
    robust mean (S-21) and, for comparison, the plain median, and asserts `≤ 2.0 ms` for the robust mean
    (expected ≈ 1.4–1.5 ms with the simulated σ(H)). A delay change between starts 4 and 5 shifts the
    5th start's sound by exactly that change.
  - **PipeWire:** sawtooth 30 ms / 5 s and 27 ms / 15 s on the reported clock, σ 1 ms: after the
    seed, `|median E| ≤ 2 ms`, no steady jump in 60 s (the sawtooth never reaches R14's persistence on
    `c`).
  - **Delay change `+30 ms`** in `active` (calibrated and learned): the true heard time of the audio
    moves by `−30 ± 0.5 ms`, `b` unchanged within 0.2 ms, `air_trim_ms` = 30.0, one `delay` placement and
    no air jump in the next 20 s; the next start (same `D_out`) lands 30 ms early against the schedule
    too. In `measure`: the audio moves by `−30 ± 0.1 ms`.
  - **Lock lost** (no windows for 60 s): `held`, `b` constant, no jump; windows return: tracking
    resumes, no jump.
  - **Format change:** reseed; at most one `air_seed` of `≤ 2 ms` when nothing hidden changed.
  - **Underrun with a hidden change of 12 ms:** reseed, true error back within 1 ms within 15 s, no
    baseline warning.
  - **Calibration set mid-stream with `ΔL = +20 ms`:** one `air_target` jump of `+20 ± 0.5 ms`.
  - **Wrong window:** one window 50 ms off: gated, `b` moves `< 0.1 ms`.
  - **Hidden step of 25 ms persisting:** absorbed by the step rule after 15 windows, then R14 corrects
    `c` (clamped steps).
- **Config/IPC/daemon/parent/API/UI**: effective rules (the four stage A tests that pinned
  `loop_not_supported_yet` change with the requirement, Q2), learned entry model and helpers, migration
  cleanup, HA translate preserve, config diff class, `set_air_calibration` encode/decode with and
  without learned fields, the daemon builds the feed only in `loop`, the sensor's windows reach the
  feed, lock/key/target propagate, the parent records a sample once per `(proc, seq)` and resends,
  Forget clears both, the API model serialises the new keys, the panel line and the learned entries
  render, guidance reasons.
- **Switch off and `measure`:** explicit tests that `measure` builds no feed and no `AirBias`, the
  engine snapshot has `air_bias_state: null`, and with the switch off nothing of stage B exists.

## Stand acceptance (Q12)

Stand: HAOS VM (PulseAudio 17), ENEBY20 on hci1 (SBC 44.1 kHz), `PLAYOUT_SYNC=loop`,
`EXPERIMENTAL_AIR_SENSOR=true`, `SENDSPIN_AIR_DEBUG=1`; the reference is the desktop bridge's
headphone output **on the playout engine in `measure`**, playing continuously (never restarted, only
muted/unmuted by the solo alternation); headset microphone; the coherent click estimator; the A17
protocol for solos (MA mutes alternating).

- **Restarting only ENEBY20's stream.** Candidates, tried in this order, each checked by the logs
  (ENEBY20's daemon logs a new `playout output open`, the reference's does not): (1) MA
  `players/cmd/power` off/on of the ENEBY20 member while the group plays; (2) un-sync ENEBY20 from the
  group and sync it back (`players/cmd/ungroup`, then `players/cmd/group` to the leader); (3) per-player
  `stop` + `play` of the member. The MA sendspin provider is never reloaded.
- **Series 1, calibrated:** ENEBY20 calibrated in the wizard against the playout reference (A17:
  228.7 ms; re-calibrate if the reference changed). 8 restarts; per restart wait 20 s (seed + settle),
  then a click span of ≥ 20 s; `M_i` = ENEBY20 − reference. Pass: `sd(M) ≤ 2 ms` and
  `|median M| ≤ 3 ms`; `air_corrections` = 1 per start (log), seed within 10 s.
- **Series 2, uncalibrated:** Forget (calibration and learned), 8 restarts; starts 1–2 learn, 3+
  corrected. Pass: `sd(M_4…M_8) ≤ 2 ms`. Recorded: the learned value per start, `X_i`.
- **Q6 check:** one run in `measure`: a delay change of `+30 ms` moves ENEBY20 by `−30 ± 1 ms` by
  microphone (stage A saw −43.6).
- **Slider check in `loop`:** in series 1 after the 8th start, a delay change of `+30 ms`: ENEBY20
  moves by `−30 ± 1.5 ms` by microphone, `air_bias_ms` unchanged, no `air_seed`/`air_target` jump; one
  more restart at the new delay lands at the same place (`−30 ± 2 ms`); then the delay goes back.
- **CPU:** `pidstat` of ENEBY20's daemon over 60 s of music, `loop` vs `measure` (switch on in both):
  not more than +0.3 % of one core.
- **Recorded:** `b` per start, seed times, gated windows, any reseed; the guidance with the switch off
  in `loop` (fires) and with ENEBY20 in `loop` locked (does not).

Results go into a "Stage B1 validation note" appended to this spec.

## Risks

- **The learned estimator on few samples.** Even the robust mean moves by ≈ 1.4 ms between starts
  4–8 on stage A's data, close to Q12's 2 ms. Mitigation: the simulation prints it; the stand series
  measures it.
- **A wrong `D_ref`.** A stage A calibration whose `d_ref_ms` the migration fills from today's static
  delay is wrong by any delay change made since it was measured. Mitigation: logged; re-calibrating
  replaces it; the stand's one entry is re-calibrated in B12.
- **PipeWire's sawtooth** biases per-window `ê` by several ms and placements by the sawtooth phase.
  Mitigation: the MAD gate, R14 on `c`, simulation cases; the Q12 stand is PulseAudio 17, PipeWire is
  validated in simulation only in B1.
- **The seed jump is audible** (up to 60 ms dropped or inserted once per cold start). Same kind as
  today's start and steady jumps; B2's rate loop can later spread small corrections.
- **A congested link (#562)** moves `H` by up to hundreds of ms for ≈ 10 s; the seed may not agree
  (fallback after 12 windows), the step rule needs 15 s. Run Q12 on an uncongested link (as A17 was).
- **A bad calibration reference** makes `L_as` wrong and the calibrated speaker wrong by the same
  amount (stage A: 209 vs 228.7 ms). Mitigation as stage A (R-14, UI note).
- **A learned value from a wrong tuning** is only as good as where the speaker played on average.
  Mitigation: calibration wins; Forget.
- **Config write races** between a learned write and a concurrent full config save from the UI (the
  same exposure as stage A's calibrations). Mitigation: the parent writes through the existing locked
  config update; a lost sample is re-learned at the next start.

## Open questions

None left from rev. 1: the controller settled all five in round 2 (above). New in rev. 2, with the
design's answer:

1. **`D_ref` of a fold that spans a delay change** (the wizard applies a delay during a recording):
   the fold's `d_out_ms` is the median `T − s` of its cells; a fold whose cells carry two delays is not
   offered (`air_delay_changed`, a new ineligibility reason), as stage A already refuses a fold across
   an air epoch.

## Rulings

| # | Ruling | Why |
|---|---|---|
| S-1 | `AirBias` and its state live in the engine on the feeder thread; the daemon loop hands immutable samples, lock state, target and key through a lock-protected `AirFeed` | the engine is feeder-only today; no shared mutable state, no cross-thread callbacks (Q4) |
| S-2 | `e` is matched to a window by written stream time `x` (tap stores `x` per cell, window carries its `x` span; median of usable readings in the span, ≥ 50 % coverage) | `x` is unique and monotonic within an output; `s` steps back, arrival time lags by 2–4 s |
| S-3 | A window whose span contains an engine event's `x` (placement, steady or air jump) or precedes the last change of `b`'s definition (target change, reseed; a delay change does not change it, S-14) is not used for `b` | `e` and `E` of such a window refer to different audio states |
| S-4 | One target formula `E_equiv = H + L_eff − D_ref` for calibrated (`L_as`, `D_ref` of the fold) and learned (robust mean, `D_ref` of the samples); `D_out` does not enter `b` | Q4 and Q7 are the same quantity; round-2 ruling 1 |
| S-5 | Placement offset `o = b + β` with a bias value, else the raw residual `r`, `0` for `start`; applied to every placement and silence re-anchor | Q4 (placements bias-aware) and Q6 (residual kept) in one rule |
| S-6 | `β` survives placements other than underrun/stall; in `active` an underrun/stall/`pa_step` reseeds (controller frozen ≤ 15 s) instead of measuring `β`; in `held`/`none` today's baseline, on the controller's input | the baseline-ignored risk (Q6); the air sees reading and hidden offsets alike |
| S-7 | Reseed = seeding with the old `b` as a provisional value (format, underrun, stall, `pa_step`); the new triple replaces it and applies its own seed jump | "reset + reseed" without a window of uncorrected placements |
| S-8 | Seed: sliding triple, agreement within 2 ms → `b₀ = median b_i` and an `air_seed` jump of `+median E` beyond 2 ms; 12 windows without agreement → `b` from the last 5, no jump | Q8; never wait forever on a congested link |
| S-9 | Tracking: EMA τ 30 s on window-middle time (Δt ≤ 5 s); gate `max(10 ms, 4·1.4826·MAD₂₀)`; 15 consecutive gated windows within 3 ms set `b` to their median | Q8; robust to a wrong window, follows a persistent hidden change, does not chase #562's swings |
| S-10 | `held` freezes `b`; the next accepted window resumes tracking (no jump); reset only on reopen | Q5; round-2 ruling 4 |
| S-11 | Calibration changes apply live (`b −= ΔL`, `air_target` jump `+ΔL` beyond 2 ms); learned values latch per output | a wizard save should act now; a learned value must not move under the output that produced it |
| S-12 | One learned sample per output from the first agreeing triple after reopen, in `loop` only, published with a per-daemon `seq` and the key; the parent persists it | Q7; raw start placement regardless of the target |
| S-13 | Learned entries in a sibling per-player field `air_learned_latency`, one per key, ≤ 8 samples | downgrade-safe (`Player` keeps unknown keys), no cap or uniqueness interplay with calibrations |
| S-14 | A delay change moves the sound by exactly `ΔD` in every mode; in `loop` with a target it is a trim: target `E = −(D_out − D_ref)`, `b` unchanged, the delay placement keeps `e = b` | round-2 ruling 1 (replaces the first-round `b += ΔD`) |
| S-15 | Air sensor effective in `measure` and `loop`; correction effective in `loop` with the sensor effective; no new per-player key; reason code `needs_measure` kept | Q2, Q11, round-2 ruling 3 |
| S-16 | Guidance `air_correction_inactive` (info) for `loop` speakers whose correction cannot run | Q10 |
| S-17 | `set_air_calibration` and the spawn param gain optional `learned_ms`/`learned_starts`, sent only when known | additive, older daemons and existing tests unaffected |
| S-18 | In `loop` with a bias `error_ms` is the EMA of `c`; raw = `error_ms + air_bias_ms` | the number the UI shows is the one the engine acts on |
| S-19 | `air_seed`/`air_target` jumps bypass the 50 ms clamp and the 10 s interval and are dropped under a pending placement | Q8; the placement already targets `c = 0` |
| S-20 | Any `AirBias` exception disables the bias for the output (`internal_error`), logged once, never reaches the feeder; the feed is bounded (32) | stage A's containment rule, extended to the engine |
| S-21 | Learned reference: `off_ref` = mean of `X_i − d_i` within ±15 ms of their median, `d_ref_ms = median(d_i)`, `latency_ms = d_ref_ms + off_ref`; learning in `loop` only | round-2 rulings 2 and 5; sd 1.4 vs 5.0 ms on stage A's starts |
| S-22 | The trim is the static delay itself; `D_ref` is stored with each calibration (`d_ref_ms`, from the fold's `d_out_ms`) and learned entry; older calibrations get it from the migration (`static_delay_ms`); an entry without one latches `D_out` per output | round-2 ruling 1: nothing per output to lose at a reopen |
