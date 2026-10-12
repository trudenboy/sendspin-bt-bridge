# Config v6, stage 1 (models and migration) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `config.json` moves to schema v6 — `players[]` with a typed `backend` and a separate `PLAYER_STATE` — while the bridge, API v1 and SPA behave exactly as before.

**Architecture:** A new `sendspin_bridge.config.players` module owns the v6 models and the two conversions: v5 device dict → player (migration input) and player → v5-shaped "device entry" (read view). The migration step turns v5 files into v6 on load and on every `update_config`. Writers move to `players` / `PLAYER_STATE`; readers switch from `config["BLUETOOTH_DEVICES"]` to `device_entries(config)`, so the runtime pipeline (activation, `config_diff`, reconfig) is untouched until stage 3. `GET/PUT /config` keep the v5 shape through an adapter in `application/config.py` until stage 2.

**Tech Stack:** Python 3.13 (CI) / 3.14 (local), Pydantic v2, pytest, uv.

**Spec:** [`docs/plans/2026-10-12-config-v6-players-design.md`](2026-10-12-config-v6-players-design.md) — sections 1, 4 and stage 1 of section 6.

## Deviations from the spec (stage 1 only)

* Most readers do **not** move to Pydantic `get_players()` yet. They read
  `device_entries(config)`, a v5-shaped list of dicts built from `players`
  (Bluetooth players only, plus `id`). This keeps the riskiest stage to a
  file-format change; the runtime pipeline moves to player ids in stage 3,
  where `pulse_sink` needs it. `get_players()` exists and is used by the
  writers and the migration.
* `GET/PUT /config`, `/config/export` and `/config/import` keep the v5 shape
  (`BLUETOOTH_DEVICES`, `LAST_VOLUMES`) through an adapter, so the SPA is not
  touched. Stage 2 removes the adapter.
* Legacy `keepalive_interval` / `keepalive_enabled` stay on the player as extra
  fields: `device_activation` still reads them. They are dropped in stage 3.
* `update_config()` migrates an unmigrated file before running its mutator, so
  every writer sees v6 even if the file was written by `translate_ha_config.py`
  a moment earlier.

## Global Constraints

* `CONFIG_SCHEMA_VERSION = 6`.
* Bluetooth player id = `str(uuid.uuid5(uuid.NAMESPACE_DNS, mac.lower()))`, bit-identical to today's `_player_id_from_mac`.
* `pulse_sink` player id = `str(uuid.uuid4())`.
* `PLAYER_STATE[id]` holds only `volume` (int 0–100), `sink` (str), `released` (bool), `released_by` (str).
* v5 → v6 is one-way; no rollback support, no double writes.
* Before the first v6 write the original file is copied to `config.v5.bak.json` in the config dir (never overwritten once present).
* A config with schema > 6 is not migrated (warning, keys used as-is).
* No private symbols or file paths in `CHANGELOG.md` bullets (`scripts/lint_changelog.py` R7).
* TDD: every behaviour change starts with a failing test. Existing tests that assert the **v5 file layout** are updated to the v6 layout; each such commit says so in its message.
* Run tests with `uv run pytest -q -p no:cacheprovider`; lint with `uv run ruff check src tests && uv run ruff format --check src tests`.

## Review Focus

1. **MAC key case mismatch** — `LAST_VOLUMES` keyed `aa:bb:…` while the device is `AA:BB:…`: the volume must still land in `PLAYER_STATE`. (Task 2)
2. **HA add-on restart after a speaker was removed in the UI** — `options.json` still lists it; it must not come back. (Task 8)
3. **Released speaker survives restart** — `released` + `released_by: "user"` move to `PLAYER_STATE` and the auto-reclaim gate still sees them. (Tasks 2, 5)
4. **SPA save of an unchanged list** — `PUT /config` with v5 entries keeps every player's id and order, so Music Assistant sees no new players and nothing restarts. (Task 7)
5. **MA-pushed static delay after migration** — `save_device_static_delay(mac, …)` updates the player found by `backend.mac`. (Task 4)

---

## File map

| File | Change |
|---|---|
| `src/sendspin_bridge/config/players.py` | **new** — models, ids, conversions, `device_entries`, state helpers |
| `src/sendspin_bridge/config/migration.py` | v6 step, player/state normalisation replaces BT device normalisation |
| `src/sendspin_bridge/config/__init__.py` | defaults, allowed keys, backup, migration-error record, writers, `update_config` |
| `src/sendspin_bridge/bluetooth/audio.py`, `bridge/client.py` | raw-file readers use `load_player_state_for_mac` |
| `src/sendspin_bridge/services/bluetooth/__init__.py` | `persist_device_enabled/released` on players/state, no `options.json` |
| `src/sendspin_bridge/services/bluetooth/bt_commands.py` | writers on players by id |
| readers (10 files, Task 6) | `device_entries(config)` |
| `src/sendspin_bridge/application/config.py`, `application/devices.py` | v5 API adapter |
| `scripts/translate_ha_config.py`, `ha-addon/config.yaml`, `ha-addon/translations/en.yaml` | add-on |
| `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py`, `recovery_assistant.py` | `config_migration_failed` |
| `tests/unit/config/test_players.py`, `test_v6_migration.py`, `tests/fixtures/config_v5_production.json` | **new** tests and fixture |

---

### Task 1: v6 models and conversions

**Files:**
- Create: `src/sendspin_bridge/config/players.py`
- Test: `tests/unit/config/test_players.py`

**Interfaces:**
- Produces:
  - `player_id_for_mac(mac: str) -> str`
  - `class BluetoothBackend`, `class SinkMatch`, `class PulseSinkBackend`, `class Player`, `class PlayerState`
  - `legacy_device_to_player(device: dict, *, existing_id: str | None = None) -> dict` — v5 device dict → v6 player dict (JSON-ready), raises `ValueError` without a valid MAC
  - `player_to_device_entry(player: dict) -> dict | None` — v6 player dict → v5-shaped dict with `id`; `None` for non-Bluetooth
  - `device_entries(config: dict) -> list[dict]` — all Bluetooth players as v5-shaped dicts, in list order, `released`/`released_by` merged in from `PLAYER_STATE`
  - `get_players(config: dict) -> list[Player]` — validated models, invalid entries skipped with a warning
  - `find_player_by_mac(config: dict, mac: str) -> dict | None`, `find_player_by_id(config: dict, player_id: str) -> dict | None` — return the **live dict** inside `config["players"]` (for mutators)
  - `player_state(config: dict, player_id: str) -> dict` — the live `PLAYER_STATE[id]` dict, created if missing
  - `LEGACY_DEVICE_STATE_KEYS = ("released", "released_by")`

- [ ] **Step 1: Write the failing tests**

```python
"""Schema v6 player models and the v5 <-> v6 conversions."""

import uuid

import pytest
from pydantic import ValidationError

from sendspin_bridge.config.players import (
    Player,
    device_entries,
    find_player_by_id,
    find_player_by_mac,
    get_players,
    legacy_device_to_player,
    player_id_for_mac,
    player_state,
    player_to_device_entry,
)

MAC = "FC:58:FA:EB:08:6C"


def test_player_id_matches_legacy_derivation():
    from sendspin_bridge.config import _player_id_from_mac

    assert player_id_for_mac(MAC) == str(uuid.uuid5(uuid.NAMESPACE_DNS, MAC.lower()))
    assert player_id_for_mac(MAC) == _player_id_from_mac(MAC)
    assert player_id_for_mac(" fc:58:fa:eb:08:6c ") == player_id_for_mac(MAC)


def test_legacy_device_to_player_moves_bt_fields_into_backend():
    player = legacy_device_to_player(
        {
            "mac": MAC.lower(),
            "adapter": "hci0",
            "player_name": "ENEBY20",
            "enabled": False,
            "static_delay_ms": 120,
            "idle_mode": "power_save",
            "keepalive_interval": 30,
            "released": True,
            "released_by": "user",
        }
    )
    assert player["id"] == player_id_for_mac(MAC)
    assert player["name"] == "ENEBY20"
    assert player["backend"] == {"type": "bluetooth_a2dp", "mac": MAC, "adapter": "hci0"}
    assert player["enabled"] is False
    assert player["static_delay_ms"] == 120
    assert player["keepalive_interval"] == 30  # legacy extra kept until stage 3
    for moved in ("mac", "adapter", "player_name", "released", "released_by"):
        assert moved not in player


def test_legacy_device_to_player_keeps_existing_id():
    player = legacy_device_to_player({"mac": MAC}, existing_id="custom-id")
    assert player["id"] == "custom-id"


def test_legacy_device_to_player_name_falls_back_to_mac():
    assert legacy_device_to_player({"mac": MAC})["name"] == MAC


@pytest.mark.parametrize("bad", [{}, {"mac": ""}, {"mac": "not-a-mac"}, {"mac": 42}])
def test_legacy_device_to_player_rejects_missing_or_bad_mac(bad):
    with pytest.raises(ValueError):
        legacy_device_to_player(bad)


def test_player_to_device_entry_round_trips_v5_shape():
    device = {"mac": MAC, "adapter": "hci1", "player_name": "Kitchen", "listen_port": 8930}
    entry = player_to_device_entry(legacy_device_to_player(device))
    assert entry == {**device, "id": player_id_for_mac(MAC)}


def test_player_to_device_entry_skips_pulse_sink():
    assert player_to_device_entry({"id": "x", "backend": {"type": "pulse_sink", "sink": "s"}}) is None


def test_device_entries_merges_released_state_in_list_order():
    other = "2C:D2:6B:B8:EC:5B"
    config = {
        "players": [
            legacy_device_to_player({"mac": other, "player_name": "B"}),
            legacy_device_to_player({"mac": MAC, "player_name": "A"}),
            {"id": "sink-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.x"}},
        ],
        "PLAYER_STATE": {player_id_for_mac(MAC): {"released": True, "released_by": "auto", "volume": 40}},
    }
    entries = device_entries(config)
    assert [e["player_name"] for e in entries] == ["B", "A"]
    assert entries[1]["released"] is True
    assert entries[1]["released_by"] == "auto"
    assert "volume" not in entries[1]
    assert "released" not in entries[0]


def test_device_entries_tolerates_garbage():
    assert device_entries({}) == []
    assert device_entries({"players": "nope"}) == []
    assert device_entries({"players": [None, {"backend": None}, {"backend": {"type": "bluetooth_a2dp"}}]}) == []


def test_get_players_validates_and_skips_invalid(caplog):
    config = {
        "players": [
            legacy_device_to_player({"mac": MAC}),
            {"id": "", "backend": {"type": "bluetooth_a2dp", "mac": MAC}},
            {"id": "p", "backend": {"type": "pulse_sink", "sink": "s"}, "idle_mode": "auto_disconnect"},
        ]
    }
    players = get_players(config)
    assert [p.id for p in players] == [player_id_for_mac(MAC)]
    assert "Skipping invalid player" in caplog.text


def test_pulse_sink_rejects_bluetooth_only_options():
    with pytest.raises(ValidationError):
        Player.model_validate(
            {"id": "p", "backend": {"type": "pulse_sink", "sink": "s"}, "keep_alive_method": "infrasound"}
        )


def test_find_helpers_return_live_dicts():
    config = {"players": [legacy_device_to_player({"mac": MAC})]}
    found = find_player_by_mac(config, MAC.lower())
    found["name"] = "changed"
    assert config["players"][0]["name"] == "changed"
    assert find_player_by_id(config, player_id_for_mac(MAC)) is config["players"][0]
    assert find_player_by_mac(config, "00:00:00:00:00:00") is None


def test_player_state_creates_and_returns_live_entry():
    config: dict = {}
    player_state(config, "abc")["volume"] = 30
    assert config == {"PLAYER_STATE": {"abc": {"volume": 30}}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_players.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'sendspin_bridge.config.players'`

