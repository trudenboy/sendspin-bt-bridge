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
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, BT, (True, None)),
        (
            {"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"},
            {**BT, "playout_sync_enabled": False},
            (False, "needs_measure"),
        ),
        (
            {"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"},
            {"backend": {"type": "pulse_sink", "sink": "x"}},
            (False, "not_bluetooth"),
        ),
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


# -- calibration storage (A15) ---------------------------------------------------------------

E = {
    "codec": "sbc",
    "rate": 44100,
    "adapter": "C0:FB:F9:62:D7:D6",
    "latency_ms": 203.4,
    "measured_at": "2026-10-28T09:00:00+00:00",
}


def _player(**extra):
    return {"id": "p", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}, **extra}


def test_resolve_by_codec_rate_and_adapter():
    from sendspin_bridge.config import resolve_air_latency

    p = {"air_to_sound_latency": [E]}
    assert resolve_air_latency(p, "sbc", 44100, "C0:FB:F9:62:D7:D6") == 203.4
    assert resolve_air_latency(p, "sbc", 48000, "C0:FB:F9:62:D7:D6") is None
    assert resolve_air_latency(p, "sbc", 44100, "C0:FB:F9:62:D6:9D") is None
    assert resolve_air_latency(p, "aac", 44100, "C0:FB:F9:62:D7:D6") is None


def test_resolve_normalises_case_and_needs_every_part():
    from sendspin_bridge.config import resolve_air_latency

    p = {"air_to_sound_latency": [E]}
    assert resolve_air_latency(p, "SBC", 44100, "c0:fb:f9:62:d7:d6") == 203.4
    assert resolve_air_latency(p, None, 44100, "C0:FB:F9:62:D7:D6") is None
    assert resolve_air_latency(p, "sbc", None, "C0:FB:F9:62:D7:D6") is None
    assert resolve_air_latency(p, "sbc", 44100, None) is None
    assert resolve_air_latency({}, "sbc", 44100, "C0:FB:F9:62:D7:D6") is None
    assert resolve_air_latency({"air_to_sound_latency": None}, "sbc", 44100, "C0:FB:F9:62:D7:D6") is None
    # A malformed stored entry never resolves.
    bad = {"air_to_sound_latency": ["x", {**E, "latency_ms": "203"}, {**E, "latency_ms": 5000}]}
    assert resolve_air_latency(bad, "sbc", 44100, "C0:FB:F9:62:D7:D6") is None


def test_upsert_replaces_same_key_and_caps_at_8():
    from sendspin_bridge.config import upsert_air_latency

    entries = [E]
    entries = upsert_air_latency(entries, {**E, "latency_ms": 199.0, "measured_at": "2026-10-29T00:00:00+00:00"})
    assert [e["latency_ms"] for e in entries] == [199.0]
    for k in range(10):
        entries = upsert_air_latency(
            entries, {**E, "adapter": f"C0:FB:F9:62:D7:{k:02X}", "measured_at": f"2026-11-{k + 1:02d}T00:00:00+00:00"}
        )
    assert len(entries) == 8


def test_upsert_evicts_the_oldest_measurement():
    from sendspin_bridge.config import upsert_air_latency

    entries: list[dict] = []
    for k in range(8):
        entries = upsert_air_latency(
            entries, {**E, "adapter": f"C0:FB:F9:62:D7:{k:02X}", "measured_at": f"2026-11-{k + 2:02d}T00:00:00+00:00"}
        )
    # Saved last, measured first: still the oldest measurement, so it is the one that goes.
    entries = upsert_air_latency(
        entries, {**E, "adapter": "C0:FB:F9:62:D7:AA", "measured_at": "2026-10-01T00:00:00+00:00"}
    )
    assert len(entries) == 8
    assert "C0:FB:F9:62:D7:AA" not in {e["adapter"] for e in entries}
    entries = upsert_air_latency(
        entries, {**E, "adapter": "C0:FB:F9:62:D7:BB", "measured_at": "2026-12-01T00:00:00+00:00"}
    )
    adapters = {e["adapter"] for e in entries}
    assert "C0:FB:F9:62:D7:BB" in adapters and "C0:FB:F9:62:D7:00" not in adapters


