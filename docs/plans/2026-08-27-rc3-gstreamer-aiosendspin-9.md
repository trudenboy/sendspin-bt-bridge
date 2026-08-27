# 2.76.0-rc.3 — GStreamer Player + aiosendspin 9.1.1

> **For Claude:** Use superpowers:executing-plans for task-by-task execution.
> Supersedes `2026-08-27-phase-1-drop-sendspin-dependency.md`.

**Goal:** Drop the `sendspin` package. The daemon subprocess talks to
`aiosendspin==9.1.1` (Noise, Identity, pairing) and plays PCM through GStreamer.
One branch, one PR. Three green commit boundaries so the engine change and the
wire change still bisect.

**Architecture:**

```
daemon subprocess (one per speaker, unchanged)
  BridgeDaemon (ours)
    ├── Identity + FileClientPairingStore     persist under CONFIG_DIR
    ├── aiosendspin 9.1.1 SendspinClient      protocol, clock, admission, FLAC→PCM
    ├── ClientListener (+ heartbeat=30)       server-initiated mode, mDNS
    └── StreamPlayer (ours) → GStreamer:
          appsrc (PCM) ! audioconvert ! audioresample !
          pulsesink device=<sink name> sync=true
```

aiosendspin 9.1.1 already decodes FLAC and delivers PCM on
`add_audio_chunk_listener`. There is no `flacparse` in this pipeline.
`static_delay_ms` is still the SDK field name; the operator-facing config key
does not change.

**Tech Stack:** Python 3.13, `aiosendspin==9.1.1` (no `[server]` extra),
GStreamer via PyGObject (`PyGObject` from PyPI, not debian `python3-gi`),
`gstreamer1.0-plugins-base`, `gstreamer1.0-pulseaudio`, pytest with a real
pipeline (`fakesink`; `GstCheck.TestClock` when the GIR is present).

---

## Why this RC combines both jumps

- `sendspin` 7.5.0 pins `aiosendspin~=6.0.1`. The 9.x wire cannot land while
  that package remains.
- Music Assistant 2.10.0 on the test stand pins `aiosendspin[server]==9.1.1`.
  Noise is mandatory; 6.x is accepted only via `allow_unencrypted`.
- The SDK, not GStreamer, now owns FLAC decode. A Phase-1 pipeline of
  `flacparse ! flacdec` would double-decode or fight PTS.

Commit 2 still plays on aiosendspin 6.1.1 so a silent GST bug is not blamed on
Noise. Commit 3 is the wire jump.

## Ground rules

- One branch, one PR. Each commit below is a boundary: ruff, pytest, and the
  changelog linter are green, and the tree still runs.
- Live verification runs once, on the assembled path, before the PR opens.
- The release after merge is **2.76.0-rc.3**.

## Settled decisions

| | Decision |
|---|---|
| Player | GStreamer; input is PCM from the SDK |
| Output | `pulsesink device=<sink>` (explicit). Drop `PULSE_SINK` in the engine commit |
| PyGObject | PyPI `PyGObject`, not `PYTHONPATH` onto debian `python3-gi` |
| Formats | Declared FLAC+PCM 44.1/48 stereo 16. SDK rejects anything else. Drop `sounddevice` / `libportaudio2` in the wire commit |
| Identity | X25519 per speaker, persisted. Wire `client_id` = `identity.peer_id`. Bridge `player_id` stays uuid5(MAC) |
| Pairing | `FileClientPairingStore` per MAC under `CONFIG_DIR`. UI: Pair → `open_pairing_window` + PIN on the card. `unpaired_access=false` |
| Delay | Keep `static_delay_ms` in config/UI/IPC — 9.1.1 still uses that name |
| Settings | No `sendspin` `ClientSettings` file. Disk holds identity + pairing store only |
| Admission | Do not copy `_should_switch_to_new_server`. The SDK arbitrates |
| Escape hatch | None |
| Test seam | Element factory; tests drive a real pipeline |
| Diagnostics | Audio-stack report replaces the sendspin version check |

First upgrade: Music Assistant sees **new** players (`peer_id` ≠ uuid5). The
operator removes the orphans. `duplicate_device_check` must use the learned MA
id / `output_protocol_id`, not uuid5(MAC).

---

## Task 1: Own the Player (unused)

**Files:**

- Add: `src/sendspin_bridge/services/audio/player/__init__.py`
- Add: `src/sendspin_bridge/services/audio/player/pipeline.py`
- Add: `src/sendspin_bridge/services/audio/player/player.py`
- Add: `tests/unit/services/audio/test_stream_player.py`
- Modify: `.github/workflows/_test.yml` (GStreamer + gobject-introspection)

The daemon does not call this yet. The interface:

```
start(pcm_format)                 # (re)negotiate caps
submit(play_time_us, payload)     # PCM chunk; PTS mapped at push
clear()                           # drop queued audio, keep the pipeline
close_stream()                    # end of stream
set_volume(value, muted)
stop()
is_drained -> bool
metrics() -> dict
```

`play_time_us` is already `SendspinClient.compute_play_time(server_ts)`. PTS:

```
pts_ns = gst_now_ns + (play_time_us − raw_now_us) * 1000
```

`pulsesink` carries `device=<sink name>`, `client-name=<player name>`,
`sync=true`, `provide-clock=false`, `slave-method=skew` (configurable),
`buffer-time` from `PULSE_LATENCY_MSEC`. The bus is polled from asyncio; no
GLib main loop.

Elements are created through an injected factory so tests swap `pulsesink` for
`fakesink`.

