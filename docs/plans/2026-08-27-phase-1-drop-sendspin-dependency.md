# Phase 1 — drop the `sendspin` package: implementation plan

> **For Claude:** Use superpowers:executing-plans for task-by-task execution.

**Goal:** Remove the `sendspin` client application from the dependency tree. The daemon
subprocess talks to `aiosendspin` directly and plays through GStreamer. The wire does not
change (aiosendspin stays at 6.1.1), so Music Assistant player ids, pairing state and
everything an operator sees stay exactly as they are.

**Architecture:**

```
daemon subprocess (one per speaker, unchanged)
  BridgeDaemon (ours)
    ├── aiosendspin.client.SendspinClient        protocol, clock, roles
    ├── ClientListener (+ heartbeat=30)          server-initiated mode, mDNS
    └── StreamPlayer (ours) → GStreamer:
          appsrc ! [flacparse!flacdec | opusparse!opusdec]? !
          audioconvert ! audioresample ! pulsesink device=<bluez_output…>
```

Scheduling against the device clock, drift correction, resampling and discontinuity
handling move from hand-written Python to GStreamer (`sync=true`, `slave-method`,
`drift-tolerance`, `alignment-threshold`, `discont-wait`). We hand it buffers carrying a
PTS and read metrics back.

**Tech Stack:** Python 3.13, `aiosendspin` 6.1.1, GStreamer 1.26 via PyGObject
(`python3-gi`, `gir1.2-gstreamer-1.0`, `gstreamer1.0-plugins-base`,
`gstreamer1.0-pulseaudio`), pytest with `GstCheck.TestClock`.

---

## Why now

- **The upstream client app is frozen.** `sendspin` 7.5.0 was released on 2026-06-16 and
  its repository has had no commit since. It still pins `aiosendspin~=6.0.1`, and it does
  not appear in the ecosystem's own `Sendspin/conformance` matrix at all.
- **The protocol moved three majors.** `aiosendspin` went 7.0 → 8.0 → 9.1.1 between
  2026-07-15 and 2026-08-25, tracking a specification that saw 23 commits in August alone
  (including renames such as `static_delay_ms` → `output_delay_ms`). Music Assistant
  2.10.0 — the release running on the test stand — pins `aiosendspin[server]==9.1.1`.
- **We are non-conformant, by permission.** The specification makes Noise encryption
  mandatory. Our 6.x client is accepted only because the MA provider passes
  `allow_unencrypted=allow_legacy_clients` to its server, a hidden option defaulting to
  true. That is a transition switch, not a contract.
- **The dependency is already a fork by inheritance.** `BridgeDaemon` overrides about a
  dozen private `SendspinDaemon` methods, `daemon_process.py` monkey-patches
  `AudioPlayer._audio_callback` / `set_format` and the FLAC decoder, and
  `services/diagnostics/sendspin_compat.py` (350 lines) exists solely to survive upstream
  API drift.

Phase 1 removes the intermediary. Phase 2 (aiosendspin 6 → 9, Noise, pairing, identity)
becomes possible afterwards and is out of scope here.

## Ground rules

- **One branch, one PR**, no intermediate release. The tasks below are commit boundaries:
  each commit passes the full gate (ruff, pytest, changelog linter) and leaves the tree
  working, so review and `git bisect` read step by step.
- The live verification runs once, on the assembled path, before the PR is opened.
- Ships in the current 2.76 cycle; the release after the merge is 2.76.0-rc.3.

## Settled decisions

| | Decision |
|---|---|
| Player | Neither hand-written nor vendored — GStreamer owns scheduling |
| Output | `pulsesink device=<sink>` (explicit) rather than the `PULSE_SINK` env trick |
| Order | Lifecycle first, audio engine second |
| Escape hatch | None; two live audio paths would double the debugging surface |
| Formats | Keep the vendored probe; the advertised set does not change |
| Settings | No `ClientSettings` file — parameters arrive over IPC, nothing on disk |
| Test seam | Element factory; tests drive a real pipeline, not a mock |
| Diagnostics | The sendspin version report is replaced by an audio-stack report |

