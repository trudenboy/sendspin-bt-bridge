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


@pytest.mark.parametrize(("minutes", "clamped"), [(61, 60), (120, 60), (-1, 0)])
def test_power_save_delay_out_of_range_is_clamped_not_an_error(minutes, clamped):
    result = _validate(minutes)
    assert result.is_valid
    assert result.normalized_config["players"][0]["power_save_delay_minutes"] == clamped


@pytest.mark.parametrize("minutes", [0, 1, 60])
def test_power_save_delay_in_range_is_valid(minutes):
    assert _validate(minutes).is_valid
