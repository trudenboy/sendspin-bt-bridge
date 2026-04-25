from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.device_activation import (
    ActivationResult,
    DeviceActivationContext,
    activate_device,
)


def _fake_client(player_name: str, *args, **kwargs) -> SimpleNamespace:
    """Factory stub mimicking SendspinClient's ctor keyword surface."""
    status: dict[str, object] = {}

    def update_status(delta: dict[str, object]) -> None:
        status.update(delta)

    client = SimpleNamespace(
        player_name=player_name,
        listen_port=kwargs.get("listen_port"),
        preferred_format=kwargs.get("preferred_format"),
        bt_manager=None,
        bluetooth_sink_name=None,
        status=status,
        _sink_monitor=None,
        _update_status=update_status,
        set_bt_management_enabled=MagicMock(),
        _on_sink_active=lambda: None,
        _on_sink_idle=lambda: None,
    )
    return client


def _fake_bt_manager_factory(bt_available: bool = True):
    captured: dict[str, object] = {}

    def factory(mac: str, **kwargs) -> SimpleNamespace:
        captured.update(kwargs)
        captured["mac"] = mac
        mgr = SimpleNamespace(
            mac_address=mac,
            check_bluetooth_available=lambda: bt_available,
            on_sink_found=kwargs.get("on_sink_found"),
        )
        return mgr

    return factory, captured


def _make_context(
    *,
    bt_available: bool = True,
    load_saved_volume_fn=None,
    persist_enabled_fn=None,
    base_listen_port: int = 8928,
    effective_bridge: str = "",
    enable_rssi_badge: bool = False,
) -> tuple[DeviceActivationContext, dict[str, object]]:
    bt_factory, captured = _fake_bt_manager_factory(bt_available=bt_available)
    ctx = DeviceActivationContext(
        server_host="auto",
        server_port=9000,
        effective_bridge=effective_bridge,
        prefer_sbc=True,
        bt_check_interval=15,
        bt_max_reconnect_fails=10,
        bt_churn_threshold=0,
        bt_churn_window=300.0,
        enable_a2dp_sink_recovery_dance=False,
        enable_pa_module_reload=False,
        enable_adapter_auto_recovery=False,
        base_listen_port=base_listen_port,
        client_factory=_fake_client,
        bt_manager_factory=bt_factory,
        load_saved_volume_fn=load_saved_volume_fn,
        persist_enabled_fn=persist_enabled_fn,
        enable_rssi_badge=enable_rssi_badge,
    )
    return ctx, captured


def test_activate_device_wires_bt_manager_and_sink_monitor_callback():
    ctx, captured = _make_context()
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Kitchen"}

    result = activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    assert isinstance(result, ActivationResult)
    assert result.bt_manager is not None
    assert result.bt_available is True
    assert result.client.bt_manager is result.bt_manager
    assert result.client.status["bluetooth_available"] is True

    # _on_sink_found closure should register the sink name + call sink_monitor
    sink_monitor = MagicMock()
    result.client._sink_monitor = sink_monitor
    on_sink_found = captured["on_sink_found"]
    on_sink_found("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink")
    assert result.client.bluetooth_sink_name == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    sink_monitor.register.assert_called_once()


def test_activate_device_without_mac_skips_bt_manager():
    ctx, _ = _make_context()
    device = {"mac": "", "player_name": "Silent"}

    result = activate_device(device, index=2, context=ctx, default_player_name="Fallback")

    assert result.bt_manager is None
    assert result.bt_available is False
    assert result.client.bt_manager is None
    assert result.listen_port == 8928 + 2  # base + index fallback


def test_activate_device_restores_saved_volume_when_load_fn_provided():
    load_fn = MagicMock(return_value=42)
    ctx, _ = _make_context(load_saved_volume_fn=load_fn)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Kitchen"}

    result = activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    load_fn.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    assert result.client.status.get("volume") == 42


def test_activate_device_degrades_when_bt_adapter_unavailable():
    ctx, _ = _make_context(bt_available=False)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Unreachable"}

    result = activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    assert result.bt_manager is not None
    assert result.bt_available is False
    assert result.client.status["bluetooth_available"] is False


def test_activate_device_respects_explicit_listen_port():
    ctx, _ = _make_context(base_listen_port=8928)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "listen_port": 9999}

    result = activate_device(device, index=5, context=ctx, default_player_name="Fallback")

    assert result.listen_port == 9999  # explicit wins over base + index


def test_activate_device_falls_back_to_base_port_plus_index():
    ctx, _ = _make_context(base_listen_port=8928)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0"}

    result = activate_device(device, index=3, context=ctx, default_player_name="Fallback")

    assert result.listen_port == 8928 + 3


def test_activate_device_honours_effective_bridge_suffix():
    ctx, captured = _make_context(effective_bridge="Home")
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Kitchen"}

    activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    # Verify the effective_bridge suffix is applied to device_name passed into
    # the BT manager factory (so the BT manager logs and sink lookups stay
    # consistent with what the client uses as its player_name).
    assert captured["device_name"] == "Kitchen @ Home"


