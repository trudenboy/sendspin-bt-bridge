# BT playout sync — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Bluetooth speaker in a Music Assistant sync group stays within a few milliseconds of the other members: the A2DP stream stays warm across stream ends, the daemon holds its playout at the PulseAudio sink to the Sendspin clock with single-frame corrections, each speaker's offset beyond the sink is measured once and applied automatically, and every term is visible in status, API, UI and logs.

**Architecture:** A new pure package `services/audio/playout/` (schedule map, port observer, sensor, corrector, loop controller, scheduler, warm hold, stats, engine) driven by a feeder thread in `PlayoutPlayer` (`gst_output.py`), which replaces `StreamPlayer` when `PLAYOUT_SYNC` is `measure` or `loop`. GStreamer runs `sync=false` and only carries audio; the sink's audio clock is read as a sensor. The parent learns the `playout` status, ties idle modes to the hold, and stores per-speaker offsets; group sync tuning gates on warm members and can save its result as an offset.

**Tech Stack:** Python 3.13/3.14 (stdlib; `array` for PCM), PyGObject/GStreamer 1.x (`appsrc`, `audioconvert`, `audioresample`, `capsfilter`, `pulsesink`), `pulsectl_asyncio`, aiosendspin 10.0.2, FastAPI + Pydantic v2, pytest (+ xdist), Vue 3 + TS + vitest.

**Spec:** [`docs/plans/2026-10-16-bt-playout-sync-design.md`](2026-10-16-bt-playout-sync-design.md). Task 1.1's decision note is appended to the spec and read by every later task.

## Global Constraints

Copied from the spec; every task's requirements include them.

* Sign convention: "positive error = **late**, i.e. heard after its scheduled time"; `r > 0` drops frames. `e(t) = t − s(x_port(t))`, `x_port(t) = A(t) − k`, `e_w(t) = t + (x_w − x_port(t)) · (1 + ρ̂) − s(x_w)`.
* Clocks: the error is in `CLOCK_MONOTONIC_RAW`; a sensor reading is a bracket accepted when `t₁ − t₀ ≤ 0.5 ms`, dated at the midpoint, up to 3 tries. `CLOCK_MONOTONIC` only for `clock_slew_ppm`.
* Pipeline in `measure`/`loop`: `appsrc ! audioconvert ! audioresample ! capsfilter(S16LE, 2 ch, sink rate or 48000) ! pulsesink sync=false slave-method=none provide-clock=true buffer-time=PULSE_LATENCY_MSEC`; pipeline clock forced with `use_clock(Gst.SystemClock.obtain())`. `LEAD_MS = 40`, `BLOCK_MS = 10`, feeder tick 5 ms, sensor/controller tick 0.1 s.
* Loop constants: Hampel 9 samples, 3·1.4826·MAD, floor 0.5 ms, step confirmed after 5; EMA `τ_f = 1.0 s`; `B = 0.03 Hz`, `ζ = 0.707`, `Kp = 0.2666 s⁻¹`, `Ki = 0.03553 s⁻²`; dead band 0.1 ms on P only; `R_max = 1000 ppm` with anti-windup; ≤ 1 frame event per 10 ms block; crossfade 16 frames; placement jumps frame-exact at start/clear/delay/underrun/stall; steady jump when 5 consecutive raw `e_w` samples (before the gate) are beyond 10 ms (`loop`) / 20 ms (`measure`) with one sign, size = their median, ≤ 1 per 10 s; after a jump reset EMA and Hampel, keep I, P held off 1 s; saturation warning at |I| > 300 ppm for 60 s.
* Placement of chunks: gap ≤ 2 ms between contiguous chunks is left to the loop; > 2 ms is a placement event. Delay changes (`resync`) always place exactly.
* Warm stream: hold `PLAYOUT_WARM_HOLD_S` (300) after `stream/end`; unlimited while grouped (`group_id` ≠ own id) and `PLAYOUT_WARM_IN_GROUP`, capped by `PLAYOUT_WARM_GROUP_MAX_S` (3600, 0 = no cap); `PLAYOUT_WARMUP_S` (60); at `stream/end` drop queued program audio, mute the PA stream, feed silence, unmute when `x_port ≥ x_end + 20 ms`; `keep_alive` holds feed the `keep_alive_method` signal.
* Start lead: declared `required_lead_time_ms` = `max(configured, PULSE_LATENCY_MSEC + LEAD_MS + 100)` in `measure`/`loop`.
* 3.1.0-beta.5 delay semantics stay: 150 ms coalescing in the daemon, one `player.resync(shift_us)` per burst, "audio whose new time has already passed is dropped: the cost is a short silence, never misaligned audio". `resync` returns whether something was playing.
* `PLAYOUT_SYNC=off` is 3.1.0-beta.5 behaviour; `DaemonArgs()` defaults keep `off`, so existing daemon tests keep their meaning. Never edit an existing test to make it pass (`CLAUDE.md`).
* Speaker offsets: never stored from `off` mode; key (codec, adapter MAC); ≤ 8 entries per player; precedence manual/MA-newer > acoustic > `bluez_delay_report` (PulseAudio only) > codec fallback (first start only); a Delay Report on PipeWire contributes 0 ms (`device_report_in_sink`); sub-ms remainder via `set_playout_trim_us` (−500…500).
* The `sendspin_sync` core is not changed (its boundary test must stay green).
* Config keys go into `DEFAULT_CONFIG`, `config/schema.json`, `BridgeConfig` in `application/models/config.py`, `_ALLOWED_POST_CONFIG_KEYS` (+ POST normalisation) in `application/config.py`, the `web_ui_only_keys` tuple in `scripts/translate_ha_config.py` and `services/infrastructure/config_diff.py`.
* Logs: INFO for output open/close, hold start/end, jumps, offset applied, fallback; WARNING rate-limited to once per 10 min per kind; DEBUG 10 s summaries (1 s with `SENDSPIN_PLAYOUT_DEBUG=1`).
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (R5–R9). TDD: red before green.
* Do not touch the bridge on port 8080 or Music Assistant on 8095/8097 while developing; live work uses the test stand (Stage 1 Task 1.1, Stage 5).

## Review Focus