**Tests:** real pipeline `appsrc ! audioconvert ! fakesink sync=true`. A buffer
with PTS=T renders at T (TestClock when introspectable, else real clock with
tolerance); a gap wider than `alignment-threshold` counts as a discontinuity;
a format change renegotiates caps; `clear()` empties the queue and leaves the
pipeline alive. Skip the GST tests only when `gi` / Gst cannot be imported.

**CI:** install `gstreamer1.0-plugins-base`, `gstreamer1.0-pulseaudio`,
`gir1.2-gstreamer-1.0`, `libcairo2-dev`, `libgirepository-2.0-dev`, and the
`gstreamer` extra (`PyGObject`) so the Player tests actually run on 3.13.

---

## Task 2: Own the daemon lifecycle and cut `sendspin` (wire still 6.1.1)

**Files:**

- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py`
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py`
- Modify: `src/sendspin_bridge/bridge/client.py` (drop `PULSE_SINK`)
- Modify: `src/sendspin_bridge/services/audio/timing_telemetry.py`
- Move: `filter_supported_call_kwargs` out of `sendspin_compat.py` (still used
  by `device_activation.py`)
- Delete: `tests/unit/services/ipc/test_bridge_daemon_signature.py`
- Modify: `pyproject.toml`, `requirements.txt`, `Dockerfile`,
  `deployment/lxc/install.sh`, HA addon Dockerfiles,
  `.github/workflows/_test.yml`

`BridgeDaemon` stops subclassing `SendspinDaemon`. It composes
`aiosendspin.client.SendspinClient` (still 6.1.1) and the Player.

Copy, do not “un-override”: `run()` is **inherited today**, as are
`_connection_loop` and `_should_switch_to_new_server`. `_connection_watchdog`
is already ours. Replace `get_client_settings` with an in-memory dataclass.
Keep `_HeartbeatListener` (`ClientListener` in 9.1.1 still builds
`WebSocketResponse()` with no heartbeat).

Standby retargets the Player; do not mutate `os.environ["PULSE_SINK"]`.
`amove_pid_sink_inputs` stays as a rescue-streams correction.

`pyproject.toml`: drop `sendspin==7.5.0`, drop `override-dependencies`, drop
`av<17`; pin `aiosendspin==6.1.1` without `[server]`. Dockerfile: add GStreamer
+ PyGObject build deps; stop deleting GStreamer to save 107 MB; drop armv7
`libav*`.

**Tests:** rewrite `test_bridge_daemon_features.py` / `test_daemon_process.py`
off `sys.modules` stubs and `object.__new__`. Add: listener bind failure, two
inbound connections racing. IPC command tests stay.

---

## Task 3: Jump the wire to aiosendspin 9.1.1

**Files:**

- Modify: `bridge_daemon.py`, `daemon_process.py` (`identity`, `pairing_store`,
  chunk listeners, `send_player_state(available=, volume=, muted=)`)
- Add: identity persist `{CONFIG_DIR}/identity/{mac}.key` (`0o600`)
- Add: `FileClientPairingStore.open({CONFIG_DIR}/pairing/{mac}.json)`
- Add: web UI Pair button + PIN on the device card
- Modify: `duplicate_device_check.py` / MA player map — learned id, not uuid5
- Delete: most of `sendspin_compat.py`; keep a small runtime-versions module
  plus an audio-stack report (GStreamer version, pulsesink present, sink
  opened, negotiated caps)
- Modify: `orchestrator.py` (drop `check_sendspin_version_compatibility`)
- Modify: `pyproject.toml` → `aiosendspin==9.1.1`; drop `sounddevice` /
  `libportaudio2`
- Modify: `CHANGELOG.md` `[Unreleased]` for 2.76.0-rc.3 (R5–R9)
- Modify: `CONTEXT.md` — Player, Identity, Pairing store; Daemon no longer
  “sink in the environment”

**Formats:** a fixed FLAC+PCM list. The SDK raises if `player_support`
advertises anything it cannot decode.

**Pairing UX (minimum for the rc):** `PairingSupport(pin_display=…)`, Pair
opens the 300 s window, status shows unpaired / pairing / paired /
`PAIRING_REQUIRED`. `PAIRING_PSK` stays enabled in the store default.

Do **not** rename `static_delay_ms` in config, JS, or HA.

---

## Risks and fallbacks

- **Bluetooth sink jitter.** `slave-method=skew` is the default; `resample` is
  a config switch, not a second engine.
- **PyGObject + python.org 3.13.** Build it in the Docker builder against the
  same GIR. Do not point `PYTHONPATH` at debian `dist-packages`.
- **GLib stalls.** Bus is polled from asyncio; if that blocks, the pipeline
  moves to its own thread behind a queue.
- **First Noise handshake.** Without the Pair button the stand cannot pair.
  Task 3 is incomplete without it.
- **Player-id change.** Document that existing MA players are orphans.

## Verification

Run once on the assembled path, before opening the PR.

1. **Unit:** Player under TestClock (or tolerance); daemon IPC tests rewritten.
2. **CI:** lint and tests on 3.13 with GStreamer + PyGObject; `docker-smoke`.
3. **Live, two-adapter stand, MA 2.10.0:** Pair → PIN → stream → PCM caps in
   the log; audible sync; `playback_sync_error_ms` and discontinuity over 30
   minutes; track / sample-rate change; reconnect without a new PIN; standby /
   wake; volume from MA and AVRCP; addon restart keeps `peer_id`;
   `docker images` size.

## Out of scope

Opus; declared-format tuning; `sendspin-cpp`; renaming delay in the UI;
a feature flag for the old engine.