- [ ] **Step 3: Write the implementation**

```python
"""Schema v6 players: models and the conversions to and from the v5 device list.

A player is a Music Assistant player the bridge runs. ``backend`` says what it
plays to: a Bluetooth speaker today, a local PulseAudio/PipeWire sink later.
Volatile per-player state (volume, sink, released) lives in ``PLAYER_STATE``,
keyed by player id, so writing it does not look like a settings change.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
BLUETOOTH = "bluetooth_a2dp"
PULSE_SINK = "pulse_sink"
LEGACY_DEVICE_STATE_KEYS = ("released", "released_by")
# v5 device keys that move into ``backend`` or get renamed.
_LEGACY_BACKEND_KEYS = ("mac", "adapter")


def normalize_mac(raw: object) -> str:
    """Upper-case, trimmed MAC, or "" when *raw* is not a MAC."""
    if not isinstance(raw, str):
        return ""
    mac = raw.strip().upper()
    return mac if _MAC_RE.match(mac) else ""


def player_id_for_mac(mac: str) -> str:
    """The player id Music Assistant knows a Bluetooth speaker by."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac.strip().lower()))


class BluetoothBackend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bluetooth_a2dp"] = BLUETOOTH
    mac: str
    adapter: str = ""

    @field_validator("mac", mode="before")
    @classmethod
    def _mac(cls, value: object) -> str:
        mac = normalize_mac(value)
        if not mac:
            raise ValueError(f"invalid MAC address {value!r}")
        return mac


class SinkMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial: str | None = None
    bus_path: str | None = None
    vendor_id: str | None = None
    product_id: str | None = None
    alsa_long_card_name: str | None = None


class PulseSinkBackend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pulse_sink"] = PULSE_SINK
    sink: str = Field(min_length=1)
    match: SinkMatch = Field(default_factory=SinkMatch)


Backend = Annotated[BluetoothBackend | PulseSinkBackend, Field(discriminator="type")]


class Player(BaseModel):
    # Extra keys survive (legacy keep-alive fields, future additions).
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    name: str = ""
    enabled: bool = True
    listen_port: int | None = Field(None, ge=1024, le=65535)
    listen_host: str | None = None
    preferred_format: str | None = None
    static_delay_ms: int | None = Field(None, ge=0, le=5000)
    static_delay_source: str | None = None
    static_delay_calibrated_at: str | None = None
    static_delay_codec: str | None = None
    required_lead_time_ms: int | None = Field(None, ge=0, le=30000)
    min_buffer_ms: int | None = Field(None, ge=0, le=30000)
    volume_controller: Literal["pa", "sendspin"] | None = None
    idle_mode: Literal["default", "power_save", "auto_disconnect", "keep_alive"] | None = None
    power_save_delay_minutes: int | None = None
    idle_disconnect_minutes: int | None = None
    keep_alive_method: Literal["infrasound", "silence", "none"] | None = None
    room_id: str | None = None
    room_name: str | None = None
    backend: Backend

    @model_validator(mode="after")
    def _backend_options(self) -> Player:
        if isinstance(self.backend, PulseSinkBackend):
            if self.idle_mode == "auto_disconnect":
                raise ValueError("idle_mode 'auto_disconnect' is only for bluetooth_a2dp players")
            if self.keep_alive_method == "infrasound":
                raise ValueError("keep_alive_method 'infrasound' is only for bluetooth_a2dp players")
        return self


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    volume: int | None = Field(None, ge=0, le=100)
    sink: str | None = None
    released: bool | None = None
    released_by: str | None = None


def legacy_device_to_player(device: dict[str, Any], *, existing_id: str | None = None) -> dict[str, Any]:
    """Turn one v5 ``BLUETOOTH_DEVICES`` entry into a v6 player dict.

    Keys move as they are; ``player_name`` becomes ``name``, ``mac`` and
    ``adapter`` go into ``backend``, ``released``/``released_by`` are left out
    (they are state, see ``PLAYER_STATE``).
    """
    mac = normalize_mac(device.get("mac"))
    if not mac:
        raise ValueError(f"Bluetooth device without a valid MAC: {device.get('mac')!r}")
    player: dict[str, Any] = {"id": existing_id or player_id_for_mac(mac)}
    player["name"] = str(device.get("player_name") or "").strip() or mac
    for key, value in device.items():
        if key in ("player_name", "id", *_LEGACY_BACKEND_KEYS, *LEGACY_DEVICE_STATE_KEYS):
            continue
        player[key] = value
    player["backend"] = {"type": BLUETOOTH, "mac": mac, "adapter": str(device.get("adapter") or "").strip()}
    return player


def player_to_device_entry(player: dict[str, Any]) -> dict[str, Any] | None:
    """The v5-shaped view of a Bluetooth player (plus ``id``), or None."""
    backend = player.get("backend") if isinstance(player, dict) else None
    if not isinstance(backend, dict) or backend.get("type") != BLUETOOTH:
        return None
    mac = normalize_mac(backend.get("mac"))
    if not mac:
        return None
    entry: dict[str, Any] = {"mac": mac, "adapter": str(backend.get("adapter") or "")}
    entry["player_name"] = player.get("name") or mac
    for key, value in player.items():
        if key in ("name", "backend", "id"):
            continue
        entry[key] = value
    entry["id"] = str(player.get("id") or player_id_for_mac(mac))
    return entry


def device_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Bluetooth player as a v5 device dict, ``released`` merged in from state."""
    players = config.get("players") if isinstance(config, dict) else None
    if not isinstance(players, list):
        return []
    state = config.get("PLAYER_STATE") if isinstance(config.get("PLAYER_STATE"), dict) else {}
    entries: list[dict[str, Any]] = []
    for player in players:
        entry = player_to_device_entry(player) if isinstance(player, dict) else None
        if entry is None:
            continue
        player_state_entry = state.get(entry["id"]) if isinstance(state.get(entry["id"]), dict) else {}
        for key in LEGACY_DEVICE_STATE_KEYS:
            if player_state_entry.get(key) is not None:
                entry[key] = player_state_entry[key]
        entries.append(entry)
    return entries


def get_players(config: dict[str, Any]) -> list[Player]:
    """Validated players; an invalid entry is skipped with a warning."""
    raw = config.get("players") if isinstance(config, dict) else None
    if not isinstance(raw, list):
        return []
    players: list[Player] = []
    for entry in raw:
        try:
            players.append(Player.model_validate(entry))
        except ValidationError as exc:
            logger.warning("Skipping invalid player %r: %s", entry, exc.errors(include_url=False))
    return players


def find_player_by_mac(config: dict[str, Any], mac: str) -> dict[str, Any] | None:
    wanted = normalize_mac(mac)
    if not wanted:
        return None
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if isinstance(backend, dict) and backend.get("type") == BLUETOOTH and normalize_mac(backend.get("mac")) == wanted:
            return player
    return None


def find_player_by_id(config: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    for player in config.get("players") or []:
        if isinstance(player, dict) and player.get("id") == player_id:
            return player
    return None


def player_state(config: dict[str, Any], player_id: str) -> dict[str, Any]:
    """The live ``PLAYER_STATE[player_id]`` dict, created when missing."""
    state = config.get("PLAYER_STATE")
    if not isinstance(state, dict):
        state = config["PLAYER_STATE"] = {}
    entry = state.get(player_id)
    if not isinstance(entry, dict):
        entry = state[player_id] = {}
    return entry
```

Also make the legacy helper delegate, in `src/sendspin_bridge/config/__init__.py`:

```python
def _player_id_from_mac(mac: str) -> str:
    """Stable, globally-unique player ID derived from BT MAC address."""
    from sendspin_bridge.config.players import player_id_for_mac

    return player_id_for_mac(mac)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_players.py`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sendspin_bridge/config/players.py src/sendspin_bridge/config/__init__.py tests/unit/config/test_players.py
