# Air sensor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Revision 2 (2026-10-28): revised after the code-checked design review. The controller's rulings on that review are in the spec; this plan implements them.

**Goal:** For every SBC Bluetooth speaker in `PLAYOUT_SYNC=measure` with `EXPERIMENTAL_AIR_SENSOR` on, the bridge reports when the audio it wrote actually left over the air: the host-side placement `H = t_air − s`, the air error `E = H + L_as − D_out` once the speaker's air-to-sound latency is calibrated in the sync wizard, a lock state and a confidence, in status, API, UI, logs and diagnostics. Nothing changes playback; with the switch off nothing of this exists. Separately, the bridge warns when the host suspends the Bluetooth sink in gaps.

**Architecture:** With the switch on, an HCI monitor hub on a reader thread (kernel timestamps, drop counters) owns the monitor socket, serves AVRCP as one consumer and feeds the existing `A2dpAirParser` with RAW seconds; a per-link tracker turns SBC scale factors into an air envelope with send-anchored times, anchor segments (PA skips) and epochs, and one sender task per speaker ships it in small batches to that speaker's daemon (`air_envelope`). The daemon taps the input PCM the playout engine writes (with `s`, `T = s + D_out`, kind and mute per cell), correlates both off its event loop (normalised cross-correlation, peak/side-lobe gate, parabolic sub-frame) and runs a lock state machine; the result is the status key `air_sensor`. The wizard calibration folds the click track on both sides (air in the parent, schedule in the daemon) and subtracts the result from the acoustic lag against the reference member.

**Tech Stack:** Python 3.13/3.14 stdlib only (`array`, `operator`, `socket.recvmsg`/`getsockopt`), the existing `a2dp_air` parser, PyGObject/GStreamer (only through the existing `PlayoutPlayer`), `pulsectl_asyncio`, FastAPI + Pydantic v2, pytest (+ xdist), Vue 3 + TS + vitest; libsbc 1.3.1 (fixture builder only, never at runtime).

**Spec:** [`docs/plans/2026-10-28-air-sensor-design.md`](2026-10-28-air-sensor-design.md). Every task's requirements include the spec; rulings are cited as R-n.

## Global Constraints

Copied from the spec; every task's requirements include them.

