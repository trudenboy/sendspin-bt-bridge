"""Both ways of reading a client must describe it the same.

`build_device_snapshot` reads a client twice over: through `client.snapshot()`
when the client offers one — an atomic read under the status lock — and
through a pile of `getattr` calls when it does not. Two extractions of the
same twenty-one facts, side by side, with nothing checking that they agree.

The fallback is not dead code: it is what every partially-built client and
every test double takes.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot

UTC = timezone.utc


class _Client:
    """A client that can answer either way, on demand."""

    def __init__(self) -> None:
        self.status = {
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": True,
            "volume": 42,
            "muted": False,
            "reconnect_attempt": 2,
            "uptime_start": datetime.now(tz=UTC),
        }
        self._status_lock = threading.Lock()
        self.player_name = "Kitchen"
        self.player_id = "sendspin-kitchen"
        self.listen_port = 8928
        self.server_host = "music-assistant.local"
        self.server_port = 9000
        self.static_delay_ms = 120.0
        self.required_lead_time_ms = 250
        self.min_buffer_ms = 250
        self.connected_server_url = ""
        self.bluetooth_sink_name = "bluez_output.AA_BB_CC_DD_EE_FF.1"
        self.bt_management_enabled = True
        self.bt_manager = SimpleNamespace(
            mac_address="AA:BB:CC:DD:EE:FF",
            effective_adapter_mac="C0:FB:F9:62:D7:D6",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=77,
            paired=True,
            max_reconnect_fails=5,
        )

    def is_running(self) -> bool:
        return True

    def snapshot(self) -> dict:
        with self._status_lock:
            bt = self.bt_manager
            return {
                "status": self.status.copy(),
                "bluetooth_sink_name": self.bluetooth_sink_name,
                "bt_management_enabled": self.bt_management_enabled,
                "connected_server_url": self.connected_server_url,
                "is_running": True,
                "player_name": self.player_name,
                "player_id": self.player_id,
                "listen_port": self.listen_port,
                "server_host": self.server_host,
                "server_port": self.server_port,
                "static_delay_ms": self.static_delay_ms,
                "required_lead_time_ms": self.required_lead_time_ms,
                "min_buffer_ms": self.min_buffer_ms,
                "bt_manager": bt,
                "bluetooth_mac": bt.mac_address,
                "effective_adapter_mac": bt.effective_adapter_mac,
                "adapter": bt.adapter,
                "adapter_hci_name": bt.adapter_hci_name,
                "battery_level": bt.battery_level,
                "paired": bt.paired,
                "max_reconnect_fails": bt.max_reconnect_fails,
            }


def _without_snapshot(client: _Client) -> object:
    """The same facts on an object that cannot answer atomically."""
    plain = SimpleNamespace(
        status=client.status,
        _status_lock=client._status_lock,
        player_name=client.player_name,
        player_id=client.player_id,
        listen_port=client.listen_port,
        server_host=client.server_host,
        server_port=client.server_port,
        static_delay_ms=client.static_delay_ms,
        required_lead_time_ms=client.required_lead_time_ms,
        min_buffer_ms=client.min_buffer_ms,
        connected_server_url=client.connected_server_url,
        bluetooth_sink_name=client.bluetooth_sink_name,
        bt_management_enabled=client.bt_management_enabled,
        bt_manager=client.bt_manager,
        is_running=client.is_running,
    )
    return plain


def _volatile(payload: dict) -> dict:
    """Drop the fields that move on their own between two reads."""
    out = dict(payload)
    out.pop("uptime", None)
    extra = dict(out.get("extra") or {})
    extra.pop("uptime", None)
    out["extra"] = extra
    return out


def test_the_two_reads_of_one_client_describe_it_identically():
    client = _Client()
    atomic = build_device_snapshot(client, configured_enabled={})
    by_attribute = build_device_snapshot(_without_snapshot(_Client()), configured_enabled={})

    assert _volatile(atomic.to_dict()) == _volatile(by_attribute.to_dict())


def test_a_client_whose_atomic_read_is_not_callable_is_read_by_attribute():
    """`snapshot = None` on a class used to crash the whole status build.

    The old guard asked whether the *class* had the attribute, which a
    ``None`` placeholder satisfies, and then called it.
    """
    client = _Client()
    broken = SimpleNamespace(
        snapshot=None,
        status=client.status,
        _status_lock=client._status_lock,
        player_name=client.player_name,
        player_id=client.player_id,
        listen_port=client.listen_port,
        server_host=client.server_host,
        server_port=client.server_port,
        static_delay_ms=client.static_delay_ms,
        connected_server_url=client.connected_server_url,
        bluetooth_sink_name=client.bluetooth_sink_name,
        bt_management_enabled=client.bt_management_enabled,
        bt_manager=client.bt_manager,
        is_running=client.is_running,
    )

    snapshot = build_device_snapshot(broken, configured_enabled={})

    assert snapshot.player_name == "Kitchen"
    assert snapshot.sink_name == "bluez_output.AA_BB_CC_DD_EE_FF.1"


def test_a_client_that_answers_with_something_else_is_read_by_attribute():
    """A stub returning a Mock must not become the snapshot's facts."""
    client = _Client()
    odd = SimpleNamespace(
        snapshot=lambda: "not a mapping",
        status=client.status,
        _status_lock=client._status_lock,
        player_name=client.player_name,
        player_id=client.player_id,
        listen_port=client.listen_port,
        server_host=client.server_host,
        server_port=client.server_port,
        static_delay_ms=client.static_delay_ms,
        connected_server_url=client.connected_server_url,
        bluetooth_sink_name=client.bluetooth_sink_name,
        bt_management_enabled=client.bt_management_enabled,
        bt_manager=client.bt_manager,
        is_running=client.is_running,
    )

    snapshot = build_device_snapshot(odd, configured_enabled={})

    assert snapshot.player_name == "Kitchen"
