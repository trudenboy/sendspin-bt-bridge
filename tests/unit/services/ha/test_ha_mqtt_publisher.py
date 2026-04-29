"""Discovery payload + lifecycle tests for ``services/ha_mqtt_publisher.py``.

We don't spin up a real broker.  Discovery payloads are pure functions and
get golden-file-style assertions; the publisher's command-handler loop is
tested by feeding synthetic messages directly.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.ha.ha_entity_model import BRIDGE_ENTITIES, DEVICE_ENTITIES, EntityKind
from sendspin_bridge.services.ha.ha_mqtt_publisher import (
    HaMqttPublisher,
    MqttPublisherConfig,
    build_bridge_state_payload,
    build_device_state_payload,
    build_discovery_payloads,
    publisher_status,
    resolve_mqtt_config,
)
from sendspin_bridge.services.ha.ha_state_projector import (
    BridgeMeta,
    DeviceMeta,
    EntityState,
    HAStateProjection,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg():
    return MqttPublisherConfig(
        enabled=True,
        host="broker.local",
        port=1883,
        username="bridge",
        password="secret",
        discovery_prefix="homeassistant",
        tls=False,
        client_id="sendspin_haos",
        bridge_id="haos",
        bridge_name="HAOS Bridge",
    )


@pytest.fixture
def projection():
    """Synthetic projection with one device + bridge meta."""
    meta = DeviceMeta(
        player_id="player-aaa",
        mac="fc:58:fa:eb:08:6c",
        player_name="ENEBY20",
        adapter_name="hci0",
        room_name="Kitchen",
    )
    bridge_meta = BridgeMeta(
        bridge_id="haos",
        bridge_name="HAOS Bridge",
        version="2.65.0",
        web_url="http://192.168.1.10:8080",
    )
    devices = {
        "player-aaa": {
            "bluetooth_connected": EntityState(value=True),
            "audio_streaming": EntityState(value=True),
            "rssi_dbm": EntityState(value=-55, attrs={"unit_of_measurement": "dBm"}),
            "battery_level": EntityState(value=80, attrs={"unit_of_measurement": "%"}),
            "idle_mode": EntityState(value="default"),
            "static_delay_ms": EntityState(value=0),
            "enabled": EntityState(value=True),
            "bt_management_enabled": EntityState(value=True),
            "audio_format": EntityState(value="SBC"),
            "reanchor_count": EntityState(value=0),
            "last_sync_error_ms": EntityState(value=None),
            "reconnect_attempt": EntityState(value=0),
            "last_error": EntityState(value=None),
            "health_state": EntityState(value="streaming"),
            "keep_alive_method": EntityState(value="infrasound"),
            "power_save_delay_minutes": EntityState(value=1),
            "reanchoring": EntityState(value=False),
            "reconnecting": EntityState(value=False),
            "bt_standby": EntityState(value=False),
            "bt_power_save": EntityState(value=False),
        }
    }
    bridge = {
        "version": EntityState(value="2.65.0"),
        "ma_connected": EntityState(value=True),
        "startup_phase": EntityState(value="ready"),
        "runtime_mode": EntityState(value="production"),
        "update_available": EntityState(value=False, attrs={"installed_version": "2.65.0"}),
    }
    return HAStateProjection(
        devices=devices,
        bridge=bridge,
        availability_runtime={"player-aaa": True},
        availability_config={"player-aaa": True},
        bridge_available=True,
        device_meta={"player-aaa": meta},
        bridge_meta=bridge_meta,
    )


# ---------------------------------------------------------------------------
# resolve_mqtt_config
# ---------------------------------------------------------------------------


def test_resolve_mqtt_config_disabled_when_block_off():
    assert resolve_mqtt_config(None, bridge_id="x", bridge_name="x") is None
    assert resolve_mqtt_config({"enabled": False}, bridge_id="x", bridge_name="x") is None


def test_resolve_mqtt_config_disabled_when_mode_not_mqtt():
    block = {"enabled": True, "mode": "rest", "mqtt": {"broker": "host"}}
    assert resolve_mqtt_config(block, bridge_id="x", bridge_name="x") is None


def test_resolve_mqtt_config_with_explicit_host():
    block = {
        "enabled": True,
        "mode": "mqtt",
        "mqtt": {
            "broker": "192.168.1.10",
            "port": 1883,
            "username": "u",
            "password": "p",
        },
    }
    cfg = resolve_mqtt_config(block, bridge_id="haos", bridge_name="Bridge")
    assert cfg is not None
    assert cfg.host == "192.168.1.10"
    assert cfg.port == 1883
    assert cfg.username == "u"
    assert cfg.password == "p"
    assert cfg.client_id.startswith("sendspin_")


def test_resolve_mqtt_config_with_host_port_combined():
    block = {"enabled": True, "mode": "mqtt", "mqtt": {"broker": "broker.local:1234"}}
    cfg = resolve_mqtt_config(block, bridge_id="x", bridge_name="x")
    assert cfg.host == "broker.local"
    assert cfg.port == 1234


def test_resolve_mqtt_config_auto_uses_lookup():
    block = {"enabled": True, "mode": "mqtt", "mqtt": {"broker": "auto"}}
    cfg = resolve_mqtt_config(
        block,
        bridge_id="x",
        bridge_name="x",
        auto_lookup=lambda: {
            "host": "core-mosquitto",
            "port": 1883,
            "username": "addons",
            "password": "secret",
            "ssl": False,
        },
    )
    assert cfg is not None
    assert cfg.host == "core-mosquitto"
    assert cfg.username == "addons"


def test_resolve_mqtt_config_auto_falls_back_to_none_when_no_addon():
    block = {"enabled": True, "mode": "mqtt", "mqtt": {"broker": "auto"}}
    cfg = resolve_mqtt_config(block, bridge_id="x", bridge_name="x", auto_lookup=lambda: None)
    assert cfg is None


def test_resolve_mqtt_config_legacy_both_mode_normalised_to_mqtt():
    """``both`` was removed in v2.65.0-rc.3.  Saved configs from earlier
    rc builds must not silently disable MQTT publishing — treat the legacy
    value as ``mqtt`` so brokers keep working through the upgrade."""
    block = {"enabled": True, "mode": "both", "mqtt": {"broker": "host"}}
    cfg = resolve_mqtt_config(block, bridge_id="x", bridge_name="x")
    assert cfg is not None
    assert cfg.host == "host"


def test_resolve_mqtt_config_rest_mode_does_not_start_publisher():
    """Picking ``rest`` runs the REST/custom_component path only — no MQTT."""
    block = {"enabled": True, "mode": "rest", "mqtt": {"broker": "host"}}
    cfg = resolve_mqtt_config(block, bridge_id="x", bridge_name="x")
    assert cfg is None


# ---------------------------------------------------------------------------
# Discovery payloads
# ---------------------------------------------------------------------------


def test_discovery_topic_format(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    topics = [t for t, _ in payloads]
    # Every discovery topic begins with the prefix and ends with /config.
    for t in topics:
        assert t.startswith("homeassistant/")
        assert t.endswith("/config")


def test_discovery_payload_count_matches_catalog(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    expected = len(DEVICE_ENTITIES) + len(BRIDGE_ENTITIES)
    assert len(payloads) == expected


def test_discovery_no_media_player_payloads(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    components = {t.split("/")[1] for t, _ in payloads}
    assert "media_player" not in components


def test_discovery_device_block_uses_bluetooth_connection(cfg, projection):
    """Critical for MA-merge: every per-device entity declares a
    ``connections=[("bluetooth", mac)]`` block so HA fuses our device card
    with MA's existing media_player.<name> card."""
    payloads = build_discovery_payloads(cfg, projection)
    device_payloads = [p for t, p in payloads if "/sendspin_player-aaa_" in t]
    assert device_payloads
    for payload in device_payloads:
        device = payload["device"]
        # Buttons share the same device block as everything else.
        assert "connections" in device
        assert ["bluetooth", "fc:58:fa:eb:08:6c"] in device["connections"]
        assert device["identifiers"] == ["sendspin_player-aaa"]
        assert device["via_device"] == "sendspin_bridge_haos"


