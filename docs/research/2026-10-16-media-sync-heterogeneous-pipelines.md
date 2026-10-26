# Synchronising media across heterogeneous playback pipelines: theory, practice, and a plan for the bridge

Date: 2026-10-16 · Builds on: [`2026-10-16-bt-sync-drift.md`](2026-10-16-bt-sync-drift.md) (the
"drift report") and [issue #546](https://github.com/trudenboy/sendspin-bt-bridge/issues/546) ·
Code base: `feat/group-sync-tuning` (v3 line, aiosendspin 10.0.2, GStreamer player).

The drift report covers the GStreamer/PulseAudio mechanics and ranks root causes H1–H6 for #546.
It is not repeated here. This report puts those findings into the general theory of
multi-destination synchronisation, compares what other systems do, and turns the result into an
applicability matrix and a phased architecture.

---

## Summary

**The problem, decomposed.** A sample's audible time at a group member is

```
t_audible = schedule(ts)                 # Sendspin timeline -> local RAW clock (time filter, µs-level)
          + e_host                       # host playout error vs. that schedule (our loop: none today)
          + L_host                       # GStreamer ring + PA buffer, never subtracted today (≈ PULSE_LATENCY_MSEC)
          + L_link(t)                    # SBC/AAC framing + L2CAP + controller queue (+ drops on stalls)
          + L_speaker(t)                 # speaker jitter buffer: unknown, varies per session and during start-up
          + L_acoustic                   # DAC/amp/air: constant
          − output_delay
```

The Web Kiosks hold ±0.3 ms because every term except `L_acoustic` is either measured by the
browser (`AudioContext.getOutputTimestamp()`, `outputLatency`) or closed-loop corrected on the
device's own playout clock. The bridge measures and corrects none of `e_host`, `L_host`,
`L_link` or `L_speaker`.

**Key insights from theory and practice.**

1. **Every native Sendspin client closes the loop on the device's playout clock. The bridge
   doesn't.** sendspin-js reads the playout clock and corrects with crossfaded sample
   insert/drop, ±0.3/0.5 % `playbackRate` tiers and a resync above 200 ms. sendspin-cpp feeds
   `notify_audio_played()` back into a soft- and hard-sync state machine. SendspinKit spells the
   invariant out as `error = (expected + L) − cursor`, with `L` read from the HAL. The spec's
   accuracy floor of ±1 ms is defined *at the audio output*.
2. **A2DP is a one-way, source-paced link.** The speaker can't push its clock back, so its
   firmware has to make its DAC follow the host's sending rate over the long term (by buffer-fill
   control, sample slip or ASRC). Otherwise its buffer would overflow or run dry. It follows that
   **the long-term acoustic rate of a BT speaker equals the host's send rate.** Lock the host send
   rate to the Sendspin timeline and the long-term drift is gone. What's left is (a) the
   speaker's *buffer-target* offset, which is constant within a warm session but differs between
   sessions, and (b) its start-up convergence and control jitter. Both are invisible to the host
   and can only be seen acoustically or through an AVDTP Delay Report. This strongly supports H1
   (start-up convergence) for the 1–3.5 ms/s slopes in #546. A crystal can't produce 1000–3500 ppm
   in steady state; a buffer draining toward its target after start can.
3. **Industry practice for "endpoint with unknown output latency" is to (i) exclude it, (ii) offer
   a manual slider, or (iii) calibrate it acoustically. Nobody runs a continuous closed loop
   through the air.** Alexa multi-room excludes Bluetooth outputs. Google Cast has a manual "Group
   delay correction" slider. WiiM has Auto Sync using an on-device microphone, plus a manual
   offset. Apple TV has Wireless Audio Sync with an iPhone microphone. Squeezelite/LMS use a
   per-player `playDelay`. Snapcast uses a per-client `latency`. Professional systems (Dante,
   AES67, AVB) avoid the problem: they fix and know the link latency, and every media clock is
   PLL-locked to PTP/gPTP. Note that Sonos Trueplay is room EQ, not latency calibration. Sonos
   controls its own hardware latency.
4. **The textbook toolkit for our boundary** (a foreign, noisy clock domain behind a buffer) is
   *ASRC driven by a filtered error signal*. That means a DLL or second-order PI loop on noisy
   timestamps (Adriaensen), as in zita-ajbridge/alsa_in, JACK, PipeWire's adaptive resampler and
   MPEG-TS PCR recovery. Sendspin also allows the cheaper version: discrete 1-frame insert/drop
   with a ~100 µs dead band.
5. **Sendspin is a "global presentation timeline" scheme**, like AVB presentation time, AES67
   link offset and MPEG-TS PTS. Under RFC 7272 IDMS terms it needs no feedback from receivers to
   reach sync, but in exchange **no one can check that a receiver complies.** The protocol has no
   IDMS-style report (RTCP XR "packet presented at"), so MA can't see that the bridge is 300 ms off.
   That is the main thing to propose upstream.

**Top recommendations (ranked by value/cost).**

| # | What | Fixes | Cost |
|---|---|---|---|
| 1 | **Keep the A2DP stream warm**: don't tear down the pipeline at `stream/end`; feed silence and keep the sink unsuspended. Measure only after ≥30–60 s of warm playback. | Start-up transient (H1), random per-run offset, #284/#341 "desync after announcement" | Low |
| 2 | **Host playout loop in the RAW domain**: set `slave-method=none` and large `alignment-threshold`. Get the playout position from pulsesink/PA timing, compute `e_host` against the Sendspin schedule, and correct with spec-style 1-frame insert/drop (crossfaded) plus a one-shot snap at start or for large errors. This subsumes "subtract `L_host`". | `e_host` drift (NTP slew, PA drops, GStreamer steps), the `pulse_latency_msec` dependence, delay-change responsiveness | Medium |
| 3 | **Speaker latency as `output_delay`**, from (a) AVDTP Delay Report (`MediaTransport1.Delay`) when present, otherwise (b) the phone click estimator in warm steady state. Persist per speaker × codec × adapter. | Static offset (230–330 ms) | Low (both exist in part) |
| 4 | **Telemetry**: log and export `host_playout_error_ms`, correction ppm, snap count, `L_host`, Delay Report and calibration age. Show a sync health badge. | Diagnosis and regression detection | Low |
| 5 | **ASRC upgrade** (libsamplerate variable ratio, PI/DLL-controlled), only if insert/drop is audible at the sustained correction rates seen on BT hosts. | Correction transparency | Medium |

**Upstream.** Sendspin spec: optional client-reported sync telemetry, à la RFC 7272
(`sync_error_us`, `correction_ppm`, `resyncs`, `self_compensated_latency_ms`,
`output_delay_source`); sub-ms output delay; a clarification of where "the port" is for bridging
clients. aiosendspin: a reusable backend-agnostic playout corrector (estimator + insert/drop/ASRC
controller), so Python players stop re-implementing it. MA: keep the stream continuous across
announcements for grouped players; an acoustic calibration flow and a sync health view for
players that report telemetry.