git commit -m "feat(config): schema v6 player models and v5 conversions"
```

---

### Task 2: Migration step v5 → v6

**Files:**
- Modify: `src/sendspin_bridge/config/migration.py` (`CONFIG_SCHEMA_VERSION`, `migrate_config_payload`, `_normalize_loaded_config`, `_prune_last_volumes`, `_prune_last_sinks`)
- Modify: `src/sendspin_bridge/config/__init__.py:95-206` (`DEFAULT_CONFIG`, `CONFIG_ALLOWED_KEYS`, `RUNTIME_STATE_CONFIG_KEYS`, `migrate_config_payload`)
- Create: `tests/fixtures/config_v5_production.json`
- Test: `tests/unit/config/test_v6_migration.py`

**Interfaces:**
- Consumes: Task 1 (`legacy_device_to_player`, `player_id_for_mac`, `normalize_mac`, `Player`, `PlayerState`)
- Produces:
  - `migration.CONFIG_SCHEMA_VERSION == 6`
  - `migration.LEGACY_V5_KEYS = frozenset({"BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS"})`
  - `migration.migrate_v5_to_v6(config: dict) -> list[ConfigMigrationIssue]` — mutates *config* in place; no-op when it has no legacy keys
  - `migrate_config_payload(...)` result never contains `LEGACY_V5_KEYS`

`migrate_v5_to_v6` runs whenever a legacy key is present, whatever the version
says: `translate_ha_config.py` writes the current version number next to the
add-on's `BLUETOOTH_DEVICES`, and a hand-edited v6 file may carry both.

- [ ] **Step 1: Create the production-shaped fixture**

`tests/fixtures/config_v5_production.json` (the HAOS production speakers from `CLAUDE.md`, MACs as listed there, no secrets):

```json
{
  "CONFIG_SCHEMA_VERSION": 5,
  "BRIDGE_NAME": "HAOS",
  "BLUETOOTH_DEVICES": [
    {"mac": "FC:58:FA:EB:08:6C", "adapter": "hci0", "player_name": "ENEBY20", "listen_port": 8928, "static_delay_ms": 0, "static_delay_source": "manual", "enabled": true, "idle_mode": "power_save", "power_save_delay_minutes": 1},
    {"mac": "2C:D2:6B:B8:EC:5B", "adapter": "hci1", "player_name": "Yandex mini 2 007 a", "listen_port": 8929, "enabled": true},
    {"mac": "80:99:E7:C2:0B:D3", "adapter": "hci0", "player_name": "WH-1000XM4", "listen_port": 8931, "enabled": false},
    {"mac": "30:21:0E:0A:AE:5A", "adapter": "hci1", "player_name": "Lenco LS-500", "listen_port": 8932, "enabled": true, "released": true, "released_by": "user"},
    {"mac": "20:74:CF:61:FB:D8", "adapter": "hci0", "player_name": "OpenMove AfterShokz", "listen_port": 8933, "enabled": false},
    {"mac": "6C:5C:3D:35:17:99", "adapter": "hci1", "player_name": "ENEBY Portable", "listen_port": 8933, "enabled": false, "keepalive_interval": 30}
  ],
  "LAST_VOLUMES": {"FC:58:FA:EB:08:6C": 45, "2c:d2:6b:b8:ec:5b": 30, "00:00:00:00:00:01": 99},
  "LAST_SINKS": {"FC:58:FA:EB:08:6C": "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"}
}
```

- [ ] **Step 2: Write the failing tests**

```python
"""Config schema v5 -> v6 migration."""

import copy
import json
from pathlib import Path

from sendspin_bridge.config.players import player_id_for_mac

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "config_v5_production.json"
ENEBY = "FC:58:FA:EB:08:6C"
YANDEX = "2C:D2:6B:B8:EC:5B"
LENCO = "30:21:0E:0A:AE:5A"


def _migrate(raw: dict):
    from sendspin_bridge.config import migrate_config_payload

    return migrate_config_payload(copy.deepcopy(raw))


def _production() -> dict:
    return json.loads(FIXTURE.read_text())


def test_schema_version_is_6():
    from sendspin_bridge.config.migration import CONFIG_SCHEMA_VERSION

    assert CONFIG_SCHEMA_VERSION == 6


def test_production_config_migrates_to_players():
    result = _migrate(_production())
    cfg = result.normalized_config
    assert result.needs_persist
    assert cfg["CONFIG_SCHEMA_VERSION"] == 6
    for legacy in ("BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS"):
        assert legacy not in cfg
    players = cfg["players"]
    assert [p["name"] for p in players] == [
        "ENEBY20", "Yandex mini 2 007 a", "WH-1000XM4", "Lenco LS-500", "OpenMove AfterShokz", "ENEBY Portable",
    ]
    assert players[0]["id"] == player_id_for_mac(ENEBY)
    assert players[0]["backend"] == {"type": "bluetooth_a2dp", "mac": ENEBY, "adapter": "hci0"}
    assert players[0]["listen_port"] == 8928
    assert players[2]["enabled"] is False
    assert players[5]["keepalive_interval"] == 30


def test_runtime_state_moves_to_player_state():
    state = _migrate(_production()).normalized_config["PLAYER_STATE"]
    assert state[player_id_for_mac(ENEBY)] == {"volume": 45, "sink": "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"}
    # Review focus 1: a lower-case LAST_VOLUMES key still finds its speaker.
    assert state[player_id_for_mac(YANDEX)] == {"volume": 30}
    # Review focus 3: released + who released it survive.
    assert state[player_id_for_mac(LENCO)] == {"released": True, "released_by": "user"}
    # A volume for a speaker that is not configured is dropped.
    assert player_id_for_mac("00:00:00:00:00:01") not in state


def test_migration_is_idempotent():
    once = _migrate(_production()).normalized_config
    twice = _migrate(once)
    assert twice.normalized_config == once
    assert not twice.needs_persist


def test_malformed_entries_are_skipped_with_warnings():
    raw = {
        "CONFIG_SCHEMA_VERSION": 5,
        "BLUETOOTH_DEVICES": [None, {"player_name": "no mac"}, {"mac": "nope"}, {"mac": ENEBY}, {"mac": ENEBY.lower()}],
    }
    result = _migrate(raw)
    assert [p["backend"]["mac"] for p in result.normalized_config["players"]] == [ENEBY]
    messages = " ".join(issue.message for issue in result.warnings)
    assert "without a valid MAC" in messages
    assert "duplicate" in messages


def test_players_win_over_leftover_bluetooth_devices():
    raw = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [{"id": "keep", "name": "Kept", "backend": {"type": "bluetooth_a2dp", "mac": ENEBY}}],
        "BLUETOOTH_DEVICES": [{"mac": YANDEX}],
    }
    result = _migrate(raw)
    assert [p["id"] for p in result.normalized_config["players"]] == ["keep"]
    assert "BLUETOOTH_DEVICES" not in result.normalized_config
    assert result.needs_persist
    assert any("ignored" in issue.message for issue in result.warnings)


def test_newer_schema_is_left_alone():
    raw = {"CONFIG_SCHEMA_VERSION": 7, "players": [], "BLUETOOTH_DEVICES": [{"mac": ENEBY}]}
    result = _migrate(raw)
    assert result.normalized_config["CONFIG_SCHEMA_VERSION"] == 7
    assert "players" in result.normalized_config
    assert result.normalized_config["players"] == []


def test_legacy_v5_device_fixes_still_apply_before_conversion():
    raw = {"CONFIG_SCHEMA_VERSION": 4, "BLUETOOTH_DEVICES": [{"mac": ENEBY, "static_delay_ms": -300, "handoff_mode": "x"}]}
    player = _migrate(raw).normalized_config["players"][0]
    assert player["static_delay_ms"] == 0
    assert "handoff_mode" not in player


def test_load_config_normalizes_players_and_prunes_state(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    pid = player_id_for_mac(ENEBY)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": pid, "name": "A", "backend": {"type": "bluetooth_a2dp", "mac": ENEBY}},
                    {"id": "bad", "backend": {"type": "warp_drive"}},
                ],
                "PLAYER_STATE": {pid: {"volume": 150, "sink": "s"}, "ghost": {"volume": 10}},
            }
        )
    )
    loaded = config.load_config()
    assert [p["id"] for p in loaded["players"]] == [pid]
    assert loaded["PLAYER_STATE"] == {pid: {"sink": "s"}}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_v6_migration.py`
Expected: FAIL — `assert 5 == 6` and `KeyError: 'players'`

- [ ] **Step 4: Implement**

In `src/sendspin_bridge/config/migration.py`:

```python
CONFIG_SCHEMA_VERSION = 6
LEGACY_V5_KEYS = frozenset({"BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS"})
```

Add after `_migrate_power_save_delay`:

```python
def migrate_v5_to_v6(config: dict) -> list:
    """Move ``BLUETOOTH_DEVICES``/``LAST_VOLUMES``/``LAST_SINKS`` into ``players`` + ``PLAYER_STATE``.

    Mutates *config*. Runs whenever a legacy key is present: the add-on's
    translator writes the current schema number next to its device list.
    """
    from sendspin_bridge.config import ConfigMigrationIssue
    from sendspin_bridge.config.players import (
        LEGACY_DEVICE_STATE_KEYS,
        legacy_device_to_player,
        normalize_mac,
        player_id_for_mac,
    )

    issues: list = []
    if not any(key in config for key in LEGACY_V5_KEYS):
        return issues

    legacy_devices = config.pop("BLUETOOTH_DEVICES", None)
    last_volumes = config.pop("LAST_VOLUMES", None)
    last_sinks = config.pop("LAST_SINKS", None)

    if isinstance(config.get("players"), list):
        if legacy_devices:
            issues.append(
                ConfigMigrationIssue(
                    field="BLUETOOTH_DEVICES",
                    message="BLUETOOTH_DEVICES ignored: the config already has players",
                )
            )
        return issues

    devices = _normalize_bluetooth_devices({"BLUETOOTH_DEVICES": legacy_devices}, defaults={"BLUETOOTH_DEVICES": []})
    players: list[dict] = []
    state: dict[str, dict] = {}
    seen: set[str] = set()
    for device in devices:
        try:
            player = legacy_device_to_player(device)
        except ValueError as exc:
            issues.append(ConfigMigrationIssue(field="BLUETOOTH_DEVICES", message=f"Skipped device: {exc}"))
            continue
        mac = player["backend"]["mac"]
        if mac in seen:
            issues.append(ConfigMigrationIssue(field="BLUETOOTH_DEVICES", message=f"Skipped duplicate device {mac}"))
            continue
        seen.add(mac)
        players.append(player)
        entry = {key: device[key] for key in LEGACY_DEVICE_STATE_KEYS if device.get(key) is not None}
        if entry:
            state[player["id"]] = entry

    by_mac = {player_id_for_mac(mac): mac for mac in seen}
    for store, field in ((last_volumes, "volume"), (last_sinks, "sink")):
        if not isinstance(store, dict):
            continue
        for raw_mac, value in store.items():
            mac = normalize_mac(raw_mac)
            pid = player_id_for_mac(mac) if mac else ""
            if pid not in by_mac:
                continue
            if field == "volume" and not (isinstance(value, int) and 0 <= value <= 100):
                continue
            if field == "sink" and not (isinstance(value, str) and value.strip()):
                continue
            state.setdefault(pid, {})[field] = value.strip() if field == "sink" else value

    config["players"] = players
    config["PLAYER_STATE"] = state
    issues.append(
        ConfigMigrationIssue(
            field="players",
            message=f"Migrated {len(players)} Bluetooth device(s) to players (config schema v6)",
        )
    )
    return issues