def test_discovery_bridge_block_distinct_identifiers(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    bridge_payloads = [p for t, p in payloads if "/sendspin_bridge_haos_" in t]
    assert bridge_payloads
    for payload in bridge_payloads:
        device = payload["device"]
        assert device["identifiers"] == ["sendspin_bridge_haos"]
        # Bridge device card has no Bluetooth connection — it's a software device.
        assert "connections" not in device or device["connections"] != [["bluetooth", "fc:58:fa:eb:08:6c"]]


def test_discovery_button_payloads_have_command_topic_no_state(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    for spec in DEVICE_ENTITIES:
        if spec.kind is EntityKind.BUTTON:
            uid = f"sendspin_player-aaa_{spec.object_id}"
            # Match the exact config topic to avoid prefix collisions
            # (e.g. ``reconnect`` substring would also match ``reconnecting``).
            target_topic = f"homeassistant/button/{uid}/config"
            payload = next(p for t, p in payloads if t == target_topic)
            assert "command_topic" in payload
            assert payload["command_topic"] == f"sendspin/player-aaa/cmd/{spec.command}"
            assert payload["payload_press"] == "PRESS"
            assert "state_topic" not in payload


def test_discovery_select_options_include_catalog_options(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    idle_payload = next(p for t, p in payloads if "_idle_mode/config" in t)
    assert idle_payload["options"] == ["default", "power_save", "auto_disconnect", "keep_alive"]
    assert idle_payload["command_topic"] == "sendspin/player-aaa/cmd/set_idle_mode"


def test_discovery_number_carries_min_max_step(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    delay_payload = next(p for t, p in payloads if "_static_delay_ms/config" in t)
    assert delay_payload["min"] == 0
    assert delay_payload["max"] == 5000
    assert delay_payload["step"] == 10


def test_discovery_state_topic_consolidated_per_device(cfg, projection):
    """All sensor / select / number / switch state lives under one JSON
    topic per device — value_template plucks the right field.  Reduces
    retained-message count and broker write churn."""
    payloads = build_discovery_payloads(cfg, projection)
    sensor_payload = next(p for t, p in payloads if "_rssi_dbm/config" in t)
    assert sensor_payload["state_topic"] == "sendspin/player-aaa/state"
    assert sensor_payload["value_template"] == "{{ value_json.rssi_dbm }}"


def test_discovery_availability_topic_routes_by_class(cfg, projection):
    """Each entity points at the availability topic appropriate to its
    availability_class:

    - ``runtime`` (RSSI, audio_streaming) → ``.../availability/runtime``
    - ``config`` (enabled switch, command buttons) → ``.../availability/config``
    - ``cumulative`` (reanchor_count, last_error) → also ``.../availability/config``
    """
    payloads = build_discovery_payloads(cfg, projection)
    rssi_payload = next(p for t, p in payloads if "_rssi_dbm/config" in t)
    assert rssi_payload["availability_topic"] == "sendspin/player-aaa/availability/runtime"
    enabled_payload = next(p for t, p in payloads if "_enabled/config" in t)
    assert enabled_payload["availability_topic"] == "sendspin/player-aaa/availability/config"
    reanchor_payload = next(p for t, p in payloads if "_reanchor_count/config" in t)
    assert reanchor_payload["availability_topic"] == "sendspin/player-aaa/availability/config"
    reconnect_payload = next(p for t, p in payloads if "_reconnect/config" in t)
    assert reconnect_payload["availability_topic"] == "sendspin/player-aaa/availability/config"


def test_discovery_room_name_becomes_suggested_area(cfg, projection):
    payloads = build_discovery_payloads(cfg, projection)
    sensor_payload = next(p for t, p in payloads if "_rssi_dbm/config" in t)
    assert sensor_payload["device"]["suggested_area"] == "Kitchen"


# ---------------------------------------------------------------------------
# State payloads
# ---------------------------------------------------------------------------


def test_device_state_payload_normalises_booleans_to_on_off(projection):
    payload = build_device_state_payload(projection, "player-aaa")
    assert payload["bluetooth_connected"] == "ON"
    assert payload["audio_streaming"] == "ON"
    assert payload["reanchoring"] == "OFF"
    assert payload["enabled"] == "ON"
    # Non-boolean values pass through unchanged.
    assert payload["rssi_dbm"] == -55
    assert payload["idle_mode"] == "default"


def test_bridge_state_payload_handles_update_entity(projection):
    payload = build_bridge_state_payload(projection)
    assert "update_available_state" in payload
    assert payload["update_available_state"] in ("ON", "OFF")
    assert "update_available_attrs" in payload


# ---------------------------------------------------------------------------
# Publisher lifecycle (no real broker)
# ---------------------------------------------------------------------------


def test_publisher_status_when_none():
    s = publisher_status(None)
    assert s["running"] is False
    assert s["broker"] is None


def test_publisher_status_reflects_state(projection):
    p = HaMqttPublisher(
        config_provider=lambda: None,
        projection_provider=lambda: projection,
        dispatcher=MagicMock(),
        event_subscribe=lambda cb: lambda: None,
    )
    p.state = "connected"
    p.connected_broker = "broker.local:1883"
    p.discovery_payload_count = 42
    s = publisher_status(p)
    assert s["running"] is True
    assert s["broker"] == "broker.local:1883"
    assert s["discovery_payload_count"] == 42


@pytest.mark.asyncio
async def test_publisher_run_disabled_when_no_config(projection):
    """When config_provider returns None, the publisher must idle without
    crashing — required because the orchestrator may instantiate the
    publisher before any HA_INTEGRATION block exists."""
    p = HaMqttPublisher(
        config_provider=lambda: None,
        projection_provider=lambda: projection,
        dispatcher=MagicMock(),
        event_subscribe=lambda cb: lambda: None,
    )
    p.start()
    await asyncio.sleep(0.05)
    assert p.state in ("disabled", "idle")
    await p.stop()
    assert p.state == "stopped"


def test_handle_command_routes_device(cfg, projection):
    dispatcher = MagicMock()
    p = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=dispatcher,
        event_subscribe=lambda cb: lambda: None,
    )

    msg = SimpleNamespace(
        topic="sendspin/player-aaa/cmd/set_idle_mode",
        payload=b'"power_save"',
    )
    p._handle_command(msg, cfg)
    dispatcher.dispatch_device.assert_called_once_with("player-aaa", "set_idle_mode", "power_save")


def test_handle_command_routes_bridge(cfg, projection):
    dispatcher = MagicMock()
    p = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=dispatcher,
        event_subscribe=lambda cb: lambda: None,
    )

    msg = SimpleNamespace(topic="sendspin/bridge/cmd/restart", payload=b"PRESS")
    p._handle_command(msg, cfg)
    dispatcher.dispatch_bridge.assert_called_once_with("restart", "PRESS")


def test_handle_command_ignores_malformed_topic(cfg, projection):
    dispatcher = MagicMock()
    p = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=dispatcher,
        event_subscribe=lambda cb: lambda: None,
    )
    msg = SimpleNamespace(topic="garbage/topic", payload=b"x")
    p._handle_command(msg, cfg)
    dispatcher.dispatch_device.assert_not_called()
    dispatcher.dispatch_bridge.assert_not_called()


def test_handle_command_accepts_bare_string_payload(cfg, projection):
    dispatcher = MagicMock()
    p = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=dispatcher,
        event_subscribe=lambda cb: lambda: None,
    )
    msg = SimpleNamespace(topic="sendspin/player-aaa/cmd/set_enabled", payload=b"ON")
    p._handle_command(msg, cfg)
    dispatcher.dispatch_device.assert_called_once_with("player-aaa", "set_enabled", "ON")


