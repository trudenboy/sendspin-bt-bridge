"""Tests for backend_info and player_state enrichment in status snapshots."""

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import state
from services.player_model import Player, PlayerState
from services.status_snapshot import build_bridge_snapshot, build_device_snapshot

UTC = timezone.utc


def _make_client(
    *,
    player_name="Kitchen",
    player_id="sendspin-kitchen",
    volume=55,
    audio_backend=None,
):
    """Minimal client stub with optional audio_backend in snapshot()."""
    snap = {
        "status": {
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "volume": volume,
            "uptime_start": datetime.now(tz=UTC),
        },
        "bluetooth_sink_name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        "bt_management_enabled": True,
        "connected_server_url": "",
        "is_running": True,
        "player_name": player_name,
        "player_id": player_id,
        "listen_port": 8928,
        "server_host": "music-assistant.local",
        "server_port": 9000,
        "static_delay_ms": -500.0,
        "bt_manager": SimpleNamespace(
            mac_address="AA:BB:CC:DD:EE:FF",
            effective_adapter_mac="11:22:33:44:55:66",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=88,
        ),
        "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
        "effective_adapter_mac": "11:22:33:44:55:66",
        "adapter": "hci0",
        "adapter_hci_name": "hci0",
        "battery_level": 88,
        "paired": True,
        "max_reconnect_fails": 5,
        "audio_backend": audio_backend,
        "audio_destination": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
    }
    return SimpleNamespace(
        status=snap["status"],
        _status_lock=threading.Lock(),
        player_name=player_name,
        player_id=player_id,
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=snap["bt_manager"],
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
        backend_status=audio_backend,
        snapshot=lambda: snap,
    )


def _register_player(orch, player_id, player_name="Kitchen"):
    """Register a mock player in the orchestrator and return it."""
    player = Player(id=player_id, player_name=player_name)
    orch.register_player(player, backend_type_override="mock")
    return player


# --- backend_info tests ---


def test_device_snapshot_has_backend_info():
    """When client has audio_backend in snapshot, backend_info is populated."""
    backend_dict = {"type": "pulse", "sink": "bluez_sink.AA_BB", "connected": True}
    client = _make_client(audio_backend=backend_dict)

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert snapshot.backend_info == backend_dict
    assert data["backend_info"] == backend_dict


def test_device_snapshot_backend_info_none():
    """When no backend info, backend_info is None."""
    client = _make_client(audio_backend=None)

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert snapshot.backend_info is None
    assert data.get("backend_info") is None


# --- player_state tests ---


def test_device_snapshot_has_player_state():
    """When player is registered in orchestrator, player_state is set."""
    client = _make_client()
    orch = state.get_backend_orchestrator()
    _register_player(orch, client.player_id, client.player_name)
    orch.set_player_state(client.player_id, PlayerState.READY)
    try:
        snapshot = build_device_snapshot(client)
        data = snapshot.to_dict()

        assert snapshot.player_state == "ready"
        assert data["player_state"] == "ready"
    finally:
        orch.unregister_player(client.player_id)


def test_device_snapshot_player_state_none():
    """When player is not in orchestrator, player_state is None."""
    client = _make_client(player_id="sendspin-unknown-device")

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert snapshot.player_state is None
    assert data.get("player_state") is None


# --- orchestrator_summary tests ---


def test_bridge_snapshot_has_orchestrator_summary():
    """Bridge snapshot summary includes player_count and connected_count."""
    client = _make_client()
    orch = state.get_backend_orchestrator()
    _register_player(orch, client.player_id, client.player_name)
    orch.set_player_state(client.player_id, PlayerState.READY)
    try:
        snapshot = build_bridge_snapshot([client])
        payload = snapshot.to_status_payload()

        assert snapshot.orchestrator_summary is not None
        assert snapshot.orchestrator_summary["player_count"] >= 1
        assert snapshot.orchestrator_summary["connected_count"] >= 1
        assert client.player_id in snapshot.orchestrator_summary["states"]
        assert snapshot.orchestrator_summary["states"][client.player_id] == "ready"
        assert "orchestrator_summary" in payload
    finally:
        orch.unregister_player(client.player_id)


def test_bridge_snapshot_orchestrator_summary_empty():
    """When no players registered, orchestrator summary counts are 0."""
    orch = state.get_backend_orchestrator()
    # Snapshot the current state so we can assert relative to it.
    pre_count = orch.player_count
    snapshot = build_bridge_snapshot([])
    payload = snapshot.to_status_payload()

    assert snapshot.orchestrator_summary is not None
    assert snapshot.orchestrator_summary["player_count"] == pre_count
    assert isinstance(snapshot.orchestrator_summary["connected_count"], int)
    assert isinstance(snapshot.orchestrator_summary["states"], dict)
    assert "orchestrator_summary" in payload
