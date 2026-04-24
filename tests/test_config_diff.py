from __future__ import annotations

from services.config_diff import ActionKind, diff_configs


def _device(mac: str, **overrides):
    base = {
        "mac": mac,
        "player_name": f"Player {mac[-5:]}",
        "adapter": "",
        "listen_port": 8928,
        "static_delay_ms": 0,
        "enabled": True,
        "idle_mode": "default",
        "keepalive_interval": 30,
        "idle_disconnect_minutes": 0,
        "power_save_delay_minutes": 1,
    }
    base.update(overrides)
    return base


def _config(devices=None, **globals_):
    cfg = {
        "CONFIG_SCHEMA_VERSION": 1,
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "BRIDGE_NAME": "bridge-a",
        "PULSE_LATENCY_MSEC": 600,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": True,
        "LOG_LEVEL": "INFO",
        "BLUETOOTH_DEVICES": devices or [],
    }
    cfg.update(globals_)
    return cfg


# ---------------------------------------------------------------------------
# No-op
# ---------------------------------------------------------------------------


def test_diff_no_changes_returns_empty():
    cfg = _config([_device("AA:BB:CC:DD:EE:FF")])
    assert diff_configs(cfg, cfg) == []


def test_diff_ignores_runtime_state_keys():
    old = _config([_device("AA:BB:CC:DD:EE:FF")], LAST_VOLUMES={"AA:BB:CC:DD:EE:FF": 60})
    new = _config([_device("AA:BB:CC:DD:EE:FF")], LAST_VOLUMES={"AA:BB:CC:DD:EE:FF": 80})
    assert diff_configs(old, new) == []


# ---------------------------------------------------------------------------
# HOT_APPLY
# ---------------------------------------------------------------------------


def test_static_delay_only_produces_hot_apply():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, static_delay_ms=0)])
    new = _config([_device(mac, static_delay_ms=400)])

    actions = diff_configs(old, new)

    assert len(actions) == 1
    (act,) = actions
    assert act.kind is ActionKind.HOT_APPLY
    assert act.mac == mac
    assert act.fields == ["static_delay_ms"]
    assert act.payload == {"static_delay_ms": 400}


def test_multiple_hot_fields_collapse_into_single_action():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, idle_mode="default", static_delay_ms=0)])
    new = _config([_device(mac, idle_mode="power_save", static_delay_ms=250, power_save_delay_minutes=2)])

    actions = diff_configs(old, new)

    assert len(actions) == 1
    (act,) = actions
    assert act.kind is ActionKind.HOT_APPLY
    assert set(act.fields) == {"idle_mode", "static_delay_ms", "power_save_delay_minutes"}
    assert act.payload["idle_mode"] == "power_save"
    assert act.payload["static_delay_ms"] == 250
    assert act.payload["power_save_delay_minutes"] == 2


def test_room_id_change_is_hot_apply():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, room_id="")])
    new = _config([_device(mac, room_id="bedroom")])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.HOT_APPLY
    assert act.fields == ["room_id"]


# ---------------------------------------------------------------------------
# WARM_RESTART
# ---------------------------------------------------------------------------


def test_listen_port_triggers_warm_restart():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, listen_port=8928)])
    new = _config([_device(mac, listen_port=8930)])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.WARM_RESTART
    assert act.mac == mac
    assert act.fields == ["listen_port"]
    assert act.payload["device"]["listen_port"] == 8930


def test_warm_restart_supersedes_hot_apply_for_same_device():
    """Changing a warm field and a hot field together → one WARM_RESTART."""
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, listen_port=8928, static_delay_ms=0)])
    new = _config([_device(mac, listen_port=8930, static_delay_ms=500)])

    actions = diff_configs(old, new)
    assert len(actions) == 1
    (act,) = actions
    assert act.kind is ActionKind.WARM_RESTART
    assert "listen_port" in act.fields
    # static_delay_ms still picked up by the restart because the new device
    # dict is passed through the payload.
    assert act.payload["device"]["static_delay_ms"] == 500


def test_player_name_rename_triggers_warm_restart():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, player_name="Kitchen")])
    new = _config([_device(mac, player_name="Kitchen Pro")])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.WARM_RESTART
    assert act.fields == ["player_name"]


