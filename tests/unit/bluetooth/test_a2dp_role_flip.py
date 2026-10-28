"""Echo-style speakers that open the A2DP stream the wrong way round (#476).

An Amazon Echo is both an audio sink and an audio source.  Connected to the
bridge it may start streaming *to* the host (the host becomes its speaker),
never connects its own sink profile, and PulseAudio then refuses the
``a2dp_sink`` card profile with "Not connected".  With the experimental sink
recovery on, the bridge first asks BlueZ to drop the speaker's source profile
and to connect its sink profile, before the disconnect/reconnect dance.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from sendspin_bridge.bluetooth.uuids import A2DP_SINK_UUID, A2DP_SOURCE_UUID, describe_uuids
from tests.support.fake_dbus import attach, bluez_knowing

_AVRCP = "0000110e-0000-1000-8000-00805f9b34fb"


def _manager(*, enable_a2dp_dance: bool):
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    return BluetoothManager(
        mac_address="AA:BB:CC:DD:EE:FF",
        device_name="Bedroom Echo",
        enable_a2dp_dance=enable_a2dp_dance,
    )


@pytest.fixture
def echo(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=True)
    bluez = bluez_knowing(
        mgr, connected=True, paired=True, services_resolved=True, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID, _AVRCP)
    )
    attach(mgr, bluez)
    return mgr, bluez


def _profile_calls(bluez):
    return [(name, args) for _path, name, args in bluez.calls if name in {"DisconnectProfile", "ConnectProfile"}]


def test_role_flip_first_asks_for_the_sink_while_the_source_stays_up(echo):
    mgr, bluez = echo
    with patch.object(mgr, "_wait_with_cancel", return_value=True):
        assert mgr._a2dp_role_flip() is True

    assert _profile_calls(bluez) == [("ConnectProfile", (A2DP_SINK_UUID,))]


def test_a_refused_sink_falls_back_to_dropping_the_source_then_connecting_the_sink(echo):
    mgr, bluez = echo
    bluez.fail["ConnectProfile"] = RuntimeError("org.bluez.Error.Failed: refused")

    def speaker_accepts_the_sink_once_the_source_is_gone(_seconds):
        if ("DisconnectProfile", (A2DP_SOURCE_UUID,)) in _profile_calls(bluez):
            bluez.fail.pop("ConnectProfile", None)
        return True

    with patch.object(mgr, "_wait_with_cancel", side_effect=speaker_accepts_the_sink_once_the_source_is_gone):
        assert mgr._a2dp_role_flip() is True

    assert _profile_calls(bluez) == [
        ("ConnectProfile", (A2DP_SINK_UUID,)),
        ("DisconnectProfile", (A2DP_SOURCE_UUID,)),
        ("ConnectProfile", (A2DP_SINK_UUID,)),
    ]


def test_role_flip_reports_refusal_when_both_orders_are_refused(echo, caplog):
    mgr, bluez = echo
    bluez.fail["ConnectProfile"] = RuntimeError("org.bluez.Error.Failed: refused")
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager")

    with patch.object(mgr, "_wait_with_cancel", return_value=True):
        assert mgr._a2dp_role_flip() is False

    messages = [r.getMessage() for r in caplog.records]
    assert any("Audio Sink ConnectProfile (source kept): refused" in m for m in messages)
    assert any("Audio Sink ConnectProfile (after dropping the source)" in m and "refused" in m for m in messages)


def test_role_flip_is_skipped_for_a_speaker_that_cannot_be_a_source(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=True)
    bluez = bluez_knowing(mgr, connected=True, paired=True, services_resolved=True, uuids=(A2DP_SINK_UUID,))
    attach(mgr, bluez)

    with patch.object(mgr, "_wait_with_cancel", return_value=True):
        assert mgr._a2dp_role_flip() is False

    assert _profile_calls(bluez) == []


def test_a_refused_source_disconnect_still_tries_the_sink(echo):
    mgr, bluez = echo
    bluez.fail["DisconnectProfile"] = RuntimeError("org.bluez.Error.NotConnected")
    with patch.object(mgr, "_wait_with_cancel", return_value=True):
        mgr._a2dp_role_flip()

    assert ("ConnectProfile", (A2DP_SINK_UUID,)) in _profile_calls(bluez)


def test_connect_tries_the_role_flip_before_the_dance(echo):
    mgr, _bluez = echo
    order: list[str] = []
    sink_after_flip = iter([False, True])

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", side_effect=lambda: next(sink_after_flip)),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_role_flip", side_effect=lambda: order.append("flip") or True),
        patch.object(mgr, "_a2dp_recovery_dance", side_effect=lambda: order.append("dance") or True),
    ):
        assert mgr.connect_device() is True

    assert order == ["flip"]


def test_connect_falls_back_to_the_dance_when_the_flip_does_not_help(echo):
    mgr, _bluez = echo
    order: list[str] = []
    sink = iter([False, False, True])

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", side_effect=lambda: next(sink)),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_role_flip", side_effect=lambda: order.append("flip") or True),
        patch.object(mgr, "_a2dp_recovery_dance", side_effect=lambda: order.append("dance") or True),
    ):
        assert mgr.connect_device() is True

    assert order == ["flip", "dance"]


def test_connect_skips_the_dance_when_a_dual_role_speaker_refuses_the_flip(echo, caplog):
    mgr, _bluez = echo
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager")

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=False),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_role_flip", return_value=False),
        patch.object(mgr, "_a2dp_recovery_dance") as dance,
    ):
        mgr.connect_device()

    dance.assert_not_called()
    assert any("Skipping the disconnect/reconnect dance" in r.getMessage() for r in caplog.records)


def test_connect_still_dances_for_a_speaker_that_is_only_a_sink(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=True)
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True, services_resolved=True, uuids=(A2DP_SINK_UUID,)))

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=False),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_recovery_dance", return_value=False) as dance,
    ):
        mgr.connect_device()

    dance.assert_called_once()


def _connect_and_snapshot_profiles_at_services_wait(mgr, bluez):
    """Connect, recording which profile calls BlueZ saw before SDP resolution was awaited."""
    seen: list = []

    def services_wait(**_kwargs):
        seen.extend(_profile_calls(bluez))
        return True

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=True),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr.device, "wait_for_services_blocking", side_effect=services_wait),
    ):
        mgr.connect_device()
    return seen


def test_connect_asks_a_dual_role_speaker_for_its_sink_as_soon_as_the_link_is_up(echo, caplog):
    mgr, bluez = echo
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager")

    before_services = _connect_and_snapshot_profiles_at_services_wait(mgr, bluez)

    assert before_services == [("ConnectProfile", (A2DP_SINK_UUID,))]
    assert ("DisconnectProfile", (A2DP_SOURCE_UUID,)) not in _profile_calls(bluez)
    assert any("Early Audio Sink ConnectProfile (source kept): accepted" in r.getMessage() for r in caplog.records)


def test_no_early_sink_request_without_the_experimental_recovery(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=False)
    bluez = bluez_knowing(
        mgr, connected=True, paired=True, services_resolved=True, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID, _AVRCP)
    )
    attach(mgr, bluez)

    assert _connect_and_snapshot_profiles_at_services_wait(mgr, bluez) == []


def test_no_early_sink_request_for_a_speaker_that_is_only_a_sink(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=True)
    bluez = bluez_knowing(mgr, connected=True, paired=True, services_resolved=True, uuids=(A2DP_SINK_UUID,))
    attach(mgr, bluez)

    assert _connect_and_snapshot_profiles_at_services_wait(mgr, bluez) == []


def test_without_the_experimental_recovery_there_is_no_role_flip(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = _manager(enable_a2dp_dance=False)
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True, services_resolved=True))

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=False),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_role_flip") as flip,
    ):
        mgr.connect_device()

    flip.assert_not_called()


def test_connect_logs_the_profiles_the_speaker_offers(echo, caplog):
    mgr, _bluez = echo
    caplog.set_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager")

    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=True),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
    ):
        mgr.connect_device()

    lines = [r.getMessage() for r in caplog.records if "offers" in r.getMessage()]
    assert lines, "no profile line logged on connect"
    assert "Audio Source" in lines[0] and "Audio Sink" in lines[0] and "AVRCP" in lines[0]


def test_describe_uuids_names_known_services_and_keeps_unknown_ones():
    text = describe_uuids([A2DP_SINK_UUID, "0000feed-0000-1000-8000-00805f9b34fb"])

    assert "Audio Sink" in text
    assert "0000feed" in text