```

In `migrate_config_payload`, the input filter must keep the legacy keys for
this step: change the first line to

```python
    normalized = _filter_allowed_config_keys(config, allowed_keys=allowed_keys | LEGACY_V5_KEYS)
```

and, just before the `SENDSPIN_SERVER` check at the end, add

```python
    if normalized.get("CONFIG_SCHEMA_VERSION", CONFIG_SCHEMA_VERSION) <= CONFIG_SCHEMA_VERSION:
        v6_issues = migrate_v5_to_v6(normalized)
        if v6_issues:
            warnings.extend(v6_issues)
            needs_persist = True
    else:
        for key in LEGACY_V5_KEYS:
            normalized.pop(key, None)
```

(The legacy `BLUETOOTH_MAC` and `LAST_VOLUME` blocks above it write
`BLUETOOTH_DEVICES` / `LAST_VOLUMES`, which this step then converts.)

Replace the device-list normalisation in `_normalize_loaded_config`:

```python
def _normalize_players(config: dict) -> None:
    from sendspin_bridge.config.players import PlayerState, get_players

    config["players"] = [
        player.model_dump(mode="json", exclude_unset=True) for player in get_players(config)
    ]
    known = {player["id"] for player in config["players"]}
    raw_state = config.get("PLAYER_STATE")
    pruned: dict[str, dict] = {}
    if isinstance(raw_state, dict):
        for pid, entry in raw_state.items():
            if pid not in known or not isinstance(entry, dict):
                continue
            clean = {
                key: value
                for key, value in entry.items()
                if key in PlayerState.model_fields and value is not None
            }
            if "volume" in clean and not (isinstance(clean["volume"], int) and 0 <= clean["volume"] <= 100):
                logger.warning("Ignoring invalid saved volume %r for player %s", clean.pop("volume"), pid)
            if clean:
                pruned[pid] = clean
    config["PLAYER_STATE"] = pruned
```

and in `_normalize_loaded_config` replace the three lines
`config["BLUETOOTH_DEVICES"] = …`, `_prune_last_volumes(…)`, `_prune_last_sinks(…)`
with `_normalize_players(config)`. Delete `_prune_last_volumes` and
`_prune_last_sinks` (their only callers were these lines; `tests/unit/config/test_prune_last_volumes.py`
is deleted with them — its cases are covered by `test_runtime_state_moves_to_player_state`
and `test_load_config_normalizes_players_and_prunes_state`; say so in the commit message).
Keep `_normalize_bluetooth_devices`: the v6 step uses it on the legacy list.

Note: `model_dump(exclude_unset=True)` keeps exactly the keys the file had —
recursively, so a `backend` without `adapter` stays without it — and the MAC
comes back normalised by the validator. A normalised player therefore does not
grow default keys, and the idempotency test holds.

In `src/sendspin_bridge/config/__init__.py`:

* `DEFAULT_CONFIG`: remove `"BLUETOOTH_DEVICES": []`, `"LAST_VOLUMES": {}`, `"LAST_SINKS": {}`; add `"players": []` and `"PLAYER_STATE": {}`.
* `RUNTIME_STATE_CONFIG_KEYS = frozenset(("PLAYER_STATE",))`.

- [ ] **Step 5: Run the new tests, then the config suite**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_v6_migration.py tests/unit/config/test_players.py`
Expected: PASS

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config`
Expected: failures only in tests that assert the v5 **file layout** or read
`load_config()["BLUETOOTH_DEVICES"]` / `["LAST_VOLUMES"]` (`test_config.py`,
`test_static_delay_migration.py`, `test_idle_mode_migration.py`). Rewrite those
assertions to read `device_entries(load_config())` (same dict shape) or
`load_config()["PLAYER_STATE"][player_id_for_mac(mac)]`. Do not change what
they check.

- [ ] **Step 6: Commit**

```bash
git add -A src/sendspin_bridge/config tests/unit/config tests/fixtures/config_v5_production.json
git commit -m "feat(config): migrate config schema v5 to v6 (players, PLAYER_STATE)