---

## 1. Theory

### 1.1 Clock domains

A *clock domain* is a set of components whose sample or tick rate comes from the same
oscillator. Every boundary between domains needs either a rate converter (ASRC, sample slip) or
a feedback path that lets one side discipline the other (PLL/DLL, flow control). In our chain:

| # | Domain | Who sets the rate | Observable by the bridge? |
|---|---|---|---|
| D1 | MA server, `CLOCK_MONOTONIC_RAW` (aiosendspin `RawMonotonicClock`) | server crystal | via the time filter (µs) |
| D2 | Bridge `CLOCK_MONOTONIC_RAW` (Sendspin schedule after the filter) | bridge crystal | yes |
| D3 | Bridge `CLOCK_MONOTONIC` (GstSystemClock, PA `pa_rtclock_now`) | D2 slewed by NTP/timesyncd, ≤ ±500 ppm | yes |
| D4 | PA sink consumption: BT sink = D3 timer pacing; ALSA sink = sound card crystal (tsched + smoother) | D3 or the card | yes (PA timing info) |
| D5 | BT controller / piconet native clock (3.2 kHz, 312.5 µs ticks) | controller | transport only; `HCI_Read_Clock` exists but doesn't reveal the speaker's DAC |
| D6 | Speaker DAC | speaker crystal, *slaved by firmware to the D4 send rate over the long term* | **no** (only acoustically or via a Delay Report) |
| D7 | Phone ADC (measurement) | phone crystal | cancels out, because kiosks in the same recording serve as reference |

Kiosk path: D1 → browser clock (filter) → `AudioContext` (DAC crystal), with closed-loop
correction on the DAC's own position. Bridge path: D1 → D2 → D3 → D4 → D5 → D6, with no loop
anywhere. GStreamer skew-slaves D4 to D3 only past ±20 ms; see the drift report §2.2.

### 1.2 Clock synchronisation between hosts

- **Cristian (1989).** One request/response, `offset = T_server + RTT/2`, error ≤ RTT/2. Take the
  minimum-RTT sample of a burst. Sendspin bursts (sendspin-cpp `SendspinTimeBurst`) do exactly
  this before the filter.
- **Berkeley (Gusella & Zatti 1989).** A master polls the slaves, averages and sends corrections.
  Agreement without an external reference. Conceptually close to an MA "group leader".
- **NTP (RFC 5905).** Four timestamps T1–T4, `θ = ((T2−T1)+(T3−T4))/2`, `δ = (T4−T1)−(T3−T2)`. A
  clock filter keeps the minimum-delay sample of 8, and a hybrid PLL/FLL disciplines the local
  oscillator. ms-level over the Internet, ~0.1 ms on a LAN. **Important for us: NTP slews
  `CLOCK_MONOTONIC` and not `CLOCK_MONOTONIC_RAW`**, which is why D2 and D3 differ.