* **Switch off = nothing.** With `EXPERIMENTAL_AIR_SENSOR=false` the existing `HciAvrcpMonitor` path runs unchanged (its tests unchanged), no hub thread, no tap, no air command; the daemon never publishes an `air_sensor` status key, and `DeviceStatus.air_sensor` and the API's `timing.air_sensor` are `null` (null, not an absent key: simpler with the repo's response models). Every task that adds a hook has a switch-off test. The switch needs a bridge restart (`RESTART_REQUIRED`, R-17).
* Times: `CLOCK_MONOTONIC_RAW` µs in the sensor. Kernel monitor stamps (`SO_TIMESTAMPNS`) are `CLOCK_REALTIME`; convert with a bracketed `real − raw` offset taken every 250 ms (bracket ≤ 50 µs, 3 tries), interpolated; an offset step > 5 ms = clock step = new epochs. Without kernel stamps use RAW at `recvmsg` return, flagged `recv` (R-3). The parser is fed RAW seconds (`t_raw_us / 1e6`); no unix → RAW conversion exists downstream; fixtures are shifted by one constant.
* Drops: `SO_RXQ_OVFL` cmsg if delivered, else `getsockopt(SOL_SOCKET, SO_MEMINFO)[SK_MEMINFO_DROPS]` polled every 250 ms; an increase = new epoch on every link.
* Definitions: `s(x)` = `ScheduleMap.at(x)` (port time, output delay already subtracted); `D_out` = the daemon's current output delay; `T = s + D_out` (stored per cell); `d_enc = 4.5·M/rate`; `d_comb = ((comb lag + 9·M) / 2)/f_in` (comb plus look-back, controller ruling after A10); **`H = t_j − s_i − (d_enc − d_comb)`** for matched energy cells (`t_j = u_j + α`); `E = H + L_as − D_out` (positive = late; null without calibration).
* Air side: SBC only; band = sub-bands `b ≥ 1` with centre `(b+½)·rate/(2M)` in 2.5–8.5 kHz, else `{1}`; energy `round(8·log2(1 + Σ_ch Σ_b 2^sf))` clipped 0…254, 255 = unparsable frame; send anchor = running median of the last 64 `t_send − u` of the anchor segment (R-5: canonical; completion lag is telemetry). **Contiguous RTP sequence + timestamp step (PA "Skipping") = anchor reset in the same epoch** (media time `u0_us` includes the skip; the daemon keeps `L` in media time and re-verifies ±5 cells); **sequence gap, > 1 s between sends, SSRC change, link reset, clock step, drop increase, hub reopen = new epoch** (re-acquire). Batches ≤ 128 frames every 250 ms; ≤ 400 frames/s per device; one sender task + queue of 16 per device, drop-oldest, non-blocking writes (write buffer > 64 KiB = skip); `A2dpAirParser(check_crc="first")` on the live path (R-4); CPU guard per streaming speaker: `cpu_fraction` > 0.04 × streaming links (R-19) for two 30 s windows → `unavailable: cpu_budget`.
* IPC: additive commands `air_envelope`, `air_source`, `set_air_calibration`, `air_fold`, payload `v: 1`, the usual `protocol_version: 2` envelope; no `IPC_PROTOCOL_VERSION` bump; the gate is the daemon's "sensor was built" signal: the parent sends air commands only while the daemon's status carries `air_sensor.accepts` containing 1, published only by a daemon that built its `AirSensor` and withdrawn on `fall_back_to_legacy` (R-6). Invalid messages are dropped and counted, one WARNING per 10 min.
* Written side: the tap works on the **input** PCM of `_push` (S16, S24 3-byte, S32 at 44.1/48 kHz, reduced to 16 bits at C speed; 88.2/96 kHz and other formats → `unsupported_input`); cells of 128 input frames; envelope `log2(1 + max|x[n] − x[n−d]|)` over the cell and the `9·M` input frames before it (the SBC frame's span, `M` from the air side, 72 for M = 8; controller ruling after A10, pending real-music confirmation in A17), `d = 3` at 44.1/48 kHz (R-7); per cell `s`, `T`, kind (`program`/`silence`, the `ScheduleMap` kinds); muted = the RAW mute timeline (user mute, volume 0, tail mute) at the cell's port time `s_i`, resolved at read time (R-8); the written fold ignores mute; lookup never assumes monotonic `s`; ring 20 s; tap reset + `reopen` event in `adopt_output` (every stream start and seek).
* Correlation: window 3 s, hop 1 s, computed off the daemon loop (executor); valid when ≥ 90 % air cells present, ≥ 80 % written cells program and ≤ 20 % muted (R-16); flatness `σ_a ≥ 2`, `σ_w ≥ 0.25`; NCC per lag; acquisition **±500 ms** around the prior (`δ₀ = 10 ms`), tracking ±20 cells, ±86 after underrun/pa_step/stall, ±5 after an anchor reset (R-15); PSR = peak / max outside ±2 cells; reject edge peaks; confirm when `PSR ≥ 1.17` (**provisional until A17**, R-9) and `ρ ≥ 0.25`; parabolic `λ` clamped ±0.5; a move > 3 cells needs a second confirmed window within 1 cell.
* Lock: `locked` ≤ 5 s since the last confirmed window, `holding` 5–20 s, `no_lock` after 20 s (R-10); `acquiring` on a new epoch or reopen; confidence `clip((PSR_med − 1.17)/0.23, 0, 1) · f_ok · decay` (R-11); jitter = 1.4826·MAD of `H` over 30 s; reported `H` = median of the last 5 confirmed windows.
* Effective rule: on iff the switch, effective playout mode `measure` and backend `bluetooth_a2dp`; reasons `switch_off` (→ no key), `needs_measure`, `loop_not_supported_yet`, `not_bluetooth`.
* Calibration: per player `air_to_sound_latency` entries keyed by (codec, SBC rate, adapter MAC), ≤ 8, `latency_ms` 0…2000 (R-12); needs a reference member (R-14); no live-lock requirement; air span `[start + SETTLE_S + h, end − TAIL_S + h]` with `h` = host latency + 20 ms; `L_as = A − H_cal`, `A = unwrap_lag_ms(φ_ac(speaker), φ_ac(ref))`, `H_cal = unwrap(φ_air − φ_T)` into ±5 s.
* The engine is never changed by the sensor in stage A; tap and correlator exceptions disable the sensor for that stream (`unavailable: internal_error`) and never reach the feeder.
* The `sendspin_sync` core is not changed (its tests stay green); the wizard integration lives in `application/sync_tuning.py`.
* The host's audio settings are never changed (the suspend check only reads).
* Config keys go into `DEFAULT_CONFIG`, `config/schema.json`, `BridgeConfig` in `application/models/config.py`, `_ALLOWED_POST_CONFIG_KEYS` (+ POST normalisation) in `application/config.py`, the `web_ui_only_keys` tuple in `scripts/translate_ha_config.py` and `services/infrastructure/config_diff.py`; per-player fields in `config/players.py` and the player definition of the schema.
* Logs: INFO on state changes (≤ 1 per minute per kind) and calibration applied; WARNING once per 10 min per kind; DEBUG 10 s summary (per window with `SENDSPIN_AIR_DEBUG=1`: `t_mid`, `s_mid`, `H`, PSR). Never log raw monitor records.
* Hardware for measurements: the desktop and the test VM 105 (x86, 2 vCPU) for benches; the HAOS stand only in A17. No Raspberry Pi is available; Pi figures are unverified and say so.
* Gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/ src/sendspin_sync/`; `uv run ruff check . && uv run ruff format --check .`; `cd ui && npm test && npm run build`; after API model changes `uv run python scripts/export_openapi.py` then `cd ui && npm run gen:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md` (R5–R9). TDD: red before green; never edit an existing test to make it pass (`CLAUDE.md`).
* Do not touch the bridge on port 8080 or Music Assistant on 8095/8097 while developing; live work happens only in Task A3a Step 5 (passive monitor read on a dev machine), A4 Step 5 (bench script on the desktop or the test VM 105) and Task A17 (the stands).

## Review Focus

1. **Time bases and signs.** REALTIME → RAW, RAW-fed parser, the send anchor and its segments, `d_enc − d_comb` (subtracted), `T = s + D_out`: a sign or unit slip moves `H` by milliseconds without failing a shape test. A9's fake and A10's truth are built at the PCM instant independently of the formula; A10 asserts a mean bias within ±0.5 ms. (Tasks A3a, A4, A9, A10)
2. **Switch off is byte-for-byte today.** AVRCP path, daemon status, API and spawn params unchanged with the switch off; explicit tests in A3b, A6b, A11, A12. (Tasks A3b, A6b, A11, A12)
3. **Untrusted input and bounds.** Every field of every air message validated in the daemon; hub, tracker and sender bound rates, queues and memory; nothing raw is logged; a real pipe test. (Tasks A3b, A4, A5, A6, A11b)
4. **No effect on playback.** The tap must not block the feeder, exceptions are contained, the correlation runs off the daemon loop, every output reopen resets the tap, a fallback withdraws `accepts`. (Tasks A7, A9, A11)
5. **Calibration provenance.** No calibration without a reference member, an SBC air fold with clicks inside one epoch and a written fold; the air span is offset by the host latency; the stored entry carries codec, rate, adapter, reference and time. (Tasks A15, A16a–c)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/sink_suspend_policy.py` | **new** static probe + observation rule (A1) |
| `src/sendspin_bridge/services/audio/sink_monitor.py` | `add_state_listener` (A1) |
| `src/sendspin_bridge/services/bluetooth/hci_socket.py` | timestamps, `SO_MEMINFO` drops, `DropCounter`, socket options (A3a) |
| `src/sendspin_bridge/services/bluetooth/hci_clock.py` | **new** `RawRealtimeMap` (A3a) |
| `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py` | **new** reader thread, consumers (A3b) |
| `src/sendspin_bridge/services/bluetooth/hci_avrcp_monitor.py` | `AvrcpConsumer` + `_process_record` (index-aware); legacy path unchanged (A3b) |
| `src/sendspin_bridge/services/bluetooth/a2dp_air.py` | `check_crc="first"` option (A4) |
| `src/sendspin_bridge/services/bluetooth/a2dp_air_envelope.py` | **new** band, energy, anchor segments, epochs, `AirLinkTracker`, `AirBatch` (A4) |
| `src/sendspin_bridge/services/bluetooth/a2dp_air_fold.py` | **new** fold helpers moved from the phase script (A16a) |
| `src/sendspin_bridge/services/bluetooth/air_sensor_service.py` | **new** parent wiring, lifecycle, folds (A6, A6b, A16b) |
| `src/sendspin_bridge/services/ipc/commands.py` | `AirEnvelope`, `AirSource`, `SetAirCalibration`, `AirFold` (A5) |
| `src/sendspin_bridge/services/ipc/daemon_process.py`, `bridge_daemon.py` | dispatch, params, status, lifecycle (A5, A11, A16b) |
| `src/sendspin_bridge/services/audio/playout/air_tap.py` | **new** `WrittenEnvelope` (A7) |
| `src/sendspin_bridge/services/audio/playout/air_correlator.py` | **new** NCC, gate, refinement, reference band-pass (A8) |
| `src/sendspin_bridge/services/audio/playout/air_sensor.py` | **new** `AirStream`, lock machine, `AirSensor`, written fold (A9, A16b) |
| `src/sendspin_bridge/services/audio/playout/gst_output.py`, `engine.py` | tap hook, reopen/engine events, read-only schedule accessors (A7, A11) |
| `src/sendspin_bridge/bridge/client.py`, `bridge/orchestrator.py`, `services/lifecycle/reconfig_orchestrator.py` | switch-gated start, status key, spawn params, subscriptions (A1, A3b, A6, A6b, A11, A12) |
| `src/sendspin_bridge/config/__init__.py`, `config/schema.json`, `config/players.py`, `application/models/config.py`, `application/config.py`, `scripts/translate_ha_config.py`, `services/infrastructure/config_diff.py` | keys (A2, A15) |
| `src/sendspin_bridge/application/models/devices.py`, `application/devices.py`, `api/routers/devices.py` | `AirSensorTiming`, calibration API (A12, A15) |
| `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py`, `recovery_assistant.py` | issues (A1, A13) |
| `src/sendspin_bridge/application/sync_tuning.py`, `application/models/sync.py`, `api/routers/sync.py`, `docs/contracts/sync-contract-v1.json` | wizard calibration (A16c) |
| `ui/src/components/devices/DeviceTimingPanel.vue`, `ui/src/utils/deviceChips.ts`, `ui/src/settings/layout.ts`, `ui/src/components/sync/StepTune.vue`, `ui/src/i18n/{en,ru}.json`, `ui/src/api/*` | UI (A14, A15, A16c) |
| `scripts/a2dp_air_phase.py` | imports the fold helpers (A16a) |
| `scripts/air_sensor_bench.py` | **new** CPU bench (A4) |
| `scripts/air_envelope_corr.py` | **new** offline correlation tool from the morning script (A8b) |
| `tests/fixtures/a2dp_air/build_air_music_fixture.py`, `air_music.btsnoop.gz`, `air_music_truth.json.gz` | **new** synthetic-music capture with PA-style skips and PCM-instant truth (A10) |
| `tests/integration/test_air_ipc_pipe.py`, `tests/integration/air_ipc_child.py` | **new** real-pipe IPC test (A11b) |
| `tests/unit/...` (one new file per task, named in each task) | **new** tests |
| `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}group-sync-tuning.md`, `{,ru/}configuration.md`, `CHANGELOG.md`, the spec (decision note) | docs (A1, A16c, A17) |

Conventions: Python tests under `tests/unit/...`; parent fixtures from `tests/conftest.py` (`api_client`, `tmp_config`, `bridge_loop`); GStreamer tests use the `requires_gst` pattern of `tests/unit/services/audio/playout/test_gst_output.py`. Fixtures named in a task's tests that do not exist yet are defined in that task's test file (no shared fixture is assumed unless the task names where it lives). Use cases raise `UseCaseError(status, code, detail)`; routers use `Depends(require_principal)` and `PROBLEM_RESPONSES`. Run one file with `uv run pytest <path> -q -p no:cacheprovider -n0`. Each task is shippable on its own and its tests run without hardware.

Order and dependencies: A1 (independent) · A2 · A3a → A3b → A4 → A5 → A6 → A6b · A7 → A8 → A8b · A9 (needs A5, A7, A8) → A10 (needs A4, A5, A7–A9) → A11 (needs A5, A9) → A11b → A12 → A13 → A14 · A15 (needs A12) → A16a → A16b (needs A6, A11) → A16c (needs A15) → A17.

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
    nodes[0]["info"]["props"]["session.suspend-timeout-seconds"] = 3
    p = parse_pw_dump(nodes)
    assert (p.suspends, p.timeout_s, p.source) == (True, 3.0, "wireplumber")

def test_absent_wireplumber_property_means_the_5s_default():
    nodes = [{"type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "bluez_output.FC_58.1"}}}]
    p = parse_pw_dump(nodes)
    assert (p.suspends, p.timeout_s, p.source) == (True, 5.0, "wireplumber")

def test_no_bluez_nodes_is_unknown():
    assert parse_pw_dump([]).suspends is None

@pytest.mark.asyncio
async def test_missing_pw_dump_falls_back_to_unknown():
    async def run(argv):
        if argv[0] == "pactl":
            return 0, "Server Name: PulseAudio (on PipeWire 1.0.5)\n"
        raise FileNotFoundError(argv[0])                                  # pw-dump not in the container
    p = await probe_suspend_policy(run=run)
    assert (p.server, p.suspends, p.source) == ("pipewire", None, "unknown")

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

def _device(**extra):
    # the recovery assistant reads device snapshots by attribute (see test_recovery_assistant.py)
    return SimpleNamespace(player_name="ENEBY20", bluetooth_connected=True, has_sink=True,
                           server_connected=True, health_summary={}, **extra)

def test_recovery_assistant_reports_a_device_with_observed_suspends():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues
    dev = _device(host_sink_suspends={"observed": 2, "warn": True, "reason": "observed_suspend", "policy": None})
    assert "bt_sink_suspend_in_gaps" in [i.key for i in _build_device_issues([dev])]

def test_client_marks_its_own_suspend_requests(client_factory):
    c = client_factory()
    assert c._suspend_requested_at is None                               # created in __init__
    c._mark_suspend_requested(now=100.0)
    assert c._bridge_requested_suspend(now=101.5) is True and c._bridge_requested_suspend(now=103.0) is False
```

(`SimpleNamespace` from `types`; `client_factory` builds a `SendspinClient` the way `tests/unit/bridge` tests already do — search there for the constructor helper.)

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/audio/test_sink_suspend_policy.py tests/unit/services/diagnostics/test_air_guidance.py -q -p no:cacheprovider -n0` → FAIL.
- [ ] **Step 3: Implement** the parsers and `probe_suspend_policy` (`pactl info`, then `pactl list modules short` or `pw-dump` with a 3 s timeout each, all through `run`; a missing or failing `pw-dump` gives `unknown`, never an exception; an absent `session.suspend-timeout-seconds` on a bluez node is WirePlumber's default 5 s); `SuspendObserver` with the rules above; `SinkMonitor.add_state_listener` (listeners called after the existing callbacks, exceptions logged at DEBUG and swallowed); in `SendspinClient`, a new attribute `self._suspend_requested_at: float | None = None` created in `__init__`, set by `_mark_suspend_requested()` at every place the client itself suspends its sink (power save, standby; grep `asuspend_sink`/`suspend_sink` in `bridge/client.py`), and a listener that passes `bridge_requested = status.bt_power_save or status.bt_standby or self._bridge_requested_suspend(now)` (within 2 s of the mark) and `connected = status.bluetooth_connected`, and publishes `host_sink_suspends` (add to `DeviceStatus` and `_ALLOWED_STATUS_KEYS`); the orchestrator runs the probe at start and every 10 min and hands the policy to every client. Registry entry (priority 34, after `pa_socket_*`) and a producer in `recovery_assistant.py` with a remediation pointing to the docs page; i18n texts (en, ru) that say the bridge never changes host settings. Docs section "Bluetooth sink suspend" with the WirePlumber 0.4 (`bluez_monitor.rules`, `["session.suspend-timeout-seconds"] = 0`) and 0.5 (`monitor.bluez.rules`, `session.suspend-timeout-seconds = 0`) snippets and the PulseAudio `unload-module module-suspend-on-idle` note.
- [ ] **Step 4: Run** both files plus `tests/unit/services/diagnostics tests/unit/services/audio/test_sink_monitor*.py -q -p no:cacheprovider` → PASS (`test_issue_registry_keys.py` checks every registered key has i18n texts).
- [ ] **Step 5: Changelog** `### Added` — "Diagnostics warn when the host suspends a Bluetooth speaker's audio sink between songs (PipeWire's suspend timeout, PulseAudio's suspend-on-idle); speakers can shift by tens of ms after such a pause. The bridge never changes the host setting."
- [ ] **Step 6: Commit** `feat(diagnostics): warn when the host suspends the Bluetooth sink in gaps`.

### Task A2: `EXPERIMENTAL_AIR_SENSOR` config key and the effective rule

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`DEFAULT_CONFIG`, `air_sensor_effective`), `src/sendspin_bridge/config/schema.json`, `src/sendspin_bridge/application/models/config.py` (`BridgeConfig`), `src/sendspin_bridge/application/config.py` (`_ALLOWED_POST_CONFIG_KEYS`, bool normalisation), `scripts/translate_ha_config.py` (`web_ui_only_keys`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (`_RESTART_REQUIRED_FIELDS`, R-17)
- Test: `tests/unit/config/test_air_sensor_config.py`, `tests/integration/test_translate_ha_config.py` (one new test)

**Interfaces:**

```python
# config/__init__.py
def air_sensor_effective(player_cfg: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[bool, str | None]:
    """(True, None) when EXPERIMENTAL_AIR_SENSOR is on, the player's effective playout mode is
    'measure' and its backend type is 'bluetooth_a2dp'; else (False, reason) with reason in
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
BT = {"backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}}


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


def test_change_needs_a_bridge_restart():
    # R-17: the switch decides at start which monitor path owns the HCI socket
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
    actions = diff_configs({"EXPERIMENTAL_AIR_SENSOR": False}, {"EXPERIMENTAL_AIR_SENSOR": True})
    assert [a.kind for a in actions] == [ActionKind.RESTART_REQUIRED]


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
- [ ] **Step 3: Implement** — `"EXPERIMENTAL_AIR_SENSOR": False` next to `PLAYOUT_SYNC` with a comment pointing at the spec; schema `{"type": "boolean", "default": false, "description": "Experimental: measure when audio leaves over the air (SBC, needs PLAYOUT_SYNC=measure and CAP_NET_RAW). Does not change playback."}`; `BridgeConfig.EXPERIMENTAL_AIR_SENSOR: StrictBool | None = False` with the docstring; POST key + bool normalisation; `_RESTART_REQUIRED_FIELDS`; `web_ui_only_keys`; `air_sensor_effective` using `effective_playout_mode` and `config.players.BLUETOOTH`.
- [ ] **Step 4: Run** the two files and `uv run pytest tests/unit/config -q -p no:cacheprovider` → PASS.
- [ ] **Step 5: Commit** `feat(config): EXPERIMENTAL_AIR_SENSOR switch (off by default)`.

### Task A3a: Monitor primitives — kernel timestamps, drop counters, REALTIME → RAW

**Files:**
- Modify: `src/sendspin_bridge/services/bluetooth/hci_socket.py` (constants, `parse_monitor_cmsgs`, `read_meminfo_drops`, `enable_monitor_sockopts`)
- Create: `src/sendspin_bridge/services/bluetooth/hci_clock.py` (`RawRealtimeMap`)
- Test: `tests/unit/services/bluetooth/test_hci_monitor_primitives.py`

Pure helpers only; nothing calls them yet, so this ships without any behaviour change.

**Interfaces:**

```python
# hci_socket.py
SO_TIMESTAMPNS: int = 35
SCM_TIMESTAMPNS: int = SO_TIMESTAMPNS
SO_RXQ_OVFL: int = 40
SO_MEMINFO: int = 55
SK_MEMINFO_DROPS: int = 8       # index into the u32 array SO_MEMINFO returns
SK_MEMINFO_VARS: int = 9

def enable_monitor_sockopts(sock: socket.socket, *, rcvbuf: int = 4 << 20) -> dict[str, bool]:
    """Set SO_TIMESTAMPNS, SO_RXQ_OVFL and the receive buffer (SO_RCVBUFFORCE, else SO_RCVBUF); returns what succeeded."""
def parse_monitor_cmsgs(ancdata: list[tuple[int, int, bytes]]) -> tuple[int | None, int | None]:
    """(realtime_ns, rxq_ovfl_drops) from recvmsg ancillary data; None for what is absent or malformed."""
def read_meminfo_drops(sock: socket.socket) -> int | None:
    """Cumulative SK_MEMINFO_DROPS via getsockopt(SOL_SOCKET, SO_MEMINFO); None when unsupported."""

class DropCounter:
    """Prefers the cmsg count once one has been seen; otherwise polls SO_MEMINFO (every 250 ms)."""
    def __init__(self, *, poll: Callable[[], int | None]) -> None: ...
    def on_cmsg(self, drops: int | None) -> None: ...
    def tick(self) -> None: ...
    @property
    def drops(self) -> int | None: ...
    @property
    def source(self) -> Literal["cmsg", "meminfo", "none"]: ...

# hci_clock.py
class RawRealtimeMap:
    """REALTIME ns -> RAW µs from bracketed pairs (bracket <= 50 µs, 3 tries); detects steps > 5 ms."""
    def __init__(self, *, raw_ns=..., real_ns=..., max_bracket_ns: int = 50_000, step_us: float = 5_000.0) -> None: ...
    def sample(self) -> bool: ...
    def to_raw_us(self, real_ns: int) -> float | None   # None until two pairs bracket it
    @property
    def epoch(self) -> int: ...
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/bluetooth/test_hci_monitor_primitives.py
import socket, struct
import pytest
from sendspin_bridge.services.bluetooth import hci_socket as hs
from sendspin_bridge.services.bluetooth.hci_clock import RawRealtimeMap

def ts_cmsg(ns): return (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", ns // 10**9, ns % 10**9))
def ovfl_cmsg(n): return (socket.SOL_SOCKET, hs.SO_RXQ_OVFL, struct.pack("I", n))

def test_cmsgs_parse_timestamp_and_drops():
    assert hs.parse_monitor_cmsgs([ts_cmsg(1_700_000_000_123_456_789), ovfl_cmsg(7)]) == (1_700_000_000_123_456_789, 7)
    assert hs.parse_monitor_cmsgs([ts_cmsg(5)]) == (5, None)          # the expected case on the monitor channel
    assert hs.parse_monitor_cmsgs([]) == (None, None)
    assert hs.parse_monitor_cmsgs([(socket.SOL_SOCKET, hs.SO_RXQ_OVFL, b"\x01")]) == (None, None)

class FakeSock:
    def __init__(self, meminfo): self.meminfo = meminfo
    def getsockopt(self, level, opt, buflen):
        assert (level, opt) == (socket.SOL_SOCKET, hs.SO_MEMINFO)
        if self.meminfo is None: raise OSError(92, "Protocol not available")
        return struct.pack(f"{hs.SK_MEMINFO_VARS}I", *self.meminfo)

def test_meminfo_drops_are_read_from_index_8():
    assert hs.read_meminfo_drops(FakeSock([0, 0, 0, 0, 0, 0, 0, 0, 12])) == 12
    assert hs.read_meminfo_drops(FakeSock(None)) is None

def test_drop_counter_uses_meminfo_without_cmsg():
    vals = iter([0, 0, 3]).__next__
    dc = hs.DropCounter(poll=vals)
    dc.on_cmsg(None); dc.tick(); dc.tick(); dc.tick()
    assert (dc.drops, dc.source) == (3, "meminfo")

def test_drop_counter_prefers_cmsg_once_seen():
    dc = hs.DropCounter(poll=lambda: 99)
    dc.on_cmsg(4); dc.tick()
    assert (dc.drops, dc.source) == (4, "cmsg")

def test_drop_counter_none_when_nothing_works():
    dc = hs.DropCounter(poll=lambda: None)
    dc.tick()
    assert (dc.drops, dc.source) == (None, "none")

def test_realtime_to_raw_interpolates_between_pairs():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_000_000_505]).__next__        # 500 ns slew over 1 s
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    assert m.sample() and m.sample()
    assert m.to_raw_us(5_000_500_000_255) == pytest.approx(500_000.005, abs=0.01)

def test_a_wide_bracket_is_retried_then_refused():
    raw = iter([0, 200_000] * 3).__next__                                # 200 µs brackets
    m = RawRealtimeMap(raw_ns=raw, real_ns=lambda: 1)
    assert m.sample() is False

def test_clock_step_starts_a_new_epoch():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_020_000_005]).__next__        # +20 ms step
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    m.sample(); m.sample()
    assert m.epoch == 1

def test_sockopts_report_what_succeeded():
    class S:
        def __init__(self): self.calls = []
        def setsockopt(self, level, opt, val):
            self.calls.append(opt)
            if opt == 33: raise PermissionError(1, "no CAP_NET_ADMIN")    # SO_RCVBUFFORCE
    got = hs.enable_monitor_sockopts(S())
    assert got["timestamps"] and got["rxq_ovfl"] and got["rcvbuf"] and not got["rcvbuf_force"]
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/services/bluetooth/test_hci_monitor_primitives.py -q -p no:cacheprovider -n0` → FAIL.
- [ ] **Step 3: Implement** the helpers (`struct` parsing of `timespec` and the u32 arrays; `getsockopt(..., 4 * SK_MEMINFO_VARS)`; `RawRealtimeMap` with the last two pairs, linear interpolation, the step rule).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Confirm on a dev machine (passive, no audio, no pairing).** With `sudo` (or `setcap cap_net_raw+ep` on a venv python) open a monitor socket with `hs.open_hci_socket(hci_dev=hs.HCI_DEV_NONE, channel=hs.HCI_CHANNEL_MONITOR)`, apply `enable_monitor_sockopts`, read for 10 s with `recvmsg(4096, 256)` and print which cmsgs arrive and `read_meminfo_drops(sock)`. Record in one line under open question 1 of the spec which drop path runs (cmsg or meminfo) and whether `SCM_TIMESTAMPNS` arrives. Both paths stay in the code either way.
- [ ] **Step 6: Commit** `feat(bluetooth): HCI monitor timestamps, drop counters and a REALTIME-to-RAW clock map`.

### Task A3b: `HciMonitorHub` behind the switch; AVRCP as a hub consumer

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/hci_monitor_hub.py`
- Modify: `src/sendspin_bridge/services/bluetooth/hci_avrcp_monitor.py` (add `AvrcpConsumer` and `_process_record(rec, conn_map, tracker)` keyed by `(index, handle)`; `HciAvrcpMonitor`, `_process_packet`, `_dispatch_acl_packet` and the seed keep their current code and contract), `src/sendspin_bridge/bridge/orchestrator.py` (switch off: `get_monitor().start()` exactly as today; switch on: `get_hub().start()` with `AvrcpConsumer` registered, and `get_monitor()` is not started)
- Test: `tests/unit/services/bluetooth/test_hci_monitor_hub.py`, `tests/unit/bridge/test_orchestrator_air_switch.py`; `tests/unit/services/bluetooth/test_hci_avrcp_monitor.py` stays green **unchanged**

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class HubRecord:
    t_raw_us: float
    index: int
    opcode: int
    payload: bytes
    drops: int | None
    timestamp: Literal["kernel", "recv"]

class HubConsumer(Protocol):
    wants_acl_tx: bool
    def on_record(self, rec: HubRecord) -> None: ...                 # hub thread; must not block
    def on_hub_state(self, state: Literal["open", "error", "closed"], reason: str | None) -> None: ...

class HciMonitorHub:
    def __init__(self, *, open_socket=..., seed=..., clock_map: RawRealtimeMap | None = None,
                 drop_counter_factory=..., thread_factory=threading.Thread) -> None: ...
    def add_consumer(self, c: HubConsumer) -> None: ...
    def remove_consumer(self, c: HubConsumer) -> None: ...
    def start(self) -> None: ...                         # idempotent; thread "hci-monitor"
    def stop(self, timeout: float = 2.0) -> None: ...
    @property
    def available(self) -> bool: ...
    @property
    def unavailable_reason(self) -> str | None: ...      # no_cap_net_raw | unsupported_platform | monitor_error
    @property
    def generation(self) -> int: ...                     # increments at every (re)open of the socket
    def adapters(self) -> dict[int, str]: ...            # index -> controller MAC (NEW_INDEX)
    def peer(self, index: int, handle: int) -> str | None: ...
    def counters(self) -> dict[str, float | int | str | None]: ...
        # records, drops, drop_source, clock_steps, cpu_fraction (thread time / wall time, 30 s window), streaming_links

def get_hub() -> HciMonitorHub: ...
```

- [ ] **Step 1: Failing tests** (a `fake_hub_socket` fixture in the test file returns a factory whose socket yields scripted `recvmsg` results `(data, ancdata, flags, addr)`, then raises `socket.timeout` until closed, and records `setsockopt`/`getsockopt` calls; frames are `struct.pack("<HHH", opcode, index, len) + payload`):

```python
# tests/unit/services/bluetooth/test_hci_monitor_hub.py
import socket, struct
import pytest
from sendspin_bridge.services.bluetooth import hci_socket as hs
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HciMonitorHub

def frame(opcode, index, payload): return struct.pack("<HHH", opcode, index, len(payload)) + payload

def consumer(wants=False):
    class C:
        wants_acl_tx = wants
        def __init__(self): self.seen, self.states = [], []
        def on_record(self, rec): self.seen.append(rec)
        def on_hub_state(self, state, reason): self.states.append((state, reason))
    return C()

def test_records_reach_consumers_with_a_raw_time(fake_hub_socket):
    c = consumer()
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_RX_PKT, 1, b"\x46\x00\x04\x00abcd"), [])]),
                        seed=lambda m: None)
    hub.add_consumer(c); hub.start(); hub.stop()
    assert [(r.opcode, r.index, r.timestamp) for r in c.seen] == [(hs.MONITOR_ACL_RX_PKT, 1, "recv")]