Tests that asserted the v5 file layout now read the same values through
device_entries() or PLAYER_STATE; test_prune_last_volumes.py is replaced by
the PLAYER_STATE pruning tests in test_v6_migration.py."
```

---

### Task 3: Persisting the migration — backup, failure, `update_config`

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`load_config`, `update_config`, new `get_config_migration_error`)
- Test: `tests/unit/config/test_v6_migration.py` (append)

**Interfaces:**
- Consumes: Task 2 `migrate_v5_to_v6`, `LEGACY_V5_KEYS`
- Produces:
  - `V5_BACKUP_NAME = "config.v5.bak.json"`
  - `get_config_migration_error() -> str | None` — set when the last `load_config()` could not migrate, cleared by a successful load
  - `update_config(mutator)` hands mutators a v6 dict

- [ ] **Step 1: Write the failing tests**

```python
def _isolate(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return config


def test_load_config_persists_v6_and_keeps_a_v5_backup(tmp_path, monkeypatch):
    config = _isolate(tmp_path, monkeypatch)
    original = FIXTURE.read_text()
    (tmp_path / "config.json").write_text(original)

    config.load_config()

    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["CONFIG_SCHEMA_VERSION"] == 6
    assert "BLUETOOTH_DEVICES" not in on_disk and "LAST_VOLUMES" not in on_disk
    assert len(on_disk["players"]) == 6
    assert (tmp_path / "config.v5.bak.json").read_text() == original
    assert config.get_config_migration_error() is None


def test_backup_is_never_overwritten(tmp_path, monkeypatch):
    config = _isolate(tmp_path, monkeypatch)
    (tmp_path / "config.v5.bak.json").write_text("first")
    (tmp_path / "config.json").write_text(FIXTURE.read_text())
    config.load_config()
    assert (tmp_path / "config.v5.bak.json").read_text() == "first"


def test_failed_migration_leaves_the_file_alone(tmp_path, monkeypatch):
    config = _isolate(tmp_path, monkeypatch)
    import sendspin_bridge.config.migration as migration

    def _boom(_cfg):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(migration, "migrate_v5_to_v6", _boom)
    original = FIXTURE.read_text()
    (tmp_path / "config.json").write_text(original)

    loaded = config.load_config()

    assert (tmp_path / "config.json").read_text() == original
    assert loaded["players"] == []
    assert "synthetic failure" in (config.get_config_migration_error() or "")


def test_update_config_mutators_see_v6(tmp_path, monkeypatch):
    config = _isolate(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": ENEBY}]}))
    seen = {}

    config.update_config(lambda cfg: seen.update(players=list(cfg.get("players", [])), legacy="BLUETOOTH_DEVICES" in cfg))

    assert seen["legacy"] is False
    assert seen["players"][0]["backend"]["mac"] == ENEBY
    assert "players" in json.loads((tmp_path / "config.json").read_text())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_v6_migration.py -k "backup or failed or update_config"`
Expected: FAIL — no backup file, `AttributeError: get_config_migration_error`, mutator sees `BLUETOOTH_DEVICES`

- [ ] **Step 3: Implement**

In `src/sendspin_bridge/config/__init__.py`, next to `CONFIG_FILE`:

```python
V5_BACKUP_NAME = "config.v5.bak.json"
_config_migration_error: str | None = None


def get_config_migration_error() -> str | None:
    """Why the last load could not move the config to the current schema, if it could not."""
    return _config_migration_error


def _backup_v5_config(raw: bytes) -> None:
    backup = CONFIG_DIR / V5_BACKUP_NAME
    if backup.exists():
        return
    try:
        backup.write_bytes(raw)
        logger.info("Saved the pre-v6 config as %s", backup)
    except OSError as exc:
        logger.warning("Could not save %s: %s", backup, exc)
```

In `load_config()`:

* declare `global _config_migration_error` at the top;
* wrap the `migrate_config_payload(saved_config)` call:

```python
            try:
                migrated = migrate_config_payload(saved_config)
            except Exception as exc:  # a migration bug must not cost the operator their settings
                _config_migration_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Config migration failed; leaving %s untouched", CONFIG_FILE)
                migrated = ConfigMigrationResult(
                    normalized_config={
                        k: v for k, v in saved_config.items() if k in CONFIG_ALLOWED_KEYS and k not in ("players", "PLAYER_STATE")
                    },
                    needs_persist=False,
                )
            else:
                _config_migration_error = None
```

* in the `if migrated.needs_persist:` branch, before `update_config(_persist_migration)`:

```python
                    from sendspin_bridge.config.migration import LEGACY_V5_KEYS

                    if any(key in saved_config for key in LEGACY_V5_KEYS):
                        _backup_v5_config(raw)
```

In `update_config`, after `before = copy.deepcopy(existing)`:

```python
        from sendspin_bridge.config.migration import migrate_v5_to_v6

        migrate_v5_to_v6(existing)
```

(`before` is taken first, so the conversion counts as a change and is written.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sendspin_bridge/config/__init__.py tests/unit/config/test_v6_migration.py
git commit -m "feat(config): back up the v5 file, keep it on a failed migration, migrate before writes"
```

---

### Task 4: Writers and raw readers of per-player values

**Files:**
- Modify: `src/sendspin_bridge/config/__init__.py` (`save_device_volume`, `save_device_sink`, `save_device_static_delay`, `save_device_buffer_setting`, new `load_player_state_for_mac`)
- Modify: `src/sendspin_bridge/bluetooth/audio.py:203-211, 322-335`
- Modify: `src/sendspin_bridge/bridge/client.py:585-595` (`_load_saved_device_volume`)
- Test: `tests/unit/config/test_player_state_writers.py` (new), `tests/unit/config/test_save_device_static_delay.py` (layout assertions)

**Interfaces:**
- Consumes: Task 1 `find_player_by_mac`, `player_state`, `player_id_for_mac`; Task 3 `update_config` gives v6
- Produces: `load_player_state_for_mac(mac: str) -> dict` (reads the file, migrates in memory, returns `PLAYER_STATE` entry copy or `{}`); public signatures of the four `save_device_*` functions unchanged

- [ ] **Step 1: Write the failing tests**

`tests/unit/config/test_player_state_writers.py`:

```python
"""Per-player values persist into players / PLAYER_STATE (schema v6)."""

import json

import pytest

from sendspin_bridge.config.players import player_id_for_mac

MAC = "AA:BB:CC:DD:EE:FF"
PID = player_id_for_mac(MAC)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [{"id": PID, "name": "S", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}]})
    )


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_volume_goes_to_player_state(tmp_path):
    from sendspin_bridge.config import save_device_volume

    save_device_volume(MAC.lower(), 37)
    assert _disk(tmp_path)["PLAYER_STATE"][PID]["volume"] == 37
    assert "LAST_VOLUMES" not in _disk(tmp_path)


def test_volume_for_unknown_speaker_is_not_saved(tmp_path):
    from sendspin_bridge.config import save_device_volume

    save_device_volume("11:22:33:44:55:66", 50)
    assert _disk(tmp_path).get("PLAYER_STATE", {}) == {}


def test_sink_goes_to_player_state(tmp_path):
    from sendspin_bridge.config import save_device_sink

    save_device_sink(MAC, " bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink ")
    assert _disk(tmp_path)["PLAYER_STATE"][PID]["sink"] == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"


def test_static_delay_goes_to_the_player(tmp_path):
    # Review focus 5: an MA-pushed delay after migration lands on the player.
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay(MAC, 640, source="ma", codec="sbc")
    player = _disk(tmp_path)["players"][0]
    assert player["static_delay_ms"] == 640
    assert player["static_delay_source"] == "ma"
    assert player["static_delay_codec"] == "sbc"


def test_buffer_setting_goes_to_the_player(tmp_path):
    from sendspin_bridge.config import save_device_buffer_setting

    save_device_buffer_setting(MAC, "min_buffer_ms", 400)
    assert _disk(tmp_path)["players"][0]["min_buffer_ms"] == 400


def test_load_player_state_for_mac_reads_v5_and_v6(tmp_path):
    from sendspin_bridge.config import load_player_state_for_mac, save_device_volume

    save_device_volume(MAC, 20)
    assert load_player_state_for_mac(MAC) == {"volume": 20}
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": MAC}], "LAST_VOLUMES": {MAC: 70}}))
    assert load_player_state_for_mac(MAC) == {"volume": 70}
    assert load_player_state_for_mac("11:22:33:44:55:66") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_player_state_writers.py`
Expected: FAIL — volume written to `LAST_VOLUMES`, `ImportError: load_player_state_for_mac`

- [ ] **Step 3: Implement**

Replace the bodies in `src/sendspin_bridge/config/__init__.py` (signatures and
error handling unchanged):

```python
def save_device_volume(mac: str | None, volume: int) -> None:
    """Persist a speaker's volume under PLAYER_STATE[<player id>].volume."""
    if not mac or not CONFIG_FILE.exists():
        return

    def _set_vol(cfg: dict) -> None:
        from sendspin_bridge.config.players import find_player_by_mac, player_state

        player = find_player_by_mac(cfg, mac)
        if player is None:
            logger.warning("Skipping saved volume for unknown Bluetooth device %s", mac.strip().upper())
            return
        player_state(cfg, player["id"])["volume"] = volume

    try:
        update_config(_set_vol)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not save volume for %s: %s", mac, e)


def save_device_sink(mac: str | None, sink_name: str) -> None:
    """Persist a speaker's PA sink name under PLAYER_STATE[<player id>].sink."""
    if not mac or not CONFIG_FILE.exists():
        return

    def _set_sink(cfg: dict) -> None:
        from sendspin_bridge.config.players import find_player_by_mac, player_state

        player = find_player_by_mac(cfg, mac)
        if player is not None:
            player_state(cfg, player["id"])["sink"] = sink_name.strip()

    try:
        update_config(_set_sink)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not save sink for %s: %s", mac, e)
```

In `save_device_static_delay` replace `_set_delay`:

```python
    def _set_delay(cfg: dict) -> None:
        from sendspin_bridge.config.players import find_player_by_mac

        player = find_player_by_mac(cfg, mac)
        if player is None:
            logger.warning("Skipping static_delay_ms save for unknown Bluetooth device %s", mac.strip().upper())
            return
        player["static_delay_ms"] = clamped
        if source:
            player["static_delay_source"] = source
            player["static_delay_calibrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if codec:
            player["static_delay_codec"] = codec
```

In `save_device_buffer_setting` replace `_set_value`:

```python
    def _set_value(cfg: dict) -> None:
        from sendspin_bridge.config.players import find_player_by_mac

        player = find_player_by_mac(cfg, mac)
        if player is not None:
            player[field] = clamped
```

Add:

```python
def load_player_state_for_mac(mac: str) -> dict:
    """The saved PLAYER_STATE entry of the speaker with *mac*, read from disk."""
    from sendspin_bridge.config.migration import migrate_v5_to_v6
    from sendspin_bridge.config.players import find_player_by_mac

    try:
        with config_lock, open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(cfg, dict):
        return {}
    migrate_v5_to_v6(cfg)
    player = find_player_by_mac(cfg, mac)
    state = (cfg.get("PLAYER_STATE") or {}).get(player["id"]) if player else None
    return dict(state) if isinstance(state, dict) else {}
```

`src/sendspin_bridge/bluetooth/audio.py` — replace the cached-sink block:

```python
        # Try cached sink name first — avoids 3s A2DP delay on service restart
        from sendspin_bridge.config import load_player_state_for_mac

        cached_sink = load_player_state_for_mac(mac_address).get("sink")
```

and the restore-volume read:

```python
            restored_volume = None
            try:
                last_volume = load_player_state_for_mac(mac_address).get("volume")
                if last_volume is not None and isinstance(last_volume, int) and 0 <= last_volume <= 100:
                    if set_sink_volume(configured_sink, last_volume):
                        logger.info("✓ Restored volume to %s%% for %s", last_volume, mac_address)
                        restored_volume = last_volume
            except (OSError, ValueError) as e:
                logger.warning("Could not restore volume: %s", e)
```

Remove now-unused imports (`json`, `CONFIG_FILE`, `config_lock`) from
`audio.py` only if ruff reports them unused.

`src/sendspin_bridge/bridge/client.py`:

```python
def _load_saved_device_volume(mac: str) -> int | None:
    """Read the saved volume so the UI has one before BT reconnects."""
    from sendspin_bridge.config import load_player_state_for_mac

    saved_volume = load_player_state_for_mac(mac).get("volume")
    if isinstance(saved_volume, int) and 0 <= saved_volume <= 100:
        return saved_volume
    return None
```

In `tests/unit/config/test_save_device_static_delay.py`, `_read_devices` reads
`device_entries(json.loads(...))` instead of `["BLUETOOTH_DEVICES"]`
(the cases stay as they are; v5 fixtures in that file are migrated by
`update_config`).

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config tests/unit/bluetooth tests/unit/bridge`
Expected: PASS. If a `tests/unit/bluetooth` test patches `CONFIG_FILE` with a v5
`LAST_SINKS` file, it still passes: `load_player_state_for_mac` migrates in memory.

- [ ] **Step 5: Commit**

```bash
git add -A src/sendspin_bridge/config src/sendspin_bridge/bluetooth/audio.py src/sendspin_bridge/bridge/client.py tests/unit/config
git commit -m "feat(config): per-speaker volume, sink, delay and buffer persist into players / PLAYER_STATE

test_save_device_static_delay reads the device back through device_entries()
because the file no longer has BLUETOOTH_DEVICES."
```

---

### Task 5: Enabled/released flags and HA-entity writers

**Files:**
- Modify: `src/sendspin_bridge/services/bluetooth/__init__.py:393-460`
- Modify: `src/sendspin_bridge/services/bluetooth/bt_commands.py:364-485`
- Test: `tests/unit/services/bluetooth/test_persist_v6.py` (new); update layout assertions in `tests/unit/services/bluetooth/test_bluetooth_svc.py`, `test_persist_path_binding.py`

**Interfaces:**
- Consumes: Task 1 helpers; Task 3 `update_config`
- Produces: unchanged public signatures `persist_device_enabled(player_name, enabled)`, `persist_device_released(player_name, released, *, released_by=None)`, `apply_device_config_change(player_id, key, value)`, `apply_device_enabled(player_id, enabled)`; none of them touch `options.json` any more

- [ ] **Step 1: Write the failing tests**

```python
"""Enabled / released flags persist into players and PLAYER_STATE (schema v6)."""

import json

import pytest

from sendspin_bridge.config.players import player_id_for_mac

MAC = "AA:BB:CC:DD:EE:FF"
PID = player_id_for_mac(MAC)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [{"id": PID, "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}]})
    )
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"bluetooth_devices": [{"mac": MAC, "player_name": "Kitchen"}]}))
    import sendspin_bridge.services.bluetooth as bt_svc

    monkeypatch.setattr(bt_svc, "_OPTIONS_FILE", options)
    return tmp_path


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_enabled_flag_on_player_and_not_in_options(tmp_path):
    from sendspin_bridge.services.bluetooth import persist_device_enabled

    before = (tmp_path / "options.json").read_text()
    persist_device_enabled("Kitchen", False)
    assert _disk(tmp_path)["players"][0]["enabled"] is False
    assert (tmp_path / "options.json").read_text() == before


def test_released_flag_in_player_state(tmp_path):
    from sendspin_bridge.services.bluetooth import persist_device_released

    persist_device_released("Kitchen", True, released_by="user")
    assert _disk(tmp_path)["PLAYER_STATE"][PID] == {"released": True, "released_by": "user"}
    persist_device_released("Kitchen", False)
    assert _disk(tmp_path)["PLAYER_STATE"][PID] == {"released": False}