def test_player_model_validates_entries():
    from pydantic import ValidationError

    from sendspin_bridge.config.players import Player

    with pytest.raises(ValidationError):
        Player.model_validate(
            {
                "id": "p",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                "air_to_sound_latency": [{**E, "latency_ms": -1}],
            }
        )


@pytest.mark.parametrize(
    "entries",
    [
        [{**E, "latency_ms": 2000.5}],
        [{**E, "rate": 22050}],
        [{**E, "codec": "aac"}],
        [{**E, "adapter": "hci1"}],
        [{**E, "spread_ms": 501}],
        [{**E, "reference": "r" * 81}],
        [{**E, "source": "guess"}],
        [{**E, "extra": 1}],
        [{k: v for k, v in E.items() if k != "measured_at"}],
        [E, {**E, "latency_ms": 1.0}],  # same key twice
        [{**E, "adapter": f"C0:FB:F9:62:D7:{k:02X}"} for k in range(9)],  # more than 8
    ],
)
def test_player_model_rejects_bad_calibrations(entries):
    from pydantic import ValidationError

    from sendspin_bridge.config.players import Player

    with pytest.raises(ValidationError):
        Player.model_validate(_player(air_to_sound_latency=entries))


def test_player_model_takes_a_full_entry():
    from sendspin_bridge.config.players import Player

    full = {**E, "spread_ms": 3.1, "reference": "Chrome (headset)", "source": "sync_wizard"}
    player = Player.model_validate(_player(air_to_sound_latency=[full]))
    assert player.model_dump(mode="json", exclude_unset=True)["air_to_sound_latency"] == [full]
    assert Player.model_validate(_player()).air_to_sound_latency is None


def test_schema_describes_the_calibration_list():
    schema = json.loads(SCHEMA.read_text())
    prop = schema["$defs"]["Player"]["properties"]["air_to_sound_latency"]
    array = next(alt for alt in prop["anyOf"] if alt.get("type") == "array")
    assert array["maxItems"] == 8 and array["items"] == {"$ref": "#/$defs/AirLatencyEntry"}
    entry = schema["$defs"]["AirLatencyEntry"]
    assert entry["additionalProperties"] is False
    assert set(entry["required"]) == {"codec", "rate", "adapter", "latency_ms", "measured_at"}
    assert entry["properties"]["latency_ms"]["minimum"] == 0 and entry["properties"]["latency_ms"]["maximum"] == 2000
    assert entry["properties"]["rate"]["enum"] == [16000, 32000, 44100, 48000]


def test_calibration_change_is_hot():
    from sendspin_bridge.services.infrastructure.config_diff import iter_device_hot_fields

    assert "air_to_sound_latency" in iter_device_hot_fields()


def test_a_calibration_change_is_a_hot_apply_with_the_whole_list():
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
    from tests.support.config_v6 import v6_config

    before = v6_config([{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "S", "air_to_sound_latency": [E]}])
    after = v6_config([{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "S", "air_to_sound_latency": []}])
    actions = diff_configs(before, after)
    assert [a.kind for a in actions] == [ActionKind.HOT_APPLY]
    assert actions[0].payload == {"air_to_sound_latency": []}
    # Missing and empty are the same: no action.
    unset = v6_config([{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "S"}])
    assert diff_configs(unset, after) == []


def test_loading_drops_bad_calibrations_and_keeps_the_player():
    from sendspin_bridge.config.migration import _normalize_players

    good = [
        {**E, "adapter": f"C0:FB:F9:62:D7:{k:02X}", "measured_at": f"2026-11-{k + 1:02d}T00:00:00+00:00"}
        for k in range(9)
    ]
    cfg = {"players": [_player(air_to_sound_latency=[{**E, "latency_ms": -1}, "x", *good])]}
    _normalize_players(cfg)
    assert [p["id"] for p in cfg["players"]] == ["p"]
    kept = cfg["players"][0]["air_to_sound_latency"]
    # Invalid ones dropped; of the valid ones the newest 8 measurements stay.
    assert len(kept) == 8 and "C0:FB:F9:62:D7:00" not in {e["adapter"] for e in kept}
