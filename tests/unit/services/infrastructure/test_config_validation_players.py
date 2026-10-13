"""Server-side bounds of a player's own settings."""

import pytest

from sendspin_bridge.services.infrastructure.config_validation import validate_uploaded_config


def _validate(minutes):
    player = {
        "id": "p1",
        "name": "A",
        "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:01"},
        "power_save_delay_minutes": minutes,
    }
    return validate_uploaded_config({"CONFIG_SCHEMA_VERSION": 6, "players": [player]}, default_base_listen_port=8928)


@pytest.mark.parametrize("minutes", [61, 120, -1])
def test_power_save_delay_out_of_range_is_a_field_error(minutes):
    result = _validate(minutes)
    assert not result.is_valid
    assert any(e.field == "players[0].power_save_delay_minutes" for e in result.errors)


@pytest.mark.parametrize("minutes", [0, 1, 60])
def test_power_save_delay_in_range_is_valid(minutes):
    assert _validate(minutes).is_valid
