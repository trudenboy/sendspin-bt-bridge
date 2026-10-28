# Air sensor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every SBC Bluetooth speaker in `PLAYOUT_SYNC=measure` with `EXPERIMENTAL_AIR_SENSOR` on, the bridge reports when the audio it wrote actually left over the air: the host-side placement `H = t_air − s(x)`, the air error `E = H + L_as − D_out` once the speaker's air-to-sound latency is calibrated in the sync wizard, a lock state and a confidence, in status, API, UI, logs and diagnostics. Nothing changes playback. Separately, the bridge warns when the host suspends the Bluetooth sink in gaps.

**Architecture:** The parent's HCI monitor (today AVRCP only) moves to a reader thread with kernel timestamps and drop counts and feeds the existing `A2dpAirParser`; a per-link tracker turns SBC scale factors into an air envelope with send-anchored air times and ships it in small batches to that speaker's daemon (`air_envelope`). The daemon taps the PCM the playout engine writes into a written envelope with `s(x)` per cell, correlates both (normalised cross-correlation, peak/side-lobe gate, parabolic sub-frame) and runs a lock state machine; the result is the status key `air_sensor`. The wizard calibration folds the click track on both sides (air in the parent, schedule in the daemon) and subtracts the result from the acoustic lag against the reference member.

**Tech Stack:** Python 3.13/3.14 stdlib only (`array`, `operator`, `ctypes`/`socket.recvmsg`), the existing `a2dp_air` parser, PyGObject/GStreamer (only through the existing `PlayoutPlayer`), `pulsectl_asyncio`, FastAPI + Pydantic v2, pytest (+ xdist), Vue 3 + TS + vitest; libsbc 1.3.1 (fixture builder only, never at runtime).

**Spec:** [`docs/plans/2026-10-28-air-sensor-design.md`](2026-10-28-air-sensor-design.md). Every task's requirements include the spec; rulings are cited as R-n.

## Global Constraints

Copied from the spec; every task's requirements include them.

* Times: `CLOCK_MONOTONIC_RAW` µs everywhere in the sensor. Kernel monitor stamps are `CLOCK_REALTIME`; convert with a bracketed `real − raw` offset taken every 250 ms (bracket ≤ 50 µs, 3 tries), interpolated; an offset step > 5 ms = clock step = new epochs, records in between dropped. Without `SO_TIMESTAMPNS` stamp with RAW at `recvmsg` return and flag `timestamp: "recv"` (R-3).
* Definitions: `s(x)` = `ScheduleMap.at(x)` (port time, output delay already subtracted); `D_out` = the daemon's current output delay; `T = s + D_out`; `H = t_air − s + Δ_enc`, `Δ_enc = 4.5·M/rate − 1.5/f_out`; `E = H + L_as − D_out` (positive = late; null without calibration).
* Air side: SBC only; band = sub-bands `b ≥ 1` with centre `(b+½)·rate/(2M)` in 2.5–8.5 kHz, else `{1}`; energy `round(8·log2(1 + Σ_ch Σ_b 2^sf))` clipped to 0…254, 255 = missing; send anchor = running median of the last 64 `t_send − u` of the epoch (R-5: canonical; completion lag is telemetry); epochs on SSRC change, RTP step disagreeing with the frames by > 1 frame, > 1 s without media, link reset, clock step; batches ≤ 128 frames every 250 ms; ≤ 400 frames/s per device; queue 16 batches per device, drop-oldest; CRC on the first frame of each packet only on the live path (R-4); CPU guard 5 % over 30 s → `unavailable: cpu_budget`.
* IPC: additive commands `air_envelope`, `air_source`, `set_air_calibration`, `air_fold`, payload `v: 1`; no `IPC_PROTOCOL_VERSION` bump; the parent sends air commands only to a daemon whose status advertised `air_sensor.accepts` containing 1 (R-6). Validation per the spec table; invalid messages are dropped and counted, one rate-limited WARNING per 10 min, never an error envelope per message.
* Written side: cells of `T_c = 128 / f_out` in output stream time; envelope `log2(1 + max|x[n] − x[n−3]|)` over both channels (R-7); taken before volume/mute (R-8); ring 20 s; silence cells `w = 0`, excluded.
* Correlation: window 3 s, hop 1 s; valid when ≥ 90 % air cells present and ≥ 80 % written cells program (R-16); flatness `σ_a ≥ 2`, `σ_w ≥ 0.25`; NCC per lag; acquisition ±250 ms around the prior `δ₀ = 10 ms`, tracking ±20 cells, wide search (±86 cells) for one window after underrun/pa_step/stall; PSR = peak / max outside ±2 cells; reject edge peaks; confirm when `PSR ≥ 1.17` and `ρ ≥ 0.25` (R-9, code constants); parabolic `λ` clamped ±0.5; continuity: a move > 3 cells needs a second confirmed window within 1 cell (R-15).
* Lock: `locked` ≤ 5 s since the last confirmed window, `holding` 5–20 s, `no_lock` after 20 s (R-10); `acquiring` on a new epoch or output reopen; confidence `clip((PSR_med − 1.17)/0.23, 0, 1) · f_ok · decay` over 10 s (R-11); jitter = 1.4826·MAD of `H` over 30 s; reported `H` = median of the last 5 confirmed windows.
* Effective rule: on iff `EXPERIMENTAL_AIR_SENSOR` and effective playout mode `measure` and Bluetooth backend; reasons `switch_off`, `needs_measure`, `loop_not_supported_yet`, `not_bluetooth`.
* Calibration: per player `air_to_sound_latency` entries keyed by (codec, adapter MAC), ≤ 8, `latency_ms` 0…2000 (R-12); needs a reference member (R-14); `L_as = A − H_cal`, `A = unwrap_lag_ms(φ_ac(speaker), φ_ac(ref))`, `H_cal = unwrap(φ_air − φ_T)` into ±5 s.
* The engine is never changed by the sensor in stage A: no write from the sensor into the engine; tap and correlator exceptions disable the sensor for that stream (`unavailable: internal_error`) and never reach the feeder.
* The `sendspin_sync` core is not changed (its boundary test stays green); the wizard integration lives in `application/sync_tuning.py`.
* The host's audio settings are never changed (suspend check only reads).
* Config keys go into `DEFAULT_CONFIG`, `config/schema.json`, `BridgeConfig` in `application/models/config.py`, `_ALLOWED_POST_CONFIG_KEYS` (+ POST normalisation) in `application/config.py`, the `web_ui_only_keys` tuple in `scripts/translate_ha_config.py` and `services/infrastructure/config_diff.py`; per-player fields in `config/players.py` and the player definition of the schema.
* Logs: INFO on state changes (≤ 1 per minute per kind) and calibration applied; WARNING once per 10 min per kind; DEBUG 10 s summary (per window with `SENDSPIN_AIR_DEBUG=1`). Never log raw monitor records.
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (R5–R9). TDD: red before green; never edit an existing test to make it pass (`CLAUDE.md`).
* Do not touch the bridge on port 8080 or Music Assistant on 8095/8097 while developing; live work happens only in Task A3 Step 6 (passive monitor read on a dev machine) and Task A17 (the stand).

## Review Focus