1. **The sensor and its mapping** (`x_port = A − k`, fill, underrun and stall rules, `e_w` prediction) — a wrong sign or a double-counted in-flight correction makes the loop run away. Check the simulation tests drive the real `PlayoutEngine` (not a copy of its maths) and cover underrun, PA step and stall. (Tasks 1.5, 1.6, 1.11, 3.3, 3.4)
2. **`stream/end` without teardown** — Pause in MA must silence the speaker within 100 ms and drop the queued audio (#464), with no flush event anywhere, and the tail mute must compose with the user's stream mute under `volume_controller=sendspin`. (Tasks 2.3, 2.5, 2.6)
3. **Delay-change semantics** — the 3.1.0-beta.5 daemon tests stay unchanged and green; in `loop`, `resync` re-places frame-exactly, drops passed audio, never rebuilds the pipeline. (Tasks 1.10, 3.6)
4. **Idle modes vs the hold** — power_save never suspends a sink under a running stream (release first), a grouped hold defers power_save but not auto_disconnect, keep-alive bursts do not double up, and `off` mode's idle behaviour is unchanged. (Task 2.7)
5. **Offset provenance** — an offset is never stored from `off` mode or from a group without a reference-grade member; a manual or MA delay newer than the entry is never overwritten; PipeWire's Delay Report is never added twice. (Tasks 4.2, 4.3, 4.5)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/playout/__init__.py`, `timeline.py`, `sensor.py`, `stats.py`, `controller.py`, `corrector.py`, `scheduler.py`, `warm.py`, `engine.py`, `gst_output.py` | **new** playout engine |
| `src/sendspin_bridge/services/audio/pa_stream_latency.py` | **new** sink-input latency probe |
| `src/sendspin_bridge/services/audio/speaker_offsets.py` | **new** offset entries + resolver (Stage 4) |
| `src/sendspin_bridge/services/audio/player/pipeline.py` | `playout_pulsesink_factory`, `build_playout_pipeline` |
| `src/sendspin_bridge/services/audio/timing_telemetry.py` | legacy extras (`pa_latency_ms`, `clock_slew_ppm`) |
| `src/sendspin_bridge/services/ipc/bridge_daemon.py`, `daemon_process.py`, `commands.py` | mode selection, `end_stream`, hold/trim commands, status `playout` |
| `src/sendspin_bridge/bridge/client.py`, `bridge/idle_machine.py` | `DeviceStatus.playout`, spawn params, idle interplay, offset apply |
| `src/sendspin_bridge/config/__init__.py`, `config/schema.json`, `config/players.py`, `scripts/translate_ha_config.py`, `application/models/config.py`, `application/config.py`, `services/infrastructure/config_diff.py` | keys |
| `src/sendspin_bridge/application/models/devices.py`, `api/routers/devices.py`, `application/devices.py` | `PlayoutTiming`, offsets API |
| `src/sendspin_bridge/application/sync_tuning.py`, `application/models/sync.py`, `api/routers/sync.py`, `docs/contracts/sync-contract-v1.json` | warm gate, offset candidates |
| `ui/src/utils/deviceChips.ts`, `ui/src/components/devices/chips/SyncChip.vue`, `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/settings/layout.ts`, `ui/src/components/sync/StepClicker.vue`, `ui/src/components/sync/StepTune.vue`, `ui/src/i18n/{en,ru}.json`, `ui/src/api/*` | UI |
| `tests/unit/services/audio/playout/*` (incl. `sim.py`), `tests/unit/services/audio/test_pa_stream_latency.py`, `tests/unit/services/audio/test_speaker_offsets.py`, `tests/unit/services/ipc/test_bridge_daemon_playout.py`, `tests/unit/bridge/test_idle_machine_hold.py`, `tests/unit/api/test_sync_playout.py`, `tests/integration/test_playout_pulsesink.py` | **new** tests |
| `scripts/sync_phase_trace.py`, `scripts/render_correction_abx.py` | **new** validation tools (Stage 5) |
| `docs-site/src/content/docs/{,ru/}group-sync-tuning.md`, `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `docs-site/src/content/docs/{,ru/}configuration.md`, `CHANGELOG.md` | docs |

Conventions: Python tests under `tests/unit/...`; GStreamer tests use the `requires_gst` marker pattern from `tests/unit/services/audio/test_stream_player.py`; parent fixtures from `tests/conftest.py` (`api_client`, `tmp_config`, `bridge_loop`). Use cases raise `UseCaseError(status, code, detail)`; routers use `Depends(require_principal)` and `PROBLEM_RESPONSES`. Run one file with `uv run pytest <path> -q -p no:cacheprovider -n0`.

---

## Stage 1 — Telemetry and measurement only

Shippable: `PLAYOUT_SYNC` defaults to `off` (unchanged playback, new legacy telemetry); `measure` is an opt-in that runs the new engine with placement and 20 ms jumps only.

### Task 1.1: Sensor spike on the stand and decision note (throwaway)

**Files:**
- Create (not committed): `scratch/playout_spike/probe.py`
- Modify: `docs/plans/2026-10-16-bt-playout-sync-design.md` (append "Task 1.1 decision note")

**Interfaces:**
- Consumes: the HAOS stand (add-on shell with `pactl`, a connected BT speaker), the Docker VM with PipeWire, a dev machine with a wired output; a phone with the click track playing through a throwaway pipeline.
- Produces: the decision note answering every **[verify]** in the spec's "Facts" and the H1–H4 quick data. Tasks 1.6, 1.7, 1.12 and 4.2 branch on it.

- [ ] **Step 1: Probe script.** `scratch/playout_spike/probe.py` builds the spec pipeline (`sync=false`, `provide-clock=true`, `slave-method=none`, forced system clock, `buffer-time` 600 ms), feeds the 180 s click track (`sendspin_sync.click_track.render_pcm()`) in 10 ms blocks keeping `appsrc` at 40 ms, and every 100 ms prints `t_raw_us, A_ns, mono_us, appsrc_level_ns, written_ns`:

```python
import time, threading
from sendspin_bridge.services.audio.player.gst_support import Gst
from sendspin_sync.click_track import render_pcm

RAW = time.CLOCK_MONOTONIC_RAW
pcm = render_pcm()
pipe = Gst.parse_launch(
    "appsrc name=src is-live=true format=time caps=audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels=2 "
    "! audioconvert ! audioresample ! capsfilter caps=audio/x-raw,format=S16LE,rate=48000,channels=2 "
    "! pulsesink name=sink sync=false provide-clock=true slave-method=none buffer-time=600000"
)
pipe.use_clock(Gst.SystemClock.obtain())
src, sink = pipe.get_by_name("src"), pipe.get_by_name("sink")
pipe.set_state(Gst.State.PLAYING)
block, pos, written = 1920, 0, 0
def feeder():
    global pos, written
    while pos < len(pcm):
        while int(src.get_property("current-level-time")) < 40_000_000:
            buf = Gst.Buffer.new_wrapped(pcm[pos:pos + block * 4]); buf.pts = written; buf.duration = 10_000_000
            src.emit("push-buffer", buf); pos += block * 4; written += 10_000_000
        time.sleep(0.005)
threading.Thread(target=feeder, daemon=True).start()
clock = None
while pos < len(pcm):
    clock = clock or sink.provide_clock()
    t0 = time.clock_gettime_ns(RAW); a = clock.get_internal_time() if clock else -1; t1 = time.clock_gettime_ns(RAW)
    print(t0 // 1000, (t1 - t0) // 1000, a, time.monotonic_ns() // 1000, int(src.get_property("current-level-time")), written)
    time.sleep(0.1)
```

- [ ] **Step 2: E1 — the audio clock exists and advances** on the HAOS bluez5 sink and on pipewire-pulse: `provide_clock()` non-NULL after the first second; `A` advances at 1.000 ± 0.0005 of RAW; bracket p99 < 0.5 ms; note the noise σ of `A − t` after removing a linear fit (expected 0.1–1 ms) — this sizes the Hampel floor (keep 0.5 ms if σ ≤ 0.3 ms; else set floor = 2σ and record it).

- [ ] **Step 3: E2 — origin `k₀`.** On the wired output (dev machine), record the speaker with the phone while the script runs and compute, for clicks at known `x`, `t_heard − t(x_port = x)` with `k = 0`: expected a constant equal to the DAC + air latency (a few ms), no dependence on `buffer-time` (repeat with 200 and 800 ms). Record `k₀` rule confirmed or the correction found.

- [ ] **Step 4: E3 — underrun semantics.** `kill -STOP` the probe for 800 ms with `buffer-time` 200 ms; after `kill -CONT`, check `A` kept advancing during the gap (PA played silence) and that the fill rule (`written − level + k − A < 0`) flags it; check the post-gap clicks are heard late by the deficit (phone).

- [ ] **Step 5: E4 — Delay Report.** On HAOS: `busctl introspect org.bluez /org/bluez/hci0/dev_XX/fdN org.bluez.MediaTransport1` for each connected speaker; record whether `Delay` exists. On the PipeWire VM with a delay-reporting device (if any in the fleet): `pactl list sink-inputs` latency with and without the device's delay (compare `Sink Latency` with `Delay/10`).

- [ ] **Step 6: E5 — group id.** On MA dev, with a sync group of a bridge player + kiosk: log the daemon's `group/update` `group_id` with the sync group powered on and off (the daemon already logs "Group update: id=…").

- [ ] **Step 7: E6 — H3/H4 quick data.** On HAOS: 60 s of `CLOCK_MONOTONIC` vs `CLOCK_MONOTONIC_RAW` in the add-on (ppm); with `PLAYOUT_SYNC=off` read `backend_output_latency_ms` at `PULSE_LATENCY_MSEC` 400 and 800 and the ENEBY acoustic offset vs kiosk at both (phone): does the offset move by ≈ the latency difference?

- [ ] **Step 8: Decision note.** Append to the spec:

```markdown
## Task 1.1 decision note (YYYY-MM-DD)

- Audio clock on bluez5 (PA 17): available yes/no; rate vs RAW … ppm; noise σ … ms; bracket p99 … µs.
- Audio clock on pipewire-pulse: …
- k₀ rule: confirmed / corrected to …
- Underrun: A advances during starvation yes/no; fill rule detects it yes/no; post-gap lateness = deficit ± … ms.
- Delay Report: HAOS PA: absent/present (devices: …); PipeWire: included in sink latency yes/no.
- group/update while a sync group is powered: group_id = … / powered off: …
- MONO vs RAW on HAOS: … ppm. Legacy offset vs PULSE_LATENCY (400→800): moved … ms (H4 confirmed/refuted).
- Changes to the plan: …
```

Branching: if E1 fails on PA → Stage 1 keeps `measure` PipeWire-only and the spec's fallback (legacy) on PA, and the plan pauses for a design change (the sensor would come from `PaStreamLatencyProbe` + frame accounting). If E4 finds `Delay` on HAOS → Task 4.2 keeps rule 3 enabled (the default code); else rule 3 stays for completeness. If E5 shows no distinct group id → Task 2.6 takes "grouped" from the parent's MA group cache (alternative code given there).

- [ ] **Step 9: Commit** only the spec: `git add docs/plans/2026-10-16-bt-playout-sync-design.md && git commit -m "docs(sync): playout sync — Task 1.1 decision note"`; delete `scratch/`.

### Task 1.2: `PLAYOUT_SYNC` config key

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`DEFAULT_CONFIG`), `src/sendspin_bridge/config/schema.json`, `src/sendspin_bridge/application/models/config.py` (`BridgeConfig`), `src/sendspin_bridge/application/config.py` (`_ALLOWED_POST_CONFIG_KEYS` and the POST normalisation next to `PULSE_LATENCY_MSEC`), `scripts/translate_ha_config.py` (the local `web_ui_only_keys` tuple), `src/sendspin_bridge/services/infrastructure/config_diff.py` (`_GLOBAL_RESTART_FIELDS`)
- Test: `tests/unit/config/test_playout_config.py`, `tests/integration/test_translate_ha_config.py` (one new test)

**Interfaces:**
- Produces: `load_config()["PLAYOUT_SYNC"] in {"off","measure","loop"}`, default `"off"`; `PUT /api/v1/config` accepts it and refuses other values; a change classifies as `ActionKind.GLOBAL_RESTART`; it survives an add-on restart.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/config/test_playout_config.py
import json
from pathlib import Path

import pytest

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"


def test_playout_sync_defaults_to_off():
    assert DEFAULT_CONFIG["PLAYOUT_SYNC"] == "off"
    assert "PLAYOUT_SYNC" in CONFIG_ALLOWED_KEYS


def test_schema_restricts_playout_sync():
    prop = json.loads(SCHEMA.read_text())["properties"]["PLAYOUT_SYNC"]
    assert prop["enum"] == ["off", "measure", "loop"] and prop["default"] == "off"


def test_config_model_rejects_unknown_mode():
    from pydantic import ValidationError
    from sendspin_bridge.application.models.config import BridgeConfig

    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"PLAYOUT_SYNC": "fast"})


def test_mode_change_restarts_daemons():
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs

    actions = diff_configs({"PLAYOUT_SYNC": "off"}, {"PLAYOUT_SYNC": "loop"})
    assert [a.kind for a in actions] == [ActionKind.GLOBAL_RESTART]


def test_post_config_accepts_the_key():
    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS

    assert "PLAYOUT_SYNC" in _ALLOWED_POST_CONFIG_KEYS
```

```python
# tests/integration/test_translate_ha_config.py (new test, same pattern as test_zero_latency_preserved)
def test_playout_sync_preserved_across_addon_restart(tmp_path):
    _write_json(tmp_path / "options.json", _minimal_options())
    _write_json(tmp_path / "config.json", {"PLAYOUT_SYNC": "loop"})
    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()
    assert _read_json(tmp_path / "config.json")["PLAYOUT_SYNC"] == "loop"
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/config/test_playout_config.py tests/integration/test_translate_ha_config.py -q -p no:cacheprovider -n0` → FAIL.
- [ ] **Step 3: Implement** — `"PLAYOUT_SYNC": "off"` next to `PULSE_LATENCY_MSEC` in `DEFAULT_CONFIG` with a comment pointing to the spec; schema `{"type": "string", "enum": ["off","measure","loop"], "default": "off"}`; `BridgeConfig.PLAYOUT_SYNC: Literal["off","measure","loop"] | None = "off"`; `_ALLOWED_POST_CONFIG_KEYS` + an enum check in the POST normalisation (400 `invalid_config` like the other enums); `_GLOBAL_RESTART_FIELDS`; `web_ui_only_keys`.
- [ ] **Step 4: Run** the two files and `uv run pytest tests/unit/config -q -p no:cacheprovider` → PASS (`tests/unit/config/test_config.py` checks every `DEFAULT_CONFIG` key is in the schema).
- [ ] **Step 5: Commit** `feat(config): PLAYOUT_SYNC switch for the playout engine (off by default)`.

### Task 1.3: `ClockSlewEstimator` and legacy telemetry `clock_slew_ppm`

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/__init__.py` (empty `__all__`), `src/sendspin_bridge/services/audio/playout/stats.py`
- Modify: `src/sendspin_bridge/services/ipc/daemon_process.py` (`_timing_telemetry_watcher`)
- Test: `tests/unit/services/audio/playout/test_stats.py`

**Interfaces:**

```python
# stats.py
class LinearTrend:
    """Least-squares slope of y(t) over a sliding time window."""
    def __init__(self, window_s: float) -> None: ...
    def add(self, t_s: float, y: float) -> None: ...
    def slope(self) -> float | None   # y units per second; None with < 3 points or < window/4 span
    def reset(self) -> None: ...

class ClockSlewEstimator:
    """CLOCK_MONOTONIC rate against CLOCK_MONOTONIC_RAW, in ppm (60 s window)."""
    def __init__(self, *, raw_ns=..., mono_ns=..., window_s: float = 60.0) -> None: ...
    def sample(self) -> None: ...
    def ppm(self) -> float | None: ...
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_stats.py
import pytest
from sendspin_bridge.services.audio.playout.stats import ClockSlewEstimator, LinearTrend


def test_trend_slope_of_a_line():
    tr = LinearTrend(window_s=60)
    for i in range(61):
        tr.add(float(i), 3.0 + 0.5 * i)
    assert tr.slope() == pytest.approx(0.5)


def test_trend_forgets_points_outside_the_window():
    tr = LinearTrend(window_s=10)
    for i in range(10):
        tr.add(float(i), 0.0)
    for i in range(10, 30):
        tr.add(float(i), float(i))
    assert tr.slope() == pytest.approx(1.0)


def test_trend_needs_enough_span():
    tr = LinearTrend(window_s=60)
    tr.add(0.0, 0.0); tr.add(1.0, 1.0)
    assert tr.slope() is None


def test_slew_of_a_clock_running_500_ppm_fast():
    raw = [0]
    est = ClockSlewEstimator(raw_ns=lambda: raw[0], mono_ns=lambda: int(raw[0] * 1.0005))
    for _ in range(61):
        est.sample()
        raw[0] += 1_000_000_000
    assert est.ppm() == pytest.approx(500.0, abs=1.0)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** with a `deque` of `(t, y)`; slope by the closed-form least squares; `ppm = (slope of (mono − raw) vs raw) × 1e6`. **Step 4:** In `_timing_telemetry_watcher`, keep one `ClockSlewEstimator`, call `sample()` every loop (1 s) and add `clock_slew_ppm: round(ppm, 1)` to the patched snapshot; add `clock_slew_ppm: float | None = None` to `DeviceStatus` and `DeviceTiming` (`clock_slew_ppm`) — test in `tests/unit/services/ipc/test_daemon_process.py`-style new file `tests/unit/services/ipc/test_timing_watcher_slew.py` asserting the key reaches the status after two ticks with a fake estimator injected through a module-level factory `_make_slew_estimator`.
- [ ] **Step 5: Run** both files → PASS. **Step 6: Commit** `feat(telemetry): report CLOCK_MONOTONIC slew against the Sendspin clock`.

### Task 1.4: `PaStreamLatencyProbe` and `pa_latency_ms`

**Files:**
- Create: `src/sendspin_bridge/services/audio/pa_stream_latency.py`
- Modify: `daemon_process.py` (telemetry watcher), `bridge/client.py` (`DeviceStatus.pa_latency_ms`), `application/models/devices.py` (`DeviceTiming.pa_latency_ms`)
- Test: `tests/unit/services/audio/test_pa_stream_latency.py`

**Interfaces:**

```python
async def read_own_stream_latency_ms(pid: int, *, pulse_factory=None) -> float | None:
    """buffer_usec + sink_usec of this process' sink-input(s) (max if several), ms; None if none."""
```

- [ ] **Step 1: Failing tests** with a fake `pulsectl_asyncio` context (`sink_input_list()` returning objects with `proplist={"application.process.id": "1234"}`, `buffer_usec`, `sink_usec`):

```python
import pytest
from types import SimpleNamespace
from sendspin_bridge.services.audio.pa_stream_latency import read_own_stream_latency_ms


class FakePulse:
    def __init__(self, inputs): self._inputs = inputs
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def sink_input_list(self): return self._inputs


def si(pid, buf, sink):
    return SimpleNamespace(proplist={"application.process.id": str(pid)}, buffer_usec=buf, sink_usec=sink)


@pytest.mark.asyncio
async def test_sums_buffer_and_sink_latency_of_own_stream():
    ms = await read_own_stream_latency_ms(1234, pulse_factory=lambda: FakePulse([si(99, 1, 1), si(1234, 560_000, 27_000)]))
    assert ms == pytest.approx(587.0)


@pytest.mark.asyncio
async def test_none_without_own_stream():
    assert await read_own_stream_latency_ms(1234, pulse_factory=lambda: FakePulse([si(99, 1, 1)])) is None


@pytest.mark.asyncio
async def test_none_when_pulse_is_unreachable():
    def boom(): raise OSError("no server")
    assert await read_own_stream_latency_ms(1234, pulse_factory=boom) is None
```

- [ ] **Step 2–4:** Implement (default factory `pulsectl_asyncio.PulseAsync("sendspin-latency")`, every exception → `None`, DEBUG log once); the watcher reads it at most once per second while playing; status key `pa_latency_ms`. Run → PASS.
- [ ] **Step 5: Commit** `feat(telemetry): report the PulseAudio stream latency of each player`.

### Task 1.5: `ScheduleMap`

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/timeline.py`
- Test: `tests/unit/services/audio/playout/test_schedule_map.py`

**Interfaces:**

```python
SegmentKind = Literal["program", "silence"]

@dataclass(frozen=True, slots=True)
class Segment:
    x0_ns: int        # output stream time where the segment starts
    s0_us: float      # RAW time (µs) at which x0 is scheduled to leave the port
    kind: SegmentKind

class ScheduleMap:
    def __init__(self, *, max_segments: int = 4096) -> None: ...
    def append(self, x0_ns: int, s0_us: float, kind: SegmentKind) -> None:
        """x0 must not go backwards; an append at the same x0 replaces the last segment."""
    def at(self, x_ns: int) -> float | None:      # s(x) = s0 + (x − x0)/1000
    def kind_at(self, x_ns: int) -> SegmentKind | None: ...
    def prune_before(self, x_ns: int) -> None:    # keeps the segment containing x
    def __len__(self) -> int: ...
```

- [ ] **Step 1: Failing tests**

```python
import pytest
from sendspin_bridge.services.audio.playout.timeline import ScheduleMap


def test_linear_inside_a_segment():
    m = ScheduleMap()
    m.append(0, 1_000_000.0, "silence")
    assert m.at(0) == 1_000_000.0
    assert m.at(10_000_000) == pytest.approx(1_010_000.0)


def test_lookup_picks_the_segment_containing_x():
    m = ScheduleMap()
    m.append(0, 0.0, "silence")
    m.append(20_000_000, 50_000.0, "program")   # placement: program starts 30 ms later than silence would
    assert m.at(19_999_000) == pytest.approx(19_999.0)
    assert m.at(20_000_000) == 50_000.0
    assert m.kind_at(25_000_000) == "program"


def test_before_first_segment_is_unknown():
    m = ScheduleMap()
    assert m.at(0) is None
    m.append(1000, 0.0, "program")
    assert m.at(0) is None


def test_x_cannot_go_backwards():
    m = ScheduleMap()
    m.append(1000, 0.0, "program")
    with pytest.raises(ValueError):
        m.append(999, 0.0, "program")


def test_prune_keeps_the_containing_segment_and_bounds_memory():
    m = ScheduleMap(max_segments=8)
    for i in range(100):
        m.append(i * 1_000_000, float(i), "program")
    assert len(m) <= 8
    m.prune_before(95_500_000)
    assert m.at(95_500_000) is not None
```

- [ ] **Step 2–4:** Implement with two parallel lists and `bisect_right`; when over `max_segments`, drop the oldest (lookups before the oldest return `None`). Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): schedule map from output stream time to the Sendspin clock`.

### Task 1.6: `PortObserver`

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/timeline.py`
- Create: `src/sendspin_bridge/services/audio/playout/sensor.py` (only `PortSample` in this task)
- Test: `tests/unit/services/audio/playout/test_port_observer.py`

**Interfaces:**

```python
# sensor.py
@dataclass(frozen=True, slots=True)
class PortSample:
    t_us: float       # RAW midpoint of the bracket
    a_ns: int         # audio clock internal time
    mono_us: float
    bracket_us: float

# timeline.py
@dataclass(frozen=True, slots=True)
class PortReading:
    t_us: float
    x_port_ns: int
    fill_ns: int
    underrun: bool     # a new underrun episode began at this reading
    stalled: bool
    recovered: bool    # first reading after a stall or an underrun

class PortObserver:
    def __init__(self, *, k0_ns: int = 0, stall_ms: float = 300.0) -> None: ...
    def observe(self, sample: PortSample, delivered_ns: int) -> PortReading: ...
    @property
    def k_ns(self) -> int: ...
```

Rules (spec "Measurement"): `fill = delivered + k − a`; `fill < 0` → `k += −fill`, `underrun=True` once per episode, `x_port = delivered`; `a` advancing ≤ 0.1 ms over ≥ `stall_ms` of `t` → `stalled=True`; the first reading after either → `recovered=True`.

- [ ] **Step 1: Failing tests**

```python
from sendspin_bridge.services.audio.playout.sensor import PortSample
from sendspin_bridge.services.audio.playout.timeline import PortObserver


def s(t_ms, a_ms):
    return PortSample(t_us=t_ms * 1000.0, a_ns=int(a_ms * 1e6), mono_us=t_ms * 1000.0, bracket_us=20.0)


def test_port_position_is_audio_clock_minus_origin():
    obs = PortObserver(k0_ns=2_000_000)
    r = obs.observe(s(1000, 500), delivered_ns=1_100_000_000)
    assert r.x_port_ns == 498_000_000
    assert r.fill_ns == 1_100_000_000 + 2_000_000 - 500_000_000


def test_underrun_moves_the_origin_by_the_deficit_once():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    r = obs.observe(s(1000, 1000), delivered_ns=900_000_000)    # port passed our data by 100 ms
    assert r.underrun and r.x_port_ns == 900_000_000
    assert obs.k_ns == 100_000_000
    r2 = obs.observe(s(1100, 1100), delivered_ns=1_400_000_000)
    assert not r2.underrun and r2.recovered
    assert r2.x_port_ns == 1_000_000_000


def test_pa_skip_moves_the_port_forward_without_touching_the_origin():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    r = obs.observe(s(100, 105), delivered_ns=700_000_000)      # 5 ms of our frames dropped by PA
    assert obs.k_ns == 0 and r.x_port_ns == 105_000_000 and not r.underrun


def test_stall_is_flagged_after_300_ms_without_progress():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    assert not obs.observe(s(200, 0), delivered_ns=600_000_000).stalled
    assert obs.observe(s(350, 0), delivered_ns=600_000_000).stalled
    assert obs.observe(s(450, 100), delivered_ns=600_000_000).recovered
```

- [ ] **Step 2–4:** Implement. Run → PASS. (If Task 1.1 changed the `k₀` rule, encode it in the constructor's caller — Task 1.12 — not here.)
- [ ] **Step 5: Commit** `feat(playout): port observer — port position, fill, underruns and stalls`.

### Task 1.7: `GstPortClock` sensor

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/sensor.py`
- Test: `tests/unit/services/audio/playout/test_sensor.py`, `tests/integration/test_playout_pulsesink.py`

**Interfaces:**

```python
class PortClock(Protocol):
    def read(self) -> PortSample | None: ...

class GstPortClock:
    MAX_BRACKET_US = 500.0
    TRIES = 3
    def __init__(self, sink: object, *, raw_ns: Callable[[], int] = _raw_ns, mono_ns: Callable[[], int] = time.monotonic_ns) -> None: ...
    def read(self) -> PortSample | None:
        """None while the sink provides no clock or every try exceeded the bracket; rejected tries are counted."""
    @property
    def rejected(self) -> int: ...
```

- [ ] **Step 1: Failing tests** with a fake sink whose `provide_clock()` returns a fake clock (`get_internal_time()`), and a scripted `raw_ns` that makes the first try's bracket 800 µs and the second 100 µs:

```python
from sendspin_bridge.services.audio.playout.sensor import GstPortClock


class FakeClock:
    def __init__(self, t): self.t = t
    def get_internal_time(self): return self.t


class FakeSink:
    def __init__(self, clock): self.clock = clock
    def provide_clock(self): return self.clock


def test_takes_the_first_tight_bracket():
    raws = iter([0, 800_000, 1_000_000, 1_100_000])
    sensor = GstPortClock(FakeSink(FakeClock(5_000_000)), raw_ns=lambda: next(raws), mono_ns=lambda: 7)
    sample = sensor.read()
    assert sample.t_us == 1050.0 and sample.a_ns == 5_000_000 and sample.bracket_us == 100.0
    assert sensor.rejected == 1


def test_no_clock_means_no_sample():
    assert GstPortClock(FakeSink(None)).read() is None


def test_gst_clock_time_none_is_no_sample():
    from sendspin_bridge.services.audio.playout.sensor import CLOCK_TIME_NONE
    assert GstPortClock(FakeSink(FakeClock(CLOCK_TIME_NONE))).read() is None
```

- [ ] **Step 2–4:** Implement (`CLOCK_TIME_NONE = 2**64 - 1`, same as `Gst.CLOCK_TIME_NONE`, defined locally so the module imports without GStreamer). Run → PASS.
- [ ] **Step 5: Integration test** `tests/integration/test_playout_pulsesink.py`, `@pytest.mark.integration`, skipped unless `pactl info` succeeds: load `module-null-sink sink_name=playout_test`, run the Task 1.1 pipeline against it for 10 s with silence, assert `read()` succeeds ≥ 90 times and the slope of `a_ns/1000 − t_us` over the run is < 200 ppm. Run locally: `uv run pytest tests/integration/test_playout_pulsesink.py -m integration -q -p no:cacheprovider -n0`.
- [ ] **Step 6: Commit** `feat(playout): read the PulseAudio sink's audio clock as a bracketed sensor`.

### Task 1.8: `PlayoutStats`

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/stats.py`
- Test: `tests/unit/services/audio/playout/test_stats.py` (extend)

**Interfaces:**

```python
class PlayoutStats:
    def __init__(self, *, window_s: float = 60.0, rate_window_s: float = 10.0) -> None: ...
    def error(self, t_s: float, e_us: float) -> None: ...        # filtered error per tick
    def port(self, t_s: float, a_minus_t_us: float) -> None: ... # for sink_rate_ppm
    def correction(self, t_s: float, inserted: int, dropped: int) -> None: ...
    def jump(self, at_iso: str, size_us: float, reason: str) -> None: ...
    def underrun(self) -> None: ...
    def pa_step(self) -> None: ...
    def rejected(self) -> None: ...
    def reset_window(self) -> None: ...                           # after a placement jump
    def snapshot(self) -> dict[str, object]:
        """error_ms, error_p95_ms, error_slope_ms_per_s, corrections_per_s, inserted, dropped,
        jumps, last_jump_ms, last_jump_reason, last_jump_at, underruns, pa_steps,
        rejected_samples, sink_rate_ppm (rounded: 3 decimals for ms, 1 for ppm)."""
```

- [ ] **Step 1: Failing tests:** p95 of `[0.1]*95 + [5.0]*5` ms is ≤ 5.0 and ≥ 0.1 (nearest-rank); slope of a ramp of 0.2 ms/s reads 0.2; `corrections_per_s` over 10 s with 30 drops = 3.0; `reset_window` clears p95/slope but keeps counters; `sink_rate_ppm` of `a − t` growing 100 µs/s = 100.0.
- [ ] **Step 2–4:** Implement on `LinearTrend` and a bounded deque. Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): telemetry aggregates for the playout error`.

### Task 1.9: `LoopController` — gate, filter, jumps (measure mode)

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/controller.py`
- Test: `tests/unit/services/audio/playout/test_controller.py`

**Interfaces:**

```python
T_S = 0.1
HAMPEL_N, HAMPEL_K, HAMPEL_FLOOR_US, STEP_CONFIRM = 9, 3.0, 500.0, 5
TAU_F_S = 1.0
JUMP_US = {"measure": 20_000.0, "loop": 10_000.0}
JUMP_CONFIRM = 5
JUMP_MIN_INTERVAL_S = 10.0
HOLDOFF_S = 1.0
Mode = Literal["measure", "loop"]

@dataclass(frozen=True, slots=True)
class Decision:
    rate: float                 # s/s; + drops (catch up when late); always 0.0 in measure
    jump_us: float | None       # + drop this much, − insert this much; None = no jump
    error_us: float | None      # filtered error (EMA), None before the first accepted sample
    rejected: bool
    saturated: bool
    degraded: bool              # a steady jump was refused by the 10 s interval

class LoopController:
    def __init__(self, *, mode: Mode) -> None: ...
    def update(self, t_s: float, e_w_us: float) -> Decision: ...
    def placed(self, t_s: float, residual_us: float = 0.0) -> None:
        """A placement or steady jump happened: reset EMA and Hampel to residual, hold P off."""
    @property
    def rate_estimate(self) -> float: ...   # integrator I (ρ̂), 0.0 in measure
```

- [ ] **Step 1: Failing tests (measure mode)**

```python
import pytest
from sendspin_bridge.services.audio.playout.controller import LoopController


def run(ctrl, samples, t0=0.0):
    out = []
    for i, e in enumerate(samples):
        out.append(ctrl.update(t0 + 0.1 * i, e))
    return out


def test_measure_never_corrects_small_errors():
    d = run(LoopController(mode="measure"), [3000.0] * 50)
    assert all(x.rate == 0.0 and x.jump_us is None for x in d)
    assert d[-1].error_us == pytest.approx(3000.0, rel=0.05)


def test_single_outlier_is_rejected():
    d = run(LoopController(mode="measure"), [100.0] * 20 + [9000.0] + [100.0] * 5)
    assert d[20].rejected
    assert d[-1].error_us == pytest.approx(100.0, abs=20)


def test_persistent_step_over_the_threshold_jumps_once():
    ctrl = LoopController(mode="measure")
    d = run(ctrl, [0.0] * 20 + [25_000.0] * 10)
    jumps = [x.jump_us for x in d if x.jump_us is not None]
    assert jumps == [pytest.approx(25_000.0, rel=0.01)]


def test_second_steady_jump_within_10_s_is_refused_and_marks_degraded():
    ctrl = LoopController(mode="measure")
    run(ctrl, [0.0] * 20 + [25_000.0] * 10)
    ctrl.placed(3.0)
    d = run(ctrl, [-25_000.0] * 10, t0=3.1)
    assert all(x.jump_us is None for x in d) and d[-1].degraded
```

- [ ] **Step 2–4:** Implement the gate (median/MAD over the last `HAMPEL_N` accepted values; a run of `STEP_CONFIRM` outside → accept and reseed the window), the EMA (`α = T_S/TAU_F_S`), the steady-jump counter (`JUMP_CONFIRM` consecutive raw `e_w` beyond `JUMP_US[mode]` with one sign, counted before the gate; the jump size is their median, not the EMA; after issuing a jump the controller resets itself as `placed()` would, assuming the engine executes it), the interval rule; rate stays 0.0 (PI added in Task 3.2). Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): error gate, filter and jump decision`.

### Task 1.10: `PlayoutScheduler` — contiguous output, silence, placement, end/clear/resync

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/scheduler.py`
- Test: `tests/unit/services/audio/playout/test_scheduler.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class InputFormat:
    sample_rate: int
    channels: int = 2
    sample_bytes: int = 2
    def frame_bytes(self) -> int: ...

@dataclass(frozen=True, slots=True)
class Block:
    pcm: bytes
    x0_ns: int
    duration_ns: int
    program: bool          # any program frame in the block
    dropped: int           # frames removed by corrections or a jump
    inserted: int          # frames added by corrections or a jump
    late_drop_us: float    # head of a placed chunk dropped because it was already due

class PlayoutScheduler:
    GAP_PLACEMENT_US = 2_000
    def __init__(self, *, schedule: ScheduleMap, block_ms: int = 10, fill: Callable[[InputFormat, int], bytes] | None = None) -> None: ...
    def set_format(self, fmt: InputFormat) -> None: ...
    def submit(self, play_time_us: int, pcm: bytes) -> None: ...
    def clear(self) -> int: ...                      # drops queued program audio, returns x_w (x_end)
    def shift(self, shift_us: int) -> bool: ...      # moves queued program audio; True if any was queued
    def request_placement(self, reason: str) -> None: ...
    def render(self, *, audible_us: float, now_us: float, rate: float = 0.0, jump_us: float | None = None) -> Block:
        """One block at the write head. ``audible_us`` is the predicted RAW time at which the write
        head will leave the port (engine: t + (x_w − x_port)(1 + ρ̂)). Placement aligns the next
        program frame to it exactly; ``rate``/``jump_us`` arrive in Stage 3."""
    @property
    def x_w_ns(self) -> int: ...
    @property
    def queued(self) -> bool: ...
    @property
    def last_placement_reason(self) -> str | None: ...
```

Behaviour (spec "Data flow", "Measurement", "Delay changes"): silence frames continue the schedule linearly (`s(x_w)` advances with `x`); placement happens when a chunk follows a gap > 2 ms, after `clear`, after `shift`, or when requested; it inserts `round((P − audible_us)·fs/1e6)` silence frames before the chunk, or drops that many frames of its head (reported in `late_drop_us`) — never both; a chunk whose whole duration is already due is dropped. `shift(Δ)` adds Δ to every queued chunk's play time and requests placement.

- [ ] **Step 1: Failing tests** (48 kHz S16 stereo; `audible_us` is the caller's prediction):

```python
import array
import pytest
from sendspin_bridge.services.audio.playout.scheduler import InputFormat, PlayoutScheduler
from sendspin_bridge.services.audio.playout.timeline import ScheduleMap

F48 = InputFormat(sample_rate=48000)


def tone(frames, value=1000):
    return array.array("h", [value, value] * frames).tobytes()


def sched():
    m = ScheduleMap()
    s = PlayoutScheduler(schedule=m)
    s.set_format(F48)
    return s, m


def frames_of(block):
    return len(block.pcm) // 4


def test_silence_when_nothing_is_queued():
    s, m = sched()
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert frames_of(b) == 480 and not b.program and set(b.pcm) == {0}
    assert m.kind_at(0) == "silence"


def test_first_chunk_is_placed_to_the_frame():
    s, m = sched()
    s.submit(1_005_000, tone(960))                  # due 5 ms after the write head's audible time
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    pcm = array.array("h", b.pcm)
    first = next(i for i in range(0, len(pcm), 2) if pcm[i] != 0) // 2
    assert first == 240                             # 5 ms of silence at 48 kHz
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(1_005_000.0)


def test_late_head_is_dropped_and_reported():
    s, _ = sched()
    s.submit(997_000, tone(960))                    # 3 ms already due
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert b.late_drop_us == pytest.approx(3000.0, abs=21)
    assert b.program and frames_of(b) == 480


def test_contiguous_chunks_with_a_small_gap_are_not_replaced():
    s, _ = sched()
    s.submit(1_000_000, tone(480)); s.submit(1_010_500, tone(480))   # 0.5 ms gap: left to the loop
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b2 = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert s.last_placement_reason is None or s.last_placement_reason == "start"
    assert set(array.array("h", b2.pcm)) == {1000}


def test_clear_drops_queued_audio_and_returns_the_write_head():
    s, _ = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    x_end = s.clear()
    assert x_end == s.x_w_ns and not s.queued
    assert not s.render(audible_us=1_010_000.0, now_us=10_000.0).program


def test_shift_moves_queued_audio_exactly():
    s, m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)            # first 10 ms written
    assert s.shift(+30_000)                                  # smaller delay: audio 30 ms later
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    pcm = array.array("h", b.pcm)
    assert pcm[0] == 0                                       # silence inserted until the shifted frame
    assert s.last_placement_reason == "delay"


def test_shift_drops_audio_whose_new_time_has_passed():
    s, _ = sched()
    s.submit(1_000_000, tone(960))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.shift(-50_000)                                         # earlier by more than what is left
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert not b.program


def test_44k1_input_advances_output_time_exactly():
    m = ScheduleMap(); s = PlayoutScheduler(schedule=m); s.set_format(InputFormat(sample_rate=44100))
    xs = [s.render(audible_us=0.0, now_us=0.0).duration_ns for _ in range(100)]
    assert sum(xs) == 1_000_000_000
```

- [ ] **Step 2–4:** Implement: queue of `[play_time_us, memoryview, pos_frames]`; a block is assembled from silence/program runs, each run appended to the `ScheduleMap` as a segment; output time accumulates exactly (`frames_total * 1e9 // fs` per format epoch, so 100 blocks of 441 frames at 44.1 kHz make exactly 1 s); `fill(fmt, frames)` defaults to zeros (Stage 2 passes the keep-alive signal). Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): scheduler — contiguous output, silence fill and exact placement`.

### Task 1.11: `PlayoutEngine` (measure mode)

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/engine.py`
- Test: `tests/unit/services/audio/playout/test_engine.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class PlayoutSettings:
    mode: Literal["measure", "loop"]
    warm_hold_s: float = 300.0
    warm_in_group: bool = True
    warm_group_max_s: float = 3600.0
    warmup_s: float = 60.0
    trim_us: int = 0
    keep_alive_method: str = "silence"

class PlayoutEngine:
    def __init__(self, *, settings: PlayoutSettings, port: PortClock, k0_ns: int = 0, wall_iso: Callable[[], str] = ...) -> None: ...
    # commands (single-threaded: the player calls them on the feeder thread)
    def set_format(self, fmt: InputFormat) -> None: ...
    def submit(self, play_time_us: int, pcm: bytes) -> None: ...
    def clear(self) -> None: ...
    def resync(self, shift_us: int) -> bool: ...
    # the two ticks
    def next_block(self, now_us: float) -> Block: ...
    def sense(self, now_us: float, delivered_ns: int) -> None: ...
    def snapshot(self) -> dict[str, object]: ...        # the status `playout` dict (spec "Status")
```

The snapshot already carries `warm`, `warm_s` (seconds since the output opened, reset by an underrun or a stall) and `warmup_s` (from the settings) in Stage 1, so the UI's warming state works before the hold exists.

`next_block` computes `audible_us = now + (x_w − x_port)/1000 · (1 + ρ̂)` from the last reading (before the first reading: `now + host latency guess` = `now + buffer_time`, then the first `sense` re-places — reason `start`), passes the controller's latest `rate`/`jump_us` (applied once), and records corrections in the stats. `sense` reads the port, feeds the observer, computes `e = t − s(x_port)` and `e_w`, updates the controller and stats; `underrun`/`recovered`/`stalled` request a placement (reason `underrun`/`stall`) and set `state`.

- [ ] **Step 1: Failing tests** with `tests/unit/services/audio/playout/sim.py` (created here, extended in Task 3.3):

```python
# sim.py
class FakeRaw:
    def __init__(self, t_us=0.0): self.t_us = t_us
    def advance(self, dt_us): self.t_us += dt_us

class SimulatedPort:
    """Consumes the engine's output behind a ring of ``buffer_ms`` at fs·(1+δ) in RAW time; reports A(t)."""
    def __init__(self, raw: FakeRaw, *, buffer_ms=600.0, delta_ppm=0.0, noise_us=0.0, seed=1): ...
    def deliver(self, duration_ns: int) -> None: ...          # data handed to the sink
    def read(self) -> PortSample: ...                        # PortClock protocol
    def skip(self, ms: float) -> None: ...                   # PA "Skipping": A jumps forward
    def starve(self, ms: float) -> None: ...                 # next deliveries arrive late by ms

def drive(engine, port, raw, seconds, *, lead_ms=40.0, block_ms=10.0, sense_every_ms=100.0):
    """Run the real engine in RAW time: blocks to keep the lead, sense at 10 Hz."""
```

```python
# test_engine.py
import pytest
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive


def engine_with_port(**port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    return eng, port, raw


def test_error_is_near_zero_with_an_ideal_port():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 5.0)
    snap = eng.snapshot()
    assert abs(snap["error_ms"]) < 0.1 and snap["jumps"] >= 1      # the start placement


def test_measure_mode_reports_a_rate_error_as_a_growing_error():
    eng, port, raw = engine_with_port(delta_ppm=-200.0)              # port 200 ppm slow → later and later
    drive(eng, port, raw, 30.0)
    snap = eng.snapshot()
    assert snap["error_slope_ms_per_s"] == pytest.approx(0.2, rel=0.2)
    assert snap["correction_ppm"] == 0.0


def test_a_submitted_chunk_is_heard_at_its_play_time():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 2.0)
    due = raw.t_us + 1_500_000
    eng.submit(int(due), b"\x10\x00" * 2 * 4800)
    drive(eng, port, raw, 2.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=50)
