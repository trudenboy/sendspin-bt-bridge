"""Tests for services/pairing_quiesce.py — pair-time peer quiesce context manager."""

from unittest.mock import MagicMock, patch

import pytest


class _FakeBluetoothManager:
    def __init__(
        self,
        *,
        mac_address: str,
        device_name: str,
        effective_adapter_mac: str,
        connected: bool = True,
        disconnect_raises: Exception | None = None,
    ):
        self.mac_address = mac_address
        self.device_name = device_name
        self.effective_adapter_mac = effective_adapter_mac
        self.connected = connected
        self._disconnect_raises = disconnect_raises
        self.cancel_reconnect = MagicMock()
        self.allow_reconnect = MagicMock()
        self.signal_standby_wake = MagicMock()
        self.disconnect_device = MagicMock(side_effect=self._maybe_raise)

    def _maybe_raise(self):
        if self._disconnect_raises is not None:
            raise self._disconnect_raises


class _FakeClient:
    def __init__(self, bt_manager: _FakeBluetoothManager):
        self.bt_manager = bt_manager


@pytest.fixture
def fake_clients_state(monkeypatch):
    """Replace ``state.clients`` / ``state.clients_lock`` with an in-test list."""
    import threading

    import state as _state

    fake_clients: list = []
    fake_lock = threading.Lock()
    monkeypatch.setattr(_state, "clients", fake_clients, raising=True)
    monkeypatch.setattr(_state, "clients_lock", fake_lock, raising=True)
    return fake_clients


def _make_client(
    mac: str,
    adapter: str,
    *,
    name: str | None = None,
    connected: bool = True,
    disconnect_raises: Exception | None = None,
) -> _FakeClient:
    mgr = _FakeBluetoothManager(
        mac_address=mac,
        device_name=name or f"Speaker-{mac[-2:]}",
        effective_adapter_mac=adapter,
        connected=connected,
        disconnect_raises=disconnect_raises,
    )
    return _FakeClient(mgr)


def test_quiesce_pauses_matching_adapter(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter_a = "AA:BB:CC:DD:EE:01"
    adapter_b = "AA:BB:CC:DD:EE:02"
    c1 = _make_client("11:11:11:11:11:11", adapter_a, name="A1")
    c2 = _make_client("22:22:22:22:22:22", adapter_a, name="A2")
    c3 = _make_client("33:33:33:33:33:33", adapter_b, name="B1")
    fake_clients_state.extend([c1, c2, c3])

    with (
        patch("services.pairing_quiesce.time.sleep") as sleep_mock,
        quiesce_adapter_peers(adapter_a) as paused,
    ):
        pass

    assert {c.bt_manager.mac_address for c in paused} == {
        c1.bt_manager.mac_address,
        c2.bt_manager.mac_address,
    }
    c1.bt_manager.cancel_reconnect.assert_called_once()
    c1.bt_manager.disconnect_device.assert_called_once()
    c2.bt_manager.cancel_reconnect.assert_called_once()
    c2.bt_manager.disconnect_device.assert_called_once()
    c3.bt_manager.cancel_reconnect.assert_not_called()
    c3.bt_manager.disconnect_device.assert_not_called()
    sleep_mock.assert_called_once()


def test_quiesce_excludes_target_mac(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    target = "99:99:99:99:99:99"
    peer = _make_client("11:11:11:11:11:11", adapter)
    self_device = _make_client(target, adapter)
    fake_clients_state.extend([peer, self_device])

    with (
        patch("services.pairing_quiesce.time.sleep"),
        quiesce_adapter_peers(adapter, exclude_mac=target) as paused,
    ):
        pass

    assert [c.bt_manager.mac_address for c in paused] == [peer.bt_manager.mac_address]
    self_device.bt_manager.cancel_reconnect.assert_not_called()
    self_device.bt_manager.disconnect_device.assert_not_called()


def test_quiesce_skips_disconnected_peer(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    live = _make_client("11:11:11:11:11:11", adapter, connected=True)
    dead = _make_client("22:22:22:22:22:22", adapter, connected=False)
    fake_clients_state.extend([live, dead])

    with (
        patch("services.pairing_quiesce.time.sleep"),
        quiesce_adapter_peers(adapter) as paused,
    ):
        pass

    assert [c.bt_manager.mac_address for c in paused] == [live.bt_manager.mac_address]
    dead.bt_manager.cancel_reconnect.assert_not_called()
    dead.bt_manager.disconnect_device.assert_not_called()


def test_quiesce_restores_on_exception(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    c1 = _make_client("11:11:11:11:11:11", adapter)
    c2 = _make_client("22:22:22:22:22:22", adapter)
    fake_clients_state.extend([c1, c2])

    class _Boom(RuntimeError):
        pass

    with (
        patch("services.pairing_quiesce.time.sleep"),
        pytest.raises(_Boom),
        quiesce_adapter_peers(adapter),
    ):
        raise _Boom("pair failed")

    for client in (c1, c2):
        client.bt_manager.allow_reconnect.assert_called_once()
        client.bt_manager.signal_standby_wake.assert_called_once()


def test_quiesce_restore_order_reversed(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    c1 = _make_client("11:11:11:11:11:11", adapter, name="first")
    c2 = _make_client("22:22:22:22:22:22", adapter, name="second")
    c3 = _make_client("33:33:33:33:33:33", adapter, name="third")
    fake_clients_state.extend([c1, c2, c3])

    order: list[str] = []
    for client in (c1, c2, c3):
        client.bt_manager.allow_reconnect.side_effect = lambda name=client.bt_manager.device_name: order.append(name)

    with (
        patch("services.pairing_quiesce.time.sleep"),
        quiesce_adapter_peers(adapter),
    ):
        pass

    assert order == ["third", "second", "first"]


def test_quiesce_settle_sleep_only_when_paused(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    # No peers on this adapter.
    other = _make_client("11:11:11:11:11:11", "FF:FF:FF:FF:FF:FF")
    fake_clients_state.append(other)

    with (
        patch("services.pairing_quiesce.time.sleep") as sleep_mock,
        quiesce_adapter_peers(adapter) as paused,
    ):
        pass

    assert paused == []
    sleep_mock.assert_not_called()


def test_quiesce_swallows_peer_disconnect_exception(fake_clients_state):
    from services.pairing_quiesce import quiesce_adapter_peers

    adapter = "AA:BB:CC:DD:EE:01"
    bad = _make_client(
        "11:11:11:11:11:11",
        adapter,
        disconnect_raises=RuntimeError("dbus failure"),
    )
    good = _make_client("22:22:22:22:22:22", adapter)
    fake_clients_state.extend([bad, good])

    with (
        patch("services.pairing_quiesce.time.sleep"),
        quiesce_adapter_peers(adapter) as paused,
    ):
        pass

    # Good peer should still have been paused, and its restore still ran.
    assert good in paused
    good.bt_manager.allow_reconnect.assert_called_once()
    good.bt_manager.signal_standby_wake.assert_called_once()
    # Bad peer never made it into the paused list, so no restore for it.
    assert bad not in paused
    bad.bt_manager.allow_reconnect.assert_not_called()