def test_handle_command_accepts_json_number_payload(cfg, projection):
    dispatcher = MagicMock()
    p = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=dispatcher,
        event_subscribe=lambda cb: lambda: None,
    )
    msg = SimpleNamespace(topic="sendspin/player-aaa/cmd/set_static_delay_ms", payload=b"500")
    p._handle_command(msg, cfg)
    dispatcher.dispatch_device.assert_called_once_with("player-aaa", "set_static_delay_ms", 500)


# ---------------------------------------------------------------------------
# Topic helpers
# ---------------------------------------------------------------------------


def test_topic_helpers_use_consistent_root(cfg):
    assert cfg.state_topic_device("p1") == "sendspin/p1/state"
    assert cfg.availability_topic_device("p1") == "sendspin/p1/availability"
    assert cfg.cmd_topic_device("p1", "reconnect") == "sendspin/p1/cmd/reconnect"
    assert cfg.state_topic_bridge() == "sendspin/bridge/state"
    assert cfg.cmd_topic_bridge("scan") == "sendspin/bridge/cmd/scan"
    assert cfg.discovery_topic("sensor", "uid_x") == "homeassistant/sensor/uid_x/config"


# ---------------------------------------------------------------------------
# Legacy availability topic — backwards-compat for rc.1-rc.3 caches
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Captures ``await client.publish(topic, payload, qos=, retain=)`` calls."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def publish(self, topic, payload, qos=0, retain=False):
        # Normalise payload to a string so JSON / plain values compare cleanly.
        if isinstance(payload, bytes):
            payload = payload.decode()
        self.calls.append((topic, str(payload)))