```

- [ ] **Step 2–4:** Implement; `SimulatedPort.heard_at_us` records the RAW time the first non-zero frame left the port. Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): engine composes scheduler, observer, controller and stats`.

### Task 1.12: `PlayoutPlayer` — GStreamer output and feeder thread

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/gst_output.py`
- Modify: `src/sendspin_bridge/services/audio/player/pipeline.py`
- Test: `tests/unit/services/audio/playout/test_gst_output.py`

**Interfaces:**

```python
# pipeline.py
def playout_pulsesink_factory(*, device: str | None, client_name: str, buffer_time_us: int) -> Callable[[], Gst.Element]:
    """pulsesink with sync=false, provide-clock=true, slave-method=none."""
def build_playout_pipeline(*, sink: Gst.Element, out_rate: int) -> tuple[Gst.Pipeline, Gst.Element]:
    """appsrc ! audioconvert ! audioresample ! capsfilter(S16LE, 2 ch, out_rate) ! sink; system clock forced."""

# gst_output.py
class PlayoutPlayer:
    """StreamPlayer's daemon-facing interface on the playout engine."""
    def __init__(self, *, sink_factory, settings: PlayoutSettings, out_rate: int | None = None,
                 raw_now_us=..., port_factory=GstPortClock, thread_factory=threading.Thread) -> None: ...
    def start(self, audio_format: PcmFormat) -> None: ...
    def submit(self, play_time_us: int, payload: bytes) -> None: ...
    def clear(self) -> None: ...
    def end_stream(self) -> None: ...          # Stage 1: same as stop(); Stage 2: hold
    def resync(self, shift_us: int) -> bool: ...
    def stop(self) -> None: ...
    def set_volume(self, value: float, muted: bool) -> None: ...
    def is_drained(self) -> bool: ...
    def metrics(self) -> dict[str, object]: ... # StreamPlayer's keys + "playout": engine.snapshot()
    def close_stream(self) -> None: ...        # compatibility no-op that marks the stream closed