def test_activate_device_restores_released_state_when_flagged():
    ctx, _ = _make_context()
    device = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "adapter": "hci0",
        "player_name": "Shelf",
        "released": True,
    }

    result = activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    result.client.set_bt_management_enabled.assert_called_once_with(False)


def test_activate_device_clamps_tiny_keepalive_interval_up_to_30():
    ctx, _ = _make_context()
    captured_client_kwargs: dict[str, object] = {}

    def recording_factory(player_name: str, *args, **kwargs) -> SimpleNamespace:
        captured_client_kwargs.update(kwargs)
        return _fake_client(player_name, *args, **kwargs)

    ctx = DeviceActivationContext(**{**ctx.__dict__, "client_factory": recording_factory})
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "keepalive_interval": 5}

    activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    assert captured_client_kwargs["keepalive_enabled"] is True
    assert captured_client_kwargs["keepalive_interval"] == 30


def test_activate_device_leaves_rssi_callback_unset_when_flag_off():
    """``EXPERIMENTAL_RSSI_BADGE`` defaults to False — the BT manager
    must not receive an ``on_rssi_update`` callback in that case so
    the periodic refresh tick short-circuits before it ever touches
    the BT operation lock or the kernel mgmt socket.  Pinning this
    keeps the no-op default-off path truly free of overhead."""
    ctx, captured = _make_context(enable_rssi_badge=False)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Kitchen"}

    activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    assert captured.get("on_rssi_update") is None


def test_activate_device_wires_rssi_callback_when_flag_on():
    """When the operator opts in, the callback must reach the BT
    manager so periodic refresh ticks can forward fresh RSSI into
    the client status pipeline."""
    ctx, captured = _make_context(enable_rssi_badge=True)
    device = {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0", "player_name": "Kitchen"}

    result = activate_device(device, index=0, context=ctx, default_player_name="Fallback")

    cb = captured.get("on_rssi_update")
    assert callable(cb)

    # And the callback writes through to the client's status dict
    # (rssi_dbm + rssi_at_ts) so SSE consumers see the value.
    cb(-55)
    assert result.client.status.get("rssi_dbm") == -55
    assert isinstance(result.client.status.get("rssi_at_ts"), float)


def test_activate_context_is_frozen():
    # @dataclass(frozen=True) guarantees the runtime-captured factories aren't
    # swapped out by a later caller (important because the same context is
    # shared across startup and all online-activation invocations).
    from dataclasses import FrozenInstanceError

    ctx, _ = _make_context()
    with pytest.raises(FrozenInstanceError):
        ctx.server_host = "tamper"  # type: ignore[misc]


# ── MPRIS export contract — per-device path + adapter resolution ────────


def test_mpris_object_path_is_per_device_unique_for_bluez_register_player():
    """v2.63.0-rc.6: each device must have a UNIQUE D-Bus object path
    because multiple speakers on the same adapter all register via
    ``org.bluez.Media1.RegisterPlayer(path, props)`` — one shared path
    would clash on the second registration.  MAC colons map to ``_``
    because D-Bus paths must be ``[A-Za-z0-9_/]``.

    Earlier rcs (1-5) tried the canonical ``/org/mpris/MediaPlayer2``
    path + a per-device well-known bus name, but BlueZ's AVRCP
    forwarder doesn't scan bus names — it only routes to paths handed
    to it via Media1.RegisterPlayer, and system-bus name requests are
    ACL-blocked by default anyway.  See CHANGELOG rc.6 entry.
    """
    from services.device_activation import _mpris_dbus_path

    a = _mpris_dbus_path("AA:BB:CC:DD:EE:FF")
    b = _mpris_dbus_path("11:22:33:44:55:66")
    assert a != b, "MAC-derived path must differ per device"
    assert a.startswith("/org/sendspin/players/")
    assert ":" not in a  # D-Bus path constraint
    assert a.endswith("AA_BB_CC_DD_EE_FF")


def test_bluez_adapter_path_returns_org_bluez_hci_form():
    """``Media1.RegisterPlayer`` must be called on the adapter where the
    device is connected, not arbitrarily on hci0.  The helper resolves
    ``BluetoothManager.adapter_hci_name`` (sysfs-derived at startup)
    into the BlueZ object-path form."""
    from types import SimpleNamespace

    from services.device_activation import _bluez_adapter_path

    assert _bluez_adapter_path(SimpleNamespace(adapter_hci_name="hci0")) == "/org/bluez/hci0"
    assert _bluez_adapter_path(SimpleNamespace(adapter_hci_name="hci1")) == "/org/bluez/hci1"
    assert _bluez_adapter_path(SimpleNamespace(adapter_hci_name="")) is None
    assert _bluez_adapter_path(SimpleNamespace(adapter_hci_name="bogus")) is None
