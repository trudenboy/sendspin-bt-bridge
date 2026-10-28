# Air sensor: when our audio leaves over the air — design

Status: draft for review, 2026-10-28. Target: the v3 line (beta channel), after 3.1.0-beta.16.
Builds on [`2026-10-16-bt-playout-sync-design.md`](2026-10-16-bt-playout-sync-design.md) ("the
playout spec"), in particular its sections "Stage 1 findings", R14, "Stage 2 gate experiment" and
"Morning follow-up", and on the A2DP air parser shipped in #560
(`services/bluetooth/a2dp_air.py`, `scripts/a2dp_air_phase.py`).
Plan: [`2026-10-28-air-sensor-plan.md`](2026-10-28-air-sensor-plan.md).

Rulings this design makes on its own (not taken by the maintainer) are marked **Ruling R-n** and
collected in "Rulings" at the end, so a reviewer can accept or overturn each one separately.

## Goal and decisions

The gate experiment showed that the start-to-start variance of a Bluetooth speaker in a sync
group (spread 33–136 ms, σ 12–44 ms over 8 cold starts in `measure`) is **host-side, before the
air**: where a new PulseAudio stream lands in a Bluetooth stream that never stops. The speaker's
own air-to-sound latency is stable (203 ms, σ 3.1 ms). The pulsesink clock sensor of the playout
engine sees only part of the variance (correlation 0.6 with the microphone) and leaves it inside
the 20 ms `measure` dead band. The rest is invisible from the PulseAudio port.

This design adds a second sensor that looks where the variance becomes visible: the HCI monitor
channel, where every A2DP media packet passes on its way to the controller. It tells, for the audio
the engine wrote, **when it left over the air**. Together with the engine's own schedule `s(x)` and
a one-off calibration of the speaker's air-to-sound latency it gives the speaker's real playout
error.

Decisions taken by the maintainer (2026-10-28, binding):

| Question | Decision |
|---|---|
| Stage 2 (warm hold) | **Cancelled.** Replaced by a check: the bridge detects whether the host suspends the Bluetooth sink in gaps (WirePlumber's suspend timeout on PipeWire, `module-suspend-on-idle` on PulseAudio) and warns in diagnostics/guidance. It never changes the host setting. |
| New stage | **Air sensor.** Stage A = telemetry only (this design in full); stage B = the engine corrects from it (outline only). |
| Where the live HCI monitor runs | In the **parent** process: one socket for all speakers, reusing and extending the existing AVRCP monitor (`hci_avrcp_monitor.py`). |
| What the parent sends to a daemon | Per device, an SBC sub-band **envelope stream** (frame energy from the scale factors of the click/music-relevant sub-bands) with the **air time of each frame** (RTP media time anchored to the send time; the completion time when trustworthy), in small batches over the daemon's stdin. |
| What the daemon does with it | Correlates the air envelope with the envelope of the PCM it wrote (same band, same frame length) to find which written audio left over the air when; with `s(x)` this yields `air error = air_time + speaker_air_to_sound_latency − scheduled play time`. |
| Weak windows | Dropped by a peak/side-lobe gate of about 1.15–1.2 (calibrated from data; today's music data: good windows 1.2–1.5, noise 1.11–1.14). The last offset is held; after N s without a confirmed window the sensor reports **no lock** and the engine stays on its pulsesink sensor. |
| Codecs | **SBC only.** Other codecs → no lock and a diagnostics hint to enable `PREFER_SBC_CODEC`. |
| Privileges | Needs `CAP_NET_RAW`; without it the sensor is **unavailable**, with a reason. |
| Switch | Opt-in `EXPERIMENTAL_AIR_SENSOR` (default `false`), effective only with `PLAYOUT_SYNC=measure` (`loop` later). |
| Stage A outputs | Air error, lock state, confidence and the host-side placement offset in the device status/API, logs (rate-limited) and diagnostics. **Nothing changes playback.** |
| Speaker latency calibration | Inside the existing group sync tuning wizard: while a speaker is measured, the parent records the air timing of the click track (the live equivalent of `scripts/a2dp_air_phase.py`), so `latency = acoustic phase − air phase`; stored per speaker (a config field), shown in the UI. The AVDTP Delay Report is only a hint. Uncalibrated, stage A still reports the host-side placement (air time vs schedule), which is the variance source; the absolute air error needs the calibration. |
| Later stages | Stage 3 (loop) and Stage 4 (offset) of the playout spec build on this sensor; the dead band is re-derived for its accuracy. LE Audio is a research track. #554 is separate. |

## Goals and non-goals

Goals (stage A):

- For every SBC Bluetooth speaker in `measure` with the switch on: the host-side placement
  `H = t_air − s` (how long after its scheduled port time the audio left over the air), with a
  lock state and a confidence, at least once per second while music or the click track plays.
- With a calibration: the air error `E = H + L_as − D_out` (definitions below).
- The calibration of `L_as` inside the sync tuning wizard, stored per speaker × codec × adapter.
- The sink-suspend check and its guidance.
- No effect on playback, no extra latency on the bridge loop, bounded CPU and memory, no new
  privilege.

Non-goals (stage A): any correction (stage B); codecs other than SBC; LE Audio; the speaker's own
buffer behaviour after an AVDTP restart (invisible from the host; the suspend check addresses
its cause); changing host audio settings.

## Evidence

From the playout spec (HAOS stand, PulseAudio 17, ENEBY20 on a CSR8510, SBC 44.1 kHz joint
stereo, bitpool 53, 7 frames per RTP packet).

Stage 2 gate experiment (ENEBY20 vs a local reference bridge in `off`, MA mutes alternating the
solo, headset microphone + the coherent click estimator, 8 cycles per mode):

| Mode | Player | ENEBY20 − reference spread | σ |
|---|---|---|---|
| Cold start (stop, ~6.5 s gap, new stream) | measure | 136 ms (two outliers) / 52 ms (repeat) | 44 / 15 |
| "Seek" (stream end + start, ~0.2 s gap) | measure | 33 ms | 12 |
| Bluetooth reconnect | measure | 18 ms | 5 |
| Cold start | off | 40 ms | 12 |

- The A2DP stream never stops between our streams on HAOS (no `module-suspend-on-idle`); only our
  PulseAudio stream is recreated.
- Air-to-sound latency (btmon capture: SBC scale factors folded over the click cycle, RTP media
  time mapped to the send time of its packet): 203 ms, σ 3.1 ms over 8 cold starts; 192 ms, σ
  4.8 ms over 8 reconnects (medians include the desktop↔HAOS clock offset).
- The measure engine saw part of the variance (residual +7…+13 ms in the slower starts,
  correlation 0.6 with the microphone) and left it in the 20 ms dead band.
- AVDTP Delay Reporting is absent on both test speakers; PulseAudio 17 ignores it anyway.

Morning follow-up:

- Sink suspended in an 8 s gap (`pactl suspend-sink`; the capture shows the A2DP pause and RTP
  restarting from 0): air-to-sound latency σ 9 ms, spread 29 ms, one outlier 20 ms early (σ ≈ 4 ms
  without it). AVDTP restarts occasionally move the speaker buffer; the air sensor cannot see it.
- Music: matching the source PCM's 2.7–8 kHz envelope to the SBC scale-factor envelope (3 s
  windows, ±20 frame search) located the source time of each window at the air with a stable
  mapping over 110 s, peak/side-lobe 1.2–1.5 at 1-frame (2.9 ms) resolution, and showed the
  engine's own 41.5 ms steady jump (logged as 45.3 ms) at the right time.

What the parser of #560 guarantees (module docstring of `a2dp_air.py`):

- Send time: every media packet carries the monitor time of its last ACL fragment. Robust.
- Completion time (NOCP crediting) only when the link is proven in step: a Connection Complete
  inside the capture, or more than 0.5 s without ACL TX while nothing is outstanding. A continuously
  streaming link joined mid-stream never qualifies.
- Known gaps: a lost ACL TX record blinds a continuous link for completions until a pause or a
  reconnect (`in_step=False`, `desyncs > 0`); `btmon -w` writes 0 in every drop field, so offline
  captures never exercise the drop path; a **live** socket must feed the kernel's receive-queue
  overflow count (`SO_RXQ_OVFL`) as `drops`, and there was no live reader until now.
- Malformed input is skipped and counted, never raised; memory is bounded (`max_links`,
  `MAX_IN_FLIGHT`, `max_sdu`, `MAX_EVICTED_KEYS`).

## Definitions

Times are `CLOCK_MONOTONIC_RAW` µs (the Sendspin timeline of the playout spec) unless stated.

| Symbol | Meaning |
|---|---|
| `x` | Output stream time of the playout engine (ns of audio written since the output opened). |
| `s(x)` | The engine's schedule: RAW time at which `x` should leave the PulseAudio port (`ScheduleMap`). It already has the output delay subtracted (`compute_play_time`). |
| `D_out` | The player's current output delay (MA's `sendspin_static_delay`, the daemon's `_player_delay_ms`). The heard schedule is `T(x) = s(x) + D_out`. |
| `u` | RTP media time of a frame's first sample, in seconds (unwrapped RTP timestamp / codec rate). |
| `t_air(u)` | Air time of the frame at `u`: `u + α`, with `α` the send anchor (below). |
| `L_as` | The speaker's air-to-sound latency (calibrated per speaker × codec × adapter). |
| `H` | Host-side placement: `H = t_air − s(x)` for the written cell `x` that left at `t_air`. |
| `E` | Air error: `E = t_air + L_as − T(x) = H + L_as − D_out`. Positive = late. |

`H` is what varies between starts (the gate experiment's finding). `E` is the speaker's real
playout error once `L_as` is known; with `D_out = L_as` (the speaker's delay set to its latency)
`E = H`.

## Architecture

```
 parent process                                                     daemon subprocess (per speaker)
 ──────────────────────────────────────────────────────────────    ─────────────────────────────────────────────
 HciMonitorHub (thread "hci-monitor")                               stdin reader (asyncio)
   recvmsg(HCI_CHANNEL_MONITOR)  + SO_TIMESTAMPNS + SO_RXQ_OVFL        air_source / air_envelope / air_fold /
   REALTIME→RAW (bracketed offset, 4 Hz)                               set_air_calibration
   index map (NEW_INDEX: hciN → adapter MAC),                              │
   (index, handle) → peer MAC                                              ▼
      │ every record                                                 AirSensor (pure, asyncio thread)
      ├─► AvrcpConsumer (unchanged behaviour)                           AirStream: air cells (t_air, e), 20 s ring
      └─► AirConsumer (only while ≥ 1 device subscribed)                Correlator: NCC, PSR gate, sub-frame
            A2dpAirParser (a2dp_air) → MediaPacket                       LockMachine: acquiring/locked/holding/no_lock
            AirLinkTracker per (index, handle):                          → H, E, confidence, jitter
              codec check, SBC params, RTP unwrap + epochs,                     ▲ snapshot copy (lock)
              send anchor α (running median), complete lag,             WrittenEnvelope (20 s ring)
              band selection, frame energy e_j                            fed by PlayoutPlayer._push (feeder thread):
              → AirBatch (≤ 128 frames, 4 Hz)                             comb |x[n]−x[n−3]| peak per cell, s(x) per cell
      AirSensorService (bridge loop)                                 status key "air_sensor" (1 Hz) ─► parent
        link → MAC → SendspinClient (device registry)                  DeviceStatus.air_sensor → API DeviceTiming.air_sensor
        capability gate (daemon advertises air_sensor.accepts)            → UI timing panel, diagnostics, logs
        bounded queue per device (16 msgs), drop-oldest
        AirClickFold (armed by the sync wizard)
```

Process boundaries and why:

- **Parent owns the socket.** One `HCI_CHANNEL_MONITOR` socket sees every controller; a socket per
  daemon would multiply the parse cost by the number of speakers and give every daemon
  `CAP_NET_RAW`-dependent code. The parent already holds the socket for AVRCP correlation.
- **Daemon owns the correlation.** Only the daemon knows what it wrote and `s(x)`; shipping the
  written envelope to the parent would cost as much IPC as shipping the air envelope the other way,
  and stage B needs the result inside the engine.
- **Ruling R-1: the monitor reader moves from the bridge event loop to a dedicated thread.** The
  monitor carries hundreds of records per second per streaming speaker; parsing A2DP on the loop
  would add milliseconds of latency to the API and to IPC. Accuracy does not depend on the reader's
  scheduling (kernel timestamps, below). The AVRCP path keeps its behaviour: `AvrcpSourceTracker`
  writes are already thread-safe ("Thread-safe writes from BT monitor thread", CLAUDE.md).
- **Ruling R-2: the handle map becomes `(index, handle)` → MAC.** Today's `handle_to_mac` ignores
  the controller index; two adapters (the stand has hci0 and hci1) can hand out the same handle. The
  seed from `HCIGETCONNLIST` already knows the `dev_id`; Connection Complete records carry the index.
  The adapter MAC per index comes from the `NEW_INDEX` records the kernel replays on bind (payload:
  type, bus, bdaddr, name).

### Timestamps

The kernel stamps every monitor frame when the HCI core handles it (`__net_timestamp` in
`hci_send_frame` for TX, in `hci_recv_frame` for events) in `CLOCK_REALTIME`; `btmon` reads the
same stamp. The hub enables `SO_TIMESTAMPNS` and reads it from `recvmsg` ancillary data.

- REALTIME → RAW: every 250 ms the reader thread takes a bracketed pair
  `(raw₀, real, raw₁)` (accepted when `raw₁ − raw₀ ≤ 50 µs`, up to 3 tries) and keeps
  `o = real − (raw₀ + raw₁)/2`. A record's RAW time is `t_real − o`, with `o` linearly
  interpolated between the two pairs that bracket the record (the record waits at most 250 ms in a
  small queue for the next pair; R-3). NTP slew (≤ 500 ppm) then contributes ≤ 0.01 ms.
- A step of `o` larger than 5 ms between pairs (a clock step) starts a new epoch for every link
  and drops the records in between.
- **Ruling R-3:** without `SO_TIMESTAMPNS` ancillary data (old kernels, non-Linux test doubles) the
  hub stamps records with `clock_gettime(CLOCK_MONOTONIC_RAW)` at `recvmsg` return, flags the
  source `timestamp: "recv"` and the daemon widens its jitter estimate; the reader thread makes the
  error sub-millisecond in practice.

### Monitor drops

The hub sets `SO_RXQ_OVFL` and passes the cumulative overflow count from the ancillary data to
`A2dpAirParser.feed(..., drops=n)`, which takes every link out of step on an increase (completion
times only; send times stay valid). It also raises `SO_RCVBUF` to 4 MiB (`SO_RCVBUFFORCE` when
`CAP_NET_ADMIN` allows it) so overflow is rare. **[verify]** that `hci_sock_recvmsg` delivers the
`SO_RXQ_OVFL` cmsg on `HCI_CHANNEL_MONITOR` (kernel 6.8/6.12; HAOS 17); if it does not, drops are
unobservable: the hub reports `monitor_drops: null`, relies on the parser's own consistency
checks (excess credits, RTP sequence gaps) and says so in diagnostics (open question 1).

## Air side (parent)

### What is parsed

Only while at least one device subscribes (switch on, effective mode `measure`, Bluetooth backend)
does the hub hand ACL TX, events, `NEW_INDEX`/`DEL_INDEX` and HCI commands to the
`A2dpAirParser`; otherwise it keeps today's AVRCP-only cost (ACL RX and two event codes). One
parser instance serves all links (`max_links=64`).

**Ruling R-4 (CPU): on the live path the SBC CRC is checked on the first frame of each media
packet only** (`parse_sbc_frame(..., check_crc=False)` for the rest). The monitor's bytes come
from the local host; the CRC guards against misparsing, which a valid first frame and a frame
count that matches the media payload header already rule out. Offline tools keep full checking.

### AirLinkTracker (per `(index, handle)`)

Consumes the parser's `MediaPacket`s of one link in send order and produces air cells.

1. **Codec.** `codec == "sbc"` with frames → SBC. Any other media payload type for 2 s → state
   `unsupported_codec` (reason `codec:<name>` from the device's `bt_codec_name` when the parent
   knows it, else `codec:unknown`). `sbc_fragmented` packets (one frame over several packets, never
   seen with BlueZ/PA/PipeWire SBC) count as gaps.
2. **SBC parameters.** `(rate, blocks, subbands, channel_mode)` of the first frame; a change starts
   a new epoch. Frame length `T_f = blocks · subbands / rate` (128/44100 s = 2.902 ms typical).
3. **RTP unwrap and epochs.** RTP timestamps are unwrapped per link (as `unwrap_rtp`). A new
   epoch starts on: an SSRC change; a timestamp step that disagrees with the frames in between by
   more than one frame (RTP restart from 0 after AVDTP SUSPEND/START, seen in the morning
   follow-up); more than 1 s without media packets; a link reset (Connection/Disconnection
   Complete, controller reset); a clock step (above). The epoch counter is per device and goes
   into every batch.
4. **Gaps.** A missing RTP sequence number inside an epoch (a dropped record, a lost SDU) leaves
   the frames it carried missing; the tracker emits them as missing cells (value 255 = "missing")
   so the media timeline stays contiguous.
5. **Send anchor.** For each packet, `α_k = t_send,k − u_k` (`u_k` = unwrapped RTP / rate). The
   anchor used for a frame is the running median of the last 64 `α_k` of the epoch (≈ 1.3 s at 49
   packets/s). A packet's own send time jitters with controller credits; the median removes it.
   The host's RTP pacing follows `CLOCK_MONOTONIC` (PulseAudio's bluez sink), so `α` drifts by the
   NTP slew (≤ 500 ppm): the median's half-window lag costs ≤ 0.33 ms. `t_air(frame) = u_frame + α`.
6. **Completion lag.** When the parser credits completions (link in step), the tracker keeps the
   median of `t_complete − t_send` over the last 2 s as `complete_lag_us` (telemetry; see R-5).
7. **Band and energy.** Sub-band set `B`: every sub-band `b ≥ 1` whose centre
   `(b + ½) · rate / (2M)` lies in 2.5–8.5 kHz; `{1}` when that is empty. For 8 sub-bands this is
   bands 1–2 (2.76–8.27 kHz at 44.1 kHz, 3–9 kHz at 48 kHz), the band of the morning experiment;
   for 4 sub-bands band 1. Frame energy `e = round(8 · log2(1 + Σ_{ch} Σ_{b∈B} 2^{sf[ch][b]}))`,
   clipped to 0…254 (a scale factor is log2 of the sub-band's peak, so this is a log peak
   amplitude in 1/8-bit steps; the maximum is about 8 · 18 = 144).
8. **Batches.** Contiguous cells of one epoch are cut into batches of at most 128 frames, sent
   every 250 ms (or when 128 frames are pending), so a batch is ≤ 372 ms of audio. A gap of more
   than 32 missing frames closes the batch.

**Ruling R-5: the canonical air time is the send anchor; completion is telemetry.** The maintainer
asked for "RTP media time anchored to send time; completion time when trustworthy". The send time
exists for every packet and survives the parser's known blind spots (lost records blind
completions, not sends). A link that streams continuously from before the monitor started never
gets proven completions, so a completion-anchored sensor would be unavailable exactly on the
stand's typical case. Calibration and runtime must use the same anchor (`L_as` absorbs the constant
send→air part); mixing anchors would shift `E` by the completion lag. So: `t_air` is always
send-anchored; `complete_lag_ms` is reported beside it, and the batch carries it so stage B can
switch anchors later together with a re-calibration.

### Rate limits and bounds (parent)

- At most 400 frames/s per device are accepted (SBC at 48 kHz with 4 blocks × 4 sub-bands would be
  3000/s; such a configuration is treated as unsupported: reason `sbc_params`).
- Per device a queue of 16 batches (≈ 4 s); when the daemon's stdin is slow the oldest is dropped
  and counted (`ipc_dropped`).
- CPU guard: the hub measures its parse time; above 5 % of one core over 30 s it unsubscribes the
  air consumer (AVRCP continues) and reports `unavailable: cpu_budget` until the next restart.

## IPC

Additive commands on the daemon's stdin (JSON lines, `build_command_envelope`), decoded in
`services/ipc/commands.py` like every other command.

**Ruling R-6: no `IPC_PROTOCOL_VERSION` bump; capability-gated instead.** The commands are
additive. A daemon spawned with the air sensor enabled advertises
`air_sensor.accepts: [1]` in its status; the parent sends air commands only to a daemon that
advertised the version it speaks. An older daemon therefore never receives the verbs (it would
emit `unknown_command` for each batch). The payload carries its own schema version `v`.

### `air_envelope` (parent → daemon, ≤ 4 Hz)

```json
{"cmd": "air_envelope", "protocol_version": 2, "v": 1,
 "epoch": 17, "seq": 4096,
 "t0_us": 1234567890123.4, "frame_us": 2902.494, "timestamp": "kernel",
 "rate": 44100, "subbands": 8, "blocks": 16, "bands": [1, 2],
 "complete_lag_us": 8123.0,
 "e": [41, 44, 39, 255, 52, 61]}
```

| Field | Meaning | Daemon validation (else the message is dropped and counted) |
|---|---|---|
| `v` | schema version | `== 1` |
| `epoch` | per-device epoch counter | int ≥ 0; a new value resets the air ring and the lock to `acquiring` |
| `seq` | index of the first frame within the epoch | int ≥ 0; a jump forward = missing frames; backwards = dropped |
| `t0_us` | RAW air time of the first frame's first sample (send-anchored) | finite, within ±10 s of the daemon's RAW now |
| `frame_us` | frame duration | 500…20000 |
| `timestamp` | `kernel` or `recv` (R-3) | one of the two |
| `rate`, `subbands`, `blocks`, `bands` | SBC parameters and the band set used | rate ∈ {16000, 32000, 44100, 48000}; subbands ∈ {4, 8}; blocks ∈ {4, 8, 12, 16}; bands ⊂ [0, subbands) |
| `complete_lag_us` | median send→completion, or null | null or 0…200000 |
| `e` | energies, 255 = missing | list of ints 0…255, length 1…256 |

Frame `j` of the batch: air time `t0_us + j · frame_us`, media index `seq + j`. A message is at most
~1.3 KB (256 entries), far below the 64 KiB `StreamReader` line limit; typical traffic is ~4 KB/s
per speaker.

### `air_source` (parent → daemon, on change and every 5 s)

```json
{"cmd": "air_source", "v": 1, "state": "streaming", "reason": null, "codec": "sbc",
 "adapter": "C0:FB:F9:62:D7:D6", "index": 1, "handle": 70, "in_step": false,
 "monitor_drops": 0, "ipc_dropped": 0}
```

`state ∈ {streaming, no_traffic, unsupported_codec, unavailable}`; `reason` free text ≤ 80 chars
(`no_cap_net_raw`, `unsupported_platform`, `monitor_error`, `cpu_budget`, `codec:aac`,
`sbc_params`, …); `adapter` is the controller MAC (the calibration key). The daemon combines it
with its own correlation state (state machine below) and is the only producer of the final status
dict while it runs.

### `set_air_calibration` (parent → daemon, at spawn via params and on change)

`{"cmd": "set_air_calibration", "latency_ms": 203.4}` or `latency_ms: null` (uncalibrated);
clamped to 0…2000. The parent resolves the entry for the speaker's current codec and adapter
(below) and resends it when either changes.

### `air_fold` (parent → daemon, calibration only)

`{"cmd": "air_fold", "id": "c1f3…", "from_us": …, "to_us": …}`: fold the written envelope over the
click cycle on the heard-schedule axis `T = s + D_out` between the two RAW times and report
`status.air_sensor.fold = {"id", "phase_ms", "ok", "reason"}` within 2 s (one pending fold per
daemon; a new id replaces the old one). Bounded to 30 s of span.

### Status (daemon → parent)

The daemon publishes `air_sensor` (a dict, below) at 1 Hz while the output is open (5 s
otherwise) through the existing status envelope. The parent adds it to `_ALLOWED_STATUS_KEYS`.

## Written side (daemon)

### WrittenEnvelope

A tap in `PlayoutPlayer._push` (feeder thread) records, for every pushed block, the block's PCM
envelope on a fixed cell grid in output stream time and the schedule of each cell.

- **Cell grid:** cell `i` covers `x ∈ [i·T_c, (i+1)·T_c)`, `T_c = 128 / f_out` (the SBC frame of
  16 blocks × 8 sub-bands at the output rate, which is the sink's rate and the SBC rate on a bluez
  sink). When the air batches report another frame length the correlator resamples the air cells
  onto `T_c` by linear interpolation (R-7 below keeps this rare).
- **Envelope:** comb high-pass `y[n] = x[n] − x[n−3]` on each channel, cell value
  `w_i = log2(1 + max |y|)` over the cell, both channels. `|H(f)| = 2|sin(3πf/f_s)|` peaks at
  `f_s/6` (7.35 kHz at 44.1 kHz, 8 kHz at 48 kHz), is ≥ 1.1 over 2.7–8.3 kHz and −27 dB at 200 Hz:
  close to the sub-bands 1–2 the air side uses, and computable at C speed with
  `map(operator.sub, …)`/`map(abs, …)`/`max` on `memoryview.cast("h")` slices (≈ 4 ms of CPU per
  second of stereo audio).
- **Schedule:** `s_i = ScheduleMap.at(x_i)` at render time (the map always covers the write head),
  plus `kind_i` (program/silence/placement silence). Silence cells get `w = 0` and are excluded from
  the correlation statistics.
- **Ring:** 20 s (6 892 cells at 44.1 kHz) in `array('d')` (s) and `array('f')` (w) plus a
  `bytearray` of kinds; a `threading.Lock` guards append (feeder) and slice copies (asyncio).
  ≈ 90 KB.
- The tap never blocks the feeder: it does no allocation beyond the slices it reads and takes the
  lock for a copy of at most one block's cells.

**Ruling R-7: the PCM envelope is the comb high-pass peak, not a true 2.7–8 kHz band-pass.** A
per-sample IIR band-pass in pure Python costs ≈ 2 % of a desktop core per speaker (≈ 6 % on a Pi
4); the comb costs ≈ 0.4 %. Correlation uses z-scored log values, so gain differences do not
matter; spectral weighting differences lower the peak a little. Plan Task A8 measures the PSR of
both envelopes on the synthetic music fixture and on the morning capture; if the comb's PSR is more
than 0.05 below the band-pass's on the same windows, the band-pass replaces it (the interface does
not change).