def test_apply_device_enabled_finds_disabled_player_by_id(tmp_path, monkeypatch):
    import sendspin_bridge.services.bluetooth.bt_commands as bt_commands

    monkeypatch.setattr(bt_commands, "find_client_by_player_id", lambda _pid: None)
    monkeypatch.setattr(bt_commands, "diff_configs", lambda old, new: [])
    result = bt_commands.apply_device_enabled(PID, False)
    assert result.success
    assert _disk(tmp_path)["players"][0]["enabled"] is False
```

`_sync_addon_options` writes `ConfigStore(_OPTIONS_FILE)`; the fixture points
`_OPTIONS_FILE` at the temp file. If `_sync_addon_options` skips the write
outside the add-on (check its guard), also patch that guard so the "not
touched" assertion would fail on the old code (Step 2 confirms it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/services/bluetooth/test_persist_v6.py`
Expected: FAIL — flags not found on players (`KeyError`/`assert True is False`), `options.json` rewritten

- [ ] **Step 3: Implement**

`services/bluetooth/__init__.py`:

```python
def persist_device_enabled(player_name: str, enabled: bool) -> None:
    """Persist the enabled flag on the player in config.json."""
    if not _config_file_exists():
        return

    def _set_enabled(cfg: dict) -> None:
        for player in cfg.get("players", []):
            if isinstance(player, dict) and _match_player_name(player.get("name", ""), player_name):
                player["enabled"] = enabled
                break

    try:
        _update_bound_config_file(_set_enabled)
    except Exception as e:
        logger.warning("Could not persist enabled flag for '%s': %s", player_name, e)


def persist_device_released(player_name: str, released: bool, *, released_by: str | None = None) -> None:
    """Persist the released (bt_management_enabled=False) flag in PLAYER_STATE.

    ``released_by`` records who initiated the release ("auto" / "user") so
    the auto-reclaim gate (issues #349/#350) can distinguish an operator's
    deliberate release from an automatic one across a bridge restart.
    Cleared whenever the device is un-released.
    """
    if not _config_file_exists():
        return

    def _set_released(cfg: dict) -> None:
        from sendspin_bridge.config.players import player_state

        for player in cfg.get("players", []):
            if isinstance(player, dict) and _match_player_name(player.get("name", ""), player_name):
                state = player_state(cfg, player["id"])
                state["released"] = released
                if released and released_by:
                    state["released_by"] = released_by
                else:
                    state.pop("released_by", None)
                break

    try:
        _update_bound_config_file(_set_released)
    except Exception as e:
        logger.warning("Could not persist released flag for '%s': %s", player_name, e)
```

If `_sync_addon_options` has no other callers afterwards (`grep -rn _sync_addon_options src`), delete it.

`_update_bound_config_file` writes through `ConfigStore.mutate`, not
`update_config`, so it does not get Task 3's migration. `ConfigStore.mutate`
is generic (it also writes `options.json`), so migrate in the wrapper:

```python
def _update_bound_config_file(mutator) -> None:
    """Atomically mutate the config file (migrated to the current schema first)."""
    from sendspin_bridge.config import CONFIG_FILE
    from sendspin_bridge.config.migration import migrate_v5_to_v6
    from sendspin_bridge.config.store import ConfigStore

    def _migrated(cfg: dict) -> None:
        migrate_v5_to_v6(cfg)
        mutator(cfg)

    ConfigStore(CONFIG_FILE).mutate(_migrated)
```

`bt_commands.py` — in both functions replace the lookup over
`new_config.get("BLUETOOTH_DEVICES")` with a lookup by player id, and run the
migration on the freshly read file:

```python
    from sendspin_bridge.config.migration import migrate_v5_to_v6
    from sendspin_bridge.config.players import find_player_by_id

    with config_lock:
        try:
            with open(CONFIG_FILE) as fh:
                old_config = json.load(fh)
        except FileNotFoundError:
            old_config = load_config()
        migrate_v5_to_v6(old_config)
        new_config = json.loads(json.dumps(old_config))  # deep copy
        target = find_player_by_id(new_config, player_id)
        if target is None:
            return _err("Unknown device", code=404)
        target[key] = value   # apply_device_config_change
        # target["enabled"] = bool(enabled)   # apply_device_enabled
```

`apply_device_config_change` no longer needs the client's MAC: drop the
`target_mac` / `"Device has no MAC address yet"` branch but keep the
`find_client_by_player_id` 404 for unknown players. `apply_device_enabled` drops
its MAC-or-derived-id loop. `diff_configs(old_config, new_config)` now receives
v6 dicts — Task 6 makes `config_diff` read them.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/services/bluetooth`
Expected: `test_persist_v6.py` passes. Remaining failures are layout assertions
in `test_bluetooth_svc.py` / `test_persist_path_binding.py`
(`cfg["BLUETOOTH_DEVICES"][0]["enabled"]`, `options.json` contents): rewrite them
to read `players[0]` / `PLAYER_STATE`, and drop the assertions that
`options.json` is updated (the spec removes that sync).

- [ ] **Step 5: Commit**

```bash
git add -A src/sendspin_bridge/services/bluetooth tests/unit/services/bluetooth
git commit -m "feat(config): enabled/released flags persist into players / PLAYER_STATE; stop syncing options.json

The add-on's device list is no longer a source of truth (config v6), so the
tests that expected options.json to follow the flags now expect it untouched."
```

---

### Task 6: Readers switch to `device_entries()`

**Files (each a one-site change):**
- `src/sendspin_bridge/services/infrastructure/config_diff.py:213-243` (`_devices_by_mac`, `_device_index_in`)
- `src/sendspin_bridge/bridge/orchestrator.py:390`
- `src/sendspin_bridge/services/lifecycle/status_snapshot.py:329`
- `src/sendspin_bridge/services/ipc/bridge_state_model.py:186`
- `src/sendspin_bridge/services/bluetooth/duplicate_device_check.py:98`
- `src/sendspin_bridge/services/diagnostics/recovery_assistant.py:746, 950`
- `src/sendspin_bridge/services/diagnostics/operator_guidance.py:1234, 1350`
- `src/sendspin_bridge/application/bluetooth.py:416`
- `src/sendspin_bridge/application/diagnostics.py:1221-1226, 1306`
- comments only: `services/lifecycle/reconfig_orchestrator.py:465`, `services/ha/ha_state_projector.py:255`, `services/bluetooth/device_activation.py:90`, `bridge/client.py:1940` — say "players" / "device entries"
- Test: `tests/unit/config/test_config_diff_v6.py` (new)

**Interfaces:**
- Consumes: Task 1 `device_entries`

The rule for every site: `config.get("BLUETOOTH_DEVICES", [])` (or `or []`,
or `config.get("devices") or config.get("BLUETOOTH_DEVICES")`) becomes
`device_entries(config)`; `_count_config_entries(config, "BLUETOOTH_DEVICES")`
becomes `len(config.get("players") or [])` (counts every player, which is what
"configured devices" means from now on). In `application/diagnostics.py:1221`
the redaction branch keys on `"players"` and masks `backend.mac`:

```python
        elif k == "players" and isinstance(v, list):
            result[k] = [
                {
                    **p,
                    "backend": {
                        bk: (_mask_text(str(bv)) if bk == "mac" else bv)
                        for bk, bv in (p.get("backend") or {}).items()
                    },
                }
                if isinstance(p, dict)
                else p
                for p in v
            ]
```

Import at each site: `from sendspin_bridge.config.players import device_entries`
(local import inside the function where the module avoids importing config at
import time — follow what the file already does for `load_config`).

- [ ] **Step 1: Write the failing test for the diff (the only reader with its own logic)**

```python
"""config_diff reads schema v6 configs."""

from sendspin_bridge.config.players import legacy_device_to_player
from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


def _cfg(*devices):
    return {"CONFIG_SCHEMA_VERSION": 6, "players": [legacy_device_to_player(d) for d in devices], "PLAYER_STATE": {}}


def test_unchanged_players_produce_no_actions():
    cfg = _cfg({"mac": A, "player_name": "A"})
    assert diff_configs(cfg, cfg) == []


def test_added_player_starts_with_its_list_index():
    actions = diff_configs(_cfg({"mac": A}), _cfg({"mac": A}, {"mac": B}))
    start = [a for a in actions if a.kind == ActionKind.START_CLIENT]
    assert [a.mac for a in start] == [B]
    assert start[0].payload["device"]["mac"] == B


def test_volume_state_change_is_not_a_config_change():
    old = _cfg({"mac": A})
    new = _cfg({"mac": A})
    new["PLAYER_STATE"] = {old["players"][0]["id"]: {"volume": 10}}
    assert diff_configs(old, new) == []
```

Check `ActionKind` member names and the index payload key in
`config_diff.py:167-205, 293-310` and adjust the assertions to the real names
before running.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/config/test_config_diff_v6.py`
Expected: FAIL — no START action (the diff reads no devices from a v6 config)

- [ ] **Step 3: Change every listed site**, then add `"PLAYER_STATE"` to the
ignored keys next to `"LAST_VOLUMES", "LAST_SINKS"` in `config_diff.py:110`
(remove those two).

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q -p no:cacheprovider`
Expected: remaining failures are only in tests that build configs with
`BLUETOOTH_DEVICES` **and pass them straight to a function** (not through
`load_config`/`update_config`) — e.g. `test_operator_guidance.py`,
`test_recovery_assistant.py`, `test_onboarding_assistant.py`,
`test_config_diff.py`. Add one helper to `tests/support/` and use it in those
tests' config builders, without touching what they assert:

```python
# tests/support/config_v6.py
"""Build schema v6 configs from v5-style device lists in tests."""

from sendspin_bridge.config.players import legacy_device_to_player


def v6_config(devices: list[dict] | None = None, **extra) -> dict:
    return {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [legacy_device_to_player(d) for d in (devices or [])],
        "PLAYER_STATE": {},
        **extra,
    }
```

Run again until green.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "refactor(config): read speakers through device_entries() instead of BLUETOOTH_DEVICES

Tests that hand a config dict straight to a builder now build it with
tests/support/config_v6.py; their assertions are unchanged."
```

---

### Task 7: API v1 keeps the v5 shape (adapter)

**Files:**
- Modify: `src/sendspin_bridge/application/config.py` (`get_config`, `_load_existing_config_for_validation`, `_append_ma_duplicate_device_warnings`, `save_config`, `_sync_ha_options`, `export_config`, `import_config`)
- Modify: `src/sendspin_bridge/application/devices.py:140-205` (`set_enabled` disabled path, `remove`)
- Test: `tests/unit/api/test_config_v6_adapter.py` (new)

