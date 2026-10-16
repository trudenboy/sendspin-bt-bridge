# Bluetooth speaker sync drift: survey, root causes, options

Date: 2026-10-16 · Scope: issue [#546](https://github.com/trudenboy/sendspin-bt-bridge/issues/546) and related reports ·
Code base: `feat/group-sync-tuning` (3.1.0-beta.4 line, aiosendspin 10.0.2, GStreamer player).

## TL;DR

1. **The bridge has no millisecond-level sync loop.** Since 2.76, playback goes through
   `appsrc ! audioconvert ! audioresample ! pulsesink sync=true slave-method=skew`
   (`services/audio/player/`). Each chunk gets a PTS from the Sendspin time filter, but
   GStreamer's `GstAudioBaseSink` writes samples back to back. It only corrects when the
   error goes past its defaults: **±20 ms** for skew slaving (`drift-tolerance`=40 ms, halved)
   and **40 ms** for re-alignment (`alignment-threshold`, held for `discont-wait`=1 s).
   The Sendspin spec requires **±1 ms** steady-state accuracy and ≤0.5 % speed correction.
   Before 2.76, the sendspin `AudioPlayer` ran a real insert/drop loop (the #113 log lines
   show `inserted/dropped`). The GStreamer migration removed that loop.
2. **The clock domains don't match.** Sendspin time (server and client) is `CLOCK_MONOTONIC_RAW`.
   GStreamer's pipeline clock (`GstSystemClock`) and the PulseAudio A2DP sink's pacing
   (`pa_rtclock_now`) are `CLOCK_MONOTONIC`, which NTP slews. The skew slaving matches
   PulseAudio to `CLOCK_MONOTONIC` and never to the Sendspin timeline. Any rate difference
   (NTP slew up to ~500 ppm; host crystal vs server when MA runs on another machine)
   builds up uncorrected inside the ±20/40 ms window.
3. **The measured slope (−1 … −3.5 ms/s, which is 1000–3500 ppm) is too steep to be
   a crystal or NTP effect.** Those stay within ±0.5 ms/s. The slope fits *discrete
   catch-up events* (PulseAudio's bluez5 sink dropping audio when the socket stalls,
   GStreamer skew or realign steps) or *the speaker's own jitter buffer draining after the
   stream starts*. Every #546 measurement was taken right after a fresh stream (the
   announcement), with a pipeline that had been torn down and rebuilt. That is the
   worst time to measure.
4. **The host adds a constant offset that it never reports.** In a live pipeline,
   `GstAudioBaseSink` renders at `PTS + pipeline latency`, and pulsesink reports
   latency ≈ its whole ring buffer (`tlength` ≈ `buffer-time` = `PULSE_LATENCY_MSEC`,
   600–800 ms). The bridge doesn't subtract it, so `static_delay_ms` has to absorb the
   PulseAudio buffer as well as the speaker. This explains why changing
   `pulse_latency_msec` shifts sync. Verify it with `backend_output_latency_ms`.
5. **PulseAudio (HAOS) ignores A2DP delay reports.** Its bluez5 sink reports a fixed
   25 ms + 1 block. The speaker's real buffer (often 100–300+ ms) is invisible to it.
   PipeWire does read `MediaTransport1.Delay`, but only for speakers that send it
   (about 1 in 4 cheap devices).
6. `playback_sync_error_ms` doesn't measure a sync error. It records `clock_now − PTS` when
   a buffer *enters* the sink pad, which is roughly minus the ring-buffer fill. The
   numbers we have today can't separate the hypotheses below.

Top three actions: (1) instrument and measure correctly (cheap, decisive),
(2) give the daemon its own spec-style correction loop in the RAW time domain and remove the
uncounted pipeline latency, (3) keep the A2DP stream warm and measure or tune only in
steady state, then close the loop with the click estimator for the speaker-side part.

---

## 1. Issue survey

### 1.1 This repository

