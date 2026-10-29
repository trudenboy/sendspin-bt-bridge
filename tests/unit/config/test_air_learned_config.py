import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from sendspin_bridge.config.players import (
    AirLatencyEntry,
    AirLearnedEntry,
    forget_air_learned,
    player_id_for_mac,
    record_air_learned_sample,
    resolve_air_learned,
    valid_air_learned_entries,
)
from sendspin_bridge.services.audio.playout.air_learned import learned_reference

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"
ADAPTER = "C0:FB:F9:62:D7:D6"
KEY = ("sbc", 44100, ADAPTER)
MAC = "AA:BB:CC:DD:EE:FF"
PID = player_id_for_mac(MAC)


def _entry(samples=(250.0, 260.0), d_out=(100.0, 110.0), **over):
    latency, d_ref = learned_reference(samples, d_out)
    base = {
        "codec": "sbc",
        "rate": 44100,
        "adapter": ADAPTER,
        "source": "learned",
        "samples_ms": list(samples),
        "d_out_ms": list(d_out),
        "latency_ms": latency,
        "d_ref_ms": d_ref,
        "starts": len(samples),
        "measured_at": "2026-10-28T09:00:00+00:00",
    }
    base.update(over)
    return base


def test_model_accepts_valid_entry():
    entry = AirLearnedEntry.model_validate(_entry())
    assert entry.starts == 2


@pytest.mark.parametrize(
    "over",
    [
        {"samples_ms": ["250", "260"]},
        {"samples_ms": [250.0] * 9, "d_out_ms": [100.0] * 9, "starts": 9},
        {"d_out_ms": [100.0]},
        {"samples_ms": [1101.0, 260.0], "d_out_ms": [100.0, 110.0]},
        {"d_out_ms": [5001.0, 110.0], "samples_ms": [5001.0, 260.0]},
        {"latency_ms": 1.0},
        {"d_ref_ms": 1.0},
        {"starts": 1},
        {"unknown": 1},
    ],
)
def test_model_rejects(over):
    with pytest.raises(ValidationError):
        AirLearnedEntry.model_validate(_entry(**over))


def test_model_rejects_latency_off_by_one_ms():
    good = _entry()
    with pytest.raises(ValidationError):
        AirLearnedEntry.model_validate({**good, "latency_ms": good["latency_ms"] + 1})
    with pytest.raises(ValidationError):
        AirLearnedEntry.model_validate({**good, "d_ref_ms": good["d_ref_ms"] + 1})


def test_calibration_gains_d_ref_ms():
    base = {"codec": "sbc", "rate": 44100, "adapter": ADAPTER, "latency_ms": 200.0, "measured_at": "x"}
    assert AirLatencyEntry.model_validate({**base, "d_ref_ms": 120.0}).d_ref_ms == 120.0
    assert AirLatencyEntry.model_validate(base).d_ref_ms is None
    with pytest.raises(ValidationError):
        AirLatencyEntry.model_validate({**base, "d_ref_ms": 6000})


def test_record_first_sample():
    out = record_air_learned_sample([], KEY, 250.0, 100.0, "2026-10-29T10:00:00+00:00")
    assert len(out) == 1
    assert out[0]["samples_ms"] == [250.0] and out[0]["d_out_ms"] == [100.0]
    assert out[0]["starts"] == 1
    assert out[0]["measured_at"] == "2026-10-29T10:00:00+00:00"
    assert (out[0]["latency_ms"], out[0]["d_ref_ms"]) == learned_reference([250.0], [100.0])


def test_record_nine_keeps_newest_eight():
    entries: list = []
    for i in range(9):
        entries = record_air_learned_sample(entries, KEY, 250.0 + i, 100.0, f"2026-10-29T10:0{i}:00+00:00")
    assert len(entries) == 1
    assert entries[0]["samples_ms"] == [250.0 + i for i in range(1, 9)]
    assert entries[0]["starts"] == 9
    assert (entries[0]["latency_ms"], entries[0]["d_ref_ms"]) == learned_reference(
        entries[0]["samples_ms"], entries[0]["d_out_ms"]
    )
    AirLearnedEntry.model_validate(entries[0])