- **PTP (IEEE 1588-2019).** Hardware timestamps at the PHY, best-master-clock algorithm,
  delay request/response or peer delay, transparent and boundary clocks. Sub-µs. **gPTP (IEEE
  802.1AS-2020)** is the profile for AVB/TSN: every bridge is time-aware and uses peer delay
  measurement; ns-level. AirPlay 2 uses a PTP variant (see [nqptp](https://github.com/mikebrady/nqptp)).
- **Statistical estimators.** Each exchange yields a noisy sample of `offset(t)`. Estimators:
  - *median / minimum filters* (Snapcast `TimeProvider`: median of the recent diff buffer);
  - *linear regression over a window* (GStreamer `gst_clock_add_observation`: slave clock
    calibration from (internal, external) pairs; Roon's "drift model");
  - *Kalman filter* on state `[offset, drift]` (Sendspin
    [time-filter](https://github.com/Sendspin/time-filter/blob/main/docs/theory.md); aiosendspin
    `client/time_sync.py`). Measurement variance comes from `max_error` (half the RTT), drift is
    applied only above an SNR threshold, and residuals far above `adaptive_cutoff` inflate the
    covariance so the filter recovers from steps;
  - *DLL / second-order loop* (Adriaensen,
    ["Using a DLL to filter time", LAC 2005](https://kokkinizita.linuxaudio.org/papers/usingdll.pdf)):
    it smooths the noisy timestamps of periodic events (audio period wakeups) into a smooth
    time ↔ sample-count mapping. Two coefficients (`b = √2·ω`, `c = ω²`, with `ω = 2π·B·T`)
    set the bandwidth `B`. JACK uses it, and so do FFmpeg's `timefilter` and PipeWire's
    `spa/utils/dll.h`.

  These are one family. A Kalman filter with a constant-velocity model, run in steady state,
  reduces to a fixed-gain second-order loop, i.e. a DLL. The Kalman form adapts its gain to the
  measurement quality; the DLL form is cheaper and easier to reason about for a fixed-rate
  sampled signal such as playout position.

**Implication.** D1 → D2 is solved: the Sendspin filter is at the µs level, especially with MA on
the same host. The bridge's problem starts *after* the filter, at D2 → D4 → D6.

### 1.3 Inter-destination media synchronisation (IDMS)

IDMS is the umbrella term for playing one stream in sync at several receivers. The survey by
Montagud et al. (*Multimedia Systems* 18(6), 2012) classifies the schemes:

- **Master/slave (MSRS)**: one receiver's playout point is the reference, and the others adjust
  to it. This is Roon's group clock master.
- **Synchronisation maestro (SMS)**: receivers report their playout points to a server, which
  computes a reference and distributes it. This is **RFC 7272** (RTCP XR IDMS report blocks:
  "packet received at NTP x, presented at NTP y", plus an IDMS settings packet from the MSAS).
- **Distributed control (DCS)**: peers exchange playout points.
- **Global presentation timeline**: the sender stamps each unit with an absolute presentation
  time on a shared clock, and every receiver must render at that time. This is AVB/IEEE 1722
  presentation time, AES67 link offset, MPEG-TS PTS against a recovered STC, AirPlay 2 anchor
  times, Snapcast and **Sendspin**.

The global-timeline scheme scales and needs no feedback, but **correctness depends on every
receiver knowing its own output latency**, and there is no built-in check. RFC 7272's feedback
reports are exactly what would expose a non-compliant receiver. Sendspin has none (§4.4).

### 1.4 Mapping media time to wall clock (RTP family)

- **RFC 3550 (RTP/RTCP).** RTP timestamps run at the media clock rate with a random offset.
  RTCP Sender Reports pair an NTP wall-clock time with the RTP timestamp, so receivers can map
  media time to wall time and lip-sync streams. The interarrival jitter estimator
  `J += (|D| − J)/16` is the classic jitter metric. **RFC 6051** adds rapid synchronisation, so
  receivers don't have to wait for the first SR.
- **RFC 7273 (clock source signalling).** SDP attributes `a=ts-refclk:` (ntp=, ptp=IEEE1588-2008:…,
  gps, local) and `a=mediaclk:direct=<offset>` declare that the RTP timestamp is a *direct
  function of the reference clock*, which removes the SR estimation step. AES67 and
  SMPTE ST 2110 use it.
- Sendspin's binary chunk header is the RFC 7273 "direct" case: the timestamp *is* server time
  in µs, with no separate mapping to learn.

### 1.5 Presentation-time models

- **MPEG-2 TS (ISO/IEC 13818-1).** A 27 MHz System Time Clock. PCR samples are sent at least
  every 100 ms (DVB: 40 ms). The decoder recovers the STC with a PLL (classically a VCXO that
  drives the DAC clock). PTS/DTS run at 90 kHz, and the T-STD buffer model bounds the buffering.
  **The decoder's output clock is rate-locked to the recovered sender clock.** That is the target
  property for us: D4 rate-locked to D2.
- **AES67 / SMPTE ST 2110-30.** Every device's media clock is derived from PTP, so all devices
  sample at the same rate with no drift. RTP timestamps are on the PTP epoch. The receiver plays
  at `RTP_ts + link offset` (a fixed latency, 1–10 ms typical). Devices outside the PTP domain
  meet it through ASRC.
- **AVB/TSN (IEEE 1722 AVTP + 802.1AS + 802.1Qav/Qat).** The talker stamps each packet with
  *presentation time* = gPTP now + max transit time (Class A: 2 ms over 7 hops). Listeners
  recover the media clock from the AVTP timestamps and present exactly then. Latency is
  *guaranteed by reservation*, not measured.
- **Web Audio.** `AudioContext.outputLatency` estimates the delay from the audio graph to the
  output device, including Bluetooth where the OS knows it. `getOutputTimestamp()` returns a
  `{contextTime, performanceTime}` pair for the sample at the output now. That is a direct DAC
  position ↔ wall clock mapping, and it is what the kiosks exploit
  ([W3C Web Audio API](https://www.w3.org/TR/webaudio/)).

### 1.6 Latency buffers vs. jitter buffers

- A **jitter buffer** absorbs arrival-time variance. Its depth is a statistical choice: a
  high quantile of arrival delay. Sendspin `min_buffer_ms` is defined that way (spec, "Measuring
  timing parameters"). WebRTC NetEQ adapts the target depth continuously.
- A **latency (presentation) buffer** holds data until its presentation time. With absolute
  timestamps its *fill* is irrelevant to sync; only its **reported output delay** matters.
- When a buffer sits *downstream* of the scheduling point (PA `tlength`, GStreamer ring, the
  speaker's jitter buffer), its depth adds directly to `t_audible` unless the scheduler subtracts
  it. That is `L_host` and `L_speaker`. The SendspinKit doc states the rule: `L` must appear
  exactly once in the error signal, sourced from the backend
  ([audio-timing-model.md](https://github.com/Sendspin/SendspinKit/blob/main/docs/audio-timing-model.md)).
- Speaker firmware usually starts with a deep buffer (to ride out link start-up) and drains to a
  shallower target. That is a slope of 0.1–0.5 % for seconds to tens of seconds: H1.

### 1.7 Drift compensation methods

| Method | How it works | Artefacts | Precision | Examples |
|---|---|---|---|---|
| Sample slip (drop/insert 1 frame) | Remove or duplicate a frame when the error passes a dead band | A tiny discontinuity at each correction. Masked at low rates (few/s); can click on pure tones. A short crossfade (sendspin-js: 8-sample fade) reduces it | ±1 frame (21 µs at 48 kHz) | Sendspin spec suggestion, sendspin-cpp soft sync, Snapcast (`correctAfterXFrames`: 1 frame every X frames, rate capped at ±0.05 %), shairport-sync "basic" |
| Resync / skip | Jump by many ms | Audible | coarse | GStreamer audiobasesink realign/skew (≥20–40 ms), LMS `skipAhead`/`pauseFor`, PA bluez5 "Skipping" |
| Playback-rate change (resampling) | Scale the rate by (1 ± ε). Pitch shifts by ε (0.5 % ≈ 8.6 cents) | None audible at ε ≤ 0.1 %; still inaudible to most at 0.5 % | sub-frame | sendspin-js `playbackRate` tiers 0.3/0.5 %, mpv `video-sync=display-resample`, Kodi "sync playback to display" |
| ASRC with control loop | Variable-ratio polyphase/sinc resampler. Ratio from a PI/DLL loop on a filtered error (buffer fill or timestamps) | None (quality depends on filter) | ppm-level ratio | [zita-ajbridge / zita-a2j](https://manpages.ubuntu.com/manpages/xenial/man1/zita-a2j.1.html) (Adriaensen, *Controlling adaptive resampling*, LAC 2012), alsa_in/alsa_out, PipeWire adaptive resampler, Dante/AES67 ASRC at domain edges, shairport-sync `soxr` |
| Time-stretch (WSOLA/PSOLA) | Change duration without pitch, segment by segment | Can smear transients | 10s of ms per second | WebRTC NetEQ accelerate/preemptive expand, `scaletempo` |
| Clock discipline (PLL/VCXO) | Steer the DAC oscillator itself | None | best | MPEG-TS decoders, AES67/Dante hardware, Roon "pull" from the master DAC |

Control-loop design notes (Adriaensen 2012; PipeWire `dll.h`):
- The error signal from a sound server is noisy (period quantisation, scheduler jitter). Low-pass
  it with a loop bandwidth of 0.05–0.5 Hz. Too wide follows the noise (audible warble); too
  narrow tracks slowly.
- A second-order (PI) loop removes a constant *frequency* offset with zero steady-state phase
  error. A first-order loop leaves a phase error proportional to the frequency offset.
- Clamp the ratio (Sendspin: ≤ 0.5 % over 150 ms) and use a separate one-shot "snap" for large
  errors and at start (Sendspin "Large errors and startup"; sendspin-cpp `HARD_SYNC_THRESHOLD_US`).
- Never stack two independent loops on the same error (GStreamer skew slaving + ours). Disable
  the inner one.

### 1.8 Measuring output latency on Linux

- **ALSA.** `snd_pcm_delay()` gives frames until a newly written frame is audible.
  `snd_pcm_status()` with `audio_tstamp` (types: compat, default, link, link-absolute,
  link-estimated) gives DAC-side position with a system timestamp, so position ↔ time pairs can
  be fed to a DLL. Hardware latency inside the codec isn't included.
- **PulseAudio.** `pa_stream_get_latency()` and `pa_timing_info` (`sink_usec`, `transport_usec`,
  `read_index`, `write_index`, `timestamp`), interpolated by PA's smoother. For an **A2DP sink**,
  `sink_usec` is a *fixed 25 ms + one block*: PA doesn't consume AVDTP Delay Reports (drift
  report §2.3). The position is therefore accurate *up to the BT socket*, paced by D3.
- **PipeWire.** Each node publishes latency (`Spa:Pod:Object:Param:Latency`, `ProcessLatency`).
  bluez5 nodes add the transport `Delay` when the device reports it, plus a user
  `latencyOffsetNsec` ([pipewire-props(7)](https://docs.pipewire.org/page_man_pipewire-props_7.html)).
  pipewire-pulse folds these into `pa_stream_get_latency`. Followers are rate-matched to the
  driver by the adaptive resampler, and the driver's DLL estimates its rate.
- **GStreamer.** The `LATENCY` query sums element latencies. For a live pipeline, `audiobasesink`
  renders at `PTS + pipeline latency`. The sink's `GstAudioClock` is derived from ring-buffer
  position (PA timing). `query_position(TIME)` on the sink returns the audible position (device
  delay subtracted). `slave-method` decides how the audio clock is matched to the pipeline clock
  (skew = step corrections; resample = only clock recalibration in audiobasesink; none).
  GStreamer also has network clocks (`GstNetClientClock`, `GstPtpClock`), used for multi-device
  playback (Jan Schmidt's [Aurena](https://github.com/thaytan/aurena)). They aren't applicable here,
  because Sendspin requires its own time filter.

### 1.9 Bluetooth specifics

- **A2DP has no clock recovery.** The source sends SBC/AAC frames at its own pace over L2CAP
  (SBC frame = 128 samples ≈ 2.67 ms at 48 kHz, several frames per media packet; AAC frame = 1024
  samples ≈ 21.3 ms plus encoder delay). The sink buffers and has to slave its DAC to the arrival
  rate, or slip. Link retries (2.4 GHz coexistence, a shared adapter, USB passthrough into a VM)
  delay the socket. PA then drops audio to catch up (drift report H2).
- **AVDTP Delay Reporting (A2DP 1.3, AVDTP 1.3).** The sink sends `DelayReport` in 1/10 ms after
  configuration and may update it. BlueZ exposes it as `MediaTransport1.Delay` when the
  endpoint was registered with delay reporting. Support is a minority on cheap speakers
  (PipeWire MR 460: "1 of 4"). macOS, Android and iOS fold it into their output-latency APIs,
  which is why browser `outputLatency` on those platforms can cover BT headphones (sendspin-js:
  "follows the actual output path … Bluetooth").
- **Controller clock vs host clock.** The piconet clock (`HCI_Read_Clock`) times the radio. It
  isn't the DAC clock, and A2DP doesn't carry it to the audio path. Host-side timing of
  `Number_Of_Completed_Packets` events shows when packets left the controller, i.e. link
  latency, not playout.
- **LE Audio (BAP/ISOAL, Core 5.2+).** Isochronous channels (CIS/BIS) have a fixed ISO interval
  anchored to the link-layer clock. Each receiver renders at *SDU synchronisation reference +
  Presentation Delay*, which is negotiated from the receivers' min/max/preferred values in the
  ASE QoS configuration ([BAP](https://www.bluetooth.com/specifications/specs/basic-audio-profile-1-0-1/)).
  `HCI_LE_Read_ISO_TX_Sync` gives the host the TX timestamp, so the host *can* map its schedule
  to the radio clock. This is AVB-style presentation time over Bluetooth, and it solves both
  offset and drift for LE Audio sinks. It needs LE Audio hardware and BlueZ + PipeWire ISO
  support; not PA/HAOS.

---

## 2. Practice in real systems

Focus: what each system does about (a) endpoints with heterogeneous or unknown output latency
and (b) devices that don't report their delay.

| System | Clock sync | Presentation / drift handling | Unknown-latency endpoints | Notes for us |
|---|---|---|---|---|
| **Sonos** | Proprietary. Group coordinator plus timestamps against a shared clock ([US 8,234,395 "synchronizing operations among … independently clocked … devices"](https://patents.google.com/patent/US8234395B2)) | Closed hardware with known DAC latency; rate adjustment on the device | Controls all hardware. TV "audio delay" is a manual lip-sync slider. **Trueplay is room EQ, not timing** | Sync is easy when you own the DAC. Not our case |
| **AirPlay 2** | PTP variant (open-source: [nqptp](https://github.com/mikebrady/nqptp)) | "Buffered audio": the sender pushes seconds ahead with anchor (RTP ↔ network time) pairs. The receiver stuffs/drops or uses soxr; shairport-sync `drift_tolerance` and `resync_threshold`, and it relies on the backend's reported delay | **Apple TV [Wireless Audio Sync](https://support.apple.com/guide/tv/atvb228b7711/tvos)**: iPhone microphone measures the round trip to the speakers and stores an offset | Same pattern we have: phone-mic calibration plus the backend delay report |
| **Google Cast multi-room** | Proprietary; leader device | Device-side correction | **Manual ["Group delay correction"](https://support.google.com/googlenest/answer/6318642)** slider per device; Google advises using it only for a consistent, significant delay | A manual offset is the accepted UX for BT/aux-attached speakers |
| **Amazon Alexa MRM** | Proprietary | Device-side | **Bluetooth outputs aren't part of multi-room groups** (wired line-out is the workaround) | Exclusion is what some vendors chose |
| **WiiM / LinkPlay** | Proprietary (claims < 150 µs on a LAN) | Device-side | **[Auto Sync with on-device mic / Manual Sync](https://faq.wiimhome.com/en/support/solutions/articles/72000528834)**; digital outputs add > 50 ms. Users report BT output in groups stays unreliable and the offset is lost after a regroup or skip ([forum](https://forum.wiimhome.com/threads/bluetooth-output-sync-audio-issue.6475/)) | The same session-varying-offset problem we see |
| **Snapcast** | Median of NTP-style diffs (`TimeProvider`) | Client compares chunk age with the player-reported DAC delay. Soft sync: rate ±0.05 % implemented as 1-frame insert/drop every X frames; hard sync when medians exceed 2/5/50 ms ([`stream.cpp`](https://github.com/snapcast/snapcast/blob/develop/client/stream.cpp)) | Per-client `latency` setting, manual. BT known to need manual compensation | The closest open-source analogue of the loop we lack |
| **Squeezelite / LMS** | Server polls player status (jiffies) | **Server-side** `_CheckSync`: discrete `skipAhead`/`pauseFor` above `minSyncAdjust`; [Squeezelite](https://github.com/ralph-irving/squeezelite) has an optional resampler for rate matching | Per-player `playDelay` / `startDelay` (manual) | Coarse correction plus a static offset |
| **Roon RAAT** | Periodic sync exchanges, drift model ([clock ownership](https://community.roonlabs.com/t/raat-and-clock-ownership/6915)) | Endpoint DAC owns its clock and the core pulls. In a group, one zone is clock master and the others compensate internally. Staff say they avoid SRC/sample stuffing; 24 h grouped-playback certification test ([drift thread](https://community.roonlabs.com/t/grouped-playback-exhibiting-clock-drift/15407/4)) | Roon Ready certification: endpoints must know their latency. AirPlay/BT zones get manual offsets | Certification is how they enforce "knows its latency" |
| **Spotify Connect** | n/a | Spotify doesn't sync heterogeneous Connect devices; grouping is delegated to vendor ecosystems (Sonos, Cast, WiiM) | — | Shows the problem is usually left to the ecosystem owner |
| **Dante / AES67 / ST 2110** | PTPv1/v2, hardware timestamping | Media clock PLL-locked to PTP; fixed latency (Dante 0.25–10 ms); ASRC at foreign-clock boundaries | Not allowed: latency is configured and guaranteed | The "ASRC at the domain edge" principle |
| **Pro wireless IEM / monitoring** | Fixed RF link (analog FM or proprietary digital, e.g. Shure Axient/PSM digital, Sennheiser EW-DX) | Fixed and specified link latency (single-digit ms); the receiver locks to the transmitter | Engineers add delay on the console per path, measured or from the spec sheet | Fixed, known latency per path, compensated by static delays |
| **Kodi / VLC / mpv (A/V sync)** | n/a | **Audio is the master clock.** Video drops or repeats frames; or the audio is resampled to follow the display (mpv `display-resample`, Kodi "sync playback to display"). VLC slaves its rate to PA via `pa_stream_update_sample_rate` (variable-rate streams) | Per-device audio offset settings (user lip-sync calibration) | Our "display" (the BT speaker) is unobservable, so the Sendspin timeline is the master and we correct the host |
| **WebRTC NetEQ** | RTP/RTCP | Adaptive jitter buffer; time-stretch (accelerate / preemptive expand) to move the buffer target without pitch change ([neteq](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_coding/neteq/)) | n/a (latency-minimising, not synchronising) | Relevant only if we ever needed fast large corrections without a snap |
| **Browsers (Sendspin kiosks)** | Sendspin filter | `getOutputTimestamp` with validation, EMA-smoothed `baseLatency+outputLatency`; correction tiers: dead band 1 ms, crossfaded sample correction < 8 ms, rate 0.3 % / 0.5 %, resync > 200 ms; a fixed 100 ms for WebKit, which doesn't report output latency ([scheduler.ts](https://github.com/Sendspin/sendspin-js/blob/main/src/audio/scheduler.ts)) | Trusts the OS-reported latency (which includes BT where the OS knows it) | **The reference implementation of what our daemon should do** |
| **sendspin-cpp / SendspinKit** | Sendspin filter with bursts | sendspin-cpp: `notify_audio_played` feeds back playout progress; soft sync 1 frame per chunk, hard sync at start or for large errors ([playback-sync.md](https://github.com/Sendspin/sendspin-cpp/blob/main/docs/playback-sync.md)). SendspinKit: the `error = (expected + L) − cursor` invariant; HAL latency; starts the queue on silence so device spin-up overlaps buffering | HAL-reported latency | Two more confirmations of the same design |

**Calibration techniques for devices without delay reporting.**
- *Impulse/click onset* (ours, Apple TV): simple, robust in quiet rooms. Precision is limited by
  onset detection (our estimator reaches ~0.1 ms per 6 s window by averaging clicks).
- *Cross-correlation with a broadband signal* (MLS, log sweep after Farina 2000, GCC-PHAT): it
  uses all the signal energy, works at low level and in noise, and gives sub-sample precision
  with interpolation. Good for a quiet calibration with music playing.
- *Simultaneous reference*: record the device under test together with a known-good reference (our
  kiosks). This cancels the phone's own latency and clock (D7). Our tool already does this.
- *Echo/AEC delay estimators* (the WebRTC AEC delay estimator on smart speakers with a microphone):
  devices with both a speaker and a microphone can measure themselves. Speakers such as the
  Yandex Station have a mic, but it isn't reachable over A2DP.
- Delay **reports** cover only what the device knows (its buffer + DAC). They exclude the
  room/air and sometimes post-DSP, so they make a starting value, not ground truth.

---

## 3. Applicability matrix for the bridge

Legend for "Where": **GP** = our GStreamer player (`services/audio/player/`), **D** = daemon
(`bridge_daemon`, per-speaker subprocess), **PA** = PulseAudio/PipeWire config, **AS** =
aiosendspin, **SP** = Sendspin protocol extension, **MA** = Music Assistant server,
**UI** = bridge web UI / sync-tuning flow.

Verification uses the phone click-track estimator (phase per 6 s window, ~0.1 ms). Standard
recordings: **V-fresh** = 120 s solo starting at a fresh stream; **V-warm** = 120 s solo after
≥5 min of continuous playback; **V-mon** = the same estimator on a `parec` recording of the BT
sink monitor (host side only); kiosks as control in the same recording. Report offset (mean
phase vs kiosk), slope (ms/s, linear fit across windows), window σ, and steps
(> 1 ms jumps between windows).

| Technique | Problem solved | Where | Prerequisites | Cost | Risks / artefacts | Verify |
|---|---|---|---|---|---|---|
| Sendspin time filter (Kalman) | D1↔D2 offset and drift | AS (exists) | — | none | — | `client/time` error in the log ≪ 0.1 ms on the same host |
| Single time domain (schedule in RAW; convert to the sink's domain at one point per update) | D2/D3 slew (≤ 0.5 ms/s) | GP | — | trivial (part of the loop) | — | V-mon slope with NTP slewing (force with `adjtimex`) ≈ 0 |
| **Host playout-error metric** `e_host = audible_pos_time − schedule(sample)` | Observability of `e_host` and `L_host` | GP | pulsesink position or PA timing; sample index ↔ play-time map in the player | low | none (read-only) | Correlates with V-mon phase within ±0.5 ms |
| Subtract `L_host` (pipeline latency) from the PTS | Offset that depends on `pulse_latency_msec` | GP | latency query after the `LATENCY` message | low | All tuned `output_delay` values shift once; needs a migration | Offset invariant across `pulse_latency_msec` 300/600/800 |
| GStreamer thresholds alone (`drift-tolerance`, `alignment-threshold`) | — | GP | — | trivial | Every correction is a resync → clicks | Not recommended |
| `slave-method=none` + large `alignment-threshold` + our loop | Removes GStreamer's 20–40 ms steps and makes small delay changes take effect | GP | our loop present | trivial | Without our loop, unbounded drift | V-mon shows no steps |
| **1-frame insert/drop (spec), optionally crossfaded** | `e_host` drift (slew, card crystal, PA drops) | D/GP (PCM before `appsrc`) | metric above | medium (~200 lines + tests) | Ticks on pure tones at high correction rates; with crossfade inaudible at ≤ ~50 corrections/s | V-mon: ±0.5 ms over 30 min; V-warm slope ≤ 0.02 ms/s |
| One-shot snap (start, after underrun, error > ~5 ms) | Start alignment, large delay changes | D/GP | metric | low | Audible only if frequent; count them | Snap counter; V-fresh first window within ±1 ms host-side |
| ASRC (libsamplerate `samplerate` variable ratio; PI/DLL loop, B ≈ 0.1 Hz) | Same as insert/drop, more transparent | D | metric; resampler dependency (wheels for armv7/aarch64) | medium | Loop instability with noisy PA timing (filter it); CPU on RPi (sinc_fastest ~1–3 % per stream) | Same as above, plus a listening test on sine sweeps |
| PA variable-rate stream (`pa_stream_update_sample_rate`) | Same, PA-side resampling | would replace pulsesink | direct libpulse binding; `PA_STREAM_VARIABLE_RATE` | high | pipewire-pulse may ignore it ([PW #1159](https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/1159)); a second code path | — |
| PipeWire adaptive rate matching | D4 vs driver (host side) | PA (platform) | PipeWire host (Docker/LXC, not HAOS) | none | Doesn't lock to Sendspin | — |
| **Keep-warm stream** (no teardown at `stream/end`, silence fill, no sink suspend) | Start-up transient (H1), per-session offset spread, re-acquire latency | D/GP; PA (`module-suspend-on-idle` timeout); `keep_alive` idle mode | #464 drain semantics without flush deadlock: drop queued PCM, new appsrc segment, keep the sink | low–medium | Battery and power use; HFP/AVRCP interplay | V-fresh vs V-warm: slope after warm-up → speaker residual; per-run offset spread ±150 ms → ±few ms |
| Pre-roll silence + larger `required_lead_time_ms` while the sink is cold | Clipped starts, part of the transient | D, SP field (exists) | sink state from `sink_monitor` | low | Only covers the first ~0.5–1 s, not a 10 s+ buffer convergence | First-click alignment in V-fresh |
| **AVDTP Delay Report** (`MediaTransport1.Delay`) | `L_speaker` offset (device-reported) | D (`bt_dbus`), UI | speaker supports it; BlueZ endpoint registered with delay reporting; on PipeWire hosts, avoid double counting (already in sink latency) | low | Reports exclude DSP and air; some devices report nonsense (sanity-clamp) | Compare with acoustic offset; spread across speakers |
| **Acoustic calibration (phone click)** → `output_delay` | `L_speaker` + `L_link` + `L_acoustic` offset | UI / sync-tuning (exists) | warm steady state; per speaker × codec × adapter | low | Measuring cold gives a session-dependent number | Repeat 3× warm; σ of the offset ≤ 2 ms |
| Periodic or automatic re-calibration (outer loop, minutes–hours) | Speaker offset changes between sessions, slow residuals | UI/D | user-accepted mic sessions | medium (product) | Privacy; interrupts music unless done on program material with correlation | Calibration age vs measured offset change |
| Sub-ms `output_delay` (µs field) | Quantisation (1 ms) vs the ±0.5 ms target | SP, AS, MA | spec change | low | Compatibility | — |
| Sync telemetry (IDMS-like report) | Visibility of non-compliance in MA | D → SP → MA | spec change; our metric | low (bridge), medium (upstream) | — | MA shows `sync_error_us` ≈ V-mon |
| Codec choice (SBC vs AAC) | `L_link` size and variance | config (exists: `prefer_sbc_codec`) | — | none | AAC adds ~20–60 ms encoder + frame latency; PA AAC support varies | Offset and σ per codec |
| Smaller PA `tlength` (`PULSE_LATENCY_MSEC` 600–800 → 150–250 once the loop exists) | Smaller `L_host`; faster delay changes; less to compensate | PA/D | loop + `L_host` subtraction | trivial | Underruns on loaded hosts (QEMU) | Underrun counters; V-mon σ |
| Avoid PA bluez5 drops (one speaker per adapter, 5 GHz Wi-Fi, avoid USB passthrough into a VM) | H2 steps | deployment docs | — | none | — | PA debug "Skipping" count = 0; no steps in V-mon |
| Time-stretch (WSOLA) for large corrections | Fast catch-up without a snap | D | — | medium | Transient smearing | Not needed (snap is spec-legal) |
| LE Audio presentation delay | Offset *and* drift at the radio level | platform | LE Audio speakers, BlueZ/PipeWire ISO | high | Immature on Linux | Future |
| PTP/gPTP or a GStreamer net clock | D1↔D2 | — | — | — | Sendspin mandates its filter | Not applicable |

---

## 4. Recommended architecture and roadmap

### 4.1 Target design: a clock-domain-aware player

```
Sendspin chunk(ts) ─► time filter (RAW) ─► schedule s(n) for every output frame n
                                               │
                       ┌───────────────────────┘
                       ▼
   PCM ─► [Corrector: insert/drop (→ ASRC later)] ─► appsrc ─► pulsesink (slave-method=none,
              ▲                                                  alignment-threshold=∞,
              │ correction (frames, ratio)                       buffer-time ≈ 200 ms)
              │                                                     │
   [Loop: dead band 100 µs, PI/DLL B≈0.1 Hz,      ◄── e_host(t) = RAW_now − s(n_audible(t))
    rate clamp ≤0.5 %, snap if |e|>5 ms                 n_audible from sink position (PA timing,
    or at start/clear/underrun]                         device delay subtracted), sampled at 10 Hz
                                                        and converted to RAW at one point
   target offset = output_delay  (= Delay Report ‖ acoustic calibration ‖ manual)
   telemetry: e_host, ppm, snaps, L_host, delay source/age  ─► logs, UI, (later) Sendspin client/state
```

Design rules:
1. **One error signal, defined at the PA sink output**, in the RAW/Sendspin domain. Whatever
   lies upstream of it (the GStreamer ring, the PA `tlength`, `L_host`) is inside the loop and
   gets compensated automatically. That removes the `pulse_latency_msec` coupling without a
   separate "subtract latency" rule.
2. **Define "the port" as the PA sink.** By the spec, everything up to the port is the client's
   own business (self-compensated); beyond the port is `output_delay` (BT link + speaker buffer +
   DAC + air). This matches the spec text ("compensates for additional delay beyond the port
   (external speakers…)") and gives `output_delay` a clean physical meaning per speaker. For local
   sound cards, `output_delay ≈ 0` and accuracy should approach the kiosks'.
3. **Only one active loop**: GStreamer slaving off. PA's own timing is used only as a sensor.
4. **Keep the stream warm.** The sink stream lives as long as the speaker is connected and the
   player is active. `stream/end` drops queued PCM and goes to silence; it doesn't tear down the
   pipeline. A new `stream/start` is a snap on a running sink.
5. **Steady-state calibration discipline.** Acoustic measurements happen only on a warm sink.
   Results are stored with their context (codec, adapter, PA vs PipeWire, bridge version), and
   re-measuring is suggested when that context changes.

Why this is enough for BT despite the invisible speaker: by §1.9 / Summary insight 2 the
speaker's long-term rate follows the host send rate, which the loop locks to the timeline. What
remains is a per-session buffer target (constant while warm, removed by calibration) and the
firmware's control jitter (measurable; usually a few ms; not correctable from the host).

### 4.2 Phases

**P0. Instrument (≈ days; from drift report R1, extended).**
- Add `e_host` (10 Hz, logged at 1 Hz), `L_host`, snap/drop counters, and
  `MediaTransport1.Delay` if exposed. Redefine or rename `playback_sync_error_ms`.
- Run the V-fresh / V-warm / V-mon trio on ENEBY Portable plus one local sound card.
- *Expected:* confirm H1 (acoustic slope decays to ≈0 while V-mon stays flat) and size H2
  (steps in V-mon).
- *Exit criterion:* we know, per hypothesis, how many ms each term contributes.

**P1. Keep-warm (≈ days).**
- No teardown at `stream/end`; silence fill; sink unsuspended while the player is active (reuse
  `keep_alive`). The sync-tuning flow warms each BT member for ≥30 s before its solo.
- *Expected:* per-run offset spread drops from ±150 ms to a few ms; the #546 slope disappears
  from steady-state measurements; #284 "desync after announcement" improves.
- *Validate:* 5 announcement → music cycles, offset per cycle.

**P2. Host playout loop (≈ 1–2 weeks with tests).**
- Insert/drop corrector with an 8–16-sample crossfade, dead band 100 µs, PI on the filtered
  error, one-shot snap.
- GStreamer: `slave-method=none`, `alignment-threshold` and `discont-wait` set very large,
  `buffer-time` default 200–250 ms.
- Delay changes (`set_output_delay`) become a target shift: soft for < 5 ms, snap otherwise. This
  replaces the current "rebuild on change".
- *Expected:* V-mon within ±0.5–1 ms for 30 min; local sound cards within kiosk class; offset
  independent of `pulse_latency_msec`; NTP slew invisible.
- *Migration:* the shift in tuned offsets is ≈ −`L_host`. Convert automatically once (stored
  offset − measured `L_host`) and say so in the changelog.
- *Validate:* unit tests on a fake sink clock (step, ramp, noise); V-mon; pulse latency sweep;
  delay step-response test (±10/30/100 ms).

**P3. Speaker offset model (≈ days).**
- `output_delay` priority: manual > acoustic (warm, stored with context) > Delay Report > a
  default per codec.
- UI shows the source and age, and a "re-measure" suggestion on context change.
- *Expected:* first-time grouping within ~10–30 ms without a mic when a Delay Report exists;
  within ±1–2 ms with acoustic calibration.
- *Validate:* Delay Report vs acoustic across the speaker fleet; repeatability across reconnects.

**P4. ASRC (conditional).**
- Only if listening tests show insert/drop artefacts at the measured correction rate (expected
  ≤ 50 ppm with NTP → ~2 corrections/s at 48 kHz: inaudible; worst-case slew 500 ppm → 24/s).
- *Validate:* sine sweep plus music ABX at forced 200 ppm.

**P5. Upstream (in parallel, after P2 data exists).**
- **Sendspin spec** (proposal with our measurements):
  1. optional `client/state` player telemetry: `sync_error_us` (measured at the port, signed),
     `correction_ppm`, `resync_count`, `self_compensated_latency_ms`, `output_delay_source`
     (`manual|device_report|acoustic|default`), modelled on RFC 7272 IDMS reports;
  2. `output_delay_us` (or fractional ms), because the ±0.5 ms target can't be met with 1 ms
     steps;
  3. a "bridging clients" note defining the port and the split between self-compensated latency
     and `output_delay`;
  4. a conformance test in [Sendspin/conformance](https://github.com/Sendspin/conformance) /
     [sync-test](https://github.com/Sendspin/sync-test) for bridges with hidden sink latency.
- **aiosendspin:** a reusable `PlayoutCorrector` (inputs: schedule, audible-position samples;
  outputs: insert/drop counts or a resample ratio, snaps). aiosendspin 10 ships no player, so
  every Python player repeats this work.
- **Music Assistant:**
  1. keep a grouped player's stream continuous across announcements (duck/mix instead of
     `stream/end` + `stream/start`), or at least send `stream/clear` on a live stream;
  2. a group calibration flow (phone mic, like Apple TV Wireless Audio Sync) that writes
     `set_output_delay` for every member type, not only the bridge;
  3. surface the reported telemetry as group sync health.

**P6. Long term.** Follow LE Audio (BAP presentation delay) in BlueZ and PipeWire. Recommend
PipeWire hosts for BT-heavy setups (Delay Reports, rate matching).

### 4.3 Expected end state

| Member type | Host error (V-mon) | Acoustic offset after calibration | Long-term drift |
|---|---|---|---|
| Local sound card via PA | ±0.5–1 ms | ±1 ms (DAC latency, constant) | ≈ 0 |
| BT speaker, warm, calibrated | ±0.5–1 ms | ±2–5 ms (firmware buffer control jitter, device-dependent) | ≈ 0 (rate follows the host) |
| BT speaker, cold start | ±1 ms | transient over 10–60 s (H1), unavoidable without keep-warm | — |

---

## Glossary

- **ASRC**: asynchronous sample-rate conversion; resampling by a continuously variable ratio between two unrelated clocks.
- **AVDTP Delay Report**: A2DP 1.3 / AVDTP 1.3 message in which a sink reports its presentation delay (units of 0.1 ms).
- **AVTP / presentation time**: IEEE 1722 timestamp meaning "present this sample at gPTP time T".
- **BAP / CIS / BIS / ISO**: LE Audio Basic Audio Profile; connected and broadcast isochronous streams; isochronous channels with link-layer-anchored timing.
- **Clock domain**: components sharing one oscillator.
- **Dead band**: an error range inside which no correction is applied.
- **DLL**: delay-locked loop; a second-order filter for timestamps or phase, here used to smooth noisy time measurements.
- **Drift**: rate difference between clocks (ppm; 1000 ppm = 1 ms/s).
- **gPTP**: IEEE 802.1AS profile of PTP for AVB/TSN.
- **IDMS**: inter-destination media synchronisation (RFC 7272).
- **Jitter buffer**: buffer that absorbs arrival-time variance.
- **Link offset**: AES67 fixed receive latency added to the RTP/PTP timestamp.
- **`L_host`**: latency from GStreamer `appsrc` to the PA sink output (ring + `tlength`).
- **`L_speaker`**: latency inside the BT speaker (jitter buffer + DSP + DAC).
- **One-shot snap / resync**: a discrete jump to the correct position (drop a prefix or insert silence).
- **`output_delay_ms`**: Sendspin per-player delay beyond the output port, subtracted from timestamps (formerly `static_delay_ms`).
- **PCR / STC / PTS**: MPEG-TS program clock reference, system time clock (27 MHz), presentation time stamp (90 kHz).
- **PI loop**: proportional-integral controller; second order, so no steady-state phase error under a constant rate offset.
- **Port**: the point where the Sendspin client's self-compensation ends (our proposal: the PA sink output).
- **RAW / MONOTONIC**: Linux `CLOCK_MONOTONIC_RAW` (unslewed crystal) vs `CLOCK_MONOTONIC` (NTP-slewed).
- **Sample slip**: inserting or dropping single frames to correct drift.
- **Time filter**: Sendspin's 2-state Kalman filter (offset, drift) mapping server time to local time.
- **V-fresh / V-warm / V-mon**: the verification recordings defined in §3.

## References

**Standards and specifications**
- Sendspin protocol spec, sections "Playback Synchronization", "Clock Synchronization", "client/state player object": https://github.com/Sendspin/spec/blob/main/README.md
- Sendspin time-filter, theory: https://github.com/Sendspin/time-filter/blob/main/docs/theory.md
- RFC 3550 RTP/RTCP: https://www.rfc-editor.org/rfc/rfc3550
- RFC 5905 NTPv4: https://www.rfc-editor.org/rfc/rfc5905
- RFC 6051 Rapid synchronisation of RTP flows: https://www.rfc-editor.org/rfc/rfc6051
- RFC 7272 Inter-destination media synchronisation using RTCP: https://www.rfc-editor.org/rfc/rfc7272
- RFC 7273 RTP clock source signalling: https://www.rfc-editor.org/rfc/rfc7273
- IEEE 1588-2019 (PTP); IEEE 802.1AS-2020 (gPTP); IEEE 1722-2016 (AVTP); AES67-2018; SMPTE ST 2110-10/-30; ISO/IEC 13818-1 (MPEG-2 Systems): standards bodies, paywalled
- W3C Web Audio API (`outputLatency`, `getOutputTimestamp`): https://www.w3.org/TR/webaudio/
- Bluetooth Basic Audio Profile (LE Audio presentation delay): https://www.bluetooth.com/specifications/specs/basic-audio-profile-1-0-1/
- BlueZ MediaTransport1 (`Delay`): https://man.archlinux.org/man/org.bluez.MediaTransport.5.en

**Papers**
- F. Cristian, "Probabilistic clock synchronization", *Distributed Computing* 3, 1989.
- R. Gusella, S. Zatti, "The accuracy of the clock synchronization achieved by TEMPO in Berkeley UNIX 4.3BSD", *IEEE TSE* 15(7), 1989.
- F. Adriaensen, "Using a DLL to filter time", LAC 2005: https://kokkinizita.linuxaudio.org/papers/usingdll.pdf
- F. Adriaensen, "Controlling adaptive resampling", LAC 2012: https://kokkinizita.linuxaudio.org/papers/adapt-resamp.pdf
- M. Montagud, F. Boronat, H. Stokking, R. van Brandenburg, "Inter-destination multimedia synchronization: schemes, use cases and standardization", *Multimedia Systems* 18(6), 2012.
- A. Farina, "Simultaneous measurement of impulse response and distortion with a swept-sine technique", AES 108th Convention, 2000.

**Open-source implementations**
- sendspin-js scheduler and output latency: https://github.com/Sendspin/sendspin-js/blob/main/src/audio/scheduler.ts, https://github.com/Sendspin/sendspin-js/blob/main/src/audio/output-latency-tracker.ts
- sendspin-cpp playback sync: https://github.com/Sendspin/sendspin-cpp/blob/main/docs/playback-sync.md
- SendspinKit audio timing model: https://github.com/Sendspin/SendspinKit/blob/main/docs/audio-timing-model.md
- Sendspin sync-test / conformance: https://github.com/Sendspin/sync-test, https://github.com/Sendspin/conformance
- aiosendspin (`client/time_sync.py`, `models/player.py`): https://github.com/Sendspin/aiosendspin
- Snapcast client `stream.cpp`: https://github.com/snapcast/snapcast/blob/develop/client/stream.cpp
- Squeezelite: https://github.com/ralph-irving/squeezelite · LMS StreamingController: https://github.com/LMS-Community/slimserver/blob/public/9.1/Slim/Player/StreamingController.pm
- shairport-sync (man page): https://manpages.debian.org/testing/shairport-sync/shairport-sync.7 · nqptp: https://github.com/mikebrady/nqptp
- zita-ajbridge / zita-a2j: https://manpages.ubuntu.com/manpages/xenial/man1/zita-a2j.1.html
- PipeWire properties (latency offset, rate matching): https://docs.pipewire.org/page_man_pipewire-props_7.html · PW MR 436/460 (BT delay): https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/436, https://gitlab.freedesktop.org/pipewire/pipewire/-/merge_requests/460 · PW #1159: https://gitlab.freedesktop.org/pipewire/pipewire/-/issues/1159
- GStreamer audiobasesink: https://github.com/GStreamer/gstreamer/blob/main/subprojects/gst-plugins-base/gst-libs/gst/audio/gstaudiobasesink.c · Aurena (network-synchronised GStreamer playback): https://github.com/thaytan/aurena
- PulseAudio bluez5 sink: https://gitlab.freedesktop.org/pulseaudio/pulseaudio/-/blob/master/src/modules/bluetooth/module-bluez5-device.c
- WebRTC NetEQ: https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_coding/neteq/
- mpv manual (`video-sync`): https://mpv.io/manual/master/

**Vendor documentation and statements**
- Apple TV Wireless Audio Sync: https://support.apple.com/guide/tv/atvb228b7711/tvos
- Google "Correct group playback delay": https://support.google.com/googlenest/answer/6318642
- WiiM Sync Audio (auto/manual): https://faq.wiimhome.com/en/support/solutions/articles/72000528834 · WiiM BT output in groups (forum): https://forum.wiimhome.com/threads/bluetooth-output-sync-audio-issue.6475/
- Alexa MRM and Bluetooth (secondary source): https://www.tomsguide.com/us/amazon-echo-multi-room-music-setup%2Creview-4634.html
- Roon RAAT clock ownership: https://community.roonlabs.com/t/raat-and-clock-ownership/6915 · grouped drift: https://community.roonlabs.com/t/grouped-playback-exhibiting-clock-drift/15407/4
- Sonos synchronisation patent US 8,234,395 B2: https://patents.google.com/patent/US8234395B2

**Project context**
- Drift report: [`2026-10-16-bt-sync-drift.md`](2026-10-16-bt-sync-drift.md) · issue #546: https://github.com/trudenboy/sendspin-bt-bridge/issues/546