def test_acl_tx_only_to_consumers_that_want_it(fake_hub_socket):
    a, b = consumer(False), consumer(True)
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_TX_PKT, 0, b"\x46\x20\x00\x00"), [])]),
                        seed=lambda m: None)
    hub.add_consumer(a); hub.add_consumer(b); hub.start(); hub.stop()
    assert (len(a.seen), len(b.seen)) == (0, 1)

def test_handles_are_per_controller(fake_hub_socket):
    def cc(handle, mac):
        params = bytes([0]) + struct.pack("<H", handle) + mac + bytes([1, 0])
        return bytes([0x03, len(params)]) + params
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, cc(70, bytes([1, 2, 3, 4, 5, 6]))), []),
            (frame(hs.MONITOR_EVENT_PKT, 1, cc(70, bytes([6, 5, 4, 3, 2, 1]))), [])]
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
    c = consumer()
    hub = HciMonitorHub(open_socket=deny, seed=lambda m: None)
    hub.add_consumer(c); hub.start(); hub.stop()
    assert not hub.available and hub.unavailable_reason == "no_cap_net_raw"
    assert ("error", "no_cap_net_raw") in c.states

def test_socket_error_reopens_and_bumps_the_generation(fake_hub_socket_failing_once):
    c = consumer()
    hub = HciMonitorHub(open_socket=fake_hub_socket_failing_once, seed=lambda m: None, backoff_s=0.0)
    hub.add_consumer(c); hub.start(); hub.wait_open(2); hub.stop()
    assert hub.generation == 2 and [s for s, _ in c.states].count("open") == 2

def test_meminfo_drops_are_attached_when_no_cmsg(fake_hub_socket):
    c = consumer()
    sock = fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])], meminfo_drops=5)
    hub = HciMonitorHub(open_socket=sock, seed=lambda m: None)
    hub.add_consumer(c); hub.start(); hub.stop()
    assert c.seen[-1].drops == 5 and hub.counters()["drop_source"] == "meminfo"

def test_a_failing_consumer_does_not_stop_the_others(fake_hub_socket):
    class Bad:
        wants_acl_tx = False
        def on_record(self, rec): raise RuntimeError("boom")
        def on_hub_state(self, s, r): pass
    good = consumer()
    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])] * 3), seed=lambda m: None)
    hub.add_consumer(Bad()); hub.add_consumer(good); hub.start(); hub.stop()
    assert len(good.seen) == 3

def test_avrcp_consumer_routes_by_index_and_handle(fake_hub_socket, monkeypatch):
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m
    noted = []
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.append(mac)})()
    c = m.AvrcpConsumer(tracker=tracker)
    c._conn[(1, 70)] = "AA:AA:AA:AA:AA:AA"
    c._conn[(0, 70)] = "BB:BB:BB:BB:BB:BB"
    avc = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])      # play pressed
    acl = struct.pack("<HH", 70, 4 + len(avc)) + struct.pack("<HH", len(avc), 0x0041) + avc
    c.on_record(type("R", (), {"opcode": hs.MONITOR_ACL_RX_PKT, "index": 1, "payload": acl})())
    assert noted == ["AA:AA:AA:AA:AA:AA"]
```

```python
# tests/unit/bridge/test_orchestrator_air_switch.py
def test_switch_off_starts_the_legacy_avrcp_monitor_only(orchestrator_with_fakes):
    env = orchestrator_with_fakes(EXPERIMENTAL_AIR_SENSOR=False)
    env.start()
    assert env.avrcp_monitor.started and not env.hub.started

def test_switch_on_starts_the_hub_with_the_avrcp_consumer(orchestrator_with_fakes):
    env = orchestrator_with_fakes(EXPERIMENTAL_AIR_SENSOR=True)
    env.start()
    assert env.hub.started and not env.avrcp_monitor.started
    assert any(type(c).__name__ == "AvrcpConsumer" for c in env.hub.consumers)
```

(`orchestrator_with_fakes` patches `get_monitor`/`get_hub` and the rest of the start path the way the existing orchestrator tests do; search `tests/unit/bridge` for the start-up harness and reuse it.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the hub: thread loop `recvmsg(4096, 256)` with a 0.5 s socket timeout; `parse_monitor_packet`; `parse_monitor_cmsgs`; `DropCounter` + `RawRealtimeMap` ticked every 250 ms on the same thread (records stamped `kernel` wait in a ≤ 250 ms deque until a later pair brackets them; R-3 fallback stamps `recv`); index map from `NEW_INDEX`/`DEL_INDEX`; `(index, handle)` → peer from Connection/Disconnection Complete and the kernel seed (keep `dev_id`); fan-out (ACL TX only to `wants_acl_tx`); per-consumer exceptions logged once per 10 min and swallowed; `cpu_fraction` from `time.thread_time()` per 30 s window and `streaming_links` (links with ACL TX in the window); open errors: `PermissionError`/`EPERM`/`EACCES` → `no_cap_net_raw`, non-Linux → `unsupported_platform`, else `monitor_error` with the existing backoff (5 → 60 s); every (re)open increments `generation` and calls `on_hub_state("open")`. `AvrcpConsumer` reuses `_parse_connection_complete`, `_parse_disconnect_complete` and `_parse_avrcp_passthrough`; the legacy `_process_packet(data, handle_to_mac, tracker)` is not changed (the switch-off path keeps it).
- [ ] **Step 4: Run** the two new files and `tests/unit/services/bluetooth/test_hci_avrcp_monitor.py tests/unit/services/bluetooth/test_hci_socket.py` → PASS.
- [ ] **Step 5: Commit** `feat(bluetooth): HCI monitor hub on a reader thread behind EXPERIMENTAL_AIR_SENSOR`. Body: "Switch off keeps the asyncio AVRCP monitor and its contract unchanged; switch on, AVRCP is a hub consumer keyed by (index, handle)."

### Task A4: Air envelope — parser CRC option, band, energy, anchor segments, epochs, `AirLinkTracker`

**Files:**
- Modify: `src/sendspin_bridge/services/bluetooth/a2dp_air.py` (`A2dpAirParser(check_crc: Literal["all", "first"] = "all")`, passed through `_media_packet` to `parse_sbc_frames(..., check_crc=...)` with a new `first_only` mode; default unchanged)
- Create: `src/sendspin_bridge/services/bluetooth/a2dp_air_envelope.py`, `scripts/air_sensor_bench.py`
- Test: `tests/unit/services/bluetooth/test_a2dp_air_envelope.py`; `tests/unit/services/bluetooth/test_a2dp_air.py` stays green unchanged

**Interfaces:**

```python
# a2dp_air.py (addition)
def parse_sbc_frames(payload: bytes, *, check_crc: bool | Literal["first"] = True) -> tuple[tuple[SbcFrame, ...], bool]: ...
class A2dpAirParser:
    def __init__(self, *, max_links: int = 64, max_sdu: int = L2CAP_MAX_SDU, stats: ParseStats | None = None,
                 check_crc: Literal["all", "first"] = "all") -> None: ...

# a2dp_air_envelope.py
MISSING = 255
MAX_BATCH_FRAMES = 128
ANCHOR_PACKETS = 64
SEND_GAP_S = 1.0

def envelope_subbands(rate: int, subbands: int) -> tuple[int, ...]: ...
def frame_envelope(frame: SbcFrame, bands: tuple[int, ...]) -> int: ...          # 0..254

class SendAnchor:
    """Running median of t_send - u over the last ANCHOR_PACKETS packets of an anchor segment (µs)."""
    def add(self, t_send_us: float, u_us: float) -> None: ...
    def value(self) -> float | None: ...
    def reseed(self, alpha_us: float) -> None: ...   # anchor reset: carry α_old − Δu_step forward
    def reset(self) -> None: ...                      # new epoch: empty
MIN_EPOCH_PACKETS = 32

@dataclass(frozen=True, slots=True)
class AirBatch:
    epoch: int
    seq: int                       # first frame's index among the frames sent in the epoch (continuity check)
    u0_us: float                   # RTP media time of the first frame since the epoch start (skips included)
    t0_us: float                   # RAW energy-cell time of the first frame: u + α (d_enc not applied)
    frame_us: float
    rate: int
    subbands: int
    blocks: int
    bands: tuple[int, ...]
    complete_lag_us: float | None
    timestamp: str                 # kernel | recv
    anchor_reset: bool             # first batch of a new anchor segment inside the epoch
    e: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class TrackerState:
    state: Literal["streaming", "no_traffic", "unsupported_codec"]
    reason: str | None
    codec: str | None
    epoch: int
    anchor_segments: int
    frames: int
    in_step: bool

class AirLinkTracker:
    def __init__(self, *, codec_hint: Callable[[], str | None] = lambda: None, max_frames_per_s: int = 400) -> None: ...
    def on_packet(self, p: MediaPacket, *, timestamp: str, in_step: bool) -> None: ...  # p.t_send/t_complete are RAW s
    def on_link_reset(self) -> None: ...           # new epoch
    def on_discontinuity(self) -> None: ...        # clock step, monitor drop increase, hub reopen: new epoch
    def take_batches(self, now_us: float, *, force: bool = False) -> list[AirBatch]: ...
    def state(self, now_us: float) -> TrackerState: ...
```

The parser is fed RAW seconds by the hub (A3b/A6), so `MediaPacket.t_send` and `t_complete` are RAW seconds; fixtures are shifted by one constant. The tracker never sees wall time and does no time conversion.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/bluetooth/test_a2dp_air_envelope.py
from dataclasses import replace
from pathlib import Path
import pytest
from sendspin_bridge.services.bluetooth.a2dp_air import (
    A2dpAirParser, ChannelMode, MediaPacket, SbcFrame, parse_records, read_btsnoop,
)
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import (
    AirLinkTracker, SendAnchor, envelope_subbands, frame_envelope,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air"
RATE = 44100
PKT_S = 7 * 128 / RATE

@pytest.mark.parametrize(("rate", "m", "bands"), [(44100, 8, (1, 2)), (48000, 8, (1, 2)), (44100, 4, (1,)), (48000, 4, (1,)), (32000, 8, (1, 2, 3))])
def test_band_selection(rate, m, bands):
    assert envelope_subbands(rate, m) == bands

def sbc(sf, m=8):
    return SbcFrame(RATE, 16, m, ChannelMode.JOINT_STEREO, False, 53, 119, (False,) * m, (tuple(sf), tuple(sf)))

def test_frame_envelope_is_log_peak_in_eighth_bits():
    assert frame_envelope(sbc([0, 4, 4, 0, 0, 0, 0, 0]), (1, 2)) == 48           # 8·log2(1 + 4·16)
    assert frame_envelope(sbc([15] * 8), (1, 2)) <= 254

def test_parser_first_frame_crc_mode_parses_the_fixture_identically():
    recs = list(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"))
    full = parse_records(recs, A2dpAirParser())
    first = parse_records(recs, A2dpAirParser(check_crc="first"))
    assert [(p.seq, len(p.frames)) for p in full] == [(p.seq, len(p.frames)) for p in first]

def test_send_anchor_is_a_median_robust_to_credit_jitter():
    a = SendAnchor()
    for k in range(64):
        a.add(1_000_000.0 + k * 20_000 + (9000.0 if k % 5 == 0 else 0.0), k * 20_000.0)
    assert a.value() == pytest.approx(1_000_000.0)

def test_send_anchor_follows_slew_with_small_lag():
    a = SendAnchor()
    for k in range(400):
        a.add(k * 20_000 * 1.0005, k * 20_000.0)
    assert a.value() == pytest.approx(399 * 20_000 * 0.0005, abs=330.0)

def packets(n, *, seq0=0, ts0=0, t0=10.0, ssrc=1, frames=7):
    out = []
    for k in range(n):
        fr = tuple(sbc([0, 3 + (k % 4), 2, 0, 0, 0, 0, 0]) for _ in range(frames))
        out.append(MediaPacket(t_send=t0 + k * PKT_S, t_complete=None, index=1, handle=70, cid=0x41,
                               seq=(seq0 + k) & 0xFFFF, rtp_ts=(ts0 + k * frames * 128) & 0xFFFFFFFF, ssrc=ssrc,
                               payload_type=96, codec="sbc", frames=fr))
    return out

def feed(tr, pk):
    for p in pk:
        tr.on_packet(p, timestamp="kernel", in_step=False)

def test_batches_are_contiguous_and_bounded():
    tr = AirLinkTracker()
    feed(tr, packets(60))
    b = tr.take_batches(1e12, force=True)
    assert all(len(x.e) <= 128 for x in b) and sum(len(x.e) for x in b) == 420
    assert [x.seq for x in b] == [i * 128 for i in range(len(b))]
    assert b[1].t0_us == pytest.approx(b[0].t0_us + 128 * b[0].frame_us, abs=1.0)

def test_skip_is_an_anchor_reset_in_the_same_epoch():
    """PA bluez5 'Skipping': contiguous sequence, RTP timestamp jumps, send times continue."""
    tr = AirLinkTracker()
    first = packets(20)
    skipped = 882                                                          # 20 ms of samples never sent
    later = packets(20, seq0=20, ts0=20 * 7 * 128 + skipped, t0=10.0 + 20 * PKT_S)
    feed(tr, first + later)
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {0}
    assert [x.anchor_reset for x in b].count(True) == 1
    reset = next(x for x in b if x.anchor_reset)
    assert reset.seq == 140                                               # sent-frame counter continues
    assert reset.u0_us == pytest.approx((140 * 128 + skipped) / RATE * 1e6)   # media time includes the skip

def test_anchor_is_carried_across_a_skip():
    tr = AirLinkTracker()
    skipped = 882
    feed(tr, packets(80) + packets(3, seq0=80, ts0=80 * 7 * 128 + skipped, t0=10.0 + 80 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    reset = next(x for x in b if x.anchor_reset)
    # the frame right after the skip left at its send time, not 'send time + skip'
    assert reset.t0_us == pytest.approx((10.0 + 80 * PKT_S) * 1e6, abs=200.0)

def test_no_batch_before_32_packets_of_an_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(31))
    assert tr.take_batches(1e12, force=True) == []
    feed(tr, packets(1, seq0=31, ts0=31 * 7 * 128, t0=10.0 + 31 * PKT_S))
    assert tr.take_batches(1e12, force=True)

def test_sequence_gap_is_a_new_epoch_without_any_time_gap():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=41, ts0=41 * 7 * 128, t0=10.0 + 41 * PKT_S))   # seq 40 lost
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}

def test_send_gap_over_1s_is_a_new_epoch_with_contiguous_rtp():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=40, ts0=40 * 7 * 128, t0=10.0 + 40 * PKT_S + 1.2))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}

def test_discontinuity_from_the_hub_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40)); tr.on_discontinuity(); feed(tr, packets(40, seq0=40, ts0=40 * 7 * 128, t0=10.0 + 40 * PKT_S))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}

def test_non_sbc_is_unsupported_after_2s():
    tr = AirLinkTracker(codec_hint=lambda: "aac")
    for k in range(150):
        p = replace(packets(1)[0], codec="unknown", frames=(), t_send=10.0 + k * 0.02, seq=k)
        tr.on_packet(p, timestamp="kernel", in_step=False)
    s = tr.state((10.0 + 149 * 0.02) * 1e6)
    assert s.state == "unsupported_codec" and s.reason == "codec:aac"

def test_fixture_cells_fold_to_the_phase_tool_phase():
    from scripts.a2dp_air_phase import analyze, click_phase_ms, sbc_encoder_delay_samples
    reports, _ = analyze(FIX / "eneby_clicks.btsnoop.gz")
    pk = [p for p in parse_records(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"), A2dpAirParser()) if p.handle == 70]
    tr = AirLinkTracker()
    feed(tr, pk)
    rows = [(x.t0_us / 1e6 + j * x.frame_us / 1e6, float(2 ** (v / 8))) for x in tr.take_batches(1e18, force=True)
            for j, v in enumerate(x.e) if v != 255]
    assert click_phase_ms(rows, 128 / RATE, 0.0, sbc_encoder_delay_samples(8) / RATE) == pytest.approx(reports[0].phase_send_ms, abs=0.5)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the parser option (`"first"`: `parse_sbc_frame(..., check_crc=True)` for frame 0, `False` for the rest; the frame-count check stays), then the tracker per the spec "AirLinkTracker": RTP unwrap per link; sequence continuity decides between anchor reset (contiguous sequence, timestamp step of more than one frame) and new epoch (sequence gap, send gap > 1 s, SSRC change, `on_link_reset`, `on_discontinuity`); across an anchor reset `seq` (frames sent) continues while `u0_us` (media time) includes the skipped samples, so the daemon keeps its lag in media time (spec); `SendAnchor` is seeded at an anchor reset with `α_old − Δu_step` and emptied at a new epoch (no batch before 32 packets of the epoch); completion-lag median over 2 s; batch cutting (an epoch change or an anchor reset closes the batch); frames-per-second guard; 2 s non-SBC rule.
- [ ] **Step 4: Run** → PASS (`test_a2dp_air.py` unchanged and green).
- [ ] **Step 5: Bench.** `scripts/air_sensor_bench.py` parses the fixture 50 times through parser (`check_crc="first"`) + tracker and prints µs per record and the projected fraction of one core at 150 records/s per speaker. Run it on the desktop and on the Docker test VM 105 (x86, 2 vCPU, like the HAOS VM; never in the production add-on shell) and put both numbers in the commit body; a Raspberry Pi is not available, so Pi cost is stated as unverified.
- [ ] **Step 6: Commit** `feat(bluetooth): SBC air envelope with send-anchored air times, anchor segments and epochs`.

### Task A5: Air IPC commands (encode, decode, validate) and daemon intake

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/commands.py` (`AirEnvelope`, `AirSource`, `SetAirCalibration`, `AirFold`, `AIR_ENVELOPE_VERSION = 1`), `src/sendspin_bridge/services/ipc/daemon_process.py` (dispatch to `daemon.air_intake(...)`; a daemon without an air sensor counts and ignores), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`air_intake` stub storing counters; `accepts` advertised only when the spawn params enable the sensor)
- Test: `tests/unit/services/ipc/test_air_commands.py`