---

## Task 1: Own the client lifecycle

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py`
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py` (the `DaemonArgs` block only)
- Delete: `tests/unit/services/ipc/test_bridge_daemon_signature.py`

`BridgeDaemon` stops subclassing `sendspin.daemon.daemon.SendspinDaemon` and composes
`aiosendspin.client.SendspinClient` itself. The class name and constructor stay as they
are, so `daemon_process.py` keeps calling it the same way.

What becomes ours (most of it is already written, as overrides):

- `run()`: SIGINT/SIGTERM handlers, the 0…5000 ms clamp on static delay, mode selection
  (a URL means client-initiated, its absence means server-initiated), teardown order.
- `_run_server_initiated` with the `_HeartbeatListener` subclass — ours already; it stops
  being an override. It exists because upstream's `ClientListener` creates its
  `WebSocketResponse` without `heartbeat`, and idle connections die on Docker bridge
  networks (music-assistant/support#4598, #120).
- `_handle_server_connection`, the connection lock, `_handle_disconnect`,
  `_should_switch_to_new_server` (keeps `last_played_server_id` in process memory),
  `_connection_loop` for client-initiated mode, `_connection_watchdog`.
- A small frozen dataclass replacing `DaemonArgs`. With it,
  `_filter_supported_daemon_args_kwargs` and `filter_supported_call_kwargs`
  (`services/diagnostics/sendspin_compat.py`, also imported by
  `services/bluetooth/device_activation.py`) lose their reason to exist on this path.

Audio in this commit is still upstream's: the daemon constructs
`sendspin.audio_connector.AudioStreamHandler` itself with the arguments
`SendspinDaemon.run()` used. That state lives until the next commit on the same branch,
and exists so that the lifecycle change and the engine change are verified separately.

`test_bridge_daemon_signature.py` is deleted rather than updated: it spawns a subprocess to
compare our overrides against upstream signatures, and after this commit there is no
override for a signature to drift from.

**Tests:** `tests/unit/services/ipc/test_bridge_daemon_features.py` (1000 lines) and
`test_daemon_process.py` (1053 lines) must stay green without being rewritten — that is the
evidence the composition reproduced the behaviour. Add what could not be tested through
inheritance: a server switch while a connection is live, a listener that fails to bind, two
inbound connections racing.

---

## Task 2: Own the audio output (GStreamer)

**Files:**
- Add: `src/sendspin_bridge/services/audio/player/pipeline.py`
- Add: `src/sendspin_bridge/services/audio/player/player.py`
- Add: `src/sendspin_bridge/services/audio/player/formats.py` (vendored probe, see below)
- Modify: `src/sendspin_bridge/services/audio/timing_telemetry.py`
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py` (drop both monkey-patches)
- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py` (use `StreamPlayer`)
- Add: `tests/unit/services/audio/test_stream_player.py`

**The pipeline** (`pipeline.py`): `pulsesink` carries `device=<sink name>` and
`client-name=<player name>`, `sync=true`, `provide-clock=false`, `slave-method=skew`
(configurable — `resample` is gentler on the ear and costs CPU that armv7 does not have),
`buffer-time` from `PULSE_LATENCY_MSEC`. The bus is polled from asyncio
(`bus.timed_pop_filtered(0, …)`); no GLib main loop is started, because a second event loop
in the daemon process is a source of stalls we would then have to debug.

**The interface** (`player.py`) — what the daemon uses, and the whole test surface:

```python
start(audio_format, codec_header)   # (re)negotiate caps, push the codec header
submit(server_ts_us, payload)       # one chunk, scheduled by its timestamp
clear()                             # drop what is queued, keep the pipeline
close_stream()                      # end of stream
set_volume(value, muted)
stop()
is_drained -> bool
metrics() -> dict
```

Elements are created through an injected factory, which is how tests swap `pulsesink` for
`fakesink`.

**Time mapping.** Per chunk:

```
pts = (client_play_time_us − raw_now_us()) + gst_now_us()
```

where `client_play_time_us` comes from `SendspinClient.compute_play_time(server_ts)`. Both
clocks are read at push time, so the difference between `CLOCK_MONOTONIC_RAW` (which
aiosendspin uses deliberately, to keep NTP slewing out of its time filter) and
`CLOCK_MONOTONIC` (GStreamer's system clock) corrects itself continuously. No custom
`GstClock` subclass is needed.

**Decoding moves into the pipeline.** The specification defines a FLAC chunk as "one or
more complete FLAC frames", with `codec_header` carrying the `fLaC` marker followed by
STREAMINFO — that is an ordinary FLAC stream for `flacparse ! flacdec`, and Opus works the
same way through its header. PyAV leaves the daemon path entirely.

**Format probing stays** (`formats.py`): vendor `AudioDevice`, `query_devices`,
`detect_supported_audio_formats`, `parse_audio_format` and `validate_audio_format` from
`sendspin/audio_devices.py` (Apache-2.0, attribution in the module header and `NOTICE`).
The consequence to accept knowingly: `sounddevice` and `libportaudio2` remain in the image
**as a format probe only**, not as an output path. In exchange the set advertised to the
server does not change by a single entry, which is the point of keeping it.

**What does not move:** `services/audio/pa_volume_controller.py` (sink-level volume) and
`services/audio/pulse.py:amove_pid_sink_inputs` with `_startup_sink_routing_watcher` — the
correction for PulseAudio's `module-rescue-streams` is needed whatever plays the audio.

**Telemetry** (`timing_telemetry.py`): `collect_timing_snapshot` reads `player.metrics()`
instead of upstream's private attributes. The status keys the UI renders stay:

| Key | New source |
|---|---|
| `backend_output_latency_ms` | pipeline latency query |
| `buffered_audio_ms` | `appsrc` level |
| `playback_position_us` | pipeline position query |
| `playback_sync_error_ms` | sink-pad probe: buffer PTS vs pipeline clock at render |
| `clock_*` | unchanged, from `SendspinClient` |

The re-anchor counters in `daemon_process.py` (`_record_reanchor_status`,
`_observe_structured_reanchor`) change source: count the resyncs GStreamer performed
(`alignment-threshold` / `discont-wait` breaches, QoS messages on the bus) plus our own gap
events. The keys and their meaning to an operator — "how many times in the last 5/30
minutes did synchronisation have to be rebuilt" — stay.

**Tests:** a real pipeline, `appsrc ! audioconvert ! fakesink sync=true`, driven by
`GstCheck.TestClock` so time advances by hand: a buffer with PTS=T renders at T; a gap wider
than `alignment-threshold` counts as a discontinuity; a format change renegotiates caps
without losing position; `clear()` empties the queue and leaves the pipeline alive.

First thing to check in this task: whether `GstCheck.TestClock` is introspectable from the
four packages above (Debian ships the GIR next to `Gst`). If it is not, add
`libgstreamer1.0-dev` to the test job, or fall back to the real clock with tolerances.

---

## Task 3: Cut the dependency and trim the image

**Files:**
- Modify: `pyproject.toml`, `requirements.txt`
- Modify: `Dockerfile`
- Modify: `.github/workflows/_test.yml`
- Modify: `deployment/lxc/install.sh` (the apt list, currently installing `libportaudio2`)
- Delete: `src/sendspin_bridge/services/diagnostics/sendspin_compat.py`
- Add: a small runtime-versions module to replace its one surviving function
- Modify: `src/sendspin_bridge/bridge/orchestrator.py` (drop the startup version check),
  `src/sendspin_bridge/web/routes/api_status.py`, `src/sendspin_bridge/web/routes/api_config.py`

- `pyproject.toml`: drop `sendspin==7.5.0`; change `aiosendspin[server]==6.1.1` to plain
  `aiosendspin==6.1.1` (the `server` extra is what pulls `av`, `numpy` and `pillow`, none of
  which this codebase imports); drop `override-dependencies` (it exists only to beat
  `sendspin`'s pin) and the `av<17` entry in `constraint-dependencies`. Regenerate
  `requirements.txt` with `uv export --no-hashes --no-dev --no-emit-project`.
- `Dockerfile`: add the four GStreamer packages and
  `PYTHONPATH=/usr/lib/python3/dist-packages` (Debian's `python3-gi` installs into
  `dist-packages`, which the python.org interpreter does not read by default); drop the
  `libav*` sonames from the armv7 branch (they were there for PyAV); the block that
  currently removes GStreamer and FFmpeg to save "~107 MB" inverts.
- `sendspin_compat.py` goes; `get_runtime_dependency_versions` (used by
  `api_status.py` and `api_config.py`) moves to a module of its own with `sendspin` gone
  from the name list. `check_sendspin_version_compatibility` and its call in
  `orchestrator.py` are deleted — the crash loop it guarded against (#324) was a property
  of the dependency being removed.
- In its place, a short audio-stack report: GStreamer version, whether `pulsesink` exists,
  whether the sink opened, which caps were negotiated. The question "why is there no sound"
  still needs an answer; it should now be answered by what is actually underneath us.

**Expected image arithmetic:** +132 MB (measured on our own base image with
`--no-install-recommends`) − 119 MB (`av` + `av.libs`) − 60 MB (`numpy`) − 6 MB (`pillow`)
≈ **−53 MB**. Verify with a real build rather than the estimate.

---

## Risks and fallbacks

- **PTS through `flacparse`/`flacdec`.** If the parser re-times buffers, decode in Python
  instead (vendor `sendspin/decoder.py`, 237 lines, Apache-2.0) and push PCM. The pipeline
  keeps its shape; two elements drop out.
- **Bluetooth sink jitter.** `slave-method=skew` is cheaper, `resample` is gentler and
  costs CPU. Expose it in config, default to `skew`, compare on the stand.
- **`GstCheck.TestClock` may not be in the chosen packages** — see Task 2.
- **PyGObject inside the daemon process.** The bus is polled from asyncio and no GLib loop
  runs; if blocking appears anyway, the pipeline moves to its own thread behind a queue.
- **armv7.** Debian armhf ships the GStreamer packages prebuilt, so nothing compiles — this
  architecture gains from the change, since today it builds PyAV and numpy from source.

## Verification

Run once on the assembled path, before opening the PR.

1. **Unit:** the pipeline under `GstCheck.TestClock` (scheduling, discontinuities, format
   change, `clear()`); the daemon through the existing 2000+ lines of IPC/daemon tests,
   unmodified.
2. **CI:** lint and tests on 3.13 with the GStreamer packages; `docker-smoke` as the image
   gate.
3. **Live, on the two-adapter stand against MA 2.10.0:** start → stream → negotiated caps
   in the log; audible sync against the second speaker; `playback_sync_error_ms` and the
   discontinuity counter over a 30-minute soak; a track change at a different sample rate;
   server disconnect and reconnect; standby and wake; volume from MA and from the speaker's
   own buttons (AVRCP); `docker images` to compare the size delta against the estimate.

## Out of scope

The move to aiosendspin 9 (Noise, pairing, persistent identity, and the player-id change in
MA that comes with it) is Phase 2. Replacing the engine with `sendspin-cpp-cli` is a
separate candidate, worth revisiting only once `sendspin-cpp` implements Noise — it does not
today. Dropping the format probe in favour of a declared set is a candidate after Phase 1.