```

Threading: every public method enqueues a command into a `deque` under a `threading.Lock` and returns; the feeder thread (`_run`, 5 ms `Event.wait`) applies commands, keeps `appsrc` at `LEAD_MS`, calls `engine.sense()` every 100 ms with `delivered = x_w − current-level-time`, and publishes `engine.snapshot()` into a lock-protected dict for `metrics()`. `start()` opens the output once (output rate from the sink: `pulsectl` sample spec of `device`, else 48000) and only sets the input format afterwards. The engine gets `k0_ns` from a latency query on the `audioresample` element right after PLAYING (0 when it passes through), or by the rule Task 1.1 recorded. If the port provides no clock within 2 s of PLAYING, `metrics()["playout"]["state"] = "fallback"` with `fallback_reason` (the daemon switches players, Task 1.13). Buffer PTS = `x0_ns`, duration = `duration_ns`.

- [ ] **Step 1: Failing tests** (`requires_gst`, fakesink as the sink, a fake port clock, `thread_factory` that returns a thread object whose `start()` does nothing so the test drives `player._tick(now)` by hand):

```python
@requires_gst
def test_feeder_keeps_the_lead_and_writes_contiguous_blocks():
    handed = []
    player = PlayoutPlayer(sink_factory=_recording_fakesink(handed), settings=PlayoutSettings(mode="measure"),
                           out_rate=48000, port_factory=lambda sink: FakePortClock(), thread_factory=_manual)
    player.start(PCM_S16_STEREO_48K)
    for i in range(20):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    pts = [(b.pts, b.duration) for b in handed]
    assert all(p2 == p1 + d1 for (p1, d1), (p2, _) in zip(pts, pts[1:]))
    player.stop()