**Interfaces:**

```python
AIR_ENVELOPE_VERSION = 1

@dataclass(frozen=True)
class AirEnvelope:
    epoch: int; seq: int; u0_us: float; t0_us: float; frame_us: float; timestamp: str
    rate: int; subbands: int; blocks: int; bands: tuple[int, ...]
    complete_lag_us: float | None; anchor_reset: bool; e: tuple[int, ...]

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
GOOD = {"cmd": "air_envelope", "v": 1, "epoch": 3, "seq": 128, "u0_us": 371_519.3, "t0_us": NOW - 900_000.0,
        "frame_us": 2902.494, "timestamp": "kernel", "rate": 44100, "subbands": 8, "blocks": 16, "bands": [1, 2],
        "complete_lag_us": None, "anchor_reset": False, "e": [10, 20, 255]}

def dec(msg): return decode_command(msg, now_raw_us=lambda: NOW)

def test_envelope_round_trips_with_the_protocol_version():
    cmd = dec(GOOD)
    assert isinstance(cmd, AirEnvelope) and cmd.e == (10, 20, 255) and cmd.bands == (1, 2)
    wire = json.loads(json.dumps(encode_command(cmd)))
    assert wire["protocol_version"] == 2 and wire["v"] == 1
    assert dec(wire) == cmd

@pytest.mark.parametrize(("key", "value"), [
    ("v", 2), ("epoch", -1), ("seq", "x"), ("t0_us", float("nan")), ("t0_us", NOW + 11e6), ("frame_us", 100.0),
    ("timestamp", "wall"), ("rate", 22050), ("subbands", 6), ("blocks", 5), ("bands", [8]),
    ("complete_lag_us", 500_000.0), ("e", []), ("e", [256]), ("e", [1] * 257), ("e", [True]),
    ("u0_us", -1.0), ("u0_us", float("inf")), ("u0_us", 128 * 2902.494 - 10.0), ("anchor_reset", "yes"),
])
def test_envelope_rejects_out_of_range(key, value):
    with pytest.raises(InvalidCommand):
        dec({**GOOD, key: value})

def test_source_and_calibration_and_fold():
    s = dec({"cmd": "air_source", "v": 1, "state": "unsupported_codec", "reason": "codec:aac", "codec": "aac",
             "adapter": "C0:FB:F9:62:D7:D6", "index": 1, "handle": 70, "in_step": False, "monitor_drops": None, "ipc_dropped": 0})
    assert isinstance(s, AirSource) and s.reason == "codec:aac"
    assert dec({"cmd": "set_air_calibration", "latency_ms": 9999}) == SetAirCalibration(latency_ms=2000.0)
    assert encode_command(SetAirCalibration(latency_ms=203.0)) == {"cmd": "set_air_calibration", "latency_ms": 203.0, "protocol_version": 2}
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
- [ ] **Step 3: Implement** the four commands in `decode_command`/`encode_command` with every rule of the spec's validation table (the per-epoch `seq` continuity check lives in the daemon's `AirStream`, A9, not in the decoder) (bools are not ints; lists must be lists of ints); `now_raw_us` defaults to `time.clock_gettime_ns(CLOCK_MONOTONIC_RAW)/1000`. In `daemon_process._read_commands`, invalid air commands increment `daemon.air_counters["invalid"]` and log one WARNING per 10 min (no `_emit_error`), while other commands keep today's error behaviour; valid ones go to `daemon.air_intake(cmd)`. `BridgeDaemon.air_intake` counts (`ignored` when no sensor) until A11 wires the sensor.
- [ ] **Step 4: Run** → PASS, plus `uv run pytest tests/unit/services/ipc -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(ipc): air envelope, source, calibration and fold commands`.

### Task A6: Parent wiring — `AirSensorService` with one sender per device

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/air_sensor_service.py`
- Modify: `src/sendspin_bridge/bridge/orchestrator.py` (switch on only: create the service after the hub, run it on the bridge loop, stop it on shutdown), `src/sendspin_bridge/bridge/client.py` (`send_air_command(cmd)`, a non-blocking write that returns `False` without writing when the daemon has not advertised `accepts` or the transport's write buffer holds more than 64 KiB)
- Test: `tests/unit/services/bluetooth/test_air_sensor_service.py`

**Interfaces:**

```python
class AirSensorService:
    """Hub consumer (hub thread) + per-device trackers + one sender task per device (bridge loop)."""
    wants_acl_tx = True
    def __init__(self, *, hub: HciMonitorHub, loop: asyncio.AbstractEventLoop,
                 resolve_client: Callable[[str], Any | None],      # peer MAC -> SendspinClient
                 parser: A2dpAirParser | None = None,              # default A2dpAirParser(check_crc="first")
                 tick_s: float = 0.25, queue_len: int = 16, cpu_per_speaker: float = 0.015) -> None: ...
    def subscribe(self, mac: str) -> None: ...
    def unsubscribe(self, mac: str) -> None: ...
    def on_record(self, rec: HubRecord) -> None: ...     # hub thread: parser.feed(rec.t_raw_us / 1e6, ...)
    def on_hub_state(self, state: str, reason: str | None) -> None: ...
    async def run(self) -> None: ...                    # bridge loop: every tick_s move batches into per-device queues
    def queued(self, mac: str) -> int: ...
```

- [ ] **Step 1: Failing tests** (a fake hub with `peer()`, `adapters()`, `counters()`, `add/remove_consumer`, `generation`; fake clients whose `send_air_command` records commands, with an `accepts` attribute and an optional stall that makes every write report a full buffer; the fixture's records replayed as `HubRecord`s with `t_raw_us = t_unix·1e6 − OFFSET_US`):

```python
# tests/unit/services/bluetooth/test_air_sensor_service.py
import pytest
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirSource

pytestmark = pytest.mark.asyncio
ENEBY, OTHER = "FC:58:FA:EB:08:6C", "2C:D2:6B:B8:EC:5B"

async def test_batches_reach_only_the_subscribed_speaker(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture(); await env.ticks(8)
    envs = [c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]
    assert envs and all(len(c.e) <= 128 for c in envs)

async def test_air_times_are_raw_seconds_from_the_record(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture(); await env.ticks(8)
    first = next(c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope))
    assert abs(first.t0_us - (env.first_media_unix_s * 1e6 - env.OFFSET_US)) < 50_000   # no wall-clock leak

async def test_nothing_is_sent_before_the_daemon_advertises(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=None)
    env.service.subscribe(ENEBY)
    await env.replay_fixture(); await env.ticks(8)
    assert env.client(ENEBY).sent == []

async def test_unattributed_links_are_ignored(air_env):
    env = await air_env(peers={}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture(); await env.ticks(8)
    assert not [c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]

async def test_a_stalled_daemon_does_not_delay_another(air_env):
    env = await air_env(peers={(1, 70): ENEBY, (0, 71): OTHER}, accepts=[1], stalled={ENEBY})
    env.service.subscribe(ENEBY); env.service.subscribe(OTHER)
    await env.replay_fixture(both_links=True); await env.ticks(40)
    assert [c for c in env.client(OTHER).sent if isinstance(c, AirEnvelope)]
    assert env.service.queued(ENEBY) <= 16
    src = [c for c in env.client(ENEBY).sent_attempts if isinstance(c, AirSource)][-1]
    assert src.ipc_dropped > 0

async def test_hub_without_permission_reports_unavailable(air_env):
    env = await air_env(peers={}, accepts=[1], hub_reason="no_cap_net_raw")
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert (src.state, src.reason) == ("unavailable", "no_cap_net_raw")

async def test_cpu_guard_scales_with_streaming_speakers(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.025, streaming_links=2)
    env.service.subscribe(ENEBY)
    await env.ticks(2)
    assert env.hub.consumers_wanting_acl_tx() == 1          # 2.5 % < 2 × 1.5 %: within budget
    env.hub.set_counters(cpu_fraction=0.025, streaming_links=1)
    await env.ticks(2)                                       # two windows over 1 × 1.5 %
    assert env.hub.consumers_wanting_acl_tx() == 0
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert src.reason == "cpu_budget"

async def test_no_subscriber_means_no_acl_tx_consumer(air_env):
    env = await air_env(peers={}, accepts=[1])
    assert env.hub.consumers_wanting_acl_tx() == 0
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: on the hub thread feed one `A2dpAirParser(check_crc="first")` with `t = rec.t_raw_us / 1e6` and `drops = rec.drops` (no other time conversion); map emitted `MediaPacket`s by `(index, handle)` → peer MAC → subscribed device → its `AirLinkTracker` (`in_step` from `parser.link_states()`); on a drop increase or a clock-map epoch change call `on_discontinuity()` on every tracker; collect batches under a lock. On the loop, every 250 ms, append batches to each device's `deque(maxlen=16)` (count drops) and wake that device's **own sender task**, which sends in order via `client.send_air_command(...)` and leaves the batch queued when the write is refused (full buffer); `AirSource` on change and every 5 s (adapter MAC from `hub.adapters()`, codec hint from `client.status["bt_codec_name"]`). Register as a hub consumer only while ≥ 1 device is subscribed. CPU guard: every 30 s compare `hub.counters()["cpu_fraction"]` with `cpu_per_speaker × max(1, streaming_links)`; two consecutive windows over budget → remove the consumer and report `cpu_budget`.
- [ ] **Step 4: Run** → PASS, plus `tests/unit/services/bluetooth tests/unit/bridge -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(bluetooth): stream each speaker's air envelope to its daemon`.

### Task A6b: Subscription lifecycle and hub recovery

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (subscribe when a daemon that advertises `accepts` is running and the speaker is connected; unsubscribe on daemon exit, Bluetooth disconnect, standby, removal; resend the current `SetAirCalibration` after every respawn), `src/sendspin_bridge/services/lifecycle/reconfig_orchestrator.py` (online add/remove/disable of a player subscribes/unsubscribes), `src/sendspin_bridge/services/bluetooth/air_sensor_service.py` (`on_hub_state("open")` after an error: new epoch on every tracker and an immediate `AirSource` to every subscribed device)
- Test: `tests/unit/services/bluetooth/test_air_sensor_lifecycle.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/bluetooth/test_air_sensor_lifecycle.py
import pytest
pytestmark = pytest.mark.asyncio

async def test_daemon_respawn_resubscribes_and_resends_calibration(lifecycle_env):
    env = await lifecycle_env(calibration_ms=203.0)
    await env.daemon_exits(); assert not env.service.is_subscribed(env.mac)
    await env.daemon_starts(accepts=[1])
    assert env.service.is_subscribed(env.mac)
    assert env.sent_dicts()[-1] == {"cmd": "set_air_calibration", "latency_ms": 203.0, "protocol_version": 2}

async def test_bluetooth_disconnect_unsubscribes(lifecycle_env):
    env = await lifecycle_env()
    await env.bt_disconnect()
    assert not env.service.is_subscribed(env.mac)
    await env.bt_connect()
    assert env.service.is_subscribed(env.mac)

async def test_live_reconfig_disable_and_add(lifecycle_env):
    env = await lifecycle_env()
    await env.reconfig(enabled=False)
    assert not env.service.is_subscribed(env.mac)
    await env.reconfig(enabled=True)
    assert env.service.is_subscribed(env.mac)

async def test_hub_reopen_starts_new_epochs_and_resends_sources(lifecycle_env):
    env = await lifecycle_env()
    await env.stream_some()
    epoch_before = env.last_envelope().epoch
    await env.hub_error_then_open()
    assert env.last_source().state in ("streaming", "no_traffic")
    await env.stream_some()
    assert env.last_envelope().epoch > epoch_before

async def test_switch_off_never_subscribes(lifecycle_env):
    env = await lifecycle_env(switch=False)
    assert env.service is None and env.client_sent_air_commands() == []
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the hooks (status transitions already pass through `_update_status`; subscribe/unsubscribe on the edges of `bluetooth_connected`, daemon running + `air_sensor.accepts`, `enabled`). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(bluetooth): air sensor subscriptions follow daemons, connections and reconfiguration`.

### Task A7: `WrittenEnvelope` tap on the input PCM

**Files:**
- Create: `src/sendspin_bridge/services/audio/playout/air_tap.py`
- Modify: `src/sendspin_bridge/services/audio/playout/gst_output.py` (`PlayoutPlayer(..., air_tap: WrittenEnvelope | None = None, output_delay_us: Callable[[], float] | None = None)`; in `_push`, after a successful push, `self._air_tap.add_block(block, fmt, schedule_at, kind_at, d_out_us)`; in `_apply_output_volume`, `self._air_tap.mute_transition(raw_now, effective_muted)` on every change; the hook is skipped entirely when `air_tap is None`), `src/sendspin_bridge/services/audio/playout/engine.py` (read-only accessors `schedule_at(x_ns) -> float | None` and `kind_at(x_ns) -> SegmentKind | None` over the existing `ScheduleMap`; no behaviour change)
- Test: `tests/unit/services/audio/playout/test_air_tap.py`

`_push` receives blocks in the **input** format (the decoder's PCM before `audioconvert`/`audioresample`): `InputFormat(sample_rate, channels, sample_bytes)` with `sample_bytes` 2 (S16LE), 3 (S24LE packed) or 4 (S32LE).

**Interfaces:**

```python
SUPPORTED_RATES = (44100, 48000)
SUPPORTED_SAMPLE_BYTES = (2, 3, 4)

def comb_lag(rate: int) -> int: ...                                  # 3 at 44.1/48 kHz: round(rate / 14700)
def to_s16(pcm: bytes, sample_bytes: int) -> memoryview: ...        # top 16 bits, C-speed slice assignment
def comb_peak_log2(s16: memoryview, channels: int, start: int, stop: int, prev: tuple[int, ...], lag: int) -> float: ...

@dataclass(frozen=True, slots=True)
class WrittenSlice:
    i0: int                    # global index of the first cell since the output opened
    cell_us: float             # 128 / input rate
    w: array                   # 'f', 0 for silence cells (muted cells keep their value)
    s: array                   # 'd' RAW µs (NaN when unknown)
    t: array                   # 'd' T = s + D_out at write time
    program: bytes             # 1 = ScheduleMap kind "program", 0 = "silence"
    muted: bytes               # 1 = the mute timeline is muted at the cell's port time s_i (resolved at slice time)

class WrittenEnvelope:
    def __init__(self, *, seconds: float = 20.0) -> None: ...
    def add_block(self, block: Block, fmt: InputFormat, schedule_at: Callable[[int], float | None],
                  kind_at: Callable[[int], str | None], d_out_us: float) -> None: ...   # feeder thread
    def mute_transition(self, t_raw_us: float, muted: bool) -> None: ...   # player: user mute/volume 0/tail mute applied
    def reset(self) -> None: ...                                     # output reopened
    @property
    def unsupported(self) -> str | None: ...                         # e.g. "input:96000/24" for the current format
    @property
    def cell_us(self) -> float | None: ...
    def newest_cell(self) -> int | None: ...
    def slice(self, i0: int, i1: int) -> WrittenSlice | None: ...    # copy under the lock; None if out of the ring
    def cell_near(self, s_us: float, around: int, span_s: float = 2.0) -> int | None: ...
        # newest cell within ±span of `around` whose s is within half a cell of s_us (no monotonic assumption)
```

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/audio/playout/test_air_tap.py
import math, struct
import pytest
from sendspin_bridge.services.audio.playout.air_tap import (
    WrittenEnvelope, comb_lag, comb_peak_log2, to_s16,
)
from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat

def interleave(l, r=None): return [v for pair in zip(l, r or l) for v in pair]
def s16(vals): return struct.pack(f"<{len(vals)}h", *vals)
def s24(vals): return b"".join(int(v * 256).to_bytes(3, "little", signed=True) for v in vals)
def s32(vals): return b"".join(int(v * 65536).to_bytes(4, "little", signed=True) for v in vals)

def ref_comb(xs, prev, lag):
    full = list(prev) + list(xs)
    return math.log2(1 + max(abs(full[n] - full[n - lag]) for n in range(lag, len(full))))

@pytest.mark.parametrize("pack,nbytes", [(s16, 2), (s24, 3), (s32, 4)])
def test_formats_reduce_to_the_same_16_bit_samples(pack, nbytes):
    vals = interleave([1000, -2000, 32000, -32768])
    assert list(to_s16(pack(vals), nbytes)) == vals

def test_comb_lag_per_rate():
    assert (comb_lag(44100), comb_lag(48000)) == (3, 3)

def test_comb_matches_a_plain_reference():
    xs = [int(8000 * math.sin(2 * math.pi * 7350 * n / 44100)) for n in range(128)]
    got = comb_peak_log2(to_s16(s16(interleave(xs)), 2), 2, 0, 128, (0,) * 6, 3)
    assert got == pytest.approx(ref_comb(xs, [0, 0, 0], 3), abs=1e-9)

def test_comb_rejects_bass_and_passes_the_band():
    lo = [int(8000 * math.sin(2 * math.pi * 200 * n / 44100)) for n in range(1280)]
    hi = [int(8000 * math.sin(2 * math.pi * 5000 * n / 44100)) for n in range(1280)]
    f = lambda xs: comb_peak_log2(to_s16(s16(interleave(xs)), 2), 2, 0, 1280, (0,) * 6, 3)
    assert f(hi) - f(lo) > 4.0

def block(fmt, x0_ns, frames, value=1000, program=True):
    pack = {2: s16, 3: s24, 4: s32}[fmt.sample_bytes]
    return Block(pcm=pack(interleave([value if n % 2 else -value for n in range(frames)])), x0_ns=x0_ns,
                 duration_ns=round(frames * 1e9 / fmt.sample_rate), program=program, dropped=0, inserted=0, late_drop_us=0.0)

F16, F24 = InputFormat(44100, 2, 2), InputFormat(48000, 2, 3)

def test_cells_carry_s_t_kind_and_mute():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 441), F16, lambda x: 1e6 + x / 1000.0, lambda x: "program", d_out_us=180_000.0)
    sl = tap.slice(0, 3)
    assert sl.cell_us == pytest.approx(128 / 44100 * 1e6)
    assert list(sl.s) == pytest.approx([1e6 + i * sl.cell_us for i in range(3)], abs=0.05)
    assert list(sl.t) == pytest.approx([1e6 + 180_000.0 + i * sl.cell_us for i in range(3)], abs=0.05)
    assert sl.program == b"\x01\x01\x01" and sl.muted == b"\x00\x00\x00"

def test_24_bit_48k_input_is_supported():
    tap = WrittenEnvelope()
    tap.add_block(block(F24, 0, 480), F24, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.unsupported is None and tap.cell_us == pytest.approx(128 / 48000 * 1e6)

@pytest.mark.parametrize("rate,nbytes,reason", [(96000, 3, "input:96000/24"), (88200, 2, "input:88200/16"), (44100, 1, "input:44100/8")])
def test_other_inputs_are_unsupported(rate, nbytes, reason):
    tap = WrittenEnvelope()
    fmt = InputFormat(rate, 2, nbytes)
    tap.add_block(Block(pcm=bytes(nbytes * 2 * 256), x0_ns=0, duration_ns=1, program=True, dropped=0, inserted=0, late_drop_us=0.0),
                  fmt, lambda x: 0.0, lambda x: "program", d_out_us=0.0)
    assert tap.unsupported == reason and tap.newest_cell() is None

def test_mute_is_resolved_by_port_time_not_push_time():
    """Cells are pushed ~0.8 s before their port time; a mute applied after the push still covers them."""
    tap = WrittenEnvelope()
    port = lambda x: 800_000.0 + x / 1000.0                          # s = push origin + 0.8 s
    tap.add_block(block(F16, 0, 4 * 128), F16, port, lambda x: "program", d_out_us=0.0)
    cell = 128 / 44100 * 1e6
    tap.mute_transition(800_000.0 + 2 * cell, True)                  # applied later, acts at the port
    sl = tap.slice(0, 4)
    assert sl.muted == b"\x00\x00\x01\x01"
    assert all(v > 0 for v in sl.w)                                  # the PCM is unchanged by mute

def test_mute_that_ends_before_the_port_does_not_count():
    tap = WrittenEnvelope()
    tap.mute_transition(0.0, True); tap.mute_transition(100_000.0, False)
    tap.add_block(block(F16, 0, 256), F16, lambda x: 800_000.0 + x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.slice(0, 2).muted == b"\x00\x00"

def test_silence_cells_are_zero():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 256, value=0, program=False), F16, lambda x: x / 1000.0, lambda x: "silence", d_out_us=0.0)
    sl = tap.slice(0, 2)
    assert sl.program == b"\x00\x00" and list(sl.w) == [0.0, 0.0]

def test_cells_split_across_blocks_are_whole():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 100), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() is None
    tap.add_block(block(F16, round(100e9 / 44100), 156), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    assert tap.newest_cell() == 1

def test_lookup_handles_s_stepping_back():
    """A placement insert makes s go back: the newest matching cell wins, no binary search."""
    tap = WrittenEnvelope()
    sched = lambda x: x / 1000.0 if x < 500_000_000 else x / 1000.0 - 100_000.0   # s steps back 100 ms at 0.5 s
    tap.add_block(block(F16, 0, 44100), F16, sched, lambda x: "program", d_out_us=0.0)
    i = tap.cell_near(450_000.0, around=tap.newest_cell())
    assert tap.slice(i, i + 1).s[0] == pytest.approx(450_000.0, abs=tap.cell_us / 2)
    assert i * tap.cell_us > 500_000                                   # the later, re-scheduled cell

def test_reset_clears_the_ring():
    tap = WrittenEnvelope()
    tap.add_block(block(F16, 0, 4410), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    tap.reset()
    assert tap.newest_cell() is None

def test_ring_forgets_old_cells():
    tap = WrittenEnvelope(seconds=1.0)
    for k in range(30):
        tap.add_block(block(F16, round(k * 4410e9 / 44100), 4410), F16, lambda x: x / 1000.0, lambda x: "program", d_out_us=0.0)
    n = tap.newest_cell()
    assert tap.slice(0, 10) is None and tap.slice(n - 10, n) is not None

def test_player_without_tap_installs_no_hook(playout_player_factory):
    p = playout_player_factory(air_tap=None)
    assert p._air_tap is None
```

(`playout_player_factory` follows `tests/unit/services/audio/playout/test_gst_output.py`'s player construction with fakes.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: `to_s16` (S16 `memoryview.cast("h")`; S24 `b = bytearray(2n); b[0::2] = pcm[1::3]; b[1::2] = pcm[2::3]`; S32 with `pcm[2::4]`, `pcm[3::4]`); per channel `ch = mv[c::channels]`, `max(map(abs, map(operator.sub, ch[lag:], ch[:-lag])))` with the `lag`-frame history; partial cells carried across blocks (≤ 127 frames per channel); per cell `s`, `t = s + d_out_us`, kind; a mute timeline (RAW transitions, 30 s kept) whose state at `s_i` gives each cell's `muted` flag when a slice is read; ring as fixed-size arrays with a global start index and a `threading.Lock`; `unsupported` set (and nothing recorded) for formats outside `SUPPORTED_RATES × SUPPORTED_SAMPLE_BYTES`. `PlayoutPlayer._apply_output_volume` calls `tap.mute_transition(raw_now, muted)` whenever the effective mute changes (user mute, user volume 0, tail mute start/end); the `_push` call is wrapped in `try/except Exception` that logs once and sets `self._air_tap = None` (it never reaches the feeder).
- [ ] **Step 4: Run** → PASS, plus `tests/unit/services/audio/playout -q -p no:cacheprovider`.
- [ ] **Step 5: Commit** `feat(playout): written-audio envelope on the input PCM with its schedule, for the air sensor`.

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

def test_finds_a_lag_near_the_edge_of_the_500ms_acquisition_range():
    air, w, prog = make(lag_true=150.0, lo=-172, hi=172)
    assert correlate_window(air, w, prog, -172, 172).lag == pytest.approx(150.0, abs=0.2)

def test_scan_cost_is_bounded():
    import time
    air, w, prog = make(lo=-172, hi=172)
    t0 = time.perf_counter(); ncc_scan(air, w, -172, 172); dt = time.perf_counter() - t0
    assert dt < 0.5                                                       # ±500 ms acquisition scan, generous for CI
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the spec "Correlation" steps 1, 2, 4–6: validity (≥ 90 % present, ≥ 80 % program over the zero-lag-centred written span), z-scored air once, per-lag `w̄`, `σ_w` from prefix sums of `w` and `w²` over present indices (when cells are missing, use the masked sums over the present index list; with ≤ 10 % missing compute the dot product on the compacted lists), dot products with `sum(map(operator.mul, a, b))`; side lobe excludes `|ℓ − ℓ*| ≤ SIDE_EXCLUDE`; `edge` when `ℓ*` is the first or last lag.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Comb vs band-pass (R-7) is decided on real music, not here.** The A8b tool runs both envelopes on the private morning capture and A17 on the stand captures; the synthetic fixture is not representative for this choice. This task only provides `bandpass_peak_log2` (a reference biquad 2.7–8.3 kHz written envelope, test-and-tool only) next to the comb, with a unit test that both rank a 5 kHz tone above a 200 Hz tone by > 24 dB.
- [ ] **Step 6: Commit** `feat(playout): envelope correlator with peak/side-lobe gate and sub-frame lag`.

### Task A8b: Offline correlation tool for real captures (`scripts/air_envelope_corr.py`)

**Files:**
- Create: `scripts/air_envelope_corr.py` (based on the morning analysis script `corr.py`, made a repo tool)
- Test: `tests/unit/services/bluetooth/test_air_envelope_corr_script.py`

The morning capture (2-minute music track to ENEBY20 with `btmon -w`) and its source WAV stay
**private** (copyrighted music, 20 MB); the tool's docstring says how to make one: start
`btmon -w capture.btsnoop` on the bridge host, play a local WAV through the bridge to one SBC
speaker, stop, then run the tool with the capture and the WAV.

**Interfaces:**

```
uv run python scripts/air_envelope_corr.py capture.btsnoop source.wav [--index N] [--handle H]
        [--definition legacy|impl|both] [--window-frames 1034] [--hop-frames 344] [--search 20]
        [--envelope comb|bandpass] [--csv out.csv]
```

```python
def legacy_windows(air, src, *, window=2000, hop=1000, search=20) -> list[dict]: ...
    # the morning script's exact definition: sub-bands 1-3, power Σ2^(2·sf), natural log; source mono through a
    # 5 kHz Q 0.8 biquad, log(peak²·1e8 + 1); un-normalised covariance, means removed once per window;
    # side lobe = max over |L − L*| > 2; parabolic fraction
def impl_windows(air_cells, written_cells, program, *, window, hop, search) -> list[dict]: ...
    # calls air_correlator.correlate_window (the daemon's code) on the implementation's envelopes:
    # frame_envelope (A4) on the air side, comb_peak_log2 (A7) or the reference band-pass on the source side
```

Output per window: index, ρ, PSR, lag (frames, fractional), status, and the air time of the window
middle minus the source time; a summary with the PSR median/p10/p90 of confirmed and rejected
windows for each definition.

- [ ] **Step 1: Failing tests** on committed fixtures only (the click capture plus a rendered click WAV written to `tmp_path` with `sendspin_sync.click_track.render_pcm(duration_s=...)` aligned to the capture's first click):

```python
def test_legacy_definition_reproduces_the_scripts_ratio_on_a_synthetic_pair(tool):
    air, src = tool.synthetic_pair(lag_frames=7, seed=3)          # helper in the script, used by its own --selftest
    w = tool.legacy_windows(air, src, window=600, hop=300, search=20)
    assert all(abs(x["lag"] - 7) < 0.3 for x in w if x["psr"] >= 1.15)

def test_impl_definition_uses_the_daemon_correlator(tool, monkeypatch):
    from sendspin_bridge.services.audio.playout import air_correlator
    called = []
    monkeypatch.setattr(air_correlator, "correlate_window", lambda *a, **k: called.append(1) or air_correlator.WindowResult("rejected", None, 0.1, 1.0))
    tool.impl_windows([1.0] * 1100, [1.0] * 1200, b"\x01" * 1200, window=1034, hop=344, search=20)
    assert called

def test_cli_on_the_click_fixture_prints_both_definitions(tmp_path, capsys, tool, click_wav):
    rc = tool.main([str(FIX / "eneby_clicks.btsnoop.gz"), str(click_wav), "--definition", "both", "--window-frames", "600", "--hop-frames", "300"])
    out = capsys.readouterr().out
    assert rc == 0 and "legacy" in out and "impl" in out
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (port `corr.py`'s functions unchanged in substance for `legacy`, add `impl`; read WAV with `wave`, S16/S24; `--selftest` builds `synthetic_pair`). **Step 4: Run** → PASS. Run the tool once on the private morning capture and paste the per-definition PSR summary into the commit body (the numbers, not the capture).
- [ ] **Step 5: Commit** `feat(scripts): offline envelope correlation for air captures (legacy and implementation PSR)`.

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
WIDE_CELLS = 86
RESET_CELLS = 5
ACQUIRE_US = 500_000.0
DELTA0_US = 10_000.0
JUMP_CONFIRM_CELLS = 3
MAX_MUTED_FRACTION = 0.2

def enc_delay_us(subbands: int, rate: int) -> float: ...        # d_enc = 4.5·M/rate
def comb_delay_us(rate: int) -> float: ...                       # d_comb = comb_lag/2 / rate

class AirStream:
    """Air cells of the current epoch on the media-time axis (20 s ring)."""
    def add(self, env: AirEnvelope) -> Literal["ok", "new_epoch", "anchor_reset", "dropped"]: ...
    @property
    def epoch(self) -> int | None: ...
    def window(self, u_end_us: float, n: int) -> tuple[list[float | None], float, float] | None: ...
        # cells resampled onto the written grid, media time of the first cell, energy-cell time of the middle

class AirSensor:
    def __init__(self, *, tap: WrittenEnvelope, now_us: Callable[[], float], output_delay_ms: Callable[[], float],
                 run_window: Callable[[Callable[[], WindowResult]], Awaitable[WindowResult]] | None = None,
                 wall_iso: Callable[[], str] = ..., log=logger) -> None: ...
    def on_envelope(self, env: AirEnvelope) -> None: ...
    def on_source(self, src: AirSource) -> None: ...
    def set_calibration(self, latency_ms: float | None) -> None: ...
    def on_engine_event(self, kind: Literal["underrun", "pa_step", "stall", "reopen", "fallback"]) -> None: ...
    async def step(self) -> None: ...                     # at most one window per HOP_S, computed via run_window (executor)
    def snapshot(self) -> dict[str, object]: ...
```

`run_window` defaults to `loop.run_in_executor(None, fn)`; tests pass a synchronous runner.

- [ ] **Step 1: Failing tests.** The `air_world` fixture (in the test file) owns a real `WrittenEnvelope`, a real `AirSensor` and a fake clock. **Its air side is built at the PCM instant, independently of the sensor's formula:** it renders the A8 `music()` contour as PCM-instant events, schedules the written cell `i` at `s_i`, defines the true air time of the PCM of cell `i` as `s_i + H_true`, and produces the air energy cell for that PCM at `t = s_i + H_true + d_enc_true`, where `d_enc_true` is a fixture constant derived from the SBC analysis-filter model in the fixture (4.5·M samples — the same physical fact, written down once in the fixture, not imported from the code under test). Written energy is computed by the real tap from real PCM (so `d_comb` comes from the comb itself). Air batches arrive every 250 ms with `PA_LATENCY = 800 ms` between writing and air.

```python
# tests/unit/services/audio/playout/test_air_sensor.py
import pytest
from sendspin_bridge.services.audio.playout.air_sensor import NO_LOCK_S

pytestmark = pytest.mark.asyncio

async def test_locks_and_reports_host_placement_without_bias(air_world):
    w = air_world(H_true_ms=37.0)
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["host_placement_ms"] == pytest.approx(37.0, abs=0.5)
    assert s["air_error_ms"] is None and s["calibrated"] is False and 0.0 < s["confidence"] <= 1.0

async def test_sign_of_the_encoder_correction(air_world):
    """Air energy lags the PCM by d_enc: forgetting the correction (or flipping it) is off by ~0.8 or ~1.6 ms."""
    w = air_world(H_true_ms=0.0)
    await w.run(seconds=10)
    assert abs(w.sensor.snapshot()["host_placement_ms"]) < 0.4

async def test_air_error_uses_calibration_and_output_delay(air_world):
    w = air_world(H_true_ms=37.0, output_delay_ms=180.0)
    w.sensor.set_calibration(203.0)
    await w.run(seconds=10)
    assert w.sensor.snapshot()["air_error_ms"] == pytest.approx(37.0 + 203.0 - 180.0, abs=0.5)

async def test_engine_jump_moves_h_not_the_lag(air_world):
    w = air_world(H_true_ms=37.0)
    await w.run(seconds=10); lag = w.sensor.snapshot()["lag_cells"]
    w.shift_schedule_ms(-41.5)
    await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["host_placement_ms"] == pytest.approx(78.5, abs=1.0) and s["lag_cells"] == pytest.approx(lag, abs=0.3)

async def test_pa_skip_anchor_reset_keeps_the_lag(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10); lag = w.sensor.snapshot()["lag_cells"]
    w.pa_skip_ms(20.0)                                   # contiguous sequence, RTP step, anchor reset, same epoch
    await w.run(seconds=4)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["epochs"] == 1 and s["host_placement_ms"] == pytest.approx(10.0, abs=1.0)
    assert s["lag_cells"] == pytest.approx(lag, abs=0.3)

async def test_reopen_reacquires(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.reopen_output(H_true_ms=-60.0)                     # every stream start/seek reopens the output
    await w.run(seconds=0.5)
    assert w.sensor.snapshot()["state"] == "acquiring"
    await w.run(seconds=6)
    assert w.sensor.snapshot()["host_placement_ms"] == pytest.approx(-60.0, abs=0.5)

async def test_acquisition_range_is_500ms(air_world):
    w = air_world(H_true_ms=420.0)                       # e.g. a PipeWire fallback latency guess far off
    await w.run(seconds=10)
    assert w.sensor.snapshot()["host_placement_ms"] == pytest.approx(420.0, abs=0.5)

async def test_muted_windows_are_skipped_not_rejected(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.mute(); await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["state"] == "holding" and s["windows_skipped"] > 0 and s["windows_rejected"] == 0

async def test_silence_holds_then_loses_lock(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.silence(); await w.run(seconds=6)
    assert w.sensor.snapshot()["state"] == "holding"
    await w.run(seconds=NO_LOCK_S)
    s = w.sensor.snapshot()
    assert s["state"] == "no_lock" and s["host_placement_ms"] is None

async def test_a_single_wrong_peak_is_not_accepted(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10); lag = w.sensor.snapshot()["lag_cells"]
    w.inject_one_window_with_lag_offset(20)
    await w.run(seconds=1)
    assert w.sensor.snapshot()["lag_cells"] == pytest.approx(lag, abs=0.3)

async def test_unsupported_input_and_source_states(air_world):
    w = air_world(H_true_ms=10.0, input_rate=96000)
    await w.run(seconds=2)
    assert w.sensor.snapshot()["state"] == "unsupported_input"
    w2 = air_world(H_true_ms=10.0)
    w2.source(state="unsupported_codec", reason="codec:aac")
    assert w2.sensor.snapshot()["state"] == "unsupported_codec"

async def test_fallback_withdraws_accepts(air_world):
    w = air_world(H_true_ms=10.0)
    w.sensor.on_engine_event("fallback")
    s = w.sensor.snapshot()
    assert (s["state"], s["reason"], s["accepts"]) == ("unavailable", "playout_fallback", [])

async def test_windows_run_through_the_runner(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    assert w.runner_calls > 0 and w.windows_computed_on_loop == 0
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per the spec: `AirStream` keyed by epoch on the media-time axis (an `anchor_reset` batch keeps the epoch and the lag, triggers a ±5-cell re-verification); window selection (newest air cells whose partners are in the tap); priors via `tap.cell_near(t_mid − δ₀ − d_enc, around=…)`; ranges (acquire ±500 ms, track ±20, wide ±86 after underrun/pa_step/stall); validity incl. `MAX_MUTED_FRACTION`; continuity candidate; `H = t_mid − s(i_m) − (d_enc − d_comb)` with `s` interpolated inside one segment; medians, confidence, jitter, counters, state machine; `reopen` resets the air-to-written lag and goes `acquiring`; `fallback` → `unavailable`/`playout_fallback`/`accepts = []`; the window function is built from copied slices and run via `run_window`; INFO logs on state changes (≤ 1/min per kind), DEBUG summary every 10 s, per window with `SENDSPIN_AIR_DEBUG=1` (`t_mid`, `s_mid`, `H`, PSR — the A17 primary acceptance reads these lines).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(playout): air sensor lock state machine and host placement`.

### Task A10: Simulation with recorded captures

Depends on A4, A5 (the batches go through the real encode/decode), A7, A8, A9.

**Files:**
- Create: `tests/fixtures/a2dp_air/build_air_music_fixture.py`, `tests/fixtures/a2dp_air/air_music.btsnoop.gz`, `tests/fixtures/a2dp_air/air_music_truth.json.gz`, `tests/unit/services/audio/playout/test_air_sim.py`

**Interfaces:**

```python
# build_air_music_fixture.py
def music_pcm(seconds: float = 20.0, rate: int = 44100, seed: int = 20261028) -> bytes: ...   # deterministic, stereo S16LE
def main(argv: list[str] | None = None) -> int: ...   # encodes with libsbc 1.3.1, writes btsnoop + truth, checks EXPECTED_SHA256
```

Generator content: 0–6 s percussive transients on a seeded rhythm with notes; 6–8 s a quiet passage (−40 dB); 8–12 s a strict 4-on-the-floor loop; 12–20 s mixed. Packing: 7 SBC frames per RTP packet, ACL fragments of 310 bytes, NOCP per fragment with seeded 1–9 ms delay, a Connection Complete at 0 s. **PulseAudio's bluez5 "Skipping" modelled as PA does it** at 9 s: 20 ms (882 samples) of PCM is never encoded, the encoder's RTP timestamp advances by 882 while the sequence number stays contiguous and the send pacing continues. An AVDTP suspend at 14 s: 1.5 s without packets, then RTP restarting at 0 and a new SSRC. (A PipeWire skip variant is a follow-up.) Bitpool 2 as in the libsbc click fixtures (scale factors do not depend on it), fixed gzip mtime, `EXPECTED_SHA256` checked like `build_libsbc_fixture.py`.

**Truth at the PCM instant.** The builder writes `air_music_truth.json.gz`: for every 128-sample block of the source PCM, the send time (btsnoop time) of the packet that carries the frame encoding it and the frame's position in that packet — or `null` for skipped samples. The test derives `t_air(p)` for a PCM sample from this table and the nominal sample rate only; it never calls the sensor's anchor or `d_enc` code. The written side's schedule is chosen by the test (`s(p) = t_air(p) − H_truth(t)`), so the expected `H` is exact by construction.

- [ ] **Step 1: Build the fixture** (`uv run python tests/fixtures/a2dp_air/build_air_music_fixture.py`; needs `libsbc.so.1` 1.3.1 on the build machine only). Keep both files ≤ 150 KB together.
- [ ] **Step 2: Failing tests** — the whole chain with real code: btsnoop records shifted to RAW by one constant → `A2dpAirParser(check_crc="first")` → `AirLinkTracker` → `air_envelope_from_batch` → `encode_command` → JSON → `decode_command` → `AirSensor`; the written side is `music_pcm()` pushed in 10 ms blocks through a real `WrittenEnvelope`:

```python
# tests/unit/services/audio/playout/test_air_sim.py
import pytest
pytestmark = pytest.mark.slow

def test_cold_start_offset_without_bias(sim):
    r = sim.run(H_truth=[(0.0, 37.0)])
    assert r.lock_time_s < 5.0
    assert abs(r.h_bias_ms_locked) <= 0.5 and r.h_err_ms_locked_p95 <= 2.0

def test_steady_jump_is_seen_within_3s(sim):
    r = sim.run(H_truth=[(0.0, 37.0), (8.0, -4.5)])
    assert r.time_to_reflect_s(8.0, -4.5, tol_ms=2.0) <= 3.0

def test_pa_skip_keeps_epoch_and_lag(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.epochs_between(8.5, 13.5) == 1 and r.anchor_resets_between(8.5, 9.5) == 1
    assert r.max_abs_err_ms_between(9.0, 13.0, states=("locked",)) <= 2.0

def test_quiet_passage_holds_not_lies(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert set(r.states_between(6.5, 8.0)) <= {"locked", "holding"}
    assert r.max_abs_err_ms_between(6.0, 8.0, states=("locked", "holding")) <= 2.0

def test_loop_section_never_confirms_a_wrong_lag(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.max_abs_err_ms_between(8.0, 12.0, states=("locked",)) <= 2.0

def test_avdtp_restart_is_a_new_epoch_and_relocks(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.epochs >= 2 and r.lock_time_after_s(15.5) < 5.0

def test_noise_control_never_confirms(sim):
    r = sim.run(H_truth=[(0.0, 10.0)], written_seed=999)
    assert r.windows_confirmed == 0

def test_real_click_capture_agrees_with_the_phase_tool(sim_clicks):
    r = sim_clicks.run()                     # eneby_clicks.btsnoop.gz + the rendered click track, schedule chosen by the test
    assert r.locked_windows >= 1 and abs(r.h_minus_fold_ms) <= 1.0
```

- [ ] **Step 3: Run** `uv run pytest tests/unit/services/audio/playout/test_air_sim.py -q -p no:cacheprovider -n0` → FAIL where the implementation is wrong; fix the implementation (A4/A7–A9 modules), never the truth.
- [ ] **Step 4: Do not calibrate the gate here.** PSR on the synthetic fixture is not representative of real music (R-9); record the confirmed-window PSR median and the noise maximum with `record_property` for information only. The gate is set in A17 on real music with the A8b tool.
- [ ] **Step 5: Run** the sim file and `tests/unit/services/audio/playout` → PASS.
- [ ] **Step 6: Commit** `test(playout): air sensor simulation on a synthetic-music SBC capture with PA-style skips`.

### Task A11: Daemon wiring

**Files:**
- Modify: `src/sendspin_bridge/services/ipc/bridge_daemon.py` (`DaemonArgs.air_sensor: dict | None = None`; in `_new_playout_player` create a `WrittenEnvelope` and pass it (and an output-delay getter reading `_player_delay_ms`) to `PlayoutPlayer` when `air_sensor.enabled`; build one `AirSensor` per daemon; `air_intake`; in `fall_back_to_legacy` call `sensor.on_engine_event("fallback")` before replacing the player; `air_snapshot()` returns `None` when no sensor exists), `src/sendspin_bridge/services/audio/playout/gst_output.py` (`adopt_output` calls `self._air_tap.reset()` and emits `reopen` through `on_engine_event`; underrun/pa_step/stall forwarded from the engine's counters), `src/sendspin_bridge/services/ipc/daemon_process.py` (spawn param `air_sensor: {"enabled": true, "latency_ms": …}`; the telemetry watcher awaits `daemon.air_step()` every second and patches `status["air_sensor"]` **only when `air_snapshot()` is not None**), `src/sendspin_bridge/bridge/client.py` (spawn param only when `air_sensor_effective` is true; `latency_ms` from A15's resolver, `None` before A15)
- Test: `tests/unit/services/ipc/test_bridge_daemon_air.py`

**Interfaces:**

```python
# bridge_daemon.py
def air_intake(self, cmd: AirEnvelope | AirSource | SetAirCalibration | AirFold) -> None: ...  # asyncio thread
async def air_step(self) -> None: ...                       # contained: an exception → unavailable/internal_error
def air_snapshot(self) -> dict[str, object] | None: ...
# gst_output.py
PlayoutPlayer(..., air_tap: WrittenEnvelope | None = None, output_delay_us: Callable[[], float] | None = None,
              on_engine_event: Callable[[str], None] | None = None)
```

- [ ] **Step 1: Failing tests** (reuse the daemon and fake-player patterns of `tests/unit/services/ipc/test_bridge_daemon_playout.py`):

```python
# tests/unit/services/ipc/test_bridge_daemon_air.py
import pytest
from sendspin_bridge.services.ipc.bridge_daemon import DaemonArgs
from sendspin_bridge.services.ipc.commands import SetAirCalibration

def test_default_args_build_no_sensor_and_no_tap(make_daemon, captured_playout_players):
    d = make_daemon(DaemonArgs(playout_mode="measure"))
    d._make_player("bluez_sink.X")
    assert d.air_snapshot() is None and captured_playout_players[-1].air_tap is None

def test_tap_is_attached_in_new_playout_player(make_daemon, captured_playout_players):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}))
    d._make_player("bluez_sink.X")
    assert captured_playout_players[-1].air_tap is not None and d.air_snapshot()["accepts"] == [1]

def test_off_mode_ignores_the_param(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="off", air_sensor={"enabled": True}))
    assert d.air_snapshot() is None

def test_every_output_open_resets_the_tap_and_reacquires(real_playout_player_with_fakes):
    p, tap, events = real_playout_player_with_fakes()
    p.start(fmt44); p.stop()                     # a stream ends (end_stream == stop in Stage 1)
    p.start(fmt44)                               # the next stream (or a seek) opens a new output
    assert events.count("reopen") == 2 and tap.resets == 2

def test_fallback_marks_unavailable_and_withdraws_accepts(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}))
    d._make_player("bluez_sink.X")
    d.fall_back_to_legacy("no audio clock")
    s = d.air_snapshot()
    assert (s["state"], s["reason"], s["accepts"]) == ("unavailable", "playout_fallback", [])

def test_calibration_reaches_the_sensor(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}))
    d.air_intake(SetAirCalibration(latency_ms=203.0))
    assert d.air_snapshot()["air_to_sound_ms"] == 203.0 and d.air_snapshot()["calibrated"] is True

def test_output_delay_follows_the_player_delay(make_daemon):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}, static_delay_ms=150.0))
    assert d._air_sensor._output_delay_ms() == 150.0