def test_resolve_needs_two_pairs():
    one = record_air_learned_sample([], KEY, 250.0, 100.0, "2026-10-29T10:00:00+00:00")
    assert resolve_air_learned({"air_learned_latency": one}, "sbc", 44100, ADAPTER) is None
    two = record_air_learned_sample(one, KEY, 260.0, 110.0, "2026-10-29T10:01:00+00:00")
    latency, d_ref, starts = resolve_air_learned({"air_learned_latency": two}, "SBC", 44100, ADAPTER.lower())  # type: ignore[misc]
    assert (latency, d_ref) == learned_reference([250.0, 260.0], [100.0, 110.0])
    assert starts == 2
    assert resolve_air_learned({"air_learned_latency": two}, "sbc", 48000, ADAPTER) is None
    assert resolve_air_learned({}, "sbc", 44100, ADAPTER) is None


def test_forget_one_and_all():
    a = _entry()
    b = _entry(rate=48000)
    rest, n = forget_air_learned([a, b], "sbc", 48000, ADAPTER)
    assert n == 1 and rest == [a]
    rest, n = forget_air_learned([a, b])
    assert n == 2 and rest == []


def test_valid_entries_drops_bad_keeps_others():
    out = valid_air_learned_entries([_entry(), {"junk": 1}, _entry(rate=48000, latency_ms=3.0)])
    assert [e["rate"] for e in out] == [44100]


def test_valid_entries_one_per_key():
    old = _entry(measured_at="2026-10-28T09:00:00+00:00")
    new = _entry(measured_at="2026-10-28T10:00:00+00:00")
    assert valid_air_learned_entries([new, old]) == [new]


def _player(**extra):
    return {"id": PID, "name": "S", "backend": {"type": "bluetooth_a2dp", "mac": MAC}, **extra}


def test_migration_drops_invalid_learned_entry_keeps_player(caplog):
    from sendspin_bridge.config.migration import _normalize_players

    cfg = {"players": [_player(air_learned_latency=[_entry(), {"junk": 1}])]}
    with caplog.at_level(logging.WARNING):
        _normalize_players(cfg)
    assert len(cfg["players"]) == 1
    assert cfg["players"][0]["air_learned_latency"] == [_entry()]
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def _calibration(**over):
    return {"codec": "sbc", "rate": 44100, "adapter": ADAPTER, "latency_ms": 200.0, "measured_at": "x", **over}


def test_migration_fills_d_ref_from_static_delay(caplog):
    from sendspin_bridge.config.migration import _normalize_players

    cfg = {"players": [_player(static_delay_ms=150, air_to_sound_latency=[_calibration()])]}
    with caplog.at_level(logging.INFO):
        _normalize_players(cfg)
    assert cfg["players"][0]["air_to_sound_latency"][0]["d_ref_ms"] == 150.0
    assert sum(1 for r in caplog.records if r.levelno == logging.INFO and "d_ref" in r.getMessage()) == 1


def test_migration_fills_d_ref_zero_without_static_delay():
    from sendspin_bridge.config.migration import _normalize_players

    cfg = {"players": [_player(air_to_sound_latency=[_calibration()])]}
    _normalize_players(cfg)
    assert cfg["players"][0]["air_to_sound_latency"][0]["d_ref_ms"] == 0.0


def test_migration_keeps_existing_d_ref_and_drops_bad():
    from sendspin_bridge.config.migration import _normalize_players

    cfg = {
        "players": [
            _player(
                static_delay_ms=150,
                air_to_sound_latency=[_calibration(d_ref_ms=90.0), _calibration(rate=48000, d_ref_ms=6000)],
            )
        ]
    }
    _normalize_players(cfg)
    entries = cfg["players"][0]["air_to_sound_latency"]
    assert [(e["rate"], e["d_ref_ms"]) for e in entries] == [(44100, 90.0)]


def test_schema_declares_learned_and_d_ref():
    schema = json.loads(SCHEMA.read_text())
    player = schema["$defs"]["Player"]["properties"]
    option = next(o for o in player["air_learned_latency"]["anyOf"] if o.get("type") == "array")
    assert option["maxItems"] == 8
    assert "d_ref_ms" in schema["$defs"]["AirLatencyEntry"]["properties"]
    assert "AirLearnedEntry" in schema["$defs"]


def test_config_diff_learned_is_hot_apply():
    from sendspin_bridge.services.infrastructure.config_diff import _DEVICE_HOT_FIELDS

    assert "air_learned_latency" in _DEVICE_HOT_FIELDS


def test_save_air_learned_sample(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [_player()]}))
    config.save_air_learned_sample(PID, KEY, 250.0, 100.0)
    config.save_air_learned_sample(PID, KEY, 260.0, 110.0)
    disk = json.loads((tmp_path / "config.json").read_text())
    entries = disk["players"][0]["air_learned_latency"]
    assert len(entries) == 1 and entries[0]["samples_ms"] == [250.0, 260.0] and entries[0]["starts"] == 2