**Ruling R-8: the written envelope is taken from the PCM the engine writes, before the user's
volume.** Volume and mute are applied in pulsesink, after the tap; the z-scored correlation is
invariant to volume, and a muted stream has no air envelope (the window fails the gate: no lock,
which is correct). The engine's tail mute after `clear` is the same case.

### Correlation

Inputs: air cells `a_j` (energies, missing flagged) on the media grid of one epoch, with air times
`t_j = t0 + j·T`, and written cells `w_i`, `s_i`.

Model: inside one epoch and one output stream, PulseAudio consumes `x` and the encoder stamps it
with RTP media time at the same sample rate, so `u = x + c` with a constant `c` (cells: written cell
`i = j + L` for air frame `j`, `L` constant). `L` changes only when PulseAudio inserts or drops
audio that is not ours (an underrun the port ran through, a "Skipping" catch-up drop), when the
output is reopened (`x` restarts), or when the air epoch changes. **Engine placements and steady
jumps do not change `L`**: they change what is written at `x` and therefore `s(x)`, which is exactly
what `H` measures. The tracking search is therefore narrow and survives engine corrections.

Per window (**Ruling R-16**: W = 3 s = 1034 cells at 2.9 ms; hop 1 s; the window ends at the newest air cell whose
written partner is older than the ring's newest cell):

1. **Validity.** At least 90 % of the window's air cells present and at least 80 % of the
   candidate written cells program (not silence). Else the window is `skipped` (not counted as a
   failure for the confidence, see below).
2. **Normalised cross-correlation** for every candidate lag `ℓ` in the search range:

   ```
   â_j = (a_j − ā) / σ_a            (over the window, missing cells excluded)
   ŵ_j(ℓ) = (w_{j+ℓ} − w̄(ℓ)) / σ_w(ℓ)   (same cells, per lag; prefix sums give w̄, σ_w in O(1))
   ρ(ℓ) = (1/N) Σ_j â_j ŵ_j(ℓ)
   ```

   Flatness gate: `σ_a ≥ 2` (energy units: 1/4 bit) and `σ_w(ℓ) ≥ 0.25` (bits); otherwise the
   window is `flat` (skipped).
3. **Search range (Ruling R-15).** *Acquisition* (no lock, new epoch, output reopened): the prior `L̂` puts the
   window's middle air cell on the written cell whose `s` is closest to `t_mid − δ₀`, `δ₀ = 10 ms`
   (the PulseAudio → controller time); range ±250 ms (±86 cells), which covers the largest
   start-to-start spread seen (136 ms) with margin. *Tracking* (locked): `L̂` = the last confirmed
   `L`, range ±20 cells (±58 ms, the morning experiment's range). After an engine `underrun`,
   `pa_step` or `stall` event the next window searches ±86 cells around `L̂`.
4. **Peak and side lobe.** `ℓ* = argmax ρ`; side lobe `ρ_s = max ρ(ℓ)` over `|ℓ − ℓ*| ≥ 3`;
   `PSR = ρ(ℓ*) / ρ_s` (with `ρ_s ≤ 0.01` taken as 0.01). A peak at the edge of the range is
   rejected (the true lag may lie outside).
5. **Gate.** Confirmed when `PSR ≥ 1.17` and `ρ(ℓ*) ≥ 0.25`. **Ruling R-9:** 1.17 sits between the
   noise (1.11–1.14) and the good windows (1.2–1.5) of today's data; Plan Task A10 recalibrates it on
   the fixtures and Task A17 on stand data (target: false-confirm rate < 1 % on noise windows, ≥ 80 %
   of music windows confirmed), and it stays a code constant, not a user setting.
6. **Sub-frame refinement.** Parabolic interpolation on `ρ(ℓ*−1), ρ(ℓ*), ρ(ℓ*+1)`:
   `λ = (ρ₋ − ρ₊) / (2(ρ₋ − 2ρ₀ + ρ₊))`, clamped to ±0.5; `L = ℓ* + λ`.
7. **Continuity (R-15).** In tracking, a confirmed `L` more than 3 cells from the last confirmed one is
   held as a *candidate* and accepted only when the next confirmed window agrees with it within 1
   cell (a real `c` step persists; a wrong peak does not).
8. **Placement.** With `j_m` the window's middle cell and `i_m = j_m + L` (fractional; `s`
   interpolated linearly between cells, never across a segment boundary — a window whose matched span
   crosses a schedule discontinuity reports the side that holds most of the window):

   ```
   H = t_{j_m} − s(i_m) + Δ_enc           Δ_enc = 4.5 · M / rate − 1.5 / f_out
   E = H + L_as − D_out                    (only with a calibration)
   ```

   `Δ_enc` moves the SBC energy (which lags the PCM by the encoder's analysis delay 4.5·M samples,
   `sbc_encoder_delay_samples` in the phase tool) and the comb's own group delay (1.5 samples) onto
   one PCM instant; it is 0.8 ms for M = 8 at 44.1 kHz.

Accuracy targets (to be measured in Tasks A10/A17, not assumed): per confirmed window σ(H) ≤ 1 ms
on music; the reported `H` (median of the last 5 confirmed windows) within ±2 ms of the fixture's
truth; agreement with the microphone within ±5 ms (acceptance, below).

Cost: tracking 41 lags × 1034 cells ≈ 42 k multiply-adds per window, ≈ 1.5 ms of Python
(`sum(map(operator.mul, …))`) per second; acquisition 173 lags ≈ 6 ms per window, only while not
locked.

### Lock state machine and confidence

```
            source not streaming / codec / unavailable
   ┌──────────────────────────────────────────────────────────────┐
   ▼                                                              │
 [off] ── switch on, measure ──► [acquiring] ── confirmed ──► [locked] ◄──────┐
                                    ▲    ▲                        │           │ confirmed
                                    │    └── epoch / reopen ──────┤           │
                                    │                             │ 5 s none  │
                                    │                             ▼           │
                                    │                         [holding] ──────┘
                                    │                             │ 20 s none since the last confirmed
                                    │                             ▼
                                    └──── (searches as in acquiring) ── [no_lock]  (values null; engine stays on pulsesink)
                                          a confirmed window → locked
```

| State | Entered when | Reported values |
|---|---|---|
| `off` | switch off, or effective mode not `measure` | the dict carries `state` and `reason` only |
| `unavailable` | the parent says so (no `CAP_NET_RAW`, platform, monitor error, CPU budget) | reason |
| `unsupported_codec` | the source is not SBC (or SBC with unsupported parameters) | reason, codec |
| `no_signal` | source `no_traffic` (not streaming over the air) | — |
| `acquiring` | air data flows, no confirmed window in this epoch yet | counters |
| `locked` | a confirmed window within the last 5 s | `H`, `E`, confidence, jitter |
| `holding` | last confirmed window 5–20 s ago | the last `H`/`E`, confidence decaying |
| `no_lock` | 20 s without a confirmed window (**Ruling R-10**: N = 20 s, long enough for quiet passages, short enough to stop reporting a stale value after a hidden step) | `H`, `E` null |

Confidence ∈ [0, 1], **Ruling R-11**:
`confidence = clip((PSR_med − 1.17) / (1.40 − 1.17), 0, 1) · f_ok · decay`, with `PSR_med` the
median PSR of the confirmed windows in the last 10 s, `f_ok` the share of confirmed among the
non-skipped windows in the last 10 s, and `decay = 1` in `locked`, falling linearly to 0 over the
`holding` period.

Jitter: the MAD (×1.4826) of `H` over the confirmed windows of the last 30 s.

### Status dict (`DeviceStatus.air_sensor`, API `DeviceTiming.air_sensor`)

```
state: off|unavailable|unsupported_codec|no_signal|acquiring|locked|holding|no_lock
reason: str|null                      codec: str|null          adapter: str|null
host_placement_ms: float|null  (H, median of the last 5 confirmed windows)
air_error_ms: float|null       (E; null when uncalibrated or not locked/holding)
air_to_sound_ms: float|null    (L_as in use)     calibrated: bool
confidence: float|null         jitter_ms: float|null       psr: float|null (last confirmed)
lag_cells: float|null          windows_confirmed: int      windows_rejected: int    windows_skipped: int
last_confirmed_at: str|null    epochs: int                 frames: int    missing_frames: int
anchor: "send"                 complete_lag_ms: float|null timestamp: kernel|recv|null
monitor_drops: int|null        ipc_dropped: int            dropped_messages: int
accepts: [1]
fold: {id, phase_ms, ok, reason}|null   (calibration only)
```

## Failure modes and fallbacks

| Failure | Detection | Behaviour |
|---|---|---|
| No `CAP_NET_RAW` / non-Linux / no adapter | socket open fails (`EPERM`, `OSError`) | AVRCP monitor as today (INFO once); air sensor `unavailable` with reason; guidance issue `air_sensor_unavailable` |
| Monitor socket error mid-run | `recvmsg` raises | existing backoff (5 → 60 s); devices get `air_source unavailable: monitor_error`; epochs restart after reopen |
| Codec not SBC | `MediaPacket.codec != "sbc"` for 2 s | `unsupported_codec`; guidance issue `air_sensor_codec` ("enable `PREFER_SBC_CODEC`") |
| Speaker not streaming | no media packets for 1 s | `no_signal` (after 1 s), lock state reset at the next epoch |
| Monitor overflow | `SO_RXQ_OVFL` increase | parser leaves step (completions only); missing frames; counted; WARNING once per 10 min |
| Lost ACL record / SDU | RTP sequence gap | missing cells; windows below 90 % present are skipped |
| AVDTP SUSPEND/START, RTP restart | RTP step / SSRC / 1 s silence | new epoch → `acquiring` |
| PA underrun / "Skipping" (c changes) | engine events, or the peak walks off | wide search for one window; continuity rule accepts the new `L` after two agreeing windows |
| Output reopened (new PA stream) | daemon-local event | written ring cleared, `acquiring` |
| Silence, quiet or flat music | validity/flatness gates | windows skipped; `holding` → `no_lock` after 20 s |
| Repetitive music (loops, four-on-the-floor) | side lobe close to the peak | PSR gate rejects; continuity rule rejects a jump to a neighbour period |
| Mixed audio on the sink (keep-alive burst, other apps) | lower ρ | gate rejects |
| Daemon too old / restarting | no `air_sensor.accepts` | parent sends nothing (R-6) |
| Clock step | REALTIME offset jump > 5 ms | records dropped, new epochs |
| Parse cost too high | CPU guard | air consumer off, `unavailable: cpu_budget` |
| Garbage on the monitor | parser counters | skipped and counted (never raised) |

The engine is never affected: stage A reads `s(x)` and writes nothing back; the tap and the
correlator are wrapped so that an exception disables the air sensor for that stream
(`unavailable: internal_error`, logged once with the traceback) and never reaches the feeder.

## CPU and memory budget

| Where | Per streaming speaker | Notes |
|---|---|---|
| Parent: monitor read + parse | ≈ 150 records/s (≈ 49 media packets of 7 frames, 1–3 ACL fragments, NOCP); target ≤ 1.5 % of a Pi 4 core | CRC on the first frame only (R-4); measured by `scripts/air_sensor_bench.py` on the fixtures (Task A4) |
| Parent: tracker + batching | ≈ 344 frames/s, 4 messages/s | negligible |
| Parent memory | parser state (≤ 256 fragments per link), ≤ 16 queued batches, fold ring 30 s (armed only) | < 1 MB |
| Daemon: tap | ≈ 0.4 % of a desktop core | comb on C-speed maps (R-7) |
| Daemon: correlation | ≈ 1.5 ms/s tracking, ≈ 6 ms/s acquiring | asyncio thread, one window per second |
| Daemon memory | written ring 20 s + air ring 20 s | ≈ 0.2 MB |
| IPC | ≈ 4 KB/s per speaker | |

Combined with the playout spec's S10 (≤ 2 % of a Pi 4 core per stream for the engine), stage A
targets ≤ 3 % per speaker on a Pi 4 above `measure` without the sensor (checked in Task A17).

## Security

- **Untrusted input.** The monitor carries bytes from the controller firmware (events, NOCP),
  from remote devices (ACL RX) and from every local process with Bluetooth access (ACL TX). The air
  path parses only ACL TX, a few event codes and index records; the existing parser never raises
  and bounds memory (links, in-flight fragments, SDU size, evicted keys). The tracker bounds frames
  per second and the batch size; the daemon validates every field of every message (types, ranges,
  lengths, time window) and drops what fails, counting it, without an error envelope per message
  (no log amplification: one rate-limited WARNING per 10 min).
- **Sensitive traffic.** The monitor socket also shows pairing traffic (PIN, link keys) and the
  content of every ACL packet. The hub never logs, stores or exposes raw records; nothing is written
  to disk (offline captures stay a manual `btmon` step). The envelope sent to a daemon is a coarse
  loudness contour of the audio the bridge itself is playing to that speaker; it never leaves the
  host, and the API exposes only aggregates (no envelopes).
- **Cross-device isolation.** A daemon receives only the envelope of its own speaker (the link is
  attributed by `(index, handle)` → MAC → configured player; an unattributed link is ignored).
- **Privileges.** No new privilege: `CAP_NET_RAW` is already required for the AVRCP monitor
  (container `NET_RAW`); daemons stay without it. `SO_RCVBUFFORCE` is attempted only when
  `CAP_NET_ADMIN` is present (it is, in the documented container setup) and falls back to
  `SO_RCVBUF`.
- **IPC.** Only the parent writes the daemon's stdin (unchanged trust boundary).

## Configuration, API, UI

### Configuration

| Key | Where | Type / default | Change class |
|---|---|---|---|
| `EXPERIMENTAL_AIR_SENSOR` | global | bool, `false` | `GLOBAL_RESTART` (spawn params and the hub subscription) |
| `air_to_sound_latency` | per player | list of entries, ≤ 8, default `[]` | hot (`set_air_calibration`) |

Effective rule (`air_sensor_effective(player_cfg, cfg) -> (bool, reason)`): on when
`EXPERIMENTAL_AIR_SENSOR` is true, the player's effective playout mode is `measure` and its backend
is Bluetooth; else off with reason `switch_off`, `needs_measure` (mode `off` or opted out),
`loop_not_supported_yet` (mode `loop`, stage B) or `not_bluetooth`.

Calibration entry (**Ruling R-12**: keyed by codec × adapter MAC like the playout spec's planned
`speaker_offsets`, because the air-to-sound latency depends on the codec's decoder buffer and the
adapter's controller):

```json
{"codec": "sbc", "adapter": "C0:FB:F9:62:D7:D6", "latency_ms": 203.4, "spread_ms": 3.1,
 "measured_at": "2026-10-28T09:12:00+00:00", "reference": "Chrome (headset)", "source": "sync_wizard"}
```

Keys go everywhere the playout keys went: `DEFAULT_CONFIG`, `config/schema.json`, `BridgeConfig`,
`_ALLOWED_POST_CONFIG_KEYS` + POST normalisation, `web_ui_only_keys` in
`scripts/translate_ha_config.py`, `config_diff.py`; the per-player field in `config/players.py`
and the schema's player definition.

### HTTP (API v1)

- `GET /api/v1/devices/{id}`: `timing.air_sensor` (`AirSensorTiming`), `audio.air_to_sound_latency`
  (the entries) — the SPA client is regenerated.
- `DELETE /api/v1/devices/{id}/air-calibrations/{codec}/{adapter}` → 204 (forget one);
  `DELETE /api/v1/devices/{id}/air-calibrations` → 204 (forget all).
- Sync sessions: `SyncSessionOut.air_calibrations[]`
  (`member, eligible, reason, latency_ms, acoustic_lag_ms, host_placement_ms, codec, adapter`) after
  a recording; `POST /api/v1/sync/sessions/{id}/air-calibrations {members: [...]}` saves the chosen
  ones. Error codes: `air_not_locked`, `no_reference`.

### UI

- **Device drawer → Timing panel:** an "Air sensor" block: state with reason in plain words,
  "Host placement +12.4 ms (±0.8)", "Air error −3.1 ms" or "needs calibration", confidence, codec;
  "Speaker latency: 203.4 ms · SBC · hci1 · 2 days ago" with **Forget**.
- **SyncChip tooltip:** one line "air: locked, H +12.4 ms" when the sensor is on.
- **Settings → Audio (advanced):** `EXPERIMENTAL_AIR_SENSOR` with "Needs Playout sync = measure and
  SBC; measures when the audio leaves over the air. Does not change playback."
- **Sync wizard:** on the result step, for bridge members whose air sensor was locked during their
  solo: "Speaker latency (air → sound): 203 ms" and a checkbox "Save as this speaker's latency".
- en and ru.

### Logs

| Level | Message (player name prefixed) |
|---|---|
| INFO | air sensor state change (`locked` with H and PSR, `no_lock`, `unavailable` with reason, `unsupported_codec`), at most once per minute per kind; calibration applied (value, codec, adapter) |
| WARNING (once per 10 min per kind) | monitor drops; invalid air messages; tap/correlator internal error (first one with traceback) |
| DEBUG | 10 s summary (state, H, E, PSR, confirmed/rejected/skipped, lag); per window with `SENDSPIN_AIR_DEBUG=1` |

### Diagnostics and guidance

New entries in `guidance_issue_registry.py` and producers in `recovery_assistant.py`:

| Key | Layer | Severity | Reason codes | Remediation |
|---|---|---|---|---|
| `air_sensor_unavailable` | bluetooth | info | `no_cap_net_raw`, `unsupported_platform`, `monitor_error`, `cpu_budget` | grant `NET_RAW` (container), or turn the switch off |
| `air_sensor_codec` | bluetooth | info | `non_sbc_codec` | enable `PREFER_SBC_CODEC` and reconnect the speaker |
| `bt_sink_suspend_in_gaps` | audio | warning | `wireplumber_suspend_timeout`, `module_suspend_on_idle`, `observed_suspend` | disable the host's Bluetooth sink suspend (docs link); the bridge does not change it |

The diagnostics bundle includes each device's `air_sensor` dict and the hub's counters.

## Sink suspend check (replaces Stage 2)

The morning follow-up showed that an AVDTP SUSPEND/START in a gap moves the speaker buffer
(σ 9 ms vs ≈ 4 ms). The bridge cannot see that from the air; it can see whether the host does it.

- **Static probe** (parent, at start and every 10 min, cheap): `pactl info` tells PulseAudio vs
  PulseAudio-on-PipeWire. PulseAudio: `pactl list modules short` → `module-suspend-on-idle`
  present (and its `timeout=` argument, default 5 s). PipeWire: if the native socket is reachable,
  `pw-dump` → the `session.suspend-timeout-seconds` property of `bluez_output.*` nodes (0 =
  disabled; WirePlumber's default is 5); if not reachable, `unknown`.
- **Observation** (authoritative): `SinkMonitor` already tracks `running ↔ idle/suspended` for
  registered Bluetooth sinks. A transition to `suspended` while the device is connected and the
  bridge did not ask for it (not `power_save`, not standby, not `auto_disconnect`) is recorded as an
  observed host suspend with the idle time before it.
- **Ruling R-13:** the guidance issue fires on an observed suspend (reason `observed_suspend`, with
  the static probe's explanation when known) or on a static probe that finds a suspend policy with a
  timeout shorter than 60 s; it clears after 24 h without an observation and a clean probe.
- Text: what happens (the speaker restarts its stream and may shift by tens of ms between songs),
  how to disable it on PipeWire (WirePlumber 0.4 `bluez_monitor.rules` / 0.5 `monitor.bluez.rules`
  with `session.suspend-timeout-seconds = 0`) and on PulseAudio (unload `module-suspend-on-idle`),
  and that the bridge never changes host settings. The air sensor corroborates with its epoch
  counter (RTP restarts) in the same device's diagnostics.

## Calibration of `L_as` (sync tuning wizard)

Requirement (**Ruling R-14**): the recording must contain a **reference** member whose sound is on
the Sendspin schedule (the session's chosen reference, `set_reference`; typically a Sendspin web
player on a headset or wired output). A phone recording has no clock link to the bridge, so the
speaker's acoustic phase can only be expressed against another member's. The UI says so; without a
reference the calibration is not offered (`no_reference`). Stage 1 showed what an unsuitable
reference does (PC speakers added ~210 ms).

Flow (semi mode, the phone capture sequence; the live microphone flow works the same way per
recording):

1. While the sequence runs, `application/sync_tuning.py` watches the session's `sequence` phases on
   a small thread and stamps each solo's start and end in RAW time (the `sendspin_sync` core and its
   clock stay untouched; its boundary test stays green).
2. For every bridge member with the air sensor on, the parent arms an `AirClickFold` on that
   speaker's link during its solo: it keeps `(t_air, chirp-band energy)` of every SBC frame and,
   at the solo's end, computes the air click phase `φ_air` with exactly the phase tool's method
   (`chirp_subbands`, `frame_energy`, `click_phase_ms` with the encoder delay; these move from
   `scripts/a2dp_air_phase.py` into `services/bluetooth/a2dp_air_fold.py`, and the script imports
   them). The phase is on the RAW axis (epoch 0, estimator convention).
3. The parent asks the daemon for the scheduled click phase over the same span (`air_fold`): the
   daemon folds its written envelope on the heard-schedule axis `T = s + D_out` with the same
   `click_phase_ms` (encoder delay 0, comb delay removed) → `φ_T`. Then
   `H_cal = unwrap(φ_air − φ_T)` (unwrapped into ±5 s; the click cycle is 10 s).
4. After the upload, the estimator gives each member's acoustic phase on the recording's clock.
   With the reference `R`: `A = unwrap(φ_ac(speaker) − φ_ac(R))` (the planner's `unwrap_lag_ms`),
   the speaker's acoustic lateness against the schedule.
5. `L_as = A − H_cal`. Both terms were measured over the same solo under the same `D_out`, so the
   delay cancels: `A = t_sound − T`, `H_cal = t_air − T`, `L_as = t_sound − t_air`.
6. Eligibility: the speaker's air sensor was locked or acquiring with SBC during the solo, the air
   fold found clicks (the fold's pattern score is above its floor), the acoustic measurement is
   `ok`, and the reference is `ok`. The session shows `air_calibrations[]`; the user saves the ones
   they want; the entry is stored for the speaker's codec and adapter and sent to the daemon.
7. The AVDTP Delay Report, when a speaker has one, is shown beside the result as a hint, never
   used as the value.

Repeat measurements: saving a new value for the same key replaces the entry; `spread_ms` is the
spread of the estimator's per-click phases (as reported by `Measurement.spread_ms`).

## Test strategy

- **Unit, pure:** band selection table; frame energy; RTP unwrap and epoch rules; send anchor
  (median, slew, jitter); batch cutting and bounds; IPC encode/decode and every validation rule;
  REALTIME → RAW interpolation and clock-step detection; cmsg parsing (`SO_TIMESTAMPNS`,
  `SO_RXQ_OVFL`); `(index, handle)` map with two controllers reusing a handle; comb envelope against
  a reference implementation on known signals; written ring (wrap, slices, segments); NCC (known
  lag, sub-frame lag, noise, flat, missing cells, edge peaks); PSR gate; continuity rule; lock state
  machine with a fake clock (every transition); confidence; status dict shape; sink-suspend probe
  parsers and the observation rule.
- **Simulation with recorded captures** (`tests/fixtures/a2dp_air`):
  - `eneby_clicks.btsnoop.gz` (real, 6 s, clicks, mid-stream start): parser → tracker → batches
    must reproduce the phase tool's click phase within 0.5 ms when folded (same method), and the
    correlator against the rendered click track (written side synthesised with a known schedule)
    must lock with a lag consistent with that phase.
  - A new deterministic **synthetic-music capture** (`air_music.btsnoop.gz`, built by
    `build_air_music_fixture.py` with libsbc 1.3.1 like the existing libsbc fixtures; pseudo-music
    from a seeded generator — percussive transients, notes, a quiet passage, a repetitive loop — no
    copyrighted material): SBC 44.1 kHz, 8 sub-bands, 16 blocks, 7 frames per packet, ACL
    fragments and NOCP with seeded jitter, a Connection Complete at the start, an RTP restart (AVDTP
    suspend) at 14 s and a 20 ms "Skipping" drop at 9 s. The test regenerates the PCM, writes it
    through a simulated engine schedule with a known `H(t)` (a cold-start offset of +37 ms and a
    steady jump of −41.5 ms at 8 s), and asserts: lock within 5 s of each epoch start; reported `H`
    within ±2 ms of the truth in `locked`; the jump appears within 3 s; the drop re-acquires
    without a false value; the quiet passage goes `holding`, not wrong; the loop does not produce a
    confirmed wrong lag; the noise-only control (air envelope from another seed) never confirms.
- **Daemon and parent integration (fakes):** a fake hub feeds packets; the parent sends batches
  only to a daemon that advertised `accepts`; the daemon status carries `air_sensor`; the API model
  serialises it; config keys round-trip and classify.
- **Stand validation with a microphone** (Task A17; HAOS stand, ENEBY20 on hci1, the local
  reference bridge in `off`, headset microphone, the coherent click estimator, the gate
  experiment's protocol): `PLAYOUT_SYNC=measure`, `EXPERIMENTAL_AIR_SENSOR=true`. For each of ≥ 8
  cold starts `i`, the mic-derived ENEBY offset `M_i` (ENEBY − reference) and the air-derived host
  placement `H_i` over the same solo. **Acceptance: `max_i |(M_i − H_i) − median(M − H)| ≤ 5 ms`**
  (the air-derived placement explains the start-to-start variance within ±5 ms), with the sensor
  `locked` for ≥ 80 % of the solo time on music and on the click track. Also recorded: `L_as` from
  the wizard vs `median(M − H)` (should agree within the reference's own error), PSR distributions
  of confirmed and rejected windows (to recalibrate R-9), CPU on the Pi 4 (budget above), and one
  run with `pactl suspend-sink` in the gaps to see the suspend guidance fire.

## Rollout

- Stage A ships with `EXPERIMENTAL_AIR_SENSOR=false` on the beta channel; documented as
  experimental (Settings help, `bluetooth-speakers.md`, `group-sync-tuning.md`, en/ru).
- The sink-suspend check ships independently (Task A1), on by default (it only reads).
- After Task A17 passes, the decision note is appended here and stage B is designed in detail.
- Stable (2.x) is not affected.

## Stage B (outline)

- The engine takes the air error as its sensor when the air sensor is `locked` with confidence
  ≥ 0.5, else stays on the pulsesink sensor. Because the air estimate arrives ≈ 4 s late (PA
  latency + batching + the 3 s window) and is noisier per window than the pulsesink reading, the
  pulsesink sensor stays the fast inner loop and the air sensor estimates its **bias** `b = e_pulse
  − E` with a slow filter (time constant ≥ 30 s); the controller acts on `e_pulse − b`. A
  persistent `|b|` above the re-derived dead band triggers a placement jump (R14 persistence rules
  apply).
- With a calibration, the player's output delay is set to `L_as` (via the Stage 4 offset path) so
  `E = H` and the loop's target is `H = 0`.
- The dead band is re-derived from the measured σ of `H` (Task A17), not kept at 0.1 ms / 20 ms.
- `loop` mode joins the effective rule; the playout spec's Stage 3/4 tasks are rewritten on top.

## Risks

- **The comb envelope correlates worse than the band-pass of the morning experiment.**
  Mitigation: R-7's measured switch.
- **`SO_RXQ_OVFL` not delivered on the monitor channel.** Mitigation: large receive buffer,
  consistency checks, `monitor_drops: null` stated honestly.
- **The CPU budget on a Pi 4 with several speakers.** Mitigation: CRC sampling (R-4), the CPU guard,
  the bench script; the feature is opt-in.
- **Repetitive music locks on a neighbour period.** Mitigation: PSR gate, continuity rule, tracking
  range ±58 ms (smaller than most musical periods); simulation case.
- **A bad calibration reference.** Mitigation: R-14's requirement, the UI note, the stored
  `reference` name, Forget.
- **Speaker-side variance after AVDTP restarts.** Not observable; the suspend check removes its
  usual cause.

## Open questions

1. Does `hci_sock_recvmsg` deliver `SO_RXQ_OVFL` (and `SO_TIMESTAMPNS`) for `HCI_CHANNEL_MONITOR`
   on the stand kernels? (Task A3 checks on a dev machine first; affects only drop reporting.)
2. Calibration without a reference member: is a relative calibration between two bridge speakers
   (both with air sensors) worth offering? (`L_as(A) − L_as(B) = (φ_A − φ_B) − (H_A − H_B)`.)
3. Completion anchoring later: the monitor runs from bridge start, so a speaker that connects
   afterwards is proven in step from its Connection Complete; is the completion anchor less noisy
   than the send anchor on the stand, and worth a re-calibration in stage B?
4. Several speakers on one adapter: does controller scheduling make one link's send times lag
   another's enough to matter? (One link per adapter on the stand today.)
5. PipeWire SBC-XQ / high bitpool dual channel: same frame structure (supported by the parser);
   confirm on the Docker VM.

## Rulings

| # | Ruling | Why |
|---|---|---|
| R-1 | The monitor reader runs on a dedicated thread | keeps parse cost off the bridge loop; timestamps come from the kernel |
| R-2 | Handle map keyed by `(index, handle)`; adapter MAC from `NEW_INDEX` | two adapters may reuse handles; the calibration key needs the adapter |
| R-3 | Fallback to RAW receive time when no kernel timestamp | keeps the sensor usable; flagged |
| R-4 | Live path checks the SBC CRC on the first frame of each packet only | CPU on a Pi 4 |
| R-5 | Canonical air time = send anchor; completion is telemetry | always available, survives the parser's blind spots, one anchor for calibration and runtime |
| R-6 | No IPC protocol bump; capability-gated additive commands with a payload version | older daemons never see the verbs |
| R-7 | PCM envelope = comb high-pass peak (switch to band-pass if PSR is ≥ 0.05 worse) | CPU |
| R-8 | Written envelope taken before volume/mute | correlation is volume-invariant; muted = no lock |
| R-9 | PSR gate 1.17, ρ ≥ 0.25, a code constant, recalibrated from data | between today's noise and good windows |
| R-10 | No lock after N = 20 s without a confirmed window | quiet passages vs stale values |
| R-11 | Confidence formula (PSR margin × confirmed share × hold decay) | one number for UI and stage B gating |
| R-12 | Calibration keyed by codec × adapter MAC, ≤ 8 entries | the latency depends on both; mirrors the planned speaker offsets |
| R-13 | Suspend guidance on an observed suspend or a static policy < 60 s; clears after 24 h clean | behaviour is authoritative, the probe explains |
| R-14 | Calibration needs a reference member on the schedule | a phone recording has no clock link to the bridge |
| R-15 | Search ranges: acquisition ±250 ms, tracking ±20 cells; continuity: a > 3-cell move needs two agreeing windows | covers the largest observed spread; robust to wrong peaks |
| R-16 | Window 3 s, hop 1 s; validity ≥ 90 % air present, ≥ 80 % written program | the morning experiment's window; rejects gaps and silence |