@pytest.mark.asyncio
async def test_sensor_exception_disables_only_the_sensor(make_daemon, monkeypatch):
    d = make_daemon(DaemonArgs(playout_mode="measure", air_sensor={"enabled": True}))
    async def boom(): raise ZeroDivisionError
    monkeypatch.setattr(d._air_sensor, "step", boom)
    await d.air_step()
    assert (d.air_snapshot()["state"], d.air_snapshot()["reason"]) == ("unavailable", "internal_error")

@pytest.mark.asyncio
async def test_status_has_no_air_key_without_the_sensor(telemetry_watcher_harness):
    h = await telemetry_watcher_harness(air=False)
    await h.advance(3.1)
    assert h.status_updates_with("air_sensor") == 0

@pytest.mark.asyncio
async def test_status_carries_air_sensor_at_1hz(telemetry_watcher_harness):
    h = await telemetry_watcher_harness(air=True)
    await h.advance(2.1)
    assert h.status_updates_with("air_sensor") >= 2
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** as listed; the engine-event listener runs on the feeder thread and hands events to the asyncio loop with `call_soon_threadsafe`; `AirFold` is answered after A16b (until then `{"id", "ok": false, "reason": "not_implemented"}`).
- [ ] **Step 4: Run** → PASS; `tests/unit/services/ipc -q -p no:cacheprovider` unchanged and green.
- [ ] **Step 5: Commit** `feat(ipc): run the air sensor in measure-mode daemons`.

