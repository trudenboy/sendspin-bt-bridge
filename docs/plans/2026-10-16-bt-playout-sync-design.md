# BT playout sync: warm stream, host playout loop, per-speaker offset, telemetry — design

Status: draft for review, 2026-10-16. Scope approved in conversation: recommendations 1–4 of
[`2026-10-16-media-sync-heterogeneous-pipelines.md`](../research/2026-10-16-media-sync-heterogeneous-pipelines.md)
("the media-sync report"), building on
[`2026-10-16-bt-sync-drift.md`](../research/2026-10-16-bt-sync-drift.md) ("the drift report") and
issue [#546](https://github.com/trudenboy/sendspin-bt-bridge/issues/546).
Target: the v3 line (beta channel), base 3.1.0-beta.5.
Plan: [`2026-10-16-bt-playout-sync-plan.md`](2026-10-16-bt-playout-sync-plan.md).

## Goal and decisions

A Bluetooth speaker played through the bridge drifts against the other members of a Music
Assistant sync group (1–3.5 ms/s right after a fresh stream in #546) and sits 230–330 ms off them
by an amount that changes between sessions. Web Kiosk players in the same recording hold
±0.06–0.4 ms. The bridge measures and corrects none of the terms between the Sendspin schedule and
the speaker (media-sync report, Summary):

```
t_audible = schedule(ts) + e_host + L_host + L_link(t) + L_speaker(t) + L_acoustic − output_delay
```

This design closes the host part and makes the speaker part a stored, measured constant:

1. **Warm stream.** The A2DP stream is not torn down at `stream/end`. The player keeps the sink
   fed (silence, or the keep-alive infrasound) for a hold period, longer while the speaker is in
   an active sync group. Measurements are taken only after a warm-up.
2. **Host playout loop.** The daemon owns the playout timing. GStreamer becomes a dumb pipe
   (`sync=false`, no slaving). The daemon measures the playout error at the PulseAudio sink in the
   Sendspin clock (`CLOCK_MONOTONIC_RAW`) and corrects it with single-frame insert/drop plus a
   one-shot jump at placement events or on a large error. The PA buffer offset (`L_host`) is inside
   the loop and disappears from `output_delay`.
3. **Per-speaker offset.** What lies beyond the PA sink (`L_link + L_speaker + L_acoustic`) is the
   speaker's offset. It comes from the A2DP Delay Report when the stack exposes one and does not
   already count it, otherwise from the phone click-track measurement taken warm. It is stored per
   speaker × codec × adapter and applied automatically. Group sync tuning can save its result as
   the speaker's offset.
4. **Telemetry.** Host playout error, correction rate, jumps, underruns, PA/host latency, clock
   slew, warm state and offset source: in the device status, API v1, a compact UI indicator, and
   logs at sane levels.

Decisions taken while designing:

| Question | Decision |
|---|---|
| Where is "the port" (where the bridge's own compensation ends)? | The PulseAudio sink output, as PA reports it (`pa_stream_get_latency`). Everything up to it is the loop's job; everything after it is `output_delay`. Media-sync report §4.1 rule 2. |
| Which clock is the reference? | The Sendspin timeline after the time filter: `CLOCK_MONOTONIC_RAW` µs (`aiosendspin.clock.RawMonotonicClock`). `CLOCK_MONOTONIC` and the GStreamer clock are never compared with it except through a bracketed sample. |
| How is the error measured? | From the PA sink's audio clock (`GstAudioClock` of `pulsesink`: frames handed to PA minus PA's reported latency), mapped to the bridge's own output stream time, against the schedule map the player keeps for every frame it writes. Exactly defined in "Measurement". |
| How is it corrected? | One frame dropped or inserted at a time (16-frame crossfade), driven by a PI loop (DLL form) with B = 0.03 Hz, ζ = 0.707, dead band 0.1 ms, rate limit 1000 ppm; a one-shot jump at stream start/clear/delay change (frame-exact) and when the error stays beyond 10 ms for 0.5 s. No resampling (follow-up). |
| What does GStreamer still do? | Convert, resample to a fixed output rate, and write to PA. `sync=false`, `slave-method=none`, `provide-clock=true` (read only), pipeline clock forced to the system clock. |
| How does `stream/end` stop audio now that the pipeline lives on? | The queued program audio is dropped from the bridge's own queue; what is already inside GStreamer/PA (≤ `L_host`) plays out **muted** (PA stream mute, effective within the sink's own latency) and the feed continues with silence. |
| Feature flag | `PLAYOUT_SYNC`: `off` (3.1.0-beta.5 behaviour, unchanged) · `measure` (new engine, warm stream, telemetry, jumps over 20 ms only) · `loop` (full loop). Default `off` until Stage 5 passes; then `loop` on the beta channel (see Rollout). |
| Where is the speaker offset kept? | Bridge config, per player: `speaker_offsets[]` keyed by codec × adapter. Music Assistant's `sendspin_static_delay` stays the live value (the bridge sets the output delay; MA follows through `client/state`, as today). |
| Delay Report on PulseAudio (HAOS)? | Expected absent (PA does not register delay reporting and ignores `MediaTransport1.Delay`); verified in Stage 1 from the existing `bt_delay_reporting_supported` telemetry. If absent, the Delay Report source is PipeWire-only, where it is already inside the sink latency the loop compensates, so it is shown as the offset source and never added again. |

Decisions taken by the user on 2026-10-16 (they close the former open questions):

| Question | Decision |
|---|---|
| Does the warm-stream hold override `power_save` while the player is in an active group? | The user's choice, not a fixed rule. Global setting `PLAYOUT_HOLD_OVERRIDES_POWER_SAVE` (default `false`: `power_save` keeps its current behaviour, i.e. the hold is released and the sink is suspended when the power-save timer fires, grouped or not) and a per-player override `playout_hold_overrides_power_save` (`null` = follow the global setting). `true` means: while grouped, `power_save` waits for the hold to end. `auto_disconnect` always releases the speaker. The web UI shows the setting with a one-line explanation (en, ru). |
| Migrate already tuned delays when the engine is turned on? | No. The beta channel is practically private. Delays tuned before `PLAYOUT_SYNC` was enabled (they absorbed `L_host`) must be re-tuned; the changelog and the user guide say so. No automatic conversion, no backup key. |
| Default hold for ungrouped players | 300 s (`PLAYOUT_WARM_HOLD_S`). |
| Per-player opt-out of the playout engine | Yes: per-player `playout_sync_enabled` (default `true`). `false` runs that player exactly as today (`off`: legacy `StreamPlayer`, no warm stream, no offsets), whatever `PLAYOUT_SYNC` says. Config, API, UI and tests are part of Stage 1. |

Out of scope (follow-ups, see the end): variable-ratio resampling (ASRC), upstream proposals
(Sendspin telemetry, sub-ms output delay, aiosendspin playout corrector, MA continuous streams
across announcements), lowering `PULSE_LATENCY_MSEC` defaults, LE Audio.

## Goals and non-goals

Goals:

- Host playout error at the PA sink within ±1 ms (p95) of the Sendspin schedule in steady state,
  independent of NTP slew, host crystal, PA catch-up drops and `PULSE_LATENCY_MSEC`.
- A BT speaker's acoustic phase stays flat once warm (the speaker's long-term rate follows the
  host send rate, media-sync report insight 2).
- A per-speaker offset that is measured once (warm) and stays valid across sessions, announcements
  and reconnects with the same codec and adapter.
- Diagnosable: every term the bridge controls is visible in status, API, UI and logs.
- `PLAYOUT_SYNC=off` is byte-for-byte today's behaviour (3.1.0-beta.5), and so is a single player with `playout_sync_enabled=false`.

Non-goals:

- Correcting the speaker's own buffer control jitter or a firmware drift (invisible from the host).
- Continuous acoustic (microphone) closed loop. Calibration is a one-shot measurement.
- Changing Music Assistant or the Sendspin protocol.
- Local sound-card players get the same engine but no special tuning (their `output_delay` should
  approach 0).

## Success criteria

Measured with the `sendspin_sync` click estimator on a phone recording (6 s windows, ~0.1 ms per
window), a Web Kiosk member as the reference in the same recording, unless stated otherwise. The
validation protocol is in "Testing → Live validation".