@requires_gst
def test_input_rate_change_keeps_the_sink():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    sink = player._sink
    player.start(PcmFormat(sample_rate=44100, channels=2, bit_depth=16))
    assert player._sink is sink
    player.stop()


@requires_gst
def test_resync_never_rebuilds_the_pipeline():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    pipeline = player._pipeline
    player.submit(2_000_000, bytes(19200))
    player._tick(now_us=1_000_000.0)
    assert player.resync(10_000) is True
    player._tick(now_us=1_005_000.0)
    assert player._pipeline is pipeline
    player.stop()


@requires_gst
def test_a_sink_without_clock_reports_fallback():
    player = _player(port_factory=lambda sink: FakePortClock(never=True))
    player.start(PCM_S16_STEREO_48K)
    for i in range(500):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    assert player.metrics()["playout"]["state"] == "fallback"
    player.stop()
```

- [ ] **Step 2–4:** Implement. Run `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider -n0` → PASS.
- [ ] **Step 5: Commit** `feat(playout): GStreamer output with a feeder thread; GStreamer only carries audio`.

### Task 1.13: Daemon wiring

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py`, `src/sendspin_bridge/services/ipc/daemon_process.py`, `src/sendspin_bridge/bridge/client.py` (spawn params)
- Test: `tests/unit/services/ipc/test_bridge_daemon_playout.py`

**Interfaces:**
- `DaemonArgs.playout_mode: str = "off"`, `DaemonArgs.playout: dict | None = None`.
- `BridgeDaemon._make_player(sink_name) -> StreamPlayer | PlayoutPlayer` used by `run()` and `retarget_sink()`.
- `BridgeDaemon._effective_lead_ms() -> float` = configured, or `max(configured, pulse_latency_msec + 40 + 100)` when the mode is not `off`; passed to `_create_client` as `required_lead_time_ms` and reported as `effective_required_lead_time_ms`.
- Fallback: when `metrics()["playout"]["state"] == "fallback"`, the telemetry watcher calls `daemon.fall_back_to_legacy(reason)` once: logs WARNING, replaces the player with `StreamPlayer` for the rest of the process.
- Status: the watcher copies `metrics()["playout"]` into `status["playout"]` (1 s while playing, 5 s otherwise, in this mode only); in `measure`/`loop`, `playback_sync_error_ms` = `playout.error_ms`.
- Parent: spawn param `"playout": {"mode": load_config()["PLAYOUT_SYNC"], ...}`.

- [ ] **Step 1: Failing tests**

```python
def test_default_args_keep_the_legacy_player(tmp_path):
    daemon = _daemon(tmp_path)                       # helper as in test_bridge_daemon_aiosendspin9.py, real players
    assert type(daemon._make_player("sink")).__name__ == "StreamPlayer"


def test_measure_mode_builds_the_playout_player(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    assert type(daemon._make_player("sink")).__name__ == "PlayoutPlayer"


def test_lead_is_raised_to_cover_the_output_buffer(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure", pulse_latency_msec=800)
    assert daemon._effective_lead_ms() == 940.0
    assert _daemon(tmp_path)._effective_lead_ms() == 250.0


def test_fallback_switches_to_legacy_once(tmp_path, caplog):
    daemon = _daemon(tmp_path, playout_mode="measure")
    daemon._player = daemon._make_player("sink")
    daemon.fall_back_to_legacy("no audio clock")
    daemon.fall_back_to_legacy("no audio clock")
    assert type(daemon._player).__name__ == "StreamPlayer"
    assert caplog.text.count("falling back to the legacy player") == 1


@pytest.mark.asyncio
async def test_status_carries_playout_and_the_sync_error():
    import asyncio
    from types import SimpleNamespace
    from sendspin_bridge.services.ipc.daemon_process import _timing_telemetry_watcher

    handler = SimpleNamespace(metrics=lambda: {"playout": {"error_ms": 0.4, "state": "playing"}})
    client = SimpleNamespace(is_time_synchronized=lambda: True, _time_filter=None)
    daemon = SimpleNamespace(_audio_handler=handler, _client=client, _args=SimpleNamespace(playout_mode="measure"),
                             fall_back_to_legacy=lambda reason: None)
    status, stop = {"playing": True}, asyncio.Event()
    task = asyncio.create_task(_timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=0.01))
    await asyncio.sleep(0.05)
    stop.set(); await task
    assert status["playout"]["error_ms"] == 0.4
    assert status["playback_sync_error_ms"] == 0.4
```

- [ ] **Step 2–4:** Implement; `daemon_process._run` reads `params.get("playout")` into `DaemonArgs`. Run the new file and `tests/unit/services/ipc -q -p no:cacheprovider` → PASS (all existing tests unchanged).
- [ ] **Step 5: Commit** `feat(daemon): choose the playout engine by PLAYOUT_SYNC and report its status`.

### Task 1.14: Parent status, API model, OpenAPI

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (`DeviceStatus.playout: dict | None = None`, `effective_required_lead_time_ms`), `src/sendspin_bridge/application/models/devices.py` (`PlayoutTiming`, `DeviceTiming.playout`, `pa_latency_ms`, `clock_slew_ppm`), `ui/openapi.json` (export)
- Test: `tests/unit/application/test_device_model_playout.py`

**Interfaces:** `PlayoutTiming` mirrors the spec's "Status" block (all fields optional except counters default 0, `mode` default `"off"`, `warm` default `False`); `DeviceTiming.playout: PlayoutTiming | None = None`.

- [ ] **Step 1: Failing tests:** a status dict with `playout={…}` builds a `Device` whose `timing.playout.error_ms` equals the input; unknown keys inside `playout` are ignored (model `extra="ignore"`); `playout=None` → `timing.playout is None`; the OpenAPI schema lists `PlayoutTiming`.
- [ ] **Step 2–4:** Implement. `uv run python scripts/export_openapi.py && cd ui && npm run gen:api`. Run → PASS.
- [ ] **Step 5: Commit** `feat(api): playout timing in the device model`.

### Task 1.15: UI — SyncChip and the timing panel

**Files:**
- Modify: `ui/src/utils/deviceChips.ts` (`syncBadge` playout variant), `ui/src/components/devices/chips/SyncChip.vue`, `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/settings/layout.ts` (`PLAYOUT_SYNC` select, advanced, Audio section), `ui/src/i18n/{en,ru}.json`
- Test: `ui/tests/unit/utils/deviceChips.test.ts` (extend), `ui/tests/component/devices/DeviceTimingPanel.test.ts` (extend)

**Interfaces:**

```ts
export interface SyncBadge {
  kind: 'sync' | 'reanchoring' | 'playout' | 'warming'
  tone: Tone
  pulse: boolean
  errorMs: number | null   // playout: |error_ms| with one decimal
  warmInS?: number | null
  count: number; countTone: Tone; count5m: number | null; count30m: number | null
}
```

`syncBadge(d)`: when `d.timing.playout && d.timing.playout.mode !== 'off'`: `warming` (neutral) while not warm and `warm_s` known (`warmInS = warmup − warm_s`, from the playout settings echoed in status as `warmup_s`), else `playout` with tone ≤ 1 ms success, ≤ 5 ms warning, else error; `count` = `jumps`. Otherwise the existing logic unchanged.

- [ ] **Step 1: Failing tests** (fixtures from `ui/tests/fixtures/device.ts`): playout 0.4 ms → `{kind:'playout', tone:'success', errorMs:0.4}`; 3.2 → warning; 12 → error; not warm with `warm_s` 25, `warmup_s` 60 → `{kind:'warming', warmInS:35}`; `mode:'off'` → the old badge. Timing panel renders "Playout" rows (error, p95, slope, corrections/s, jumps, underruns, PA latency, host latency, sink rate, clock slew, warm) and hides the block when `playout` is null. i18n keys exist in en and ru (`i18n-sync-keys` style test).
- [ ] **Step 2–4:** Implement. `cd ui && npm test && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(ui): playout sync indicator and timing details`.

### Task 1.16: Logs, changelog, Stage 1 measurement run

**Files:**
- Modify: `gst_output.py`/`engine.py` (log lines per the spec table; a `RateLimitedLog` helper in `stats.py` with a 600 s window per kind), `CHANGELOG.md`
- Test: `tests/unit/services/audio/playout/test_logging.py`

- [ ] **Step 1: Failing tests:** a WARNING of one kind is emitted once in 10 minutes of fake time and again after; a jump logs INFO with size and reason; the 10 s DEBUG summary appears every 10 s of fake time (1 s with `SENDSPIN_PLAYOUT_DEBUG=1` via monkeypatched env).
- [ ] **Step 2–4:** Implement → PASS.
- [ ] **Step 5: CHANGELOG** `[Unreleased]`: `### Added` — "Playout telemetry for every player: PulseAudio stream latency and system clock slew in the timing panel." and "Experimental `PLAYOUT_SYNC=measure`: the bridge times Bluetooth playout itself and shows the playout error against the Sendspin clock." `uv run python scripts/lint_changelog.py CHANGELOG.md` → clean.
- [ ] **Step 6: Commit** `feat(playout): logs; changelog for stage 1`.
- [ ] **Step 7: Stage 1 measurement (stand).** Needs `scripts/sync_phase_trace.py` — do Task 5.1 first (it depends on nothing in Stages 2–4). With `measure` on the HAOS stand, record ENEBY V-fresh and V-warm (Stage 5 protocol, steps 1–2) and the telemetry; append a short "Stage 1 findings" paragraph to the spec's Review notes (H1 decay seen or not; `sink_rate_ppm`; `pa_steps`; `clock_slew_ppm`), commit `docs(sync): playout sync — stage 1 findings`.

---

## Stage 2 — Warm stream

Shippable: in `measure`, `stream/end` no longer tears the output down; idle modes, keep-alive and sync tuning respect the hold. `off` unchanged.

### Task 2.1: Warm config keys

**Files:** as Task 1.2 (`PLAYOUT_WARM_HOLD_S` 0–3600 = 300, `PLAYOUT_WARM_IN_GROUP` = true, `PLAYOUT_WARM_GROUP_MAX_S` 0–86400 = 3600, `PLAYOUT_WARMUP_S` 10–300 = 60; all `_GLOBAL_RESTART_FIELDS`), `ui/src/settings/layout.ts` (advanced fields), i18n.
**Test:** `tests/unit/config/test_playout_config.py` (extend: defaults, schema ranges, model rejects 3601/9, diff kind, HA preserve).

- [ ] Steps: failing tests → implement → PASS → parent passes them, plus the player's `keep_alive_method`, in the spawn `playout` dict (a new test in `tests/unit/bridge/test_playout_spawn_params.py` asserting the params JSON) → commit `feat(config): warm stream hold settings`.

### Task 2.2: `WarmHold`

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/warm.py`
- Test: `tests/unit/services/audio/playout/test_warm.py`

**Interfaces:**

```python
HoldReason = Literal["grace", "group"]

@dataclass(frozen=True, slots=True)
class HoldState:
    holding: bool
    reason: HoldReason | None
    left_s: float | None

class WarmHold:
    def __init__(self, *, hold_s: float, in_group: bool, group_max_s: float) -> None: ...
    def program_started(self, t_s: float) -> None: ...
    def program_stopped(self, t_s: float) -> None: ...
    def grouped(self, t_s: float, value: bool) -> None: ...
    def release(self) -> None: ...                  # the parent's power_save asked for it
    def state(self, t_s: float) -> HoldState: ...   # holding=False → the output may close
