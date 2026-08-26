"""The normalised state must read facts that something actually produces.

The device snapshot and the normalised state meet through an untyped bag of
some sixty string keys: `DeviceSnapshot.extra`, which is the whole runtime
status dict plus a few dozen keys added by hand.  Nothing checks that a key
the reader asks for is a key the writer wrote, so a misspelling is not an
error — it is a `None` that travels all the way to the screen.

Twice now that has happened.  The reconnect limit arrived as `None` because
the reader asked for `bt_max_reconnect_fails` while the writer wrote
`max_reconnect_fails`.  And the audio block asks for `resolved_sink_name`,
which nothing has ever written, so every device in the state model reports a
sink name of `None` next to `has_sink: true`.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot

UTC = timezone.utc


def _client(*, sink_name: str = "bluez_output.6C_5C_3D_35_17_99.1") -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "volume": 50,
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
            mac_address="6C:5C:3D:35:17:99",
            effective_adapter_mac="C0:FB:F9:62:D7:D6",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=None,
            max_reconnect_fails=5,
        ),
        bluetooth_sink_name=sink_name,
        bt_management_enabled=True,
        is_running=lambda: True,
    )


def test_the_sink_a_device_is_playing_through_is_named_in_its_state():
    """`has_sink: true` beside `sink_name: null` is a contradiction on screen."""
    snapshot = build_device_snapshot(_client())

    audio = build_normalized_device_state(snapshot).to_dict()["audio"]

    assert audio["has_sink"] is True
    assert audio["sink_name"] == "bluez_output.6C_5C_3D_35_17_99.1"


def test_a_device_without_a_sink_still_says_so():
    snapshot = build_device_snapshot(_client(sink_name=""))

    audio = build_normalized_device_state(snapshot).to_dict()["audio"]

    assert audio["has_sink"] is False
    assert audio["sink_name"] in (None, "")
