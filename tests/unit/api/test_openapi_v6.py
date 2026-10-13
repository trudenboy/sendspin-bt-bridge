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