```

- [ ] **Step 1: Failing tests**

```python
from sendspin_bridge.services.audio.playout.warm import WarmHold


def test_grace_after_program_stops():
    h = WarmHold(hold_s=300, in_group=True, group_max_s=3600)
    h.program_started(0); h.program_stopped(10)
    assert h.state(200).holding and h.state(200).reason == "grace"
    assert not h.state(311).holding


def test_grouped_hold_lasts_until_the_cap():
    h = WarmHold(hold_s=300, in_group=True, group_max_s=3600)
    h.grouped(0, True); h.program_stopped(0)
    assert h.state(3000).reason == "group"
    assert not h.state(3601).holding


def test_leaving_the_group_falls_back_to_the_grace_from_the_stop():
    h = WarmHold(hold_s=300, in_group=True, group_max_s=0)
    h.grouped(0, True); h.program_stopped(0); h.grouped(1000, False)
    assert not h.state(1000).holding


def test_release_ends_any_hold_until_program_plays_again():
    h = WarmHold(hold_s=300, in_group=True, group_max_s=0)
    h.grouped(0, True); h.program_stopped(0); h.release()
    assert not h.state(1).holding
    h.program_started(5); h.program_stopped(6)
    assert h.state(7).holding


def test_zero_hold_closes_at_once_when_not_grouped():
    h = WarmHold(hold_s=0, in_group=True, group_max_s=3600)
    h.program_stopped(0)
    assert not h.state(0).holding


def test_program_playing_is_not_a_hold():
    h = WarmHold(hold_s=300, in_group=True, group_max_s=3600)
    h.program_started(0)
    assert not h.state(5).holding and h.state(5).reason is None
```

- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(playout): warm hold policy`.

### Task 2.3: Scheduler and engine — end of stream, tail mute, keep-alive fill, warm counter

**Files:**
- Modify: `scheduler.py` (fill callable from settings), `engine.py`
- Test: `tests/unit/services/audio/playout/test_engine_warm.py`

**Interfaces (engine):**

```python
def end_stream(self, now_us: float) -> None:      # drop queue, x_end, mute_tail on, hold.program_stopped
def set_grouped(self, now_us: float, value: bool) -> None: ...
def release_hold(self) -> None: ...
@property
def mute_tail(self) -> bool: ...                  # True from end_stream/clear until x_port ≥ x_end + 20 ms
@property
def should_close(self) -> bool: ...               # hold ended and nothing queued
```

