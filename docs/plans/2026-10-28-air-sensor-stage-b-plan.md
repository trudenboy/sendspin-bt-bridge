# Air sensor stage B1 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Revision 1 (2026-10-28).

**Goal:** In `PLAYOUT_SYNC=loop` with `EXPERIMENTAL_AIR_SENSOR` on, the playout engine learns per output the bias between its pulsesink clock and the air sensor and places audio so that the air-derived error is zero (calibrated: on the schedule; uncalibrated: at the learned placement). A cold start converges in one correction. First, `measure` (and `loop` without air data) stops snapping the residual at every placement, so a delay change moves the audio by exactly its delta. The rate loop is B2, not here.

**Architecture:** The stage A `AirSensor` (daemon loop) hands every accepted window, now carrying the written-stream-time span `x` it matched, through a lock-protected `AirFeed` to the engine on the feeder thread, together with the lock state, the target latency (calibrated or learned) and the link key. The engine keeps a 30 s history of its pulsesink readings keyed by `x`; a pure `AirBias` state machine (none / seeding / active / held) turns windows into the bias `b = ê − (H + L_eff − d_out)`, seeds it from an agreeing triple with one full correction (`air_seed`), tracks it with τ = 30 s behind a gate, and reacts to engine events. The controller acts on `c = e_w − b − β`; every placement aligns to `audible − o` (`o = b + β` with a bias, else the raw residual). One learned sample per output goes to the parent, which keeps the last 8 per (codec, rate, adapter) in a sibling per-player field.

**Tech Stack:** Python 3.13/3.14 stdlib (`threading`, `statistics`, `math`, `collections.deque`), the existing playout engine and simulation harness, FastAPI + Pydantic v2, pytest (+ xdist), Vue 3 + TS + vitest.

**Spec:** [`docs/plans/2026-10-28-air-sensor-stage-b-design.md`](2026-10-28-air-sensor-stage-b-design.md). Every task's requirements include the spec; decisions are cited as Qn, rulings as S-n.

## Global Constraints

Copied from the spec; every task's requirements include them.