### Task A11b: Air IPC over a real subprocess pipe

**Files:**
- Create: `tests/integration/test_air_ipc_pipe.py`, `tests/integration/air_ipc_child.py` (a minimal child: builds a stub daemon with a real `AirSensor` over a synthetic tap and runs the real `daemon_process._read_commands`, plus a 1 Hz status writer using the real `_emit_status`)

- [ ] **Step 1: Failing tests**

```python
# tests/integration/test_air_ipc_pipe.py
import asyncio, json, sys
import pytest
pytestmark = pytest.mark.asyncio

async def test_batches_cross_a_real_pipe_and_status_comes_back(air_child):
    proc = await air_child()
    svc = await air_child.parent_sender(proc)                 # the real SubprocessCommandService
    for env in air_child.fixture_envelopes(seconds=6):        # from eneby_clicks via A4/A5 code
        await svc.send(proc, env)
    status = await air_child.read_status_until(proc, lambda s: s.get("air_sensor", {}).get("frames", 0) > 1500, timeout=10)
    assert status["air_sensor"]["dropped_messages"] == 0
    assert status["air_sensor"]["accepts"] == [1]

async def test_max_size_batch_fits_the_line_limit(air_child):
    proc = await air_child()
    svc = await air_child.parent_sender(proc)
    await svc.send(proc, air_child.envelope(e=[254] * 256))
    status = await air_child.read_status_until(proc, lambda s: s.get("air_sensor", {}).get("frames", 0) >= 256, timeout=5)
    assert status["air_sensor"]["dropped_messages"] == 0

async def test_garbage_lines_are_counted_not_fatal(air_child):
    proc = await air_child()
    proc.stdin.write(b'{"cmd": "air_envelope", "v": 1, "e": "nope"}\n' + b"\xff\xfe\n")
    await proc.stdin.drain()
    status = await air_child.read_status_until(proc, lambda s: "air_sensor" in s, timeout=5)
    assert status["air_sensor"]["dropped_messages"] >= 1 and proc.returncode is None
```