| # | Criterion | Pass |
|---|---|---|
| S1 | BT speaker phase slope after warm-up (V-warm: 2-minute solo after ≥ 60 s warm; slope corrected by the kiosk segments before and after) | \|slope\| < 0.05 ms/s, on ENEBY Portable and one more speaker |
| S2 | Offset vs kiosk after calibration (mean phase difference, 3 sessions with a reconnect between them) | \|offset\| < 5 ms in every session |
| S3 | Start-to-start repeatability: 5 announcement → music cycles, warm | offset spread < 3 ms (was ±150 ms) |
| S4 | Host error (telemetry `playout.error_ms`) over 30 min of music | p95 < 1 ms, no jump after the first placement |
| S5 | Independence of `PULSE_LATENCY_MSEC` (300 / 600 / 800) | acoustic offset spread < 2 ms |
| S6 | No audible artefacts at 1 correction/s (ABX, music and a 1 kHz sine, forced 1/s); at the rate limit (48/s) on music | not distinguishable at 1/s; no complaint on music at the limit |
| S7 | Delay step response (±10, ±30, ±100 ms via MA) | shift = step ± 1 ms, effective within `L_host` + 150 ms |
| S8 | Stop latency at `stream/end` (pause in MA) | audio silent within 100 ms (#464 parity) |
| S9 | Sensor agreement on a local sound card (wired) | acoustic phase variation tracks `playout.error_ms` within ±0.5 ms over 10 min; acoustic slope < 0.01 ms/s |
| S10 | CPU on a Raspberry Pi 4, one stream | ≤ 2 % of one core above `off` |

## Facts this design relies on

Checked against this repository (v3, 3.1.0-beta.5) and aiosendspin 10.0.2 unless marked
**[verify]**; **[verify]** items are confirmed or corrected by Stage 1 Task 1.1 (decision note
appended here).

- aiosendspin 10.0.2: `RawMonotonicClock` is `CLOCK_MONOTONIC_RAW`;
  `SendspinClient.compute_play_time(ts)` returns the filter's client time minus the output delay;
  `set_output_delay_ms()` notifies `add_output_delay_listener` callbacks; the library ships no
  audio player. `is_time_synchronized()` tells whether the filter converged.
- `StreamPlayer` (`services/audio/player/player.py`): `appsrc(is-live, format=TIME) !
  audioconvert ! audioresample ! pulsesink(sync=true, provide-clock=false, slave-method=skew,
  buffer-time=PULSE_LATENCY_MSEC)`; `submit()` pushes each chunk at once with
  `pts = gst_now + (play_time − raw_now)`; `resync(shift_us)` rebuilds the pipeline and re-submits
  the queued audio shifted (3.1.0-beta.5); `metrics()` reports `backend_output_latency_ms`
  (latency query) and `playback_sync_error_ms` (clock − PTS at the sink pad: ≈ −ring fill, not an
  error).
- `BridgeDaemon` (`services/ipc/bridge_daemon.py`): `_on_stream_end` calls `player.stop()` (#464:
  flush events deadlocked some A2DP sinks; teardown was the fix); `_on_output_delay_change`
  coalesces changes for `_DELAY_RESYNC_QUIET_S = 0.15` s and then calls `player.resync(shift)`;
  chunks arriving meanwhile get `_queued_delay_correction_us()`. `DaemonArgs.slave_method="skew"`
  is never overridden. `required_lead_time_ms` and `min_buffer_ms` default to 250.
- `daemon_process.py`: timing telemetry every 5 s while playing (20 s otherwise) through
  `collect_timing_snapshot`; the standby command retargets the player's sink.
- Parent (`bridge/client.py`): `DeviceStatus` accepts only declared fields; idle decisions come
  from `IdleMachine` (`bridge/idle_machine.py`), fed by PA sink state (`SinkMonitor`
  `on_sink_active/idle`) and, when no sink monitor runs, by the daemon flags
  (`on_playback_change`). Keep-alive bursts (`paplay`) are skipped while `audio_streaming`.
- BlueZ transport telemetry exists: `bt_reported_delay_ms`, `bt_delay_reporting_supported`,
  `bt_codec_name`, refreshed every 5–15 s (`_transport_telemetry_loop`), and a pure
  `build_latency_recommendation` with sources `bluez_delay_report`, `codec_fallback`,
  calibrations. `static_delay_source` already records where a delay came from
  (`default`, `manual`, `auto_pending`, `bluez_delay_report`, `codec_fallback`,
  `music_assistant`, `legacy`, …) with `static_delay_codec`.
- Group sync tuning (`sendspin_sync`, `sync_tuning`): MA's `sendspin_static_delay` is the source of
  truth; members carry `origin: bridge|sendspin|other` and `client_id`; the planner normalises the
  smallest delay to 0 (so its values are **relative** unless a reference-grade member is in the
  group); the clicker is a fresh MA stream (`play_media` replace).
- GStreamer `audiobasesink` (drift report §2.2, source read): with `sync=false` a buffer is
  written at the ring's next sample, contiguously, without alignment, skew or QoS;
  `GstAudioClock` time = (ring samples done − ring delay)/rate and never goes backwards;
  `provide_clock()` returns NULL when `provide-clock` is false; pulsesink's ring delay is
  `pa_stream_get_latency` with interpolated timing. **[verify]** that a playing `pulsesink`
  with `provide-clock=true`, `sync=false` and a forced system pipeline clock returns its audio
  clock and that its internal time advances at the sink's consumption rate on a bluez5 sink and on
  pipewire-pulse.
- PulseAudio 17 bluez5 sink: paced by `CLOCK_MONOTONIC`; reports 25 ms + one block; drops audio
  on socket stalls ("Skipping"); does not consume AVDTP Delay Reports. **[verify]** whether
  `MediaTransport1.Delay` exists at all on HAOS (PA registers its endpoints; delay reporting is
  negotiated only if the endpoint asks for it).
- PipeWire bluez5 adds the transport `Delay` (when reported) to the node latency, which
  pipewire-pulse folds into `pa_stream_get_latency`. **[verify]** on the Docker test VM (PipeWire)
  with a delay-reporting speaker.
- Music Assistant sync-group members receive `group/update` with the group's id while the group is
  active. **[verify]** on MA dev which `group_id` a member sees while its sync group is powered vs
  powered off (used for the "in a group" hold rule).

## Architecture

```
 daemon subprocess (per speaker)                                                     parent
 ─────────────────────────────────────────────────────────────────────────────────  ──────────────
 aiosendspin client ── chunk(ts) ─► compute_play_time (RAW µs, − output_delay)
                                        │ decode (FLAC→PCM)
                                        ▼
  BridgeDaemon ── submit(play_time, pcm) ──► PlayoutPlayer (gst_output.py)          DeviceStatus.playout
     stream/start ─► start(fmt)                │ command queue (thread-safe)        ▲  API DeviceTiming.playout
     stream/clear ─► clear()                   ▼                                    │  UI SyncChip / Timing panel
     stream/end   ─► end_stream()   ┌─────── feeder thread (5 ms tick) ───────────┐ │
     delay change ─► resync(shift)  │ PlayoutEngine (pure, single-threaded)        │ │  IdleMachine (+hold)
     hold/release ─► set_hold()     │  Scheduler: queue → contiguous output frames │ │  speaker offsets
                                    │    silence/infrasound fill, placement,       │ │  (Stage 4)
                                    │    insert/drop (Corrector)                   │ │
                                    │  ScheduleMap: output time x → RAW s(x)       │ │
                                    │  PortObserver: x_port = A − k; fill;         │ │
                                    │    underrun/stall detection                  │ │
                                    │  LoopController (10 Hz): e → rate, jumps     │ │
                                    │  WarmHold: when to stop feeding              │ │
                                    │  PlayoutStats: EMA, p95, slope, counters     │─┘ status IPC (1 Hz)
                                    └──────┬────────────────────▲─────────────────┘
                                   push 10 ms blocks       sample A(t) + RAW bracket (10 Hz)
                                           ▼                    │
     appsrc ─► audioconvert ─► audioresample ─► capsfilter(fixed rate) ─► pulsesink
                                                         sync=false, slave-method=none,
                                                         provide-clock=true (read only),
                                                         buffer-time = PULSE_LATENCY_MSEC
     PulseAudio (bluez5 sink / pipewire-pulse) ─► A2DP ─► speaker
```

Components (all new under `src/sendspin_bridge/services/audio/playout/` unless noted):

| Component | File | Responsibility |
|---|---|---|
| `ScheduleMap` | `timeline.py` | Piecewise-linear map from output stream time `x` (ns of audio written since the output opened) to the Sendspin RAW time `s(x)` (µs) at which it should be heard. New segment at every discontinuity (placement, correction, silence/program boundary). Bounded; old segments dropped once the port passed them. |
| `PortObserver` | `timeline.py` | Turns sensor samples into the port position `x_port`, keeps the offset `k` (ring/resampler origin), the fill, and detects underruns and stalls. |
| `PortSample`, `GstPortClock`, `FakePortClock` | `sensor.py` | One bracketed reading: RAW before, audio-clock internal time, RAW after, and `CLOCK_MONOTONIC`. |
| `Corrector` | `corrector.py` | Drop or insert one frame in interleaved S16LE with a 16-frame crossfade; fade-out/in for jumps. |
| `LoopController` | `controller.py` | Outlier gate, pre-filter, PI (DLL form), dead band, rate limit, anti-windup, jump decision. Pure. |
| `PlayoutScheduler` | `scheduler.py` | Host-side queue of timed PCM; renders contiguous blocks; placement; applies corrections through a phase accumulator; end/clear/resync. |
| `WarmHold` | `warm.py` | Decides how long silence is fed after program audio stops. Pure. |
| `PlayoutStats`, `ClockSlewEstimator` | `stats.py` | Telemetry aggregates. |
| `PlayoutEngine` | `engine.py` | Composes the above; `tick(now)` API; owns no thread and no GStreamer. |
| `PlayoutPlayer` | `gst_output.py` | GStreamer pipeline, feeder thread, sensor wiring; the daemon-facing interface of `StreamPlayer` (`start`, `submit`, `clear`, `stop`, `resync`, `metrics`, `set_volume`, `is_drained`) plus `end_stream()` and `set_hold()`. |
| `PaStreamLatencyProbe` | `services/audio/pa_stream_latency.py` | 1 Hz `pulsectl_asyncio` read of this daemon's sink-input `buffer_usec + sink_usec` (cross-check; also used in `off` mode). |
| `speaker_offsets` | `services/audio/speaker_offsets.py` | Offset entries, key, resolver (Stage 4). |

`StreamPlayer` stays for `PLAYOUT_SYNC=off`. The daemon picks the player once at start from
`DaemonArgs.playout_mode`; `DaemonArgs` defaults to `off`, so every existing daemon test keeps
its meaning.

### Clocks

| Symbol | Clock | Use |
|---|---|---|
| `t` | `CLOCK_MONOTONIC_RAW` µs (`time.clock_gettime_ns`) | The Sendspin timeline; the error is in this clock. |
| `A(t)` | `GstAudioClock` internal time of pulsesink, ns | Port position in output stream time, PA's view. |
| `m(t)` | `CLOCK_MONOTONIC` | Only for the slew telemetry (MONO vs RAW ppm); never in the loop. |
| feeder tick | `threading.Event.wait` (MONOTONIC) | Pacing only; no value from it enters a computation. |

Every sensor reading is a bracket `(t₀, A, m, t₁)`; it is accepted when `t₁ − t₀ ≤ 0.5 ms` and
dated at `(t₀ + t₁)/2` (up to 3 tries; else the sample is dropped and counted).

### Data flow

1. `submit(play_time_us, pcm)` (asyncio thread) appends a chunk `(play_time_us, pcm, fmt)` to the
   engine's inbound command queue. No GStreamer call happens on the asyncio thread.
2. The feeder thread wakes every 5 ms, drains commands, and while `appsrc`'s `current-level-time`
   is below `LEAD_MS = 40` ms renders one 10 ms block (`BLOCK_MS`) and pushes it. Every 100 ms it
   also takes a sensor sample and runs the observer, the controller and the stats.
3. A block is program audio where a chunk's frames are due, silence (or infrasound) elsewhere. The
   scheduler records each block's `(x, s(x))` segment(s) and any correction in the `ScheduleMap`.
4. Downstream, `pulsesink` blocks the streaming thread when the ring is full; `appsrc` backs up;
   the feeder stops at `LEAD_MS`. In flight: ring (`buffer-time`) + PA + `LEAD_MS`.
5. `metrics()` returns a snapshot copied under a lock; the daemon's telemetry watcher publishes it
   at 1 Hz while playing (5 s otherwise) in the status key `playout`.

### Output format

The PA stream format is fixed for the output's life: S16LE stereo at the sink's own rate (read
once with `pulsectl` when the output opens; 48000 if unknown), set by a `capsfilter` after
`audioresample`. A Sendspin format change (44.1 ↔ 48 kHz) only renegotiates `appsrc →
audioresample`; the PA stream and the A2DP transport stay as they are, so the stream stays warm.
The scheduler works in output stream time (ns), so input frames of either rate map onto it; one
dropped or inserted input frame moves the schedule by `1/fs_in`. The resampler's constant delay is
part of `k`.

## Measurement

Definitions (signs: positive error = **late**, i.e. heard after its scheduled time):

- `x` — output stream time: the duration of everything the scheduler has written since the output
  opened (ns). `x_w(t)` is the write head.
- `s(x)` — the RAW time (µs) at which the frame at `x` is scheduled to leave the port. For program
  audio `s = play_time(chunk) + j/fs_in` for input frame `j` of the chunk, plus the shifts of any
  insert/drop done before it; for silence the timeline continues linearly from the last frame
  (silence is scheduled too, so the loop runs during the hold).
- `x_port(t)` — the output stream time of the frame leaving the PA sink at RAW time `t`:
  `x_port(t) = A(t) − k`.
- **Playout error** `e(t) = t − s(x_port(t))`.

How `A(t)` is read: `pulsesink.provide_clock()` (the sink is configured with
`provide-clock=true`; the pipeline is given `Gst.SystemClock.obtain()` with `use_clock` so the
sink's clock never becomes the pipeline clock) returns the `GstAudioClock`; `get_internal_time()`
is `(samples_done − delay)/rate`, where `delay` is PA's stream latency (data written to PA and not
yet out of the sink, including the sink's own reported latency). It is therefore the PA sink output
position in the ring's sample count, with PA's timing smoother behind it.

How `k` is kept (`PortObserver`):

- At open, the ring is empty and the first block is written at ring sample 0 (`sync=false`
  appends at the ring's next sample), so `k₀` = the resampler delay (latency query on
  `audioresample`, 0 when it passes through). **[verify]** in Task 1.1 against an acoustic
  measurement on a wired output.
- Fill `F(t) = x_d(t) + k − A(t)`, with `x_d` = time handed to the sink (`x_w` minus
  `appsrc current-level-time`). `F` hovers near `buffer-time`.
- **Underrun:** if `F ≤ 0` (the port overtook what we delivered), PA played silence that is not in
  our stream: when data flows again, `k += −F_min` (the deficit), `underruns += 1`, and the next
  controller step is a jump. Detected within one sensor period.
- **PA catch-up drop** ("Skipping"): `A` steps forward while `F > 0`. Our frames were discarded,
  so `x_port` really advanced: `e` steps early by the dropped amount and the loop corrects it (a
  jump if > 10 ms). Counted as `pa_steps` when a step > 2 ms is seen in `A − t` after removing the
  rate estimate.
- **Stall:** `A` does not advance for ≥ 300 ms while `t` does (sink suspended, transport lost):
  state `suspended`, controller frozen; on recovery the observer re-anchors (`k` from the fill
  rule) and the controller jumps.

Predicted error at the write head (what the controller acts on, which removes the dead time of
corrections still in flight):

```
e_w(t) = t + (x_w − x_port(t)) · (1 + ρ̂) − s(x_w)
```

`ρ̂` is the port-rate estimate (the controller's integrator, in s/s). Corrections already written
are inside `s(x_w)`, so they are counted once, before they reach the port.

Derived telemetry: `host_latency_ms = (x_w − x_port)/1e6` (write head to port: `LEAD_MS` + ring +
PA), `pa_latency_ms` from `PaStreamLatencyProbe` (`buffer_usec + sink_usec`, 1 Hz, cross-check),
`sink_rate_ppm` = slope of `A − t` over 60 s, `clock_slew_ppm` = slope of `m − t` over 60 s.

In `off` mode none of this exists; the engine is not built. Stage 1 still adds
`pa_latency_ms`, `clock_slew_ppm` and the latency query to the legacy telemetry, so H3/H4 of the
drift report can be checked without the new engine.

## Control loop

Plant: `de/dt = δ − r`, where `δ` is the port's rate error against RAW (NTP slew, crystals, PA
pacing; constant or slow) plus steps (PA drops, time-filter jumps), and `r` is our correction rate
(s/s; `r > 0` drops frames and catches up when late).

| Stage | Rule | Constant | Rationale |
|---|---|---|---|
| Sampling | one bracketed sample per tick | `T = 0.1 s` | 10 Hz is enough for a loop at 0.03 Hz and cheap in Python. |
| Outlier gate | Hampel on the last 9 `e_w` samples: reject if \|e − median\| > max(3 · 1.4826 · MAD, 0.5 ms); a level that stays outside for 5 samples is accepted as a step | 9, 3σ, 0.5 ms, 5 | PA timing updates and scheduler jitter produce single-sample spikes; real steps persist. |
| Pre-filter | EMA on the gated error for the P term and the display | `τ_f = 1.0 s` (α = 0.1) | Cuts the P-term noise by ~4.5×; its pole (1 rad/s) is 5× above the loop's natural frequency. |
| PI (DLL form) | `I ← clamp(I + Ki·ε·T)`, `r = clamp(Kp·dz(ε_f) + I)` | `B = 0.03 Hz`, `ζ = 0.707`, `ωn = 2πB = 0.1885 rad/s`, `Kp = 2ζωn = 0.2666 s⁻¹`, `Ki = ωn² = 0.03553 s⁻²` | Second order: a constant rate error leaves no phase error (Adriaensen 2012; PipeWire `dll.h`). With σ ≈ 0.3 ms measurement noise the residual is ≈ 0.04 ms; a 100 ppm rate onset peaks at ≈ 0.25 ms; a 2 ms step settles (95 %) in ≈ 30 s with a peak rate ≈ 530 ppm. |
| Dead band | P term is 0 while \|ε_f\| < 0.1 ms; I keeps integrating | `0.1 ms` | Spec suggestion (~100 µs); avoids hunting while I holds the steady rate. |
| Rate limit | \|r\| ≤ R_max; I clamped to ±R_max; I frozen while r is saturated in the error's direction (anti-windup) | `R_max = 1000 ppm` (0.1 %, 48 frames/s at 48 kHz) | 5× under the spec's 0.5 %; covers NTP slew (≤ 500 ppm) with margin. |
| Frame events | phase accumulator: per rendered block `acc += r · fs_in · Δt`; while `acc ≥ 1` drop one input frame, while `acc ≤ −1` insert one; at most one event per 10 ms block | — | Spreads corrections evenly; one per block is enough up to 100/s. |
| Crossfade | linear over 16 frames around the cut/duplicate | `16` (0.33 ms at 48 kHz) | sendspin-js uses 8 samples; 16 halves the click energy on tonal content at no cost. |
| Jump (placement) | at stream start, `stream/clear`, a delay change, after an underrun or a stall: place the next program frame exactly (insert silence or drop the head) | 1 frame | Free before the audio starts; keeps 3.1.0-beta.5's "delay changes take effect at once, of any size". |
| Jump (steady) | 5 consecutive raw `e_w` samples (0.5 s, before the outlier gate, which would hold a fresh step back) beyond `J` with the same sign: fade out 2 ms, insert/drop `round(median·fs)` frames (median of the 5), fade in; the controller then resets as after a placement; at most one per 10 s (more → WARNING, state `degraded`) | `J = 10 ms` (`loop`), `20 ms` (`measure`, parity with GStreamer's skew bound) | Spec "large errors": a one-shot resync, rare. 10 ms takes the loop ≥ 10 s at R_max; a jump is better. |
| After a jump | reset the EMA and the Hampel window to the post-jump value; keep I (the rate estimate is still valid); P term held off 1 s | — | Avoids reacting to the jump's own transient. |
| Saturation watch | \|I\| > 300 ppm for 60 s → one WARNING per 10 min ("the speaker's sink runs X ppm off the Sendspin clock") | 300 ppm, 60 s | Flags NTP trouble or a broken sink clock; corrections stay legal. |
| `measure` mode | the same estimator and placement jumps; `r = 0` (no fine corrections); steady jump at 20 ms | — | Pure measurement with no worse behaviour than GStreamer's ±20 ms. |

Conformance with the Sendspin spec (Playback Synchronization): correction ≤ 0.5 % over 150 ms
(ours ≤ 0.1 %), single-frame drop/duplicate with a dead band ~100 µs, one-shot resync only at
start, after an underrun or for large errors.

## Warm stream

### Policy

After program audio stops (`stream/end`), the player keeps feeding the sink:

| Condition | Hold |
|---|---|
| Player's current Sendspin group has other members (`group_id` ≠ its own id) and `PLAYOUT_WARM_IN_GROUP` | while grouped, capped by `PLAYOUT_WARM_GROUP_MAX_S` (default 3600 s; 0 = no cap) |
| Otherwise | `PLAYOUT_WARM_HOLD_S` (default 300 s; 0 = stop at once, the 3.1.0-beta.5 behaviour) |
| Idle mode `power_save`, timer fires | Default (`PLAYOUT_HOLD_OVERRIDES_POWER_SAVE=false`, or the player's `playout_hold_overrides_power_save=false`): the parent asks the daemon to release the hold, then suspends the sink, grouped or not (today's behaviour). With the override on: a grace hold is released as above, but while the hold reason is `group` power-save waits for the hold to end (then arms again). The per-player value wins over the global one. |
| Idle mode `auto_disconnect`, timer fires | standby as today (the hold ends with the output). |
| Idle mode `keep_alive` | the hold feeds the keep-alive signal (`keep_alive_method`: infrasound / silence) instead of zeros; the parent's `paplay` bursts are skipped while the daemon holds. `none` → zeros. |
| Standby, BT disconnect, sink retarget, daemon stop | hold ends with the output (cold next time). |

The hold restarts at every `stream/end`; program audio cancels it. When it ends, the feeder stops,
the pipeline goes to NULL (as `stop()` does today), PA's sink-input disappears, and the sink can
idle or suspend as usual.

### Stopping audio at `stream/end` without a teardown

1. The scheduler drops every queued program chunk (the #464 requirement) and records `x_end`
   (the write head).
2. The PA stream is muted at once through `pulsesink`'s `mute` property (PA applies stream mute at
   render time, so the audio already queued in the sink-input is silenced within the sink's own
   latency, ~25–50 ms on bluez5). The tail (≤ `L_host`) plays out muted.
3. The feed continues with silence. When `x_port ≥ x_end + 20 ms`, the stream mute is lifted
   (composed with the user's stream mute in `volume_controller=sendspin`).

`stream/clear` (seek, skip) does the same without starting the hold.

### Warm-up

`warm_s` counts seconds since the output opened or since the last underrun or stall (placement
jumps do not reset it: the A2DP stream stayed continuous). `warm = warm_s ≥ PLAYOUT_WARMUP_S`
(default 60 s; the research found speaker buffer convergence in the first 10–60 s). Stats windows
(slope, p95) are reported with a `warm` flag. Group sync tuning waits for its bridge members to be
warm (see "Integration with group sync tuning").

### Start-up lead

In `measure` and `loop`, a frame must be written `L_host` before it is due. At stream start the
server sends at least `required_lead_time_ms` ahead, so the daemon declares
`max(required_lead_time_ms, PULSE_LATENCY_MSEC + LEAD_MS + 100)` to the server (the configured
value is unchanged; the effective one is reported in status). A head that still arrives too late
is dropped and reported (`late_start_ms`). Cost: a group's start waits for the largest lead of its
members (≈ 0.75–0.95 s with `PULSE_LATENCY_MSEC` 600–800).

### Delay changes (3.1.0-beta.5 semantics kept)

The daemon is unchanged: changes are coalesced for 150 ms and `player.resync(shift_us)` is called
once with the total shift. `PlayoutPlayer.resync` shifts every queued chunk's play time by
`shift_us`, drops audio whose new time has passed, and forces a placement jump at the write head,
so the next frame lands exactly. Audio already in flight (≤ `L_host`) plays with the old timing:
the change is heard within `L_host`, never misaligned, and the A2DP stream stays warm (no
rebuild). Returns `True` when something was playing, as today.

## Per-speaker offset

### What it is

`offset = L_link + L_speaker + L_acoustic` — everything after the PA sink, the Sendspin
`output_delay` of a bridge player in `measure`/`loop` mode. It is constant within a warm session
and, by the media-sync report's analysis, repeatable per speaker × codec × adapter (verified by
S2/S3).

### Sources and precedence

| Priority | Source (`static_delay_source`) | When |
|---|---|---|
| 1 | `manual`, `music_assistant` newer than the offset entry | The operator's explicit value wins. |
| 2 | `acoustic` — a stored entry for the current codec × adapter, measured warm in `loop`/`measure` | Applied automatically on connect and on a codec or adapter change. |
| 3 | `bluez_delay_report` — `MediaTransport1.Delay` on a PulseAudio host | Only when PA does not include it in the sink latency (PA never does). Starting value, `confidence: medium`. |
| — | `device_report_in_sink` — Delay present on a PipeWire host | Shown as the source; contributes 0 ms (already inside the port latency the loop removes). |
| 4 | `codec_fallback` (existing table) | First start only (`auto_pending`), as today. |

A Delay Report is advisory (it excludes post-DSP and air). If both a report and an acoustic entry
exist, the acoustic one wins and the difference is shown.

**PulseAudio (HAOS).** The bluez5 module does not ask BlueZ for delay reporting and ignores
`Delay` (drift report §2.3). Expected outcome of Task 1.1: no `Delay` on HAOS. Then source 3 never
applies on HAOS and the offset comes only from the click track. **PipeWire.** Source "in sink". So
the Delay Report never changes `output_delay` on either stack unless Task 1.1 finds a PA host that
exposes it; the plan keeps the resolver rule so such a host works.

### Storage

Per player in `config.json` (`players[].speaker_offsets`, preserved like `static_delay_*`):

```json
{"codec": "sbc", "adapter": "C0:FB:F9:62:D6:9D", "offset_ms": 212.4, "source": "acoustic",
 "measured_at": "2026-10-20T10:00:00Z", "spread_ms": 0.8, "reference": "web-kiosk-a",
 "audio_server": "pulseaudio", "playout_mode": "loop", "bridge_version": "3.2.0-beta.1"}
```

Key = (codec, adapter MAC). At most 8 entries per player (oldest dropped). An entry measured with
`PLAYOUT_SYNC=off` is never stored (its value would include `L_host`).

### Applying it

The parent resolves the offset on connect and whenever `bt_codec_name` or the adapter changes,
and calls the existing hot-apply path (`apply_hot_config({"static_delay_ms": round(offset)})`):
the daemon sets the output delay, aiosendspin reports it in `client/state`, Music Assistant stores
it — the same path the latency assistant uses. The sub-millisecond remainder
(`offset − round(offset)`) is sent as `set_playout_trim_us` and added to the loop's target, so the
1 ms granularity of the protocol does not cost accuracy. `static_delay_source` becomes `acoustic`
(or `bluez_delay_report`). `PLAYOUT_SPEAKER_OFFSETS=false` turns automatic application off (the
entries stay visible).

### Integration with group sync tuning

- **Warm gate.** The session resource gains, per bridge member, `warm_in_s` (seconds until warm,
  0 = warm, `null` = not a bridge member or `off` mode). `POST …/sequence` and a live recording of
  a cold bridge member answer 409 `member_cold` (with `member` and `warm_in_s`) unless the request
  sets `force: true`. The wizard shows "Warming up the Bluetooth speakers — 35 s" on the clicks
  step and enables the sequence when all are warm. The 180 s clicker leaves time for a 60 s
  warm-up and up to 9 members at the speakers.
- **Saving the result as the speaker's offset.** After `apply` (state `tuning`), the session
  resource lists `offset_candidates`: for each bridge member measured `ok` while warm, in
  `measure`/`loop`, at point `device`, with a reference-grade member measured `ok` in the same
  round (a non-bridge Sendspin player — e.g. a Web Kiosk — or a bridge `pulse_sink` player in
  `loop`): `offset_ms = after_ms(member) − after_ms(reference) + trim`, codec and adapter from the
  member's status. Members without a reference get `eligible: false, reason: "relative_only"`
  (the planner's values are relative to the earliest member). `POST
  /api/v1/sync/sessions/{id}/speaker-offsets {members: [...]}` stores the eligible ones (source
  `acoustic`). The Tune step offers "Save as this speaker's offset (SBC · hci0)" per eligible
  member, ticked by default.
- The `sendspin_sync` core is unchanged: eligibility, warm state and storage live in the bridge
  adapters and the application layer (the core-boundary test stays green).

## Failure modes and fallbacks

| Situation | Detection | Behaviour |
|---|---|---|
| Sensor unavailable (`provide_clock()` NULL, internal time NONE or not advancing within 2 s of PLAYING, not a GstAudioBaseSink) | engine start / first 2 s | `state=fallback`, reason logged at WARNING once; the daemon rebuilds with `StreamPlayer` (legacy) for the rest of the session. |
| Noisy sensor (PA timing updates, VM jitter) | Hampel rejects; `rejected_samples` counter | Loop continues; > 30 % rejected for 60 s → WARNING and `state=degraded`. |
| PipeWire (pipewire-pulse) | `pactl info` server name at output open | Same engine. PipeWire rate-matches the bluez5 node to its driver, so `δ` is small; its latency includes the Delay Report (offset source rule). |
| Sink suspended under us (power_save, `pactl suspend-sink`) | stall rule | `state=suspended`, controller frozen, feeder stops at `LEAD_MS`; on resume re-anchor + jump; `warm_s` resets. By policy the hold is released before power_save suspends. |
| Underrun (feeder starved > `buffer-time`) | fill rule | `k` corrected, jump, `underruns += 1`, `warm_s` resets; > 3 in 10 min → WARNING. |
| Format change | `start(fmt)` with a new rate | appsrc caps renegotiated; output caps fixed; warm kept; the next chunk is placed (jump). If renegotiation fails: rebuild (cold), logged at INFO. |
| Time filter not converged / steps | `is_time_synchronized()`; gap between consecutive chunks' play times | Chunks are placed by their play time; a gap > 2 ms between contiguous chunks is a placement event (jump); ≤ 2 ms is left to the loop. |
| Standby / BT link lost | standby command, sink retarget, daemon stop | Output closed, hold ends; cold next time. |
| PA "Skipping" drops (link stalls) | `A − t` steps while `F > 0` | Real early step, corrected by the loop (or a jump); `pa_steps` counted. |
| Reported PA latency changes (SBC bitpool change → block size) | step in `A − t` coinciding with a `pa_latency_ms` change | Treated as a real move of the port (it is PA's best estimate of the port); corrected. |
| Daemon CPU starvation | `rejected_samples`, underruns | As above; Python work per 10 ms block is a copy plus at most one 16-frame crossfade. |
| `PLAYOUT_SYNC=off` | — | Today's `StreamPlayer`; new telemetry limited to `pa_latency_ms`, `clock_slew_ppm`, latency query. |

## Configuration

Global keys (registered in `DEFAULT_CONFIG`, `config/schema.json`, `BridgeConfig` in
`application/models/config.py`, `_ALLOWED_POST_CONFIG_KEYS` and its normalisation in
`application/config.py`, the `web_ui_only_keys` of `scripts/translate_ha_config.py`; classified in
`config_diff`). Not Home Assistant add-on options (web UI settings, Audio section, advanced).

| Key | Type / range | Default | Applies | Stage |
|---|---|---|---|---|
| `PLAYOUT_SYNC` | `off` \| `measure` \| `loop` | `off` (→ `loop` on beta after Stage 5) | warm restart of daemons | 1 |
| `PLAYOUT_WARM_HOLD_S` | 0–3600 s | 300 | warm restart | 2 |
| `PLAYOUT_WARM_IN_GROUP` | bool | true | warm restart | 2 |
| `PLAYOUT_WARM_GROUP_MAX_S` | 0–86400 s (0 = no cap) | 3600 | warm restart | 2 |
| `PLAYOUT_WARMUP_S` | 10–300 s | 60 | warm restart | 2 |
| `PLAYOUT_SPEAKER_OFFSETS` | bool | true | hot (parent only) | 4 |
| `PLAYOUT_HOLD_OVERRIDES_POWER_SAVE` | bool | false | hot (parent only) | 2 |

Per player: `playout_sync_enabled` (bool, default `true`; `false` = this player runs as `off`;
warm restart of that daemon; Stage 1), `playout_hold_overrides_power_save` (bool | null, `null` =
follow the global setting; hot; Stage 2), `speaker_offsets` (list, see Storage; written only by
the bridge, read-only in the form). There is no migration key: delays are not converted when the
engine is enabled.

The effective mode of a player is `off` when `playout_sync_enabled` is `false`, else `PLAYOUT_SYNC`
(`effective_playout_mode()`); the parent passes it in the spawn parameters, the status and the API
report it, offsets are neither stored nor applied while it is `off`, and sync tuning treats the
player as a legacy member.

Loop constants are module constants in `controller.py`, not settings. `SENDSPIN_PLAYOUT_DEBUG=1`
in the daemon environment raises the summary log to every 1 s and logs every jump with its inputs.

## Interfaces

### Daemon parameters and IPC

- Spawn params gain `playout: {mode, warm_hold_s, warm_in_group, warm_group_max_s, warmup_s,
  trim_us, keep_alive_method}`; `DaemonArgs` gains `playout_mode="off"` and `playout:
  PlayoutSettings | None = None`.
- New commands (`services/ipc/commands.py`, typed, clamped): `set_playout_hold {release: bool}`
  (the parent ends a hold before power_save; skipped while the override keeps a group hold), `set_playout_trim_us {trim_us: int}` (−500…500).
- Status key `playout` (dict, below) and `effective_required_lead_time_ms`.

### Status (`DeviceStatus.playout`, API `DeviceTiming.playout`)

```
mode: off|measure|loop           state: idle|playing|holding|suspended|degraded|fallback
error_ms: float|null  (signed, + late, EMA)      error_p95_ms: float|null  (60 s, |e|)
error_slope_ms_per_s: float|null (60 s fit)      correction_ppm: float|null (r)
corrections_per_s: float|null (10 s)             inserted: int    dropped: int
jumps: int (placements + steady jumps)  last_jump_ms: float|null  last_jump_reason: start|clear|delay|underrun|stall|steady|null
last_jump_at: str|null           underruns: int   pa_steps: int   rejected_samples: int
host_latency_ms: float|null      pa_latency_ms: float|null
sink_rate_ppm: float|null        clock_slew_ppm: float|null
warm: bool  warm_s: float|null   warmup_s: float   hold_reason: grace|group|null  hold_left_s: float|null
late_start_ms: float|null        trim_us: int     fallback_reason: str|null
```

In `measure`/`loop`, `playback_sync_error_ms` (status, API `timing.sync_error_ms`, the Home
Assistant sensor) carries `error_ms`; in `off` it keeps today's meaning. `DeviceAudio` gains
`speaker_offset` (the applied entry, or null) and `speaker_offsets` (all entries).

### HTTP (API v1)

- `GET /api/v1/devices/{id}` — `timing.playout`, `audio.speaker_offset(s)` (response models
  regenerate the SPA client).
- `DELETE /api/v1/devices/{id}/speaker-offsets/{codec}/{adapter}` — forget one entry (204);
  `DELETE /api/v1/devices/{id}/speaker-offsets` — forget all.
- Sync: session resource gains `members[].warm_in_s` and `offset_candidates[]`
  (`member, eligible, reason, offset_ms, codec, adapter`); `POST
  /api/v1/sync/sessions/{id}/sequence` and `…/recordings` accept `force`; new `POST
  /api/v1/sync/sessions/{id}/speaker-offsets {members}` → saved entries. New error code
  `member_cold`.

### UI

- **SyncChip** (device card and list row): in `measure`/`loop` it shows `|error_ms|` with tone
  (≤ 1 ms success, ≤ 5 ms warning, else error) and a "warming 35 s" neutral variant while not warm;
  tooltip: corrections/s, jumps, PA latency, offset source. `off` keeps today's chip.
- **Timing panel** (device drawer): a "Playout" block with every status field above in plain
  words, and "Speaker offset: 212.4 ms · acoustic · SBC · hci0 · 3 days ago" with **Forget**.
- **Settings → Audio (advanced)**: the five keys, with help text.
- **Sync wizard**: warm countdown on the clicks step; "Save as this speaker's offset" on the Tune
  step. en and ru.

### Logs

| Level | Message (one line, player name prefixed) |
|---|---|
| INFO | output opened (mode, rate, buffer, effective lead); output closed (reason); hold started/ended (reason, length); jump (size ms, reason) — steady jumps also as WARNING if > 1 per 10 s; speaker offset applied (value, source, codec, adapter); fallback to legacy (reason) |
| WARNING (rate-limited, once per 10 min per kind) | sensor unavailable; > 3 underruns in 10 min; saturation (\|I\| > 300 ppm for 60 s); > 30 % rejected samples; repeated steady jumps |
| DEBUG | 10 s summary (`e`, p95, `r`, corrections/s, `A − t` slope, fill, PA latency); every correction burst > 10/s; with `SENDSPIN_PLAYOUT_DEBUG=1` a 1 s summary |

## Testing

- **Unit, pure, fake clocks.** `ScheduleMap` (segments, lookup, insert/drop shifts, pruning);
  `PortObserver` (`k₀`, fill, underrun → `k` grows by the deficit, PA step → `x_port` jumps,
  stall); `Corrector` (drop removes exactly one frame, insert adds one, crossfade bounded, bytes
  untouched outside the fade); `LoopController` (step, ramp at ±100/±500/±1500 ppm, noise, single
  outliers, persistent step → jump, dead band, saturation/anti-windup, measure mode never
  corrects); `PlayoutScheduler` (silence fill, placement exact to one frame, drop of a late head,
  gap ≤ 2 ms kept, gap > 2 ms placed, end/clear drop queue and mark `x_end`, resync shift and drop
  of passed audio, format switch); `WarmHold` (grace, group, cap, release, program audio cancels);
  `PlayoutStats` (EMA, p95, slope, rates); `ClockSlewEstimator`.
- **Closed-loop simulation** (`tests/unit/services/audio/playout/sim.py`): a `SimulatedPort`
  consumes the engine's output at `fs·(1+δ)` in RAW time behind a ring of `buffer-time`, with
  Gaussian latency noise, PA "Skipping" events and starvation; the engine runs on a `FakeClock`.
  Acceptance in CI: for δ ∈ {0, ±50, ±500 ppm} and noise σ = 0.3 ms, after 60 simulated seconds
  \|e\| p95 < 0.5 ms, corrections/s ≈ \|δ\|·fs ± 10 %, no steady jump; a 5 ms PA step is corrected
  in < 30 s without a jump; a 15 ms step causes exactly one jump; an underrun is detected and
  re-anchored; δ = 1500 ppm saturates and warns.
- **GStreamer pipeline tests** (`requires_gst`, `fakesink`): the feeder keeps `appsrc` at
  `LEAD_MS`; blocks are contiguous (PTS = previous end); output caps fixed across an input-rate
  change without a new sink; `end_stream` keeps the pipeline PLAYING and pushes silence; `stop`
  goes to NULL; `resync` does not rebuild. With a real `pulsesink` only under
  `@pytest.mark.integration` (needs a PA/PipeWire server with `module-null-sink`): the sensor
  reads an advancing audio clock and its rate matches RAW within 200 ppm over 10 s.
- **Daemon tests**: `DaemonArgs()` default stays `off` (all existing tests unchanged);
  with `playout_mode="loop"`, `stream/end` calls `end_stream()` (not `stop()`), `resync` is still
  called once per coalesced burst with the total shift, status carries `playout`, the declared lead
  is raised.
- **Parent tests**: idle machine with a holding daemon (power_save releases the hold and suspends by
  default, grouped or not; with the global or the per-player override a group hold defers
  power_save, and the per-player value wins; auto_disconnect always releases; keep-alive bursts
  suppressed); per-player opt-out (`effective_playout_mode`, spawn params, status, API, no offsets,
  sync tuning treats the player as legacy); status and
  API models; offset resolver (precedence, PipeWire rule, newer manual wins); config registration
  (schema, translate preserve, diff classification); sync API (`member_cold`, `force`,
  `offset_candidates`, save endpoint, `relative_only`).
- **UI** (vitest): SyncChip variants, timing panel block, settings fields, wizard warm countdown and
  the save checkbox.
- **Live validation** (Stage 5, stand: HAOS PA 17 with ENEBY Portable and one more BT speaker, the
  Docker VM with PipeWire, a wired local output, two Web Kiosks; phone on the desk):
  1. *V-warm*: group = BT speaker + kiosk; clicker; record 2.5 min: kiosk solo 20 s, BT solo 120 s,
     kiosk solo 20 s. `scripts/sync_phase_trace.py` gives per-window phases; BT slope corrected
     by the kiosk bracket (phone crystal); offset = BT − kiosk. S1, S2.
  2. *V-fresh*: the same right after a cold start (hold 0, sink suspended) — documents H1 and
     shows the warm-up is needed.
  3. *Announcement cycles*: 5× announcement → music, warm; offset per cycle. S3.
  4. *PULSE_LATENCY sweep* 300/600/800 ms. S5.
  5. *Delay steps* ±10/30/100 ms from MA during V-warm. S7.
  6. *Stop latency*: pause in MA, phone recording of the cut. S8.
  7. *Wired output*: local sound card into the group; 10 min; compare acoustic phase with
     `playout.error_ms`. S9.
  8. *Forced rate error* (dev machine, PipeWire): `adjtimex --tick` to skew `CLOCK_MONOTONIC` by
     ~500 ppm; loop holds S4; `off` drifts.
  9. *Underrun injection*: `kill -STOP` the daemon for 400 ms; one underrun, one jump, recovery.
  10. *ABX*: rendered files with forced 1/s and 48/s corrections (music, 1 kHz sine). S6.
  11. *CPU*: `pidstat` on the Pi 4, `off` vs `loop`. S10.

## Rollout

- Stages 1–4 ship with `PLAYOUT_SYNC=off` by default on the beta channel; `measure` and `loop` are
  opt-in and documented as experimental. Telemetry in `off` mode ships with Stage 1.
- **Recommendation:** after Stage 5 passes S1–S10 on the stand, flip the beta default to `loop`
  (one beta release with a changelog entry and a re-tune note), keep `off` as the escape hatch
  for at least two beta releases, then decide about removing `StreamPlayer`. Stable (2.x) is not
  affected.
- **No delay migration.** Switching a player from `off` to `measure`/`loop` changes what
  `output_delay` means (it no longer has to absorb `L_host`), but the user decided (2026-10-16)
  that nothing is converted: the beta channel is practically private. Delays tuned before the
  engine was enabled must be re-tuned (group sync tuning, then optionally saved as the speaker's
  offset). The changelog entry for the release that enables the engine and the user guide say so;
  the same holds when a player is opted out again.

## Risks

- **The sensor is wrong in a way the loop cannot see** (PA's bluez5 latency is a fixed guess; PA
  smoother artefacts). The loop then holds an exact error against a wrong port. Mitigation: the
  offset calibration is acoustic and absorbs constants; S2/S3 test repeatability; S9 checks the
  sensor on a wired output; Task 1.1 validates before any correction ships.
- **`sync=false` behaviour differs from the source reading** (ring origin, underrun semantics in
  pulsesink's custom ring). Mitigation: Task 1.1 spike; the fallback to `StreamPlayer`; the
  observer's fill rule re-anchors on any inconsistency.
- **Speakers that sleep on digital silence** shrink or reset their buffer during the hold, so a
  warm stream does not keep them warm. Mitigation: `keep_alive` feeds infrasound; V-fresh vs
  V-warm after a long hold shows it; per-speaker offsets still apply.
- **Battery speakers** stay connected and streaming longer (hold, group). Mitigation: defaults
  (300 s, 1 h cap); `auto_disconnect` always releases the speaker, `power_save` releases it unless
  the user turned on the hold-overrides-power-save option; a problem speaker can be opted out.
- **Larger start lead** delays group starts by up to ~1 s on hosts with `PULSE_LATENCY_MSEC` 800.
  Mitigation: documented; lowering the buffer is the follow-up once the loop is proven.
- **Insert/drop audible on tonal content** at high correction rates. Mitigation: crossfade, rate
  limit, ABX (S6); ASRC follow-up if needed.
- **Existing tuned delays shift by `L_host`** when the mode changes. Mitigation: none in code (no
  migration, by decision); the changelog and the guide say to re-tune.
- **Python timing in the feeder** under GIL contention (FLAC decode). Mitigation: `LEAD_MS` +
  ring give ≥ 240 ms of slack; underrun detection; CPU check S10.

## Decisions (formerly open questions)

Taken by the user on 2026-10-16; the design above and the plan implement them.

1. **Group hold vs `power_save`:** the user's choice. `PLAYOUT_HOLD_OVERRIDES_POWER_SAVE`
   (global, default `false`: `power_save` behaves as today) plus the per-player
   `playout_hold_overrides_power_save` (`null` = global). With the override on, a group hold
   (capped by `PLAYOUT_WARM_GROUP_MAX_S`) makes `power_save` wait. `auto_disconnect` always
   releases the speaker. Web UI: a setting with a one-line explanation (en, ru).
2. **Delay migration:** none. Delays tuned before enabling the engine are re-tuned; the changelog
   and the user guide say so.
3. **Default hold for ungrouped players:** 300 s.
4. **Per-player opt-out:** yes, `playout_sync_enabled` (default `true`); `false` = today's
   behaviour for that player. Config, API, UI and tests in Stage 1.

## Follow-ups (out of scope)

- Variable-ratio resampling (libsamplerate/soxr, ratio from the same PI loop) if ABX shows
  insert/drop artefacts at the measured correction rates (media-sync report P4).
- Lower `PULSE_LATENCY_MSEC` defaults (150–250 ms) once the loop is the default: smaller lead,
  faster delay changes and stops.
- Upstream: Sendspin optional client sync telemetry (`sync_error_us`, `correction_ppm`, resyncs,
  `output_delay_source`, RFC 7272-style), `output_delay_us` (sub-ms), a "bridging clients" note
  defining the port, conformance tests; aiosendspin reusable `PlayoutCorrector`; Music Assistant
  continuous group streams across announcements (or `stream/clear` instead of end/start), a group
  calibration flow for all Sendspin players, sync health from telemetry.
- Home Assistant entities for the new playout fields.
- LE Audio presentation delay when BlueZ/PipeWire ISO support matures.

## Review notes

(Filled in by the review and by Task 1.1's decision note.)

## Task 1.1 decision note (2026-10-16, local, provisional)

Local part only: a dev machine with PulseAudio-on-PipeWire 1.0.5 (`pipewire-pulse`), GStreamer
1.24.2, a wired analog sink and a `module-null-sink`; no Bluetooth speaker. The probe is the
plan's script with the device, `buffer-time` and stream volume as arguments, 140 s per run,
sampled every 100 ms, first 2 s dropped. Items marked PENDING-STAND need the HAOS stand or the
phone; the commands are at the end of this note.

- **Audio clock on bluez5 (PA 17):** measured 2026-10-27 on HAOS (QEMU VM, 2 vCPU), ENEBY20
  `bluez_sink…a2dp_sink` (SBC), `buffer-time` 600 ms, 140 s, while the speaker's sync group was
  also playing music through the bridge. `provide_clock()` non-NULL within the first 0.1 s; `A`
  monotonic over all 1361 samples; rate −7.6 ppm vs RAW (inside ±500). Noise σ of `A − t` after
  the linear fit **4.0 ms** (1 s mean 3.2, 5 s mean 2.7, 10 s mean 2.2 ms) — the same sawtooth as
  on pipewire-pulse (49 steps > 2 ms, median 2.2 ms), slightly smaller. Bracket p99 **4.3 ms**,
  max 54.5 ms: reading the clock on the VM is much slower than locally (p99 39–79 µs), partly
  GIL contention with the feeder thread in the probe. Consequences: the windowed-fit observer from
  the local note is required on PA 17 too; the trim deadband ≥ 2 ms stands; the observer must
  reject samples with a bracket above ~1 ms instead of trusting each read.
  Probe fix: the plan's script pushes 1920-frame (40 ms) blocks labelled 10 ms, so `written`
  and `F` read 4× slow; `A` is unaffected. Use 480-frame blocks for the fill analysis.
- **Audio clock on pipewire-pulse:** available: yes. `provide_clock()` returns
  `GstPulseSinkClock` (`GstAudioClock`) within 0.0–0.13 s of `PLAYING` in all 10 runs; `A`
  never went backwards. Single-stream runs, rate of `A` vs RAW: analog −0.1 / +10.9 / −15.8 ppm
  (`buffer-time` 200 / 600 / 800 ms), null sink −46.5 ppm (600 ms). With six streams at once
  the fits gave −83…+164 ppm, because of step changes in `A − t` (below). Every run is inside
  the ±500 ppm bound. Bracket (the time to read `A`): p99 39–79 µs, max 2.7 ms (rare
  scheduler hiccups). **Noise σ of `A − t` after the linear fit: 7.1–7.5 ms**, at every
  `buffer-time` and on both sinks. This is not white noise. It is a sawtooth: inside each
  ~1.5 s interval `A` runs 0.1–0.6 % off RAW (the smoother extrapolating), then a 6.7 ms
  (median) step corrects it at the next timing update. Means reduce σ only slowly: 1 s mean
  6.6–7.1 ms, 5 s mean 3.5–4.2 ms, 10 s mean 1.3–2.4 ms. The expected 0.1–1 ms was not met.
  Under the plan's rule the Hampel floor would be 2σ ≈ 15 ms.
- **`F` (fill with `k = 0`):** it sits at `buffer-time` − 40…50 ms (200 → 135–157 ms,
  600 → 433–478 ms, 800 → 638–676 ms), with ±30–40 ms of ripple from the same sawtooth plus the
  10 ms appsrc blocks.
- **k₀ rule:** PENDING-STAND (E2: phone on the wired output, `buffer-time` 200 and 800 ms).
- **Underrun (E3, local part):** `buffer-time` 200 ms, the probe process `SIGSTOP`ped for 800 ms.
  A advances during starvation: **yes**. It advanced 850 ms over 851 ms of RAW, and `A − t` stayed
  continuous within the usual sawtooth. The fill rule detects it: **yes**, on the first sample
  after `SIGCONT` (`F` = −627 ms on analog, −672 ms on null). After recovery `F` settles at a
  new constant: pre-gap 158 / 113 ms, post-gap −512 / −554 ms, so `k` shifted by 670 / 667 ms.
  `−F_min` read 627 / 672 ms, an error of 43 / −5 ms. The analog error is about one appsrc
  target (40 ms): the first sample after `SIGCONT` still saw the pre-gap `written`. So `k`
  should be re-anchored from the post-recovery steady `F` (or `F` once data flows again), not from
  the raw `F_min`. Post-gap lateness heard = deficit ± … ms: PENDING-STAND (phone).
- **Delay Report:** HAOS PA (measured 2026-10-27, BlueZ 5.82, PulseAudio 17): **no**. The
  `MediaTransport1` objects of both active speakers (Lenco LS-500 on hci0, ENEBY20 on hci1,
  SBC, `State=active`) carry `Device`, `UUID`, `Codec`, `Configuration`, `State`, `Endpoint`
  and (ENEBY20) `Volume`, but no `Delay` property: neither speaker reports delay on this stack,
  so the per-speaker offset has to come from the phone measurement here. PipeWire:
  PENDING-STAND (Docker VM 105 with a delay-reporting speaker).
- **group/update while a sync group is powered / off:** measured 2026-10-27 with MA dev sync group
  "Beta" = ENEBY20 + Lenco LS-500 (both bridge players). Powered and playing: both daemons get
  the **same** `group_id` (`291a170b…`) with `name` = the leader's player name (ENEBY20), not the
  sync group's name. Power off: both get `state=STOPPED` on that id, then the non-leader gets a
  fresh solo id with its own name (`8904c94c…`). Power on: the non-leader first gets another fresh
  solo id (`1db35897…`), then joins `291a170b…` again; `PLAYING` follows within ~40 ms. So the
  leader's group id is stable across power cycles, solo ids are per session, and "in an active
  sync group" is detectable as `group name ≠ own player name` or a group id shared with another
  bridge player. Earlier local note: the local bridge log
  has only solo players: `Group update: id='64ae3ccc-…'` and `id='77efd219-…'`, each with
  `name` = the player's own name and `state=STOPPED`. So an ungrouped player does receive a
  per-player group id. That id is not its player id in `config.json`. The powered sync group case
  needs MA dev with a sync group.
- **MONO vs RAW:** local −9.7 ppm over 60 s (NTP slewing). HAOS (2026-10-27, 60 s in the SSH
  add-on, same kernel clocks as the bridge container): **+3.1 ppm**, i.e. ~0.003 ms/s — two
  orders of magnitude below the drift in #546, so it is not the cause, but the loop still runs
  in RAW as designed.
  Legacy offset vs `PULSE_LATENCY_MSEC` (400→800): PENDING-STAND (H4).
- **Changes to the plan (provisional, from the local result):**
  - E1 passes on pipewire-pulse for existence, monotonicity, rate and bracket, so Stage 1 keeps
    the `GstAudioClock` sensor and does not pause.
  - E1 fails the noise expectation, so **Tasks 1.6 and 1.7 change**. The Hampel floor of
    0.5 ms is not usable on pipewire-pulse. Per-sample `A − t` must not drive the loop. The
    observer estimates phase from a robust linear fit of `A` vs RAW over a ≥ 10 s window
    (σ ≈ 1.3–2.4 ms here), or from the lower envelope of the sawtooth. The steps (~6.7 ms
    every ~1.5 s) are classified as smoother corrections, not `pa_steps`: the `pa_steps`
    threshold of 2 ms would fire every 1.5 s, so it rises to > 15 ms or uses the windowed
    residual.
  - The S9 ±0.5 ms acceptance on a wired output is at risk on PipeWire. It is re-assessed
    after the stand result for PA 17, whose bluez5 smoother may behave differently. If PA 17
    shows the same sawtooth, the 20 ms jump threshold stays, but the trim loop's deadband
    becomes ≥ 2 ms and the design's sub-ms claims move to "within ~2 ms".
  - The underrun rule stays. `k` takes the deficit from the post-recovery steady `F` (median of
    the first 1–2 s after data flows) instead of `−F_min`, which under-reads by up to one appsrc
    level.
  - E4 and E5 still drive Tasks 4.2 and 2.6 as written. Until the stand answers, Task 2.6
    implements the parent MA-group-cache alternative, since the local logs show that solo
    players also get a non-empty group id, so "has a group id" cannot mean "grouped".

### Stand commands (PENDING-STAND)

On HAOS: install the "Advanced SSH & Web Terminal" add-on (community store), turn
**Protection mode off**, and open its web terminal. Then enter the bridge add-on container
(beta channel, v3):

```sh
C=$(docker ps --format '{{.Names}}' | grep sendspin_bt_bridge | head -1); echo "$C"
docker exec -it "$C" bash
export PULSE_SERVER=${PULSE_SERVER:-unix:/run/audio/pulse.sock}
pactl info | grep -E 'Server (Name|Version)'; pactl list sinks short
```

**E1 on bluez5 (PA 17), with a connected speaker, music stopped in MA.** Save the probe from
the plan as `/tmp/probe.py` (paste it with `cat > /tmp/probe.py`; replace the `pulsesink`
line's `buffer-time=600000` with `device=<bluez_sink…a2dp_sink> buffer-time=600000
volume=0.05`). Then:

```sh
cd /tmp && timeout 150 python3 /tmp/probe.py > /tmp/e1_bt_600.log 2>&1; tail -3 /tmp/e1_bt_600.log
python3 - <<'PY'
rows=[list(map(int,l.split())) for l in open('/tmp/e1_bt_600.log') if l[:1].isdigit()]
rows=[r for r in rows if r[2]>=0]; t0=rows[0][0]; rows=[r for r in rows if r[0]-t0>2e6]
t=[(r[0]-t0)/1e6 for r in rows]; A=[r[2]/1e9 for r in rows]; n=len(t)
mt=sum(t)/n; mA=sum(A)/n; b=sum((x-mt)*(y-mA) for x,y in zip(t,A))/sum((x-mt)**2 for x in t)
res=[y-(mA+b*(x-mt)) for x,y in zip(t,A)]; s=(sum(r*r for r in res)/n)**.5
br=sorted(r[1] for r in rows); print(f"n={n} rate={(b-1)*1e6:+.1f}ppm sigma={s*1e3:.2f}ms p99bracket={br[int(.99*n)]}us")
PY
```

Paste back: the `rate / sigma / p99bracket` line and `/tmp/e1_bt_600.log` (or its first 50 and
last 50 lines).

**E4 Delay Report** (in the same container):

```sh
python3 - <<'PY'
import dbus; b=dbus.SystemBus()
om=dbus.Interface(b.get_object('org.bluez','/'),'org.freedesktop.DBus.ObjectManager')
for p,ifs in om.GetManagedObjects().items():
    t=ifs.get('org.bluez.MediaTransport1')
    if t: print(p, {k:str(v) for k,v in t.items() if k in ('Codec','State','Delay','Volume')})
PY
```

Paste back the printed lines, one per connected speaker. If `Delay` is missing, the speaker
does not report delay on this stack.

**E6 MONO vs RAW on HAOS** (in the container):
`python3 -c "import time;R,M=time.CLOCK_MONOTONIC_RAW,time.CLOCK_MONOTONIC;r,m=time.clock_gettime_ns(R),time.clock_gettime_ns(M);time.sleep(60);print(((time.clock_gettime_ns(M)-m)/(time.clock_gettime_ns(R)-r)-1)*1e6,'ppm')"`.
Paste back the number. H4 (legacy offset vs `PULSE_LATENCY_MSEC` 400 vs 800): in the add-on
options set `pulse_latency_msec` to 400, restart, and play a group with ENEBY20 and the kiosk.
Note `backend_output_latency_ms` from the device's diagnostics and record the ENEBY-vs-kiosk
offset with the phone (the group sync tuning page). Repeat at 800, then set it back.

**E5 group id**: in MA dev, make a sync group of one bridge player and the kiosk. Power the
group on, play anything, then power it off. Then in the HAOS terminal run
`ha addons logs <slug> | grep "Group update: id="` (the slug is the container name without the
`addon_` prefix) and paste back the lines with their timestamps and which state they came from.

**E2 / E3 lateness (phone):** on the dev machine, the wired output plays the click track through
the probe (`buffer-time` 200, then 800) while the phone records near the speaker. For E3, use the
underrun runner (the probe is `SIGSTOP`ped 800 ms at t = 30 s). Hand both recordings and the
probe logs to the agent. It computes `t_heard − t(x_port = x)` and the post-gap lateness.

## Stage 1 findings (2026-10-27, HAOS stand, 3.1.0-beta.14)

- Setup: sync group "Chrome (Renso's Linux, Sendspin web player) + ENEBY20 (bridge)"; the click track
  played as a normal item; MA `volume_mute` alternated the soloed member every ~28 s; one continuous
  phone voice memo; phases from `sendspin_sync.estimator` on 18 s windows of the same recording, so
  both members share one clock (no phone clock sync needed).
- `PLAYOUT_SYNC=off`: ENEBY20 plays 372 ms after Chrome (two cycles: 369.2 / 375.8 ms; Chrome itself
  moved 5.6 ms between its windows; spreads 0.3–2.0 ms).
- `PLAYOUT_SYNC=measure`: ENEBY20 plays 377 ms after Chrome (one cycle; spreads 0.4–0.5 ms). The engine
  reported its error near 0 after the start placement; no steady jump in this run.
- Reading: measure places audio at the PulseAudio port as well as the legacy player does (difference
  within the reference's own drift). Correction (later the same day): the phone runs used the PC
  speakers as the Chrome reference, and those add ~210 ms of their own, and ENEBY20's delay was 0 at
  16:42 (218 was set at 16:51). Re-measured with a headset as the Chrome output: delay 218 → ENEBY20
  49–68 ms early, 160 → 10.5 ms late, linear, so ENEBY20's speaker-side latency is ≈ 170 ms (set).
- Session-to-session variance (#556): with the delay unchanged and the bridge telemetry unchanged
  (PA sink-input latency 195–199 ms, time sync in range), a fresh session at 18:23 put ENEBY20
  205 ms early vs Chrome and 216 ms vs a second local bridge (which agreed with Chrome within
  4–11 ms across fresh starts). The variance is after PulseAudio, in the Bluetooth link or the
  speaker buffer: H1 confirmed in practice, and the case for Stage 2's warm stream. A stored
  per-speaker delay alone cannot fix it.
- Engine defects seen on a desktop PipeWire sink (#556): false steady jumps of 20–27 ms every
  15–20 s (pulsesink clock sawtooth read as error) and a 459 ms steady jump after two underruns.
- The coherent click estimator (cycle-folded matched filter) measures a cheap headset microphone in
  a noisy room where the per-click detector reports not heard.
- Also found: the output rate came from freed pulsectl memory (32681 Hz), fixed in 3.1.0-beta.14;
  output open takes ~1.1 s in both players (first 0.1–0.2 s of each stream dropped as overdue);
  the Lenco LS-500 does not reproduce the click track at all (likely an amplifier noise gate on sparse
  clicks); the group-tuning wizard was unreliable on the stand (not heard / unstable / lost the bridge
  connection through HA ingress and lost the session on reload).