1. **Time bases.** REALTIME → RAW conversion, the send anchor, `Δ_enc`, `T = s + D_out`: a sign or unit slip moves `H` by milliseconds without failing a shape test. Check that the simulation test (A10) asserts `H` against an independently built truth and that the anchor test covers slew. (Tasks A3, A4, A8, A10)
2. **Untrusted input and bounds.** Every field of every air message is validated in the daemon; the hub and tracker bound rates and memory; nothing raw is logged. (Tasks A3, A4, A5, A6)
3. **No effect on playback.** The tap must not allocate or block in the feeder beyond one block's cells, exceptions are contained, and `EXPERIMENTAL_AIR_SENSOR=false` leaves daemon and parent byte-for-byte as before (no tap, no hub subscription, no new status key). (Tasks A7, A11)
4. **AVRCP unchanged.** The reader-thread refactor keeps every existing `test_hci_avrcp_monitor.py` test green and adds the `(index, handle)` fix with its own test. (Task A3)
5. **Calibration provenance.** A calibration is never offered without a reference member and a locked SBC air sensor during the solo; the stored entry carries codec, adapter, reference and time. (Tasks A15, A16)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/sink_suspend_policy.py` | **new** static probe + observation rule (A1) |
| `src/sendspin_bridge/services/audio/sink_monitor.py` | `add_state_listener` (A1) |
| `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py` | **new** reader thread, cmsg, clock map, consumers (A3) |
| `src/sendspin_bridge/services/bluetooth/hci_socket.py` | `parse_monitor_cmsgs`, socket options (A3) |
| `src/sendspin_bridge/services/bluetooth/hci_avrcp_monitor.py` | becomes a hub consumer; `(index, handle)` map (A3) |
| `src/sendspin_bridge/services/bluetooth/a2dp_air_envelope.py` | **new** band, energy, RTP epochs, anchor, `AirLinkTracker`, `AirBatch` (A4) |
| `src/sendspin_bridge/services/bluetooth/a2dp_air_fold.py` | **new** fold helpers moved from the phase script (A16) |
| `src/sendspin_bridge/services/bluetooth/air_sensor_service.py` | **new** parent wiring (A6, A16) |
| `src/sendspin_bridge/services/ipc/commands.py` | `AirEnvelope`, `AirSource`, `SetAirCalibration`, `AirFold` (A5) |
| `src/sendspin_bridge/services/ipc/daemon_process.py`, `bridge_daemon.py` | dispatch, params, status (A5, A11) |
| `src/sendspin_bridge/services/audio/playout/air_tap.py` | **new** `WrittenEnvelope` (A7) |
| `src/sendspin_bridge/services/audio/playout/air_correlator.py` | **new** NCC, gate, refinement (A8) |
| `src/sendspin_bridge/services/audio/playout/air_sensor.py` | **new** `AirStream`, `LockMachine`, `AirSensor` (A9) |
| `src/sendspin_bridge/services/audio/playout/gst_output.py` | tap hook, engine events (A7, A11) |
| `src/sendspin_bridge/bridge/client.py`, `bridge/orchestrator.py` | status key, spawn params, hub start (A6, A11, A12) |
| `src/sendspin_bridge/config/__init__.py`, `config/schema.json`, `config/players.py`, `application/models/config.py`, `application/config.py`, `scripts/translate_ha_config.py`, `services/infrastructure/config_diff.py` | keys (A2, A15) |
| `src/sendspin_bridge/application/models/devices.py`, `application/devices.py`, `api/routers/devices.py` | `AirSensorTiming`, calibration API (A12, A15) |
| `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py`, `recovery_assistant.py`, `operator_guidance.py` | issues (A1, A13) |
| `src/sendspin_bridge/application/sync_tuning.py`, `application/models/sync.py`, `api/routers/sync.py`, `docs/contracts/sync-contract-v1.json` | wizard calibration (A16) |
| `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/utils/deviceChips.ts`, `ui/src/settings/layout.ts`, `ui/src/components/sync/StepTune.vue`, `ui/src/i18n/{en,ru}.json`, `ui/src/api/*` | UI (A14, A15, A16) |
| `scripts/a2dp_air_phase.py` | imports the fold helpers (A16) |
| `scripts/air_sensor_bench.py` | **new** CPU bench on the fixtures (A4) |
| `tests/fixtures/a2dp_air/build_air_music_fixture.py`, `tests/fixtures/a2dp_air/air_music.btsnoop.gz` | **new** synthetic-music capture (A10) |
| `tests/unit/services/audio/test_sink_suspend_policy.py`, `tests/unit/config/test_air_sensor_config.py`, `tests/unit/services/bluetooth/test_hci_monitor_hub.py`, `tests/unit/services/bluetooth/test_a2dp_air_envelope.py`, `tests/unit/services/ipc/test_air_commands.py`, `tests/unit/services/bluetooth/test_air_sensor_service.py`, `tests/unit/services/audio/playout/test_air_tap.py`, `tests/unit/services/audio/playout/test_air_correlator.py`, `tests/unit/services/audio/playout/test_air_sensor.py`, `tests/unit/services/audio/playout/test_air_sim.py`, `tests/unit/services/ipc/test_bridge_daemon_air.py`, `tests/unit/api/test_air_sensor_api.py`, `tests/unit/services/diagnostics/test_air_guidance.py`, `tests/unit/api/test_sync_air_calibration.py` | **new** tests |
| `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}group-sync-tuning.md`, `{,ru/}configuration.md`, `CHANGELOG.md`, the spec (decision note) | docs (A1, A17) |

Conventions: Python tests under `tests/unit/...`; parent fixtures from `tests/conftest.py` (`api_client`, `tmp_config`, `bridge_loop`); GStreamer tests use the `requires_gst` pattern of `tests/unit/services/audio/playout/test_gst_output.py`. Use cases raise `UseCaseError(status, code, detail)`; routers use `Depends(require_principal)` and `PROBLEM_RESPONSES`. Run one file with `uv run pytest <path> -q -p no:cacheprovider -n0`. Each task below is shippable on its own: with the switch off nothing user-visible changes until A12, and every task's tests run without hardware.

Task order and dependencies: A1 (independent) · A2 · A3 → A4 → A5 → A6 · A7 → A8 → A9 → A10 (needs A4, A7–A9) → A11 (needs A5, A9) → A12 → A13 → A14 · A15 (needs A12) → A16 (needs A6, A11, A15) → A17.

---

## Stage A — Telemetry only

### Task A1: Sink-suspend-in-gaps check and guidance (replaces Stage 2)

**Files:**
- Create: `src/sendspin_bridge/services/audio/sink_suspend_policy.py`
- Modify: `src/sendspin_bridge/services/audio/sink_monitor.py` (`add_state_listener`, called from `_dispatch_transition` and `_observe_state` for every state change, including `idle → suspended`), `src/sendspin_bridge/bridge/client.py` (listener per client; `DeviceStatus.host_sink_suspends: dict | None`), `src/sendspin_bridge/bridge/orchestrator.py` (start the probe task next to `sink_monitor.start()`), `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (`bt_sink_suspend_in_gaps`), `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (`_build_sink_suspend_issues`), `ui/src/i18n/{en,ru}.json` (issue texts), `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md` (how to disable on WirePlumber 0.4/0.5 and PulseAudio)
- Test: `tests/unit/services/audio/test_sink_suspend_policy.py`, `tests/unit/services/diagnostics/test_air_guidance.py` (suspend part)

**Interfaces:**

```python
# sink_suspend_policy.py
@dataclass(frozen=True)
class SuspendPolicy:
    server: Literal["pulseaudio", "pipewire", "unknown"]
    suspends: bool | None            # None = unknown
    timeout_s: float | None
    source: Literal["module-suspend-on-idle", "wireplumber", "none", "unknown"]

def parse_pactl_info(text: str) -> Literal["pulseaudio", "pipewire", "unknown"]: ...
def parse_pa_modules(text: str) -> SuspendPolicy: ...          # `pactl list modules short`
def parse_pw_dump(nodes: list[dict]) -> SuspendPolicy: ...     # bluez_output.* session.suspend-timeout-seconds
async def probe_suspend_policy(*, run=...) -> SuspendPolicy: ...  # run(argv) -> (rc, stdout); never raises

class SuspendObserver:
    """Per device: host suspends observed while the bridge did not ask for one."""
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, clear_after_s: float = 86_400.0) -> None: ...
    def on_state(self, old: str | None, new: str, *, bridge_requested: bool, connected: bool) -> None: ...
    def snapshot(self, policy: SuspendPolicy | None) -> dict[str, object] | None
        # {"observed": int, "last_at": str|None, "last_idle_s": float|None,
        #  "policy": {...}, "warn": bool, "reason": "observed_suspend"|"module_suspend_on_idle"|"wireplumber_suspend_timeout"|None}

# sink_monitor.py
def add_state_listener(self, listener: Callable[[str, str | None, str, str], None]) -> None:
    """listener(mac, old_state, new_state, sink_name) for every observed state change of a registered sink."""
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/test_sink_suspend_policy.py
import pytest
from sendspin_bridge.services.audio.sink_suspend_policy import (
    SuspendObserver, SuspendPolicy, parse_pa_modules, parse_pactl_info, parse_pw_dump, probe_suspend_policy,
)

def test_pactl_info_tells_pipewire_from_pulseaudio():
    assert parse_pactl_info("Server Name: PulseAudio (on PipeWire 1.0.5)\n") == "pipewire"
    assert parse_pactl_info("Server Name: pulseaudio\nServer Version: 17.0\n") == "pulseaudio"
    assert parse_pactl_info("garbage") == "unknown"

def test_module_suspend_on_idle_with_timeout():
    p = parse_pa_modules("7\tmodule-suspend-on-idle\ttimeout=3\t\n8\tmodule-bluez5-discover\t\t\n")
    assert p == SuspendPolicy(server="pulseaudio", suspends=True, timeout_s=3.0, source="module-suspend-on-idle")

def test_module_suspend_on_idle_default_timeout_is_5s():
    assert parse_pa_modules("7\tmodule-suspend-on-idle\t\t\n").timeout_s == 5.0

def test_haos_has_no_suspend_module():
    p = parse_pa_modules("8\tmodule-bluez5-discover\t\t\n")
    assert p.suspends is False and p.source == "none"

def test_wireplumber_timeout_on_bluez_nodes():
    nodes = [{"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "bluez_output.FC_58.1",
              "session.suspend-timeout-seconds": 0}}}]
    assert parse_pw_dump(nodes).suspends is False
    nodes[0]["info"]["props"]["session.suspend-timeout-seconds"] = 5
    p = parse_pw_dump(nodes)
    assert (p.suspends, p.timeout_s, p.source) == (True, 5.0, "wireplumber")

@pytest.mark.asyncio
async def test_probe_never_raises():
    async def boom(argv): raise OSError("no pactl")
    p = await probe_suspend_policy(run=boom)
    assert p.server == "unknown" and p.suspends is None

def test_observer_counts_only_host_suspends():
    t = [1000.0]
    obs = SuspendObserver(clock=lambda: t[0])
    obs.on_state("running", "idle", bridge_requested=False, connected=True)
    t[0] += 5.2
    obs.on_state("idle", "suspended", bridge_requested=False, connected=True)
    obs.on_state("running", "suspended", bridge_requested=True, connected=True)   # power_save: ours
    obs.on_state("running", "suspended", bridge_requested=False, connected=False) # disconnecting
    snap = obs.snapshot(None)
    assert snap["observed"] == 1 and snap["last_idle_s"] == pytest.approx(5.2) and snap["warn"] is True
    assert snap["reason"] == "observed_suspend"

def test_static_policy_below_60s_warns_without_observation():
    obs = SuspendObserver()
    snap = obs.snapshot(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert snap["warn"] is True and snap["reason"] == "wireplumber_suspend_timeout"

def test_warning_clears_after_24h_clean():
    t = [0.0]
    obs = SuspendObserver(clock=lambda: t[0])
    obs.on_state("idle", "suspended", bridge_requested=False, connected=True)
    t[0] += 86_401
    assert obs.snapshot(SuspendPolicy("pulseaudio", False, None, "none"))["warn"] is False
```

```python
# tests/unit/services/diagnostics/test_air_guidance.py (suspend part)
from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition

def test_suspend_issue_is_registered_as_audio_warning():
    d = issue_definition("bt_sink_suspend_in_gaps")
    assert d is not None and d.layer == "audio" and d.severity == "warning"
    assert set(d.default_reason_codes) >= {"observed_suspend", "module_suspend_on_idle", "wireplumber_suspend_timeout"}

def test_recovery_assistant_reports_a_device_with_observed_suspends(make_device):
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues
    dev = make_device(host_sink_suspends={"observed": 2, "warn": True, "reason": "observed_suspend", "policy": None})
    keys = [i.key for i in _build_device_issues([dev])]
    assert "bt_sink_suspend_in_gaps" in keys
```

(`make_device` is the device factory used in `tests/unit/services/diagnostics/test_recovery_assistant.py`; reuse it or copy its minimal form into a local fixture.)

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/test_sink_suspend_policy.py tests/unit/services/diagnostics/test_air_guidance.py -q -p no:cacheprovider -n0` → FAIL.
- [ ] **Step 3: Implement** the parsers and `probe_suspend_policy` (`pactl info`, then `pactl list modules short` or `pw-dump` with a 3 s timeout each, all through `run`); `SuspendObserver` with the rules above; `SinkMonitor.add_state_listener` (listeners called after the existing callbacks, exceptions logged at DEBUG and swallowed); in `SendspinClient`, a listener that passes `bridge_requested = status.bt_power_save or status.bt_standby or self._suspend_requested_at is recent (< 2 s)` and `connected = status.bluetooth_connected`, and publishes `host_sink_suspends` (add to `DeviceStatus` and `_ALLOWED_STATUS_KEYS`); the orchestrator runs the probe at start and every 10 min and hands the policy to every client. Registry entry (priority 34, after `pa_socket_*`) and a producer in `recovery_assistant.py` with a remediation pointing to the docs page; i18n texts (en, ru) that say the bridge never changes host settings. Docs section "Bluetooth sink suspend" with the WirePlumber 0.4 (`bluez_monitor.rules`, `["session.suspend-timeout-seconds"] = 0`) and 0.5 (`monitor.bluez.rules`, `session.suspend-timeout-seconds = 0`) snippets and the PulseAudio `unload-module module-suspend-on-idle` note.
- [ ] **Step 4: Run** both files plus `tests/unit/services/diagnostics tests/unit/services/audio/test_sink_monitor*.py -q -p no:cacheprovider` → PASS (`test_issue_registry_keys.py` checks every registered key has i18n texts).
- [ ] **Step 5: Changelog** `### Added` — "Diagnostics warn when the host suspends a Bluetooth speaker's audio sink between songs (PipeWire's suspend timeout, PulseAudio's suspend-on-idle); speakers can shift by tens of ms after such a pause. The bridge never changes the host setting."
- [ ] **Step 6: Commit** `feat(diagnostics): warn when the host suspends the Bluetooth sink in gaps`.

### Task A2: `EXPERIMENTAL_AIR_SENSOR` config key and the effective rule

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`DEFAULT_CONFIG`, `air_sensor_effective`), `src/sendspin_bridge/config/schema.json`, `src/sendspin_bridge/application/models/config.py` (`BridgeConfig`), `src/sendspin_bridge/application/config.py` (`_ALLOWED_POST_CONFIG_KEYS`, bool normalisation), `scripts/translate_ha_config.py` (`web_ui_only_keys`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (`_GLOBAL_RESTART_FIELDS`)
- Test: `tests/unit/config/test_air_sensor_config.py`, `tests/integration/test_translate_ha_config.py` (one new test)

**Interfaces:**

```python
# config/__init__.py
def air_sensor_effective(player_cfg: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[bool, str | None]:
    """(True, None) when EXPERIMENTAL_AIR_SENSOR is on, the player's effective playout mode is
    'measure' and its backend is Bluetooth; else (False, reason) with reason in
    {'switch_off', 'needs_measure', 'loop_not_supported_yet', 'not_bluetooth'}."""
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/config/test_air_sensor_config.py
import json
from pathlib import Path

import pytest

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG, air_sensor_effective

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"
BT = {"backend": {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF"}}


def test_defaults_off_and_allowed():
    assert DEFAULT_CONFIG["EXPERIMENTAL_AIR_SENSOR"] is False
    assert "EXPERIMENTAL_AIR_SENSOR" in CONFIG_ALLOWED_KEYS


def test_schema_declares_a_boolean():
    prop = json.loads(SCHEMA.read_text())["properties"]["EXPERIMENTAL_AIR_SENSOR"]
    assert prop["type"] == "boolean" and prop["default"] is False


@pytest.mark.parametrize(
    ("cfg", "player", "expected"),
    [
        ({"EXPERIMENTAL_AIR_SENSOR": False, "PLAYOUT_SYNC": "measure"}, BT, (False, "switch_off")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "off"}, BT, (False, "needs_measure")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, BT, (False, "loop_not_supported_yet")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"}, {**BT, "playout_sync_enabled": False}, (False, "needs_measure")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"}, {"backend": {"type": "pulse_sink", "sink": "x"}}, (False, "not_bluetooth")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"}, BT, (True, None)),
    ],
)
def test_effective_rule(cfg, player, expected):
    assert air_sensor_effective(player, cfg) == expected


def test_change_restarts_daemons():
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
    actions = diff_configs({"EXPERIMENTAL_AIR_SENSOR": False}, {"EXPERIMENTAL_AIR_SENSOR": True})
    assert [a.kind for a in actions] == [ActionKind.GLOBAL_RESTART]


def test_post_config_accepts_and_validates():
    from pydantic import ValidationError
    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS
    from sendspin_bridge.application.models.config import BridgeConfig
    assert "EXPERIMENTAL_AIR_SENSOR" in _ALLOWED_POST_CONFIG_KEYS
    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"EXPERIMENTAL_AIR_SENSOR": "maybe"})
```

```python
# tests/integration/test_translate_ha_config.py (new test, same pattern as the PLAYOUT_SYNC one)
def test_air_sensor_switch_preserved_across_addon_restart(tmp_path):
    _write_json(tmp_path / "options.json", _minimal_options())
    _write_json(tmp_path / "config.json", {"EXPERIMENTAL_AIR_SENSOR": True})
    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()
    assert _read_json(tmp_path / "config.json")["EXPERIMENTAL_AIR_SENSOR"] is True
```

- [ ] **Step 2: Run** the two files → FAIL.
- [ ] **Step 3: Implement** — `"EXPERIMENTAL_AIR_SENSOR": False` next to `PLAYOUT_SYNC` with a comment pointing at the spec; schema `{"type": "boolean", "default": false, "description": "Experimental: measure when audio leaves over the air (SBC, needs PLAYOUT_SYNC=measure and CAP_NET_RAW). Does not change playback."}`; `BridgeConfig.EXPERIMENTAL_AIR_SENSOR: StrictBool | None = False` with the docstring; POST key + bool normalisation; `_GLOBAL_RESTART_FIELDS`; `web_ui_only_keys`; `air_sensor_effective` using `effective_playout_mode`.
- [ ] **Step 4: Run** the two files and `uv run pytest tests/unit/config -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(config): EXPERIMENTAL_AIR_SENSOR switch (off by default)`.

### Task A3: `HciMonitorHub` — reader thread, kernel timestamps, drops, consumers

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py`
- Modify: `src/sendspin_bridge/services/bluetooth/hci_socket.py` (`SO_TIMESTAMPNS`/`SO_RXQ_OVFL` constants, `parse_monitor_cmsgs`, `enable_monitor_sockopts`), `src/sendspin_bridge/services/bluetooth/hci_avrcp_monitor.py` (`AvrcpConsumer`; `HciAvrcpMonitor.start()` starts the hub and registers the consumer; `(index, handle)` map; seed keeps `dev_id`), `src/sendspin_bridge/bridge/orchestrator.py` (unchanged call sites: `get_monitor().start()/stop()`)
- Test: `tests/unit/services/bluetooth/test_hci_monitor_hub.py`; `tests/unit/services/bluetooth/test_hci_avrcp_monitor.py` must stay green unchanged

**Interfaces:**

```python
# hci_socket.py
SO_TIMESTAMPNS: int = 35
SCM_TIMESTAMPNS: int = SO_TIMESTAMPNS
SO_RXQ_OVFL: int = 40

def enable_monitor_sockopts(sock: socket.socket, *, rcvbuf: int = 4 << 20) -> dict[str, bool]:
    """Set SO_TIMESTAMPNS, SO_RXQ_OVFL and the receive buffer (SO_RCVBUFFORCE, else SO_RCVBUF); returns what succeeded."""
def parse_monitor_cmsgs(ancdata: list[tuple[int, int, bytes]]) -> tuple[int | None, int | None]:
    """(realtime_ns, drops) from recvmsg ancillary data; None for what is absent or malformed."""

# hci_monitor_hub.py
@dataclass(frozen=True, slots=True)
class HubRecord:
    t_raw_us: float          # RAW µs (converted kernel stamp, or recv time)
    index: int
    opcode: int
    payload: bytes
    drops: int | None        # cumulative SO_RXQ_OVFL count, None when unavailable
    timestamp: Literal["kernel", "recv"]

class RawRealtimeMap:
    """REALTIME ns → RAW µs from bracketed pairs; detects steps."""
    def __init__(self, *, raw_ns=..., real_ns=..., max_bracket_ns: int = 50_000, step_us: float = 5_000.0) -> None: ...
    def sample(self) -> bool: ...                       # take one pair; False if no bracket within 3 tries
    def to_raw_us(self, real_ns: int) -> float | None   # None until two pairs bracket it
    @property
    def epoch(self) -> int: ...                         # increments on a step

class HubConsumer(Protocol):
    wants_acl_tx: bool
    def on_record(self, rec: HubRecord) -> None: ...    # called on the hub thread; must not block
    def on_hub_state(self, state: Literal["open", "error", "closed"], reason: str | None) -> None: ...

class HciMonitorHub:
    def __init__(self, *, open_socket=..., seed=..., clock_map: RawRealtimeMap | None = None,
                 cpu_budget: float = 0.05, cpu_window_s: float = 30.0) -> None: ...
    def add_consumer(self, c: HubConsumer) -> None: ...
    def remove_consumer(self, c: HubConsumer) -> None: ...
    def start(self) -> None: ...                         # idempotent; thread "hci-monitor"
    def stop(self, timeout: float = 2.0) -> None: ...
    @property
    def available(self) -> bool: ...
    @property
    def unavailable_reason(self) -> str | None: ...      # no_cap_net_raw | unsupported_platform | monitor_error
    def adapters(self) -> dict[int, str]: ...            # index -> controller MAC (NEW_INDEX)
    def peer(self, index: int, handle: int) -> str | None: ...  # ACL peer MAC
    def counters(self) -> dict[str, int | None]: ...     # records, drops, clock_steps, cpu_pct, ...

def get_hub() -> HciMonitorHub: ...
```

- [ ] **Step 1: Failing tests** (a fake socket whose `recvmsg` returns scripted `(data, ancdata, flags, addr)` tuples; records built with `struct.pack("<HHH", opcode, index, len) + payload`):

```python
# tests/unit/services/bluetooth/test_hci_monitor_hub.py
import struct, socket, time
import pytest
from sendspin_bridge.services.bluetooth import hci_socket as hs
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HciMonitorHub, RawRealtimeMap

def frame(opcode, index, payload): return struct.pack("<HHH", opcode, index, len(payload)) + payload
def ts_cmsg(ns): return (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", ns // 10**9, ns % 10**9))
def ovfl_cmsg(n): return (socket.SOL_SOCKET, hs.SO_RXQ_OVFL, struct.pack("I", n))

def test_cmsgs_parse_timestamp_and_drops():
    assert hs.parse_monitor_cmsgs([ts_cmsg(1_700_000_000_123_456_789), ovfl_cmsg(7)]) == (1_700_000_000_123_456_789, 7)
    assert hs.parse_monitor_cmsgs([]) == (None, None)
    assert hs.parse_monitor_cmsgs([(socket.SOL_SOCKET, hs.SO_RXQ_OVFL, b"\x01")]) == (None, None)  # short

def test_realtime_to_raw_interpolates_between_pairs():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__          # ns, brackets of 10 ns
    real = iter([5_000_000_000_005, 5_001_000_000_505]).__next__        # 500 ns slew over 1 s
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    assert m.sample() and m.sample()
    # real stamp halfway: offset interpolated
    assert m.to_raw_us(5_000_500_000_255) == pytest.approx(500_000.005, abs=0.01)

def test_clock_step_starts_a_new_epoch():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_020_000_005]).__next__        # +20 ms step
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    m.sample(); m.sample()
    assert m.epoch == 1

def test_avrcp_consumer_sees_the_same_records(fake_hub_socket):
    seen = []
    class C:
        wants_acl_tx = False
        def on_record(self, rec): seen.append((rec.opcode, rec.index, rec.timestamp))
        def on_hub_state(self, state, reason): pass
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_RX_PKT, 1, b"\x46\x00\x04\x00abcd"), [])]),
                        seed=lambda m: None)
    hub.add_consumer(C()); hub.start(); hub.stop()
    assert seen == [(hs.MONITOR_ACL_RX_PKT, 1, "recv")]

def test_acl_tx_reaches_only_consumers_that_want_it(fake_hub_socket):
    got = {"a": 0, "b": 0}
    def mk(name, wants):
        class C:
            wants_acl_tx = wants
            def on_record(self, rec): got[name] += 1
            def on_hub_state(self, s, r): pass
        return C()
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_TX_PKT, 0, b"\x46\x20\x00\x00"), [])]),
                        seed=lambda m: None)
    hub.add_consumer(mk("a", False)); hub.add_consumer(mk("b", True)); hub.start(); hub.stop()
    assert got == {"a": 0, "b": 1}

def test_handles_are_per_controller(fake_hub_socket):
    def conn_complete(handle, mac_bytes):
        params = bytes([0]) + struct.pack("<H", handle) + mac_bytes + bytes([1, 0])
        return bytes([0x03, len(params)]) + params
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, conn_complete(70, bytes([1, 2, 3, 4, 5, 6]))), []),
            (frame(hs.MONITOR_EVENT_PKT, 1, conn_complete(70, bytes([6, 5, 4, 3, 2, 1]))), [])]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None)
    hub.start(); hub.stop()
    assert hub.peer(0, 70) == "06:05:04:03:02:01" and hub.peer(1, 70) == "01:02:03:04:05:06"

def test_new_index_gives_the_adapter_address(fake_hub_socket):
    payload = bytes([0, 1]) + bytes([0xD6, 0xD7, 0x62, 0xF9, 0xFB, 0xC0]) + b"hci1\0\0\0\0"
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_NEW_INDEX, 1, payload), [])]), seed=lambda m: None)
    hub.start(); hub.stop()
    assert hub.adapters() == {1: "C0:FB:F9:62:D7:D6"}

def test_permission_error_is_unavailable_with_reason():
    def deny(): raise PermissionError(1, "Operation not permitted")
    hub = HciMonitorHub(open_socket=deny, seed=lambda m: None)
    hub.start(); hub.stop()
    assert not hub.available and hub.unavailable_reason == "no_cap_net_raw"

def test_kernel_timestamp_is_used_when_present(fake_hub_socket, monkeypatch):
    m = RawRealtimeMap(raw_ns=lambda: 0, real_ns=lambda: 0)
    monkeypatch.setattr(m, "to_raw_us", lambda ns: ns / 1000.0 - 42.0)
    seen = []
    class C:
        wants_acl_tx = False
        def on_record(self, rec): seen.append((rec.t_raw_us, rec.timestamp, rec.drops))
        def on_hub_state(self, s, r): pass
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [ts_cmsg(1_000_000), ovfl_cmsg(3)])]),
                        seed=lambda mp: None, clock_map=m)
    hub.add_consumer(C()); hub.start(); hub.stop()
    assert seen == [(958.0, "kernel", 3)]
```

(`fake_hub_socket` is a fixture in the test file: it returns a factory whose socket yields the scripted reads from `recvmsg` and then blocks on `timeout` until closed, and records `setsockopt` calls.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the hub: thread loop `recvmsg(4096, 256)` with a 0.5 s socket timeout; `parse_monitor_packet`; `parse_monitor_cmsgs`; clock map sampled every 250 ms on the same thread (records stamped `kernel` wait in a ≤ 250 ms deque until a later pair brackets them; R-3 fallback stamps `recv`); index map from `NEW_INDEX`/`DEL_INDEX`; `(index, handle)` → peer from Connection/Disconnection Complete and the kernel seed (keep `dev_id`); fan-out to consumers (ACL TX only to `wants_acl_tx`); per-consumer exceptions logged once per 10 min and swallowed; CPU accounting with `time.thread_time()` per 30 s window → `cpu_pct` (the guard only reports here; A6 acts on it); open errors: `PermissionError`/`EPERM`/`EACCES` → `no_cap_net_raw`, non-Linux → `unsupported_platform`, else `monitor_error` with the existing backoff (5 → 60 s) on the thread. `HciAvrcpMonitor` becomes a thin wrapper: `start()` registers `AvrcpConsumer` (the old `_process_packet` logic, map keyed by `(index, handle)`) and starts the hub; `stop()` removes it and stops the hub when no consumer is left. Keep `_process_packet`, `_dispatch_acl_packet`, `_parse_*` importable with their signatures (existing tests patch them), adding an optional `index` argument with default 0.
- [ ] **Step 4: Run** `tests/unit/services/bluetooth/test_hci_monitor_hub.py tests/unit/services/bluetooth/test_hci_avrcp_monitor.py tests/unit/services/bluetooth/test_hci_socket.py` → PASS.
- [ ] **Step 5: Verify on a dev machine (passive, no audio, no pairing).** With `sudo` (or `setcap cap_net_raw+ep` on a venv python) run `uv run python -c "from sendspin_bridge.services.bluetooth.hci_monitor_hub import get_hub; import time; h=get_hub(); h.start(); time.sleep(10); print(h.counters(), h.adapters()); h.stop()"` while the machine's own Bluetooth is idle; record whether records arrive stamped `kernel` and whether `drops` is an int (SO_RXQ_OVFL delivered) or None. Append the answer to open question 1 in the spec in one line.
- [ ] **Step 6: Commit** `refactor(bluetooth): HCI monitor hub on a reader thread with kernel timestamps and drop counts`.

### Task A4: Air envelope — band, energy, epochs, send anchor, `AirLinkTracker`

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/a2dp_air_envelope.py`, `scripts/air_sensor_bench.py`
- Test: `tests/unit/services/bluetooth/test_a2dp_air_envelope.py`

**Interfaces:**

```python
MISSING = 255
MAX_BATCH_FRAMES = 128
ANCHOR_PACKETS = 64

def envelope_subbands(rate: int, subbands: int) -> tuple[int, ...]: ...
def frame_envelope(frame: SbcFrame, bands: tuple[int, ...]) -> int: ...          # 0..254

class SendAnchor:
    """Running median of t_send - u over the last ANCHOR_PACKETS packets of an epoch (µs)."""
    def add(self, t_send_us: float, u_us: float) -> None: ...
    def value(self) -> float | None: ...
    def reset(self) -> None: ...

@dataclass(frozen=True, slots=True)
class AirBatch:
    epoch: int
    seq: int                       # first frame's index in the epoch
    t0_us: float                   # RAW air time of the first frame (send-anchored)
    frame_us: float
    rate: int
    subbands: int
    blocks: int
    bands: tuple[int, ...]
    complete_lag_us: float | None
    timestamp: str                 # kernel | recv
    e: tuple[int, ...]             # 255 = missing

@dataclass(frozen=True, slots=True)
class TrackerState:
    state: Literal["streaming", "no_traffic", "unsupported_codec"]
    reason: str | None
    codec: str | None
    epoch: int
    frames: int
    missing_frames: int
    in_step: bool

class AirLinkTracker:
    def __init__(self, *, clock_us: Callable[[], float], codec_hint: Callable[[], str | None] = lambda: None,
                 max_frames_per_s: int = 400) -> None: ...
    def on_packet(self, p: MediaPacket, *, t_send_us: float, t_complete_us: float | None,
                  timestamp: str, in_step: bool) -> None: ...
    def on_link_reset(self) -> None: ...
    def on_clock_step(self) -> None: ...
    def take_batches(self, now_us: float, *, force: bool = False) -> list[AirBatch]: ...   # due every 250 ms
    def state(self, now_us: float) -> TrackerState: ...
```

`MediaPacket.t_send`/`t_complete` stay unix seconds (parser unchanged); the consumer (A6) passes the RAW conversions explicitly, so the tracker never sees wall time.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/bluetooth/test_a2dp_air_envelope.py
import gzip, statistics
from dataclasses import replace
from pathlib import Path
import pytest
from sendspin_bridge.services.bluetooth.a2dp_air import (
    A2dpAirParser, ChannelMode, MediaPacket, SbcFrame, parse_records, read_btsnoop,
)
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import (
    MISSING, AirLinkTracker, SendAnchor, envelope_subbands, frame_envelope,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air"

@pytest.mark.parametrize(("rate", "m", "bands"), [(44100, 8, (1, 2)), (48000, 8, (1, 2)), (44100, 4, (1,)), (48000, 4, (1,)), (32000, 8, (1, 2, 3))])
def test_band_selection(rate, m, bands):
    assert envelope_subbands(rate, m) == bands

def sbc(sf_left, sf_right=None, m=8):
    rows = (tuple(sf_left), tuple(sf_right or sf_left))
    return SbcFrame(44100, 16, m, ChannelMode.JOINT_STEREO, False, 53, 119, (False,) * m, rows)

def test_frame_envelope_is_log_peak_in_eighth_bits():
    f = sbc([0, 4, 4, 0, 0, 0, 0, 0])                    # 2 ch x (2^4 + 2^4) = 64 -> log2(65) * 8 = 48.2
    assert frame_envelope(f, (1, 2)) == 48
    assert frame_envelope(sbc([15] * 8), (1, 2)) <= 254

def test_send_anchor_is_a_median_robust_to_credit_jitter():
    a = SendAnchor()
    for k in range(64):
        jitter = 9000.0 if k % 5 == 0 else 0.0           # every 5th packet waits for credits
        a.add(1_000_000.0 + k * 20_000 + jitter, k * 20_000.0)
    assert a.value() == pytest.approx(1_000_000.0)

def test_send_anchor_follows_slew_with_small_lag():
    a = SendAnchor()
    for k in range(400):                                 # 500 ppm: t_send - u grows 10 us per packet
        a.add(k * 20_000 * 1.0005, k * 20_000.0)
    assert a.value() == pytest.approx(399 * 20_000 * 0.0005, abs=330.0)

def packets(n, frames=7, start_ts=0, ssrc=1, t0=10.0, rate=44100):
    out = []
    for k in range(n):
        fr = tuple(sbc([0, 3 + (k % 4), 2, 0, 0, 0, 0, 0]) for _ in range(frames))
        out.append(MediaPacket(t_send=t0 + k * frames * 128 / rate, t_complete=None, index=1, handle=70, cid=0x41,
                               seq=k & 0xFFFF, rtp_ts=(start_ts + k * frames * 128) & 0xFFFFFFFF, ssrc=ssrc,
                               payload_type=96, codec="sbc", frames=fr))
    return out

def feed(tr, pk):
    for p in pk:
        tr.on_packet(p, t_send_us=p.t_send * 1e6, t_complete_us=None, timestamp="kernel", in_step=False)

def test_batches_are_contiguous_and_bounded():
    tr = AirLinkTracker(clock_us=lambda: 0.0)
    feed(tr, packets(60))
    batches = tr.take_batches(1e12, force=True)
    assert all(len(b.e) <= 128 for b in batches)
    assert [b.seq for b in batches] == [i * 128 for i in range(len(batches))]
    assert sum(len(b.e) for b in batches) == 420
    assert batches[1].t0_us == pytest.approx(batches[0].t0_us + 128 * batches[0].frame_us, abs=1.0)

def test_missing_sequence_numbers_become_missing_frames():
    tr = AirLinkTracker(clock_us=lambda: 0.0)
    pk = packets(10)
    feed(tr, pk[:4] + pk[5:])
    e = [v for b in tr.take_batches(1e12, force=True) for v in b.e]
    assert e[28:35] == [MISSING] * 7 and MISSING not in e[:28]

def test_rtp_restart_starts_a_new_epoch():
    tr = AirLinkTracker(clock_us=lambda: 0.0)
    feed(tr, packets(10))
    feed(tr, packets(10, start_ts=0, t0=20.0))          # AVDTP suspend/start: RTP from 0 again
    epochs = {b.epoch for b in tr.take_batches(1e12, force=True)}
    assert epochs == {0, 1}

def test_non_sbc_is_unsupported_after_2s():
    t = [0.0]
    tr = AirLinkTracker(clock_us=lambda: t[0], codec_hint=lambda: "aac")
    for k in range(150):
        p = replace(packets(1)[0], codec="unknown", frames=(), t_send=10.0 + k * 0.02)
        t[0] = p.t_send * 1e6
        tr.on_packet(p, t_send_us=t[0], t_complete_us=None, timestamp="kernel", in_step=False)
    s = tr.state(t[0])
    assert s.state == "unsupported_codec" and s.reason == "codec:aac"

def test_fixture_envelope_matches_the_phase_tool_fold():
    """Real capture: folding the tracker's cells over the click cycle reproduces the phase tool."""
    from scripts.a2dp_air_phase import analyze, click_phase_ms, sbc_encoder_delay_samples
    stats_reports, _ = analyze(FIX / "eneby_clicks.btsnoop.gz")
    pk = [p for p in parse_records(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"), A2dpAirParser()) if p.handle == 70]
    tr = AirLinkTracker(clock_us=lambda: 0.0)
    feed(tr, pk)
    rows = [(b.t0_us / 1e6 + j * b.frame_us / 1e6, float(2 ** (v / 8))) for b in tr.take_batches(1e18, force=True)
            for j, v in enumerate(b.e) if v != MISSING]
    phase = click_phase_ms(rows, 128 / 44100, 0.0, sbc_encoder_delay_samples(8) / 44100)
    assert phase == pytest.approx(stats_reports[0].phase_send_ms, abs=0.5)
```

(The last test compares the median-anchored tracker with the phase tool's whole-capture median anchor; on a 6 s capture both medians cover the same packets, so 0.5 ms is the quantisation of the energy plus the anchor window.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the spec "AirLinkTracker" (RTP unwrap per link, epochs, sequence gaps → missing frames, anchor via `statistics.median` on a `deque(maxlen=64)`, completion-lag median over 2 s, batch cutting, frames-per-second guard, 2 s non-SBC rule). Add `scripts/air_sensor_bench.py`: parse the fixture 50 times through parser + tracker and print µs per record and the projected % of one core at 150 records/s (no assertion; used in A17).
- [ ] **Step 4: Run** → PASS; run `uv run python scripts/air_sensor_bench.py` and note the number in the commit body.
- [ ] **Step 5: Commit** `feat(bluetooth): SBC air envelope with send-anchored air times per link`.

### Task A5: Air IPC commands (encode, decode, validate) and daemon intake

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/commands.py` (`AirEnvelope`, `AirSource`, `SetAirCalibration`, `AirFold`, `AIR_ENVELOPE_VERSION = 1`), `src/sendspin_bridge/services/ipc/daemon_process.py` (dispatch to `daemon.air_intake(...)`; a daemon without an air sensor counts and ignores), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`air_intake` stub storing counters; `accepts` advertised only when the spawn params enable the sensor)
- Test: `tests/unit/services/ipc/test_air_commands.py`

**Interfaces:**

```python
AIR_ENVELOPE_VERSION = 1

@dataclass(frozen=True)
class AirEnvelope:
    epoch: int; seq: int; t0_us: float; frame_us: float; timestamp: str
    rate: int; subbands: int; blocks: int; bands: tuple[int, ...]
    complete_lag_us: float | None; e: tuple[int, ...]

@dataclass(frozen=True)
class AirSource:
    state: str; reason: str | None; codec: str | None; adapter: str | None
    index: int | None; handle: int | None; in_step: bool; monitor_drops: int | None; ipc_dropped: int

@dataclass(frozen=True)
class SetAirCalibration:
    latency_ms: float | None

@dataclass(frozen=True)
class AirFold:
    id: str; from_us: float; to_us: float

def decode_command(message: Any, *, now_raw_us: Callable[[], float] | None = None) -> Command: ...
# AirEnvelope's t0 window check needs "now"; other commands ignore the argument.
def air_envelope_from_batch(batch: AirBatch) -> AirEnvelope: ...
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/ipc/test_air_commands.py
import json
import pytest
from sendspin_bridge.services.ipc.commands import (
    AirEnvelope, AirFold, AirSource, InvalidCommand, SetAirCalibration, decode_command, encode_command,
)

NOW = 5_000_000_000.0
GOOD = {"cmd": "air_envelope", "v": 1, "epoch": 3, "seq": 128, "t0_us": NOW - 900_000.0, "frame_us": 2902.494,
        "timestamp": "kernel", "rate": 44100, "subbands": 8, "blocks": 16, "bands": [1, 2],
        "complete_lag_us": None, "e": [10, 20, 255]}

def dec(msg): return decode_command(msg, now_raw_us=lambda: NOW)

def test_envelope_round_trips():
    cmd = dec(GOOD)
    assert isinstance(cmd, AirEnvelope) and cmd.e == (10, 20, 255) and cmd.bands == (1, 2)
    assert dec(json.loads(json.dumps(encode_command(cmd)))) == cmd

@pytest.mark.parametrize(("key", "value"), [
    ("v", 2), ("epoch", -1), ("seq", "x"), ("t0_us", float("nan")), ("t0_us", NOW + 11e6), ("frame_us", 100.0),
    ("timestamp", "wall"), ("rate", 22050), ("subbands", 6), ("blocks", 5), ("bands", [8]),
    ("complete_lag_us", 500_000.0), ("e", []), ("e", [256]), ("e", [1] * 257), ("e", [True]),
])
def test_envelope_rejects_out_of_range(key, value):
    with pytest.raises(InvalidCommand):
        dec({**GOOD, key: value})

def test_source_and_calibration_and_fold():
    s = dec({"cmd": "air_source", "v": 1, "state": "unsupported_codec", "reason": "codec:aac", "codec": "aac",
             "adapter": "C0:FB:F9:62:D7:D6", "index": 1, "handle": 70, "in_step": False, "monitor_drops": None, "ipc_dropped": 0})
    assert isinstance(s, AirSource) and s.reason == "codec:aac"
    assert dec({"cmd": "set_air_calibration", "latency_ms": 9999}) == SetAirCalibration(latency_ms=2000.0)
    assert dec({"cmd": "set_air_calibration", "latency_ms": None}) == SetAirCalibration(latency_ms=None)
    with pytest.raises(InvalidCommand):
        dec({"cmd": "air_fold", "id": "a", "from_us": 0.0, "to_us": 31e6})       # > 30 s span
    assert isinstance(dec({"cmd": "air_fold", "id": "a" * 8, "from_us": 0.0, "to_us": 10e6}), AirFold)

def test_source_reason_is_bounded():
    with pytest.raises(InvalidCommand):
        dec({"cmd": "air_source", "v": 1, "state": "streaming", "reason": "x" * 81})

@pytest.mark.asyncio
async def test_daemon_without_air_sensor_ignores_envelopes_quietly(daemon_stdin_harness, caplog):
    h = await daemon_stdin_harness(air_sensor=False)
    for _ in range(50):
        await h.send(GOOD)
    assert h.errors == [] and h.daemon.air_counters["ignored"] == 50
    assert sum("air" in r.message for r in caplog.records if r.levelname == "WARNING") <= 1
```

(`daemon_stdin_harness` runs `_read_commands` against a pipe and a stub daemon object; build it from the existing stdin tests' pattern in `tests/unit/services/ipc/` — search for `_read_commands`.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the four commands in `decode_command`/`encode_command` with every rule of the spec's validation table (bools are not ints; lists must be lists of ints); `now_raw_us` defaults to `time.clock_gettime_ns(CLOCK_MONOTONIC_RAW)/1000`. In `daemon_process._read_commands`, invalid air commands increment `daemon.air_counters["invalid"]` and log one WARNING per 10 min (no `_emit_error`), while other commands keep today's error behaviour; valid ones go to `daemon.air_intake(cmd)`. `BridgeDaemon.air_intake` counts (`ignored` when no sensor) until A11 wires the sensor.
- [ ] **Step 4: Run** → PASS, plus `uv run pytest tests/unit/services/ipc -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(ipc): air envelope, source, calibration and fold commands`.

### Task A6: Parent wiring — `AirSensorService`

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/air_sensor_service.py`
- Modify: `src/sendspin_bridge/bridge/orchestrator.py` (create and start the service after the hub when any device is effective; stop on shutdown), `src/sendspin_bridge/bridge/client.py` (`air_sensor_effective` check at spawn; `send_air_command(cmd)` that drops when the daemon has not advertised `accepts`)
- Test: `tests/unit/services/bluetooth/test_air_sensor_service.py`

**Interfaces:**

```python
class AirSensorService:
    """Hub consumer (hub thread) + per-device trackers + batching onto the bridge loop."""
    wants_acl_tx = True
    def __init__(self, *, hub: HciMonitorHub, loop: asyncio.AbstractEventLoop,
                 resolve_client: Callable[[str], Any | None],      # peer MAC -> SendspinClient (device registry)
                 parser: A2dpAirParser | None = None, tick_s: float = 0.25, queue_len: int = 16,
                 cpu_budget: float = 0.05) -> None: ...
    def subscribe(self, mac: str) -> None: ...          # called by a client whose air sensor is effective
    def unsubscribe(self, mac: str) -> None: ...
    def on_record(self, rec: HubRecord) -> None: ...     # hub thread
    def on_hub_state(self, state: str, reason: str | None) -> None: ...
    async def run(self) -> None: ...                    # bridge loop: every tick_s, flush batches/sources to clients
    def arm_fold(self, mac: str, from_us: float) -> str: ...      # A16
    def finish_fold(self, fold_id: str, to_us: float) -> float | None: ...  # A16
```

- [ ] **Step 1: Failing tests** (fake hub, fake clients recording `send_air_command`; packets from `tests/fixtures/a2dp_air/eneby_clicks.btsnoop.gz` replayed as `HubRecord`s with `t_raw_us = t_unix·1e6 − OFFSET`):

```python
# tests/unit/services/bluetooth/test_air_sensor_service.py
import asyncio
import pytest
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirSource

pytestmark = pytest.mark.asyncio

async def test_batches_reach_only_the_subscribed_speaker(air_env):
    env = await air_env(peers={(1, 70): "FC:58:FA:EB:08:6C"}, accepts=[1])
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.replay_fixture(); await env.ticks(8)
    envs = [c for c in env.client("FC:58:FA:EB:08:6C").sent if isinstance(c, AirEnvelope)]
    assert envs and all(len(c.e) <= 128 for c in envs)
    assert env.client_count_with_traffic() == 1

async def test_nothing_is_sent_before_the_daemon_advertises(air_env):
    env = await air_env(peers={(1, 70): "FC:58:FA:EB:08:6C"}, accepts=None)
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.replay_fixture(); await env.ticks(8)
    assert env.client("FC:58:FA:EB:08:6C").sent == []

async def test_unattributed_links_are_ignored(air_env):
    env = await air_env(peers={}, accepts=[1])
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.replay_fixture(); await env.ticks(8)
    assert all(not isinstance(c, AirEnvelope) for c in env.client("FC:58:FA:EB:08:6C").sent)

async def test_slow_daemon_drops_oldest_and_counts(air_env):
    env = await air_env(peers={(1, 70): "FC:58:FA:EB:08:6C"}, accepts=[1], stalled=True)
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.replay_fixture(); await env.ticks(40)
    src = [c for c in env.client("FC:58:FA:EB:08:6C").sent if isinstance(c, AirSource)][-1]
    assert src.ipc_dropped > 0 and env.service.queued("FC:58:FA:EB:08:6C") <= 16

async def test_hub_without_permission_reports_unavailable(air_env):
    env = await air_env(peers={}, accepts=[1], hub_reason="no_cap_net_raw")
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.ticks(1)
    src = [c for c in env.client("FC:58:FA:EB:08:6C").sent if isinstance(c, AirSource)][-1]
    assert (src.state, src.reason) == ("unavailable", "no_cap_net_raw")

async def test_cpu_guard_unsubscribes_air_but_not_avrcp(air_env):
    env = await air_env(peers={(1, 70): "FC:58:FA:EB:08:6C"}, accepts=[1], cpu_pct=0.09)
    env.service.subscribe("FC:58:FA:EB:08:6C")
    await env.ticks(1)
    assert env.hub.consumers_wanting_acl_tx() == 0
    src = [c for c in env.client("FC:58:FA:EB:08:6C").sent if isinstance(c, AirSource)][-1]
    assert src.reason == "cpu_budget"

async def test_switch_off_means_no_consumer(air_env):
    env = await air_env(peers={}, accepts=[1])
    assert env.hub.consumers_wanting_acl_tx() == 0     # nobody subscribed: AVRCP-only cost
```

(`air_env` lives in the test file: fake hub with `peer()`, `adapters()`, `counters()`, `add/remove_consumer`; fake clients with `send_air_command` appending to `sent`, an `accepts` attribute and an optional stall that blocks the send coroutine.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: on the hub thread, convert each record and feed one `A2dpAirParser`; map emitted `MediaPacket`s by `(index, handle)` → peer MAC → subscribed device → its `AirLinkTracker` (unix → RAW via the record's own offset: keep `t_raw_us − t_unix·1e6` of the record that produced the packet; pass `in_step` from `parser.link_states()`); collect batches under a lock; on the loop, every 250 ms, move batches into each device's `deque(maxlen=16)` (count drops) and send them in order through `client.send_air_command(air_envelope_from_batch(b))`; send `AirSource` on change and every 5 s (adapter MAC from `hub.adapters()`, codec hint from `client.status["bt_codec_name"]`); register as a hub consumer only while ≥ 1 device is subscribed; act on `hub.counters()["cpu_pct"] > cpu_budget` by removing itself and reporting `cpu_budget`. `SendspinClient.send_air_command` checks `status.get("air_sensor", {}).get("accepts")` contains 1, else returns without writing.
- [ ] **Step 4: Run** → PASS, plus `tests/unit/services/bluetooth tests/unit/bridge -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(bluetooth): stream each speaker's air envelope to its daemon`.

### Task A7: `WrittenEnvelope` tap in the playout output

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_tap.py`
- Modify: `src/sendspin_bridge/services/audio/playout/gst_output.py` (`PlayoutPlayer(air_tap: WrittenEnvelope | None = None)`; in `_push`, after a successful push, `tap.add_block(block, schedule_at)`; on output reopen `tap.reset()`), `src/sendspin_bridge/services/audio/playout/engine.py` (read-only accessor `schedule_at(x_ns) -> float | None` and `kind_at`, no behaviour change)
- Test: `tests/unit/services/audio/playout/test_air_tap.py`

**Interfaces:**

```python
COMB_LAG = 3

def comb_peak_log2(pcm: memoryview, channels: int, start: int, stop: int, prev: tuple[int, ...]) -> float: ...
    # log2(1 + max |x[n] - x[n-3]|) over frames [start, stop) of interleaved S16LE, both channels;
    # prev holds the last COMB_LAG frames of the previous block per channel.

@dataclass(frozen=True, slots=True)
class WrittenSlice:
    i0: int                    # index of the first cell (global, since the output opened)
    cell_us: float
    w: array                   # 'f'
    s: array                   # 'd'  (RAW µs; NaN when unknown)
    program: bytes             # 1 = program, 0 = silence

class WrittenEnvelope:
    def __init__(self, *, out_rate: int, seconds: float = 20.0) -> None: ...
    @property
    def cell_us(self) -> float: ...                     # 128 / out_rate
    def add_block(self, block: Block, fmt: InputFormat, schedule_at: Callable[[int], float | None],
                  kind_at: Callable[[int], str | None]) -> None: ...   # feeder thread
    def reset(self) -> None: ...
    def newest_cell(self) -> int | None: ...
    def slice(self, i0: int, i1: int) -> WrittenSlice | None: ...      # copy under the lock; None if out of ring
    def cell_at_time(self, t_us: float) -> int | None: ...             # cell whose s is closest (binary search on s)
```

The tap works in output stream time; a block's input-rate frames map onto cells by `x`, so a 44.1/48 kHz input change needs no special case.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_air_tap.py
import math, struct
from array import array
import pytest
from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope, comb_peak_log2
from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat

FMT = InputFormat(sample_rate=44100)

def pcm_of(samples_l, samples_r=None):
    r = samples_r or samples_l
    return struct.pack(f"<{2 * len(samples_l)}h", *[v for pair in zip(samples_l, r) for v in pair])

def reference_comb(xs, prev):
    full = list(prev) + list(xs)
    return math.log2(1 + max(abs(full[n] - full[n - 3]) for n in range(3, len(full))))

def test_comb_matches_a_plain_reference():
    xs = [int(8000 * math.sin(2 * math.pi * 7350 * n / 44100)) for n in range(128)]
    got = comb_peak_log2(memoryview(pcm_of(xs)).cast("h"), 2, 0, 128, (0, 0, 0, 0, 0, 0))
    assert got == pytest.approx(reference_comb(xs, [0, 0, 0]), abs=1e-9)

def test_comb_rejects_bass_and_passes_the_band():
    lo = [int(8000 * math.sin(2 * math.pi * 200 * n / 44100)) for n in range(1280)]
    hi = [int(8000 * math.sin(2 * math.pi * 5000 * n / 44100)) for n in range(1280)]
    z = (0,) * 6
    assert comb_peak_log2(memoryview(pcm_of(hi)).cast("h"), 2, 0, 1280, z) - comb_peak_log2(memoryview(pcm_of(lo)).cast("h"), 2, 0, 1280, z) > 4.0  # > 24 dB

def block(x0_ns, frames, value=1000, program=True):
    return Block(pcm=pcm_of([value if n % 2 else -value for n in range(frames)]), x0_ns=x0_ns,
                 duration_ns=round(frames * 1e9 / 44100), program=program, dropped=0, inserted=0, late_drop_us=0.0)

def test_cells_carry_the_schedule_of_their_start():
    tap = WrittenEnvelope(out_rate=44100)
    tap.add_block(block(0, 441), FMT, schedule_at=lambda x: 1_000_000.0 + x / 1000.0, kind_at=lambda x: "program")
    sl = tap.slice(0, 3)
    assert sl.cell_us == pytest.approx(128 / 44100 * 1e6)
    assert list(sl.s) == pytest.approx([1_000_000.0 + i * sl.cell_us for i in range(3)], abs=0.05)
    assert sl.program == b"\x01\x01\x01"

def test_cells_split_across_blocks_are_whole():
    tap = WrittenEnvelope(out_rate=44100)
    tap.add_block(block(0, 100), FMT, lambda x: x / 1000.0, lambda x: "program")
    assert tap.newest_cell() is None                       # 100 < 128 frames: no full cell yet
    tap.add_block(block(round(100 * 1e9 / 44100), 156), FMT, lambda x: x / 1000.0, lambda x: "program")
    assert tap.newest_cell() == 1

def test_silence_cells_are_marked_and_zero():
    tap = WrittenEnvelope(out_rate=44100)
    tap.add_block(block(0, 256, value=0, program=False), FMT, lambda x: x / 1000.0, lambda x: "silence")
    sl = tap.slice(0, 2)
    assert sl.program == b"\x00\x00" and list(sl.w) == [0.0, 0.0]

def test_ring_forgets_old_cells():
    tap = WrittenEnvelope(out_rate=44100, seconds=1.0)
    for k in range(30):
        tap.add_block(block(round(k * 4410 * 1e9 / 44100), 4410), FMT, lambda x: x / 1000.0, lambda x: "program")
    assert tap.slice(0, 10) is None and tap.slice(tap.newest_cell() - 10, tap.newest_cell()) is not None

def test_cell_at_time_finds_the_scheduled_cell():
    tap = WrittenEnvelope(out_rate=44100)
    tap.add_block(block(0, 44100), FMT, lambda x: 5e6 + x / 1000.0, lambda x: "program")
    assert tap.cell_at_time(5e6 + 100 * tap.cell_us + 1.0) == 100

def test_player_without_tap_is_unchanged(playout_player_factory):
    p = playout_player_factory(air_tap=None)
    assert p._air_tap is None                              # no hook installed
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: per channel `ch = mv[c::channels]` slices of the block plus the 3-frame history, `max(map(abs, map(operator.sub, ch[3:], ch[:-3])))`; accumulate partial cells across blocks (frames carried as a small `array('h')` per channel, at most 127 frames); `s` from `schedule_at(x_cell_start)`, `program` from `kind_at(x) == "program"`; ring as fixed-size arrays with a global start index; `threading.Lock` around append and copy. The hook in `_push` is `if self._air_tap is not None:` wrapped in `try/except Exception` that logs once and sets `self._air_tap = None` (spec: never reaches the feeder).
- [ ] **Step 4: Run** → PASS, plus `tests/unit/services/audio/playout -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(playout): written-audio envelope with its schedule, for the air sensor`.

### Task A8: Correlator — NCC, PSR gate, sub-frame refinement

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_correlator.py`
- Test: `tests/unit/services/audio/playout/test_air_correlator.py`

**Interfaces:**

```python
PSR_GATE = 1.17
RHO_MIN = 0.25
SIDE_EXCLUDE = 2           # cells excluded around the peak for the side lobe
FLAT_AIR = 2.0             # energy units (1/8 bit), σ_a minimum
FLAT_WRITTEN = 0.25        # bits, σ_w minimum

@dataclass(frozen=True, slots=True)
class WindowResult:
    status: Literal["confirmed", "rejected", "flat", "invalid", "edge"]
    lag: float | None          # L = ℓ* + λ (cells), None unless confirmed
    rho: float | None
    psr: float | None

def ncc_scan(air: Sequence[float | None], written: Sequence[float], lag_lo: int, lag_hi: int) -> list[float]:
    """ρ(ℓ) for ℓ in [lag_lo, lag_hi]: air[j] against written[j + ℓ - lag_lo] (missing air cells excluded)."""
def refine(rho_m: float, rho_0: float, rho_p: float) -> float: ...      # parabolic, clamped ±0.5
def correlate_window(air: Sequence[float | None], written: Sequence[float], program: bytes,
                     lag_lo: int, lag_hi: int) -> WindowResult: ...
```

`air` is the window's air cells (energies as floats, `None` = missing); `written` covers the window shifted by every candidate lag (length `len(air) + lag_hi − lag_lo`); the correlator knows nothing about time.

- [ ] **Step 1: Failing tests** (seeded synthetic envelopes: a random-walk "music" contour, air = written shifted by a known lag, quantised to 1/8 bit, plus noise):

```python
# tests/unit/services/audio/playout/test_air_correlator.py
import math, random
import pytest
from sendspin_bridge.services.audio.playout.air_correlator import PSR_GATE, correlate_window, ncc_scan, refine

def music(n, seed=1):
    rnd = random.Random(seed); v, out = 6.0, []
    for i in range(n):
        v = max(0.0, min(14.0, v + rnd.gauss(0, 0.6) + (3.0 if rnd.random() < 0.04 else 0.0) - 0.05 * (v - 6)))
        out.append(v)
    return out

def make(lag_true=37.0, n=1034, lo=-86, hi=86, noise=0.3, seed=1):
    w = music(n + hi - lo + 4, seed)
    rnd = random.Random(seed + 100)
    def at(x):                                   # linear interpolation for fractional lags
        i = int(math.floor(x)); f = x - i
        return w[i] * (1 - f) + w[i + 1] * f
    air = [round(8 * (at(j + lag_true - lo) + rnd.gauss(0, noise))) for j in range(n)]
    return [float(a) for a in air], w[: n + hi - lo], b"\x01" * (n + hi - lo)

def test_refine_is_exact_on_a_parabola():
    f = lambda x: 1 - (x - 0.3) ** 2
    assert refine(f(-1), f(0), f(1)) == pytest.approx(0.3)
    assert refine(0.0, 1.0, 5.0) == 0.5                      # clamped

def test_finds_an_integer_lag():
    air, w, prog = make(lag_true=37.0)
    r = correlate_window(air, w, prog, -86, 86)
    assert r.status == "confirmed" and r.lag == pytest.approx(37.0, abs=0.15) and r.psr >= PSR_GATE

def test_finds_a_fractional_lag_within_a_fifth_of_a_cell():
    air, w, prog = make(lag_true=-12.4)
    assert correlate_window(air, w, prog, -86, 86).lag == pytest.approx(-12.4, abs=0.2)

def test_unrelated_envelopes_are_rejected():
    air, _, _ = make(seed=1)
    _, w, prog = make(seed=7)
    assert correlate_window(air, w, prog, -86, 86).status == "rejected"

def test_flat_windows_are_flat_not_rejected():
    _, w, prog = make()
    assert correlate_window([40.0] * 1034, w, prog, -86, 86).status == "flat"

def test_missing_and_silence_make_a_window_invalid():
    air, w, prog = make()
    holes = [None if i % 8 == 0 else a for i, a in enumerate(air)]        # 12.5 % missing
    assert correlate_window(holes, w, prog, -86, 86).status == "invalid"
    assert correlate_window(air, w, b"\x00" * len(prog), -86, 86).status == "invalid"

def test_a_peak_on_the_range_edge_is_not_trusted():
    air, w, prog = make(lag_true=86.0)                       # true lag = the last candidate
    assert correlate_window(air, w, prog, -86, 86).status in ("edge", "rejected")

def test_periodic_content_does_not_confirm_a_neighbour_period():
    base = [6 + 4 * math.sin(2 * math.pi * i / 60) for i in range(1300)]   # 174 ms loop, very periodic
    air = [float(round(8 * base[j + 100])) for j in range(1034)]
    r = correlate_window(air, base[14:14 + 1034 + 172], b"\x01" * 1206, -86, 86)
    assert r.status != "confirmed" or abs(r.lag - 0.0) < 0.5            # true lag is 0 in this frame; never 60 away

def test_scan_cost_is_bounded():
    import time
    air, w, prog = make()
    t0 = time.perf_counter(); ncc_scan(air, w, -86, 86); dt = time.perf_counter() - t0
    assert dt < 0.25                                                      # acquisition scan, generous for CI
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the spec "Correlation" steps 1, 2, 4–6: validity (≥ 90 % present, ≥ 80 % program over the zero-lag-centred written span), z-scored air once, per-lag `w̄`, `σ_w` from prefix sums of `w` and `w²` over present indices (when cells are missing, use the masked sums over the present index list; with ≤ 10 % missing compute the dot product on the compacted lists), dot products with `sum(map(operator.mul, a, b))`; side lobe excludes `|ℓ − ℓ*| ≤ SIDE_EXCLUDE`; `edge` when `ℓ*` is the first or last lag.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Comb vs band-pass check (R-7).** Add `tests/unit/services/audio/playout/test_air_envelope_choice.py` marked `slow`: on the A10 fixture (skip until it exists) compute the median PSR of confirmed windows with the comb envelope and with a reference biquad band-pass 2.7–8.3 kHz written envelope; assert `comb ≥ bandpass − 0.05`. If it fails once A10 lands, switch `comb_peak_log2` for the band-pass in A7's module and record the CPU cost in the spec.
- [ ] **Step 6: Commit** `feat(playout): envelope correlator with peak/side-lobe gate and sub-frame lag`.

### Task A9: `AirSensor` — air stream, search policy, lock state machine, snapshot

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_sensor.py`
- Test: `tests/unit/services/audio/playout/test_air_sensor.py`

**Interfaces:**

```python
LOCK_HOLD_S = 5.0
NO_LOCK_S = 20.0
WINDOW_S = 3.0
HOP_S = 1.0
TRACK_CELLS = 20
ACQUIRE_US = 250_000.0
DELTA0_US = 10_000.0
JUMP_CONFIRM_CELLS = 3

class AirStream:
    """Air cells of the current epoch on the media grid (20 s ring)."""
    def add(self, env: AirEnvelope) -> bool: ...        # False when dropped (old epoch, backwards seq)
    @property
    def epoch(self) -> int | None: ...
    def window(self, j_end: int, n: int) -> tuple[list[float | None], float] | None: ...  # cells, t of cell j_end-n

class AirSensor:
    def __init__(self, *, tap: WrittenEnvelope, now_us: Callable[[], float], output_delay_ms: Callable[[], float],
                 wall_iso: Callable[[], str] = ..., log=logger) -> None: ...
    def on_envelope(self, env: AirEnvelope) -> None: ...
    def on_source(self, src: AirSource) -> None: ...
    def set_calibration(self, latency_ms: float | None) -> None: ...
    def on_engine_event(self, kind: Literal["underrun", "pa_step", "stall", "reopen"]) -> None: ...
    def step(self) -> None: ...                          # run due windows (≤ 1 per HOP_S); asyncio thread
    def snapshot(self) -> dict[str, object]: ...         # the spec's status dict
```

- [ ] **Step 1: Failing tests** (a fake tap built from the A8 `music()` contour with a known schedule `s_i = S0 + i·T_c`; air envelopes generated with `t_air = s_i + H_true − Δ_enc` for the written cell `i = j + L`; fake clock):

```python
# tests/unit/services/audio/playout/test_air_sensor.py
import pytest
from sendspin_bridge.services.audio.playout.air_sensor import AirSensor, NO_LOCK_S

def test_locks_and_reports_host_placement(air_world):
    w = air_world(H_true_ms=37.0, L=500)
    w.run(seconds=8)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["host_placement_ms"] == pytest.approx(37.0, abs=1.0)
    assert s["air_error_ms"] is None and s["calibrated"] is False
    assert 0.0 < s["confidence"] <= 1.0

def test_air_error_needs_the_calibration_and_uses_the_output_delay(air_world):
    w = air_world(H_true_ms=37.0, L=500, output_delay_ms=180.0)
    w.sensor.set_calibration(203.0)
    w.run(seconds=8)
    assert w.sensor.snapshot()["air_error_ms"] == pytest.approx(37.0 + 203.0 - 180.0, abs=1.0)

def test_engine_jump_moves_h_not_the_lag(air_world):
    w = air_world(H_true_ms=37.0, L=500)
    w.run(seconds=8); lag = w.sensor.snapshot()["lag_cells"]
    w.shift_schedule_ms(-41.5)                      # the engine's steady jump: s(x) moves, x does not
    w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["host_placement_ms"] == pytest.approx(37.0 + 41.5, abs=1.5)
    assert s["lag_cells"] == pytest.approx(lag, abs=0.3)

def test_silence_holds_then_loses_lock(air_world):
    w = air_world(H_true_ms=10.0, L=500)
    w.run(seconds=8)
    w.silence(); w.run(seconds=6)
    assert w.sensor.snapshot()["state"] == "holding" and w.sensor.snapshot()["host_placement_ms"] is not None
    w.run(seconds=NO_LOCK_S)
    s = w.sensor.snapshot()
    assert s["state"] == "no_lock" and s["host_placement_ms"] is None

def test_new_epoch_reacquires(air_world):
    w = air_world(H_true_ms=10.0, L=500)
    w.run(seconds=8)
    w.new_epoch(L=-300); w.run(seconds=0.5)
    assert w.sensor.snapshot()["state"] == "acquiring"
    w.run(seconds=8)
    assert w.sensor.snapshot()["state"] == "locked"

def test_a_single_wrong_peak_is_not_accepted(air_world):
    w = air_world(H_true_ms=10.0, L=500)
    w.run(seconds=8)
    w.inject_one_window_with_lag(L=520)             # one confirmed-looking window 20 cells away
    w.run(seconds=1)
    assert w.sensor.snapshot()["lag_cells"] == pytest.approx(500, abs=0.3)

def test_source_states_pass_through(air_world):
    w = air_world(H_true_ms=10.0, L=500)
    w.source(state="unsupported_codec", reason="codec:aac")
    assert w.sensor.snapshot()["state"] == "unsupported_codec" and w.sensor.snapshot()["reason"] == "codec:aac"
    w.source(state="unavailable", reason="no_cap_net_raw")
    assert w.sensor.snapshot()["state"] == "unavailable"

def test_snapshot_shape_is_stable(air_world):
    keys = set(air_world(H_true_ms=0.0, L=0).sensor.snapshot())
    assert keys >= {"state", "reason", "codec", "adapter", "host_placement_ms", "air_error_ms", "air_to_sound_ms",
                    "calibrated", "confidence", "jitter_ms", "psr", "lag_cells", "windows_confirmed",
                    "windows_rejected", "windows_skipped", "last_confirmed_at", "epochs", "frames",
                    "missing_frames", "anchor", "complete_lag_ms", "timestamp", "monitor_drops", "ipc_dropped",
                    "dropped_messages", "accepts", "fold"}
```

(`air_world` is a fixture in the test file that owns a real `WrittenEnvelope`, a real `AirSensor` and a fake clock; it writes blocks into the tap and pushes `AirEnvelope`s every 250 ms of simulated time with the latency `PA_LATENCY = 600 ms` between writing and air, so the sensor sees the same delays as in the daemon.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the spec: `AirStream` keyed by epoch with a 20 s ring; window selection (newest air cell whose partner cell `j + L̂ + range` is ≤ `tap.newest_cell()`); priors (`cell_at_time(t_mid − δ₀)`), ranges, wide search after engine events, continuity candidate; `H` with `Δ_enc` and linear `s` interpolation inside one segment (if the matched span crosses a NaN or a step > 1 cell of `s`, use the majority side); medians, confidence, jitter, counters, state machine; INFO logs on state changes (≤ 1/min per kind), DEBUG summary every 10 s (per window with `SENDSPIN_AIR_DEBUG=1`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(playout): air sensor lock state machine and host placement`.

### Task A10: Simulation with recorded captures

**Files:**
- Create: `tests/fixtures/a2dp_air/build_air_music_fixture.py`, `tests/fixtures/a2dp_air/air_music.btsnoop.gz`, `tests/unit/services/audio/playout/test_air_sim.py`
- Test: the new test file

**Interfaces:**

```python
# build_air_music_fixture.py
def music_pcm(seconds: float = 20.0, rate: int = 44100, seed: int = 20261028) -> bytes: ...   # deterministic, stereo S16LE
TRUTH: dict  # {"t_conn": ..., "skip_at_s": 9.0, "skip_ms": 20.0, "restart_at_s": 14.0, "frames_per_packet": 7, ...}
def main(argv: list[str] | None = None) -> int: ...   # encodes with libsbc 1.3.1, writes the btsnoop, checks EXPECTED_SHA256
```

Generator content: 0–6 s percussive transients on a seeded rhythm with notes; 6–8 s a quiet passage (−40 dB); 8–12 s a strict 4-on-the-floor loop; 12–20 s mixed. Packing: 7 SBC frames per RTP packet, ACL fragments of 310 bytes, NOCP per fragment with seeded 1–9 ms delay, a Connection Complete at 0 s, a 20 ms "Skipping" (frames dropped before encoding, RTP timestamps continuous) at 9 s, an AVDTP suspend at 14 s (no packets for 300 ms, RTP restarting at 0, new SSRC). Bitpool 2 as in the libsbc click fixtures (scale factors do not depend on it), fixed gzip mtime, `EXPECTED_SHA256` checked like `build_libsbc_fixture.py`.

- [ ] **Step 1: Build the fixture** (`uv run python tests/fixtures/a2dp_air/build_air_music_fixture.py`; needs `libsbc.so.1` 1.3.1 on the build machine only). Keep it ≤ 120 KB.
- [ ] **Step 2: Failing tests** — the whole chain with real code (parser → tracker → `air_envelope_from_batch` → `decode_command` → `AirSensor`), the written side produced by writing `music_pcm()` through a `WrittenEnvelope` with an independently chosen schedule truth:

```python
# tests/unit/services/audio/playout/test_air_sim.py
import pytest
pytestmark = pytest.mark.slow

def test_cold_start_offset_is_measured(sim):
    r = sim.run(H_truth=[(0.0, 37.0)])                       # +37 ms cold-start placement error
    assert r.lock_time_s < 5.0
    assert r.h_err_ms_locked_p95 <= 2.0

def test_steady_jump_is_seen_within_3s(sim):
    r = sim.run(H_truth=[(0.0, 37.0), (8.0, -4.5)])          # −41.5 ms jump at 8 s
    assert r.time_to_reflect_s(8.0, -4.5, tol_ms=2.0) <= 3.0

def test_skipping_drop_reacquires_without_a_false_value(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.max_abs_err_ms_between(9.0, 13.0, states=("locked",)) <= 3.0

def test_quiet_passage_holds_not_lies(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert set(r.states_between(6.5, 8.0)) <= {"locked", "holding"}
    assert r.max_abs_err_ms_between(6.0, 8.0, states=("locked", "holding")) <= 3.0

def test_loop_section_never_confirms_a_wrong_lag(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.max_abs_err_ms_between(8.0, 12.0, states=("locked",)) <= 3.0

def test_avdtp_restart_is_a_new_epoch_and_relocks(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.epochs >= 2 and r.lock_time_after_s(14.0) < 5.0

def test_noise_control_never_confirms(sim):
    r = sim.run(H_truth=[(0.0, 10.0)], written_seed=999)     # written envelope of different music
    assert r.windows_confirmed == 0

def test_real_click_capture_locks_consistently_with_the_phase_tool(sim_clicks):
    r = sim_clicks.run()                                     # eneby_clicks.btsnoop.gz + rendered click track
    assert r.locked_windows >= 1 and abs(r.h_minus_fold_ms) <= 3.0

def test_gate_calibration_report(sim, record_property):
    r = sim.run(H_truth=[(0.0, 10.0)])
    record_property("psr_confirmed_median", r.psr_confirmed_median)
    record_property("psr_noise_max", sim.run(H_truth=[(0.0, 10.0)], written_seed=999).psr_max)
    assert r.psr_confirmed_median > 1.17
```

(`sim` maps the btsnoop's unix times to RAW with a fixed offset, writes the written side in 10 ms blocks with `s(x) = t_air_truth(x) − H_truth(t)` and feeds envelopes with the measured 250 ms batching; the truth uses the frame positions from `TRUTH`, not the code under test.)

- [ ] **Step 3: Run** `uv run pytest tests/unit/services/audio/playout/test_air_sim.py -q -p no:cacheprovider -n0` → FAIL where the implementation is wrong; fix the implementation (A4/A7–A9 modules), never the truth.
- [ ] **Step 4: Recalibrate R-9.** If the confirmed-window PSR median on music and the noise maximum leave a different gap than 1.17 (target: noise max < gate < 0.9 × music median), change `PSR_GATE` once, record both numbers and the new value in the spec's R-9 row, and re-run A8/A9 tests.
- [ ] **Step 5: Run** the sim file and `tests/unit/services/audio/playout` → PASS. Also run A8's Step 5 check.
- [ ] **Step 6: Commit** `test(playout): air sensor simulation on a synthetic-music SBC capture and the click capture`.

### Task A11: Daemon wiring

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`DaemonArgs.air_sensor: dict | None = None`; build `WrittenEnvelope` + `AirSensor` when it is set and the player is a `PlayoutPlayer`; `air_intake` dispatch; output-delay getter; engine events), `src/sendspin_bridge/services/ipc/daemon_process.py` (spawn param `air_sensor: {"enabled": true, "latency_ms": …}`; 1 Hz `AirSensor.step()` + status `air_sensor` in the telemetry watcher; `accepts: [1]` in the first status), `src/sendspin_bridge/services/audio/playout/gst_output.py` (pass the tap; report `underrun`/`pa_step`/`stall`/`reopen` to an optional listener), `src/sendspin_bridge/bridge/client.py` (spawn param from `air_sensor_effective` + the calibration resolver of A15, or `latency_ms: None` before A15)
- Test: `tests/unit/services/ipc/test_bridge_daemon_air.py`

**Interfaces:**

```python
# bridge_daemon.py
def air_intake(self, cmd: AirEnvelope | AirSource | SetAirCalibration | AirFold) -> None: ...  # asyncio thread
def air_snapshot(self) -> dict[str, object] | None: ...   # None when the sensor is not built
# gst_output.py
PlayoutPlayer(..., air_tap: WrittenEnvelope | None = None, on_engine_event: Callable[[str], None] | None = None)
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/ipc/test_bridge_daemon_air.py
import pytest
from sendspin_bridge.services.ipc.bridge_daemon import DaemonArgs

def test_default_args_build_no_air_sensor(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="measure"))
    assert d.air_snapshot() is None

def test_measure_with_air_sensor_builds_tap_and_sensor(make_daemon, fake_playout_player):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True, "latency_ms": None}),
                    player=fake_playout_player)
    assert fake_playout_player.air_tap is not None
    snap = d.air_snapshot()
    assert snap["state"] == "acquiring" or snap["state"] == "no_signal"
    assert snap["accepts"] == [1]

def test_off_mode_ignores_the_air_sensor_param(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="off", air_sensor={"enabled": True}))
    assert d.air_snapshot() is None

def test_calibration_command_reaches_the_sensor(make_daemon, fake_playout_player):
    from sendspin_bridge.services.ipc.commands import SetAirCalibration
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}), player=fake_playout_player)
    d.air_intake(SetAirCalibration(latency_ms=203.0))
    assert d.air_snapshot()["air_to_sound_ms"] == 203.0 and d.air_snapshot()["calibrated"] is True

def test_output_delay_follows_the_player_delay(make_daemon, fake_playout_player):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}, static_delay_ms=150.0),
                    player=fake_playout_player)
    assert d._air_sensor._output_delay_ms() == 150.0

def test_sensor_exception_disables_only_the_sensor(make_daemon, fake_playout_player, monkeypatch):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}), player=fake_playout_player)
    monkeypatch.setattr(d._air_sensor, "step", lambda: 1 / 0)
    d.air_step()
    assert d.air_snapshot()["state"] == "unavailable" and d.air_snapshot()["reason"] == "internal_error"
    assert fake_playout_player.stopped is False

@pytest.mark.asyncio
async def test_status_carries_air_sensor_at_1hz(telemetry_watcher_harness):
    h = await telemetry_watcher_harness(air=True)
    await h.advance(2.1)
    assert h.status_updates_with("air_sensor") >= 2
```

(`make_daemon`, `fake_playout_player`: reuse the patterns of `tests/unit/services/ipc/test_bridge_daemon_playout.py`; `telemetry_watcher_harness` mirrors the playout telemetry test of that file.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: build the sensor only for `measure` + a `PlayoutPlayer` + `air_sensor.enabled`; the tap's `out_rate` from the player's output rate; `output_delay_ms` reads `self._player_delay_ms`; `air_intake` routes commands; `air_step()` wraps `AirSensor.step()` with the containment rule; the telemetry watcher calls `air_step()` every second and patches `status["air_sensor"] = air_snapshot()`; the player's engine-event listener calls `sensor.on_engine_event` via `loop.call_soon_threadsafe` (feeder thread → asyncio); `AirFold` is answered by A16 (until then reply `{"id", "ok": false, "reason": "not_implemented"}`).
- [ ] **Step 4: Run** → PASS; `tests/unit/services/ipc -q -p no:cacheprovider` unchanged and green.
- [ ] **Step 5: Commit** `feat(ipc): run the air sensor in measure-mode daemons`.

### Task A12: Parent status, API model, OpenAPI

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (`DeviceStatus.air_sensor: dict | None = None`; `"air_sensor"` in `_ALLOWED_STATUS_KEYS`; parent-side dict when the daemon does not run the sensor: `{"state": "off", "reason": <effective reason>}` or `{"state": "unavailable", "reason": <hub reason>}`), `src/sendspin_bridge/application/models/devices.py` (`AirSensorTiming`, `DeviceTiming.air_sensor`, `_air_or_none`), `ui/openapi.json` + `ui/src/api/*` (regenerated)
- Test: `tests/unit/api/test_air_sensor_api.py`

**Interfaces:**

```python
class AirSensorFold(ResponseModel):
    id: str | None = None; phase_ms: float | None = None; ok: bool = False; reason: str | None = None

class AirSensorTiming(ResponseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True, extra="ignore")
    state: str = "off"; reason: str | None = None; codec: str | None = None; adapter: str | None = None
    host_placement_ms: float | None = None; air_error_ms: float | None = None; air_to_sound_ms: float | None = None
    calibrated: bool = False; confidence: float | None = None; jitter_ms: float | None = None; psr: float | None = None
    lag_cells: float | None = None; windows_confirmed: int = 0; windows_rejected: int = 0; windows_skipped: int = 0
    last_confirmed_at: str | None = None; epochs: int = 0; frames: int = 0; missing_frames: int = 0
    anchor: str = "send"; complete_lag_ms: float | None = None; timestamp: str | None = None
    monitor_drops: int | None = None; ipc_dropped: int = 0; dropped_messages: int = 0; fold: AirSensorFold | None = None
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/api/test_air_sensor_api.py
def test_device_timing_carries_air_sensor(api_client, running_device):
    running_device.status.update({"air_sensor": {"state": "locked", "host_placement_ms": 12.4, "confidence": 0.8,
                                                 "accepts": [1], "unknown_key": 1}})
    body = api_client.get(f"/api/v1/devices/{running_device.id}").json()
    air = body["timing"]["air_sensor"]
    assert air["state"] == "locked" and air["host_placement_ms"] == 12.4 and "unknown_key" not in air

def test_off_device_reports_the_reason(api_client, running_device):
    running_device.status.update({"air_sensor": {"state": "off", "reason": "needs_measure"}})
    air = api_client.get(f"/api/v1/devices/{running_device.id}").json()["timing"]["air_sensor"]
    assert air == {**air, "state": "off", "reason": "needs_measure"}

def test_malformed_air_sensor_is_dropped(api_client, running_device):
    running_device.status.update({"air_sensor": {"state": 5, "confidence": "high"}})
    assert api_client.get(f"/api/v1/devices/{running_device.id}").json()["timing"]["air_sensor"] is None

def test_status_key_is_accepted_from_the_daemon():
    from sendspin_bridge.bridge.client import _ALLOWED_STATUS_KEYS
    assert "air_sensor" in _ALLOWED_STATUS_KEYS

def test_openapi_lists_the_model(api_client):
    assert "AirSensorTiming" in api_client.get("/api/v1/openapi.json").json()["components"]["schemas"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the model (validated like `_playout_or_none`; `state` must be a string), the status field, the parent-side states; regenerate `uv run python scripts/export_openapi.py && cd ui && npm run gen:api`.
- [ ] **Step 4: Run** → PASS; `cd ui && npm run build`.
- [ ] **Step 5: Commit** `feat(api): air sensor status in device timing`.

### Task A13: Diagnostics, guidance and logs

**Files:**
- Modify: `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (`air_sensor_unavailable`, `air_sensor_codec`), `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (`_build_air_sensor_issues`), the diagnostics bundle builder (`air_sensor` per device + `get_hub().counters()`; find it with `grep -rn "def build_diagnostics" src/sendspin_bridge`), `ui/src/i18n/{en,ru}.json`
- Test: `tests/unit/services/diagnostics/test_air_guidance.py` (air part)

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/diagnostics/test_air_guidance.py (air part)
from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition
from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

def test_air_issues_are_registered():
    assert issue_definition("air_sensor_unavailable").severity == "info"
    assert issue_definition("air_sensor_codec").default_reason_codes == ("non_sbc_codec",)

def test_unsupported_codec_suggests_prefer_sbc(make_device):
    dev = make_device(air_sensor={"state": "unsupported_codec", "reason": "codec:aac"})
    issue = next(i for i in _build_device_issues([dev]) if i.key == "air_sensor_codec")
    assert "PREFER_SBC_CODEC" in (issue.primary_action.key + issue.primary_action.label + issue.message)

def test_missing_capability_is_one_bridge_level_issue(make_device):
    devs = [make_device(air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"}) for _ in range(3)]
    issues = [i for i in _build_device_issues(devs) if i.key == "air_sensor_unavailable"]
    assert len(issues) == 1 and "no_cap_net_raw" in issues[0].reason_codes

def test_switch_off_raises_nothing(make_device):
    dev = make_device(air_sensor={"state": "off", "reason": "switch_off"})
    assert not [i for i in _build_device_issues([dev]) if i.key.startswith("air_sensor")]

def test_bundle_contains_air_snapshot(diagnostics_bundle_for):
    bundle = diagnostics_bundle_for(air_sensor={"state": "locked", "host_placement_ms": 3.0})
    assert bundle["devices"][0]["air_sensor"]["state"] == "locked" and "hci_monitor" in bundle
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**: issues only when the switch is on (`state` not `off`); one aggregated `air_sensor_unavailable` for the bridge (same reason on every device), per device `air_sensor_codec` with the primary action "Prefer SBC codec" (the existing settings deep link for `PREFER_SBC_CODEC`); i18n en/ru; bundle fields. Logs are already in A9/A3/A6; add a test-free check that no log line contains raw record bytes (grep in review).
- [ ] **Step 4: Run** → PASS (`test_issue_registry_keys.py` green). **Step 5: Commit** `feat(diagnostics): air sensor guidance (capability, codec) and bundle data`.

### Task A14: UI — timing panel block, chip tooltip, setting

**Files:**
- Modify: `ui/src/components/devices/DeviceTimingPanel.vue` ("Air sensor" block), `ui/src/utils/deviceChips.ts` (tooltip line), `ui/src/settings/layout.ts` (`EXPERIMENTAL_AIR_SENSOR` under Audio → advanced, with help), `ui/src/i18n/{en,ru}.json`
- Test: `ui/src/components/devices/__tests__/DeviceTimingPanel.air.spec.ts`, `ui/src/utils/__tests__/deviceChips.air.spec.ts`, `ui/src/settings/__tests__/layout.air.spec.ts` (follow the folder convention of the existing specs next to these files)

- [ ] **Step 1: Failing tests** (vitest):
  - the panel shows "Host placement +12.4 ms (±0.8)", "Air error −3.1 ms", the confidence as a percentage and the state word for `locked`; shows "needs calibration" when `calibrated` is false; shows the reason text for `unavailable`/`unsupported_codec`/`off`; renders nothing for `air_sensor: null`;
  - the chip tooltip has one "air: locked, H +12.4 ms" line only when `state` is not `off`;
  - the settings layout lists `EXPERIMENTAL_AIR_SENSOR` with a help key that exists in en and ru.
- [ ] **Step 2: Run** `cd ui && npx vitest run <the three specs>` → FAIL. **Step 3: Implement** with the generated `AirSensorTiming` type; numbers with sign and one decimal; en and ru strings (no hard-coded text). **Step 4: Run** `npm test && npm run build` → PASS.
- [ ] **Step 5: Commit** `feat(ui): air sensor in the timing panel and settings`.

### Task A15: Calibration storage, hot apply and API

**Files:**
- Modify: `src/sendspin_bridge/config/players.py` (`AirLatencyEntry`, `Player.air_to_sound_latency: list[AirLatencyEntry] | None = None`, ≤ 8, unique (codec, adapter)), `src/sendspin_bridge/config/schema.json` (player definition), `src/sendspin_bridge/config/__init__.py` (`resolve_air_latency(player_cfg, codec, adapter) -> float | None`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (`air_to_sound_latency` in `_DEVICE_HOT_FIELDS`), `src/sendspin_bridge/bridge/client.py` (`apply_hot_config` sends `SetAirCalibration`; resend on codec/adapter change from the `air_sensor` status), `src/sendspin_bridge/application/devices.py` + `api/routers/devices.py` (`DELETE …/air-calibrations[/{codec}/{adapter}]`), `src/sendspin_bridge/application/models/devices.py` (`DeviceAudio.air_to_sound_latency`), `ui/src/components/devices/DeviceTimingPanel.vue` (line + Forget), i18n
- Test: `tests/unit/config/test_air_sensor_config.py` (calibration part), `tests/unit/api/test_air_sensor_api.py` (calibration part)

**Interfaces:**

```python
class AirLatencyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codec: Literal["sbc"]
    adapter: str = Field(pattern=r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
    latency_ms: float = Field(ge=0, le=2000)
    spread_ms: float | None = Field(None, ge=0, le=500)
    measured_at: str
    reference: str = Field("", max_length=80)
    source: Literal["sync_wizard", "manual"] = "sync_wizard"

def resolve_air_latency(player_cfg: Mapping[str, Any], codec: str | None, adapter: str | None) -> float | None: ...
def upsert_air_latency(entries: list[dict], entry: dict, *, max_entries: int = 8) -> list[dict]: ...  # replace same key; drop oldest beyond 8
```

- [ ] **Step 1: Failing tests**

```python
# config part
from sendspin_bridge.config import resolve_air_latency, upsert_air_latency
E = {"codec": "sbc", "adapter": "C0:FB:F9:62:D7:D6", "latency_ms": 203.4, "measured_at": "2026-10-28T09:00:00+00:00"}

def test_resolve_by_codec_and_adapter():
    p = {"air_to_sound_latency": [E]}
    assert resolve_air_latency(p, "sbc", "C0:FB:F9:62:D7:D6") == 203.4
    assert resolve_air_latency(p, "sbc", "C0:FB:F9:62:D6:9D") is None
    assert resolve_air_latency(p, "aac", "C0:FB:F9:62:D7:D6") is None

def test_upsert_replaces_same_key_and_caps_at_8():
    entries = [E]
    entries = upsert_air_latency(entries, {**E, "latency_ms": 199.0, "measured_at": "2026-10-29T00:00:00+00:00"})
    assert [e["latency_ms"] for e in entries] == [199.0]
    for k in range(10):
        entries = upsert_air_latency(entries, {**E, "adapter": f"C0:FB:F9:62:D7:{k:02X}", "measured_at": f"2026-11-{k + 1:02d}T00:00:00+00:00"})
    assert len(entries) == 8

def test_player_model_validates_entries():
    import pytest
    from pydantic import ValidationError
    from sendspin_bridge.config.players import Player
    with pytest.raises(ValidationError):
        Player.model_validate({"id": "p", "backend": {"type": "bluetooth", "mac": "AA:BB:CC:DD:EE:FF"},
                               "air_to_sound_latency": [{**E, "latency_ms": -1}]})

def test_calibration_change_is_hot():
    from sendspin_bridge.services.infrastructure.config_diff import iter_device_hot_fields
    assert "air_to_sound_latency" in iter_device_hot_fields()
```

```python
# api part
def test_forget_one_calibration(api_client, running_device_with_calibration):
    r = api_client.delete(f"/api/v1/devices/{running_device_with_calibration.id}/air-calibrations/sbc/C0:FB:F9:62:D7:D6")
    assert r.status_code == 204
    assert api_client.get(f"/api/v1/devices/{running_device_with_calibration.id}").json()["audio"]["air_to_sound_latency"] == []
    assert running_device_with_calibration.sent_commands[-1] == {"cmd": "set_air_calibration", "latency_ms": None}

def test_forget_unknown_is_404(api_client, running_device_with_calibration):
    assert api_client.delete(f"/api/v1/devices/{running_device_with_calibration.id}/air-calibrations/sbc/00:00:00:00:00:00").status_code == 404
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; the parent resolves the latency from the adapter MAC reported by `air_sensor.adapter` and codec `air_sensor.codec` (falls back to the player's configured adapter and `bt_codec_name`), sends `SetAirCalibration` at spawn (spawn param) and when the resolved value changes. UI line "Speaker latency: 203.4 ms · SBC · hci1 · 2 days ago" with Forget (confirm dialog).
- [ ] **Step 4: Run** → PASS; regenerate OpenAPI and the SPA client; `npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(config): per-speaker air-to-sound latency, applied live`.

### Task A16: Wizard calibration of `L_as`

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/a2dp_air_fold.py` (moved from `scripts/a2dp_air_phase.py`: `chirp_subbands`, `frame_energy`, `unwrap_rtp`, `click_phase_ms`, `sbc_encoder_delay_samples`; the script imports them — its tests stay green unchanged)
- Modify: `src/sendspin_bridge/services/bluetooth/air_sensor_service.py` (`AirClickFold`, `arm_fold`/`finish_fold`), `src/sendspin_bridge/services/audio/playout/air_sensor.py` (`fold(from_us, to_us) -> tuple[float | None, str | None]` on the written ring, axis `T = s + D_out`), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (answer `AirFold` into `air_sensor.fold`), `src/sendspin_bridge/application/sync_tuning.py` (sequence watcher with RAW stamps; `air_calibrations` after `record_sequence`; save use case), `src/sendspin_bridge/application/models/sync.py` (`AirCalibrationCandidate`, `SyncSessionOut.air_calibrations`, `AirCalibrationSaveIn`), `src/sendspin_bridge/api/routers/sync.py` (`POST /api/v1/sync/sessions/{id}/air-calibrations`), `docs/contracts/sync-contract-v1.json`, `ui/src/components/sync/StepTune.vue` (result rows + checkbox), i18n, `docs-site/src/content/docs/{,ru/}group-sync-tuning.md`
- Test: `tests/unit/api/test_sync_air_calibration.py`, `tests/unit/services/audio/playout/test_air_sensor.py` (fold part), `tests/unit/services/bluetooth/test_air_sensor_service.py` (fold part)

**Interfaces:**

```python
# a2dp_air_fold.py — moved, signatures unchanged
def chirp_subbands(frame: SbcFrame) -> range: ...
def frame_energy(frame: SbcFrame) -> float: ...
def click_phase_ms(rows, frame_s, epoch_s=0.0, encoder_delay_s=0.0) -> float | None: ...
def sbc_encoder_delay_samples(subbands: int) -> float: ...

class AirClickFold:
    def __init__(self, *, from_us: float, max_s: float = 30.0) -> None: ...
    def add_frame(self, t_air_us: float, frame: SbcFrame) -> None: ...
    def phase_ms(self, to_us: float) -> float | None: ...     # RAW axis, epoch 0, estimator convention

# application/sync_tuning.py
def air_calibration_candidates(session: SyncSession, stamps: Mapping[str, tuple[float, float]]) -> list[AirCalibrationCandidate]: ...
def save_air_calibrations(sid: str, members: list[str]) -> list[AirCalibrationCandidate]: ...

# application/models/sync.py
class AirCalibrationCandidate(ResponseModel):
    member: str; eligible: bool; reason: str | None
    latency_ms: float | None; acoustic_lag_ms: float | None; host_placement_ms: float | None
    codec: str | None; adapter: str | None; delay_report_ms: float | None
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_air_sensor.py (fold part)
def test_written_fold_finds_the_click_phase_on_the_heard_axis(click_world):
    w = click_world(output_delay_ms=180.0, click0_heard_raw_us=5_000_123_000.0)
    w.write_seconds(12)
    phase, reason = w.sensor.fold(w.t(1.0), w.t(11.0))
    assert reason is None
    assert phase == pytest.approx((5_000_123_000.0 / 1000.0 - 1000.0) % 10_000.0, abs=0.5)   # chirp start, LEAD_IN 1 s

# tests/unit/services/bluetooth/test_air_sensor_service.py (fold part)
def test_air_fold_on_the_fixture_equals_the_phase_tool():
    # AirClickFold fed with the fixture's frames at RAW = unix·1e6 − OFFSET gives the tool's phase − OFFSET mod cycle
    ...
    assert fold_phase == pytest.approx((tool_phase_ms - OFFSET_US / 1000.0) % 10_000.0, abs=0.5)

# tests/unit/api/test_sync_air_calibration.py
def test_latency_is_acoustic_lag_minus_host_placement(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0},   # phases, recording clock
                       air_phase={"eneby": 2_012.0}, sched_phase={"eneby": 2_000.0})      # RAW axis, ms mod 10 s
    out = env.record_and_get_session()
    c = next(c for c in out["air_calibrations"] if c["member"] == "eneby")
    assert c["eligible"] and c["acoustic_lag_ms"] == pytest.approx(215.0)
    assert c["host_placement_ms"] == pytest.approx(12.0) and c["latency_ms"] == pytest.approx(203.0)

def test_no_reference_no_calibration(sync_air_env):
    env = sync_air_env(reference=None, acoustic={"eneby": 1415.0}, air_phase={"eneby": 2012.0}, sched_phase={"eneby": 2000.0})
    c = env.record_and_get_session()["air_calibrations"][0]
    assert not c["eligible"] and c["reason"] == "no_reference"

def test_not_locked_is_not_eligible(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0}, air_phase={"eneby": None},
                       sched_phase={"eneby": 2000.0}, air_state="no_lock")
    c = next(c for c in env.record_and_get_session()["air_calibrations"] if c["member"] == "eneby")
    assert not c["eligible"] and c["reason"] == "air_not_locked"

def test_phase_wrap_is_unwrapped(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 9_950.0, "eneby": 165.0},
                       air_phase={"eneby": 9_995.0}, sched_phase={"eneby": 9_990.0})
    c = next(c for c in env.record_and_get_session()["air_calibrations"] if c["member"] == "eneby")
    assert c["acoustic_lag_ms"] == pytest.approx(215.0) and c["latency_ms"] == pytest.approx(210.0)

def test_save_stores_entry_and_applies_it(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0},
                       air_phase={"eneby": 2012.0}, sched_phase={"eneby": 2000.0})
    env.record_and_get_session()
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/air-calibrations", json={"members": ["eneby"]})
    assert r.status_code == 200
    entry = env.player_config("eneby")["air_to_sound_latency"][0]
    assert entry["latency_ms"] == pytest.approx(203.0) and entry["reference"] == "kiosk" and entry["source"] == "sync_wizard"
    assert env.sent("eneby")[-1] == {"cmd": "set_air_calibration", "latency_ms": 203.0}
```

The `sendspin_sync` core must not change: its existing boundary tests (`tests/unit/sendspin_sync`) run unchanged in Step 4, and the review checks `git diff --stat -- src/sendspin_sync` is empty.

(`sync_air_env` stubs the estimator results and the air/daemon folds through the service seams; `click_world` writes the click track (`sendspin_sync.click_track.render_pcm`) through a `WrittenEnvelope` with a chosen schedule.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: move the fold helpers (re-export from the script for its tests); `AirClickFold` on the service (armed per bridge member when its solo starts, frames from that link's `MediaPacket`s with `t_air` from the tracker's anchor, finished at the solo's end); the daemon's `fold` (cells' `T = s + D_out`, rows `(T/1e6, 2**w)`, `click_phase_ms(rows, cell_s, 0.0, comb_delay_s)`); the watcher thread in `start_sequence`/`capture_start` (poll `session.sequence` every 100 ms; stamp RAW at `solo` start/`gap` start per member; arm/finish folds; send `AirFold` to the daemon with the slot span and collect `air_sensor.fold` within 2 s); after `record_sequence`, compute candidates (reference = the session's reference; `A = unwrap_lag_ms(φ_spk, φ_ref)`, `H_cal = unwrap(φ_air − φ_T)` into ±5000 ms; `L_as = A − H_cal`; eligibility per the spec; `delay_report_ms` from the device's `bt_reported_delay_ms` as a hint); the save use case writes through `upsert_air_latency` under `config_lock` and hot-applies; the router + contract + StepTune UI (rows with the value, the hint and a "Save as this speaker's latency" checkbox; en/ru); docs section.
- [ ] **Step 4: Run** the three test files, `tests/unit/sendspin_sync`, `tests/unit/services/bluetooth/test_a2dp_air_phase_script.py`, `tests/unit/api/test_sync_api.py` → PASS; regenerate OpenAPI and the SPA client; `npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(sync): calibrate a speaker's air-to-sound latency in the tuning wizard`.

### Task A17: Stand validation with a microphone, decision note, docs, changelog

**Files:**
- Modify: `docs/plans/2026-10-28-air-sensor-design.md` (append "Stage A validation note"), `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}configuration.md` (the switch), `CHANGELOG.md`
- Create (not committed): `scratch/air_validation/` (recordings, CSV)

**Interfaces:**
- Consumes: the HAOS stand (ENEBY20 on hci1, `PLAYOUT_SYNC=measure`, `EXPERIMENTAL_AIR_SENSOR=true`, `PREFER_SBC_CODEC=true`), the desktop reference bridge in `off`, a headset microphone, the coherent click estimator, `scripts/a2dp_air_phase.py` and a `btmon` capture for cross-checks; the Pi 4 for CPU; the PipeWire Docker VM for the suspend check.
- Produces: the acceptance result and the recalibrated constants (R-9 gate, R-10 N) appended to the spec; stage B can start from it.

- [ ] **Step 1: Calibrate** ENEBY20 once in the wizard (reference = the desktop reference bridge's speaker, or a Sendspin web player on a headset); save; note `L_as`, `spread_ms` and the Delay Report hint (absent on ENEBY20).
- [ ] **Step 2: ≥ 8 cold starts** with the gate experiment's protocol (stop, ~6.5 s gap, new stream; MA mutes alternating the solo; one continuous headset recording; music for 60 s then the click track for 40 s per start). Per start `i`: `M_i` = ENEBY − reference from the estimator on the click part; `H_i` = the median `host_placement_ms` reported while ENEBY was soloed (from the status poll at 1 Hz, saved to CSV with RAW stamps); lock share during music and clicks.
- [ ] **Step 3: Acceptance.** `max_i |(M_i − H_i) − median(M − H)| ≤ 5 ms` over ≥ 8 starts, and the sensor `locked` ≥ 80 % of the solo time on music and on clicks. Also: `median(M − H)` vs the saved `L_as` (report the difference; it is the reference's error plus D_out bookkeeping), the PSR distributions of confirmed vs rejected windows (DEBUG log with `SENDSPIN_AIR_DEBUG=1`), and one `btmon` cross-check of `H` against `scripts/a2dp_air_phase.py` on the click part (≤ 3 ms).
- [ ] **Step 4: Extra runs:** 3 "seek" cycles and 3 reconnects (report `M − H` spread); 8 cycles with `pactl suspend-sink` in the gaps on the PipeWire VM (the `bt_sink_suspend_in_gaps` issue fires; `air_sensor.epochs` grows); an AAC speaker or `PREFER_SBC_CODEC=false` (state `unsupported_codec`, guidance shown); the add-on without `NET_RAW` if the stand allows (state `unavailable: no_cap_net_raw`); CPU on the Pi 4 with one speaker: `pidstat` of the parent and the daemon with and without the switch (budget: ≤ 3 % per speaker above `measure`), plus `uv run python scripts/air_sensor_bench.py` on the Pi.
- [ ] **Step 5: Decision note** appended to the spec:

```markdown
## Stage A validation note (YYYY-MM-DD, HAOS stand, 3.1.0-beta.N)

- Calibration: L_as = … ms (spread … ms), reference …
- Cold starts (n = …): M − H residuals …; max |residual − median| = … ms → PASS/FAIL (±5 ms).
- Lock share: music … %, clicks … %. PSR confirmed median …, rejected max … → gate kept at / moved to ….
- Seek / reconnect: M − H spread … / … ms.
- Suspend check on PipeWire: fired yes/no; epochs ….
- Codec / capability fallbacks: ….
- CPU (Pi 4, one speaker): parent … %, daemon … % (budget 3 %).
- Changes for stage B: dead band from σ(H) = … ms; ….
```

- [ ] **Step 6: Docs and changelog.** `bluetooth-speakers.md` (en/ru): what the air sensor is, requirements (SBC, `NET_RAW`, measure), what the numbers mean, how to calibrate; `configuration.md`: the switch. `CHANGELOG.md` `### Added`: "Experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`, off by default): with Playout sync = measure and SBC, the bridge reports when each speaker's audio actually leaves over the air and how far that is from its schedule; the group sync tuning wizard can calibrate a speaker's latency from the air to the sound. Playback is not changed." Lint with `uv run python scripts/lint_changelog.py CHANGELOG.md`.
- [ ] **Step 7: Commit** only the spec, docs and changelog: `docs(sync): air sensor stage A validation note and user docs`; delete `scratch/`.

---

## Stage B — the engine corrects from the air sensor (outline)

Not detailed until Task A17's note exists; each item becomes a task in the A-task format then.

- **B1: Effective rule for `loop`.** `air_sensor_effective` accepts `loop`; spawn params and status unchanged.
- **B2: Bias estimator.** In the engine, a pure `AirBias` filter: input `(t, E, confidence)` from the air sensor (≈ 4 s late, 1 Hz) and the pulsesink error history `e_pulse(t)` at the same audio (aligned by `x`, not by arrival time); output `b = median(e_pulse − E)` with a time constant ≥ 30 s; frozen when the air sensor is not `locked` or confidence < 0.5. Tests with the A10 simulation extended by a pulsesink sensor with a per-start bias of ±15 ms (Stage 1 finding).
- **B3: Controller input.** The loop acts on `e_pulse − b` (the inner loop keeps the pulsesink cadence); a persistent `|b|` beyond the re-derived dead band (from σ(H) of A17) triggers a placement jump under R14's persistence rules; measure mode reports the corrected error but still only jumps at 20 ms.
- **B4: Output delay = `L_as`.** With a calibration, the speaker's output delay follows `L_as` through the Stage 4 offset path (source `air_calibration`), so `E = H` and the target is `H = 0`; the playout spec's Stage 4 precedence table gains the entry.
- **B5: Completion anchor (optional).** If open question 3 shows a lower σ for completion-anchored `H`, switch the anchor together with a forced re-calibration (entries carry the anchor).
- **B6: Validation.** The playout spec's S1–S3 on the stand with the loop driven by the air bias; cold-start spread target < 5 ms.