(`air_child` starts `sys.executable -I tests/integration/air_ipc_child.py` with `asyncio.create_subprocess_exec`, stdin/stdout pipes, and kills it in teardown.)

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the child script (test-only) and any framing fix the test exposes in production code. **Step 4: Run** `uv run pytest tests/integration/test_air_ipc_pipe.py -q -p no:cacheprovider -n0` → PASS.
- [ ] **Step 5: Commit** `test(ipc): air commands over a real subprocess pipe`.

### Task A12: Parent status, API model, OpenAPI

**Files:**
- Modify: `src/sendspin_bridge/bridge/client.py` (`DeviceStatus.air_sensor: dict | None = None`; `"air_sensor"` in `_ALLOWED_STATUS_KEYS`; switch **off**: the field stays `None` and is never set; switch on: parent-side dict when the daemon does not run the sensor, `{"state": "off", "reason": <effective reason>}` or `{"state": "unavailable", "reason": <hub reason>}`), `src/sendspin_bridge/application/models/devices.py` (`AirSensorTiming`, `DeviceTiming.air_sensor: AirSensorTiming | None = None`, `_air_or_none`; serialised as `null` when there is none, like `playout`), `ui/openapi.json` + `ui/src/api/*` (regenerated)
- Test: `tests/unit/api/test_air_sensor_api.py`

**Interfaces:**

```python
class AirSensorFold(ResponseModel):
    id: str | None = None; phase_ms: float | None = None; ok: bool = False; reason: str | None = None

class AirSensorTiming(ResponseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True, extra="ignore")
    state: str = "off"; reason: str | None = None; codec: str | None = None; rate: int | None = None; adapter: str | None = None
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

def test_switch_off_has_no_air_key_anywhere(api_client, running_device, config_with):
    config_with(EXPERIMENTAL_AIR_SENSOR=False)
    body = api_client.get(f"/api/v1/devices/{running_device.id}").json()
    assert body["timing"]["air_sensor"] is None and running_device.status.get("air_sensor") is None

def test_switch_on_but_not_effective_reports_off_with_reason(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="off")
    assert dev.status["air_sensor"] == {"state": "off", "reason": "needs_measure"}

def test_status_key_is_accepted_from_the_daemon():
    from sendspin_bridge.bridge.client import _ALLOWED_STATUS_KEYS
    assert "air_sensor" in _ALLOWED_STATUS_KEYS

def test_openapi_lists_the_model(api_client):
    assert "AirSensorTiming" in api_client.get("/api/v1/openapi.json").json()["components"]["schemas"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the model (validated like `_playout_or_none`; `state` must be a string), `null` when `None`, the status field, the parent-side states (switch on only); regenerate `uv run python scripts/export_openapi.py && cd ui && npm run gen:api`.
- [ ] **Step 4: Run** → PASS; `cd ui && npm run build`.
- [ ] **Step 5: Commit** `feat(api): air sensor status in device timing`.

### Task A13: Diagnostics, guidance and logs

**Files:**
- Modify: `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (`air_sensor_unavailable` with reason codes `no_cap_net_raw`, `unsupported_platform`, `monitor_error`, `cpu_budget`, `unsupported_input`, `playout_fallback`, `internal_error`; `air_sensor_codec` with `non_sbc_codec`), `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (`_build_air_sensor_issues`), the diagnostics bundle builder (`air_sensor` per device + `get_hub().counters()`; find it with `grep -rn "def build_diagnostics" src/sendspin_bridge`), `ui/src/i18n/{en,ru}.json`
- Test: `tests/unit/services/diagnostics/test_air_guidance.py` (air part)

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/diagnostics/test_air_guidance.py (air part)
from types import SimpleNamespace
from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition
from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

def test_air_issues_are_registered():
    assert issue_definition("air_sensor_unavailable").severity == "info"
    assert issue_definition("air_sensor_codec").default_reason_codes == ("non_sbc_codec",)

def _device(**extra):
    return SimpleNamespace(player_name="ENEBY20", bluetooth_connected=True, has_sink=True,
                           server_connected=True, health_summary={}, **extra)

def test_unsupported_codec_suggests_prefer_sbc():
    dev = _device(air_sensor={"state": "unsupported_codec", "reason": "codec:aac"})
    issue = next(i for i in _build_device_issues([dev]) if i.key == "air_sensor_codec")
    assert "PREFER_SBC_CODEC" in (issue.primary_action.key + issue.primary_action.label + issue.message)

def test_missing_capability_is_one_bridge_level_issue():
    devs = [_device(air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"}) for _ in range(3)]
    issues = [i for i in _build_device_issues(devs) if i.key == "air_sensor_unavailable"]
    assert len(issues) == 1 and "no_cap_net_raw" in issues[0].reason_codes

def test_switch_off_raises_nothing():
    for dev in (_device(), _device(air_sensor=None), _device(air_sensor={"state": "off", "reason": "needs_measure"})):
        assert not [i for i in _build_device_issues([dev]) if i.key.startswith("air_sensor")]

def test_unsupported_input_is_reported():
    dev = _device(air_sensor={"state": "unsupported_input", "reason": "input:96000/24"})
    assert [i for i in _build_device_issues([dev]) if i.key == "air_sensor_unavailable"]

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
- Modify: `src/sendspin_bridge/config/players.py` (`AirLatencyEntry`, `Player.air_to_sound_latency: list[AirLatencyEntry] | None = None`, ≤ 8, unique (codec, rate, adapter)), `src/sendspin_bridge/config/schema.json` (player definition), `src/sendspin_bridge/config/__init__.py` (`resolve_air_latency(player_cfg, codec, rate, adapter) -> float | None`), `src/sendspin_bridge/services/infrastructure/config_diff.py` (`air_to_sound_latency` in `_DEVICE_HOT_FIELDS`), `src/sendspin_bridge/bridge/client.py` (`apply_hot_config` sends `SetAirCalibration`; resend on codec/rate/adapter change from the `air_sensor` status), `src/sendspin_bridge/application/devices.py` + `api/routers/devices.py` (`DELETE …/air-calibrations[/{codec}/{rate}/{adapter}]`), `src/sendspin_bridge/application/models/devices.py` (`DeviceAudio.air_to_sound_latency`), `ui/src/components/devices/DeviceTimingPanel.vue` (line + Forget), i18n
- Test: `tests/unit/config/test_air_sensor_config.py` (calibration part), `tests/unit/api/test_air_sensor_api.py` (calibration part)

**Interfaces:**

```python
class AirLatencyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codec: Literal["sbc"]
    rate: Literal[16000, 32000, 44100, 48000]
    adapter: str = Field(pattern=r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
    latency_ms: float = Field(ge=0, le=2000)
    spread_ms: float | None = Field(None, ge=0, le=500)
    measured_at: str
    reference: str = Field("", max_length=80)
    source: Literal["sync_wizard", "manual"] = "sync_wizard"

def resolve_air_latency(player_cfg: Mapping[str, Any], codec: str | None, rate: int | None, adapter: str | None) -> float | None: ...
def upsert_air_latency(entries: list[dict], entry: dict, *, max_entries: int = 8) -> list[dict]: ...  # key (codec, rate, adapter); drop oldest beyond 8
```

- [ ] **Step 1: Failing tests**

```python
# config part
from sendspin_bridge.config import resolve_air_latency, upsert_air_latency
E = {"codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6", "latency_ms": 203.4, "measured_at": "2026-10-28T09:00:00+00:00"}

def test_resolve_by_codec_rate_and_adapter():
    p = {"air_to_sound_latency": [E]}
    assert resolve_air_latency(p, "sbc", 44100, "C0:FB:F9:62:D7:D6") == 203.4
    assert resolve_air_latency(p, "sbc", 48000, "C0:FB:F9:62:D7:D6") is None
    assert resolve_air_latency(p, "sbc", 44100, "C0:FB:F9:62:D6:9D") is None
    assert resolve_air_latency(p, "aac", 44100, "C0:FB:F9:62:D7:D6") is None

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
        Player.model_validate({"id": "p", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                               "air_to_sound_latency": [{**E, "latency_ms": -1}]})

def test_calibration_change_is_hot():
    from sendspin_bridge.services.infrastructure.config_diff import iter_device_hot_fields
    assert "air_to_sound_latency" in iter_device_hot_fields()
```

```python
# api part
def test_forget_one_calibration(api_client, running_device_with_calibration):
    r = api_client.delete(f"/api/v1/devices/{running_device_with_calibration.id}/air-calibrations/sbc/44100/C0:FB:F9:62:D7:D6")
    assert r.status_code == 204
    assert api_client.get(f"/api/v1/devices/{running_device_with_calibration.id}").json()["audio"]["air_to_sound_latency"] == []
    assert running_device_with_calibration.sent_commands[-1] == {"cmd": "set_air_calibration", "latency_ms": None, "protocol_version": 2}

def test_forget_unknown_is_404(api_client, running_device_with_calibration):
    assert api_client.delete(f"/api/v1/devices/{running_device_with_calibration.id}/air-calibrations/sbc/44100/00:00:00:00:00:00").status_code == 404
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; the parent resolves the latency from `air_sensor.adapter`, `air_sensor.codec` and the SBC rate the daemon reports (`air_sensor.rate`, add it to the A9 snapshot) (falls back to the player's configured adapter and `bt_codec_name`), sends `SetAirCalibration` at spawn (spawn param) and when the resolved value changes. UI line "Speaker latency: 203.4 ms · SBC 44.1 kHz · hci1 · 2 days ago" with Forget (confirm dialog).
- [ ] **Step 4: Run** → PASS; regenerate OpenAPI and the SPA client; `npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(config): per-speaker air-to-sound latency, applied live`.

### Task A16a: Fold helpers as a library; short-span click phase

**Files:**
- Create: `src/sendspin_bridge/services/bluetooth/a2dp_air_fold.py` (moved from `scripts/a2dp_air_phase.py`: `chirp_subbands`, `frame_energy`, `unwrap_rtp`, `click_phase_ms`, `sbc_encoder_delay_samples`; plus `click_fold_found(rows, frame_s, epoch_s, encoder_delay_s) -> bool`, the "clicks present" criterion: pattern-score peak at least 3× the median score)
- Modify: `scripts/a2dp_air_phase.py` (imports the helpers; names re-exported so its tests stay green unchanged)
- Test: `tests/unit/services/bluetooth/test_a2dp_air_fold.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/services/bluetooth/test_a2dp_air_fold.py
import pytest
from sendspin_bridge.services.bluetooth.a2dp_air_fold import click_fold_found, click_phase_ms
from sendspin_sync import click_track as ct

def click_rows(phase_ms, start_s, span_s, frame_s=128 / 44100):
    """Energy rows of an ideal click track whose click 0 chirp starts at phase_ms on the fold axis."""
    rows, t = [], start_s
    while t < start_s + span_s:
        rel = ((t - phase_ms / 1000.0 - ct.LEAD_IN_S) % ct.CYCLE_S) * 1000.0
        k, within = divmod(rel, ct.PERIOD_S * 1000.0)
        hot = within < ct.CHIRP_MS or ct.gap_ms(int(k)) <= within < ct.gap_ms(int(k)) + ct.CHIRP_MS
        rows.append((t, 1000.0 if hot else 1.0)); t += frame_s
    return rows

@pytest.mark.parametrize("span_s", [5.0, 6.0, 7.0, 11.9])
@pytest.mark.parametrize("start_s", [0.3, 4.1, 7.7])
def test_partial_cycle_spans_still_give_the_phase(span_s, start_s):
    rows = click_rows(3210.0, start_s, span_s)
    assert click_fold_found(rows, 128 / 44100, 0.0, 0.0)
    assert click_phase_ms(rows, 128 / 44100) == pytest.approx(3210.0 + 128 / 44100 * 500, abs=1.5)

def test_no_clicks_is_not_found():
    rows = [(i * 0.0029, 1.0) for i in range(2000)]
    assert not click_fold_found(rows, 0.0029, 0.0, 0.0)

def test_script_still_exports_the_helpers():
    import scripts.a2dp_air_phase as tool
    assert tool.click_phase_ms is click_phase_ms
```

(The `+ frame_s·500` term is the half-frame offset of rows dated at their first sample; adjust the expectation to the helper's documented convention when implementing, and keep the tolerance.)

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the move and `click_fold_found`. **Step 4: Run** the new file and `tests/unit/services/bluetooth/test_a2dp_air_phase_script.py` → PASS.
- [ ] **Step 5: Commit** `refactor(bluetooth): click-fold helpers as a library, checked on partial cycles`.

### Task A16b: Air and written folds (service and daemon)

**Files:**
- Modify: `src/sendspin_bridge/services/bluetooth/air_sensor_service.py` (`AirClickFold`; `arm_fold(mac, from_us)`, `finish_fold(fold_id, to_us) -> AirFoldResult`), `src/sendspin_bridge/services/audio/playout/air_sensor.py` (`fold(to_us, span_s=12.0) -> tuple[float | None, str | None]` on the tap's `T` cells), `src/sendspin_bridge/services/ipc/bridge_daemon.py` (answer `AirFold` into `air_sensor.fold`)
- Test: `tests/unit/services/bluetooth/test_air_sensor_service.py` (fold part), `tests/unit/services/audio/playout/test_air_sensor.py` (fold part)

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class AirFoldResult:
    phase_ms: float | None          # RAW axis, epoch 0, PCM instant (d_enc removed)
    found: bool
    epochs: int                     # epochs seen inside the span (must be 1 for a calibration)
    codec: str | None
    rate: int | None
    adapter: str | None

class AirClickFold:
    def __init__(self, *, from_us: float, max_s: float = 30.0) -> None: ...
    def add_frame(self, t_cell_us: float, frame: SbcFrame, epoch: int) -> None: ...
    def result(self, to_us: float) -> AirFoldResult: ...

# AirSensor
def fold(self, to_us: float, span_s: float = 12.0) -> tuple[float | None, str | None]: ...
    # folds program cells (mute ignored) with T in [to_us - span_s, to_us] on the T axis; d_comb removed;
    # fewer than 5 s of program cells since the last reopen -> (None, "no_written_clicks")
```

- [ ] **Step 1: Failing tests**

```python
# fold part, service
def test_air_fold_on_the_fixture_equals_the_phase_tool():
    # the fixture's frames at RAW = unix·1e6 − OFFSET_US through the tracker's anchor give the tool's phase − OFFSET mod cycle
    res = fold_fixture(offset_us=OFFSET_US)
    assert res.found and res.epochs == 1
    assert res.phase_ms == pytest.approx((TOOL_PHASE_MS - OFFSET_US / 1000.0) % 10_000.0, abs=0.5)

def test_air_fold_without_sbc_frames_is_not_found():
    f = AirClickFold(from_us=0.0)
    assert not f.result(6e6).found

# fold part, daemon sensor
def test_written_fold_on_the_heard_axis(click_world):
    w = click_world(output_delay_ms=180.0, click0_heard_raw_us=5_000_123_000.0)
    w.write_seconds(14)
    phase, reason = w.sensor.fold(w.t(13.0))
    assert reason is None
    assert phase == pytest.approx((5_000_123_000.0 / 1000.0 - 1000.0) % 10_000.0, abs=0.5)

def test_written_fold_ignores_mute(click_world):
    """MA mutes the speaker during other members' solos; mute acts after the tap, the PCM still has clicks."""
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0, muted=True)
    w.write_seconds(14)
    phase, reason = w.sensor.fold(w.t(13.0))
    assert reason is None and phase == pytest.approx((1_000_000.0 / 1000.0 - 1000.0) % 10_000.0, abs=0.5)

def test_written_fold_needs_5s_since_the_last_reopen(click_world):
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14); w.reopen(); w.write_seconds(3)
    assert w.sensor.fold(w.t(16.5)) == (None, "no_written_clicks")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**: `AirClickFold` rows `(t_cell/1e6, frame_energy(frame))` folded with `click_phase_ms(..., encoder_delay_s = d_enc)`; the service arms one per bridge member during its solo (frames from that link's packets, `t_cell` from the tracker's anchor); the daemon's `fold` uses the tap's `T` per cell, rows `(T/1e6, 2**w)`, `click_phase_ms(rows, cell_s, 0.0, d_comb)` over program cells regardless of mute, `click_fold_found` or < 5 s of cells → `"no_written_clicks"`; `AirFold` replies in `air_sensor.fold = {id, phase_ms, ok, reason}`. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sync): click folds on the air and on the written schedule`.

### Task A16c: Wizard calibration, API and UI

**Files:**
- Modify: `src/sendspin_bridge/application/sync_tuning.py` (sequence watcher with RAW stamps; air span `[start + SETTLE_S + h, end − TAIL_S + h]` with `h` = the member's `playout.host_latency_ms` + 20 ms read at the solo's end; `AirFold` request after the span; candidates after `record_sequence`; save use case), `src/sendspin_bridge/application/models/sync.py` (`AirCalibrationCandidate`, `SyncSessionOut.air_calibrations`, `AirCalibrationSaveIn`), `src/sendspin_bridge/api/routers/sync.py` (`POST /api/v1/sync/sessions/{id}/air-calibrations`), `docs/contracts/sync-contract-v1.json`, `ui/src/components/sync/StepTune.vue`, `ui/src/i18n/{en,ru}.json`, `docs-site/src/content/docs/{,ru/}group-sync-tuning.md`
- Test: `tests/unit/api/test_sync_air_calibration.py`

**Interfaces:**

```python
def air_span(stamps: tuple[float, float], host_latency_ms: float | None) -> tuple[float, float]: ...
def air_calibration_candidates(session: SyncSession, folds: Mapping[str, tuple[AirFoldResult, tuple[float | None, str | None]]]) -> list[AirCalibrationCandidate]: ...
def save_air_calibrations(sid: str, members: list[str]) -> list[AirCalibrationCandidate]: ...

class AirCalibrationCandidate(ResponseModel):
    member: str; eligible: bool; reason: str | None
    latency_ms: float | None; acoustic_lag_ms: float | None; host_placement_ms: float | None
    codec: str | None; rate: int | None; adapter: str | None; delay_report_ms: float | None
```

- [ ] **Step 1: Failing tests** (`sync_air_env` stubs the estimator results and the two folds through the service seams; no `make_device`-style shared fixture is assumed — the env builds its own minimal session and devices):

```python
# tests/unit/api/test_sync_air_calibration.py
import pytest
from sendspin_bridge.application.sync_tuning import air_span

def test_air_span_is_offset_by_settle_and_host_latency():
    assert air_span((100.0e6, 107.0e6), 800.0) == pytest.approx((100.0e6 + 0.8e6 + 0.82e6, 107.0e6 - 0.3e6 + 0.82e6))
    assert air_span((100.0e6, 107.0e6), None) == pytest.approx((100.0e6 + 0.8e6 + 0.02e6, 107.0e6 - 0.3e6 + 0.02e6))

def test_latency_is_acoustic_lag_minus_host_placement(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0},
                       air_phase={"eneby": 2012.0}, sched_phase={"eneby": 2000.0})
    c = next(c for c in env.record_and_get_session()["air_calibrations"] if c["member"] == "eneby")
    assert c["eligible"] and c["acoustic_lag_ms"] == pytest.approx(215.0)
    assert c["host_placement_ms"] == pytest.approx(12.0) and c["latency_ms"] == pytest.approx(203.0)

