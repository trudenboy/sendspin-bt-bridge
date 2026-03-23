from __future__ import annotations

from types import SimpleNamespace

from services.operator_check_runner import run_safe_check


def test_run_safe_check_reports_preflight_failure(monkeypatch):
    import services.operator_check_runner as runner

    monkeypatch.setattr(
        runner,
        "collect_preflight_status",
        lambda: {
            "status": "degraded",
            "dbus": False,
            "collections_status": {"bluetooth": {"status": "error"}},
            "bluetooth": {"controller": False, "paired_devices": 0},
            "audio": {"sinks": 0},
        },
    )

    result = run_safe_check("runtime_access")

    assert result["status"] == "error"
    assert "D-Bus access is unavailable" in result["summary"]


def test_run_safe_check_revalidates_ma_groups(monkeypatch):
    import services.operator_check_runner as runner
    from services.device_registry import DeviceRegistrySnapshot

    captured = {}
    monkeypatch.setattr(
        runner,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[SimpleNamespace(player_name="Kitchen", player_id="player-1")]),
    )
    monkeypatch.setattr(
        runner,
        "build_device_snapshot",
        lambda client: SimpleNamespace(player_name="Kitchen", bluetooth_mac="AA:BB:CC:DD:EE:FF"),
    )

    async def _fake_discover(ma_url, ma_token, bridge_players):
        return (
            {"player-1": {"id": "syncgroup_1", "name": "Kitchen"}},
            [{"id": "syncgroup_1", "name": "Kitchen", "members": []}],
        )

    monkeypatch.setattr(runner, "discover_ma_groups", _fake_discover)
    monkeypatch.setattr(
        runner, "set_ma_groups", lambda mapping, all_groups: captured.update({"mapping": mapping, "groups": all_groups})
    )

    result = run_safe_check("ma_auth", config={"MA_API_URL": "http://ma.local", "MA_API_TOKEN": "token"})

    assert result["status"] == "ok"
    assert result["matched_players"] == 1
    assert captured["mapping"]["player-1"]["id"] == "syncgroup_1"


def test_run_safe_check_rechecks_connected_sinks(monkeypatch):
    import services.operator_check_runner as runner
    from services.device_registry import DeviceRegistrySnapshot

    client = SimpleNamespace(player_name="Kitchen")
    client.bt_manager = SimpleNamespace(configure_bluetooth_audio=lambda: True)
    snapshots = [
        SimpleNamespace(
            player_name="Kitchen", bluetooth_mac="AA:BB:CC:DD:EE:FF", bluetooth_connected=True, sink_name=None
        ),
        SimpleNamespace(
            player_name="Kitchen",
            bluetooth_mac="AA:BB:CC:DD:EE:FF",
            bluetooth_connected=True,
            sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        ),
    ]
    monkeypatch.setattr(
        runner,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[client]),
    )
    monkeypatch.setattr(runner, "build_device_snapshot", lambda current_client: snapshots.pop(0))

    result = run_safe_check("sink_verification", device_names=["Kitchen"])

    assert result["status"] == "ok"
    assert result["device_results"][0]["sink"] == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
