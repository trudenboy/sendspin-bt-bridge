# Config v6, stage 2 (API v1 and SPA on `players`) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** API v1 (`/config`, `/devices`) and the SPA speak schema v6 — `players[]` with a typed `backend` — instead of the v5 shape; Bluetooth behaviour for users is unchanged.

**Architecture:** The wire contract changes: `GET/PUT /config`, `/config/validate`, export and import use `players`; `PLAYER_STATE` stays internal. Inside `save_config` the existing, well-tested pipeline (validation, unpair-on-delete, delay intent, MA duplicate warnings, reconfig diff) keeps running on the v5-shaped device view through two private converters, so this stage changes the contract, not the pipeline; stage 3 moves the pipeline to player ids. `/devices` gains `backend` and `supported_actions`; commands a backend cannot do answer `409 not_supported_for_backend`. The SPA edits players by id.

**Tech Stack:** Python 3.13/3.14, FastAPI, Pydantic v2, pytest; Vue 3 + TS, vitest, openapi-typescript.

**Spec:** [`docs/plans/2026-10-12-config-v6-players-design.md`](2026-10-12-config-v6-players-design.md) — section 2 and stage 2 of section 6. Stage 1: [`2026-10-12-config-v6-stage1-plan.md`](2026-10-12-config-v6-stage1-plan.md) (branch `feat/config-v6-stage1`, PR #528).

## Deviations from the spec (stage 2)

* **Field name.** `Device.capabilities` already exists (a dict of per-action availability and remediation, used by diagnostics). The spec's list of what a backend can do is therefore `Device.supported_actions: list[str]`.
* **Internal converters stay.** The spec says "the adapter is removed". The wire adapter is removed; `save_config` keeps two private converters between the payload's `players` and the v5-shaped view its pipeline uses. They go in stage 3 with the pipeline.
* **Validation errors** keep the bridge's current 400 structured shape for field errors (`{"errors": [{"field", "message"}]}`), with field paths `players[<i>]....`. The three spec rules are `422` problems with codes `player_backend_immutable`, `duplicate_player`, `option_not_supported_for_backend`.
* **Bug fix pulled in:** `GET /config` stops writing the running client's `listen_host` / `listen_port` into the speaker entries (that made an unchanged save warm-restart every speaker, a pre-existing bug). It reports them read-only as `_effective_listen_host` / `_effective_listen_port` on each player instead; `PUT` strips every `_`-prefixed key of a player.

## Global Constraints

* Bluetooth player id = `uuid5(NAMESPACE_DNS, mac.lower())`; `pulse_sink` player id = `uuid4()`; an existing player's id never changes.
* `PLAYER_STATE` is never in `GET /config`, `/config/export` or a `/config/validate` answer, and never accepted from a request.
* `backend.match` is server-managed: ignore a client value; keep the stored one.
* For an existing id, changing `backend.type` or `backend.mac` → `422 player_backend_immutable`; changing `backend.sink` is allowed.
* Two players with one MAC or one sink → `422 duplicate_player`.
* `auto_disconnect` or `keep_alive_method: infrasound` on `pulse_sink` → `422 option_not_supported_for_backend`.
* Commands outside a device's `supported_actions` → `409 not_supported_for_backend` (RFC 9457 problem), checked in one place.
* `supported_actions` vocabulary: `volume`, `transport`, `power_save`, `reconnect`, `repair`, `standby`, `claim`, `pairing_window`, `rssi`, `battery`. Bluetooth: all ten. `pulse_sink`: `volume`, `transport`, `power_save`.
* Player order in the file follows the payload's `players` order.
* `/config/import` accepts v5 and v6 files.
* CI gates (run all before every commit that touches them): `uv run pytest -q -p no:cacheprovider`; `uv run mypy --config-file pyproject.toml src/sendspin_bridge/`; `uv run ruff check src tests && uv run ruff format --check src tests`; `uv run python scripts/export_openapi.py --check`; in `ui/`: `npm test`, `npm run build`, `npm run check:api`; `uv run python scripts/lint_changelog.py CHANGELOG.md`.
* TDD per `CLAUDE.md`. Tests that assert the v5 **API shape** are updated to v6; keep what they check, say so in the commit message.

## Review Focus

1. **Untouched SPA save** — GET then PUT the same document: same ids, order, `PLAYER_STATE`, and **no reconfig actions** (now including running speakers, the bug this stage fixes). (Task 3)
2. **Settings drawer save of one speaker** keeps every other player, including a `pulse_sink` entry written by hand. (Tasks 3, 6)
3. **Old client sends v5** (`BLUETOOTH_DEVICES`) to `PUT /config` — answer `400` with a clear message, never a silent wipe of all speakers. (Task 3)
4. **Add a speaker from the scan dialog** — new player gets `uuid5(mac)`, appears once, re-adding the same MAC is a no-op. (Tasks 3, 6)
5. **Validation errors point at the right player** — an invalid `listen_port` on the 3rd player (with a `pulse_sink` player before it) is reported as `players[2].listen_port`. (Task 2)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/application/models/config.py` | `BridgeConfig`: `players: list[Player]`, schema `Literal[6]`, no `BLUETOOTH_DEVICES`/`LAST_*` |
| `src/sendspin_bridge/config/players.py` | `Player.model_config` gets a JSON-schema title; `supported_actions_for(backend_type)` |
| `src/sendspin_bridge/services/infrastructure/config_validation.py` | validates `players`; field paths `players[i]`; drops `to_v6=False` use |
| `src/sendspin_bridge/config/migration.py`, `config/__init__.py` | remove the `to_v6` keyword (no caller left) |
| `src/sendspin_bridge/application/config.py` | v6 `get_config` / `validate_config` / `save_config` / `import_config`; converters renamed and private |
| `src/sendspin_bridge/application/devices.py`, `api/routers/devices.py`, `application/models/devices.py` | `backend`, `supported_actions`, 409 guard |
| `ui/openapi.json`, `ui/src/api/schema.d.ts` | regenerated |
| `ui/src/stores/bluetooth.ts`, `components/devices/DeviceSettingsPanel.vue`, `settings/device.ts`, `settings/layout.ts`, `components/bluetooth/AddSpeakerDialog.vue`, `i18n/en.json`, `i18n/ru.json`, device menus | SPA on `players` |
| `ui/tests/unit/settings/device.test.ts`, `ui/tests/unit/stores/bluetooth.test.ts` | updated / new |
| `CHANGELOG.md` | `[Unreleased]` |

---

### Task 1: Config API models and helpers

**Files:**
- Modify: `src/sendspin_bridge/application/models/config.py` (`BridgeConfig` around line 284; `BluetoothDevice` class stays only if something else imports it — grep, else delete; `LASTVOLUMES` likewise)
- Modify: `src/sendspin_bridge/config/players.py`
- Test: `tests/unit/config/test_players.py` (append), `tests/unit/api/test_openapi_v6.py` (new)

**Interfaces:**
- Produces:
  - `SUPPORTED_ACTIONS: dict[str, tuple[str, ...]]` and `supported_actions_for(backend_type: str) -> list[str]` in `config/players.py`
  - `BridgeConfig.players: list[Player]` in the OpenAPI document, `Player.backend` a discriminated union (`oneOf` with `discriminator.propertyName == "type"`)

- [ ] **Step 1: Failing tests**

```python
# tests/unit/config/test_players.py (append)
from sendspin_bridge.config.players import supported_actions_for


def test_supported_actions_per_backend():
    assert supported_actions_for("bluetooth_a2dp") == [
        "volume", "transport", "power_save", "reconnect", "repair",
        "standby", "claim", "pairing_window", "rssi", "battery",
    ]
    assert supported_actions_for("pulse_sink") == ["volume", "transport", "power_save"]
    assert supported_actions_for("unknown") == []
```

```python
# tests/unit/api/test_openapi_v6.py
"""The published config schema is v6."""

from sendspin_bridge.api.app import create_app


def _schemas():
    return create_app().openapi()["components"]["schemas"]


def test_bridge_config_has_players_not_bluetooth_devices():
    props = _schemas()["BridgeConfig"]["properties"]
    assert "players" in props
    for gone in ("BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS", "PLAYER_STATE"):
        assert gone not in props


def test_player_backend_is_a_discriminated_union():
    schemas = _schemas()
    backend = schemas["Player"]["properties"]["backend"]
    assert backend["discriminator"]["propertyName"] == "type"
    refs = {b["$ref"].rsplit("/", 1)[-1] for b in backend["oneOf"]}
    assert refs == {"BluetoothBackend", "PulseSinkBackend"}
```

Check how `create_app()` is imported in other API tests (`tests/support/api_client.py`) and copy that (it may need the isolated-config fixture).

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest -q -p no:cacheprovider tests/unit/config/test_players.py tests/unit/api/test_openapi_v6.py` → ImportError / KeyError `players`.

- [ ] **Step 3: Implement**

`config/players.py`:

```python
SUPPORTED_ACTIONS: dict[str, tuple[str, ...]] = {
    BLUETOOTH: (
        "volume", "transport", "power_save", "reconnect", "repair",
        "standby", "claim", "pairing_window", "rssi", "battery",
    ),
    PULSE_SINK: ("volume", "transport", "power_save"),
}


def supported_actions_for(backend_type: str) -> list[str]:
    """What a player of this backend can do (``Device.supported_actions``)."""
    return list(SUPPORTED_ACTIONS.get(backend_type, ()))
```

`application/models/config.py` — in `BridgeConfig`:

```python
    CONFIG_SCHEMA_VERSION: Literal[6] | None = None
    ...
    players: list[Player] = Field(default_factory=list)
    """
    Configured players. Each has a stable id and a backend: a Bluetooth speaker
    (bluetooth_a2dp) or a local audio output (pulse_sink).
    """
```

(import `Player` from `sendspin_bridge.config.players`), and delete the `BLUETOOTH_DEVICES`, `LAST_VOLUMES`, `LAST_SINKS` fields. Keep `extra="allow"`. Delete `BluetoothDevice`/`LASTVOLUMES` if nothing else imports them (`grep -rn "BluetoothDevice\b\|LASTVOLUMES" src ui/src tests`); `ui/tests/unit/settings/device.test.ts` reads `schemas.BluetoothDevice` — Task 6 moves it to `Player`, so leave the UI alone here and note it.

- [ ] **Step 4: Run, expect PASS**; also `uv run mypy --config-file pyproject.toml src/sendspin_bridge/`.

- [ ] **Step 5: Commit** — `feat(api): config schema v6 models (players with a typed backend)`

---

### Task 2: Validation on `players`

**Files:**
- Modify: `src/sendspin_bridge/services/infrastructure/config_validation.py` (`_validate_effective_listen_ports` ~152, `validate_uploaded_config` ~205-409)
- Modify: `src/sendspin_bridge/config/migration.py` (`migrate_config_payload` — drop `to_v6`), `src/sendspin_bridge/config/__init__.py` (wrapper signature)
- Test: `tests/unit/config/test_config_validation.py` (move its payloads to v6), `tests/unit/config/test_config_validation_v6.py` (new)

**Interfaces:**
- Consumes: Task 1 models; stage 1 `migrate_config_payload` (now always v6), `device_entries`, `normalize_mac`.
- Produces: `validate_uploaded_config(uploaded, *, default_base_listen_port) -> ConfigValidationResult` whose `normalized_config` is **v6** (`players`), errors with field paths `players[<index in players>].<key>` (backend keys as `players[i].backend.mac`).

Approach (keep the per-device checks, change what they iterate): run `migrate_config_payload(uploaded)` (v5 uploads become v6 here, which is how import keeps accepting v5); then for each index `i` of `normalized["players"]` whose backend is `bluetooth_a2dp`, build the v5-shaped entry with `player_to_device_entry`, run the existing per-device normalisation/validation on it, and write the normalised values back onto the player (`backend.mac`/`backend.adapter` from `mac`/`adapter`, everything else as the same key; `player_name` → `name`). Every issue's field is `players[{i}].{key}` (`mac`/`adapter` → `backend.mac`/`backend.adapter`). Non-Bluetooth players are validated with `Player.model_validate` only. `_validate_effective_listen_ports` takes the player list and the index of each player (all backends take a port: `base + index` over the **players** list — check `device_activation` uses the same index; in stage 1 the index was the position in `BLUETOOTH_DEVICES`, which equals the BT-only position; keep that index for Bluetooth players so ports do not move: compute it as the position among Bluetooth players and say so in a comment).

- [ ] **Step 1: Failing tests** (`test_config_validation_v6.py`):

```python
"""validate_uploaded_config speaks schema v6."""

from sendspin_bridge.config.players import player_id_for_mac
from sendspin_bridge.services.infrastructure.config_validation import validate_uploaded_config

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


def _bt(mac, **extra):
    return {"name": mac, "backend": {"type": "bluetooth_a2dp", "mac": mac}, **extra}


def test_v5_upload_is_returned_as_players():
    result = validate_uploaded_config({"BLUETOOTH_DEVICES": [{"mac": A.lower()}]}, default_base_listen_port=8928)
    assert result.is_valid
    players = result.normalized_config["players"]
    assert players[0]["backend"]["mac"] == A
    assert players[0]["id"] == player_id_for_mac(A)
    assert "BLUETOOTH_DEVICES" not in result.normalized_config


def test_error_paths_point_at_the_player_index():
    # Review focus 5: a pulse_sink player first, the bad Bluetooth one third.
    payload = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [
            {"id": "dac", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.x"}},
            _bt(A),
            _bt(B, listen_port=80),
        ],
    }
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert not result.is_valid
    assert [e.field for e in result.errors] == ["players[2].listen_port"]


def test_duplicate_effective_ports_are_reported_on_players():
    payload = {"CONFIG_SCHEMA_VERSION": 6, "players": [_bt(A, listen_port=9001), _bt(B, listen_port=9001)]}
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert any(e.field == "players[1].listen_port" for e in result.errors)
```

Check `ConfigValidationResult` attribute names (`is_valid`, `errors[].field`) in the module and adjust.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** as described; remove the `to_v6` keyword from `migrate_config_payload` and its wrapper (grep: only `config_validation.py` used it).
- [ ] **Step 4:** Move `tests/unit/config/test_config_validation.py` payloads to v6 (`players` with `_bt(...)`) and expected field paths to `players[i]...`; keep every case. Run that file + the new one + `tests/unit/config`. Expect PASS. `tests/unit/api` will be red until Task 3 — expected.
- [ ] **Step 5: Commit** — `feat(config): validate configs as players (schema v6)` (message notes the moved test payloads).

---

### Task 3: `/config` on v6 (get, validate, save, import, export, device remove/enable)

**Files:**
- Modify: `src/sendspin_bridge/application/config.py` (`get_config` ~229, `_load_existing_config_for_validation` ~328, `_append_ma_duplicate_device_warnings` ~341, `validate_config` ~668, `save_config` ~683-1040, `import_config` ~604, `_to_api_view` / `_from_api_view` ~119-196)
- Modify: `src/sendspin_bridge/application/devices.py` (`set_enabled`, `remove`)
- Test: `tests/unit/api/test_config_v6_adapter.py` → rename to `test_config_v6_api.py` and rewrite; update API tests that send `BLUETOOTH_DEVICES` (`test_config_and_diagnostics.py`, `test_config_preserve_secrets.py`, `test_config_ordering.py`, `test_device_commands.py`, `test_config_roundtrip.py`, …)

**Interfaces:**
- Consumes: Tasks 1–2; stage 1 `device_entries`, `legacy_device_to_player`, `player_to_device_entry`, `player_id_for_mac`, `find_player_by_id`.
- Produces: `PUT /config` takes `{"players": [...], ...}`; `GET /config` returns `players` (each Bluetooth player with read-only `_effective_listen_port` / `_effective_listen_host` when its client runs) and never `PLAYER_STATE`.

Private converters (replace `_to_api_view` / `_from_api_view`):

* `_internal_view(file_or_payload: dict) -> dict` — the v5-shaped dict the save pipeline runs on (`BLUETOOTH_DEVICES` from Bluetooth players, each entry carrying `id`; no `LAST_VOLUMES`).
* `_players_from_internal(internal: dict, payload_players: list[dict], existing_file: dict) -> list[dict]` — final `players` in **payload order**: each Bluetooth payload player is rebuilt from the normalised internal entry with the same MAC (`legacy_device_to_player(entry, existing_id=<id>)`), its id being the payload id if present else the existing id for that MAC else `player_id_for_mac`; each non-Bluetooth payload player is taken from the payload as validated (with `id` assigned `uuid4()` when missing and `backend.match` taken from the existing player of that id, never from the client).
* `PLAYER_STATE` = existing state for kept ids (client never supplies it).

Rules enforced on the payload before the pipeline runs, in this order, each a `UseCaseError(422, code, message)`:
1. `players` missing but `BLUETOOTH_DEVICES` present → `400 invalid_config` "This bridge expects `players` (config schema 6); reload the page" — never treat it as "no speakers" (review focus 3).
2. `player_backend_immutable` — payload player whose `id` exists in the file with a different `backend.type` or Bluetooth `backend.mac`.
3. `duplicate_player` — two payload players with the same Bluetooth MAC, or the same `pulse_sink` sink.
4. `option_not_supported_for_backend` — from `Player.model_validate` errors on `idle_mode`/`keep_alive_method` for `pulse_sink` (map the validator message to this code); other model errors → the 400 field-error answer with `players[i].<loc>` paths.

Strip every key starting with `_` from each payload player (and from the top level, as today) before anything else.

`get_config()`: `config = load_config()`; drop `PLAYER_STATE`; for each player whose Bluetooth client runs, set `_effective_listen_port` / `_effective_listen_host` (instead of writing `listen_port` / `listen_host`). Remove the old enrichment that wrote into the entries.

`validate_config()`: returns the v6 `normalized_config` (no `PLAYER_STATE`).

`import_config()`: validated upload is v6 already (Task 2); write it with `players` as validated and `PLAYER_STATE` from the stored file for kept ids.

`application/devices.py`: `remove()` works on `load_config()["players"]` directly (drop the player with `id == device_id`, else 404) and saves through `save_config` with the v6 document (minus `PLAYER_STATE`); `set_enabled()` disabled path finds the player by id.

- [ ] **Step 1: Failing tests** (`tests/unit/api/test_config_v6_api.py`; reuse the stage-1 adapter test's fixture — a v6 file with `custom-a` (BT, adapter hci0, state volume/sink/released) and `player_id_for_mac(B)`; add a `pulse_sink` player `dac-1`):

```python
def test_get_config_returns_players_without_state(client):
    body = client.get("/api/v1/config").json()
    assert [p["id"] for p in body["players"]] == ["custom-a", player_id_for_mac(B), "dac-1"]
    assert "PLAYER_STATE" not in body and "BLUETOOTH_DEVICES" not in body


def test_untouched_save_keeps_everything_and_changes_nothing(client, tmp_path, monkeypatch):
    # Review focus 1.
    import sendspin_bridge.application.config as config_uc

    seen = []
    real = config_uc.diff_configs
    monkeypatch.setattr(config_uc, "diff_configs", lambda a, b: seen.append(real(a, b)) or seen[-1])
    payload = client.get("/api/v1/config").json()
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B), "dac-1"]
    assert disk["PLAYER_STATE"]["custom-a"] == {"volume": 40, "sink": "s", "released": True}
    assert seen == [[]]


def test_effective_listen_values_are_read_only(client, tmp_path, monkeypatch):
    # The running client's port/host are reported, not written into the speaker.
    ...  # register a fake running client for custom-a (see how test_config_and_diagnostics
    ...  # fakes the registry for listen_host) and assert:
    # body["players"][0]["_effective_listen_port"] == 8928 and "listen_port" not in body["players"][0]
    # and a PUT of that body leaves "listen_port"/"listen_host" out of the file and produces no actions.


def test_new_bluetooth_player_gets_uuid5_and_readding_is_idempotent(client, tmp_path):
    # Review focus 4.
    new = "AA:BB:CC:DD:EE:03"
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "C", "backend": {"type": "bluetooth_a2dp", "mac": new.lower()}})
    assert client.put("/api/v1/config", json=payload).status_code == 200
    ids = [p["id"] for p in _disk(tmp_path)["players"]]
    assert ids[-1] == player_id_for_mac(new)
    payload = client.get("/api/v1/config").json()
    payload["players"].append({"name": "C again", "backend": {"type": "bluetooth_a2dp", "mac": new}})
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "duplicate_player"


def test_backend_of_an_existing_player_is_immutable(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][0]["backend"]["mac"] = "AA:BB:CC:DD:EE:09"
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "player_backend_immutable"


def test_pulse_sink_rejects_bluetooth_only_options(client):
    payload = client.get("/api/v1/config").json()
    payload["players"][2]["idle_mode"] = "auto_disconnect"
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 422 and resp.json()["code"] == "option_not_supported_for_backend"


def test_v5_payload_is_refused_not_treated_as_empty(client, tmp_path):
    # Review focus 3.
    before = _disk(tmp_path)
    resp = client.put("/api/v1/config", json={"BLUETOOTH_DEVICES": [{"mac": B}]})
    assert resp.status_code == 400
    assert _disk(tmp_path) == before


def test_client_cannot_set_player_state_or_match(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    payload["PLAYER_STATE"] = {"custom-a": {"volume": 1}}
    payload["players"][2]["backend"]["match"] = {"serial": "forged"}
    client.put("/api/v1/config", json=payload)
    disk = _disk(tmp_path)
    assert disk["PLAYER_STATE"]["custom-a"]["volume"] == 40
    assert disk["players"][2]["backend"].get("match", {}).get("serial") != "forged"


def test_remove_by_id_and_import_v5(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "_bt_remove_device", lambda *a, **k: None)
    assert client.delete(f"/api/v1/devices/{player_id_for_mac(B)}").status_code == 200
    assert [p["id"] for p in _disk(tmp_path)["players"]] == ["custom-a", "dac-1"]
    v5 = {"CONFIG_SCHEMA_VERSION": 5, "BLUETOOTH_DEVICES": [{"mac": B, "player_name": "Only B"}]}
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", json.dumps(v5).encode(), "application/json")})
    assert resp.status_code == 200, resp.text
    assert [p["name"] for p in _disk(tmp_path)["players"]] == ["Only B"]
```

Fill in `test_effective_listen_values_are_read_only` by copying the registry fake used by the existing `test_api_config_get_enriches_devices_from_registry_snapshot` test (it will be rewritten to the new `_effective_*` keys in Step 4 — keep what it checks: the effective port/host are visible).

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** as described above.
- [ ] **Step 4:** Update API tests that send/read `BLUETOOTH_DEVICES` to `players` (helper: `tests/support/config_v6.py` `v6_players(...)` from stage 1 builds player dicts). Keep every assertion's meaning. The "enrich devices from registry" tests now assert `_effective_listen_port/_host` and that `listen_port/host` are not added.
- [ ] **Step 5:** `uv run pytest -q -p no:cacheprovider` (full, green except `ui`-only checks), mypy, ruff.
- [ ] **Step 6: Commit** — `feat(api): /config speaks players (schema v6); unchanged saves no longer restart speakers`

---

### Task 4: `/devices` — `backend`, `supported_actions`, 409

**Files:**
- Modify: `src/sendspin_bridge/application/models/devices.py` (`Device`, `DisabledDevice`), the status→Device builder (`Device.from_status`), `src/sendspin_bridge/application/devices.py` (guard), `src/sendspin_bridge/api/routers/devices.py` (wire the guard for the 7 commands)
- Possibly: `src/sendspin_bridge/services/lifecycle/status_snapshot.py` / client status (where `player_id`, `bluetooth_mac` come from) to expose the backend type
- Test: `tests/unit/api/test_devices_backend.py` (new)

**Interfaces:**
- Produces: `Device.backend: DeviceBackend {type: str, mac: str | None, adapter: str | None, sink: str | None}`; `Device.supported_actions: list[str]`; `Device.bluetooth: DeviceBluetooth | None`; `DisabledDevice.backend_type: str`; `application.devices.require_action(device_id: str, action: str) -> None` raising `UseCaseError(409, "not_supported_for_backend", …)`.

Guard: look the id up in `load_config()["players"]` (running and disabled players alike); if found and `action not in supported_actions_for(backend.type)` → 409. Unknown ids fall through to the existing 404. Map commands: `/reconnect`→`reconnect`, `/repair`→`repair`, `/standby` and `/wake`→`standby`, `/claim`→`claim`, `/pairing-window`→`pairing_window`, `/power-save`→`power_save`, `/volume` and `/mute`→`volume`, `/playback` and `/transport`→`transport`.

For running devices, `backend` comes from the client: today every client is Bluetooth (`type = bluetooth_a2dp`, `mac = bluetooth_mac`, `adapter = bluetooth_adapter_hci`); build it in `from_status` from the existing status keys, so no runtime change is needed in this stage.

- [ ] **Step 1: Failing tests**

```python
def test_running_bluetooth_device_reports_backend_and_actions(client, fake_running_device):
    d = client.get("/api/v1/devices").json()[0]
    assert d["backend"]["type"] == "bluetooth_a2dp"
    assert d["backend"]["mac"] == fake_running_device.mac
    assert d["supported_actions"] == supported_actions_for("bluetooth_a2dp")


def test_bluetooth_commands_on_a_pulse_sink_player_are_409(client, tmp_path):
    _write_v6(tmp_path, players=[{"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "s"}}])
    for path in ("reconnect", "repair", "standby", "wake", "claim", "pairing-window"):
        resp = client.post(f"/api/v1/devices/dac-1/{path}")
        assert resp.status_code == 409, path
        assert resp.json()["code"] == "not_supported_for_backend"


def test_unknown_device_is_still_404(client):
    assert client.post("/api/v1/devices/nope/reconnect").status_code == 404
```

Build `fake_running_device` from how `tests/unit/api/test_device_commands.py` / `test_status.py` register a fake client; `_write_v6` writes a v6 config like the Task 3 fixture.

- [ ] **Step 2: FAIL. Step 3: implement. Step 4: PASS + full suite, mypy, ruff. Step 5: Commit** — `feat(api): devices report their backend and supported actions; 409 for unsupported commands`

---

### Task 5: OpenAPI and generated client

**Files:** `ui/openapi.json`, `ui/src/api/schema.d.ts`

- [ ] **Step 1:** `uv run python scripts/export_openapi.py` (writes `ui/openapi.json`; check `--help` for the output flag if it does not).
- [ ] **Step 2:** `cd ui && npm run gen:api`.
- [ ] **Step 3:** `uv run python scripts/export_openapi.py --check` and `cd ui && npm run check:api` → clean; `npm run build` will now fail type-checks where the SPA still uses `BLUETOOTH_DEVICES` — expected, fixed in Task 6. Record the error list in the report.
- [ ] **Step 4: Commit** — `chore(ui): regenerate the API client for config schema v6`

---

### Task 6: SPA on `players`

**Files:**
- Modify: `ui/src/stores/bluetooth.ts` (`addToBridge`), `ui/src/components/devices/DeviceSettingsPanel.vue`, `ui/src/settings/device.ts`, `ui/src/settings/layout.ts`, `ui/src/components/bluetooth/AddSpeakerDialog.vue`, `ui/src/i18n/en.json`, `ui/src/i18n/ru.json`, the device action menus (`SpeakerActionsMenu.vue`, `DeviceCard.vue`, `DeviceListRow.vue`, `DeviceDetailDrawer.vue` — wherever reconnect/repair/standby/claim/pairing-window are offered)
- Test: `ui/tests/unit/settings/device.test.ts`, `ui/tests/unit/stores/bluetooth.test.ts`

Changes:

* `addToBridge(mac, name, adapter, extra)`: reads `config.players`; if any player has `backend.type === 'bluetooth_a2dp'` and the same MAC (case-insensitive) → `return false`; else appends `{ name: name || mac, enabled: true, backend: { type: 'bluetooth_a2dp', mac: mac.toUpperCase(), adapter }, room_name? }` (no `id` — the server assigns it) and saves.
* `DeviceSettingsPanel`: find the entry by `p.id === props.device.id`; save replaces that player in `config.players`; strip `_effective_*` keys only on save (keep them for display).
* `settings/device.ts`: field keys `name` (was `player_name`) and `backend.adapter` (was `adapter`); add `backend` and `id` to `DEVICE_MANAGED_KEYS` (`backend.mac` / `type` are not editable); keep the rest. Read/write in the panel must handle the dotted key (`backend.adapter`) — write into `entry.backend.adapter`, never a top-level `"backend.adapter"` key. Show `_effective_listen_port` as the `listen_port` placeholder when set.
* `settings/layout.ts`: `COMPONENT_OWNED_KEYS` `players: 'devices'` (replace `BLUETOOTH_DEVICES`); `MANAGED_KEYS` drop `LAST_SINKS` / `LAST_VOLUMES`.
* i18n: rename `settings.device.player_name` → `settings.device.name`, `settings.device.adapter` → `settings.device.backend.adapter` (en + ru); update `AddSpeakerDialog.vue`'s label key.
* Menus: hide actions not in `device.supported_actions` (use `supported_actions ?? []` defensively; Bluetooth devices list all, so the visible change is nil today).
* `device.test.ts`: read the field schema from `schemas.Player.properties`, resolving dotted keys through `BluetoothBackend` (`backend.adapter` → `schemas.BluetoothBackend.properties.adapter`); the "accounts for every key" test covers `Player` properties.

- [ ] **Step 1: Failing tests** — in `ui/tests/unit/stores/bluetooth.test.ts` add: `addToBridge` appends a player with `backend.type === 'bluetooth_a2dp'`, upper-case MAC, no `id`; a second call with the same MAC (lower-case) returns `false` and does not save (mock `getConfig`/`saveConfig` as the existing store tests do). `device.test.ts` updated as above fails until `device.ts` changes.
- [ ] **Step 2:** `cd ui && npm test` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `npm test`, `npm run build` (vue-tsc + vite), `npm run check:api` → all green.
- [ ] **Step 5: Commit** — `feat(ui): the SPA edits players (config schema v6)`

---

### Task 7: Changelog, full verification, live check

- [ ] **Step 1: CHANGELOG** `[Unreleased]` (existing sections stay; add):
  * `### Changed`: "The bridge's HTTP API (v1) now reads and writes speakers as `players` (each with a stable id and a backend) instead of `BLUETOOTH_DEVICES`; scripts that edit the configuration through the API need the new shape. Configuration files in the old format are still accepted by import."
  * `### Fixed`: "Saving the settings without changing anything no longer restarts the running speakers."
  Run the changelog lint.
- [ ] **Step 2:** all CI gates from Global Constraints.
- [ ] **Step 3: Live check** (CLAUDE.md "Local Demo Workflow"; one instance; a copy of `tests/fixtures/config_v5_production.json` in a fresh dir under `/home/renso/.claude/jobs/3236d1c5/tmp/v6s2-live/`, `WEB_PORT=8098`; a separate instance may already run on 8080 — leave it alone): `GET /api/v1/config` → `players` (6, fixture order), no `PLAYER_STATE`; PUT the same body → `reconfig` empty and file players/state unchanged; `GET /api/v1/devices` → each device has `backend` and `supported_actions`; open the SPA (built `ui/dist`) at `http://localhost:8098`, open one speaker's settings, change its name, save → only that player changes. Stop the instance by its PID and verify.
- [ ] **Step 4: Commit** — `docs(changelog): API v1 speaks config schema v6`