def test_adapter_change_emits_bt_remove_before_warm_restart():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, adapter="hci0")])
    new = _config([_device(mac, adapter="hci1")])

    bt_remove, warm = diff_configs(old, new)
    assert bt_remove.kind is ActionKind.BT_REMOVE
    assert bt_remove.mac == mac
    assert bt_remove.payload["old_adapter"] == "hci0"
    assert warm.kind is ActionKind.WARM_RESTART
    assert "adapter" in warm.fields


# ---------------------------------------------------------------------------
# Enable / disable / add / remove
# ---------------------------------------------------------------------------


def test_enable_to_disable_emits_stop_client():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, enabled=True)])
    new = _config([_device(mac, enabled=False)])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.STOP_CLIENT
    assert act.mac == mac


def test_disable_to_enable_emits_start_client():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, enabled=False)])
    new = _config([_device(mac, enabled=True)])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.START_CLIENT
    assert act.mac == mac


def test_device_removed_emits_stop_client():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac)])
    new = _config([])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.STOP_CLIENT
    assert act.mac == mac


def test_device_added_emits_start_client():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([])
    new = _config([_device(mac)])

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.START_CLIENT
    assert act.mac == mac
    # device_index must be present in the payload so the online-activation
    # path can use the same base_listen_port+index fallback as startup
    # (otherwise adding a device behind disabled entries in the config can
    # land on a wrong port).
    assert act.payload.get("device_index") == 0


def test_start_client_device_index_reflects_bluetooth_devices_position():
    # Three devices in config: first two disabled, third enabled-and-new.
    # The new device is at BLUETOOTH_DEVICES index 2 — the START_CLIENT
    # action payload must carry that, not the length of ``active_clients``.
    mac_new = "CC:CC:CC:CC:CC:03"
    old = _config(
        [
            _device("AA:AA:AA:AA:AA:01", enabled=False),
            _device("BB:BB:BB:BB:BB:02", enabled=False),
        ]
    )
    new = _config(
        [
            _device("AA:AA:AA:AA:AA:01", enabled=False),
            _device("BB:BB:BB:BB:BB:02", enabled=False),
            _device(mac_new),
        ]
    )

    actions = diff_configs(old, new)
    start_actions = [a for a in actions if a.kind is ActionKind.START_CLIENT]
    assert len(start_actions) == 1
    assert start_actions[0].mac == mac_new
    assert start_actions[0].payload.get("device_index") == 2


def test_both_sides_disabled_no_action():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, enabled=False, static_delay_ms=0)])
    new = _config([_device(mac, enabled=False, static_delay_ms=400)])

    assert diff_configs(old, new) == []


# ---------------------------------------------------------------------------
# GLOBAL_BROADCAST
# ---------------------------------------------------------------------------


def test_log_level_change_is_global_broadcast():
    old = _config(LOG_LEVEL="INFO")
    new = _config(LOG_LEVEL="DEBUG")

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_BROADCAST
    assert act.mac is None
    assert act.fields == ["LOG_LEVEL"]
    assert act.payload == {"LOG_LEVEL": "DEBUG"}


def test_volume_via_ma_change_is_global_broadcast():
    old = _config(VOLUME_VIA_MA=True)
    new = _config(VOLUME_VIA_MA=False)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_BROADCAST
    assert act.fields == ["VOLUME_VIA_MA"]


def test_ma_url_change_is_global_broadcast():
    old = _config(MA_API_URL="http://old.local:8095")
    new = _config(MA_API_URL="http://new.local:8095")

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_BROADCAST
    assert act.fields == ["MA_API_URL"]


# ---------------------------------------------------------------------------
# GLOBAL_RESTART
# ---------------------------------------------------------------------------


def test_sendspin_server_change_is_global_restart():
    old = _config(SENDSPIN_SERVER="auto")
    new = _config(SENDSPIN_SERVER="192.168.10.99")

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_RESTART
    assert act.fields == ["SENDSPIN_SERVER"]


def test_bridge_name_change_is_global_restart():
    old = _config(BRIDGE_NAME="bridge-a")
    new = _config(BRIDGE_NAME="bridge-b")

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_RESTART


def test_pulse_latency_change_is_global_restart():
    old = _config(PULSE_LATENCY_MSEC=600)
    new = _config(PULSE_LATENCY_MSEC=800)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_RESTART


def test_prefer_sbc_change_is_global_restart():
    old = _config(PREFER_SBC_CODEC=False)
    new = _config(PREFER_SBC_CODEC=True)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.GLOBAL_RESTART
    assert act.fields == ["PREFER_SBC_CODEC"]