Snapshot additions: `state` (`holding` while the hold runs), `hold_reason`, `hold_left_s`. `warm_s` (Task 1.11) now survives stream ends: it still resets only on open, underrun and stall. `submit` after `end_stream` calls `hold.program_started` and requests placement (reason `start`). The fill for silence is `build_subsonic_carrier_pcm`-equivalent infrasound when `keep_alive_method == "infrasound"` (reuse `services/audio/latency_calibration.build_subsonic_carrier_pcm` with the bridge's keep-alive amplitude), zeros otherwise.

- [ ] **Step 1: Failing tests** (simulation from Task 1.11): after `end_stream`, `mute_tail` is True and becomes False once the simulated port passes `x_end + 20 ms` (≈ `buffer_ms` later); no program frame is rendered after `end_stream`; `should_close` stays False for 300 s of simulated time with `hold_s=300` and becomes True after; a new `submit` during the hold places the chunk exactly (heard at its play time ± 50 µs) and `jumps` counts it with reason `start`; `warm_s` keeps growing across an end/start pair and resets after `port.starve(800)`; infrasound fill is non-zero with `keep_alive_method="infrasound"`.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(playout): keep the output warm after a stream ends`.

### Task 2.4: Player — `end_stream`, mute composition, closing at hold end

**Files:**
- Modify: `gst_output.py`
- Test: `tests/unit/services/audio/playout/test_gst_output.py` (extend)

**Interfaces:** `end_stream()` enqueues `engine.end_stream`; the feeder applies `sink.mute = user_muted or engine.mute_tail` whenever either changes (the `volume_controller=sendspin` stream mute is the `user_muted` part of `set_volume`); when `engine.should_close`, the feeder sets the pipeline to NULL and the player becomes idle (a later `start()` reopens). `set_grouped(bool)` and `release_hold()` are public and enqueued.

- [ ] **Step 1: Failing tests** (`requires_gst`, fakesink with a `mute`-capable fake: a `volume ! fakesink` bin exposing `mute`, or a `MagicMock` sink wrapper as in `test_a_stream_volume_set_before_playback_reaches_every_new_sink`): `end_stream` keeps the pipeline PLAYING and the next blocks are silent; mute goes on immediately and off after the tail; a user mute set during the tail survives the tail's end; hold end sets the pipeline to NULL and `start()` builds a new one.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(playout): stop audio at stream end by muting the tail, not by a teardown`.

### Task 2.5: Daemon — stream end, group, hold release command

**Files:**
- Modify: `bridge_daemon.py`, `daemon_process.py` (`_read_commands`), `services/ipc/commands.py` (`SetPlayoutHold`)
- Test: `tests/unit/services/ipc/test_bridge_daemon_playout.py` (extend), `tests/unit/services/ipc/test_ipc_commands.py` (new cases only)

**Interfaces:**
- `BridgeDaemon._on_stream_end`: `if self._args.playout_mode != "off": player.end_stream()` else the existing `player.stop()`; the rest unchanged.
- `_on_group_update`: in `measure`/`loop`, `player.set_grouped(payload.group_id not in (None, own_client_id))`, where `own_client_id` = `self._identity.peer_id` (Task 1.1 E5 alternative: the parent sends `set_playout_hold {grouped}` from its MA group cache — then this task adds `grouped` to the command instead).
- Command `set_playout_hold {release: bool}` → `daemon.release_playout_hold()` → `player.release_hold()`; decode clamps nothing else; unknown fields ignored.
- The existing `test_stream_end_stops_output_and_discards_buffered_audio` stays as is (default `off`).

- [ ] **Step 1: Failing tests:** with `playout_mode="measure"`, `_on_stream_end` calls `end_stream` and not `stop`; with default args the old test passes untouched; a `group/update` with another id calls `set_grouped(True)`, with the own id `set_grouped(False)`; `decode_command({"cmd":"set_playout_hold","release":True})` → `SetPlayoutHold(release=True)` and `encode_command` round-trips.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(daemon): warm stream at stream end; hold release command`.

### Task 2.6: Parent — idle modes, keep-alive, hold

**Files:**
- Modify: `src/sendspin_bridge/bridge/idle_machine.py`, `src/sendspin_bridge/bridge/client.py`
- Test: `tests/unit/bridge/test_idle_machine_hold.py`

**Interfaces:**
- `PlaybackState` gains `playout_holding: bool` and `playout_hold_reason: str | None` (from `status["playout"]`).
- `IdleMachine(..., playout_engine: bool = False)` (default keeps every existing caller and test as is): when True, sink events are ignored for timers (`on_sink_active/idle` return `[]`) and `on_playback_change` (daemon flags = program audio) drives them, as the existing fallback path does.
- New action `ReleasePlayoutHold` before `power_save` suspends: `ArmPowerSaveTimer` firing in the client now runs `_enter_power_save()` which, if `status.playout.state == "holding"`: when `hold_reason == "group"` → log INFO "power save waits for the group" and re-arm when the hold ends (status transition `holding → idle` triggers `on_hold_ended` → `ArmPowerSaveTimer(0)`); else send `SetPlayoutHold(release=True)`, wait up to 2 s for `playout.state != "holding"`, then suspend.
- `_keepalive_loop` skips a burst while `status.playout.state == "holding"`.
- `auto_disconnect` unchanged (standby closes the output).

- [ ] **Step 1: Failing tests**

```python
from sendspin_bridge.bridge.idle_machine import ArmPowerSaveTimer, IdleMachine, PlaybackState


def machine(mode="power_save"):
    return IdleMachine(mode=mode, idle_disconnect_minutes=5, power_save_delay_minutes=1,
                       sink_monitor_active=lambda: True, playout_engine=True)


def test_sink_idle_is_ignored_while_the_engine_feeds_the_sink():
    assert machine().on_sink_idle(PlaybackState()) == []


def test_program_stop_arms_power_save_even_with_a_sink_monitor():
    actions = machine().on_playback_change(previous=PlaybackState(audio_streaming=True), current=PlaybackState())
    assert actions == [ArmPowerSaveTimer(delay_s=60)]


def test_without_the_engine_nothing_changes():
    m = IdleMachine(mode="power_save", idle_disconnect_minutes=5, power_save_delay_minutes=1,
                    sink_monitor_active=lambda: True, playout_engine=False)
    assert m.on_sink_idle(PlaybackState()) == [ArmPowerSaveTimer(delay_s=60)]
```

Client-level tests (same file, `SendspinClient` built with the existing test helpers in `tests/unit/bridge/test_sendspin_client.py`): power-save fire with a grace hold sends `set_playout_hold release` before `asuspend_sink`; with a group hold it does not suspend and re-arms when status moves to `idle`; keep-alive burst skipped while holding; `PLAYOUT_SYNC=off` keeps the old order of calls (assert `asuspend_sink` without a release).
- [ ] **Step 2–4:** Implement → PASS (`tests/unit/bridge/test_idle_machine.py` unchanged and green).
- [ ] **Step 5: Commit** `feat(idle): idle modes follow program audio while the playout engine holds the sink`.

### Task 2.7: Sync tuning — warm gate

**Files:**
- Modify: `src/sendspin_bridge/application/sync_tuning.py`, `src/sendspin_bridge/application/models/sync.py`, `src/sendspin_bridge/api/routers/sync.py`, `docs/contracts/sync-contract-v1.json` (`scripts/export_sync_contract.py`), `ui/src/components/sync/StepClicker.vue`, `ui/src/components/sync/MeasureWizard.vue`, i18n
- Test: `tests/unit/api/test_sync_playout.py`, `ui/tests/component/sync/StepClicker.test.ts` (extend or new)

**Interfaces:**
- `SyncMember` out gains `warm_in_s: float | None` (bridge member in `measure`/`loop`: `max(0, warmup_s − warm_s)`; else `null`), computed in `_members_out` from the bridge client whose `sendspin_client_id` equals the member's `client_id`.
- `start_sequence(sid, members, force=False)` and `upload_recording(..., force=False)`: a bridge member with `warm_in_s > 0` → `UseCaseError(409, "member_cold", …)` with problem fields `member` and `warm_in_s`, unless `force`.
- Request models: `SequenceIn.force: bool = False`; recordings query `force: bool = False`.

- [ ] **Step 1: Failing tests:** a session with a cold bridge member answers 409 `member_cold` with `warm_in_s`; `force=true` starts; a warm member (or `off` mode, `warm_in_s` null) starts as before; the contract export `--check` passes after regeneration; the UI shows "Warming up the Bluetooth speakers — 35 s" and disables "Start the sequence" until all are warm, with a "Measure anyway" link that sends `force`.
- [ ] **Step 2–4:** Implement; `uv run python scripts/export_sync_contract.py`; `uv run python scripts/export_openapi.py && cd ui && npm run gen:api`. Run → PASS.
- [ ] **Step 5: Commit** `feat(sync): wait for Bluetooth members to be warm before measuring`.

### Task 2.8: Changelog and docs for Stage 2

- [ ] `CHANGELOG.md` `### Changed`: "With `PLAYOUT_SYNC=measure`, a Bluetooth speaker's stream stays open with silence for `PLAYOUT_WARM_HOLD_S` after playback stops (longer while it is in an active group), so the next stream starts in sync; idle modes count from the end of the music." `### Added`: "Group sync tuning waits until Bluetooth members are warm before measuring." Lint → clean. Commit `docs(changelog): warm stream`.

---

## Stage 3 — Control loop

Shippable: `loop` becomes available (still opt-in): fine corrections, 10 ms jumps, delay migration.

### Task 3.1: `Corrector`

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/corrector.py`
- Test: `tests/unit/services/audio/playout/test_corrector.py`

**Interfaces:**

```python
CROSSFADE_FRAMES = 16

def drop_frame(pcm: bytes, at: int, *, channels: int = 2) -> bytes:
    """Remove frame ``at`` (16-bit interleaved); the 16 frames before the cut are crossfaded into
    the ones after it. Length shrinks by exactly one frame."""

def insert_frame(pcm: bytes, at: int, *, channels: int = 2) -> bytes:
    """Duplicate frame ``at`` with the symmetric crossfade. Length grows by exactly one frame."""

def fade(pcm: bytes, *, out: bool, frames: int, channels: int = 2) -> bytes: ...   # 2 ms ramps around jumps
```

- [ ] **Step 1: Failing tests:** `len(drop_frame(x, 100)) == len(x) − 4`; `insert` +4; frames outside `[at − 16, at + 16]` are byte-identical to the input (shifted by one after the cut); on a constant signal both are the identity apart from length; on a 1 kHz sine at −6 dBFS the largest sample-to-sample step around the cut is ≤ 1.5× the sine's own largest step (no click); `at` at the start/end of a block clamps the crossfade to what exists; mono works with `channels=1`.
- [ ] **Step 2–4:** Implement with `array("h")`, integer arithmetic, clamped to int16. Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): single-frame drop and insert with a crossfade`.

### Task 3.2: `LoopController` — PI loop

**Files:**
- Modify: `controller.py`
- Test: `tests/unit/services/audio/playout/test_controller.py` (extend)

**Interfaces:** constants `B_HZ = 0.03`, `ZETA = 0.707`, `OMEGA = 2π·B_HZ`, `KP = 2·ZETA·OMEGA`, `KI = OMEGA**2`, `DEADBAND_US = 100.0`, `R_MAX = 1e-3`, `SAT_WARN_RATE = 300e-6`, `SAT_WARN_S = 60.0`; in `loop` mode `update` returns `rate = clamp(KP·dz(ε_f)·1e-6 + I)` with `I ← clamp(I + KI·ε·T_S·1e-6)` frozen while saturated in the error's direction; `saturated` flag; `placed()` holds P off for `HOLDOFF_S`; `sat_warning(t_s) -> float | None` returns the ppm once when |I| > 300 ppm has lasted 60 s.

- [ ] **Step 1: Failing tests** (plant simulated inline: `e += (δ − r)·T` µs/s — this tests the controller's dynamics, the engine-level test is Task 3.3):

```python
import math
import pytest
from sendspin_bridge.services.audio.playout.controller import KI, KP, LoopController, R_MAX


def plant(ctrl, delta_ppm, seconds, e0_us=0.0, noise=None, t0=0.0):
    e, t, errs, rates = e0_us, t0, [], []
    for i in range(int(seconds / 0.1)):
        meas = e + (noise[i] if noise else 0.0)
        d = ctrl.update(t, meas)
        if d.jump_us is not None:
            e -= d.jump_us                         # the engine executes the jump
        e += (delta_ppm * 1e-6 - d.rate) * 0.1 * 1e6
        t += 0.1
        errs.append(e); rates.append(d.rate)
    return errs, rates


def test_constants_are_the_specified_loop():
    assert KP == pytest.approx(0.2666, rel=1e-3) and KI == pytest.approx(0.03553, rel=1e-3)


def test_constant_rate_error_leaves_no_phase_error():
    errs, rates = plant(LoopController(mode="loop"), 200.0, 180)
    assert abs(errs[-1]) < 20.0                      # µs
    assert rates[-1] == pytest.approx(200e-6, rel=0.05)


def test_ramp_onset_peak_is_small():
    errs, _ = plant(LoopController(mode="loop"), 100.0, 60)
    assert max(abs(e) for e in errs) < 400.0


def test_rate_is_limited_and_does_not_wind_up():
    ctrl = LoopController(mode="loop")
    errs, rates = plant(ctrl, 1500.0, 30)          # beyond R_max: saturates, jumps keep the error bounded
    assert max(rates) == pytest.approx(R_MAX)
    assert ctrl.rate_estimate == pytest.approx(R_MAX)   # I frozen at the limit, not beyond
    errs2, _ = plant(ctrl, 0.0, 60, e0_us=errs[-1], t0=30.0)
    assert abs(errs2[-1]) < 500.0                  # recovers because I never wound up


def test_dead_band_keeps_p_quiet_inside_100_us():
    ctrl = LoopController(mode="loop")
    d = [ctrl.update(0.1 * i, 50.0) for i in range(30)]
    assert all(abs(x.rate - ctrl.rate_estimate) < 1e-9 for x in d)


def test_noise_residual_is_tiny():
    import random
    rnd = random.Random(1)
    noise = [rnd.gauss(0, 300.0) for _ in range(3000)]
    errs, _ = plant(LoopController(mode="loop"), 50.0, 300, noise=noise)
    tail = errs[-1000:]
    assert math.sqrt(sum(e * e for e in tail) / len(tail)) < 100.0


def test_saturation_warning_after_60_s():
    ctrl = LoopController(mode="loop")
    plant(ctrl, 600.0, 100)
    assert ctrl.sat_warning(100.0) == pytest.approx(600.0, rel=0.1)
    assert ctrl.sat_warning(100.1) is None
```

- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(playout): PI playout loop with dead band, rate limit and anti-windup`.

### Task 3.3: Closed-loop simulation acceptance

**Files:**
- Modify: `tests/unit/services/audio/playout/sim.py` (noise, `skip`, `starve`, `heard_at_us` for every program frame)
- Create: `tests/unit/services/audio/playout/test_closed_loop.py`

- [ ] **Step 1: Failing tests** (they drive the real `PlayoutEngine` in `loop` mode; fail until Task 3.4 wires corrections):

```python
import pytest
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive


def loop_engine(**kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, noise_us=300.0, **kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="loop"), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    return eng, port, raw


@pytest.mark.parametrize("delta", [0.0, 50.0, -50.0, 500.0, -500.0])
def test_holds_the_port_to_the_schedule(delta):
    eng, port, raw = loop_engine(delta_ppm=delta)
    drive(eng, port, raw, 120.0)
    snap = eng.snapshot()
    assert snap["error_p95_ms"] < 0.5
    assert snap["jumps"] == 1                                   # only the start placement
    assert snap["corrections_per_s"] == pytest.approx(abs(delta) * 48000e-6, abs=0.3)


def test_pa_skip_of_5_ms_is_corrected_without_a_jump():
    eng, port, raw = loop_engine()
    drive(eng, port, raw, 30.0); port.skip(5.0); drive(eng, port, raw, 40.0)
    snap = eng.snapshot()
    assert abs(snap["error_ms"]) < 0.3 and snap["jumps"] == 1 and snap["pa_steps"] == 1


def test_step_of_15_ms_jumps_once():
    eng, port, raw = loop_engine()
    drive(eng, port, raw, 30.0); port.skip(15.0); drive(eng, port, raw, 20.0)
    assert eng.snapshot()["jumps"] == 2 and eng.snapshot()["last_jump_reason"] == "steady"


def test_underrun_is_detected_and_reanchored():
    eng, port, raw = loop_engine(buffer_ms=200.0)
    drive(eng, port, raw, 20.0); port.starve(400.0); drive(eng, port, raw, 20.0)
    snap = eng.snapshot()
    assert snap["underruns"] == 1 and snap["last_jump_reason"] == "underrun" and abs(snap["error_ms"]) < 0.5


def test_program_audio_is_heard_on_time_under_a_rate_error():
    eng, port, raw = loop_engine(delta_ppm=300.0)
    drive(eng, port, raw, 60.0)
    due = raw.t_us + 2_000_000
    eng.submit(int(due), b"\x10\x00" * 2 * 48000)
    drive(eng, port, raw, 5.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=500)


def test_saturating_rate_error_warns(caplog):
    eng, port, raw = loop_engine(delta_ppm=1500.0)
    drive(eng, port, raw, 90.0)
    assert "runs" in caplog.text and "ppm off the Sendspin clock" in caplog.text
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_closed_loop.py -q -p no:cacheprovider -n0` → FAIL (no corrections yet). Commit the tests with Task 3.4.

### Task 3.4: Engine — corrections, `e_w`, steady jumps

**Files:**
- Modify: `scheduler.py` (phase accumulator, `Corrector` calls, jump insert/drop with fades), `engine.py` (`e_w`, pass `rate`/`jump_us`, record `inserted/dropped`, `pa_steps` detection: a step > 2 ms in `A − t` after removing `sink_rate_ppm`·t)
- Test: Task 3.3's file plus `tests/unit/services/audio/playout/test_scheduler.py` (extend: accumulator spreads 24 events/s evenly at 500 ppm; never more than one per block; corrections applied to silence leave silence; a jump of +5 ms drops 240 frames with fades; −5 ms inserts 240 silent frames with fades; every correction appends a schedule segment so `s(x)` reflects it)

- [ ] **Step 1:** Scheduler tests fail. **Step 2:** Implement scheduler. **Step 3:** Implement engine. **Step 4: Run** the playout test folder → PASS (Task 3.3 now green). **Step 5: Commit** `feat(playout): the loop corrects the port with single frames and rare jumps`.

### Task 3.5: Player and daemon in `loop` mode

**Files:**
- Modify: `config/schema.json`… (already allow `loop`), `gst_output.py` (warnings: saturation, > 3 underruns/10 min, > 30 % rejected/60 s, repeated steady jumps → `state = degraded`), `bridge_daemon.py` (nothing mode-specific beyond `playout_mode != "off"`)
- Test: `tests/unit/services/audio/playout/test_gst_output.py` (extend: `loop` builds; warnings through `RateLimitedLog`), `tests/unit/services/ipc/test_bridge_daemon_playout.py` (extend: `loop` behaves as `measure` for end/resync/status)

- [ ] Steps: failing tests → implement → PASS → commit `feat(playout): PLAYOUT_SYNC=loop`.

### Task 3.6: Delay changes in `loop` keep 3.1.0-beta.5 semantics

**Files:**
- Test: `tests/unit/services/audio/playout/test_engine_delay.py`, `tests/unit/services/ipc/test_bridge_daemon_playout.py` (extend)

- [ ] **Step 1: Tests:** engine-level: during program audio, `resync(+30_000)` makes the next program frame heard 30 ms later than it would have been, ± 1 frame, the frames already in flight are heard on their old time, nothing is rebuilt, `last_jump_reason == "delay"`; `resync(−200_000)` drops the audio whose new time has passed (heard frames never earlier than their new time); `resync` with nothing queued returns False. Daemon-level with a real `PlayoutPlayer` on a fake port: three changes inside 150 ms → one `resync` with the total shift (the existing coalescing), and the log line "Output delay changed 300→320 ms; player resynchronised".
- [ ] **Step 2:** If any fails, fix the engine (not the tests). **Step 3: Commit** `test(playout): delay changes take effect exactly without a rebuild`.

### Task 3.7: Delay migration when the engine is enabled

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (on spawn), `src/sendspin_bridge/config/__init__.py` (`save_player_delay_migration`), `config/schema.json` + `config/players.py` (`static_delay_pre_playout_ms: int | None`, `static_delay_playout_migrated: bool | None`)
- Test: `tests/unit/bridge/test_playout_delay_migration.py`

**Interfaces:**

```python
def migrate_delay_for_playout(*, mode: str, delay_ms: float, source: str, legacy_latency_ms: float | None,
                              pulse_latency_ms: int, backup_ms: int | None, migrated: bool
                              ) -> tuple[float, int | None, bool] | None:
    """(new_delay, backup, migrated) or None when nothing changes.
    off→engine, user-tuned source, not yet migrated: new = max(0, delay − (legacy_latency or pulse_latency)), backup = delay.
    engine→off, migrated: new = backup, backup = None, migrated = False."""
```

User-tuned sources: `manual`, `music_assistant`, `legacy`, `microphone_calibration`, `manual_calibration`. Applied through the normal hot-apply + persist path, so Music Assistant learns the new value; INFO log "[name] Output delay migrated for the playout engine: 812 → 12 ms (backup kept)".

- [ ] **Step 1: Failing tests:** off→loop with `manual` 812 ms and legacy latency 800 → (12, 812, True); a second start does nothing; `default` source → None; loop→off restores 812; legacy latency missing → uses `PULSE_LATENCY_MSEC`; never negative.
- [ ] **Step 2–4:** Implement (gated behind the user's decision on Open question 2; if they decline, the task ships only the INFO hint "re-tune after enabling the engine" and the function is not called). Run → PASS.
- [ ] **Step 5: Commit** `feat(playout): migrate tuned delays once when the playout engine takes over`.

### Task 3.8: Changelog for Stage 3

- [ ] `### Added`: "Experimental `PLAYOUT_SYNC=loop`: the bridge holds Bluetooth playout to the Sendspin clock within a millisecond, correcting one sample at a time; the PulseAudio buffer no longer shifts sync." `### Changed`: "Enabling the playout engine lowers previously tuned output delays once by the old PulseAudio buffer latency; switching back restores them." Lint, commit.

---

## Stage 4 — Per-speaker offset and sync-tuning integration

Shippable: offsets are stored and applied for players in `measure`/`loop`; sync tuning can save them.

### Task 4.1: Offset entries in the player config

**Files:**
- Create: `src/sendspin_bridge/services/audio/speaker_offsets.py`
- Modify: `config/players.py` (`speaker_offsets: list[dict] | None`), `config/schema.json`, `config/__init__.py` (`save_speaker_offset(player_id, entry)`, `forget_speaker_offsets(player_id, codec=None, adapter=None)`), `config_diff.py` (`speaker_offsets` hot / ignored for restarts), `PLAYOUT_SPEAKER_OFFSETS` key (as Task 1.2, hot)
- Test: `tests/unit/services/audio/test_speaker_offsets.py`, `tests/unit/config/test_speaker_offset_store.py`

**Interfaces:**

```python
OffsetSource = Literal["acoustic", "bluez_delay_report"]

@dataclass(frozen=True, slots=True)
class SpeakerOffset:
    codec: str             # normalised lower-case: sbc, aac, …
    adapter: str           # adapter MAC, upper-case
    offset_ms: float       # 0.1 ms resolution
    source: OffsetSource
    measured_at: str
    spread_ms: float | None = None
    reference: str | None = None
    audio_server: str | None = None     # pulseaudio | pipewire
    playout_mode: str = "loop"
    bridge_version: str = ""
    def key(self) -> tuple[str, str]: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: Mapping) -> SpeakerOffset | None: ...   # None for malformed entries

MAX_ENTRIES = 8
def upsert(entries: list[SpeakerOffset], entry: SpeakerOffset) -> list[SpeakerOffset]: ...  # replace by key, newest first, ≤ 8
```

- [ ] **Step 1: Failing tests:** round trip; malformed → None and skipped on load; upsert replaces by key and keeps ≤ 8 newest; refusing an entry with `playout_mode="off"` (`ValueError`); store writes under `config_lock` and survives `load_config`; `forget` by key and all.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(offsets): per-speaker offsets by codec and adapter in the player config`.

### Task 4.2: Offset resolver

**Files:**
- Modify: `speaker_offsets.py`
- Test: `tests/unit/services/audio/test_speaker_offsets.py` (extend)

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class Resolution:
    value_ms: float | None        # what output_delay + trim should be; None = leave as is
    source: str                   # acoustic | bluez_delay_report | device_report_in_sink | none
    entry: SpeakerOffset | None
    reason: str

def resolve_offset(*, entries: Sequence[SpeakerOffset], codec: str | None, adapter: str | None,
                   current_source: str, current_set_at: str | None, reported_delay_ms: float | None,
                   audio_server: str, mode: str, auto_apply: bool) -> Resolution: ...
```

Rules (spec "Sources and precedence"): `mode == "off"` or `not auto_apply` → `None`; `current_source in {"manual","music_assistant"}` and `current_set_at` newer than the matching entry's `measured_at` → `None` ("operator value is newer"); a matching acoustic entry → it; else `reported_delay_ms` on `pulseaudio` → `bluez_delay_report`; on `pipewire` with a report → `device_report_in_sink`, `value_ms = None` ("already in the sink latency"); else `none`.

- [ ] **Step 1: Failing tests** — one per rule, plus: codec normalisation ("SBC" matches "sbc"); adapter case; an entry for another codec does not apply; a newer acoustic entry beats an older MA value.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(offsets): resolve which speaker offset applies`.

### Task 4.3: Parent applies offsets; trim command

**Files:**
- Modify: `bridge/client.py` (`_transport_telemetry_loop` → `_maybe_apply_speaker_offset(snapshot)` on codec/adapter change and on connect), `services/ipc/commands.py` (`SetPlayoutTrimUs`, clamp −500…500), `daemon_process.py`, `bridge_daemon.py` (`set_playout_trim_us` → `player.set_trim_us`), `engine.py` (trim added to every `s(x)` lookup's target: `e = t − s(x_port) − trim`)
- Test: `tests/unit/bridge/test_speaker_offset_apply.py`, `tests/unit/services/audio/playout/test_engine.py` (extend: a trim of +400 µs makes program frames heard 400 µs earlier), `tests/unit/services/ipc/test_ipc_commands.py` (new cases)

**Interfaces:** applying `Resolution(value_ms=212.4)` → `apply_hot_config({"static_delay_ms": 212})` + `SetPlayoutTrimUs(400)` + status `static_delay_source="acoustic"`, `static_delay_codec`, `speaker_offset` (dict of the entry); INFO log "Speaker offset applied: 212.4 ms (acoustic, sbc, C0:FB:…)". Re-applied at most once per (codec, adapter, entry) per connection.

- [ ] Steps: failing tests (fake transport snapshots: SBC on hci0 → entry applied; switch to AAC with no entry → nothing; MA sets 250 later → not overwritten; `PLAYOUT_SPEAKER_OFFSETS=false` → nothing) → implement → PASS → commit `feat(offsets): apply the stored speaker offset on connect and codec change`.

### Task 4.4: Device API — offsets in the model, forget endpoints

**Files:**
- Modify: `application/models/devices.py` (`SpeakerOffsetOut`, `DeviceAudio.speaker_offset`, `speaker_offsets`), `application/devices.py` (`forget_speaker_offsets`), `api/routers/devices.py`
- Test: `tests/unit/api/test_device_speaker_offsets.py`

**Interfaces:** `DELETE /api/v1/devices/{device_id}/speaker-offsets` (204) and `DELETE /api/v1/devices/{device_id}/speaker-offsets/{codec}/{adapter}` (204; 404 `offset_not_found`); both `require_principal` + CSRF; forgetting the applied entry leaves the current delay as is and sets `static_delay_source` to `manual`.

- [ ] Steps: failing tests (auth required, 404 unknown device, 404 unknown entry, 204 removes from config and status) → implement → OpenAPI export + `npm run gen:api` → PASS → commit `feat(api): list and forget speaker offsets`.

### Task 4.5: Sync tuning — offset candidates and saving

**Files:**
- Modify: `application/sync_tuning.py`, `application/models/sync.py`, `api/routers/sync.py`, `docs/contracts/sync-contract-v1.json`
- Test: `tests/unit/api/test_sync_playout.py` (extend)

**Interfaces:**

```python
class OffsetCandidate(ResponseModel):
    member: str
    eligible: bool
    reason: Literal["ok", "relative_only", "not_bridge", "not_warm", "legacy_mode", "position_point", "not_measured"]
    offset_ms: float | None
    codec: str | None
    adapter: str | None

# SyncSessionOut.offset_candidates: list[OffsetCandidate]   (empty before apply)
# POST /api/v1/sync/sessions/{session_id}/speaker-offsets {members: list[str]} -> list[SpeakerOffsetOut]
```

Eligibility (spec "Integration with group sync tuning"): the member is a bridge player in `measure`/`loop`; its measurement is `ok` and was taken warm (the application records `warm_in_s == 0` per measurement at upload/sequence time in a side table keyed by session id — the core is untouched); session point `device`; a reference-grade member measured `ok` in the same session (origin `sendspin`, or a bridge `pulse_sink` player in `loop`); `offset_ms = after_ms(member) − after_ms(reference) + trim_ms(member)`. Saving writes `SpeakerOffset(source="acoustic", reference=<reference name>, spread_ms=<measurement spread>, audio_server, playout_mode, bridge_version)` for each requested eligible member and applies it (Task 4.3 path); ineligible members in the request → 409 `offset_not_eligible` with `member` and `reason`.

- [ ] **Step 1: Failing tests:** kiosk + BT warm in `loop` → candidate eligible with the right value; BT + BT only → `relative_only`; BT measured cold with `force` → `not_warm`; `off` mode → `legacy_mode`; position point → `position_point`; saving stores and applies; saving an ineligible member → 409; `sendspin_sync` boundary test still green.
- [ ] **Step 2–4:** Implement; regenerate contract and OpenAPI. Run → PASS.
- [ ] **Step 5: Commit** `feat(sync): save a measured delay as the Bluetooth speaker's offset`.

### Task 4.6: UI — offsets

**Files:**
- Modify: `ui/src/components/sync/StepTune.vue` (per eligible member "Save as this speaker's offset (SBC · hci0)", ticked; Finish sends the save first), `ui/src/components/devices/DeviceTimingPanel.vue` ("Speaker offset" row with source/age and **Forget**; list of other entries), `ui/src/api/sync.ts`, `ui/src/api/devices.ts`, i18n en/ru, `ui/src/settings/layout.ts` (`PLAYOUT_SPEAKER_OFFSETS` toggle)
- Test: `ui/tests/component/sync/StepTune.test.ts`, `ui/tests/component/devices/DeviceTimingPanel.test.ts`

- [ ] Steps: failing vitest (checkbox only for eligible members; reason text for ineligible ones; Finish calls save then finish; Forget calls the DELETE and refreshes) → implement → `npm test && npm run build` → commit `feat(ui): save and forget speaker offsets`.

### Task 4.7: Changelog for Stage 4

- [ ] `### Added`: "Bluetooth speaker offsets: a delay measured with the phone in group sync tuning can be saved for the speaker, codec and adapter, and is applied automatically on the next connection." Lint, commit.

---

## Stage 5 — Live validation and docs

Shippable: the measured evidence, the user guides, and (after sign-off) `loop` as the beta default.

### Task 5.1: `scripts/sync_phase_trace.py`

**Files:**
- Create: `scripts/sync_phase_trace.py`
- Test: `tests/unit/scripts/test_sync_phase_trace.py`

**Interfaces:** `python scripts/sync_phase_trace.py REC.wav --segments kiosk:0-20,bt:20-140,kiosk:140-160 [--window 6 --step 2] [--csv out.csv]` → per window `(t_s, segment, phase_ms, spread_ms, clicks)` using `sendspin_sync.estimator.estimate` on the decoded recording (`sync_tuning.audio_decode`), then per segment: mean phase, linear slope (ms/s), steps (> 1 ms between consecutive windows), and for `bt`: slope corrected by the reference drift `(kiosk_after − kiosk_before)/Δt` and offset `bt − mean(kiosk)`.

- [ ] **Step 1: Failing tests** on synthetic recordings rendered from `click_track.render_pcm` with a known delay ramp (0.03 ms/s) and a 20 ppm resampled "phone clock": the script reports slope 0.03 ± 0.005 ms/s after the kiosk-bracket correction and an offset within 0.2 ms of the synthetic one; a 3 ms step is listed.
- [ ] **Step 2–4:** Implement → PASS. **Step 5: Commit** `feat(scripts): phase trace for the live sync validation`.

### Task 5.2: `scripts/render_correction_abx.py`

**Files:** Create `scripts/render_correction_abx.py`; test `tests/unit/scripts/test_render_correction_abx.py`.

**Interfaces:** `python scripts/render_correction_abx.py IN.wav --rate 1 --kind drop|insert|mixed --out OUT.wav` applies `corrector.drop_frame/insert_frame` at the given events per second (1, 48) to a 16-bit stereo WAV, plus `--sine 1000` to synthesise the tonal test file.

- [ ] Steps: failing test (output length differs from input by exactly the number of events; events spaced 1/rate s) → implement → PASS → commit `feat(scripts): render insert/drop test files for listening tests`.

### Task 5.3: Live validation on the stand

**Files:** Modify the spec (append "Stage 5 validation results" with the S1–S10 table filled in).

- [ ] **Step 1:** Install the branch build on the HAOS stand (add-on beta) and the Docker VM; set `PLAYOUT_SYNC=loop`; keep production untouched.
- [ ] **Step 2:** Run protocol steps 1–11 of the spec's "Live validation" (ENEBY Portable + one more BT speaker, wired local output, two kiosks). Record the raw recordings outside the repo; record `sync_phase_trace.py` outputs and telemetry snapshots (`curl -s http://<stand>:8080/api/v1/devices/<id> | jq .timing.playout`).
- [ ] **Step 3:** Fill the results table; any failing criterion becomes a fix task (red test first) before Task 5.5. Commit `docs(sync): playout sync — live validation results`.

### Task 5.4: User documentation (en, ru)

**Files:** `docs-site/src/content/docs/group-sync-tuning.md`, `bluetooth-speakers.md`, `configuration.md` and their `ru/` twins.

- [ ] Write: what `PLAYOUT_SYNC` does and how to switch it; warm stream and its battery cost; idle-mode interplay; why the first measurement waits for a warm-up; saving and forgetting speaker offsets; reading the timing panel; troubleshooting (fallback, underruns, saturation warning → check NTP). `cd docs-site && npm run build` passes. Commit `docs: playout sync guides (en, ru)`.

### Task 5.5: Beta default (after sign-off)

**Files:** `config/__init__.py` (`PLAYOUT_SYNC` default `"loop"`), `config/schema.json` default, `tests/unit/config/test_playout_config.py` (the default assertion changes — the commit message explains why: the rollout decision), `CHANGELOG.md`.

- [ ] Only with the user's go-ahead after Task 5.3. `### Changed`: "Bluetooth playout sync is on by default (`PLAYOUT_SYNC=loop`); set it to `off` to return to the previous behaviour." Migration note in the guide. Lint, gates, commit `feat(playout): enable the playout loop by default on beta`.

---

## Self-review checklist (run before handing the plan over)

- Constants identical in spec, Global Constraints and Tasks 1.9/3.2 (B 0.03 Hz, ζ 0.707, Kp 0.2666, Ki 0.03553, dead band 0.1 ms, R_max 1000 ppm, jumps 10/20 ms, LEAD 40 ms, block 10 ms).
- Key names identical everywhere (`PLAYOUT_SYNC`, `PLAYOUT_WARM_HOLD_S`, `PLAYOUT_WARM_IN_GROUP`, `PLAYOUT_WARM_GROUP_MAX_S`, `PLAYOUT_WARMUP_S`, `PLAYOUT_SPEAKER_OFFSETS`, `speaker_offsets`, `static_delay_pre_playout_ms`).
- Every existing test referenced as "unchanged" is untouched; the only intended edit of an existing assertion is Task 5.5's default, explained in its commit.
