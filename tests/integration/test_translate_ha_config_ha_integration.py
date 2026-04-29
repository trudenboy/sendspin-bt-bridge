"""Tests for ``ha_integration`` addon-options translation in
``scripts/translate_ha_config.py``."""

from __future__ import annotations

from scripts.translate_ha_config import _translate_ha_integration


def test_translate_empty_block_uses_defaults():
    out = _translate_ha_integration({})
    assert out["enabled"] is False
    assert out["mode"] == "off"
    assert out["mqtt"]["broker"] == "auto"
    assert out["mqtt"]["port"] == 1883
    assert out["mqtt"]["discovery_prefix"] == "homeassistant"
    assert out["mqtt"]["tls"] is False
    assert out["rest"]["advertise_mdns"] is True
    assert out["rest"]["supervisor_pair"] is True


def test_translate_re_nests_flat_addon_keys():
    """Supervisor schema is flat (mqtt_broker, mqtt_port, ...). The
    bridge wants nested HA_INTEGRATION.mqtt.{broker,port,...}."""
    out = _translate_ha_integration(
        {
            "enabled": True,
            "mode": "mqtt",
            "mqtt_broker": "192.168.1.10",
            "mqtt_port": 8883,
            "mqtt_username": "u",
            "mqtt_password": "p",
            "mqtt_tls": True,
            "advertise_mdns": False,
            "supervisor_pair": False,
        }
    )
    assert out["enabled"] is True
    assert out["mode"] == "mqtt"
    assert out["mqtt"]["broker"] == "192.168.1.10"
    assert out["mqtt"]["port"] == 8883
    assert out["mqtt"]["username"] == "u"
    assert out["mqtt"]["password"] == "p"
    assert out["mqtt"]["tls"] is True
    assert out["rest"]["advertise_mdns"] is False
    assert out["rest"]["supervisor_pair"] is False


def test_translate_handles_non_dict_input():
    """Schema validation has already run by the time this runs, but defend
    against malformed Supervisor payloads anyway."""
    out = _translate_ha_integration("garbage")  # type: ignore[arg-type]
    assert out["enabled"] is False


def test_translate_int_coercion_for_port():
    out = _translate_ha_integration({"mqtt_port": "1884"})
    assert out["mqtt"]["port"] == 1884