| # | State | Symptom | Environment | Tried | Outcome / takeaway |
|---|---|---|---|---|---|
| [#113](https://github.com/trudenboy/sendspin-bt-bridge/issues/113) | closed | DEBUG `Sync error: -1.3 ms … inserted: 0, dropped: 0` | Docker, Ubuntu 24.04, PipeWire, Intel 7265, 2.50.4 | — | Working as designed. Shows that the pre-2.76 player had an insert/drop loop and held about ±1.4 ms host-side. |
| [#161](https://github.com/trudenboy/sendspin-bt-bridge/issues/161) + [discussion #140](https://github.com/trudenboy/sendspin-bt-bridge/discussions/140) | closed | Sync changes after every track skip; two BT speakers end up seconds apart; "350 ms → ahead, 400 ms → way behind" | Docker, Debian 12, pipewire-pulse (`bluez_output.*.1`), 2.59–2.61, aiosendspin 5.1, MA 2.8.5 → 2.9b8 | SBC, pulse latency 100/600/1000, static delays 0–1000, MA beta, aiosendspin 5.1.1 (drift fixes [#217](https://github.com/Sendspin/aiosendspin/pull/217)/[#219](https://github.com/Sendspin/aiosendspin/pull/219)) | Logs showed a ~500 ms "Sync error … reanchor" at every start. Cutting pulse latency 600→100 removed the 500 ms. 1000 ms caused crackle. The user got sync "mostly" with per-speaker 550/300 ms, but adding or removing a member, or skipping, broke it again. **Takeaway:** the PA buffer size goes straight into the offset, and every stream start re-anchors to a different point. |
| [#212](https://github.com/trudenboy/sendspin-bt-bridge/issues/212) | closed | Stutter and D-Bus freezes | RPi 5, USB dongle, 2.4 GHz Wi-Fi | 5 GHz Wi-Fi | Fixed by moving Wi-Fi to 5 GHz. **Takeaway:** radio coexistence → socket stalls → PA bluez5 drops audio ("Skipping") → timing jumps. |
| [#237](https://github.com/trudenboy/sendspin-bt-bridge/issues/237) | closed | BT always late vs Sonos/pCP; request for negative delay | HA add-on, JBL Charge 4 | — | Negative delay isn't possible (spec). `output_delay_ms` pulls the BT speaker earlier, as #284 later clarified. |
| [#284](https://github.com/trudenboy/sendspin-bt-bridge/issues/284) | open | ~1 s behind other Sendspin devices; 0.5–1.5 s desync after an **announcement** or stream restart | HAOS RPi, PA 17, BlueZ 5.82, Soundcore Select 2S, 2.69 | pulse latency 300/800, delay 0, MA beta slider | `static_delay_ms` (polarity fixed in #294) resolves the steady offset for one user. Announcements and stream restarts still desync the BT speaker only. **This is the same trigger as the #546 measurement.** |
| [#291](https://github.com/trudenboy/sendspin-bt-bridge/issues/291) / [#339](https://github.com/trudenboy/sendspin-bt-bridge/issues/339) | closed/open | Second speaker on one adapter distorted; playback stops after seconds | Proxmox LXC, PA 16.1, BlueZ 5.72 | SBC, pulse 1000, second adapter | Link and BlueZ problems. Relevant only because link stalls feed PA's drop logic. |
| [#323](https://github.com/trudenboy/sendspin-bt-bridge/issues/323) | open (needs info) | Initial sync good, then speed or pitch drops; desync "within seconds"; `Sync error 1257 ms … reanchor` | HAOS 17.3 (ThinkPad, AX200), PA, Audioengine A5+, Creative Pebble Pro, Marshall, 2.71.1, MA 2.8.7 | delays on both sides, SQ vs lossless | Pre-GStreamer. The audible pitch fits the old player's correction loop, or PA rate effects. A retest on ≥2.76 was requested; the current engine corrects differently (steps, not rate). |
| [#341](https://github.com/trudenboy/sendspin-bt-bridge/issues/341) | open (needs info) | Crackle mainly in the **first seconds after a fresh stream start**. The sink monitor and a null sink both contain it. "Monitor recording drifts by ~143 samples (3.24 ms) over a few seconds, then stabilises" | Docker Debian, PipeWire 1.4.2, LDAC/aptX HD, 2.68–2.71.2 | pulse 1000, codecs, raw vs flac, null sink | Host-side startup transient (pre-GStreamer). A natural track transition is clean. Same "fresh stream = bad timing" pattern as #284 and #546. |
| [#464](https://github.com/trudenboy/sendspin-bt-bridge/issues/464) | closed | Pause/Stop lags 5–30 s | LXC, PA, 2.76.0-rc.3 | patches | Fixed in 2.76.0: the GStreamer pipeline is now **torn down on `stream/end`**, so every new stream (an announcement included) rebuilds the PA stream from scratch. A side note measured 562 ms of sink-input "Buffer Latency" at `PULSE_LATENCY_MSEC=600`. |
| [#526](https://github.com/trudenboy/sendspin-bt-bridge/issues/526) | closed | Bit-perfect not followed | HAOS PA | — | PA resamples to the BT sink rate. Not about sync, but it confirms that `audioresample` + PA resampling sit in the path. |
| [#546](https://github.com/trudenboy/sendspin-bt-bridge/issues/546) | open | ENEBY Portable phase −1 … −3.5 ms/s during a 12 s solo; 230–330 ms behind the kiosks; kiosks ±0.06–0.4 ms | HAOS add-on 3.1.0-beta.4, PA, SBC, MA dev; click track as an **announcement**, phone mic | — | Subject of this report. The phone's clock is not the cause: the same recording shows the kiosks stable. |

### 1.2 Upstream (Sendspin, Music Assistant)

| Link | Relevance |
|---|---|
| [Sendspin spec, Playback Synchronization](https://github.com/Sendspin/spec/blob/main/README.md#playback-synchronization) | **±1 ms floor, ±0.5 ms target, ≤0.5 % speed deviation over 150 ms**. One-shot resyncs only at startup or underrun, and they must be rare. Suggested strategy: per-chunk drop/duplicate of N=1 frame, with a ~100 µs dead band. ASRC is explicitly allowed. Our GStreamer path doesn't meet this. |
| [Sendspin/spec #88](https://github.com/Sendspin/spec/issues/88), [#89](https://github.com/Sendspin/spec/issues/89) | Cross-SDK conformance audit. Late-chunk thresholds differ by SDK (5 ms … 1 s); balloob/maximmaxim345 say anything above ±1 ms is non-conformant. The time-filter library is normative. |
| [Sendspin/spec #78](https://github.com/Sendspin/spec/issues/78) | ESP32 stutters at stream start: the client dumps audio while it syncs. A start-of-stream transient that other clients have too. |
| [Sendspin/spec #55](https://github.com/Sendspin/spec/issues/55) → [time-filter theory](https://github.com/Sendspin/time-filter/blob/main/docs/theory.md) | How the Kalman offset+drift filter works. |
| [music-assistant/support #6419](https://github.com/music-assistant/support/issues/6419) | The Sendspin→AirPlay bridge drops 1.7–3.3 s at start and schedules each player with a different lead. That is an audible fixed offset. Same class as ours: a bridge to a device with hidden output latency. |
| [music-assistant/support #4422](https://github.com/music-assistant/support/issues/4422) | Group drift with mismatched DSP (pre-Sendspin). |
| [MA server #3689](https://github.com/music-assistant/server/pull/3689), [#3840](https://github.com/music-assistant/server/pull/3840) | Per-player Sendspin delay slider; "late joiners misaligned with DSP" fix. |

There is no upstream issue about Bluetooth A2DP drift itself. Other Sendspin players drive a
DAC they can read the clock of, so the problem is specific to bridges.

---

## 2. How the current path works

### 2.1 Data and timing path (3.x)

```
MA (aiosendspin server, RawMonotonicClock)
  └─ chunk(server_ts) ──ws──► daemon subprocess (bridge_daemon.py)
        client.compute_play_time(server_ts)          # time filter → CLOCK_MONOTONIC_RAW µs, minus output_delay
        FLAC→PCM (bridge decoder)
        StreamPlayer.submit(play_time_us, pcm)        # player.py
           pts = gst_clock_now(MONOTONIC) + (play_time_us − raw_now_us(RAW))
        appsrc(is-live, format=TIME) ! audioconvert ! audioresample !
        pulsesink(device=bluez_sink…, sync=true, provide-clock=false,
                  slave-method=skew, buffer-time=PULSE_LATENCY_MSEC)
  PulseAudio 17 (HAOS) module-bluez5-device: timer paced by pa_rtclock_now (MONOTONIC)
  SBC → L2CAP socket → controller → speaker jitter buffer → speaker DAC (own crystal)
```

Key code:

- `src/sendspin_bridge/services/ipc/bridge_daemon.py`: `_on_audio_chunk` (play time and
  submit), `_on_stream_end` (calls `player.stop()`, which tears the pipeline down; added for #464), `DaemonArgs.slave_method="skew"`
  (hard-coded; `daemon_process.py` never sets it), `pulse_latency_msec` from
  the `PULSE_LATENCY_MSEC` env var, `min_buffer_ms`/`required_lead_time_ms` = 250 ms.
- `src/sendspin_bridge/services/audio/player/player.py`: `gst_pts_ns`, `submit`, `metrics`.
  `_attach_sink_probe` computes `playback_sync_error_ms = clock.now − buf.pts` when a buffer
  **arrives** at the sink pad, before rendering. That is roughly minus the time the buffer
  still waits in the ring buffer. **It is not a playout error.**
- `src/sendspin_bridge/services/audio/player/pipeline.py`: element properties. Not set:
  `drift-tolerance`, `alignment-threshold`, `discont-wait`, `latency-time`, `ts-offset`.
- aiosendspin 10.0.2: `clock.py` (`RawMonotonicClock`, used by both client and server),
  `client/time_sync.py` (2-D Kalman, drift-SNR gate), `client.compute_play_time` →
  `compute_client_time(server_ts) − output_delay_us`. **aiosendspin 10 ships no audio
  player.** All sync behaviour after the timestamp is the bridge's.

### 2.2 What GStreamer does with those timestamps

From [`gstaudiobasesink.c`](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-base/gst-libs/gst/audio/gstaudiobasesink.c):

- Defaults: `buffer-time` 200 ms (we set 600–800 ms), `latency-time` 10 ms,
  `slave-method` skew, **`drift-tolerance` 40 ms**, **`alignment-threshold` 40 ms**,
  `discont-wait` 1 s.
- **Latency.** For a live upstream (our `appsrc is-live=true`), the sink reports
  `min_latency = seglatency × segsize`. Since `seglatency` defaults to `segtotal`
  ([gstaudioringbuffer.c](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-base/gst-libs/gst/audio/gstaudioringbuffer.c)),
  that is the whole PA `tlength`, which pulsesink sets from `buffer-time`
  ([pulsesink.c](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-good/ext/pulse/pulsesink.c)).
  In render, `sync_offset = ts_offset − render_delay + latency` and
  `render_start += sync_offset`. **A sample is scheduled to sound at PTS + pipeline latency.**
  The bridge never subtracts it. `backend_output_latency_ms` (from a latency query) should
  show the number.
- **Alignment.** If a buffer's timestamp is within `alignment-threshold` (40 ms) of where
  the previous one ended, the sink ignores the timestamp and writes contiguously. Only
  after the gap stays beyond 40 ms for 1 s does it resync, which is an audible jump.
- **Skew slaving** (`provide-clock=false`, so the pipeline clock = GstSystemClock,
  `CLOCK_MONOTONIC`). The sink compares its ring-buffer clock (PulseAudio's reported
  playback time) with the pipeline clock, keeps a 1/32 moving average, and only when
  `|avg_skew| > drift_tolerance/2` (**20 ms**) shifts the calibration by the whole average
  and forces `next_sample = -1` (a resync). It corrects in steps of 20+ ms, never smoothly.
- `slave-method=resample` doesn't resample audio in `audiobasesink`. The observation code is
  `#if 0`; it only re-calibrates the clock mapping. GStreamer has no built-in ASRC to switch on.

So nothing in the pipeline compares *what the speaker plays* with the *Sendspin schedule*
at better than 20–40 ms. The only reference it tracks is `CLOCK_MONOTONIC`.

### 2.3 What PulseAudio does for an A2DP sink

From [`module-bluez5-device.c`](https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/blob/master/src/modules/bluetooth/module-bluez5-device.c):

- The sink is **paced by the system clock** (`pa_rtclock_now()`, CLOCK_MONOTONIC): it sends
  `time_passed − audio_sent` worth of SBC blocks. The speaker's DAC clock never feeds back.
- **Drop-on-stall:** when more than two blocks are due (the socket accepted data too slowly
  because of radio retries, a congested controller, USB passthrough in a VM, or 2.4 GHz
  coexistence), it discards everything beyond two blocks ("`Skipping %llu us … in audio
  stream`", debug level). It also lowers the SBC bitpool, which changes the block size and
  so the sink's reported fixed latency. Each drop moves later audio **earlier**, the same
  sign as #546.
- Reported sink latency = **25 ms fixed + one block** + the write/read index delta. PA does
  **not** read BlueZ `MediaTransport1.Delay` (AVDTP Delay Report): `bluez5-util.c` has no
  handler for it. The speaker's internal buffer (typically 100–300 ms, sometimes 800+)
  can't be seen from the host on HAOS.
- [BlueZ `org.bluez.MediaTransport`](https://man.archlinux.org/man/org.bluez.MediaTransport.5.en):
  `Delay` (uint16, 1/10 ms) exists when the sink supports delay reporting. PipeWire uses it
  ([MR 436](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/436),
  [MR 460](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/460) adds
  `latencyOffsetNsec` because "only 1 of 4 BT headsets I have reports delay"), with known bugs for
  static delays ([#2902](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/2902)).

### 2.4 The speaker

A2DP has no clock recovery back to the source. The host sends at its own rate, and the
speaker has to absorb its DAC's crystal offset (typically ±20–100 ppm, i.e. ≤0.1 ms/s)
in its jitter buffer. It can resample, drop or insert, or let the buffer drift until a
threshold. Many SoCs also start with a deep buffer and **shrink it after start or after
a burst**. The host can only see any of this through a microphone, or a Delay report
if the device sends one.

---

## 3. Root-cause hypotheses for #546 (ranked)

What we observed: within 12 s after a fresh stream starts, the phase moves earlier
by 1–3.5 ms/s (0.1–0.35 % fast). The rate is not constant (run 2: −3.5 then −1 ms/s). The
start offset varies by ±150 ms between runs (7560…7862 ms). Kiosks in the same recording
stay flat.

| Rank | Hypothesis | Fits | Doesn't fit / bound | Distinguishing signature |
|---|---|---|---|---|
| **H1** | **Start-up transient with speaker-side buffer convergence.** On a fresh A2DP stream (the pipeline was torn down at the previous `stream/end`; PA may have suspended the sink, so the transport is re-acquired), the speaker starts with a deep or variable jitter buffer and drains it toward its target by playing fast or dropping. | Sign (earlier), magnitude (0.1–0.35 % is typical of firmware catch-up), decaying slope, random start offset per run, "only BT, only after announcements" (#284, #341 pattern) | Can't be confirmed without a long recording | Per-click phase regression over ≥120 s from stream start: **exponential decay to a plateau**; plateau the same across runs. Host-side error (H5 metric) stays flat while the acoustic phase moves. |
| **H2** | **Host-side catch-up steps.** PA bluez5 "Skipping" when the L2CAP socket stalls (QEMU USB passthrough of a CSR8510, radio retries, two speakers per adapter), and/or GStreamer skew/realign resyncs while PA's timing smoother is still settling after the pipeline starts. | Sign, irregular slope, worse in VMs; #212, #341 | Each event is a step of ≥2 SBC blocks (PA) or ≥20 ms (GStreamer), which 6 s windows would smear into slopes | Steps in the per-click phase residuals. PA debug log `Skipping … us`. `GST_DEBUG=audiobasesink:5` shows `correct clock skew` / `resync`. QoS or discont counters move. |
| **H3** | **No closed loop to the Sendspin timeline** (structural). Playback runs at `CLOCK_MONOTONIC`/PA pace while the schedule is in `CLOCK_MONOTONIC_RAW` plus the filter. The differences (NTP slew ≤ ~0.5 ms/s; host vs server crystal 0.02–0.1 ms/s when MA is on another machine) build up to 20–40 ms before any step. | Explains a **slow** steady drift and sawtooth resyncs over minutes, and "correction holds only for a while" in general | Too small to explain 1–3.5 ms/s on its own; MA and the bridge share a host in #546, so crystal drift is ~0 there | Compare `CLOCK_MONOTONIC` vs `CLOCK_MONOTONIC_RAW` over 60 s on the host (`adjtimex`/timesyncd frequency). A slow linear drift remains after H1 settles. |
| **H4** | **Constant host offset not accounted for** (`PTS + pipeline latency ≈ PULSE_LATENCY_MSEC`) plus the speaker buffer that PA can't see. | The 230–330 ms (and historical 500 ms / 1 s) offsets; offset follows `pulse_latency_msec` | Offset only, not drift | Change `pulse_latency_msec` 800→400: if the offset moves by ~400 ms, confirmed. Read `backend_output_latency_ms`. |
| H5 | Speaker DAC crystal vs host | Real, permanent | ≤0.1 ms/s, 10–35× below the observation | Shows up only as the residual slope after H1/H2/H3 are removed, over minutes. |
| H6 | Time filter not converged | — | Same host as MA (tiny RTT), connected long before the announcement; the filter isn't reset per stream | `client/time` error estimate in the log; run the same test with MA on another host. |

The "who adjusts the rate?" question in #546 has a clear answer: **nobody adjusts it
at the ms level.** GStreamer steps only past ±20–40 ms and only against `CLOCK_MONOTONIC`.
PA paces with the host clock and drops audio on stalls. The speaker adjusts its own buffer.

### 3.1 Metrics and logs that would tell the hypotheses apart

1. **Fix the host-side error metric.** Once per second, compute
   `host_error = (time the sample at the DAC side of PA was scheduled for) − (now)`.
   Practical form: `pulsesink.query_position(TIME)` gives the running time that PA says is
   audible now (audiobasesink subtracts its device delay). Compare it with
   `pipeline_clock.now − base_time − pipeline_latency` and convert to the RAW timeline via
   the same `raw_now`/`gst_now` pair used in `submit`. Log `host_error_ms`, its slope, the
   number of resyncs, and PA's `pa_stream_get_latency`/sink latency.
2. **GStreamer debug** for one test: `GST_DEBUG=audiobasesink:5,pulse:4,basesink:4` in the daemon env.
   Grep `correct clock skew`, `resync`, `discont`, `alignment`.
3. **PulseAudio debug**: on HAOS run `ha audio logs` after `ha audio options --log-level debug`
   or the equivalent (or `pactl set-log-level 4` with access to the PA socket). Grep `Skipping`,
   `bitpool`, `Suspending`, `Resuming`.
4. **Clock domains**: a 60 s Python script reading `CLOCK_MONOTONIC` and `CLOCK_MONOTONIC_RAW`
   at both ends; slope in ppm. `timedatectl timesync-status` (frequency).
5. **Acoustic**: the click estimator per click (not per 6 s window), linear and
   exponential fit, residual steps. Record (a) 0–120 s after a fresh stream start, and
   (b) 5 min into continuous playback. At the same time, record the BT sink monitor
   (`parec -d bluez_sink.<mac>.a2dp_sink.monitor`). Monitor phase vs acoustic phase
   separates host from speaker.
6. Sink-input `pactl list sink-inputs` (Buffer Latency / Sink Latency) at 1 Hz during the run.

Expected patterns: H1 = host flat, acoustic decays; H2 = host steps, coinciding with the
log lines; H3 = host linear slope equal to the MONO-vs-RAW ppm (plus MA-host ppm); H4 = offset
tracks `pulse_latency_msec`.

---

## 4. How others solve it

| Approach | Principle | Applies to a host feeding an unmodifiable A2DP speaker? | Cost |
|---|---|---|---|
| **A2DP AVDTP Delay Reporting** (A2DP 1.3; BlueZ [`MediaTransport1.Delay`](https://man.archlinux.org/man/org.bluez.MediaTransport.5.en)) | The sink tells the source its presentation delay (1/10 ms) | Partly: only if the speaker implements it (a minority of cheap devices) and the audio server registered the endpoint with delay reporting. PA (HAOS) ignores it; PipeWire uses it ([MR 436](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/436), [MR 460](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/460)). Static value: gives the offset, not drift | Low: read the D-Bus property via `bt_dbus`, show it, and offer it as the starting `output_delay_ms` |
| **LE Audio / Auracast** (BAP presentation delay, CIG/BIG isochronous channels) | Each receiver renders at a common anchor + presentation delay, so the link layer carries the timing ([SIG overview](https://www.bluetooth.com/learn-about-bluetooth/feature-enhancements/le-audio/), [Auracast earbuds guide](https://bluetooth.com/wp-content/uploads/2024/05/2403_Auracast_Earbuds.pdf)) | No for current A2DP speakers. A future option for LE Audio speakers (BlueZ ≥5.7x + PipeWire ISO support; [PipeWire MR 1923](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/1923) rate-matches ISO groups) | High; needs LE Audio hardware on both sides |
| **PipeWire bluez5 rate matching** | A2DP sink node is a DLL follower; adaptive resampler matches the graph driver ([pipewire-devices(7)](https://man.archlinux.org/man/extra/pipewire/pipewire-devices.7.en)) | Host-side only (the speaker DAC is still invisible). Not available on HAOS (PA 17) | Docker/LXC users on PipeWire get it for free; doesn't fix the Sendspin-timeline loop |
| **PulseAudio variable-rate streams** (`PA_STREAM_VARIABLE_RATE` + `pa_stream_update_sample_rate`, as VLC did: [1](https://mailman.videolan.org/pipermail/vlc-commits/2011-July/008134.html), [2](https://mailman.videolan.org/pipermail/vlc-commits/2011-July/008137.html)) | Client nudges its stream rate; PA resamples | Yes, host-side ASRC inside PA. Not exposed by GStreamer `pulsesink`; would need direct libpulse/pulsectl or a custom element. pipewire-pulse may ignore it ([PW #1159](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/1159)) | Medium |
| **Snapcast** ([repo](https://github.com/snapcast/snapcast)) | Server timestamps + client time sync; client corrects with small sample insert/drop against the ALSA/PA reported delay; per-client latency for hidden delay | Same problem as us. BT is known not to be compensable automatically ([Mopidy thread](https://discourse.mopidy.com/t/seeking-advice-on-multi-room-audio-setup-with-mopidy-and-snapcast/6070)); people set a manual latency | — (a model to copy, not a tool) |
| **Squeezelite / LMS** ([`StreamingController::_CheckSync`](https://github.com/LMS-Community/slimserver/blob/public/9.1/Slim/Player/StreamingController.pm)) | **Server-side** monitoring of each player's play point; discrete `skipAhead` / `pauseFor` corrections above `minSyncAdjust`; per-player `playDelay` for hidden output delay | Shows a different design point: a coarse discrete correction plus a static device delay. BT stays manual | — |
| **shairport-sync** ([man page](https://manpages.debian.org/testing/shairport-sync/shairport-sync.7)) / **AirPlay 2** ([nqptp](https://man.archlinux.org/man/extra/nqptp/nqptp.8.en)) | PTP-ish timing; receiver stuffs or drops frames ("basic") or **soxr interpolation**; `drift_tolerance` and `resync_threshold` 50 ms; relies on the **backend's reported delay** ([discussion #1553](https://github.com/mikebrady/shairport-sync/discussions/1553)) | The design closest to what we need (the spec suggests the same thing). Its sync is only as good as the output-delay report, so on BT it has the same hidden-buffer gap | Medium (we'd write the equivalent) |
| **Roon RAAT** ([clock ownership](https://community.roonlabs.com/t/raat-and-clock-ownership/6915)) | Endpoint DAC is the clock; the core models it and pulls. In a group, one DAC is master and the others adapt | Our "DAC" is behind A2DP and unobservable. The clock model only works where position can be read | — |
| **Dante / AES67 / broadcast** ([Dante clocking](https://dev.audinate.com/GA/dante-controller/userguide/webhelp/content/clock_synchronization.htm), [PTP](https://www.thebroadcastbridge.com/content/entry/17819/ptp-keeping-time-in-the-ip-world)) | Every device's media clock is locked by PLL to PTP (or word clock); **ASRC** at any boundary with a foreign clock; fixed, known link latency | The ASRC-at-the-boundary principle applies directly: our boundary is RAW-timeline → PA/BT. PTP-locking the speaker is impossible | ASRC in software: low CPU (soxr/speex) |
| **zita-ajbridge / alsa_in** ([Adriaensen, "Controlling adaptive resampling", LAC 2012](https://kokkinizita.linuxaudio.org/papers/adapt-resamp.pdf), [zita-a2j(1)](https://manpages.ubuntu.com/manpages/xenial/man1/zita-a2j.1.html)) | Measure buffer fill or timestamps (noisy), filter with a DLL / 2nd-order PI loop, drive a variable-ratio resampler; ratio stable to a few ppm | **Exactly the host-side loop we're missing.** Error signal = (PA-reported playout time) − (Sendspin schedule) | Medium: ~200 lines + a resampler with variable ratio |
| **Microphone closed loop** (Apple TV [Wireless Audio Sync](https://support.apple.com/guide/tv/atvb228b7711/tvos), Sonos Trueplay-style, MassDroid) | Play a test signal, measure the acoustic delay, set the offset | **The only way to see the speaker's buffer.** We already have the click estimator. Good for the offset; drift needs repeat measurements | Low–medium (exists; needs steady-state discipline and auto-apply) |

---

## 5. Recommendations (ranked)

### R1. Measure correctly (quick; decides everything else)
- **What:** the instrumentation in §3.1. The most important single change is a real
  `host_playout_error_ms` (1 Hz) from pulsesink position vs schedule in the RAW domain, plus
  resync/skip counters. Repeat the click test with (a) a 2-minute solo right after a fresh
  start and (b) a 2-minute solo after 5 min of continuous playback, plus a simultaneous
  monitor recording and PA/GStreamer debug logs.
- **Effect:** ranks H1–H4 within an hour of testing. No user-visible change.
- **Risk:** none (debug flags are off by default).
- **Verify:** the click estimator gives a phase per click; fit linear + exponential models and
  look at the residuals for steps. The kiosks are the control.
- **Touches:** bridge (`services/audio/player/player.py` metrics, `daemon_process.py` env).
  `playback_sync_error_ms` should be renamed or redefined. Today it reads ≈ −(ring fill).

### R2. Account for the pipeline latency (quick; offset)
- **What:** subtract the pipeline latency from the PTS, or set `ts-offset = −latency` on
  pulsesink after the `LATENCY` message (or simply `pipeline.set_latency(0)` / query and compensate).
  Then `output_delay_ms` only has to cover BT plus the speaker, as the spec intends.
  Also lower the default `buffer-time` (PA `tlength`) from 600–800 ms to ~200–300 ms once R3
  exists. The large buffer was a workaround for the old engine.
- **Effect:** removes a ~600–800 ms constant that silently depends on `pulse_latency_msec`.
  Existing users' tuned `static_delay_ms` would have to drop by the same amount. This needs a
  migration note or an automatic offset.
- **Risk:** medium (it changes every tuned offset). Gate it behind a version and announce it.
- **Verify:** acoustic offset vs `pulse_latency_msec` sweeps (300/600/800) should become flat.
- **Touches:** bridge (`player.py`, `pipeline.py`).

### R3. Keep the stream warm and measure only in steady state (quick; addresses H1)
- **What:** (a) don't tear the GStreamer pipeline and PA stream down on every `stream/end`.
  Drop buffered audio (the #464 requirement) by flushing the appsrc or rebuilding only
  appsrc, and keep pulsesink fed with silence for N seconds or for the whole session. Keep
  the BT sink from suspending (`keep_alive` idle mode already exists). (b) In the
  group-sync-tuning flow, play a 30–60 s warm-up of the click track before soloing a BT
  member, or measure on continuous music rather than a fresh announcement.
- **Effect:** if H1 holds, the drift disappears from measurements and from real
  announcement-into-music transitions (#284 "desync after announcement").
- **Risk:** low–medium (the #464 flush deadlock: avoid flush events and use silence plus a
  fresh appsrc). Slightly more power on battery speakers.
- **Verify:** start-of-stream acoustic slope with and without warm-up. The plateau value
  becomes repeatable across runs (±5 ms instead of ±150 ms).
- **Touches:** bridge (`bridge_daemon._on_stream_end`, player), sync-tuning planner.

### R4. A spec-compliant correction loop in the daemon (larger; addresses H2/H3, host side)
- **What:** stop relying on audiobasesink slaving. Set `drift-tolerance` and
  `alignment-threshold` large so GStreamer never steps by itself. Then, every ~100 ms,
  compute the host playout error in the **RAW / time-filter domain** (R1 metric) and run a
  PI/DLL loop (Adriaensen-style) that drives either
  - **sample drop/insert** per the spec's suggested strategy (N=1 frame per chunk, dead band
    ~100 µs, ≤0.5 %), done in Python on the PCM before `appsrc`; simple and bit-exact
    elsewhere. Or
  - **variable-ratio resampling** (soxr/speex with a settable ratio, or libpulse
    `pa_stream_update_sample_rate`), which is smoother for music.
  A one-shot snap remains for errors over a few ms at startup or after an underrun (spec "Large errors").
- **Effect:** host-side error held to about ±1 ms against the Sendspin timeline, whatever the
  NTP slew, host crystal, PA drops or GStreamer quirks. Meets the spec floor up to the PA
  output. Doesn't fix the speaker's own buffer (H1/H5).
- **Risk:** medium. Feedback stability with PA's noisy timing (filter it; the
  [Adriaensen paper](https://kokkinizita.linuxaudio.org/papers/adapt-resamp.pdf) gives a
  tested loop), CPU on RPi (drop/insert is negligible), and test effort.
- **Verify:** `host_playout_error_ms` stays within ±1 ms over 30 min. The acoustic slope after
  warm-up drops to the speaker's residual (≤0.1 ms/s). The MONO-vs-RAW slew test no longer
  shows in the acoustic data.
- **Touches:** bridge (`services/audio/player/`, `bridge_daemon`). Could be offered upstream as
  a reusable "Sendspin player correction" helper for aiosendspin users that drive PA/PipeWire.

### R5. Close the loop on the speaker with what we can see (medium)
- **What:** (a) read `MediaTransport1.Delay` via D-Bus where present and use it as the
  default `output_delay_ms` (and show it in the UI). This works on PipeWire hosts with
  delay-reporting speakers. (b) Use the click estimator as an **auto-calibration**: after
  warm-up, measure the steady-state offset and write `sendspin_static_delay` (the
  sync-tuning flow already does this). Re-measure on demand or on a schedule to catch the
  slow speaker drift (H5). (c) Optionally a slow outer loop that adjusts the drop/insert
  target from periodic mic measurements, if users accept a mic in the room.
- **Effect:** a correct steady offset per speaker. The remaining drift is the DAC crystal
  (≤0.1 ms/s ≈ 6 ms/min), which a periodic re-measure or an outer loop can trim.
- **Risk:** low for (a)/(b); (c) is a product question (privacy, mic availability).
- **Verify:** repeated measurements over hours; spread of the steady offset.
- **Touches:** bridge (`bluetooth/dbus`, sync_tuning), MA config key.

### R6. Platform options (situational)
- Recommend PipeWire hosts (Docker/LXC) for better BT timing (rate matching, delay reports).
  Not available on HAOS.
- Avoid conditions that trigger PA drops: one speaker per adapter, 5 GHz Wi-Fi, avoid USB BT
  passthrough into QEMU if possible (the production HAOS VM uses CSR8510 passthrough). These
  affect H2 directly.
- LE Audio (presentation delay) is the real long-term fix for hardware that supports it. Track
  BlueZ/PipeWire LE Audio maturity; it isn't reachable on PA/HAOS.

### Not recommended
- Tightening `drift-tolerance`/`alignment-threshold` alone. Every correction in audiobasesink
  is a resync (`next_sample = -1`), so a 1–2 ms tolerance would produce frequent audible
  discontinuities.
- `slave-method=resample`. In audiobasesink it doesn't resample.
- Ever-larger `pulse_latency_msec`. With R2 missing it only shifts the offset; it doesn't
  improve sync (#161 history).

---

## 6. Open questions for the next test session
1. What were `static_delay_ms`, `pulse_latency_msec` and `backend_output_latency_ms` for the
   ENEBY Portable in the #546 runs? (They're needed to split the 230–330 ms between H4 and the speaker.)
2. Did the PA BT sink suspend between announcements (`pactl list sinks` state)?
3. Does the slope decay to zero within 60–120 s (H1), or persist (H2/H3)?
4. MONO vs RAW frequency on the HAOS VM (kvm-clock + timesyncd).

## Sources
- Issues: [#113](https://github.com/trudenboy/sendspin-bt-bridge/issues/113), [#161](https://github.com/trudenboy/sendspin-bt-bridge/issues/161), [disc. #140](https://github.com/trudenboy/sendspin-bt-bridge/discussions/140), [#212](https://github.com/trudenboy/sendspin-bt-bridge/issues/212), [#237](https://github.com/trudenboy/sendspin-bt-bridge/issues/237), [#284](https://github.com/trudenboy/sendspin-bt-bridge/issues/284), [#291](https://github.com/trudenboy/sendspin-bt-bridge/issues/291), [#323](https://github.com/trudenboy/sendspin-bt-bridge/issues/323), [#339](https://github.com/trudenboy/sendspin-bt-bridge/issues/339), [#341](https://github.com/trudenboy/sendspin-bt-bridge/issues/341), [#464](https://github.com/trudenboy/sendspin-bt-bridge/issues/464), [#526](https://github.com/trudenboy/sendspin-bt-bridge/issues/526), [#546](https://github.com/trudenboy/sendspin-bt-bridge/issues/546)
- Upstream: [Sendspin spec](https://github.com/Sendspin/spec/blob/main/README.md), [spec #55](https://github.com/Sendspin/spec/issues/55), [#78](https://github.com/Sendspin/spec/issues/78), [#88](https://github.com/Sendspin/spec/issues/88), [#89](https://github.com/Sendspin/spec/issues/89), [time-filter theory](https://github.com/Sendspin/time-filter/blob/main/docs/theory.md), [aiosendspin #217](https://github.com/Sendspin/aiosendspin/pull/217), [#219](https://github.com/Sendspin/aiosendspin/pull/219), [MA support #6419](https://github.com/music-assistant/support/issues/6419), [#4422](https://github.com/music-assistant/support/issues/4422), [MA server #3689](https://github.com/music-assistant/server/pull/3689), [#3840](https://github.com/music-assistant/server/pull/3840)
- GStreamer: [gstaudiobasesink.c](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-base/gst-libs/gst/audio/gstaudiobasesink.c), [gstaudioringbuffer.c](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-base/gst-libs/gst/audio/gstaudioringbuffer.c), [pulsesink.c](https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-good/ext/pulse/pulsesink.c)
- PulseAudio: [module-bluez5-device.c](https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/blob/master/src/modules/bluetooth/module-bluez5-device.c), [bluez5-util.c](https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/blob/master/src/modules/bluetooth/bluez5-util.c), [issue 799](https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/issues/799)
- BlueZ / PipeWire: [MediaTransport(5)](https://man.archlinux.org/man/org.bluez.MediaTransport.5.en), [PW MR 436](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/436), [PW MR 460](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/460), [PW #2902](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/2902), [PW #1159](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/1159), [PW MR 1923](https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/1923), [pipewire-devices(7)](https://man.archlinux.org/man/extra/pipewire/pipewire-devices.7.en)
- Others: [Snapcast](https://github.com/snapcast/snapcast), [Mopidy forum on BT + Snapcast](https://discourse.mopidy.com/t/seeking-advice-on-multi-room-audio-setup-with-mopidy-and-snapcast/6070), [LMS StreamingController](https://github.com/LMS-Community/slimserver/blob/public/9.1/Slim/Player/StreamingController.pm), [shairport-sync(7)](https://manpages.debian.org/testing/shairport-sync/shairport-sync.7), [shairport-sync #1553](https://github.com/mikebrady/shairport-sync/discussions/1553), [nqptp(8)](https://man.archlinux.org/man/extra/nqptp/nqptp.8.en), [Roon RAAT clock ownership](https://community.roonlabs.com/t/raat-and-clock-ownership/6915), [Dante clock sync](https://dev.audinate.com/GA/dante-controller/userguide/webhelp/content/clock_synchronization.htm), [PTP overview](https://www.thebroadcastbridge.com/content/entry/17819/ptp-keeping-time-in-the-ip-world), [Adriaensen, adaptive resampling](https://kokkinizita.linuxaudio.org/papers/adapt-resamp.pdf), [zita-a2j(1)](https://manpages.ubuntu.com/manpages/xenial/man1/zita-a2j.1.html), [VLC PA resampling](https://mailman.videolan.org/pipermail/vlc-commits/2011-July/008137.html), [Apple TV Wireless Audio Sync](https://support.apple.com/guide/tv/atvb228b7711/tvos), [Bluetooth LE Audio](https://www.bluetooth.com/learn-about-bluetooth/feature-enhancements/le-audio/), [Auracast earbuds guide](https://bluetooth.com/wp-content/uploads/2024/05/2403_Auracast_Earbuds.pdf)
