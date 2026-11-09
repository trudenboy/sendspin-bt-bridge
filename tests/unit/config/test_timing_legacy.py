import json

from sendspin_bridge.config import save_device_static_delay


def test_confirmed_timing_can_be_saved_when_the_file_still_uses_v5(tmp_config):
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 5,
                "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen", "static_delay_ms": 10}],
                "UPDATE_CHANNEL": "beta",
            }
        )
    )
    save_device_static_delay("AA:BB:CC:DD:EE:FF", 42, source="manual", strict=True)
    stored = json.loads(tmp_config.read_text())
    assert stored["players"][0]["static_delay_ms"] == 42
    assert stored["players"][0]["static_delay_source"] == "manual"
    assert stored["UPDATE_CHANNEL"] == "beta"