**Interfaces:**
- Consumes: Task 1 `device_entries`, `legacy_device_to_player`, `normalize_mac`; Task 2 `migrate_v5_to_v6`
- Produces (module-private, `application/config.py`):
  - `_to_api_view(file_config: dict) -> dict` — v6 → payload with `BLUETOOTH_DEVICES` (entries without `id`, `released`, `released_by`) and `LAST_VOLUMES` `{mac: volume}`; drops `players`, `PLAYER_STATE`
  - `_from_api_view(payload: dict, existing_file: dict) -> dict` — payload → v6 file dict: `players` rebuilt from `BLUETOOTH_DEVICES` reusing existing ids by MAC and keeping payload order; `PLAYER_STATE` = existing state for kept players + `LAST_VOLUMES` from payload; legacy keys removed

- [ ] **Step 1: Write the failing tests**

```python
"""API v1 still speaks the v5 config shape while the file is v6 (stage 1)."""

import json

import pytest

from sendspin_bridge.config.players import player_id_for_mac

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": "custom-a", "name": "A", "backend": {"type": "bluetooth_a2dp", "mac": A, "adapter": "hci0"}},
                    {"id": player_id_for_mac(B), "name": "B", "backend": {"type": "bluetooth_a2dp", "mac": B}},
                ],
                "PLAYER_STATE": {"custom-a": {"volume": 40, "sink": "s", "released": True}},
            }
        )
    )


@pytest.fixture()
def client():
    from tests.support.api_client import make_client

    return make_client()


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_get_config_returns_v5_shape(client):
    body = client.get("/api/v1/config").json()
    assert [d["mac"] for d in body["BLUETOOTH_DEVICES"]] == [A, B]
    assert body["BLUETOOTH_DEVICES"][0]["player_name"] == "A"
    assert "id" not in body["BLUETOOTH_DEVICES"][0]
    assert "players" not in body and "PLAYER_STATE" not in body


def test_saving_the_same_list_keeps_ids_order_and_state(client, tmp_path):
    # Review focus 4: an untouched save changes nothing Music Assistant sees.
    payload = client.get("/api/v1/config").json()
    resp = client.put("/api/v1/config", json=payload)
    assert resp.status_code == 200, resp.text
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == ["custom-a", player_id_for_mac(B)]
    assert disk["PLAYER_STATE"]["custom-a"]["sink"] == "s"
    assert disk["PLAYER_STATE"]["custom-a"]["released"] is True
    assert "BLUETOOTH_DEVICES" not in disk and "LAST_VOLUMES" not in disk


def test_saving_a_reordered_list_with_a_new_speaker(client, tmp_path):
    payload = client.get("/api/v1/config").json()
    new = "AA:BB:CC:DD:EE:03"
    payload["BLUETOOTH_DEVICES"] = [payload["BLUETOOTH_DEVICES"][1], {"mac": new, "player_name": "C"}]
    assert client.put("/api/v1/config", json=payload).status_code == 200
    disk = _disk(tmp_path)
    assert [p["id"] for p in disk["players"]] == [player_id_for_mac(B), player_id_for_mac(new)]
    assert "custom-a" not in disk["PLAYER_STATE"]


def test_export_is_v6_without_state_and_import_accepts_v5(client, tmp_path):
    exported = json.loads(client.get("/api/v1/config/export").content)
    assert "players" in exported and "PLAYER_STATE" not in exported
    v5 = {"CONFIG_SCHEMA_VERSION": 5, "BLUETOOTH_DEVICES": [{"mac": B, "player_name": "Only B"}]}
    resp = client.post("/api/v1/config/import", files={"file": ("c.json", json.dumps(v5).encode(), "application/json")})
    assert resp.status_code == 200, resp.text
    assert [p["name"] for p in _disk(tmp_path)["players"]] == ["Only B"]


def test_remove_speaker_through_devices_endpoint(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "_bt_remove_device", lambda *a, **k: None)
    resp = client.delete(f"/api/v1/devices/{player_id_for_mac(B)}")
    assert resp.status_code == 200, resp.text
    assert [p["id"] for p in _disk(tmp_path)["players"]] == ["custom-a"]
```

Check the real export route path and the import form field name in
`api/routers/config.py`, and adjust before running.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/api/test_config_v6_adapter.py`
Expected: FAIL — `KeyError: 'BLUETOOTH_DEVICES'` from GET, save writes `BLUETOOTH_DEVICES`

- [ ] **Step 3: Implement**

In `application/config.py`:

```python
def _to_api_view(file_config: dict) -> dict:
    """The v5 shape API v1 still speaks (stage 1 of config v6; removed in stage 2)."""
    from sendspin_bridge.config.players import device_entries

    view = {k: v for k, v in file_config.items() if k not in ("players", "PLAYER_STATE")}
    entries = device_entries(file_config)
    state = file_config.get("PLAYER_STATE") or {}
    view["BLUETOOTH_DEVICES"] = [
        {k: v for k, v in entry.items() if k not in ("id", "released", "released_by")} for entry in entries
    ]
    view["LAST_VOLUMES"] = {
        entry["mac"]: state[entry["id"]]["volume"]
        for entry in entries
        if isinstance(state.get(entry["id"]), dict) and isinstance(state[entry["id"]].get("volume"), int)
    }
    return view


def _from_api_view(payload: dict, existing_file: dict) -> dict:
    """Turn a v5-shaped payload back into the v6 file, keeping ids, order and state."""
    from sendspin_bridge.config.migration import migrate_v5_to_v6
    from sendspin_bridge.config.players import legacy_device_to_player, normalize_mac, player_id_for_mac

    existing = dict(existing_file)
    migrate_v5_to_v6(existing)
    ids_by_mac = {
        normalize_mac((p.get("backend") or {}).get("mac")): p["id"]
        for p in existing.get("players") or []
        if isinstance(p, dict) and p.get("id")
    }
    players = []
    for device in payload.get("BLUETOOTH_DEVICES") or []:
        if not isinstance(device, dict) or not normalize_mac(device.get("mac")):
            continue
        players.append(legacy_device_to_player(device, existing_id=ids_by_mac.get(normalize_mac(device["mac"]))))
    kept = {p["id"] for p in players}
    state = {pid: dict(entry) for pid, entry in (existing.get("PLAYER_STATE") or {}).items() if pid in kept}
    for raw_mac, volume in (payload.get("LAST_VOLUMES") or {}).items():
        mac = normalize_mac(raw_mac)
        pid = ids_by_mac.get(mac) or (player_id_for_mac(mac) if mac else "")
        if pid in kept and isinstance(volume, int) and 0 <= volume <= 100:
            state.setdefault(pid, {})["volume"] = volume
    out = {k: v for k, v in payload.items() if k not in ("BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS")}
    out["players"] = players
    out["PLAYER_STATE"] = state
    return out
```

Wire it in:

* `get_config()`: `config = _to_api_view(load_config())` as the first line; the
  enrichment loop below keeps iterating `config["BLUETOOTH_DEVICES"]` unchanged.
* `_load_existing_config_for_validation()`: return `_to_api_view(<what it reads now>)`.
* `save_config()`:
  * after `existing = loaded or {}`: `existing = _to_api_view(existing) if existing else {}`
    — everything below that reads `existing["BLUETOOTH_DEVICES"]` / `["LAST_VOLUMES"]` keeps working;
    keep the raw file in `existing_file = loaded or {}` for the write;
  * in the preserved-keys tuple replace `"LAST_VOLUMES", "LAST_SINKS"` with `"PLAYER_STATE"`
    (it is preserved from `existing_file`, not the view: write
    `if "PLAYER_STATE" in existing_file: config.setdefault("PLAYER_STATE", existing_file["PLAYER_STATE"])`
    next to the loop);
  * replace `write_config_file(config, …)` with
    `write_config_file(_from_api_view(config, existing_file), config_file=CONFIG_FILE, config_dir=CONFIG_FILE.parent)`.
* `_sync_ha_options()`: delete the `sup_devices` block and the `"bluetooth_devices": sup_devices,` entry.
* `export_config()`: export the v6 file without `PLAYER_STATE` (read the stored
  file through the same path it uses now, then `pop("PLAYER_STATE", None)`;
  make sure the sanitiser still masks secrets).
* `import_config()`: right after the `isinstance(uploaded, dict)` check, if
  `"players" in uploaded`, set `uploaded = _to_api_view(uploaded)` so the v5
  validator sees its usual shape; before `ConfigStore(CONFIG_FILE).replace(uploaded, …)`
  convert with `uploaded = _from_api_view(uploaded, ConfigStore(CONFIG_FILE).read_stored() or {})`.
  Check `ConfigStore.replace`'s `owned`/`preserve` semantics and keep `PLAYER_STATE`
  from the stored file (add it to `preserve`).

In `application/devices.py`:

* disabled path of `set_enabled`: iterate `device_entries(load_config())` instead
  of `load_config().get("BLUETOOTH_DEVICES", [])` and match on `entry["id"] == device_id`
  (drops the `_player_id_from_mac` call).
* `remove()`: build the payload from the API view —
  `config = config_uc._to_api_view(load_config())` — the rest of the function is unchanged.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/api tests/unit/application`
Expected: new tests pass. Existing API tests that only use the API stay green
(that is the point of the adapter). Fix tests that read the **file** after a
save expecting `BLUETOOTH_DEVICES`/`LAST_VOLUMES` (`test_config_and_diagnostics.py`,
`test_config_preserve_secrets.py`, `test_config_ordering.py`,
`test_device_commands.py`) to read `device_entries(...)` / `PLAYER_STATE`.

- [ ] **Step 5: Commit**

```bash
git add -A src/sendspin_bridge/application tests/unit/api tests/unit/application
git commit -m "feat(config): API v1 keeps the v5 config shape over the v6 file

Tests that read config.json after a save now read players/PLAYER_STATE;
what they assert about the saved values is unchanged."
```

---

### Task 8: Home Assistant add-on

**Files:**
- Modify: `scripts/translate_ha_config.py:170-365`
- Modify: `ha-addon/config.yaml` (remove `bluetooth_devices` from `options:` and `schema:`), `ha-addon/translations/en.yaml` (remove its `bluetooth_devices` block)
- Test: `tests/integration/test_translate_ha_config.py` (append; update layout assertions)

