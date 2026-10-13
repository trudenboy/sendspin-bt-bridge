# Config v6, stage 3 (local audio output players) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `pulse_sink` player — any local PulseAudio/PipeWire sink (USB DAC, HDMI, analogue out) — can be added from the SPA and plays as a Music Assistant player, surviving unplug/replug and sink renames.

**Architecture:** A sink inventory module reads local sinks with their stable identifiers and matches a player's stored `backend.match` to the current sink. Activation dispatches by `backend.type`; a `pulse_sink` player is a `SendspinClient` without a Bluetooth manager whose sink is fixed. The reconfig pipeline (`config_diff`, `ReconfigOrchestrator`) is keyed by player id instead of MAC so non-Bluetooth players start/stop live. `SinkMonitor` gains presence callbacks for non-Bluetooth sinks and drives standby on unplug. The SPA gets an "Audio output" tab and backend-neutral device state.

**Tech Stack:** Python 3.13/3.14, pulsectl-asyncio (+ `pactl` fallback), FastAPI, Pydantic v2, pytest; Vue 3 + TS, vitest, Playwright (live check).

**Spec:** [`docs/plans/2026-10-12-config-v6-players-design.md`](2026-10-12-config-v6-players-design.md) — section 3 and stage 3 of section 6. Stages 1–2 merged into `v3` (PRs #528, #529).

## Decisions carried from stage 2 (binding)

* **Music Assistant identity.** A running Bluetooth speaker's runtime id (`client.player_id`, sent to MA as the Sendspin `client_id`) is uuid5 of its *name*, not of its MAC. **Do not change it** — that creates new MA players. A `pulse_sink` player's runtime id is its config id (uuid4), stable from creation. Every client gets `client.config_id` (its player id in the config); APIs keep `Device.id` = runtime id and `Device.config_id` = config id.
* `Device.supported_actions` for `pulse_sink` = `volume`, `transport`, `power_save`; Bluetooth-only commands answer 409 (stage 2).
* `backend.match` is server-managed.

## Deviations from the spec (stage 3)

* Formats from DAC capabilities (spec stage 4) are not in this plan.
* The optional startup sink reset is a global setting `PULSE_SINK_RESET_ON_START` (default off), not per player.

## Global Constraints

* Sink inventory excludes `bluez_*` sinks and the bridge's own `sendspin_fallback` / `sendspin_standby`.
* Match order: `device.serial` → `device.bus_path` → `device.vendor.id`+`device.product.id` (only if exactly one sink has them; with several, narrowed by `alsa.long_card_name`) → stored `backend.sink`. No match → `sink_missing`; ambiguous → `sink_ambiguous`; claimed by an earlier player in list order → `sink_in_use`.
* A `pulse_sink` daemon never starts without its sink (no fallback to the default sink).
* Unplug → standby (`SetStandby(sink=sendspin_standby)`, streams moved to the null sink), event `sink_lost`; replug → `SetStandby(sink=<sink>)`, event `sink_restored`; renamed sink found by match → `backend.sink` updated in the config, event `sink_rematched`.
* `pulse_sink` defaults to `volume_controller: sendspin` when unset; the bridge never changes a shared output's volume unless `volume_controller: pa` was chosen.
* Guidance warnings for a `pulse_sink` sink: muted, volume 0 %, active port `not available` (`availability unknown` is not a warning).
* All CI gates before every commit that touches them: `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/`; `uv run ruff check src tests && uv run ruff format --check src tests`; `uv run python scripts/export_openapi.py --check`; in `ui/`: `npm test`, `npm run build`, `npm run check:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md`.
* TDD per `CLAUDE.md`. Bluetooth behaviour must not change.

## Review Focus

1. **Bluetooth unchanged** — after the reconfig re-keying, adding/removing/editing a Bluetooth speaker live does exactly what it did (same actions, same ports, same MA ids). (Task 3)
2. **Two identical USB DACs** without serials, distinguished only by `bus_path` — each player keeps its own DAC across replug and reboot. (Task 1)
3. **DAC unplugged while playing** — no audio goes to another output; the player stays in its MA group, silent; replug resumes. (Task 5)
4. **Shared HAOS output** — adding a `pulse_sink` player on the HA audio sink does not change that sink's volume on start, on MA volume commands, or on standby. (Tasks 4, 5)
5. **pulse_sink added with no sink present** (unplugged at add time) — saved, shown as "output missing", starts when plugged in. (Tasks 2, 4, 5)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/services/audio/sink_inventory.py` | **new** — `LocalSink`, `list_local_sinks()`, `match_sink()`, pactl text parser |
| `src/sendspin_bridge/api/routers/audio.py`, `application/audio.py` | **new** — `GET /audio/sinks` |
| `src/sendspin_bridge/application/config.py` | fill `backend.match` from the inventory on save |
| `src/sendspin_bridge/services/infrastructure/config_diff.py`, `services/lifecycle/reconfig_orchestrator.py`, `services/bluetooth/device_registry.py` | key by player id |
| `src/sendspin_bridge/services/bluetooth/device_activation.py` (→ `activate_player`), `bridge/orchestrator.py`, `bridge/client.py` | pulse_sink activation, `config_id`, status keys |
| `src/sendspin_bridge/services/audio/sink_monitor.py` | presence callbacks for non-Bluetooth sinks |
| `src/sendspin_bridge/services/diagnostics/*` | sink health guidance, startup reset |
| `src/sendspin_bridge/application/models/devices.py`, `application/status.py` | backend from status for pulse_sink, `audio.sink_missing` |
| `ui/src/components/bluetooth/AddSpeakerDialog.vue` (or a new `AddPlayerDialog.vue`), device card/row/drawer, `settings/device.ts`, i18n | Audio output tab, backend-neutral state, pulse_sink settings |
| `tests/fixtures/pactl/*.txt` | captured `pactl list sinks` outputs |

---

### Task 1: Sink inventory and matching

**Files:** create `src/sendspin_bridge/services/audio/sink_inventory.py`, `tests/unit/services/audio/test_sink_inventory.py`, `tests/fixtures/pactl/` (3 captured outputs: one USB DAC with serial; two identical USB DACs without serial on different ports; HDMI + analogue on one card).

**Interfaces (produces):**

```python
@dataclass(frozen=True)
class LocalSink:
    name: str
    description: str
    state: str                    # running | idle | suspended | unknown
    sample_spec: str              # e.g. "s16le 2ch 48000Hz"
    muted: bool
    volume: int                   # 0-100 (loudest channel)
    active_port: str | None
    active_port_available: str | None   # "available" | "not available" | "availability unknown" | None
    identifiers: dict[str, str]   # serial, bus_path, vendor_id, product_id, alsa_long_card_name (only present keys)

def parse_pactl_sinks(text: str) -> list[LocalSink]: ...      # `pactl list sinks` output
async def list_local_sinks() -> list[LocalSink]: ...         # pulsectl first, pactl fallback; excludes bluez_*/sendspin_*
def match_sink(match: dict, stored_name: str | None, sinks: list[LocalSink], *, claimed: set[str] = frozenset()) -> SinkMatch
@dataclass(frozen=True)
class SinkMatch:
    sink: LocalSink | None
    method: str | None            # serial | bus_path | vendor_product | alsa_long_card_name | name
    status: str                   # ok | sink_missing | sink_ambiguous | sink_in_use
```

Property keys read: `device.serial`, `device.bus_path`, `device.vendor.id`, `device.product.id`, `alsa.long_card_name`. With pulsectl, read `sink.proplist`, `sink.state`, `sink.sample_spec`, `sink.port_active` (`available` → map 0/1/2 to unknown/not available/available as pulsectl exposes it — check the attribute name in the installed pulsectl).

- [ ] **Step 1:** capture fixtures. If no real multi-DAC output is available, hand-write them in the exact `pactl list sinks` format (copy a real block from this host for structure: `pactl list sinks` — read-only).
- [ ] **Step 2: failing tests** — parser extracts identifiers/port availability for each fixture; `bluez_*` and `sendspin_*` excluded by `list_local_sinks` (monkeypatch the source); matching table: serial hit; serial absent → bus_path hit; vendor:product unique hit; vendor:product twice + alsa name narrows; vendor:product twice without tie-breaker → `sink_ambiguous`; only stored name → `name`; nothing → `sink_missing`; `claimed` contains the found sink → `sink_in_use`. Review focus 2: the two-identical-DACs fixture resolves each player to its own `bus_path`.
- [ ] **Step 3: implement. Step 4: green + mypy/ruff. Step 5: commit** `feat(audio): local sink inventory with stable device matching`.

---

### Task 2: `GET /audio/sinks` and server-filled `backend.match`

**Files:** create `src/sendspin_bridge/application/audio.py`, `src/sendspin_bridge/api/routers/audio.py` (register in `api/app.py`); modify `src/sendspin_bridge/application/config.py` (the stage-2 converter that sets `backend.match` from the stored player); tests `tests/unit/api/test_audio_sinks.py`, extend `tests/unit/api/test_config_v6_api.py`.

**Interfaces:** `GET /api/v1/audio/sinks` → `list[AudioSinkOut]` with `{name, description, state, sample_spec, muted, volume, active_port_available, identifiers, in_use_by: str | None}` (`in_use_by` = config id of the `pulse_sink` player whose `backend.sink` is that name). Response model in `application/models/audio.py`.

Save rule: for a `pulse_sink` payload player, if the stored player has the same `backend.sink` and a non-empty `match`, keep it; otherwise compute `match` from the inventory entry with that name (`identifiers`); if the sink is not present now, save with `match: {}` (review focus 5 — the player is still saved).

- [ ] Failing tests: list excludes bluez/sendspin sinks and fills `in_use_by`; saving a new pulse_sink player whose sink is present stores its identifiers as `match`; with the sink absent the save succeeds with an empty match; changing `backend.sink` recomputes `match`; client-sent match still ignored.
- [ ] Implement (inventory read in the executor; one read per request), regenerate OpenAPI + TS client, all gates. Commit `feat(api): list local audio outputs; remember a pulse_sink's device identifiers`.

---

### Task 3: Reconfig keyed by player id

**Files:** `config_diff.py` (`_devices_by_mac` → by player id over **all** players; `ReconfigAction` gains `player_id`, keeps `mac` for Bluetooth-only actions like `BT_REMOVE`), `reconfig_orchestrator.py` (clients looked up by `client.config_id`; `_drop_from_registry` by id), `device_registry.py` (`client_map_by_config_id()`), `application/config.py` unpair logic unchanged (MAC-based, Bluetooth only); tests: `tests/unit/config/test_config_diff.py`, `test_config_diff_v6.py`, `tests/unit/services/lifecycle/test_reconfig_orchestrator.py`.

Rules: a player is identified across old/new config by `id`; the start index for the `base_listen_port + index` fallback stays the Bluetooth position for Bluetooth players (unchanged ports) and is the position among **all** players after the last Bluetooth one for others — simplest stable rule: non-Bluetooth players get `base_listen_port + (number of Bluetooth players) + (their position among non-Bluetooth players)`; document it; test it.

`client.config_id` must exist before this task's code runs: add it in this task to `SendspinClient.__init__` (default `None`) and set it in `activate_device` from the device entry's `id` (stage-1 `device_entries` carry `id`).

- [ ] Failing tests: review focus 1 — the existing config_diff/reconfig test suites pass unchanged in meaning (only the input builders may change to players); new: adding a pulse_sink player yields `START_CLIENT` with `player_id`; removing one yields `STOP_CLIENT`; editing a hot field on it yields `HOT_APPLY` targeting the client found by `config_id`; Bluetooth adapter change still yields `BT_REMOVE` with its MAC.
- [ ] Implement; all gates. Commit `refactor(reconfig): identify players by id, not by MAC`.

---

### Task 4: Activating a `pulse_sink` player

**Files:** `services/bluetooth/device_activation.py` (rename entry to `activate_player(player, *, index, context, default_player_name)`; keep `activate_device` as a thin wrapper for one release or update both callers), `bridge/orchestrator.py` (`initialize_devices` iterates `get_players`-ordered players; Bluetooth via the existing path, `pulse_sink` via the new factory), `services/lifecycle/reconfig_orchestrator.py` start path, `bridge/client.py` (status keys `backend_type`, `audio_sink`, `sink_missing`, `sink_match_status`; `player_id` override for pulse_sink), `application/models/devices.py` (`from_status` builds `backend` from `backend_type`/`audio_sink`; `bluetooth=None` for pulse_sink), `application/status.py`.

Behaviour:
* `pulse_sink` client: `SendspinClient(..., bt_manager=None)`, `client.player_id = client.config_id = player["id"]` (MA identity = config id), `bluetooth_sink_name = <matched sink name>`; `volume_controller` defaults to `sendspin` when the player has none.
* On activation run `match_sink` against `list_local_sinks()` with `claimed` = sinks already taken by earlier players; status ok → start the daemon with that sink; otherwise do not start, set `sink_missing` / `sink_match_status` in status. If the matched name differs from `backend.sink`, update the config (`update_config`, find player by id) and publish `sink_rematched`.
* Must not run any Bluetooth code path (`run()` already skips BT when `bt_manager` is None — verify `start_sendspin` with no BT manager uses `bluetooth_sink_name`; add a guard if it falls back to the default sink).

- [ ] Failing tests (unit, with a fake inventory and a fake daemon spawner as existing client tests do): pulse_sink player with its sink present → daemon spawned with `PULSE_SINK=<sink>`, `player_id == config id`, volume controller `sendspin`; sink absent → no spawn, status `sink_missing`; renamed sink matched by serial → config `backend.sink` updated + event; Bluetooth activation unchanged (existing tests); `/devices` shows the pulse_sink device with `backend.type == "pulse_sink"`, `bluetooth is None`, `supported_actions` = the three. Review focus 4: no `aset_sink_volume` call on start for `sendspin` controller.
- [ ] Implement; all gates. Commit `feat(audio): pulse_sink players play to a local audio output`.

---

### Task 5: Hot-plug, standby and rematch

**Files:** `services/audio/sink_monitor.py` (`register_presence(key, match, stored_name, on_present(sink_name), on_absent())` for non-bluez sinks; on `new` run `match_sink` for registered keys; on `remove` of the matched sink call `on_absent`; polling fallback every `BT_CHECK_INTERVAL` when the monitor is unavailable), `bridge/client.py` (pulse_sink handlers: absent → standby to null sink + status `sink_missing` + event `sink_lost`; present → `SetStandby(sink=<name>)` + move streams + event `sink_restored`; if not running yet (absent at start) → start the daemon), events in `services/diagnostics/internal_events.py` / device event types.

- [ ] Failing tests driving `SinkMonitor` with fake `new`/`remove` events (pattern of existing sink_monitor tests): unplug while running → standby command sent, streams moved to `sendspin_standby`, status `sink_missing`, event `sink_lost`; replug → restore, event `sink_restored`; replug under a new name matched by serial → `sink_rematched` + config updated; absent at start then plugged → daemon starts (review focus 5); review focus 3: during unplug nothing is routed to another sink (assert the only move target is `sendspin_standby`); polling fallback detects the same transitions.
- [ ] Implement; all gates. Commit `feat(audio): pulse_sink players follow their device across unplug and replug`.

---

### Task 6: Sink health guidance and startup reset

**Files:** `services/diagnostics/recovery_assistant.py` / `operator_guidance.py` / `guidance_issue_registry.py` (issues `output_muted`, `output_volume_zero`, `output_port_unavailable` for pulse_sink players, using the inventory), `config/__init__.py` defaults + `config/schema.json` + `application/models/config.py` (`PULSE_SINK_RESET_ON_START: bool = False`), activation (one `asuspend_sink(name, True)` then `False` before the first daemon start when enabled), SPA settings layout field (advanced, audio section) + i18n en/ru.

- [ ] Failing tests: muted sink → `output_muted` with a summary that MA volume cannot raise it; volume 0 → `output_volume_zero`; port `not available` → `output_port_unavailable`; `availability unknown` → no issue; Bluetooth players get none of these; reset on → suspend/resume called once per sink at activation, off → never.
- [ ] Implement; all gates (incl. ui layout tests for the new key). Commit `feat(diagnostics): warn when a local audio output is muted, at zero or unplugged; optional startup reset`.

---

### Task 7: SPA — Audio output players

**Files:** add-speaker dialog (new tab "Audio output": list from `GET /audio/sinks`, `in_use_by` greyed, name field → append `{name, enabled: true, backend: {type: 'pulse_sink', sink}}` and save), stores (`ui/src/stores/audio.ts` new), device card/row/drawer (backend-neutral state: for `pulse_sink` connected = `audio.has_sink && !audio.sink_missing`; label "Output missing" when `sink_missing`; no RSSI/battery/BT info; released not applicable), `settings/device.ts` (pulse_sink fields: `name`, `backend.sink` select from `/audio/sinks`, room, volume_controller, idle_mode without `auto_disconnect`, keep_alive_method without `infrasound`, listen_port; hide adapter), i18n en/ru, tests (vitest: add-output flow appends the right player; pulse device card shows connected/missing correctly; settings hide BT-only fields for pulse_sink).

- [ ] Failing vitest tests, implement, `npm test`, `npm run build`, `npm run check:api`. Commit `feat(ui): add and manage local audio output players`.

---

### Task 8: Mock sinks, live check, changelog, docs

**Files:** `services/audio/sink_inventory.py` (env `SENDSPIN_MOCK_SINKS=<path to pactl-format file>` replaces the real source; documented as dev-only), CHANGELOG `[Unreleased]` (`### Added`: local audio output players; `### Changed` if any), `docs-site` page for audio outputs (short: what it is, HAOS note — only sinks HA audio exposes; not bit-perfect), Playwright live check.

- [ ] Tests for the mock source. Live check (CLAUDE.md demo workflow; a separate instance on 8080 may run — use 8097+ free port, stop by PID): with `SENDSPIN_MOCK_SINKS` pointing at the two-DAC fixture, add an Audio output player in the SPA, see it in `/devices` with `backend.type == pulse_sink`; swap the fixture file to remove that sink and trigger the polling fallback → card shows "Output missing"; restore → back. If a real local sink exists on this host (`pactl list sinks short`), also add it for real and check a daemon starts with `PULSE_SINK` set (do not play audio loudly; do not change system volume).
- [ ] All gates; commit `docs: local audio output players`.
