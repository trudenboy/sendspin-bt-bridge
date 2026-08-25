"""The reconnect limit has to survive the trip into the normalised state.

`build_device_snapshot` records the configured limit as
`extra["max_reconnect_fails"]`; the normalised state read
`extra["bt_max_reconnect_fails"]`, a key nothing writes there.  The limit
therefore arrived as ``None``, and every screen that reads the normalised
state could only say "attempt 3 is in progress" where the raw extras would
have said "attempt 3/10, 7 remain".
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot

UTC = timezone.utc


def _client(*, reconnect_attempt: int, max_reconnect_fails: int) -> SimpleNamespace:
    return SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": False,
            "bluetooth_available": True,
            "playing": False,
            "volume": 50,
            "reconnect_attempt": reconnect_attempt,
            "uptime_start": datetime.now(tz=UTC),
        },
        _status_lock=threading.Lock(),
        player_name="Kitchen",
        player_id="sendspin-kitchen",
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=0.0,
        connected_server_url="",
        bt_manager=SimpleNamespace(
            mac_address="AA:BB:CC:DD:EE:FF",
            effective_adapter_mac="11:22:33:44:55:66",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=None,
            max_reconnect_fails=max_reconnect_fails,
        ),
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )


def test_the_snapshot_and_the_normalised_state_name_the_limit_the_same_way():
    snapshot = build_device_snapshot(_client(reconnect_attempt=3, max_reconnect_fails=10))

    bluetooth = build_normalized_device_state(snapshot).to_dict()["bluetooth"]

    assert bluetooth["max_reconnect_fails"] == 10
    assert bluetooth["reconnect_attempts_remaining"] == 7


def test_the_shared_sentence_keeps_the_limit_after_normalisation():
    """The phrasing every screen shares reads the normalised state first."""
    from sendspin_bridge.services.diagnostics.device_phrasing import reconnect_attempt_summary

    snapshot = build_device_snapshot(_client(reconnect_attempt=3, max_reconnect_fails=10))

    assert "3/10" in reconnect_attempt_summary(snapshot)
    assert "7 attempts remain" in reconnect_attempt_summary(snapshot)