# ---------------------------------------------------------------------------
# RESTART_REQUIRED
# ---------------------------------------------------------------------------


def test_web_port_change_is_restart_required():
    old = _config(WEB_PORT=8080)
    new = _config(WEB_PORT=9090)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.RESTART_REQUIRED
    assert act.fields == ["WEB_PORT"]


def test_auth_enabled_flip_is_restart_required():
    old = _config(AUTH_ENABLED=False)
    new = _config(AUTH_ENABLED=True)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.RESTART_REQUIRED


def test_experimental_a2dp_dance_flip_is_restart_required():
    """BluetoothManager reads EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE in __init__ — no hot-reload path."""
    old = _config(EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE=False)
    new = _config(EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE=True)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.RESTART_REQUIRED
    assert act.fields == ["EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE"]


def test_experimental_pa_module_reload_flip_is_restart_required():
    """EXPERIMENTAL_PA_MODULE_RELOAD is parent-state, plumbed per-instance — restart needed."""
    old = _config(EXPERIMENTAL_PA_MODULE_RELOAD=False)
    new = _config(EXPERIMENTAL_PA_MODULE_RELOAD=True)

    (act,) = diff_configs(old, new)
    assert act.kind is ActionKind.RESTART_REQUIRED
    assert act.fields == ["EXPERIMENTAL_PA_MODULE_RELOAD"]


def test_experimental_pair_just_works_flip_needs_no_action():
    """EXPERIMENTAL_PAIR_JUST_WORKS is read via load_config() each time
    _run_standalone_pair_inner runs, so a toggle takes effect on the next
    pair attempt with no restart or subprocess reload (issue #168).
    """
    old = _config(EXPERIMENTAL_PAIR_JUST_WORKS=False)
    new = _config(EXPERIMENTAL_PAIR_JUST_WORKS=True)

    assert diff_configs(old, new) == []


# ---------------------------------------------------------------------------
# Ordering guarantees
# ---------------------------------------------------------------------------


def test_bt_remove_precedes_warm_restart_for_same_mac():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, adapter="hci0", listen_port=8928)])
    new = _config([_device(mac, adapter="hci1", listen_port=8930)])

    actions = diff_configs(old, new)
    assert [a.kind for a in actions] == [ActionKind.BT_REMOVE, ActionKind.WARM_RESTART]


def test_device_actions_precede_global_actions():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, static_delay_ms=0)], LOG_LEVEL="INFO")
    new = _config([_device(mac, static_delay_ms=300)], LOG_LEVEL="DEBUG")

    actions = diff_configs(old, new)
    kinds = [a.kind for a in actions]
    assert kinds == [ActionKind.HOT_APPLY, ActionKind.GLOBAL_BROADCAST]


def test_multiple_devices_each_get_their_own_action():
    mac_a = "AA:BB:CC:DD:EE:01"
    mac_b = "AA:BB:CC:DD:EE:02"
    old = _config([_device(mac_a, static_delay_ms=0), _device(mac_b, listen_port=8928)])
    new = _config([_device(mac_a, static_delay_ms=400), _device(mac_b, listen_port=8930)])

    actions = diff_configs(old, new)
    assert len(actions) == 2
    by_mac = {a.mac: a for a in actions}
    assert by_mac[mac_a].kind is ActionKind.HOT_APPLY
    assert by_mac[mac_b].kind is ActionKind.WARM_RESTART


# ---------------------------------------------------------------------------
# Scalar normalisation
# ---------------------------------------------------------------------------


def test_none_to_empty_string_not_treated_as_change():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, adapter=None)])
    new = _config([_device(mac, adapter="")])

    assert diff_configs(old, new) == []


def test_whitespace_only_change_ignored():
    old = _config(BRIDGE_NAME="bridge-a")
    new = _config(BRIDGE_NAME="  bridge-a  ")

    assert diff_configs(old, new) == []


# ---------------------------------------------------------------------------
# Summary serialisation
# ---------------------------------------------------------------------------


def test_action_summary_is_json_friendly():
    mac = "AA:BB:CC:DD:EE:FF"
    old = _config([_device(mac, static_delay_ms=0)])
    new = _config([_device(mac, static_delay_ms=200)])

    (act,) = diff_configs(old, new)
    summary = act.to_summary()
    assert summary == {
        "kind": "hot_apply",
        "mac": mac,
        "label": act.label,
        "fields": ["static_delay_ms"],
    }