* **`measure` stays observe-only (Q2).** No `AirFeed`, no `AirBias` in `measure`; the engine snapshot has `air_bias_state: null` there. With `EXPERIMENTAL_AIR_SENSOR=false` nothing of stage B exists (stage A's switch-off rule). Every task that adds a hook has a `measure` test and a switch-off test.
* **Q6 everywhere without a bias:** a placement aligns the next frame to `audible − o` with `o = r` (the engine's raw residual `_error_ema_us`, which includes `β`), `0` when no residual exists (the `start` placement); `β` survives placements other than `underrun`/`stall`; `controller.placed(t, residual_us = o − β, quiet_wait=True)`; the published EMA restarts at `o`, not 0.
* **With a bias value** (`active`, `held`, or a provisional value while reseeding): `o = b + β`; controller input `c = e_w − b − β`; published `error_ms` = EMA of `c` (S-18).
* **Definitions:** `E_equiv = H + L_eff − d_out_i` with the **per-window** `d_out` (controller ruling), `L_eff` = calibrated `L_as` for the current key, else the learned value latched at the output's first update (≥ 2 samples), else none; `b_i = ê − E_equiv`, `ê` = median `e = t − s(x_port)` of the usable readings with `x_port` in the window's `[x_lo, x_hi]`, ≥ 50 % coverage (15 of 30 readings for 3 s).
* **Constants (spec "Constants"):** `J = 10 ms` in `loop` (unchanged); `SEED_WINDOWS = 3`; `SEED_AGREE_US = 2000`; `SEED_MAX_WINDOWS = 12`; `DEAD_BAND_US = 2000`; `MIN_CONFIDENCE = 0.5`; `BIAS_TAU_S = 30`; `DT_MAX_S = 5`; `GATE_MIN_US = 10000`, `GATE_MAD_K = 4`, `GATE_HISTORY = 20`; `STEP_WINDOWS = 15`, `STEP_AGREE_US = 3000`; `RESEED_TIMEOUT_S = 15`; `E_HISTORY_S = 30`; `E_MIN_COVERAGE = 0.5`; `FEED_MAX = 32`; `LEARN_MIN_SAMPLES = 2`; `LEARN_KEEP = 8`; learned and calibrated latencies 0…2000 ms.
* **Air jumps** (`air_seed`, `air_target`) bypass the 50 ms clamp and the 10 s interval (Q8), are executed like steady jumps (positive = drop) and are dropped while a placement is pending (S-19).
* **Event exclusion (S-3):** a window whose `x` span contains an engine event's `x` (placement, steady or air jump: the write head at the event) or starts before the last change of `b`'s definition (delay, target change, reseed) is never used for `b`; the seed triple lies wholly after both.
* **Delay change with a bias:** `b += ΔD` (`ΔD = −shift_us`), logged `Output delay change absorbed by the air correction (b … → … ms)` at INFO (S-14).
* **Threading:** `AirBias`, `EHistory` and the engine are feeder-only; `AirFeed` is the only shared object (one `threading.Lock`, O(1) critical sections); the daemon loop never calls the engine; the feeder never calls the sensor (S-1).
* **Containment (S-20):** any `AirBias` exception disables the bias for that output (`air_bias_state: none`, `air_bias_reason: internal_error`), logged once with the traceback, and never reaches the feeder (a feeder exception means `fall_back`).
* **IPC:** `set_air_calibration` gains optional `learned_ms` (clamped 0…2000) and `learned_starts` (int ≥ 2), sent only when a learned value exists; the spawn param `air_sensor` gains the same keys only when known; payload `v: 1`, no protocol bump (S-17).
* **Learned entries (S-13):** per-player sibling field `air_learned_latency`, ≤ 8 entries (one per key), `samples_ms` 1–8, `latency_ms = learned_latency_ms(samples_ms)` (median per Q7 unless open question 2 settles otherwise before B8a), `source: "learned"`; Forget clears both kinds for the key (Q7).
* **Existing tests:** never edit one to make it pass (`CLAUDE.md`). Where a stage B decision changes a requirement a stage A test pinned (named in the task), the task changes that test and the commit message says which decision changed it and why.
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (R5–R9). TDD: red before green.
* No live audio, Bluetooth, MA or bridge actions in any task except B12 (the controller's stand run). Do not touch the bridge on port 8080 or Music Assistant on 8095/8097 while developing.
* **Concurrent work on this branch:** another agent edits `docs/plans/2026-10-28-air-sensor-design.md`, `docs-site/`, `scripts/air_envelope_corr.py`, `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py` and `CHANGELOG.md`. No task before B13 touches them; B13 starts only after that work is committed (rebase first). Commit only the files a task names (`git add <paths>`).

## Review Focus

1. **Signs and units.** `b = ê − E_equiv`, `E_equiv = H + L_eff − d_out_i`, `ΔD = −shift_us`, jumps positive = drop, `o` subtracted from `audible`, `b −= ΔL` on a target change. A sign slip moves audio by twice the bias without failing a shape test; the closed-loop simulation (B6) checks the true heard error. (B0, B4a, B4b, B5, B6)
2. **Q6 does not break R14.** `β` kept across placements, the superseded-jump case, the start placement, the baseline tests of `test_steady_jumps.py` green. (B0)
3. **Thread boundary.** Only `AirFeed` crosses threads; reopen and tap generations keep an old output's window out of a new engine. (B3, B5, B7)
4. **`measure` and switch off unchanged.** (B1, B5, B7)
5. **Learned target provenance.** One sample per output from the initial triple, keyed, persisted once per `(proc, seq)`, active from the 3rd start, latched per output, calibration wins, Forget clears both. (B4a, B4b, B8a, B8b)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/playout/engine.py` | placement offset, `β` kept (B0); `AirBias`, `EHistory`, corrected input, air jumps, events, snapshot (B5) |
| `src/sendspin_bridge/services/audio/playout/air_bias.py` | **new** `AirSample`, `AirTarget`, `FeedView`, `AirFeed`, `EHistory` (B3); `AirAction`, `LearnedSample`, `AirBias` (B4a, B4b) |
| `src/sendspin_bridge/services/audio/playout/air_tap.py` | `x` per cell, `WrittenSlice.x` (B2) |
| `src/sendspin_bridge/services/audio/playout/air_sensor.py` | `AirWindow.x_lo_ns/x_hi_ns/gen`, `on_window` hook, `lock_state()`, `key()` (B2) |
| `src/sendspin_bridge/services/audio/playout/gst_output.py` | `air_feed` passed to the engine, `feed.reopen` (B5) |
| `src/sendspin_bridge/services/ipc/commands.py` | `SetAirCalibration.learned_ms/learned_starts` (B7) |
| `src/sendspin_bridge/services/ipc/bridge_daemon.py` | sensor built in `loop` (B1); `AirFeed` wiring, calibration tee (B7) |
| `src/sendspin_bridge/config/__init__.py` | effective rules (B1); `save_air_learned_sample`, re-exports (B8a) |
| `src/sendspin_bridge/config/players.py`, `config/migration.py`, `config/schema.json` | `AirLearnedEntry` and helpers, cleanup, schema (B8a) |
| `scripts/translate_ha_config.py`, `src/sendspin_bridge/services/infrastructure/config_diff.py` | preserve / hot-apply `air_learned_latency` (B8a) |
| `src/sendspin_bridge/bridge/client.py` | learned target resolve/send, sample persistence (B8b) |
| `src/sendspin_bridge/application/devices.py`, `application/status.py` | Forget clears both; learned entries in the device (B8b, B9) |
| `src/sendspin_bridge/application/models/devices.py` | `PlayoutTiming` keys, `AirLearnedEntryOut` (B9) |
| `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py`, `recovery_assistant.py` | `air_correction_inactive` (B11) |
| `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/i18n/{en,ru}.json`, `ui/src/api/*`, `ui/openapi.json` | panel line, learned entries, texts (B1, B10, B11) |
| `tests/unit/services/audio/playout/sim.py` | `true_port_time_us`, `on_block` hook, `SimAir` (B6) |
| `docs/plans/2026-10-28-air-sensor-stage-b-design.md`, `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}configuration.md`, `CHANGELOG.md` | validation note, user docs, changelog (B13) |

Conventions as in the stage A plan: tests under `tests/unit/...`, one file per task named in the task; run one file with `uv run pytest <path> -q -p no:cacheprovider -n0`; fixtures a task's tests need and that do not exist are defined in that test file.

Order and dependencies: B0 · B1 · B2 → B3 → B4a → B4b → B5 (needs B0, B3, B4b) → B6 → B7 (needs B1, B2, B5) → B8a → B8b (needs B7) → B9 (needs B5, B8a) → B10 → B11 → B12 → B13. B0, B1 and B2 are independent of each other.

## Pre-flight conflict table

Tasks that share a file or an interface; run them in the order given, never in parallel.

| File / interface | Tasks | Note |
|---|---|---|
| `engine.py` (`next_block`, `_record_placement`, `_record_event`, `_sense`) | B0 → B5 | B5 builds on B0's `_placement_offset_us()`; both edit `_record_placement` |
| `tests/unit/services/audio/playout/test_engine.py`, `test_steady_jumps.py` | B0 (must stay green), B5 | B0 may only change a test there that encodes the implicit snap (Q6), with the reason in the commit |
| `air_sensor.py` (`AirWindow`, `_accept`, `_placement`) | B2 → B7 | B7 wires the hook B2 adds |
| `air_tap.py` (`WrittenSlice`) | B2 | `WrittenSlice` gains a field: every constructor call in tests that passes positional args must keep working (add `x` last with a default) |
| `air_bias.py` | B3 → B4a → B4b → B5 | one module, grown task by task |
| `bridge_daemon.py` (`_build_air_sensor`) | B1 → B7 | B1 removes the gate, B7 adds the feed |
| `config/__init__.py` (`air_sensor_effective`) | B1 → B8a | different functions; B8a re-exports learned helpers |
| `bridge/client.py` (spawn params, `_send_air_calibration_once`, status handling) | B1 (only through `air_sensor_effective`), B8b | B8b changes the spawn dict only when a learned value exists |
| `SetAirCalibration` | B7 → B8b | B8b sends what B7 decodes |
| `PlayoutTiming` / snapshot keys | B5 → B9 → B10, B11 | key names fixed in B5 (spec "Telemetry") |
| `ui/src/i18n/{en,ru}.json` | B1, B10, B11 | different keys; B1 only rewrites `timing.air.reasons.needs_measure` and removes `loop_not_supported_yet` |
| Stage A tests pinning `loop_not_supported_yet` | B1 | requirement changed by Q2 (listed in B1) |
| `CHANGELOG.md`, `docs-site/`, the stage A spec | B13 only | edited concurrently by another agent until its work is committed |

---

## Stage B1 — the engine corrects from the air

### Task B0: Placements keep the residual (Q6, the measure-mode implicit snap)

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/engine.py` (`next_block`, `_record_placement`, `_record_event`, new `_placement_offset_us`)
- Test: `tests/unit/services/audio/playout/test_engine_residual.py` (new)

**Interfaces:**

```python
# engine.py
def _placement_offset_us(self) -> float:
    """The error a placement keeps (o): the raw residual (_error_ema_us, baseline included), 0 without one.
    B5 extends it with the air bias."""

def _record_event(self, size_us: float, reason: str, residual_us: float = 0.0) -> None:
    """As today, but the published EMA restarts at residual_us (the error the event keeps)."""
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_engine_residual.py
"""Q6: a placement keeps the residual; a delay change moves the audio by exactly its shift."""

import logging
import re

import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000
ZERO = b"\x00\x00" * 2  # program audio the simulated port does not count as heard
TONE = b"\x10\x00" * 2
ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"
_JUMP = re.compile(r"playout jump: (\w+), (-?[\d.]+) ms")


def engine(mode="measure", **port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode=mode), port=port)
    eng.set_format(InputFormat(sample_rate=FS))
    return eng, port, raw


def zeros_then_marker(eng, raw, *, zero_s, gap_us=0.0):
    """Program silence for ``zero_s`` from 1 s from now, then a tone (``gap_us`` after it)."""
    start = raw.t_us + 1e6
    eng.submit(int(start), ZERO * int(FS * zero_s))
    due = start + zero_s * 1e6 + gap_us
    eng.submit(int(due), TONE * FS)
    return due


def drive_until(eng, port, raw, done):
    for _ in range(100):
        drive(eng, port, raw, 0.1)
        if done(eng.snapshot()):
            return
    raise AssertionError("the event did not happen")


def jumps(caplog):
    return [(m.group(1), float(m.group(2))) for r in caplog.records if (m := _JUMP.search(r.getMessage()))]


# The stand: +30 ms moved ENEBY20 by -43.6 ms because e_w = +13.4 ms was zeroed. In loop the
# residual must stay inside J = 10 ms, else a steady jump removes it before the change.
@pytest.mark.parametrize(("mode", "residual_ms"), [("measure", 13.4), ("loop", 7.0)])
def test_delay_change_moves_the_audio_by_exactly_the_shift(mode, residual_ms):
    eng, port, raw = engine(mode)
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 5.0)
    port.skip(-residual_ms)  # the port falls behind: heard late by the residual, inside the dead band
    drive(eng, port, raw, 8.0)
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)
    assert eng.resync(-30_000) is True  # output delay +30 ms: the queued audio plays 30 ms earlier
    drive(eng, port, raw, 2.0)
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)  # kept, not zeroed
    drive(eng, port, raw, 23.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    assert heard - (due - 30_000) == pytest.approx(residual_ms * 1000, abs=500)  # today: ≈ 0


def test_a_gap_after_an_underrun_baseline_does_not_move_the_audio(caplog):
    eng, port, raw = engine("measure")
    due = zeros_then_marker(eng, raw, zero_s=40.0, gap_us=50_000)  # a 50 ms gap: a "gap" placement
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until(eng, port, raw, lambda s: s["underruns"] >= 1 and s["last_jump_reason"] == "underrun")
        port.slew(-30.0, 300.0)  # the reading settles 30 ms behind the port: an artefact, the audio is on time
        drive(eng, port, raw, 40.0)
    reasons = [r for r, _ in jumps(caplog)]
    assert "gap" in reasons and "steady" not in reasons
    assert eng.snapshot()["error_ms"] == pytest.approx(30.0, abs=3.0)  # the baseline is still reported
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=1_000)  # today: ≈ 30 ms early


def test_a_clear_keeps_the_baseline():
    eng, port, raw = engine("measure")
    zeros_then_marker(eng, raw, zero_s=60.0)
    drive(eng, port, raw, 12.0)
    port.starve(800.0)
    drive_until(eng, port, raw, lambda s: s["underruns"] >= 1 and s["last_jump_reason"] == "underrun")
    port.slew(-30.0, 300.0)
    drive(eng, port, raw, 12.0)
    eng.clear()
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 20)
    drive(eng, port, raw, 15.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] in ("clear", "starved")
    assert snap["error_ms"] == pytest.approx(30.0, abs=3.0)  # today: the clear zeroes it (and moves the audio 30 ms)
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_engine_residual.py -q -p no:cacheprovider -n0` → FAIL (the residual is zeroed, the marker is heard at the shifted time, the gap moves the audio 30 ms).
- [ ] **Step 3: Implement.** In `next_block` compute `o = self._placement_offset_us()` once and pass `audible − o` wherever `audible` goes today (both `reanchor` calls, `render`), with `size_us = (audible − o) − s_old`. In `_record_placement(reason, size_us, t_s)`: `self._controller.placed(t_s, residual_us=o − self._baseline_us, quiet_wait=True)` and `self._record_event(size_us, reason, residual_us=o)`; reset `_baseline_us`/`_baseline_degraded` only when `reason in _BASELINE_REASONS` (a fresh baseline is collected then, as today; a placement while one is being measured still restarts the collection). `o` is the value used for that render (keep it on `self` for the record call). `_record_event` sets `self._error_ema_us = residual_us`; steady jumps pass the default. The `start` placement gets `o = 0` naturally (`_error_ema_us` is `None` before it). Update the module docstring ("Placements … reset the controller") and the comment at engine.py:497-499.
- [ ] **Step 4: Run** the new file, then `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` → PASS. `test_jump_is_never_passed_while_a_placement_is_pending` and the baseline tests in `test_steady_jumps.py` must stay green unchanged (the controller resets its EMA to the post-jump value when it decides a jump, so a superseding placement keeps ≈ 0). If an existing test fails because it asserts an error of ≈ 0 right after a placement **that had a residual to keep**, it encodes the bug Q6 removes: change only that assertion, and name the test and Q6 in the commit message; any other failure is a bug in the implementation.
- [ ] **Step 5: Commit** `fix(playout): placements keep the residual, so a delay change moves audio by exactly its delta (Q6)`.

### Task B1: Effective rules — the air sensor in `loop`

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`air_sensor_effective`, new `air_correction_effective`), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`_build_air_sensor`: build in `measure` and `loop`), `src/sendspin_bridge/config/schema.json` (switch description), `ui/src/i18n/{en,ru}.json` (`timing.air.reasons.needs_measure` → "Needs playout sync in measure or loop mode." / "Нужна синхронизация воспроизведения в режиме measure или loop."; remove `loop_not_supported_yet`; settings help of the switch: "… Measures in measure mode; corrects placement in loop mode."), the `EXPERIMENTAL_AIR_SENSOR` comment in `DEFAULT_CONFIG`
- Test: `tests/unit/config/test_air_sensor_config.py`, `tests/unit/services/ipc/test_bridge_daemon_air.py`, `tests/unit/api/test_air_sensor_api.py` (requirement change, below), `tests/unit/config/test_air_correction_config.py` (new)

**Interfaces:**

```python
# config/__init__.py
def air_sensor_effective(player_cfg, cfg) -> tuple[bool, str | None]:
    """On when the switch is on, the effective playout mode is 'measure' or 'loop' and the backend is
    Bluetooth; reasons 'switch_off', 'needs_measure' (mode off), 'not_bluetooth'."""

def air_correction_effective(player_cfg, cfg) -> tuple[bool, str | None]:
    """On when the effective playout mode is 'loop' and the air sensor is effective; reasons
    'not_loop' (measure: observe only), 'switch_off', 'needs_measure', 'not_bluetooth'."""
```

Requirement change (Q2): four stage A tests pinned "no sensor in `loop`". Change exactly these, and say in the commit message that Q2 (2026-10-28) moved the air sensor into `loop`:
- `tests/unit/config/test_air_sensor_config.py:27`: `(… "PLAYOUT_SYNC": "loop"}, BT, (False, "loop_not_supported_yet"))` → `(True, None)`;
- `tests/unit/services/ipc/test_bridge_daemon_air.py:79` (`test_a_mode_other_than_measure_ignores_the_param`): parametrize `["off"]` only, plus a new test that `loop` builds the sensor;
- `tests/unit/services/ipc/test_bridge_daemon_air.py:612`: drop the `loop` config from `test_a_sensor_that_is_not_effective_adds_no_spawn_param`, plus a new test that `loop` asks for the sensor at spawn;
- `tests/unit/api/test_air_sensor_api.py:266-268` (`test_switch_on_loop_mode_reports_off_with_its_reason`): replace by a test that `loop` gets no `off` status from the parent (the daemon publishes the sensor's own status).

- [ ] **Step 1: Failing tests** (the changed ones above, and):

```python
# tests/unit/config/test_air_correction_config.py
import pytest

from sendspin_bridge.config import air_correction_effective, air_sensor_effective

BT = {"backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}}


@pytest.mark.parametrize(
    ("cfg", "player", "expected"),
    [
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, BT, (True, None)),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"}, BT, (False, "not_loop")),
        ({"EXPERIMENTAL_AIR_SENSOR": False, "PLAYOUT_SYNC": "loop"}, BT, (False, "switch_off")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, {**BT, "playout_sync_enabled": False}, (False, "needs_measure")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, {"backend": {"type": "pulse_sink", "sink": "x"}}, (False, "not_bluetooth")),
        ({}, BT, (False, "switch_off")),  # both default off (Q11)
    ],
)
def test_correction_rule(cfg, player, expected):
    assert air_correction_effective(player, cfg) == expected


def test_sensor_runs_in_loop():
    assert air_sensor_effective(BT, {"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}) == (True, None)
```

Daemon test (in `test_bridge_daemon_air.py`, using its `make_daemon` and `ON` fixtures): `make_daemon(playout_mode="loop", air_sensor=ON)` → `d.air_snapshot()["state"]` is a sensor state and `d.air_accepts() == [1]`.

- [ ] **Step 2: Run** the four files → FAIL.
- [ ] **Step 3: Implement** the rules (the `loop` branch goes, `measure`/`loop` both pass on to the backend check; `air_correction_effective` returns `not_loop` before the sensor check when the mode is `measure`, else the sensor's reason), the daemon gate `if self._args.playout_mode not in ("measure", "loop"): return`, the texts. The parent's spawn code (bridge/client.py:2292-2298) needs no change: it already asks for the sensor whenever `air_sensor_effective` says so.
- [ ] **Step 4: Run** `uv run pytest tests/unit/config tests/unit/services/ipc tests/unit/api -q -p no:cacheprovider` and `cd ui && npm test` → PASS.
- [ ] **Step 5: Commit** `feat(air): the air sensor also runs in playout loop mode (observe only until the correction lands)`.

### Task B2: Written stream time per cell and per window

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_tap.py` (an `array('d')` ring of `x` per cell, filled from `block.x0_ns` + `i·CELL_FRAMES·1e9/rate`; `WrittenSlice.x`), `src/sendspin_bridge/services/audio/playout/air_sensor.py` (`AirWindow.x_lo_ns`, `x_hi_ns`, `gen`; `_placement` returns them; `on_window` hook; `lock_state()`, `key()`)
- Test: `tests/unit/services/audio/playout/test_air_tap.py` (extend), `tests/unit/services/audio/playout/test_air_sensor.py` (extend)

**Interfaces:**

```python
# air_tap.py
@dataclass(frozen=True, slots=True)
class WrittenSlice:
    ...                       # unchanged fields
    gen: int = 0
    x: array | None = None    # 'd' written stream time (ns) of each cell's first frame; last, with a default

# air_sensor.py
@dataclass(frozen=True, slots=True)
class AirWindow:
    at_us: float; lag: float; h_us: float; d_out_us: float; psr: float; s_mid: float
    x_lo_ns: float = math.nan   # x of the first matched written cell
    x_hi_ns: float = math.nan   # x of the end of the last matched written cell
    gen: int = 0                # tap generation of the slice

class AirSensor:
    def __init__(..., on_window: Callable[[AirWindow, float], None] | None = None) -> None
        # called on the daemon loop after every _accept(win) with (win, confidence now); exceptions logged at DEBUG
    def lock_state(self) -> tuple[str, float | None]   # (state, confidence) as the snapshot would report
    def key(self) -> tuple[str | None, int | None, str | None]  # (codec, rate, adapter)
```

- [ ] **Step 1: Failing tests:** tap — after two blocks of 10 ms at 48 kHz starting at `x0 = 5e9`, `slice(0, n).x[k] == 5e9 + k·128e9/48000` (exact to 1 ns), the ring wraps and keeps `x` aligned with `s`; after `reset()` a new block restarts `x` from its own `x0`; a format change keeps `x` continuous (the engine's `x` continues across a format epoch). Sensor — with the stage A test fakes, a confirmed window's `x_lo_ns`/`x_hi_ns` equal the tap's `x` of the matched span's first cell and the end of its last (`x_hi − x_lo ≈ 3 s` within one cell), `gen` equals the slice's; `on_window` is called once per accepted window (twice when a candidate is confirmed: candidate first) with the confidence `_confidence("locked", now)`; an `on_window` that raises does not change the sensor's state; `lock_state()` and `key()` match `snapshot()`'s `state`, `confidence`, `codec`, `rate`, `adapter`.
- [ ] **Step 2: Run** both files → FAIL.
- [ ] **Step 3: Implement.** In `_placement` the matched span is `lo..hi` of the slice (local indexes); `x_lo = sl.x[lo]`, `x_hi = sl.x[hi] + cell_ns`; keep the slice's `x` in `_Plan` (new field `x: array`). `_accept` calls the hook after updating its state. Memory: 20 s × 345 cells/s × 8 B ≈ 55 KB more per daemon.
- [ ] **Step 4: Run** `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` (incl. `test_air_sim.py`) → PASS.
- [ ] **Step 5: Commit** `feat(air): windows carry the written stream time they matched`.

### Task B3: `AirFeed` and `EHistory`

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_bias.py` (this part: constants, `AirSample`, `AirTarget`, `FeedView`, `AirFeed`, `EHistory`)
- Test: `tests/unit/services/audio/playout/test_air_feed.py`

**Interfaces:**

```python
FEED_MAX = 32
E_HISTORY_S = 30.0
E_MIN_COVERAGE = 0.5
SENSE_HZ = 10.0
LEARN_MIN_SAMPLES = 2

@dataclass(frozen=True, slots=True)
class AirSample:
    tap_gen: int
    x_lo_ns: float
    x_hi_ns: float
    at_us: float      # air time of the window middle
    h_us: float
    d_out_us: float
    confidence: float

@dataclass(frozen=True, slots=True)
class AirTarget:
    latency_ms: float | None                       # L_eff candidate: the calibration if any, else the learned value
    source: Literal["calibrated", "learned"] | None
    learned_ms: float | None                       # kept so a forgotten calibration can fall back
    learned_starts: int | None
    version: int                                   # bumped by every set_target

@dataclass(frozen=True, slots=True)
class FeedView:
    samples: tuple[AirSample, ...]
    lock: tuple[str, float | None]                 # sensor state, confidence; ("no_signal", None) until set
    target: AirTarget
    key: tuple[str | None, int | None, str | None]

class AirFeed:
    """The only object shared by the daemon loop (producer) and the feeder thread (consumer)."""
    dropped: int
    def push(self, sample: AirSample) -> None        # refused when sample.tap_gen < the reopen generation; drop-oldest beyond FEED_MAX
    def set_lock(self, state: str, confidence: float | None) -> None
    def set_target(self, *, latency_ms: float | None, learned_ms: float | None, learned_starts: int | None) -> None
        # source = "calibrated" if latency_ms is not None, else "learned" if learned_ms is not None and learned_starts >= 2, else None
    def set_key(self, codec: str | None, rate: int | None, adapter: str | None) -> None
    def reopen(self, min_tap_gen: int) -> None       # drops queued samples
    def take(self) -> FeedView                       # drains samples; the other values are the latest
    def next_learned_seq(self) -> int                # 1, 2, … for this daemon (survives engines)

class EHistory:
    """Pulsesink readings keyed by written stream time (feeder-only)."""
    def add(self, t_us: float, x_port_ns: float, e_us: float, *, usable: bool) -> None   # prunes t older than E_HISTORY_S
    def median_over(self, x_lo_ns: float, x_hi_ns: float) -> tuple[float, int] | None
        # (median e of the usable readings with x_lo <= x_port < x_hi, their count); None below
        # E_MIN_COVERAGE · SENSE_HZ · (x_hi − x_lo)/1e9 usable readings
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_air_feed.py
import threading

from sendspin_bridge.services.audio.playout.air_bias import FEED_MAX, AirFeed, AirSample, EHistory

ADAPTER = "C0:FB:F9:62:D7:D6"


def sample(i, *, gen=1, x0=10e9, h=-35_000.0, d_out=184_000.0, conf=1.0):
    return AirSample(gen, x0 + i * 1e9, x0 + i * 1e9 + 3e9, 1e6 * i, h, d_out, conf)


def test_take_drains_samples_in_order_and_keeps_the_latest_values():
    f = AirFeed()
    f.push(sample(0))
    f.push(sample(1))
    f.set_lock("locked", 0.9)
    f.set_target(latency_ms=228.7, learned_ms=None, learned_starts=None)
    f.set_key("sbc", 44100, ADAPTER)
    v = f.take()
    assert [s.at_us for s in v.samples] == [0.0, 1e6]
    assert v.lock == ("locked", 0.9) and v.key == ("sbc", 44100, ADAPTER)
    assert (v.target.latency_ms, v.target.source) == (228.7, "calibrated")
    again = f.take()
    assert again.samples == () and again.target.version == v.target.version


def test_target_sources():
    f = AirFeed()
    assert f.take().target.source is None
    f.set_target(latency_ms=None, learned_ms=226.0, learned_starts=1)
    assert f.take().target.source is None  # active from the 3rd start: two samples needed
    f.set_target(latency_ms=None, learned_ms=226.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms) == ("learned", 226.0)
    f.set_target(latency_ms=228.7, learned_ms=226.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms, t.learned_ms) == ("calibrated", 228.7, 226.0)


def test_feed_is_bounded_drop_oldest():
    f = AirFeed()
    for i in range(FEED_MAX + 5):
        f.push(sample(i))
    got = f.take().samples
    assert len(got) == FEED_MAX and got[0].at_us == 5e6 and f.dropped == 5


def test_reopen_drops_queued_and_refuses_older_generations():
    f = AirFeed()
    f.push(sample(0, gen=1))
    f.reopen(min_tap_gen=2)
    f.push(sample(1, gen=1))  # computed on the old output, accepted after the reopen
    f.push(sample(2, gen=2))
    assert [s.at_us for s in f.take().samples] == [2e6]


def test_learned_seq_counts_per_feed():
    f = AirFeed()
    assert (f.next_learned_seq(), f.next_learned_seq()) == (1, 2)


def test_push_and_take_from_two_threads():
    f = AirFeed()
    got: list[float] = []
    done = threading.Event()

    def consume():
        while not done.is_set():
            got.extend(s.at_us for s in f.take().samples)
        got.extend(s.at_us for s in f.take().samples)

    t = threading.Thread(target=consume)
    t.start()
    for i in range(10_000):
        f.push(sample(i))
    done.set()
    t.join()
    assert len(got) + f.dropped == 10_000 and got == sorted(got)


def test_history_median_over_a_span_uses_usable_readings_only():
    h = EHistory()
    for k in range(60):  # 6 s at 10 Hz
        h.add(1e6 + k * 1e5, k * 1e8, 1000.0 * (k % 3), usable=k != 20)
    med, n = h.median_over(0.0, 3e9)
    assert med == 1000.0 and n == 29


def test_history_below_half_coverage_is_none():
    h = EHistory()
    for k in range(14):  # 14 readings in a 3 s span: below 15
        h.add(1e6 + k * 1e5, k * 2e8, 500.0, usable=True)
    assert h.median_over(0.0, 3e9) is None


def test_history_forgets_readings_older_than_30_s():
    h = EHistory()
    for k in range(400):  # 40 s
        h.add(k * 1e5, k * 1e8, 100.0, usable=True)
    assert h.median_over(0.0, 3e9) is None and h.median_over(30e9, 33e9) is not None
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_air_feed.py -q -p no:cacheprovider -n0` → FAIL (no module).
- [ ] **Step 3: Implement.** `AirFeed`: one `threading.Lock`; samples in a `deque(maxlen=FEED_MAX)` with an explicit `dropped` count (count before append when full); `take` swaps the deque under the lock and returns a tuple. `EHistory`: a `deque` of `(t, x, e, usable)`; `x` increases within an output, so `median_over` bisects on a list of `x` copied per call (≤ 300 entries) or scans; prune on `add`. Module docstring: the thread contract (S-1) and the coverage rule (S-2).
- [ ] **Step 4: Run** → PASS (also with `-n 4` to shake the thread test).
- [ ] **Step 5: Commit** `feat(air): the feed between the air sensor and the playout engine, and the reading history keyed by x`.

### Task B4a: `AirBias` — seeding, tracking, gate, step, learned sample

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_bias.py`
- Test: `tests/unit/services/audio/playout/test_air_bias.py` (new; seeding/tracking part)

**Interfaces:**

```python
SEED_WINDOWS = 3
SEED_AGREE_US = 2_000.0
SEED_MAX_WINDOWS = 12
DEAD_BAND_US = 2_000.0
MIN_CONFIDENCE = 0.5
BIAS_TAU_S = 30.0
DT_MAX_S = 5.0
GATE_MIN_US = 10_000.0
GATE_MAD_K = 4.0
GATE_HISTORY = 20
STEP_WINDOWS = 15
STEP_AGREE_US = 3_000.0
RESEED_TIMEOUT_S = 15.0

BiasState = Literal["none", "seeding", "active", "held"]

@dataclass(frozen=True, slots=True)
class AirAction:
    jump_us: float                          # + drop (late), − insert
    reason: Literal["air_seed", "air_target"]

@dataclass(frozen=True, slots=True)
class LearnedSample:
    seq: int
    x_ms: float
    codec: str
    rate: int
    adapter: str

class AirBias:
    def __init__(self, feed: AirFeed, *, log: logging.Logger = logger) -> None
    def update(self, now_s: float, history: EHistory) -> AirAction | None
        # takes the feed's view, applies lock/target changes, then every sample in order
    def on_event(self, x_ns: float) -> None   # a placement or a steady/air jump written at x (S-3)
    @property
    def state(self) -> BiasState
    @property
    def value_us(self) -> float | None       # b, provisional included
    @property
    def frozen(self) -> bool                 # the controller waits (reseed after a reading break, B4b)
    @property
    def learned(self) -> LearnedSample | None   # the newest sample of this output (at most one)
    def snapshot(self) -> dict[str, object]  # the air_* keys of the spec's "Playout snapshot" table, air_learned as a dict
```

Rules for this task (spec "The `AirBias` state machine", "Tracking", "Learned target"):
- A sample is **eligible** when `confidence ≥ MIN_CONFIDENCE`, `x_lo ≥` the newest event `x` and the last definition change `x` (S-3), no event `x` lies inside `(x_lo, x_hi)`, and `history.median_over(x_lo, x_hi)` is not None. Ineligible samples are counted in `air_bias_skipped` (snapshot key, diagnostics) and ignored.
- Target: on the first `update` the target in the view is latched (`L_eff` = its `latency_ms`; a learned value only here); `state = "seeding"` with a target, `"none"` without (`air_bias_reason = "no_target"`).
- Seeding (and, in `none`, learning only): keep the newest three eligible samples; they agree when `max(E) − min(E) ≤ SEED_AGREE_US` with `E_i = h_i + L_eff·1000 − d_out_i` (in `none`, agreement uses `h_i − d_out_i`, which differs from `E_i` by a constant). On the **first** agreement of the output emit the learned sample `x_ms = median(d_out_i − h_i)/1000` with the feed's key and `feed.next_learned_seq()` (no key → none). With a target: `b = median(ê_i − E_i)`, `state = "active"`, return `AirAction(+median(E_i), "air_seed")` when `|median(E_i)| > DEAD_BAND_US`. After `SEED_MAX_WINDOWS` eligible samples without agreement: `b = median(b_i of the last 5)`, `active`, no action, no learned sample.
- Tracking in `active`: `b_i = ê − E_i`; gate `max(GATE_MIN_US, GATE_MAD_K·1.4826·MAD(last GATE_HISTORY b_i))`; inside the gate `b += (1 − exp(−Δt/BIAS_TAU_S))·(b_i − b)` with `Δt = clamp((at_i − at_prev)/1e6, 0, DT_MAX_S)`; outside it counts `air_bias_gated`, and `STEP_WINDOWS` consecutive gated samples within `STEP_AGREE_US` of each other set `b` to their median (INFO `air bias step: … → … ms`), no action.

- [ ] **Step 1: Failing tests** (a `FakeHistory` returning a fixed `(ê, 30)` per span or `None`; samples built with `h`, `d_out` and `x` spans 1 s apart):

```python
# tests/unit/services/audio/playout/test_air_bias.py (part 1)
import math

import pytest

from sendspin_bridge.services.audio.playout.air_bias import (
    DEAD_BAND_US, SEED_MAX_WINDOWS, STEP_WINDOWS, AirBias, AirFeed, AirSample,
)

L_AS = 228.7
D_OUT = 184_000.0
KEY = ("sbc", 44100, "C0:FB:F9:62:D7:D6")


class FakeHistory:
    def __init__(self, e_us=5_000.0):
        self.e_us = e_us

    def median_over(self, lo, hi):
        return None if self.e_us is None else (self.e_us, 30)


def win(i, h_us, *, conf=1.0, d_out=D_OUT, gen=1):
    return AirSample(gen, 20e9 + i * 1e9, 23e9 + i * 1e9, 20e6 + i * 1e6, h_us, d_out, conf)


def bias(*, latency=L_AS, learned=None, starts=None):
    feed = AirFeed()
    feed.set_key(*KEY)
    feed.set_lock("locked", 1.0)
    feed.set_target(latency_ms=latency, learned_ms=learned, learned_starts=starts)
    return AirBias(feed), feed


def run(b, feed, hist, samples, t0=20.0):
    actions = []
    for k, s in enumerate(samples):
        feed.push(s)
        a = b.update(t0 + k, hist)
        if a is not None:
            actions.append(a)
    return actions


def test_three_agreeing_windows_seed_with_one_full_correction():
    b, feed = bias()
    hist = FakeHistory(e_us=5_000.0)
    # A late start: H = -6 ms gives E = H + L_as - D_out = +38.7 ms (the three within 0.7 ms)
    acts = run(b, feed, hist, [win(i, -6_000.0 + d) for i, d in enumerate((0.0, 300.0, -400.0))])
    assert b.state == "active"
    e_med = -6_000.0 + L_AS * 1000 - D_OUT
    assert len(acts) == 1 and acts[0].reason == "air_seed"
    assert acts[0].jump_us == pytest.approx(e_med, abs=1.0)  # no 50 ms clamp, positive = late = drop
    assert b.value_us == pytest.approx(5_000.0 - e_med, abs=1.0)


def test_inside_the_dead_band_no_jump():
    b, feed = bias()
    h = D_OUT - L_AS * 1000 + 1_500.0  # E = +1.5 ms
    assert run(b, feed, FakeHistory(), [win(i, h) for i in range(3)]) == []
    assert b.state == "active"


def test_disagreeing_windows_slide_and_fall_back_after_12():
    b, feed = bias()
    hs = [(-6_000.0 if i % 2 else -10_000.0) for i in range(SEED_MAX_WINDOWS)]  # 4 ms apart: never agree
    acts = run(b, feed, FakeHistory(), [win(i, h) for i, h in enumerate(hs)])
    assert acts == [] and b.state == "active" and b.learned is None


def test_low_confidence_or_thin_coverage_is_not_eligible():
    b, feed = bias()
    run(b, feed, FakeHistory(), [win(i, -6_000.0, conf=0.4) for i in range(5)])
    run(b, feed, FakeHistory(e_us=None), [win(i + 5, -6_000.0) for i in range(5)])
    assert b.state == "seeding" and b.snapshot()["air_bias_skipped"] == 10


def test_tracking_follows_with_tau_30_s():
    b, feed = bias()
    h = D_OUT - L_AS * 1000  # E = 0
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    run(b, feed, FakeHistory(8_000.0), [win(i + 3, h) for i in range(30)], t0=23.0)  # b_i 3 ms higher for 30 s
    assert b.value_us == pytest.approx(5_000.0 + 3_000.0 * (1 - math.exp(-1.0)), abs=150.0)


def test_one_wrong_window_is_gated():
    b, feed = bias()
    h = D_OUT - L_AS * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    before = b.value_us
    run(b, feed, FakeHistory(5_000.0), [win(3, h - 50_000.0)], t0=23.0)
    assert abs(b.value_us - before) < 100.0 and b.snapshot()["air_bias_gated"] == 1


def test_a_persistent_hidden_step_is_absorbed_after_15_windows():
    b, feed = bias()
    h = D_OUT - L_AS * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    run(b, feed, FakeHistory(5_000.0), [win(i + 3, h + 25_000.0) for i in range(STEP_WINDOWS)], t0=23.0)
    assert b.value_us == pytest.approx(5_000.0 - 25_000.0, abs=200.0)


def test_learned_sample_once_per_output_from_the_initial_triple():
    b, feed = bias(latency=None)  # no target: learning only
    h = -35_000.0
    run(b, feed, FakeHistory(), [win(i, h) for i in range(6)])
    s = b.learned
    assert b.state == "none" and s is not None and s.seq == 1
    assert (s.codec, s.rate, s.adapter) == KEY and s.x_ms == pytest.approx((D_OUT - h) / 1000, abs=0.01)
    run(b, feed, FakeHistory(), [win(i + 6, h) for i in range(6)], t0=26.0)
    assert b.learned.seq == 1  # still the one sample


def test_no_key_no_learned_sample():
    feed = AirFeed()
    feed.set_lock("locked", 1.0)
    b = AirBias(feed)
    run(b, feed, FakeHistory(), [win(i, -35_000.0) for i in range(3)])
    assert b.learned is None


def test_events_inside_a_window_exclude_it():
    b, feed = bias()
    b.on_event(21.5e9)  # inside the first window's span
    run(b, feed, FakeHistory(), [win(i, -6_000.0) for i in range(3)])
    assert b.state == "seeding"  # the triple must start after 21.5e9: two more windows needed
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the rules above; everything in µs internally, `L_eff` converted once; `update` wraps nothing (the engine contains exceptions, B5). Snapshot keys: `air_bias_state`, `air_bias_ms`, `air_target`, `air_target_ms`, `air_learned_starts`, `air_corrections` (incremented here when an action is returned), `air_last_correction_ms`, `air_bias_gated`, `air_bias_skipped`, `air_bias_reason`, `air_learned` (`{"seq","x_ms","codec","rate","adapter"}` or None). INFO on seeding (`air bias seeded at %.1f ms (E %+.1f ms, 3 windows within %.1f ms)`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(air): the bias filter — seed, tracking, gate and learned sample`.

### Task B4b: `AirBias` — events and target changes

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_bias.py`
- Test: `tests/unit/services/audio/playout/test_air_bias.py` (part 2: events)

**Interfaces:**

```python
class AirBias:
    def on_delay(self, delta_d_us: float, x_ns: float) -> None          # ΔD = new − old delay
    def on_format(self, x_ns: float) -> None
    def on_reading_break(self, kind: Literal["underrun", "stall", "pa_step"], x_ns: float, now_s: float) -> None
    @property
    def handles_breaks(self) -> bool   # True in active / provisional seeding: the engine skips today's baseline
```

Rules (spec table): delay → `b += ΔD` when a value exists, definition change at `x` (INFO absorbed line with old/new `b`); format → provisional reseed (`seeding`, value kept, triple restarts, definition change at `x`); reading break in `active` or provisional `seeding` → provisional reseed, `frozen = True` until the reseed or `RESEED_TIMEOUT_S` (then `active` with the provisional value, `frozen = False`, `handles_breaks` becomes False until the next seed, so the engine measures today's baseline on `c`); in `held` → no change (`handles_breaks` False); in `none`/initial `seeding` → triple restarts. Lock: a view lock state other than `locked`/`holding` → `active`/provisional `seeding` → `held` (`air_bias_reason = "not_locked"`, INFO `air bias held (sensor <state>)`); `held` + an eligible sample → `active` with a tracking update, no action. Target (versions after the latch): calibrated `latency_ms` changed by `ΔL` while a value exists → `b −= ΔL`, definition change, return `AirAction(+ΔL, "air_target")` when `|ΔL| > DEAD_BAND_US`; a calibration appearing in `none` → `seeding`; calibration gone → fall back to the **latched** learned value (`ΔL` as above) or → `none` (`no_target`, value dropped; the engine then keeps the audio, B5); learned-only changes ignored. A reseed's agreeing triple replaces the value (no filtering across) and may return `air_seed`; it never emits a learned sample.

- [ ] **Step 1: Failing tests** (same helpers as part 1): delay `+30 ms` in `active` → `value_us` +30 000, a window with `x_lo` before the delay's `x` is skipped (`air_bias_skipped` +1), one after it updates; format → `state == "seeding"`, `value_us` unchanged, next agreeing triple with the same `E` → no action, a triple 5 ms off → `air_seed` of 5 ms; underrun in `active` → `frozen` and `handles_breaks`, triple → `active`, not frozen; no triple within 15 s (`now_s` advanced) → `active`, not frozen, `handles_breaks` False; underrun in `held` → nothing changes; lock `no_lock` → `held`, value constant over 60 s of samples with no eligible ones; lock back + one sample → `active`, no action; calibration 228.7 → 248.7 → `air_target` `+20 000`, value −20 000; calibration forgotten with a latched learned 226.0 → `air_target` of `(226.0 − 248.7)·1000`; forgotten without learned → `none`, `value_us is None`; a learned value set after the first update in `none` → still `none`; a reseed never sets a second `learned`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** the whole file → PASS.
- [ ] **Step 5: Commit** `feat(air): bias events — delay, format, reading breaks, lock and target changes`.

### Task B5: Engine — corrected input, bias-aware placements, air jumps, snapshot

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/engine.py`, `src/sendspin_bridge/services/audio/playout/gst_output.py` (`PlayoutPlayer(air_feed=…)`, the engine gets it in `adopt_output`, `_reopen_air_tap` calls `feed.reopen(tap.gen)` after `tap.reset()`)
- Test: `tests/unit/services/audio/playout/test_engine_air.py` (new), `tests/unit/services/audio/playout/test_gst_output.py` (extend)

**Interfaces:**

```python
class PlayoutEngine:
    def __init__(self, *, settings, port, k0_ns=0, wall_iso=_wall_iso, air_feed: AirFeed | None = None) -> None
        # AirBias + EHistory only when settings.mode == "loop" and air_feed is not None
```

Changes (spec "The engine's three changes"):
- `_sense`: after `s_port`, `history.add(r.t_us, r.x_port_ns, r.t_us − s_port, usable=…)` (usable: not stalled, not in underrun, `s_port` known); then `action = bias.update(t_s, history)` inside `try` (S-20); an action sets `_pending_jump_us`/`_pending_jump_reason` (dropped if a placement is pending, as steady jumps are). New underrun / stall / PA step seen in `_sense` → `bias.on_reading_break(kind, r.x_port_ns, t_s)`.
- Controller input: `e_rel = e_w − β − (bias.value_us or 0.0)`; `if bias.frozen: return` (like the baseline wait); published EMA as today (it is then the EMA of `c`).
- `_placement_offset_us()`: `bias.value_us + β` when a value exists, else B0's rule.
- `_record_placement`: skip today's baseline collection when `bias.handles_breaks`; every `_record_event` calls `bias.on_event(self._sched.x_w_ns)`; `resync(shift)` calls `bias.on_delay(−shift, x_w)` before the placement request; `set_format` with a previous format calls `bias.on_format(x_w)`.
- A bias that goes `none` from `active` (calibration forgotten, no learned) re-bases the controller once: `controller.placed(t, residual_us = c_ema + b_old, quiet_wait=True)`, so the audio stays where it is.
- `_record_event` logs the air reasons as `playout jump: air_seed, 37.2 ms`; the controller is reset with `placed(t, 0.0, quiet_wait=True)` after an air jump.
- `snapshot()` merges `bias.snapshot()` (keys present with `None`/0 values when there is no bias, `air_bias_state: None`); the 10 s DEBUG summary adds `b` and the state.

- [ ] **Step 1: Failing tests** (`tests/unit/services/audio/playout/test_engine_air.py`, with the sim and a real `AirFeed` fed by hand): `measure` with a feed → no bias, `air_bias_state is None`; `loop` without a feed → same; `loop` with a feed and a target, after three pushed samples whose `E` is +37 ms → one `air_seed` jump of +37 ms (`last_jump_reason == "air_seed"`, `snapshot()["air_corrections"] == 1`), no clamp message; afterwards `error_ms` ≈ 0 ± 1.5 (EMA of `c`) while the raw pulsesink error is ≈ `air_bias_ms`; a `clear` placement afterwards keeps `c` ≈ 0 (`error_ms` within ±1.5 ms 2 s after it, `air_bias_ms` unchanged); an air action while a placement is pending is dropped (no second jump); underrun in `active` → no "error baseline" INFO line, controller frozen until three new samples; an `AirBias` whose `update` raises → `air_bias_state == "none"`, `air_bias_reason == "internal_error"`, one ERROR log with traceback, the engine keeps playing (`state == "playing"`); delay change `+30 ms` in `active` → the marker is heard where it would have been without the change (± 0.5 ms) and the INFO absorbed line appears. `test_gst_output.py`: `adopt_output` passes the feed to the new engine and `feed.reopen` is called with the tap's generation after its reset (fakes as in the existing file).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` → PASS (B0's tests unchanged).
- [ ] **Step 5: Commit** `feat(playout): loop mode corrects placement from the air bias`.

### Task B6: Closed-loop simulation acceptance

**Files:**
- Modify: `tests/unit/services/audio/playout/sim.py` (`SimulatedPort.true_port_time_us(x_ns)`, `drive(..., on_block=None)`, new `SimAir`)
- Create: `tests/unit/services/audio/playout/test_air_closed_loop.py`

**Interfaces:**

```python
# sim.py
class SimulatedPort:
    def true_port_time_us(self, x_ns: float) -> float | None
        """RAW time the output frame at stream time x left the (true) port: from a log of
        (x0, ring position) per delivery and the piecewise-linear A(t) (rate, stalls, skips as steps)."""

def drive(..., on_block: Callable[[Block], None] | None = None) -> None   # called after every rendered block

class SimAir:
    """Air windows from the simulation's truth, delivered to an AirFeed like the daemon would."""
    def __init__(self, feed, port, engine, *, h0_us, sigma_h_us=500.0, seed=1, window_s=3.0, hop_s=1.0,
                 pipeline_us=250_000.0, d_out_us=184_000.0, tap_gen=1, confidence=1.0) -> None
    def on_block(self, block) -> None      # records (x0, duration, s(x0) from engine.schedule_at, d_out) at write time
    def tick(self, now_us: float) -> None  # every hop: the window of air in [now − pipeline − window, now − pipeline]
        # t_air(x) = true_port_time_us(x) + h0_us; H = t_air(x_mid) − s(x_mid) + N(0, sigma_h);
        # pushes AirSample(tap_gen, x_lo, x_hi, at=t_air(x_mid), h, d_out(x_mid), confidence); feed.set_lock("locked", …)
    def set_d_out(self, d_out_us: float) -> None
    def stop(self) / resume(self)          # no windows while stopped (lock lost after 20 s: set_lock("no_lock", None))
    def heard_error_us(self, x_ns: float, l_true_ms: float) -> float  # truth: t_air + L_true − (s + d_out)
```

Each test builds a fresh engine per start (`PlayoutSettings(mode="loop")`, the feed reopened with `tap_gen + 1`), submits 40 s of tone, calls `SimAir.tick` from `drive(on_sense=…)` and `on_block`, and measures the true heard error as the median of `heard_error_us` over the audio written 20–30 s after the start.

- [ ] **Step 1: Failing tests** (`test_air_closed_loop.py`; values from the spec's "Test strategy"):
  - `test_calibrated_cold_starts[800, 200]` — host latency (`SimulatedPort(buffer_ms=…)`) 800 and 200 ms, `noise_us=4000`, `h0` ∈ {9, 16, 23, 30, 38, 45, 52, 60} ms, `L_true = L_as = 228.7`, `D_out = 184`: per start `|median heard error| ≤ 1.0 ms`, `sd` over the 8 starts `≤ 0.5 ms`, `air_corrections == 1` per start, the seed jump logged within 10 s of the first audio.
  - `test_measure_mode_keeps_the_spread_as_a_control` — the same starts in `measure`: `sd ≥ 15 ms` (the variance stage B removes; proves the harness is not trivially passing).
  - `test_learned_target_from_the_third_start` — uncalibrated, the parent's role played by the test (after each start append the engine's `air_learned.x_ms` and `feed.set_target(latency_ms=None, learned_ms=learned_latency_ms(samples), learned_starts=n)` before the next start), `h0` = stage A's sequence (−45.2, −32.4, −31.4, −44.5, −44.9, −36.3, −44.7, −36.4 ms, shifted so the raw placement is late): starts 1–2 have `air_corrections == 0`; starts 3–8 one each; each corrected start lands within 1 ms of `D_out − X_ref(at that start)`; print the start-to-start sd of starts 4–8 and assert it is `≤` the bound the maintainer sets with open question 2 (median: 5.1 ms; robust mean: 1.5 ms).
  - `test_pipewire_sawtooth[(30, 5), (27, 15)]` — `port.sawtooth(amplitude_ms, period_s)` from the start, `noise_us=1000`: `|median heard error| ≤ 2 ms` after the seed, no `steady` jump in 60 s.
  - `test_delay_change_is_absorbed_in_active` — after the seed, `eng.resync(−30_000)` and `SimAir.set_d_out(214_000)`: `air_bias_ms` +30.0 ± 0.1, heard error unchanged within 1 ms, exactly one more jump (`delay`).
  - `test_lock_lost_holds_the_bias` — `SimAir.stop()` for 60 s: `air_bias_state == "held"`, `air_bias_ms` constant, no jump; `resume()`: `active` again, no jump.
  - `test_format_change_reseeds_quietly` — `eng.set_format(InputFormat(44100))` and audio in it: `air_bias_state` returns to `active`, at most one `air_seed` and its size `≤ 2 ms`.
  - `test_underrun_with_a_hidden_change_reseeds` — `port.starve(800)` then `SimAir.h0_us += 12_000`: heard error back within 1 ms within 15 s, no "error baseline" line.
  - `test_calibration_set_mid_stream` — start uncalibrated with a latched learned value, then `feed.set_target(latency_ms=…)` 20 ms above it: one `air_target` jump of `+20 ± 0.5 ms`.
  - `test_hidden_step_is_absorbed_then_corrected` — `SimAir.h0_us += 25_000` mid-stream: after ≥ 15 windows `air_bias_ms` moves by −25 ± 0.5, then R14 corrects `c` (one steady jump of ≈ 25 ms within the next 10 s).
- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_air_closed_loop.py -q -p no:cacheprovider -n0` → FAIL first on the harness (`true_port_time_us`, `SimAir`), then PASS once the harness exists and B5 is in; any failure after the harness is a defect in B4/B5 (fix the implementation, not the test). Keep runtime under 60 s (`step_ms=1.0` is the default; use `step_ms=2.0` where the assertions allow).
- [ ] **Step 3: Commit** `test(air): closed-loop simulation of the air-corrected loop with the stand's numbers`.

### Task B7: IPC and daemon wiring

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/commands.py` (`SetAirCalibration(latency_ms, learned_ms=None, learned_starts=None)`, decoder and encoder: learned fields optional, encoded only when not None), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`_build_air_sensor`: `AirFeed` when `playout_mode == "loop"`, spawn params `learned_ms`/`learned_starts`, `AirSensor(on_window=self._air_feed_push)`, a calibration tee so a `SetAirCalibration` reaches the sensor and `feed.set_target`; `air_step` sets `feed.set_lock(*sensor.lock_state())` and `feed.set_key(*sensor.key())` after each step; `_new_playout_player` passes `air_feed`), `src/sendspin_bridge/services/ipc/daemon_process.py` only if the dispatch needs the new fields (it should not)
- Test: `tests/unit/services/ipc/test_air_commands.py` (extend), `tests/unit/services/ipc/test_bridge_daemon_air.py` (extend)

- [ ] **Step 1: Failing tests:** decode `{"cmd": "set_air_calibration", "v": 1, "latency_ms": null, "learned_ms": 226.0, "learned_starts": 3}` → `SetAirCalibration(None, 226.0, 3)`; `learned_ms` 2500 → clamped 2000; `learned_starts` 1 or `"3"` or `True` → `InvalidCommand`; `learned_ms` without `learned_starts` → `InvalidCommand`; the encoder without learned values produces exactly today's payload (existing tests stay byte-identical). Daemon: `make_daemon(playout_mode="loop", air_sensor={"enabled": True, "latency_ms": None, "learned_ms": 226.0, "learned_starts": 2})` → `d._air_feed.take().target.source == "learned"`; `measure` → `d._air_feed is None`; an accepted window reaches the feed as an `AirSample` with the window's `x_lo_ns`, `x_hi_ns`, `gen`, `h_us`, `d_out_us`, confidence; a dispatched `set_air_calibration` with `latency_ms` 228.7 sets the feed's target to calibrated and the sensor's calibration; after `air_step` the feed's lock and key equal the sensor's; `_make_player` passes the daemon's feed (the same object for every player); the feed survives a player retarget.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/ipc -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(air): the daemon feeds the playout engine from the air sensor in loop mode`.

### Task B8a: Config — learned entries

**Files:**
- Modify: `src/sendspin_bridge/config/players.py` (`AirLearnedEntry`, `AIR_LEARNED_MAX_ENTRIES = 8`, `AIR_LEARNED_MAX_SAMPLES = 8`, `Player.air_learned_latency`, helpers), `src/sendspin_bridge/config/__init__.py` (re-exports, `save_air_learned_sample(player_id, key, x_ms)` writing through the locked config update like `save_device_volume`, config/__init__.py:424), `src/sendspin_bridge/config/migration.py` (`_clean_air_learned`, called where `_clean_air_latency` is), `src/sendspin_bridge/config/schema.json` (player definition), `scripts/translate_ha_config.py` (preserved field `air_learned_latency` next to `air_to_sound_latency`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (hot-apply set next to `air_to_sound_latency`)
- Test: `tests/unit/config/test_air_learned_config.py` (new), `tests/integration/test_translate_ha_config.py` (one new test)

**Interfaces:**

```python
# config/players.py
def learned_latency_ms(samples: Sequence[float]) -> float:
    """Q7: the median of the samples (open question 2 may change the estimator before this task)."""

class AirLearnedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    codec: Literal["sbc"]
    rate: Literal[16000, 32000, 44100, 48000]
    adapter: str = Field(pattern=r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
    source: Literal["learned"] = "learned"
    samples_ms: list[Annotated[float, Field(ge=0, le=2000)]] = Field(min_length=1, max_length=8)
    latency_ms: float = Field(ge=0, le=2000)        # validated: |latency_ms − learned_latency_ms(samples_ms)| ≤ 0.05
    starts: int = Field(ge=1)                       # validated: ≥ len(samples_ms)
    measured_at: str

def valid_air_learned_entries(raw: object) -> list[dict[str, Any]]           # invalid dropped one by one, one per key, ≤ 8 newest
def resolve_air_learned(player_cfg, codec, rate, adapter) -> tuple[float, int] | None   # None below 2 samples
def record_air_learned_sample(entries, key: tuple[str, int, str], x_ms: float, now_iso: str) -> list[dict]
    # appends (oldest dropped beyond 8), recomputes latency_ms, starts += 1, measured_at = now_iso
def forget_air_learned(entries, codec=None, rate=None, adapter=None) -> tuple[list[dict], int]
```

- [ ] **Step 1: Failing tests:** the model accepts the spec's example and rejects a string number, a ninth sample, a `latency_ms` 1 ms off its median, `starts` below the sample count, an unknown key; `record_air_learned_sample` on `[]` gives one sample, `starts == 1`; nine records keep the newest 8, `starts == 9`, `latency_ms` = median of the 8; `resolve_air_learned` is None with one sample and `(median, 2)` with two; normalisation of codec case and adapter MAC like `resolve_air_latency`; `forget_air_learned` one key / all; `valid_air_learned_entries` drops a bad entry, keeps the others; migration: a config with one valid and one invalid learned entry loads with one entry and a WARNING, never drops the player; schema: the player definition declares `air_learned_latency` (array, maxItems 8); config diff: changing `air_learned_latency` is hot-apply (the class of `air_to_sound_latency`); `save_air_learned_sample` on a temp config appends to the right player and key; HA translate keeps `air_learned_latency` across an add-on restart (same pattern as the `air_to_sound_latency` test).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/config tests/integration/test_translate_ha_config.py -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(config): learned air latency per speaker, codec, rate and adapter`.

### Task B8b: Parent — learned target, sample persistence, Forget clears both

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (`air_learned_latency` attribute loaded with `air_to_sound_latency` at spawn and on hot apply; spawn param `air_sensor` gains `learned_ms`/`learned_starts` only when `resolve_air_learned` returns a value; `_send_air_calibration_once` compares and sends the triple `(latency_ms, learned_ms, learned_starts)`; on every status update whose `playout.air_learned` has a `(daemon process, seq)` not seen, validate it (key complete, `x_ms` 0…2000), schedule `save_air_learned_sample` off the loop like `_schedule_persist` does (generalise its value argument), update the in-memory entries and resend), `src/sendspin_bridge/application/devices.py` (`forget_air_calibrations` also forgets the learned entry or all of them; 404 only when neither had the key)
- Test: `tests/unit/bridge/test_air_learned_parent.py` (new), `tests/unit/api/test_air_sensor_api.py` (extend: Forget)

- [ ] **Step 1: Failing tests:** a client with one learned sample spawns with `air_sensor == {"enabled": True, "latency_ms": None}` (unchanged dict); with two, the dict adds `"learned_ms": <median>, "learned_starts": 2`; a status with `playout.air_learned = {"seq": 1, "x_ms": 228.1, "codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6"}` persists once (a second identical status does not write again; a new daemon process with seq 1 writes again), then a `set_air_calibration` with the new learned value is sent to a daemon that advertises `accepts`; an invalid sample (rate missing, `x_ms` 3000) is ignored with one DEBUG line; Forget of one key removes the calibration and the learned entry and resends `latency_ms: null` without learned fields; Forget of a key that only has a learned entry → 204; neither → 404 `unknown_calibration`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/bridge tests/unit/api tests/unit/services/ipc -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(air): the bridge learns each speaker's air latency from its starts`.

### Task B9: API and OpenAPI

**Files:**
- Modify: `src/sendspin_bridge/application/models/devices.py` (`PlayoutTiming`: `air_bias_state`, `air_bias_ms`, `air_target`, `air_target_ms`, `air_learned_starts`, `air_corrections: int = 0`, `air_last_correction_ms`, `air_bias_gated: int = 0`, `air_bias_reason`, with descriptions; the class docstring says `error_ms` is the corrected error in `loop` with a bias; `AirLearnedEntryOut`; the device's audio model gains `air_learned_latency`), `src/sendspin_bridge/application/status.py` (pass `valid_air_learned_entries(player.get("air_learned_latency"))`), `ui/openapi.json` and `ui/src/api/*` (regenerated)
- Test: `tests/unit/api/test_air_correction_api.py` (new)

- [ ] **Step 1: Failing tests:** `PlayoutTiming.model_validate` keeps the new keys and ignores `air_learned`; the device JSON carries `timing.playout.air_bias_state` and `audio.air_learned_latency`; the OpenAPI schema lists the new fields as required in the output (the repo's `json_schema_serialization_defaults_required`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**, then `uv run python scripts/export_openapi.py` and `cd ui && npm run gen:api`. **Step 4: Run** `uv run pytest tests/unit/api -q -p no:cacheprovider` and `cd ui && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(api): air correction state and learned latencies in the device model`.

### Task B10: UI — the timing panel

**Files:**
- Modify: `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/i18n/{en,ru}.json`
- Test: `ui/src/components/devices/DeviceTimingPanel.spec.ts` (extend or create next to the component, as the existing panel tests do)

- [ ] **Step 1: Failing tests:** `loop` + `air_target: "calibrated"`, `air_target_ms: 228.7` → "Air correction: calibrated (228.7 ms)"; `learned`, 6 starts → "learned (6 starts)"; `air_bias_state: "none"`, reason `no_target`, one learned sample stored → "no data (learning: 1 of 2 starts)"; reason `not_locked` → "inactive: not locked"; bias +12.3 → "Air bias +12.3 ms"; `air_corrections: 1` → "Air corrections 1"; `active` → the delay note; `measure` → no correction line; a learned entry renders "Learned: 228.1 ms · SBC · hci1 · 6 starts" with Forget calling the existing forget API; ru texts exist for every new key (the i18n key test).
- [ ] **Step 2: Run** `cd ui && npm test` → FAIL. **Step 3: Implement.** **Step 4: Run** `npm test && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(ui): air correction line and learned latencies in the timing panel`.

### Task B11: Guidance — `air_correction_inactive`

**Files:**
- Modify: `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (entry: layer `audio`, severity `info`, reason codes `air_switch_off`, `air_not_effective`, `air_unavailable`, `air_not_locked`, priority next to `air_sensor_unavailable`), `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (`_build_air_correction_issues`, called next to `_build_air_sensor_issues`), `ui/src/i18n/{en,ru}.json` (issue texts)
- Test: `tests/unit/services/diagnostics/test_air_correction_guidance.py` (new)

- [ ] **Step 1: Failing tests** (device snapshots as in `test_air_guidance.py`): `playout.mode == "loop"` and `air_sensor is None` → reason `air_switch_off`; `air_sensor.state == "off"` → `air_not_effective`; `unavailable`/`unsupported_codec`/`unsupported_input` → `air_unavailable`; `warm_s = 61`, `air_bias_state = "none"`, `air_bias_reason = "not_locked"` → `air_not_locked`; the same with `warm_s = 59` → no issue; `air_bias_state = "none"` with reason `no_target` → no issue; `measure` → no issue; `active` → no issue; the registry entry exists with i18n texts in en and ru.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/diagnostics -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(diagnostics): tell when a loop-mode speaker runs without its air correction`.

### Task B12: Stand acceptance (manual, done by the controller)

Not for an implementer subagent: the controller runs it on the HAOS stand with the branch overlay, as in stage A's A17.

**Interfaces:**
- Consumes: HAOS VM (PulseAudio 17), ENEBY20 on hci1, `PLAYOUT_SYNC=loop`, `EXPERIMENTAL_AIR_SENSOR=true`, `PREFER_SBC_CODEC=true`, `SENDSPIN_AIR_DEBUG=1`; the desktop bridge's headphone output on the playout engine in `measure` as the warm reference; headset microphone; the coherent click estimator; MA (test environment, any player action allowed; never reload the MA sendspin provider).
- Produces: the "Stage B1 validation note" in the spec.

- [ ] **Step 1: Restart method.** Find how to restart only ENEBY20's stream while the reference keeps playing: try MA `players/cmd/power` off/on of the member, then un-sync/re-sync (`players/cmd/ungroup`, `players/cmd/group` to the leader), then member `stop`/`play`; accept the first one after which ENEBY20's daemon logs a new `playout output open` and the reference's does not.
- [ ] **Step 2: Series 1, calibrated** (ENEBY20 calibrated against the playout reference, A17: 228.7 ms; re-calibrate if the reference changed): 8 restarts; per restart wait 20 s, then ≥ 20 s of clicks with alternating solos; `M_i` = ENEBY20 − reference. Pass: `sd(M) ≤ 2 ms`, `|median M| ≤ 3 ms`; one `air_seed` per start, within 10 s. Record `b`, seed time, `air_bias_gated`, reseeds.
- [ ] **Step 3: Series 2, uncalibrated:** Forget (calibration and learned), 8 restarts. Pass: `sd(M_4 … M_8) ≤ 2 ms`. Record `X_i` and the learned value per start.
- [ ] **Step 4: Q6 check** in `measure`: a delay change of `+30 ms` moves ENEBY20 by `−30 ± 1 ms` (microphone and the `playout jump: delay` size).
- [ ] **Step 5: Absorption check** after series 1: `+30 ms` delay in `loop` → `|ΔM| ≤ 1.5 ms` and the absorbed log line.
- [ ] **Step 6: CPU:** `pidstat` of ENEBY20's daemon over 60 s of music, `loop` vs `measure` (switch on in both): ≤ +0.3 % of one core.
- [ ] **Step 7: Guidance:** switch off with `loop` → `air_correction_inactive` (`air_switch_off`); locked `loop` → none.
- [ ] **Step 8:** Hand the numbers to B13.

### Task B13: Validation note, docs and changelog

Starts only after the concurrent stage A docs work (stage A spec, `docs-site/`, `CHANGELOG.md`) is committed; rebase first.

**Files:**
- Modify: `docs/plans/2026-10-28-air-sensor-stage-b-design.md` (append "Stage B1 validation note"; update the constants/rulings rows the stand changed), `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md` (air correction in `loop`: what it does, calibrated vs learned, the delay note, Forget), `docs-site/src/content/docs/{,ru/}configuration.md` (the switch now also in `loop`), `CHANGELOG.md`

- [ ] **Step 1: Validation note** in the stage A note's format: restart method; series 1 `M` per start, `sd`, `median`; series 2 `M`, `sd(M_4…M_8)`, `X_i`; Q6 check; absorption check; CPU; guidance; any constant changed and why.
- [ ] **Step 2: Docs** (en, ru) as listed.
- [ ] **Step 3: Changelog** `[Unreleased]`: `### Added` — "Playout sync `loop` mode now corrects Bluetooth speakers from the experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`): each start is placed where the speaker's audio really leaves over the air, on the schedule when the speaker's latency is calibrated in the group sync wizard, otherwise where it played on average in its previous starts. The timing panel shows the correction; Forget also clears the learned latency." `### Fixed` — "In playout sync `measure` mode a delay change now moves the speaker by exactly the change; before, it also removed a small leftover offset (up to the 20 ms dead band), and a gap after an underrun could move the audio by the offset the engine had just reported." Lint: `uv run python scripts/lint_changelog.py CHANGELOG.md`.
- [ ] **Step 4: Commit** only these files: `docs(air): stage B1 validation note and user docs`.