def _topics_for(client: _RecordingClient) -> list[str]:
    return [t for t, _ in client.calls]


def test_full_state_publishes_legacy_availability_topic(cfg, projection):
    """Per Copilot review on PR #218: ``_publish_full_state`` must keep
    writing the legacy single-channel ``sendspin/<pid>/availability``
    topic so HA caches from rc.1–rc.3 stay in sync.  The legacy topic
    mirrors the runtime channel."""
    publisher = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=MagicMock(),
        event_subscribe=lambda cb: lambda: None,
    )
    client = _RecordingClient()
    asyncio.run(publisher._publish_full_state(client, cfg, projection))

    legacy_writes = [(t, p) for t, p in client.calls if t == cfg.availability_topic_device("player-aaa")]
    assert legacy_writes, "legacy availability topic was not published"
    assert legacy_writes[-1][1] == "online"  # mirrors runtime=True from the projection


def test_delta_runtime_change_mirrors_to_legacy_topic(cfg, projection):
    """Per Copilot review on PR #218: a runtime availability flip must
    also reach the legacy topic so HA caches that still subscribe to it
    receive the transition.  Without this mirror, an entity that went
    offline would stay stuck at the last retained ``online`` value."""
    from sendspin_bridge.services.ha.ha_state_projector import StateDelta

    publisher = HaMqttPublisher(
        config_provider=lambda: cfg,
        projection_provider=lambda: projection,
        dispatcher=MagicMock(),
        event_subscribe=lambda cb: lambda: None,
    )
    client = _RecordingClient()
    delta = StateDelta(
        devices={},
        bridge={},
        availability_runtime_changed={"player-aaa": False},
    )
    asyncio.run(publisher._publish_delta(client, cfg, projection, delta))

    legacy_writes = [(t, p) for t, p in client.calls if t == cfg.availability_topic_device("player-aaa")]
    assert legacy_writes, "legacy availability topic was not mirrored on delta"
    assert legacy_writes[-1][1] == "offline"  # mirrors the runtime flip
