# Air sensor stage B1 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Revision 3 (2026-10-29): the pre-flight review applied (C1, I1–I10, the minor fixes, the task splits B5a/B5b, B6a/B6b, B8b-1/B8b-2). Revision 2 applied the round-2 rulings (delay = trim with `D_ref`, robust learned mean, no new key, resume after `held`, learning in `loop` only).

**Goal:** In `PLAYOUT_SYNC=loop` with `EXPERIMENTAL_AIR_SENSOR` on, the playout engine learns per output the bias between its pulsesink clock and the air sensor and places audio so that the air-derived error is on its target (calibrated: on the schedule at the calibration's delay; uncalibrated: at the learned placement). A cold start converges in one correction. A delay change moves the sound by exactly the change in every mode. First, `measure` (and `loop` without air data) stops snapping the residual at every placement, and a delay change becomes a frame-exact jump. The rate loop is B2, not here.

**Architecture:** The stage A `AirSensor` (daemon loop) hands every accepted window, now carrying the written-stream-time span `x` it matched, through a lock-protected `AirFeed` to the engine on the feeder thread, together with the lock state, the target (calibrated or learned, each with its reference delay `D_ref`), the link key and the output delay. The engine keeps a 30 s history of its pulsesink readings keyed by `x`; a pure `AirBias` state machine (none / seeding / active / held) turns windows into the bias `b = ê − (H + L_eff − D_ref)`, seeds it from an agreeing triple with one full correction (`air_seed`), tracks it with τ = 30 s behind a gate, and reacts to engine events. The controller acts on `c = e_w − b − β` (`β = 0` with a bias except in `held`); every placement aligns to `audible − o`; a delay change is a jump of `ΔD` at the write head. One learned sample per output goes to the parent, which keeps the last 8 per (codec, rate, adapter) in a sibling per-player field.

**Tech Stack:** Python 3.13/3.14 stdlib (`threading`, `statistics`, `math`, `collections.deque`), the existing playout engine and simulation harness, FastAPI + Pydantic v2, pytest (+ xdist), Vue 3 + TS + vitest.

**Spec:** [`docs/plans/2026-10-28-air-sensor-stage-b-design.md`](2026-10-28-air-sensor-stage-b-design.md) rev. 3. Every task's requirements include the spec; decisions are cited as Qn, controller rulings as round 2 (n) and C1/In, design rulings as S-n.

## Global Constraints

Copied from the spec; every task's requirements include them.

* **`measure` stays observe-only (Q2).** No `AirFeed`, no `AirBias` in `measure`; the engine snapshot has `air_bias_state: null` there. With `EXPERIMENTAL_AIR_SENSOR=false` nothing of stage B exists. Every task that adds a hook has a `measure` test and a switch-off test.
* **Residual domains (S-24, spec "Residual domains"):** without a bias value the controller input is `e_rel = e_w − β`, a placement uses `o = r = EMA + β` (`0` before the first reading), `placed(t, residual = o − β)`, published `error_ms = EMA + β`; with a bias value the input is `c = e_w − b − β`, `o = b + β`, `placed(t, residual = 0)`, published `error_ms = EMA + β` (raw = `error_ms + air_bias_ms`). Order in `_record_placement`: `o` from the render → `β` reset only for `underrun`/`stall` without a bias → `placed(t, o − β − (b or 0))` → EMA restart.
* **Baseline (C1, S-6):** with a bias value `β = 0` and no baseline collection, except in `held` (today's rules on `c`). At the seed `b₀ = median(ê_i − E_i)` (folds the current `β`), then `β := 0`; on resume from `held` `b += β`, `β := 0`; before the first seed today's rules apply.
* **Delay changes (I1, S-23):** `resync(shift ≠ 0)` shifts the queued play times without a placement and, if no placement is pending and program audio is queued, executes a jump of `ΔD = −shift` (positive drops) with reason `delay`, not clamped, at the write head; `resync(0)` stays a `delay` placement. Every mode.
* **Target (S-4, S-14, S-22):** `E_equiv = H + L_eff − D_ref`; `L_eff`/`D_ref` = the calibration for the current key (`latency_ms`, `d_ref_ms`), else the learned value (≥ 2 samples, accepted until this output published its own sample, I3), else none; a missing `D_ref` latches `D_out,latched`. `D_out` never enters `b`; `air_trim_ms = D_out,latched + ΣΔD − D_ref`.
* **Constants (spec "Constants"):** `J = 10 ms` in `loop`; `SEED_WINDOWS = 3`; `SEED_AGREE_US = 2000`; `SEED_MAX_WINDOWS = 12`; `DEAD_BAND_US = 2000`; `MIN_CONFIDENCE = 0.5`; `BIAS_TAU_S = 30`; `DT_MAX_S = 5`; `GATE_MIN_US = 10000`, `GATE_MAD_K = 4`, `GATE_HISTORY = 20` accepted `b_i` (seeded with the seed's, I6); `STEP_WINDOWS = 15`, `STEP_AGREE_US = 3000`; `RESEED_TIMEOUT_S = 15`; `E_HISTORY_S = 30`; `E_MIN_COVERAGE = 0.5`; `FEED_MAX = 32`; `LEARN_MIN_SAMPLES = 2`; `LEARN_KEEP = 8`; `LEARN_BAND_MS = 15`; learned offset `|X − d| ≤ 1000 ms`, `d` 0…5000 ms.
* **Air jumps** (`air_seed`, `air_target`) bypass the 50 ms clamp and the 10 s interval (Q8), are executed like steady jumps (positive = drop) and are dropped while a placement is pending (S-19).
* **Event exclusion (S-3):** a window whose `x` span contains an engine event's `x` (placement, delay/steady/air jump: the write head at the event) or starts before the last change of `b`'s definition (target change, reseed) is never used for `b`; the seed triple lies wholly after both. EHistory readings are usable when no stall/underrun is in progress and `s(x_port)` exists (S-2).
* **Threading:** `AirBias`, `EHistory` and the engine are feeder-only; `AirFeed` is the only shared object (one `threading.Lock`, O(1) critical sections) (S-1).
* **Containment (I8, S-20):** one engine helper wraps every `AirBias`/`EHistory` call; an exception sets `air_bias_state: none`, `air_bias_reason: internal_error` for the output, re-bases controller and EMA to the raw domain (audio kept), logs a WARNING with traceback at most once per 10 min (`RateLimitedLog`), never reaches the feeder.
* **IPC (I4, S-17):** `set_air_calibration` and the spawn param `air_sensor` gain optional `d_ref_ms` (0…5000), `learned_ms`, `learned_d_ref_ms` (0…5000), `learned_starts` (int ≥ 2), the learned three together with `|learned_ms − learned_d_ref_ms| ≤ 1000`, each sent only when known; `daemon_process._air_sensor_args` passes them; payload `v: 1`, no protocol bump.
* **Learned entries (S-13, S-21):** per-player sibling field `air_learned_latency`, ≤ 8 entries (one per key), pairs `samples_ms`/`d_out_ms` (1–8), `(latency_ms, d_ref_ms) = learned_reference(samples_ms, d_out_ms)` from the pure module `services/audio/playout/air_learned.py` (I5); calibration entries gain `d_ref_ms`; Forget clears both kinds for the key (Q7). Learning in `loop` only.
* **Existing tests:** never edit one to make it pass (`CLAUDE.md`). Where a stage B decision changes a requirement a stage A test pinned (named in the task), the task changes that test and the commit message names the decision.
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (R5–R9). TDD: red before green.
* No live audio, Bluetooth, MA or bridge actions in any task except B12 (the controller's stand run). Do not touch the bridge on port 8080 or Music Assistant on 8095/8097 while developing.
* **Concurrent work on this branch:** another agent edits `docs/plans/2026-10-28-air-sensor-design.md`, `docs-site/`, `scripts/air_envelope_corr.py`, `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py` and `CHANGELOG.md`. No task before B13 touches them; B13 starts only after that work is committed (rebase first). Commit only the files a task names (`git add <paths>`).

## Review Focus

1. **Signs and units.** `b = ê − E_equiv`, `E_equiv = H + L_eff − D_ref`, `ΔD = −shift_us` (jump `+ΔD` drops, `b` unchanged), jumps positive = drop, `o` subtracted from `audible`, `b −= ΔL` on a target change. A sign slip moves audio by twice the bias without failing a shape test; the closed-loop simulation checks the true heard error. (B0, B4a, B4b, B5a, B5b, B6a)
2. **Q6/I1 do not break R14.** `β` kept across placements, the superseded-jump case, the start placement, `resync(0)`, the baseline tests of `test_steady_jumps.py` green. (B0)
3. **Residual domains and C1.** Placement residuals, EMA restarts, seed and resume folding `β`, the guard's re-base. (B5a, B6a)
4. **Thread boundary.** Only `AirFeed` crosses threads; reopen and tap generations keep an old output's window out of a new engine. (B3, B5b, B7)
5. **`measure` and switch off unchanged.** (B1, B5a, B7)
6. **Learned target provenance.** One sample per output from the initial triple, keyed, with its delay, persisted once per `(proc, seq)`, active from the 3rd start, accepted until the own sample, calibration wins, Forget clears both. (B4a, B4b, B8a, B8b-1)
7. **The slider keeps its meaning.** A delay change moves the sound by `ΔD` in `measure` and `loop`, now and at later starts. (B0, B5b, B6a, B8a, B8b-2)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/playout/scheduler.py` | `shift_queue` (shift without a placement) (B0) |
| `src/sendspin_bridge/services/audio/playout/engine.py` | placement offset, `β` kept, delay jump (B0); bias domains, guard (B5a); air jumps, events, snapshot (B5b) |
| `src/sendspin_bridge/services/audio/playout/air_bias.py` | **new** `AirSample`, `AirTarget`, `FeedView`, `AirFeed`, `EHistory` (B3); `AirAction`, `LearnedSample`, `AirBias` (B4a, B4b) |
| `src/sendspin_bridge/services/audio/playout/air_learned.py` | **new** pure `learned_reference` (B4a) |
| `src/sendspin_bridge/services/audio/playout/air_tap.py` | `x` per cell, `WrittenSlice.x` (B2) |
| `src/sendspin_bridge/services/audio/playout/air_sensor.py` | `AirWindow.x_lo_ns/x_hi_ns/gen`, `on_window`, `lock_state()`, `key()` (B2); fold `d_out_ms`, `air_delay_changed` (B8b-2) |
| `src/sendspin_bridge/services/audio/playout/gst_output.py` | `air_feed` passed to the engine, `feed.reopen` (B5b) |
| `src/sendspin_bridge/services/ipc/commands.py` | `SetAirCalibration` new fields (B7) |
| `src/sendspin_bridge/services/ipc/daemon_process.py` | `_air_sensor_args` passes the new keys (B7) |
| `src/sendspin_bridge/services/ipc/bridge_daemon.py` | sensor built in `loop` (B1); `AirFeed` wiring, calibration tee, output delay (B7) |
| `src/sendspin_bridge/config/__init__.py` | effective rule (B1); `save_air_learned_sample`, re-exports (B8a) |
| `src/sendspin_bridge/config/players.py`, `config/migration.py`, `config/schema.json` | `AirLatencyEntry.d_ref_ms`, `AirLearnedEntry`, helpers, migration, schema (B8a) |
| `scripts/translate_ha_config.py`, `src/sendspin_bridge/services/infrastructure/config_diff.py` | preserve / hot-apply `air_learned_latency` (B8a) |
| `src/sendspin_bridge/bridge/client.py` | learned target resolve/send, sample persistence (B8b-1); fold `d_out_ms` pass-through (B8b-2) |
| `src/sendspin_bridge/application/devices.py` | Forget clears both (B8b-1) |
| `src/sendspin_bridge/application/sync_tuning.py`, `application/models/sync.py` | calibration `d_ref_ms`, `air_delay_changed` (B8b-2) |
| `src/sendspin_bridge/application/status.py`, `application/models/devices.py` | learned entries, `PlayoutTiming` keys, `AirLatencyEntryOut.d_ref_ms` (B9) |
| `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py`, `recovery_assistant.py` | `air_correction_inactive` (B11) |
| `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/i18n/{en,ru}.json`, `ui/src/api/*`, `ui/openapi.json` | panel line, learned entries, texts (B1, B8b-2, B10, B11) |
| `tests/unit/services/audio/playout/sim.py` | `true_port_time_us`, `on_block` hook, `SimAir` (B6a) |
| `docs/plans/2026-10-28-air-sensor-stage-b-design.md`, `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}configuration.md`, `CHANGELOG.md` | validation note, user docs, changelog (B13) |

Conventions as in the stage A plan: tests under `tests/unit/...`, one file per task named in the task; run one file with `uv run pytest <path> -q -p no:cacheprovider -n0`; fixtures a task's tests need and that do not exist are defined in that test file.

Order and dependencies: B0 · B1 · B2 → B3 → B4a → B4b → B5a (needs B0, B4b) → B5b → B6a → B6b → B7 (needs B1, B2, B5b) → B8a (needs B4a) → B8b-1 (needs B7, B8a) → B8b-2 (needs B8b-1) → B9 (needs B5b, B8a) → B10 → B11 → B12 → B13. B0, B1 and B2 are independent of each other. B6a/B6b may fix defects in `air_bias.py` or `engine.py` they expose (with a test that names the defect), or route them back to B4/B5's implementer.

## Pre-flight conflict table

Tasks that share a file or an interface; run them in the order given, never in parallel.

| File / interface | Tasks | Note |
|---|---|---|
| `engine.py` (`next_block`, `_record_placement`, `_record_event`, `_sense`, `resync`) | B0 → B5a → B5b (→ B6a/B6b fixes) | B5a builds on B0's `_placement_offset_us()` and residual order; B5b adds jumps and events |
| `scheduler.py` (`shift_queue`) | B0 | B5b only calls it |
| `tests/unit/services/audio/playout/test_engine.py`, `test_steady_jumps.py` | B0 (must stay green), B5a, B5b | B0 may only change an assertion that encodes the implicit snap (Q6), with the reason in the commit |
| `air_sensor.py` (`AirWindow`, `_accept`, `_placement`, `fold`) | B2 → B7 → B8b-2 | B7 wires B2's hook; B8b-2 changes the fold answer |
| `air_tap.py` (`WrittenSlice`) | B2 | `x` added last with a default, so positional constructions keep working |
| `air_bias.py` | B3 → B4a → B4b → B5a/B5b (→ B6 fixes) | one module, grown task by task |
| `air_learned.py` | B4a → B8a, B6a | config and the simulation import it |
| `bridge_daemon.py` (`_build_air_sensor`, `_apply_output_delay`) | B1 → B7 | B1 removes the gate, B7 adds the feed and the output delay |
| `daemon_process.py` (`_air_sensor_args`) | B7 | |
| `config/__init__.py` (`air_sensor_effective`) | B1 → B8a | different functions |
| `bridge/client.py` (spawn params, `_send_air_calibration_once`, status handling, fold result) | B8b-1 → B8b-2 | |
| `SetAirCalibration` / spawn param keys | B7 → B8b-1 | B8b-1 sends what B7 decodes |
| fold answer `d_out_ms` / `air_delay_changed` | B8b-2 only (daemon, parent, sync tuning, i18n) | I10 |
| `PlayoutTiming` / snapshot keys | B5b → B9 → B10, B11 | key names fixed in B4a/B5b (spec "Playout snapshot") |
| `ui/src/i18n/{en,ru}.json` | B1, B8b-2, B10, B11 | different keys |
| Stage A tests pinning `loop_not_supported_yet` | B1 | requirement changed by Q2 (listed in B1) |
| `CHANGELOG.md`, `docs-site/`, the stage A spec | B13 only | edited concurrently by another agent until its work is committed |

---

## Stage B1 — the engine corrects from the air

### Task B0: Placements keep the residual; a delay change is a frame-exact jump (Q6, I1)

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/scheduler.py` (`shift_queue`), `src/sendspin_bridge/services/audio/playout/engine.py` (`next_block`, `_record_placement`, `_record_event`, `resync`, new `_placement_offset_us`)
- Test: `tests/unit/services/audio/playout/test_engine_residual.py` (new)

**Interfaces:**

```python
# scheduler.py
def shift_queue(self, shift_us: int) -> bool:
    """Move every queued chunk and _expect_us by shift_us without requesting a placement; True if program is queued."""

# engine.py
def resync(self, shift_us: int) -> bool:
    """shift_us != 0: shift_queue(shift_us); unless a placement is pending, a jump of -shift_us (reason "delay",
    not clamped) at the write head when program is queued; it replaces a decided steady jump not yet rendered.
    shift_us == 0: a "delay" placement as today (an engine-requested placement). Returns whether audio was queued."""

def _placement_offset_us(self) -> float:
    """o: the raw residual (_error_ema_us, baseline included), 0 without one. B5a extends it with the bias."""

def _record_event(self, size_us: float, reason: str, residual_us: float = 0.0) -> None:
    """As today, but the published EMA restarts at residual_us."""
```

`_record_placement` order (spec "Residual domains"): (1) `o` = the value the render used; (2) `if reason in _BASELINE_REASONS: β := 0` and a fresh collection starts (as today; a placement while one is collected restarts it), else `β` and `_baseline_degraded` are kept; (3) `controller.placed(t, residual_us = o − β, quiet_wait=True)` with the `β` after step 2; (4) `_record_event(size_us, reason, residual_us = o)`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_engine_residual.py
"""Q6 and I1: placements keep the residual; a delay change is a frame-exact jump of its delta."""

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
@pytest.mark.parametrize("delta_ms", [30.0, -30.0])
def test_delay_change_moves_the_audio_by_exactly_its_delta(caplog, mode, residual_ms, delta_ms):
    eng, port, raw = engine(mode)
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 5.0)
    port.skip(-residual_ms)  # the port falls behind: heard late by the residual, inside the dead band
    drive(eng, port, raw, 8.0)
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        assert eng.resync(int(-delta_ms * 1000)) is True  # output delay +ΔD: the queued audio plays ΔD earlier
        drive(eng, port, raw, 2.0)
    assert ("delay", pytest.approx(delta_ms, abs=0.05)) in jumps(caplog)  # a jump of ΔD, not a placement
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)  # kept, not zeroed
    drive(eng, port, raw, 23.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    assert heard - (due - delta_ms * 1000) == pytest.approx(residual_ms * 1000, abs=500)  # today: ≈ 0


def test_the_delay_jump_does_not_read_the_noisy_port():
    eng, port, raw = engine("measure", noise_us=4000.0)  # σ 4 ms per reading, PulseAudio 17
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 12.0)
    eng.resync(-30_000)  # the true residual is 0; a placement would land on the 9-reading median (±1.3 ms)
    drive(eng, port, raw, 25.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due - 30_000, abs=200)


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


def test_resync_zero_is_still_a_placement():
    eng, port, raw = engine("measure")
    zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 5.0)
    eng.resync(0)
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["last_jump_reason"] == "delay"
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_engine_residual.py -q -p no:cacheprovider -n0` → FAIL (the delay is a placement that zeroes the residual; the gap moves the audio 30 ms; the clear zeroes the baseline).
- [ ] **Step 3: Implement.** Scheduler `shift_queue` (the loop of `shift` without `request_placement`; `shift` keeps its contract for `resync(0)`). Engine: `resync` per the interface (the pending jump goes through `_pending_jump_us` with a new `_pending_jump_reason = "delay"`; `next_block` passes it to `render(jump_us=…)` as for steady jumps and logs the reason; the controller's jump insert bookkeeping (`_insert_left`) applies to it); in `next_block` compute `o = self._placement_offset_us()` once and pass `audible − o` wherever `audible` goes (both `reanchor` calls, `render`), with `size_us = (audible − o) − s_old`; `_record_placement` in the order above. Update the module docstring and the comment at engine.py:497-499.
- [ ] **Step 4: Run** the new file, then `uv run pytest tests/unit/services/audio/playout tests/unit/services/ipc/test_bridge_daemon_playout.py -q -p no:cacheprovider` → PASS. `test_jump_is_never_passed_while_a_placement_is_pending` (test_engine.py:159; it uses `resync(0)`), `test_delay_change_places_exactly_and_does_not_degrade` (now a 700 ms insert jump with reason `delay`) and the baseline tests in `test_steady_jumps.py` must stay green unchanged. If an existing test fails because it asserts an error of ≈ 0 right after a placement **that had a residual to keep**, it encodes the bug Q6 removes: change only that assertion and name the test and Q6 in the commit message; any other failure is an implementation bug.
- [ ] **Step 5: Commit** `fix(playout): placements keep the residual and a delay change is a frame-exact jump (Q6)`.

### Task B1: Effective rule — the air sensor in `loop`

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`air_sensor_effective`, the `EXPERIMENTAL_AIR_SENSOR` comment in `DEFAULT_CONFIG`), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`_build_air_sensor`: build in `measure` and `loop`), `src/sendspin_bridge/config/schema.json` (switch description), `ui/src/i18n/{en,ru}.json` (`timing.air.reasons.needs_measure` → "Needs playout sync in measure or loop mode." / "Нужна синхронизация воспроизведения в режиме measure или loop."; remove `loop_not_supported_yet`; the switch's settings help: "… Measures in measure mode; corrects placement in loop mode.")
- Test: `tests/unit/config/test_air_sensor_config.py`, `tests/unit/services/ipc/test_bridge_daemon_air.py`, `tests/unit/api/test_air_sensor_api.py` (requirement change, below)

No `air_correction_effective`: it had no consumer (the daemon decides from the mode, guidance from the device status).

Requirement change (Q2): four stage A tests pinned "no sensor in `loop`". Change exactly these and name Q2 (2026-10-28) in the commit message:
- `tests/unit/config/test_air_sensor_config.py:27`: `(… "PLAYOUT_SYNC": "loop"}, BT, (False, "loop_not_supported_yet"))` → `(True, None)`;
- `tests/unit/services/ipc/test_bridge_daemon_air.py:79` (`test_a_mode_other_than_measure_ignores_the_param`): parametrize `["off"]` only, plus a new test that `loop` builds the sensor (`make_daemon(playout_mode="loop", air_sensor=ON)` → `d.air_snapshot()["state"]` is a sensor state, `d.air_accepts() == [1]`);
- `tests/unit/services/ipc/test_bridge_daemon_air.py:612`: drop the `loop` config from `test_a_sensor_that_is_not_effective_adds_no_spawn_param`, plus a new test that `loop` asks for the sensor at spawn;
- `tests/unit/api/test_air_sensor_api.py:266-268` (`test_switch_on_loop_mode_reports_off_with_its_reason`): replaced by a test that `loop` gets no `off` status from the parent.

- [ ] **Step 1: Failing tests:** the changed ones above.
- [ ] **Step 2: Run** the three files → FAIL.
- [ ] **Step 3: Implement** the rule (the `loop` branch goes; `measure`/`loop` both pass on to the backend check), the daemon gate `if self._args.playout_mode not in ("measure", "loop"): return`, the texts. The parent's spawn code (bridge/client.py:2292-2298) needs no change.
- [ ] **Step 4: Run** `uv run pytest tests/unit/config tests/unit/services/ipc tests/unit/api -q -p no:cacheprovider` and `cd ui && npm test` → PASS.
- [ ] **Step 5: Commit** `feat(air): the air sensor also runs in playout loop mode (observe only until the correction lands)`.

### Task B2: Written stream time per cell and per window

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_tap.py` (an `array('d')` ring of `x` per cell from `block.x0_ns + i·CELL_FRAMES·1e9/rate`; `WrittenSlice.x`), `src/sendspin_bridge/services/audio/playout/air_sensor.py` (`AirWindow.x_lo_ns`, `x_hi_ns`, `gen`; `_placement` returns them; `on_window`; `lock_state()`, `key()`)
- Test: `tests/unit/services/audio/playout/test_air_tap.py`, `tests/unit/services/audio/playout/test_air_sensor.py` (extend)

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
    def lock_state(self) -> tuple[str, float | None]   # (state, confidence) as the snapshot reports them
    def key(self) -> tuple[str | None, int | None, str | None]  # (codec, rate, adapter)
```

- [ ] **Step 1: Failing tests:** tap — after two blocks of 10 ms at 48 kHz starting at `x0 = 5e9`, `slice(0, n).x[k] == 5e9 + k·128e9/48000` (to 1 ns); the ring wraps with `x` aligned to `s`; after `reset()` a new block restarts `x` from its own `x0`; a format change keeps `x` continuous. Sensor — with the stage A fakes, a confirmed window's `x_lo_ns`/`x_hi_ns` equal the tap's `x` of the matched span's first cell and the end of its last (`x_hi − x_lo ≈ 3 s` within one cell), `gen` equals the slice's; `on_window` is called once per accepted window (twice when a candidate is confirmed: candidate first) with `_confidence("locked", now)`; an `on_window` that raises does not change the sensor's state; `lock_state()`/`key()` match `snapshot()`.
- [ ] **Step 2: Run** both files → FAIL. **Step 3: Implement** (`x_lo = sl.x[lo]`, `x_hi = sl.x[hi] + cell_ns`; `_Plan.x`). Memory ≈ 55 KB more per daemon. **Step 4: Run** `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` → PASS.
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
    latency_ms: float | None                       # the chosen source's L_eff
    d_ref_ms: float | None                         # its D_ref (None: latch D_out at the first update)
    source: Literal["calibrated", "learned"] | None
    learned_ms: float | None                       # kept so a forgotten calibration can fall back
    learned_d_ref_ms: float | None
    learned_starts: int | None
    version: int                                   # bumped by every set_target

@dataclass(frozen=True, slots=True)
class FeedView:
    samples: tuple[AirSample, ...]
    lock: tuple[str, float | None]                 # sensor state, confidence; ("no_signal", None) until set
    target: AirTarget
    key: tuple[str | None, int | None, str | None]
    d_out_ms: float | None                         # the output delay the daemon applied last

class AirFeed:
    """The only object shared by the daemon loop (producer) and the feeder thread (consumer)."""
    dropped: int
    def push(self, sample: AirSample) -> None        # refused below the reopen generation; drop-oldest beyond FEED_MAX
    def set_lock(self, state: str, confidence: float | None) -> None
    def set_target(self, *, latency_ms: float | None, d_ref_ms: float | None = None, learned_ms: float | None = None,
                   learned_d_ref_ms: float | None = None, learned_starts: int | None = None) -> None
        # source = "calibrated" if latency_ms is not None, else "learned" if learned_ms is not None and
        # learned_starts >= 2, else None; latency_ms/d_ref_ms of the target are the chosen source's
    def set_key(self, codec: str | None, rate: int | None, adapter: str | None) -> None
    def set_output_delay(self, d_out_ms: float) -> None
    def reopen(self, min_tap_gen: int) -> None       # drops queued samples
    def take(self) -> FeedView                       # drains samples; the other values are the latest
    def next_learned_seq(self) -> int                # 1, 2, … for this daemon (survives engines)

class EHistory:
    """Pulsesink readings keyed by written stream time (feeder-only)."""
    def add(self, t_us: float, x_port_ns: float, e_us: float, *, usable: bool) -> None
        # usable = no stall/underrun in progress and s(x_port) known (S-2); prunes t older than E_HISTORY_S
    def median_over(self, x_lo_ns: float, x_hi_ns: float) -> tuple[float, int] | None
        # (median e of the usable readings with x_lo <= x_port < x_hi, their count);
        # None below E_MIN_COVERAGE · SENSE_HZ · (x_hi − x_lo)/1e9 usable readings
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
    f.set_target(latency_ms=228.7, d_ref_ms=184.0)
    f.set_key("sbc", 44100, ADAPTER)
    f.set_output_delay(184.0)
    v = f.take()
    assert [s.at_us for s in v.samples] == [0.0, 1e6]
    assert v.lock == ("locked", 0.9) and v.key == ("sbc", 44100, ADAPTER) and v.d_out_ms == 184.0
    assert (v.target.latency_ms, v.target.d_ref_ms, v.target.source) == (228.7, 184.0, "calibrated")
    again = f.take()
    assert again.samples == () and again.target.version == v.target.version


def test_target_sources():
    f = AirFeed()
    assert f.take().target.source is None
    f.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=184.0, learned_starts=1)
    assert f.take().target.source is None  # active from the 3rd start: two samples needed
    f.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=190.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms, t.d_ref_ms) == ("learned", 226.0, 190.0)
    f.set_target(latency_ms=228.7, d_ref_ms=184.0, learned_ms=226.0, learned_d_ref_ms=190.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms, t.d_ref_ms, t.learned_ms) == ("calibrated", 228.7, 184.0, 226.0)


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


def test_push_and_take_from_two_threads_smoke():
    """Smoke test only: no lost or reordered sample under contention (it cannot prove the absence of races)."""
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
- [ ] **Step 3: Implement.** `AirFeed`: one `threading.Lock`; samples in a `deque(maxlen=FEED_MAX)` with an explicit `dropped` count; `take` swaps the deque under the lock and returns a tuple. `EHistory`: a `deque` of `(t, x, e, usable)`, pruned on `add`; `median_over` scans (≤ 300 entries). Module docstring: the thread contract (S-1) and the usable/coverage rule (S-2).
- [ ] **Step 4: Run** → PASS (also with `-n 4`).
- [ ] **Step 5: Commit** `feat(air): the feed between the air sensor and the playout engine, and the reading history keyed by x`.

### Task B4a: Learned reference (pure) and `AirBias` — seeding, tracking, gate, learned sample

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_learned.py` (stdlib only)
- Modify: `src/sendspin_bridge/services/audio/playout/air_bias.py`
- Test: `tests/unit/services/audio/playout/test_air_learned.py` (new), `tests/unit/services/audio/playout/test_air_bias.py` (new; part 1)

**Interfaces:**

```python
# air_learned.py
LEARN_BAND_MS = 15.0
LEARN_OFFSET_MAX_MS = 1000.0

def learned_reference(samples_ms: Sequence[float], d_out_ms: Sequence[float]) -> tuple[float, float]:
    """(latency_ms, d_ref_ms) (round 2 (2), S-21): off_i = X_i − d_i; off_ref = mean of the off_i within
    LEARN_BAND_MS of their median; d_ref = median(d_i); latency = d_ref + off_ref. Raises ValueError on
    empty or unequal inputs or |off_i| > LEARN_OFFSET_MAX_MS."""

# air_bias.py
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
    x_ms: float        # median(d_out_i − h_i) of the triple
    d_out_ms: float    # median(d_out_i)
    codec: str
    rate: int
    adapter: str

class AirBias:
    def __init__(self, feed: AirFeed, *, log: logging.Logger = logger) -> None
    def update(self, now_s: float, history: EHistory, *, beta_us: float = 0.0) -> AirAction | None
        # takes the feed's view, applies lock/target/output-delay changes, then every sample in order;
        # beta_us = the engine's current β (folded into b at the seed, C1)
    def on_event(self, x_ns: float) -> None   # a placement or a delay/steady/air jump written at x (S-3)
    @property
    def state(self) -> BiasState
    @property
    def value_us(self) -> float | None       # b, provisional included
    @property
    def seeded_now(self) -> bool             # True for the update that produced the first value of b (the engine sets β := 0, C1)
    @property
    def frozen(self) -> bool                 # the controller waits (reseed after a reading break, B4b)
    @property
    def learned(self) -> LearnedSample | None   # this output's sample (at most one)
    def snapshot(self) -> dict[str, object]  # the air_* keys of the spec's "Playout snapshot" table
```

Rules for this task (spec "State machine", "Tracking", "Learned target"):
- A sample is **eligible** when `confidence ≥ MIN_CONFIDENCE`, `x_lo ≥` the newest event `x` and the last definition change `x` (S-3), no event `x` lies inside `(x_lo, x_hi)`, and `history.median_over(x_lo, x_hi)` is not None. Ineligible samples are counted in `air_bias_skipped` and ignored.
- Target: the view's target is taken on every update while this output has not published its learned sample (I3) and while the source is `calibrated`; afterwards learned-only changes are ignored (B4b covers calibration changes). `L_eff` = its `latency_ms`; `D_ref` = its `d_ref_ms`, or `D_out,latched` (the view's `d_out_ms` at the first update) when None. `state = "seeding"` with a target, `"none"` without (`air_bias_reason = "no_target"`).
- Seeding (and, in `none`, learning only): keep the newest three eligible samples; they agree when `max(E) − min(E) ≤ SEED_AGREE_US` with `E_i = h_i + (L_eff − D_ref)·1000` (in `none`, agreement on `h_i`). On the **first** agreement of the output emit the learned sample (`x_ms = median(d_out_i − h_i)/1000`, `d_out_ms = median(d_out_i)/1000`, the feed's key, `feed.next_learned_seq()`; no key → none). With a target: `b₀ = median(ê_i − E_i)` (this folds the current `β`, C1: `median(ê_i − β − E_i) + β`), `state = "active"`, `seeded_now = True`, the MAD history starts with the three `b_i` (I6), return `AirAction(+median(E_i), "air_seed")` when `|median(E_i)| > DEAD_BAND_US`. After `SEED_MAX_WINDOWS` eligible samples without agreement: `b = median(b_i of the last 5)`, `active`, history = those five, no action, no learned sample.
- Tracking in `active`: `b_i = ê − E_i`; gate `max(GATE_MIN_US, GATE_MAD_K·1.4826·MAD(history))`, history = the last `GATE_HISTORY` **accepted** `b_i`; inside the gate `b += (1 − exp(−Δt/BIAS_TAU_S))·(b_i − b)` with `Δt = clamp((at_i − at_prev)/1e6, 0, DT_MAX_S)` and `b_i` joins the history; outside it counts `air_bias_gated` (not added to the history), and `STEP_WINDOWS` consecutive gated samples within `STEP_AGREE_US` of each other set `b` to their median and restart the history with them (INFO `air bias step: … → … ms`), no action.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_air_learned.py
import statistics

import pytest

from sendspin_bridge.services.audio.playout.air_learned import learned_reference

STAGE_A_H = [-45.2, -32.4, -31.4, -44.5, -44.9, -36.3, -44.7, -36.4]


def test_robust_mean_of_the_raw_placements():
    xs = [184.0 - h for h in STAGE_A_H]
    latency, d_ref = learned_reference(xs, [184.0] * 8)
    assert d_ref == 184.0 and latency == pytest.approx(184.0 - statistics.mean(STAGE_A_H), abs=1e-9)


def test_an_outlier_start_is_left_out():
    xs = [184.0 - h for h in [-40.0, -41.0, -39.0, -60.0]]  # -60 is 20 ms from the median
    latency, _ = learned_reference(xs, [184.0] * 4)
    assert latency == pytest.approx(184.0 + 40.0, abs=1e-9)


def test_the_delay_of_a_sample_does_not_move_the_target():
    h = [-40.0, -41.0, -39.0]
    a = learned_reference([184.0 - x for x in h], [184.0] * 3)
    b = learned_reference([214.0 - x for x in h], [214.0, 214.0, 184.0])
    assert a[0] - a[1] == pytest.approx(b[0] - b[1])


@pytest.mark.parametrize(("xs", "ds"), [([], []), ([1.0], [1.0, 2.0]), ([1500.0], [184.0])])
def test_invalid_input_raises(xs, ds):
    with pytest.raises(ValueError):
        learned_reference(xs, ds)


def test_stage_a_sequence_moves_less_with_the_robust_mean_than_with_the_median():
    used_mean = [learned_reference([184.0 - h for h in STAGE_A_H[:k]], [184.0] * k)[0] for k in range(3, 8)]
    used_median = [184.0 - statistics.median(STAGE_A_H[:k]) for k in range(3, 8)]
    assert statistics.stdev(used_mean) == pytest.approx(1.44, abs=0.05)
    assert statistics.stdev(used_median) == pytest.approx(5.02, abs=0.05)
```

```python
# tests/unit/services/audio/playout/test_air_bias.py (part 1)
import math

import pytest

from sendspin_bridge.services.audio.playout.air_bias import (
    SEED_MAX_WINDOWS, STEP_WINDOWS, AirBias, AirFeed, AirSample,
)

L_AS = 228.7
D_OUT = 184_000.0  # the delay the windows' audio was written with
D_REF = 184.0      # the calibration's reference delay (ms); equal here, so E = H + L_as − D_out
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
    feed.set_output_delay(D_OUT / 1000)
    feed.set_target(latency_ms=latency, d_ref_ms=D_REF, learned_ms=learned, learned_d_ref_ms=D_REF, learned_starts=starts)
    return AirBias(feed), feed


def run(b, feed, hist, samples, t0=20.0, beta_us=0.0):
    actions = []
    for k, s in enumerate(samples):
        feed.push(s)
        a = b.update(t0 + k, hist, beta_us=beta_us)
        if a is not None:
            actions.append(a)
    return actions


def test_three_agreeing_windows_seed_with_one_full_correction():
    b, feed = bias()
    # A late start: H = -6 ms gives E = H + L_as - D_ref = +38.7 ms (the three within 0.7 ms)
    acts = run(b, feed, FakeHistory(5_000.0), [win(i, -6_000.0 + d) for i, d in enumerate((0.0, 300.0, -400.0))])
    e_med = -6_000.0 + (L_AS - D_REF) * 1000
    assert b.state == "active"
    assert len(acts) == 1 and acts[0].reason == "air_seed"
    assert acts[0].jump_us == pytest.approx(e_med, abs=1.0)  # no 50 ms clamp, positive = late = drop
    assert b.value_us == pytest.approx(5_000.0 - e_med, abs=1.0)


def test_the_seed_folds_the_baseline():
    # ê is raw: a 30 ms reading artefact measured as β before the seed ends up in b, not in c (C1)
    b, feed = bias()
    h = -6_000.0
    run(b, feed, FakeHistory(35_000.0), [win(i, h) for i in range(3)], beta_us=30_000.0)
    assert b.value_us == pytest.approx(35_000.0 - (h + (L_AS - D_REF) * 1000), abs=1.0)


def test_inside_the_dead_band_no_jump():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000 + 1_500.0  # E = +1.5 ms
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
    h = (D_REF - L_AS) * 1000  # E = 0
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    run(b, feed, FakeHistory(8_000.0), [win(i + 3, h) for i in range(30)], t0=23.0)  # b_i 3 ms higher for 30 s
    assert b.value_us == pytest.approx(5_000.0 + 3_000.0 * (1 - math.exp(-1.0)), abs=150.0)


def test_one_wrong_window_is_gated_and_does_not_widen_the_gate():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    before = b.value_us
    run(b, feed, FakeHistory(5_000.0), [win(3 + k, h - 50_000.0) for k in range(5)], t0=23.0)  # 5 wrong in a row
    assert abs(b.value_us - before) < 100.0 and b.snapshot()["air_bias_gated"] == 5  # MAD over accepted only (I6)


def test_a_persistent_hidden_step_is_absorbed_after_15_windows():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
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
    assert s.d_out_ms == pytest.approx(D_OUT / 1000)
    run(b, feed, FakeHistory(), [win(i + 6, h) for i in range(6)], t0=26.0)
    assert b.learned.seq == 1  # still the one sample


def test_no_key_no_learned_sample():
    feed = AirFeed()
    feed.set_lock("locked", 1.0)
    b = AirBias(feed)
    run(b, feed, FakeHistory(), [win(i, -35_000.0) for i in range(3)])
    assert b.learned is None


def test_the_target_does_not_contain_the_windows_delay():
    # Windows written at a delay 30 ms above D_ref, same H: the same E (the trim is the delay itself)
    b, feed = bias()
    run(b, feed, FakeHistory(), [win(i, -6_000.0, d_out=D_OUT + 30_000.0) for i in range(3)])
    assert b.snapshot()["air_last_correction_ms"] == pytest.approx(-6.0 + L_AS - D_REF, abs=0.01)


def test_events_inside_a_window_exclude_it():
    b, feed = bias()
    b.on_event(21.5e9)  # inside the first two windows' spans
    run(b, feed, FakeHistory(), [win(i, -6_000.0) for i in range(3)])
    assert b.state == "seeding"  # the triple must start after 21.5e9: two more windows needed
```

- [ ] **Step 2: Run** both files → FAIL.
- [ ] **Step 3: Implement** the rules above; everything in µs internally. Snapshot keys: `air_bias_state`, `air_bias_ms`, `air_bias_reason`, `air_target`, `air_target_ms`, `air_d_ref_ms`, `air_trim_ms` (`D_out,latched + ΣΔD − D_ref`, ΣΔD from B4b), `air_learned_starts`, `air_corrections` (incremented when an action is returned), `air_last_correction_ms`, `air_bias_gated`, `air_bias_skipped`, `air_learned` (`{"seq","x_ms","d_out_ms","codec","rate","adapter"}` or None). INFO on seeding (`air bias seeded at %.1f ms (E %+.1f ms, 3 windows within %.1f ms)`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(air): the learned reference and the bias filter — seed, tracking, gate, learned sample`.

### Task B4b: `AirBias` — events, targets, held and reseed

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_bias.py`
- Test: `tests/unit/services/audio/playout/test_air_bias.py` (part 2: events)

**Interfaces:**

```python
class AirBias:
    def on_delay(self, delta_d_us: float, x_ns: float) -> None          # ΔD: b unchanged, ΣΔD += ΔD, an event at x
    def on_format(self, x_ns: float) -> None
    def on_reading_break(self, kind: Literal["underrun", "stall", "pa_step"], x_ns: float, now_s: float) -> None
    def resume_beta(self) -> float      # returns the β the engine must clear when held resumes (it is folded into b)
    @property
    def handles_breaks(self) -> bool    # True while b has a value outside held: the engine measures no baseline (C1)
    @property
    def reseed_pending(self) -> bool
```

Rules (spec table): delay → `b` unchanged, `x` recorded as an event, `ΣΔD += ΔD`, INFO `Output delay changed …; air target trim −(D_out − D_ref) = … ms` when a target exists; format → provisional reseed (`seeding`, value kept, triple restarts, definition change at `x`; in `held` only `reseed_pending = True`); reading break in `active` or provisional `seeding` → provisional reseed, `frozen = True` until the reseed or `RESEED_TIMEOUT_S`; at the timeout: sensor locked → `active` with the provisional value, unfrozen; lock lost → `held` with `reseed_pending`; in `held` → `reseed_pending = True` (the engine measures today's baseline on `c`); in `none`/initial `seeding` → triple restarts. Lock: a view lock state other than `locked`/`holding` → `active`/provisional `seeding` → `held` (`air_bias_reason = "not_locked"`, INFO `air bias held (sensor <state>)`); `held` + an eligible sample with the lock back → the engine's `β` is folded first (`b += resume_beta()` handshake: the engine passes `beta_us` on `update`, the bias adds it to `b` and reports it so the engine sets `β := 0`), then `reseed_pending` → `seeding` with the provisional value (I2), else `active` with a tracking update, no action. Target after the first update: calibrated `latency_ms − d_ref_ms` changed by `ΔL` while a value exists → `b −= ΔL`, definition change, `AirAction(+ΔL, "air_target")` when `|ΔL| > DEAD_BAND_US`; a calibration appearing in `none` → `seeding`; calibration gone → the learned value in force (`ΔL` as above) or → `none` (`no_target`, value dropped; the engine performs the bias → none transition, B5a); a learned value is taken until this output published its sample (I3), ignored after. A reseed's agreeing triple replaces the value (no filtering across) and may return `air_seed`; it never emits a learned sample.

- [ ] **Step 1: Failing tests** (part 1's helpers). Each case starts from a fresh, seeded `active` instance (three windows at `E = 0`, `b = 5 ms`) unless it says "sequence":
  - delay `+30 ms` → `value_us` unchanged, `air_trim_ms` 30.0, a window spanning the delay's `x` is skipped (`air_bias_skipped` +1); windows after it with `d_out` +30 ms and the same `H` return no action and leave `b` within 0.1 ms; the INFO trim line is logged;
  - format → `seeding`, value unchanged; next agreeing triple at the same `E` → no action; (separate case) a triple 5 ms later → `air_seed` of 5 000;
  - underrun → `frozen`, `handles_breaks`, triple → `active`, not frozen;
  - underrun, then no eligible window for 15 s of `now_s` with the lock kept → `active`, not frozen, value unchanged;
  - sequence: underrun, lock `no_lock` → `held`, `reseed_pending`; lock back + three agreeing windows → `seeding` after the first, `active` after the third, an `air_seed` only beyond 2 ms (I2);
  - lock `no_lock` → `held`, value constant over 60 windows that are all ineligible; lock back + one window with `beta_us = 12_000` → `active`, `b` = old + 12 000 (C1), no action;
  - calibration 228.7 → 248.7 (same `d_ref_ms`) → `air_target` +20 000, value −20 000; same `latency_ms` with `d_ref_ms` 184 → 204 → `air_target` −20 000;
  - calibration forgotten with learned 226.0 (`learned_d_ref_ms` 184) in force → `air_target` of `(226.0 − 248.7)·1000` (sequence after the previous change) / `(226.0 − 228.7)·1000` (fresh);
  - forgotten without learned → `none`, `value_us is None`;
  - learned only, `none`: a learned value (2 starts) arriving before the output's own sample → `seeding` (I3); arriving after the sample was published → still `none`;
  - a reseed never sets a second `learned`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** the whole file → PASS.
- [ ] **Step 5: Commit** `feat(air): bias events — delay, format, reading breaks, held, lock and target changes`.

### Task B5a: Engine — bias domains, baseline rules and the guard

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/engine.py`
- Test: `tests/unit/services/audio/playout/test_engine_air.py` (new; part 1)

**Interfaces:**

```python
class PlayoutEngine:
    def __init__(self, *, settings, port, k0_ns=0, wall_iso=_wall_iso, air_feed: AirFeed | None = None) -> None
        # AirBias + EHistory only when settings.mode == "loop" and air_feed is not None
    def _air(self, fn: Callable[[], T], default: T) -> T
        # the one guard (I8): every AirBias/EHistory call; on an exception: _disable_air("internal_error"), return default
```

Changes (spec "The engine's changes", "Residual domains"):
- `_sense`: after `s_port`, `_air(lambda: history.add(r.t_us, r.x_port_ns, r.t_us − s_port, usable=…))`; then `action = _air(lambda: bias.update(t_s, history, beta_us=β), None)`; when `bias.seeded_now`: `β := 0`, stop the baseline collection, and without an action re-base `controller.placed(t, residual = EMA + β_old − b₀)`; a resume from `held` sets `β := 0` (the bias folded it). Actions are queued in B5b.
- Controller input: `e_rel = e_w − β − (bias.value_us or 0.0)`; `if bias.frozen: return`.
- `_placement_offset_us()`: `bias.value_us + β` with a value, else B0's rule.
- `_record_placement`: skip today's baseline collection when `bias.handles_breaks`; the residual for `placed` is `o − β − (b or 0)` and the EMA restart `o − (b or 0)` (published `+β`).
- The R14 re-measure branch (engine.py:326-339) compares `wanted + β` against `J` in the controller's domain, unchanged in form.
- `_disable_air(reason)`: the bias → none transition (`r = EMA + β + b`, `β := 0`, `placed(t, residual = r)`, EMA `r`), `air_bias_state: none`, `air_bias_reason: reason`, WARNING with traceback via `RateLimitedLog` (once per 10 min); also used when the bias drops its value (calibration forgotten, no learned).
- `snapshot()` merges `bias.snapshot()` (keys present with `None`/0 values when there is no bias, `air_bias_state: None`).

- [ ] **Step 1: Failing tests** (`test_engine_air.py` part 1, with the sim and a real `AirFeed` fed by hand through a small helper that pushes samples whose `x` spans cover audio the engine already wrote): `measure` with a feed → no bias, `air_bias_state is None`; `loop` without a feed → same; `loop` with a calibrated target and three samples at `E = +1.0 ms` (inside the dead band) → `active`, no jump, `error_ms` ≈ `E` ± 1.5 (EMA in the `c` domain) while the raw pulsesink error ≈ `error_ms + air_bias_ms`; a `clear` afterwards keeps `c` ≈ 0 ± 1.5 and `air_bias_ms` unchanged; an underrun before the seed with a 30 ms reading slew → "error baseline" INFO, then the seed → `β == 0` (introspect `_baseline_us`), `error_ms` within ±1.5 of `E`; an underrun in `active` → no "error baseline" line, the controller frozen until three new samples; a `held` underrun with a 12 ms slew → baseline 12 ms, lock back + a sample → `β == 0`, `air_bias_ms` +12; an `AirBias` whose `update` raises → `air_bias_state == "none"`, `air_bias_reason == "internal_error"`, one WARNING with a traceback (a second failure within 10 min logs nothing new), `error_ms` continuous (within 1.5 ms of before), `state == "playing"`; an `EHistory.add` that raises → the same.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` → PASS (B0's tests unchanged).
- [ ] **Step 5: Commit** `feat(playout): loop mode measures its error against the air bias`.

### Task B5b: Engine — air jumps, events and the player's feed

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/engine.py`, `src/sendspin_bridge/services/audio/playout/gst_output.py` (`PlayoutPlayer(air_feed=…)`, the engine gets it in `adopt_output`, `_reopen_air_tap` calls `feed.reopen(tap.gen)` after `tap.reset()`)
- Test: `tests/unit/services/audio/playout/test_engine_air.py` (part 2), `tests/unit/services/audio/playout/test_gst_output.py` (extend)

Changes:
- An `AirAction` sets `_pending_jump_us` and `_pending_jump_reason` (`air_seed`/`air_target`); dropped when a placement is pending; not clamped; after it renders, `_record_event` logs `playout jump: air_seed, 37.2 ms` and `controller.placed(t, 0.0, quiet_wait=True)`.
- Every `_record_event` (placements, delay/steady/air jumps) calls `_air(lambda: bias.on_event(x_w))`; `resync(shift ≠ 0)` calls `bias.on_delay(−shift, x_w)` (B0's jump does the rest); `set_format` with a previous format calls `bias.on_format(x_w)`; new underrun / stall / PA step in `_sense` call `bias.on_reading_break(kind, r.x_port_ns, t_s)`.
- The 10 s DEBUG summary adds `b` and the state.

- [ ] **Step 1: Failing tests:** three samples at `E = +37 ms` → one `air_seed` jump of +37.0 ± 0.05 ms (`last_jump_reason == "air_seed"`, `air_corrections == 1`, no "clamped" line), afterwards `error_ms` ≈ 0 ± 1.5; an action while a placement is pending is dropped (no second jump, `air_corrections` still counts only executed ones); a delay change `+30 ms` (`resync(−30_000)`) in `active` → the marker heard exactly 30 ms earlier than without the change (± 0.5 ms, B0's method), `air_bias_ms` unchanged, no air jump, the INFO trim line; a format change → `air_bias_state == "seeding"` until three new samples. `test_gst_output.py`: `adopt_output` passes the feed to the new engine and `feed.reopen` is called with the tap's generation after its reset (fakes as in the existing file); `PlayoutPlayer` without a feed behaves as today.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/audio/playout -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(playout): loop mode corrects placement from the air bias`.

### Task B6a: Closed-loop simulation — harness and the fast subset

**Files:**
- Modify: `tests/unit/services/audio/playout/sim.py` (`SimulatedPort.true_port_time_us(x_ns)`, `drive(..., on_block=None)`, new `SimAir`)
- Create: `tests/unit/services/audio/playout/test_air_closed_loop.py` (fast cases)

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
                 pipeline_us=250_000.0, acquire_s=4.0, d_out_us=184_000.0, tap_gen=1, confidence=1.0) -> None
    def on_block(self, block) -> None      # records (x0, duration, s(x0) from engine.schedule_at, d_out) at write time
    def tick(self, now_us: float) -> None  # every hop: the window of air in [now − pipeline − window, now − pipeline];
        # none until acquire_s of air has flowed (one window + one hop); t_air(x) = true_port_time_us(x) + h0_us;
        # H = t_air(x_mid) − s(x_mid) + N(0, sigma_h); pushes AirSample(tap_gen, x_lo, x_hi, t_air(x_mid), H, d_out(x_mid),
        # confidence); feed.set_lock("locked", confidence) once windows flow
    def set_d_out(self, d_out_us: float) -> None
    def stop(self) / resume(self)          # no windows while stopped; after 20 s set_lock("no_lock", None)
    def heard_error_us(self, x_ns: float, l_true_ms: float) -> float  # truth: t_air + L_true − (s + d_out) = sound − client time
```

Each test builds a fresh engine per start (`PlayoutSettings(mode="loop")`, the feed reopened with `tap_gen + 1`), submits 40 s of tone, calls `SimAir.tick` from `drive(on_sense=…)` and `on_block`, and measures the heard error as the median of `heard_error_us` over the audio written 20–30 s after the start.

- [ ] **Step 1: Failing tests** (fast; parameters and tolerances from the spec's table, all at `noise_us = 4000`, σ(H) = 500 µs, `L_true = L_as = 228.7`, `D_out = D_ref = 184`, `step_ms = 2.0`):
  - `test_calibrated_cold_starts` — `buffer_ms = 800`, `h0` ∈ {9, 16, 23, 30, 38, 45, 52, 60} ms: per start `|median heard error| ≤ 1.0 ms`; `sd ≤ 0.5 ms` over the 8; one `air_seed` per start; the seed jump within 10 s of the first audio (acquisition 4 s + host latency + the triple).
  - `test_measure_mode_keeps_the_spread_as_a_control` — the same starts in `measure`: `sd ≥ 15 ms`.
  - `test_delay_change_moves_the_sound_in_active` — after the seed, `eng.resync(−30_000)` and `SimAir.set_d_out(214_000)`, the feed's output delay 214: heard error moves by `−30 ± 0.5 ms`; `air_bias_ms` unchanged within 0.2 ms; `air_trim_ms == 30.0`; one more jump (`delay`), no air jump in the next 20 s; a new start at `D_out = 214` lands at `−30 ± 1 ms`.
  - `test_reading_slew_after_an_underrun_before_the_seed` (C1) — `port.starve(800)` at 1.5 s, then `port.slew(−30, 300)`: after the seed the heard error `≤ 1 ms`, the engine's `β == 0`, no steady jump in the following 30 s.
- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/playout/test_air_closed_loop.py -q -p no:cacheprovider -n0 -m "not slow"` → FAIL first on the harness, then PASS once it exists; total runtime ≤ 60 s (coarser steps or fewer starts if needed, recorded in the test's docstring). A failure after the harness exists is a defect in B4/B5: fix it here with a regression test that names it, or route it back.
- [ ] **Step 3: Commit** `test(air): closed-loop simulation harness and the fast acceptance cases`.

### Task B6b: Closed-loop simulation — the slow cases

**Files:**
- Modify: `tests/unit/services/audio/playout/test_air_closed_loop.py` (cases marked `@pytest.mark.slow`)

- [ ] **Step 1: Tests** (spec table; under 3 min in total):
  - `test_calibrated_cold_starts_low_latency` — as B6a with `buffer_ms = 200`.
  - `test_learned_target_from_the_third_start` — uncalibrated; after each start the test appends the engine's `air_learned` pair and calls `feed.set_target(latency_ms=None, learned_ms=…, learned_d_ref_ms=…, learned_starts=n)` from `learned_reference` before the next start; `h0` = stage A's sequence (shifted so the raw placement is late): starts 1–2 `air_corrections == 0`, 3–8 one each, each within 1 ms of `H_ref` at that start; print the sd of starts 4–8 for the robust mean and the plain median; assert `≤ 2.0 ms` for the robust mean; a `+30 ms` delay after start 4 moves start 5 by `−30 ± 1 ms`.
  - `test_pipewire_sawtooth[(30, 5), (27, 15)]` — `port.sawtooth(amplitude_ms, period_s)`, `noise_us = 1000`: after the seed `|median heard error| ≤ 2 ms`, no `steady` jump in 60 s.
  - `test_lock_lost_holds_the_bias` — `SimAir.stop()` for 60 s: `held`, `air_bias_ms` constant, no jump; `resume()`: `active`, no jump.
  - `test_format_change_reseeds` — `eng.set_format(InputFormat(44100))` with audio in it and `SimAir.h0_us += 5_000`: exactly one `air_seed` of `5 ± 0.5 ms`; without the `h0` change: `seeding` → `active` with no jump.
  - `test_underrun_with_a_hidden_change_reseeds` — `port.starve(800)` then `SimAir.h0_us += 12_000`: heard error within 1 ms within 15 s, no "error baseline" line.
  - `test_calibration_set_mid_stream` — learned value in force, then a calibration 20 ms above it: one `air_target` of `+20 ± 0.5 ms`.
  - `test_hidden_step_is_absorbed_then_corrected` — `SimAir.h0_us += 25_000`: after ≥ 15 windows `air_bias_ms` −25 ± 0.5, then one steady jump of ≈ 25 ms within 10 s.
- [ ] **Step 2: Run** with `-m slow` → PASS (fix or route back defects as in B6a).
- [ ] **Step 3: Commit** `test(air): closed-loop simulation — learned, PipeWire, lock, format, underrun, target and step cases`.

### Task B7: IPC and daemon wiring

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/commands.py` (`SetAirCalibration(latency_ms, d_ref_ms=None, learned_ms=None, learned_d_ref_ms=None, learned_starts=None)`, decoder and encoder, the new fields optional and encoded only when not None), `src/sendspin_bridge/services/ipc/daemon_process.py` (`_air_sensor_args` passes `d_ref_ms` and the learned three with the same validation as the command; invalid ones are dropped, the sensor still built), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`_build_air_sensor`: an `AirFeed` when `playout_mode == "loop"`, its target from the params, its output delay from `_air_output_delay_ms()`; `AirSensor(on_window=self._air_feed_push)`; a calibration tee so a `SetAirCalibration` reaches the sensor and `feed.set_target`; `air_step` sets `feed.set_lock(*sensor.lock_state())` and `feed.set_key(*sensor.key())` after each step; `_apply_output_delay` calls `feed.set_output_delay(new_ms)`; `_new_playout_player` passes `air_feed`)
- Test: `tests/unit/services/ipc/test_air_commands.py`, `tests/unit/services/ipc/test_daemon_process.py`, `tests/unit/services/ipc/test_bridge_daemon_air.py` (extend)

- [ ] **Step 1: Failing tests:** decode `{"cmd": "set_air_calibration", "v": 1, "latency_ms": 228.7, "d_ref_ms": 184.0}` → `SetAirCalibration(228.7, 184.0)`; `{… "latency_ms": null, "learned_ms": 226.0, "learned_d_ref_ms": 184.0, "learned_starts": 3}` → `SetAirCalibration(None, None, 226.0, 184.0, 3)`; `d_ref_ms` 6000 → `InvalidCommand`; `|learned_ms − learned_d_ref_ms| > 1000` → `InvalidCommand`; `learned_starts` 1, `"3"` or `True` → `InvalidCommand`; a learned field without the other two → `InvalidCommand`; the encoder without the new values produces exactly today's payload. `_air_sensor_args({"enabled": True, "latency_ms": None, "learned_ms": 226.0, "learned_d_ref_ms": 184.0, "learned_starts": 2})` keeps the learned three; with `learned_starts: 1` drops them; `{"enabled": True, "latency_ms": None}` → exactly today's dict. Daemon, built through `_air_sensor_args` like a spawn: `loop` with those params → `d._air_feed.take().target` is `("learned", 226.0, 184.0)` and `d_out_ms` the static delay; `measure` → `d._air_feed is None`; an accepted window reaches the feed as an `AirSample` with the window's `x_lo_ns`, `x_hi_ns`, `gen`, `h_us`, `d_out_us`, confidence; a dispatched `set_air_calibration` with `latency_ms` 228.7, `d_ref_ms` 184 sets the feed's target to calibrated and the sensor's calibration; after `air_step` the feed's lock and key equal the sensor's; a delay change sets the feed's output delay; `_make_player` passes the daemon's feed (the same object for every player).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/ipc -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(air): the daemon feeds the playout engine from the air sensor in loop mode`.

### Task B8a: Config — learned entries and the calibration's reference delay

**Files:**
- Modify: `src/sendspin_bridge/config/players.py` (`AirLatencyEntry.d_ref_ms`, `AirLearnedEntry`, `AIR_LEARNED_MAX_ENTRIES = 8`, `AIR_LEARNED_MAX_SAMPLES = 8`, `Player.air_learned_latency`, helpers importing `learned_reference` from `services/audio/playout/air_learned.py`), `src/sendspin_bridge/config/__init__.py` (re-exports, `save_air_learned_sample(player_id, key, x_ms, d_out_ms)` writing through the locked config update like `save_device_volume`, config/__init__.py:424), `src/sendspin_bridge/config/migration.py` (`_clean_air_learned`, called where `_clean_air_latency` is; `_clean_air_latency` fills a missing `d_ref_ms` from the player's `static_delay_ms`, else 0, one INFO line per entry), `src/sendspin_bridge/config/schema.json`, `scripts/translate_ha_config.py` (preserved field `air_learned_latency` next to `air_to_sound_latency`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (hot-apply set)
- Test: `tests/unit/config/test_air_learned_config.py` (new), `tests/integration/test_translate_ha_config.py` (one new test)

Adding `d_ref_ms` to the strict `AirLatencyEntry` is safe: stage A is not released.

**Interfaces:**

```python
# config/players.py
class AirLatencyEntry(BaseModel):  # stage A, gains:
    d_ref_ms: float | None = Field(None, ge=0, le=5000)   # the delay the fold was measured with (S-22)

class AirLearnedEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    codec: Literal["sbc"]
    rate: Literal[16000, 32000, 44100, 48000]
    adapter: str = Field(pattern=r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
    source: Literal["learned"] = "learned"
    samples_ms: list[float] = Field(min_length=1, max_length=8)                               # finite
    d_out_ms: list[Annotated[float, Field(ge=0, le=5000)]] = Field(min_length=1, max_length=8)   # same length
    latency_ms: float      # validated against learned_reference within 0.05
    d_ref_ms: float        # validated against learned_reference within 0.05
    starts: int = Field(ge=1)  # ≥ len(samples_ms)
    measured_at: str
    # validator: equal lengths, every |samples_ms[i] − d_out_ms[i]| ≤ 1000 (the offset, not the X range)

def valid_air_learned_entries(raw: object) -> list[dict[str, Any]]            # invalid dropped one by one, one per key, ≤ 8 newest
def resolve_air_learned(player_cfg, codec, rate, adapter) -> tuple[float, float, int] | None   # (latency, d_ref, starts); None below 2
def record_air_learned_sample(entries, key: tuple[str, int, str], x_ms: float, d_out_ms: float, now_iso: str) -> list[dict]
    # appends the pair (oldest dropped beyond 8), recomputes latency_ms/d_ref_ms, starts += 1, measured_at = now_iso
def forget_air_learned(entries, codec=None, rate=None, adapter=None) -> tuple[list[dict], int]
```

- [ ] **Step 1: Failing tests:** the model accepts the spec's example and rejects a string number, a ninth sample, `d_out_ms` of another length, an offset `|X − d|` of 1001 ms, a `latency_ms` or `d_ref_ms` 1 ms off `learned_reference`, `starts` below the sample count, an unknown key; `record_air_learned_sample` on `[]` gives one pair, `starts == 1`; nine records keep the newest 8, `starts == 9`; `resolve_air_learned` is None with one pair and `(latency, d_ref, 2)` with two; codec case and adapter MAC normalised like `resolve_air_latency`; `forget_air_learned` one key / all; a bad entry is dropped, the others kept; migration: a config with one valid and one invalid learned entry loads with one entry and a WARNING, never dropping the player; a stage A calibration without `d_ref_ms` gets the player's `static_delay_ms` (0 without one) and one INFO line; a calibration with `d_ref_ms` 6000 is dropped; schema declares `air_learned_latency` (array, maxItems 8) and `d_ref_ms`; config diff: `air_learned_latency` is hot-apply; `save_air_learned_sample` on a temp config appends to the right player and key; HA translate keeps `air_learned_latency` across an add-on restart.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/config tests/integration/test_translate_ha_config.py -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(config): learned air latency per speaker, codec, rate and adapter; reference delay of calibrations`.

### Task B8b-1: Parent — learned target, sample persistence, Forget clears both

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (`air_learned_latency` attribute loaded with `air_to_sound_latency` at spawn and on hot apply; spawn param `air_sensor` gains `d_ref_ms` when the calibration has one and `learned_ms`/`learned_d_ref_ms`/`learned_starts` only when `resolve_air_learned` returns a value; `_send_air_calibration_once` compares and sends `(latency_ms, d_ref_ms, learned_ms, learned_d_ref_ms, learned_starts)`; on every status update whose `playout.air_learned` has a `(daemon process, seq)` not seen: validate (key complete, `d_out_ms` 0…5000, `|x_ms − d_out_ms| ≤ 1000`), schedule `save_air_learned_sample` off the loop like `_schedule_persist` (generalise its value argument), update the in-memory entries and resend), `src/sendspin_bridge/application/devices.py` (`forget_air_calibrations` also forgets the learned entry or all of them; 404 only when neither had the key)
- Test: `tests/unit/bridge/test_air_learned_parent.py` (new), `tests/unit/api/test_air_sensor_api.py` (extend: Forget)

- [ ] **Step 1: Failing tests:** a client with one learned pair spawns with `air_sensor == {"enabled": True, "latency_ms": None}` (unchanged dict); with two, the dict adds `learned_ms`, `learned_d_ref_ms`, `learned_starts: 2` from `learned_reference`; a calibration with `d_ref_ms` 184 adds `"d_ref_ms": 184.0`; a status with `playout.air_learned = {"seq": 1, "x_ms": 228.1, "d_out_ms": 184.0, "codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6"}` persists once (a second identical status does not write again; a new daemon process with seq 1 writes again), then a `set_air_calibration` with the new learned value is sent to a daemon that advertises `accepts`; an invalid sample (rate missing, offset 1500 ms, `d_out_ms` missing) is ignored with one DEBUG line; Forget of one key removes the calibration and the learned entry and resends `latency_ms: null` without learned fields; Forget of a key that has only a learned entry → 204; neither → 404 `unknown_calibration`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/bridge tests/unit/api tests/unit/services/ipc -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(air): the bridge learns each speaker's air latency from its starts`.

### Task B8b-2: The calibration's reference delay — fold `d_out_ms` and `air_delay_changed`

**Files:**
- Modify: `src/sendspin_bridge/services/audio/playout/air_sensor.py` (`fold` returns the median `T − s` of the folded cells as `d_out_ms`; cells with more than one delay (spread > 0.5 ms) answer `ok: false, reason: "air_delay_changed"`; the status `fold` dict gains `d_out_ms`), `src/sendspin_bridge/bridge/client.py` (the fold result's `d_out_ms` passes through), `src/sendspin_bridge/application/sync_tuning.py` (the saved calibration entry carries `d_ref_ms` = the member's written fold `d_out_ms`; `air_delay_changed` joins the ineligibility reasons after `air_epoch_changed`), `src/sendspin_bridge/application/models/sync.py` and `docs/contracts/sync-contract-v1.json` if the reason list is enumerated there, `ui/src/i18n/{en,ru}.json` (the reason's text: "The speaker's delay changed during the recording." / "Задержка колонки изменилась во время записи.")
- Test: `tests/unit/services/audio/playout/test_air_sensor.py` (extend: fold), `tests/unit/api/test_sync_air_calibration.py` (extend)

- [ ] **Step 1: Failing tests:** a fold over cells written at 184 ms answers `d_out_ms: 184.0`; over cells at 184 and 214 ms answers `ok: false, reason: "air_delay_changed"`; a wizard save stores `d_ref_ms` 184.0 in the entry; a member whose written fold answered `air_delay_changed` is ineligible with that reason; the UI text exists in en and ru.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/audio/playout tests/unit/api -q -p no:cacheprovider` and `cd ui && npm test` → PASS.
- [ ] **Step 5: Commit** `feat(sync): calibrations remember the delay they were measured at`.

### Task B9: API and OpenAPI

**Files:**
- Modify: `src/sendspin_bridge/application/models/devices.py` (`PlayoutTiming`: `air_bias_state`, `air_bias_ms`, `air_bias_reason`, `air_target`, `air_target_ms`, `air_d_ref_ms`, `air_trim_ms`, `air_learned_starts`, `air_corrections: int = 0`, `air_last_correction_ms`, `air_bias_gated: int = 0`, `air_bias_skipped: int = 0`, with descriptions; the class docstring says `error_ms` is the corrected error in `loop` with a bias; `AirLatencyEntryOut.d_ref_ms`; `AirLearnedEntryOut`; the device's audio model gains `air_learned_latency`), `src/sendspin_bridge/application/status.py` (pass `valid_air_learned_entries(player.get("air_learned_latency"))`), `ui/openapi.json` and `ui/src/api/*` (regenerated)
- Test: `tests/unit/api/test_air_correction_api.py` (new)

- [ ] **Step 1: Failing tests:** `PlayoutTiming.model_validate` keeps the new keys and ignores `air_learned`; the device JSON carries `timing.playout.air_bias_state`, `timing.playout.air_bias_skipped`, `audio.air_learned_latency` and `audio.air_to_sound_latency[].d_ref_ms`; the OpenAPI schema lists the new fields as required in the output.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**, then `uv run python scripts/export_openapi.py` and `cd ui && npm run gen:api`. **Step 4: Run** `uv run pytest tests/unit/api -q -p no:cacheprovider` and `cd ui && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(api): air correction state and learned latencies in the device model`.

### Task B10: UI — the timing panel

**Files:**
- Modify: `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/i18n/{en,ru}.json`
- Test: `ui/tests/component/devices/DeviceTimingPanel.air.test.ts`, `ui/tests/component/devices/DeviceTimingPanel.calibration.test.ts` (extend)

- [ ] **Step 1: Failing tests:** `loop` + `air_target: "calibrated"`, `air_target_ms: 228.7` → "Air correction: calibrated (228.7 ms)"; `learned`, 6 starts → "learned (6 starts)"; `air_bias_state: "none"`, reason `no_target`, one learned pair stored → "no data (learning: 1 of 2 starts)"; `air_bias_ms: null` with the sensor not locked → "inactive: not locked"; bias +12.3 → "Air bias +12.3 ms"; `air_corrections: 1` → "Air corrections 1"; `air_trim_ms: 30` → "Delay trim −30.0 ms (target 30.0 ms early)", `0` → no trim row; `measure` → no correction line; a learned entry renders "Learned: 219.4 ms at 184 ms delay · SBC · hci1 · 6 starts" with Forget calling the existing forget API; ru texts exist for every new key (the i18n key test).
- [ ] **Step 2: Run** `cd ui && npm test` → FAIL. **Step 3: Implement.** **Step 4: Run** `npm test && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(ui): air correction line and learned latencies in the timing panel`.

### Task B11: Guidance — `air_correction_inactive`

**Files:**
- Modify: `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (entry: layer `audio`, severity `info`, reason codes `air_switch_off`, `air_not_effective`, `air_unavailable`, `air_not_locked`, priority next to `air_sensor_unavailable`), `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (`_build_air_correction_issues`, called next to `_build_air_sensor_issues`), `ui/src/i18n/{en,ru}.json` (issue texts)
- Test: `tests/unit/services/diagnostics/test_air_correction_guidance.py` (new)

- [ ] **Step 1: Failing tests** (device snapshots as in `test_air_guidance.py`; condition per the spec's table, in order): `playout.mode == "loop"` and `air_sensor is None` → `air_switch_off`; `air_sensor.state == "off"` → `air_not_effective`; `unavailable`/`unsupported_codec`/`unsupported_input` → `air_unavailable`; `air_bias_reason == "internal_error"` → `air_unavailable`; `warm_s = 61`, `air_bias_ms = None`, `air_bias_state = "seeding"`, sensor `acquiring` → `air_not_locked`; the same with `air_bias_state = "none"`, reason `no_target`, sensor `no_lock` → `air_not_locked`; `warm_s = 59` → no issue; `none`/`no_target` with the sensor `locked` (learning) → no issue; `seeding` with `air_bias_ms = 12.0` (provisional) → no issue; `held` → no issue; `active` → no issue; `measure` → no issue; the registry entry exists with i18n texts in en and ru.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/services/diagnostics -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(diagnostics): tell when a loop-mode speaker runs without its air correction`.

### Task B12: Stand acceptance (manual, done by the controller)

Not for an implementer subagent: the controller runs it on the HAOS stand with the branch overlay, as in stage A's A17.

**Interfaces:**
- Consumes: HAOS VM (PulseAudio 17), ENEBY20 on hci1, `PLAYOUT_SYNC=loop`, `EXPERIMENTAL_AIR_SENSOR=true`, `PREFER_SBC_CODEC=true`, `SENDSPIN_AIR_DEBUG=1`; the desktop bridge's headphone output on the playout engine in `measure` as the warm reference; headset microphone; the coherent click estimator; MA (test environment, any player action allowed; never reload the MA sendspin provider).
- Produces: the "Stage B1 validation note" in the spec.

- [ ] **Step 1: Restart method.** Find how to restart only ENEBY20's stream while the reference keeps playing: MA `players/cmd/power` off/on of the member, then un-sync/re-sync (`players/cmd/ungroup`, `players/cmd/group` to the leader), then member `stop`/`play`; take the first one after which ENEBY20's daemon logs a new `playout output open` and the reference's does not.
- [ ] **Step 2: Series 1, calibrated.** First re-calibrate ENEBY20 in the wizard against the playout reference, so the entry carries a measured `d_ref_ms` (the migrated A17 entry's is an estimate). Then 8 restarts; per restart wait 20 s, then ≥ 20 s of clicks with alternating solos; `M_i` = ENEBY20 − reference. Pass: `sd(M) ≤ 2 ms`, `|median M| ≤ 3 ms`; one `air_seed` per start, within 10 s. Record `b`, seed time, `air_bias_gated`, reseeds.
- [ ] **Step 3: Series 2, uncalibrated:** Forget (calibration and learned), 8 restarts. Pass: `sd(M_4 … M_8) ≤ 2 ms`. Record `X_i` and the learned value per start.
- [ ] **Step 4: Q6/I1 check** in `measure`: a delay change of `+30 ms` moves ENEBY20 by `−30 ± 1 ms` (microphone) and logs `playout jump: delay, 30.0 ms`.
- [ ] **Step 5: Slider check in `loop`** after series 1: `+30 ms` → ENEBY20 moves by `−30 ± 1.5 ms`, `air_bias_ms` unchanged, no air jump, the INFO trim line; one more restart at the new delay lands at `−30 ± 2 ms`; then the delay goes back.
- [ ] **Step 6: CPU:** `pidstat` of ENEBY20's daemon over 60 s of music, `loop` vs `measure` (switch on in both): ≤ +0.3 % of one core.
- [ ] **Step 7: Guidance:** switch off with `loop` → `air_correction_inactive` (`air_switch_off`); locked `loop` → none.
- [ ] **Step 8:** Hand the numbers to B13.

### Task B13: Validation note, docs and changelog

Starts only after the concurrent stage A docs work (stage A spec, `docs-site/`, `CHANGELOG.md`) is committed; rebase first.

**Files:**
- Modify: `docs/plans/2026-10-28-air-sensor-stage-b-design.md` (append "Stage B1 validation note"; update the constants/rulings rows the stand changed), `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md` (air correction in `loop`: what it does, calibrated vs learned, the delay as a trim relative to the calibration's delay, Forget), `docs-site/src/content/docs/{,ru/}configuration.md` (the switch now also in `loop`), `CHANGELOG.md`

- [ ] **Step 1: Validation note** in the stage A note's format: restart method; series 1 `M` per start, `sd`, `median`; series 2 `M`, `sd(M_4…M_8)`, `X_i`; Q6/I1 check; slider check in `loop`; CPU; guidance; any constant changed and why.
- [ ] **Step 2: Docs** (en, ru) as listed.
- [ ] **Step 3: Changelog** `[Unreleased]`: `### Added` — "Playout sync `loop` mode now corrects Bluetooth speakers from the experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`): each start is placed where the speaker's audio really leaves over the air, on the schedule when the speaker's latency is calibrated in the group sync wizard, otherwise where it played on average in its previous starts. The speaker's delay setting keeps working as a trim: changing it moves the speaker by exactly the change. The timing panel shows the correction; Forget also clears the learned latency." `### Fixed` — "In playout sync a delay change now moves the speaker by exactly the change; before, it also removed a small leftover offset (up to the dead band), and a gap after an underrun could move the audio by the offset the engine had just reported." Lint: `uv run python scripts/lint_changelog.py CHANGELOG.md`.
- [ ] **Step 4: Commit** only these files: `docs(air): stage B1 validation note and user docs`.