**Interfaces:**
- Consumes: Task 2 migration (via the bridge's `load_config` on next start)

Behaviour: when the existing `config.json` has `players`, the translator copies
`players` and `PLAYER_STATE` over and **ignores** `bluetooth_devices` (prints one
line if it is non-empty). Otherwise (first start after upgrade, or a fresh
install) it writes the legacy `BLUETOOTH_DEVICES` (+ the existing preservation
logic) and preserves `LAST_VOLUMES`/`LAST_SINKS`, and the bridge's
`load_config()` migrates them.

- [ ] **Step 1: Write the failing tests**

```python
def test_v6_players_survive_and_options_devices_are_ignored(tmp_path, capsys):
    # Review focus 2: a speaker removed in the UI does not come back from options.json.
    existing = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [{"id": "p1", "name": "Kept", "backend": {"type": "bluetooth_a2dp", "mac": "11:22:33:44:55:66"}}],
        "PLAYER_STATE": {"p1": {"volume": 33}},
    }
    _write_json(tmp_path / "config.json", existing)
    _write_json(tmp_path / "options.json", _minimal_options())  # still lists AA:BB:CC:DD:EE:FF

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["players"] == existing["players"]
    assert cfg["PLAYER_STATE"] == {"p1": {"volume": 33}}
    assert "BLUETOOTH_DEVICES" not in cfg
    assert "bluetooth_devices" in capsys.readouterr().out


def test_first_start_after_upgrade_hands_legacy_devices_to_the_bridge(tmp_path, monkeypatch):
    _write_json(tmp_path / "config.json", {"CONFIG_SCHEMA_VERSION": 5, "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 50}})
    _write_json(tmp_path / "options.json", _minimal_options())
    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    loaded = config.load_config()
    assert [p["backend"]["mac"] for p in loaded["players"]] == ["AA:BB:CC:DD:EE:FF"]
    pid = loaded["players"][0]["id"]
    assert loaded["PLAYER_STATE"][pid]["volume"] == 50


def test_addon_schema_has_no_device_list():
    import yaml

    addon = yaml.safe_load((Path(__file__).resolve().parents[2] / "ha-addon" / "config.yaml").read_text())
    assert "bluetooth_devices" not in addon["options"]
    assert "bluetooth_devices" not in addon["schema"]
```

Check how the existing tests point the script at `tmp_path` (module-level
`OPTIONS_FILE`/`CONFIG_FILE` from env, probably via an autouse fixture) and reuse it.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q -p no:cacheprovider tests/integration/test_translate_ha_config.py -k "v6 or upgrade or schema_has"`
Expected: FAIL — `KeyError: 'players'`, schema still has `bluetooth_devices`

- [ ] **Step 3: Implement**

In `main()` of `scripts/translate_ha_config.py`, read the existing config once
at the start (the file is already read later inside `try:`; move that read up
and reuse it), then:

```python
    existing_players = existing.get("players") if isinstance(existing.get("players"), list) else None
    options_devices = list(opts.get("bluetooth_devices") or [])
    if existing_players is not None:
        config["players"] = existing_players
        config["PLAYER_STATE"] = existing.get("PLAYER_STATE") or {}
        if options_devices:
            print(
                "[translate_ha_config] bluetooth_devices in the add-on options is ignored: "
                "speakers are managed in the bridge's web interface"
            )
    else:
        config["BLUETOOTH_DEVICES"] = options_devices
```

Remove `"BLUETOOTH_DEVICES": list(opts.get("bluetooth_devices") or [])` from the
literal; guard the normalisation loop, the `LAST_VOLUMES`/`LAST_SINKS`
preservation and the per-device preservation block with
`if "BLUETOOTH_DEVICES" in config:`; the final `print` counts
`len(config.get("players") or config.get("BLUETOOTH_DEVICES") or [])`.
Keep the script's `CONFIG_SCHEMA_VERSION` import as is (it writes 6, and the
bridge's migration converts `BLUETOOTH_DEVICES` regardless of the number).

In `ha-addon/config.yaml` delete `bluetooth_devices: []` under `options:` and
the whole `bluetooth_devices:` list under `schema:`; delete the
`bluetooth_devices:` block in `ha-addon/translations/en.yaml`. Run
`uv run python scripts/generate_ha_addon_variants.py check-current-repo` and,
if it reports drift for `ha-addon-beta`/`ha-addon-rc`, leave it — those
directories are regenerated by `release.yml`; note it in the PR.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/integration/test_translate_ha_config.py tests/integration/test_sync_ha_options_ha_integration.py`
Expected: new tests pass; rewrite layout assertions in the existing ones
(`test_runtime_state_preserved`, `test_static_delay_ms_preserved_*`,
`test_released_flag_*`) only where they read a v6 file — the first-start path
keeps the v5 keys, so most of them stay as they are.

- [ ] **Step 5: Commit**

```bash
git add -A scripts/translate_ha_config.py ha-addon/config.yaml ha-addon/translations/en.yaml tests/integration
git commit -m "feat(addon): speakers leave the add-on options; config.json players are the source of truth"
```

---

### Task 9: Guidance when the migration failed

**Files:**
- Modify: `src/sendspin_bridge/services/diagnostics/guidance_issue_registry.py` (new entry)
- Modify: `src/sendspin_bridge/services/diagnostics/recovery_assistant.py` (builder + call next to `_build_config_writable_issue`)
- Test: `tests/unit/services/diagnostics/test_recovery_assistant.py` (append)

**Interfaces:**
- Consumes: Task 3 `get_config_migration_error()`, `V5_BACKUP_NAME`

- [ ] **Step 1: Write the failing test**

```python
def test_config_migration_failure_is_a_recovery_issue(monkeypatch):
    import sendspin_bridge.services.diagnostics.recovery_assistant as ra

    monkeypatch.setattr(ra, "get_config_migration_error", lambda: "RuntimeError: boom")
    issue = ra._build_config_migration_issue()
    assert issue is not None
    assert issue.key == "config_migration_failed"
    assert issue.severity == "error"
    assert "boom" in issue.summary

    monkeypatch.setattr(ra, "get_config_migration_error", lambda: None)
    assert ra._build_config_migration_issue() is None
```

Also confirm `tests/unit/services/diagnostics/test_issue_registry_keys.py`
fails until the registry entry exists (it checks issue keys against the registry).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/services/diagnostics/test_recovery_assistant.py -k migration`
Expected: FAIL — `AttributeError: _build_config_migration_issue`

- [ ] **Step 3: Implement**

Registry:

```python
    "config_migration_failed": GuidanceIssueDefinition(
        key="config_migration_failed",
        layer="storage",
        priority=14,
        severity="error",
        default_reason_codes=("config_migration_failed",),
    ),
```

`recovery_assistant.py` (import `get_config_migration_error`, `V5_BACKUP_NAME`
from `sendspin_bridge.config` at the top with the other config imports):

```python
def _build_config_migration_issue() -> RecoveryIssue | None:
    """The config file could not be moved to the current schema; speakers are not loaded."""
    error = get_config_migration_error()
    if not error:
        return None
    primary_action, secondary_actions = build_recovery_issue_actions("config_migration_failed", [])
    return RecoveryIssue(
        key="config_migration_failed",
        severity="error",
        title="The configuration could not be updated to this version",
        summary=(
            "The bridge started without speakers and left config.json untouched. "
            f"Error: {error}. Please report it with the bug report button; "
            f"a copy of the old file is kept as {V5_BACKUP_NAME} once an update succeeds."
        ),
        primary_action=primary_action,
        secondary_actions=secondary_actions,
    )
```

Append it to the issue list where `_build_config_writable_issue(...)` is appended.

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q -p no:cacheprovider tests/unit/services/diagnostics`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sendspin_bridge/services/diagnostics tests/unit/services/diagnostics
git commit -m "feat(diagnostics): recovery card when the config cannot be migrated"
```

---

### Task 10: Changelog, full verification, live check

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Changelog** (`### Changed`, plus `### Removed` after it):

```markdown
### Changed

- The configuration file moves to a new format (schema 6) on the first start:
  speakers are stored as players, and their saved volume, audio output and
  "released" state are kept apart from the settings. A copy of the previous
  file is saved as `config.v5.bak.json`. Going back to a 2.x version after this
  update starts without speakers.

### Removed

- The Home Assistant add-on no longer lists speakers in its Configuration tab.
  Speakers are added, changed and removed in the bridge's web interface; the
  add-on's old list is read once when updating and ignored afterwards.
```

Run: `uv run python scripts/lint_changelog.py CHANGELOG.md`
Expected: `CHANGELOG.md: clean`

- [ ] **Step 2: Full suite and lint**

Run: `uv run pytest -q -p no:cacheprovider && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all pass

- [ ] **Step 3: Live check with the production-shaped config**

```bash
mkdir -p "$CLAUDE_JOB_DIR/tmp/v6-live" && cp tests/fixtures/config_v5_production.json "$CLAUDE_JOB_DIR/tmp/v6-live/config.json"
CONFIG_DIR="$CLAUDE_JOB_DIR/tmp/v6-live" WEB_PORT=8099 uv run python -m sendspin_bridge
```

(Follow `CLAUDE.md` "Local Demo Workflow": one instance, check the port first.)
Check: the log has "Migrated 6 Bluetooth device(s) to players"; `config.json`
is v6 and `config.v5.bak.json` equals the fixture; `GET /api/v1/config` lists the
six speakers in order; the SPA's device list and disabled list match the
fixture; saving the settings page unchanged produces no restart actions in the
log. Stop the instance and verify the PID is gone.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): config schema 6"
```

---

## Self-review notes

* Spec §1 rules → Tasks 1–2 (ids, renames, backend, state, malformed and
  duplicate entries), §1 "where it runs" → Tasks 2–3, §1 add-on → Task 8,
  §4 migration errors → Tasks 2, 3, 9. API `422` rules, `capabilities`,
  `GET /audio/sinks` and `pulse_sink` runtime are stages 2–3, not this plan.
* `pulse_sink` defaulting to `volume_controller: sendspin` is stage 3 (no
  `pulse_sink` player can be created before then).