def test_live_lock_is_not_required(sync_air_env):
    env = sync_air_env(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0},
                       air_phase={"eneby": 2012.0}, sched_phase={"eneby": 2000.0}, air_state="acquiring")
    assert next(c for c in env.record_and_get_session()["air_calibrations"] if c["member"] == "eneby")["eligible"]

@pytest.mark.parametrize(("kw", "reason"), [
    ({"effective": False}, "not_effective"),
    ({"reference": None}, "no_reference"),
    ({"acoustic": {"kiosk": 1200.0, "eneby": None}}, "not_measured"),
    ({"air_phase": {"eneby": None}}, "no_air_clicks"),
    ({"sched_phase": {"eneby": None}}, "no_written_clicks"),
    ({"air_epochs": 2}, "air_epoch_changed"),
    ({"codec": "aac"}, "not_sbc"),
])
def test_ineligible_reasons(sync_air_env, kw, reason):
    base = dict(reference="kiosk", acoustic={"kiosk": 1200.0, "eneby": 1415.0}, air_phase={"eneby": 2012.0}, sched_phase={"eneby": 2000.0})
    c = next(c for c in sync_air_env(**{**base, **kw}).record_and_get_session()["air_calibrations"] if c["member"] == "eneby")
    assert not c["eligible"] and c["reason"] == reason

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
    assert (entry["codec"], entry["rate"], entry["reference"], entry["source"]) == ("sbc", 44100, "kiosk", "sync_wizard")
    assert entry["latency_ms"] == pytest.approx(203.0)
    assert env.sent("eneby")[-1] == {"cmd": "set_air_calibration", "latency_ms": 203.0, "protocol_version": 2}
```

The `sendspin_sync` core must not change: its existing tests (`tests/unit/sendspin_sync`) run unchanged in Step 4, and review checks `git diff --stat -- src/sendspin_sync` is empty.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: the watcher thread in `start_sequence`/`capture_start` (polls `session.sequence` every 100 ms; stamps RAW at each member's `solo` start and the following `gap` start; arms the air fold at `air_span` start, finishes it at its end, then sends `AirFold` to the daemon and waits ≤ 2 s for `air_sensor.fold`); after `record_sequence`, candidates (reference = the session's reference; `A = unwrap_lag_ms(φ_spk, φ_ref)`; `H_cal = unwrap(φ_air − φ_T)` into ±5000 ms; `L_as = A − H_cal`; eligibility in the spec's order with its reason list (`not_effective`, `no_reference`, `not_measured`, `not_sbc`, `air_epoch_changed`, `no_air_clicks`, `no_written_clicks`) — no lock requirement; `delay_report_ms` from `bt_reported_delay_ms` as a hint); save via `upsert_air_latency` under `config_lock` and hot-apply; router, contract, StepTune rows for eligible members with the value, the hint and a "Save as this speaker's latency" checkbox, ineligible members with their reason in words (en/ru); docs section (requirements: a reference on a headset or wired output; why).
- [ ] **Step 4: Run** the test file, `tests/unit/sendspin_sync`, `tests/unit/api/test_sync_api.py` → PASS; regenerate OpenAPI and the SPA client; `npm test && npm run build`.
- [ ] **Step 5: Commit** `feat(sync): calibrate a speaker's air-to-sound latency in the tuning wizard`.

### Task A17: Stand validation, gate calibration, decision note, docs, changelog

**Files:**
- Modify: `docs/plans/2026-10-28-air-sensor-design.md` (append "Stage A validation note"; update R-7, R-9, R-10, R-16 rows with the measured values), `docs-site/src/content/docs/{,ru/}bluetooth-speakers.md`, `{,ru/}configuration.md`, `CHANGELOG.md`
- Create (not committed): `scratch/air_validation/` (recordings, captures, CSV)

**Interfaces:**
- Consumes: the HAOS stand (ENEBY20 on hci1, PulseAudio 17, `PLAYOUT_SYNC=measure`, `EXPERIMENTAL_AIR_SENSOR=true`, `PREFER_SBC_CODEC=true`, `SENDSPIN_AIR_DEBUG=1`), the PipeWire Docker VM, the desktop reference bridge in `off`, a headset microphone, the coherent click estimator, `btmon -w` on the bridge host, `scripts/a2dp_air_phase.py`, `scripts/air_envelope_corr.py` (A8b), `scripts/air_sensor_bench.py`.
- Produces: the acceptance results and the settled constants (gate, N, window, envelope choice) in the spec; stage B starts from them.

- [ ] **Step 1: Calibrate** ENEBY20 once in the wizard (reference: a Sendspin web player on a headset, or the desktop reference bridge's wired output); save; note `L_as`, `spread_ms` and the Delay Report hint (absent on ENEBY20).
- [ ] **Step 2: ≥ 8 cold starts** with the gate experiment's protocol (stop, ~6.5 s gap, new stream; MA mutes alternating the solo; one continuous headset recording; music for 60 s, then the click track for 40 s per start), with `btmon -w` running for the whole series. Save the daemon's DEBUG window lines (`t_mid`, `s_mid`, `H`, PSR) and the 1 Hz status to CSV with RAW stamps.
- [ ] **Step 3: Primary acceptance — live vs offline `H` on the same capture.** For every confirmed live window, recompute the air time of the same PCM offline from the `btmon` capture (independent parser run; a **local anchor** = the median of `t_send − u` over the packets within ±1 s of the window, segmented at skips and epochs, independent of the live running median; the same `d_enc`), `H_offline = t_air − s_mid`. Add this mode to `scripts/air_envelope_corr.py` (`--offline-h daemon_windows.csv`). Pass: p95 of `|H_live − H_offline|` ≤ 2 ms (target ≤ 1 ms), music and clicks.
- [ ] **Step 4: Microphone acceptance.** Per start `i`: `M_i` = ENEBY − reference from the estimator on the click part; `H_i` = the median live `H` of the confirmed windows whose middle lies inside exactly the span `M_i` is estimated on (same RAW span, mapped through the recording alignment). Pass: `std(M − H) ≤ 4 ms` and `var(M − H) ≤ 0.25 · var(M)` over ≥ 8 starts, with the sensor locked for ≥ 80 % of those spans. Report `median(M − H)` vs the saved `L_as` (their difference is the reference's own error).
- [ ] **Step 5: Absolute `H` on both stands.** Repeat 3 cold starts on the PipeWire VM with an SBC speaker; record absolute `H` on both stands and confirm it lies well inside the ±500 ms acquisition range (if not, widen it and record why).
- [ ] **Step 6: Gate, window and envelope on real music.** With `scripts/air_envelope_corr.py` on the stand captures and the private morning capture: PSR distributions of confirmed and rejected windows with the implementation's definition at 3 s and at 5.8 s windows, comb vs band-pass envelope. Set `PSR_GATE` (target: < 1 % false confirms on noise windows — source WAV of another track — and ≥ 80 % of music windows confirmed), keep or switch the envelope (R-7: band-pass if the comb is > 0.05 worse), keep or change the window (R-16) and N (R-10). Re-run the unit and simulation tests after any constant change.
- [ ] **Step 7: Extra runs:** 3 "seek" cycles and 3 reconnects (`M − H` spread); 8 cycles with `pactl suspend-sink` in the gaps on the PipeWire VM (the `bt_sink_suspend_in_gaps` issue fires; `air_sensor.epochs` grows); `PREFER_SBC_CODEC=false` with an AAC-capable speaker if one is available (state `unsupported_codec`, guidance shown); a 96 kHz/24-bit source (state `unsupported_input`); CPU with one speaker on the HAOS VM (x86, 2 vCPU) and the desktop: `pidstat` of the parent and the daemon with and without the switch (budget ≤ 3 % of one core per speaker) and `scripts/air_sensor_bench.py`. No Raspberry Pi is available: state Pi cost as unverified in the docs.
- [ ] **Step 8: Decision note** appended to the spec:

```markdown
## Stage A validation note (YYYY-MM-DD, HAOS stand + PipeWire VM, 3.1.0-beta.N)

- Calibration: L_as = … ms (spread … ms), reference …
- Live vs offline H (n windows = …): p95 |ΔH| = … ms → PASS/FAIL (≤ 2 ms).
- Cold starts (n = …): std(M − H) = … ms, var(M − H)/var(M) = … → PASS/FAIL (≤ 4 ms, ≤ 0.25).
- Absolute H: HAOS … ms, PipeWire … ms (acquisition ±500 ms kept / changed).
- Lock share: music … %, clicks … %. PSR (impl, 3 s) confirmed median …, noise max … → gate …; envelope comb/band-pass …; window ….
- Seek / reconnect: M − H spread … / … ms.
- Suspend check on PipeWire: fired yes/no; epochs ….
- Fallbacks: codec …, input …, capability ….
- CPU (one speaker): HAOS VM parent … %, daemon … %; desktop … % (budget 3 %); Pi unverified.
- Changes for stage B: dead band from σ(H) = … ms; ….
```

- [ ] **Step 9: Docs and changelog.** `bluetooth-speakers.md` (en/ru): what the air sensor is, requirements (SBC, `NET_RAW`, measure, 44.1/48 kHz sources), what the numbers mean, how to calibrate, that it needs a bridge restart to switch, Pi cost unverified; `configuration.md`: the switch. `CHANGELOG.md` `### Added`: "Experimental air sensor (`EXPERIMENTAL_AIR_SENSOR`, off by default): with Playout sync = measure and SBC, the bridge reports when each speaker's audio actually leaves over the air and how far that is from its schedule; the group sync tuning wizard can calibrate a speaker's latency from the air to the sound. Playback is not changed." Lint with `uv run python scripts/lint_changelog.py CHANGELOG.md`.
- [ ] **Step 10: Commit** only the spec, docs and changelog: `docs(sync): air sensor stage A validation note and user docs`; delete `scratch/`.

---

## Stage B — the engine corrects from the air sensor (outline)

Not detailed until Task A17's note exists; each item becomes a task in the A-task format then.

- **B1: Effective rule for `loop`.** `air_sensor_effective` accepts `loop`; spawn params and status unchanged.
- **B2: Bias estimator.** In the engine, a pure `AirBias` filter: input `(t, E, confidence)` from the air sensor (≈ 4 s late, 1 Hz) and the pulsesink error history `e_pulse` for the same audio (aligned by `x`, not by arrival time); output `b = median(e_pulse − E)` with a time constant ≥ 30 s; frozen when the air sensor is not `locked` or confidence < 0.5. **Reset on every output reopen** (every stream start and seek) and seeded from the median of the first three confirmed windows of the new stream, so a cold start converges in seconds. Tests with the A10 simulation extended by a pulsesink sensor with a per-start bias of ±15 ms (Stage 1 finding).
- **B3: Controller input.** The loop acts on `e_pulse − b` (the inner loop keeps the pulsesink cadence); a persistent `|b|` beyond the re-derived dead band (from σ(H) of A17) triggers a placement jump under R14's persistence rules; measure mode reports the corrected error but still only jumps at 20 ms.
- **B4: Output delay = `L_as`.** With a calibration, the speaker's output delay follows `L_as` through the Stage 4 offset path (source `air_calibration`), so `E = H` and the target is `H = 0`; the playout spec's Stage 4 precedence table gains the entry.
- **B5: Completion anchor (optional).** If open question 3 shows a lower σ for completion-anchored `H`, switch the anchor together with a forced re-calibration (entries carry the anchor).
- **B6: Validation.** The playout spec's S1–S3 on the stand with the loop driven by the air bias; cold-start spread target < 5 ms.
