from __future__ import annotations

import json

import sendspin_bridge.config as config
from sendspin_bridge.services.bluetooth import adapter_names


def _write_config(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _reset_cache() -> None:
    adapter_names._adapter_names_by_mac = {}
    adapter_names._adapter_cache_loaded = False


def test_refresh_adapter_name_cache_reads_current_config_path(tmp_path, monkeypatch):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_config(first, {"adapters": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Old adapter"}]})
    _write_config(second, {"adapters": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "New adapter"}]})

    _reset_cache()
    monkeypatch.setattr(config, "CONFIG_FILE", first)
    assert adapter_names.get_adapter_name("AA:BB:CC:DD:EE:FF") == "Old adapter"

    monkeypatch.setattr(config, "CONFIG_FILE", second)
    adapter_names.refresh_adapter_name_cache()

    assert adapter_names.get_adapter_name("AA:BB:CC:DD:EE:FF") == "New adapter"


def test_get_adapter_name_does_not_reload_empty_cache_on_every_lookup(tmp_path, monkeypatch):
    empty_config = tmp_path / "empty.json"
    _write_config(empty_config, {"adapters": []})

    _reset_cache()
    monkeypatch.setattr(config, "CONFIG_FILE", empty_config)

    load_calls = 0
    real_load = adapter_names.load_adapter_name_cache

    def _counting_load() -> None:
        nonlocal load_calls
        load_calls += 1
        real_load()

    monkeypatch.setattr(adapter_names, "load_adapter_name_cache", _counting_load)

    assert adapter_names.get_adapter_name("AA:BB:CC:DD:EE:FF") is None
    assert adapter_names.get_adapter_name("AA:BB:CC:DD:EE:FF") is None
    assert load_calls == 1
