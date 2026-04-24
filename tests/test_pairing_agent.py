from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.pairing_agent import PairingAgent, _build_agent_iface


def test_rejects_invalid_capability():
    with pytest.raises(ValueError):
        PairingAgent(capability="NotARealCap")


def test_agent_request_pin_code_records_attempt_and_stores_pin():
    # dbus-fast's @method decorator discards the Python return value when the
    # method is invoked directly (the value is returned to the D-Bus caller
    # via a separate channel). Verify plumbing via the agent's own state
    # instead: pin attribute is set from constructor, pin_attempted flips
    # after BlueZ-triggered RequestPinCode.
    agent = _build_agent_iface(pin="1234")

    assert agent.pin == "1234"
    assert agent.pin_attempted is False

    agent.RequestPinCode("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

    assert agent.pin_attempted is True


def test_agent_request_confirmation_records_passkey():
    agent = _build_agent_iface(pin="0000")

    agent.RequestConfirmation("/org/bluez/hci0/dev_AA_BB", 941189)

    # Implicit "accept" (no raise, no DBusError), passkey captured for logs.
    assert agent.last_passkey == 941189
    assert agent.cancelled is False


def test_agent_cancel_sets_flag():
    agent = _build_agent_iface(pin="0000")

    agent.Cancel()

    assert agent.cancelled is True


def test_agent_authorize_service_accepts_audio_uuid():
    agent = _build_agent_iface(pin="0000")
    # A2DP Sink (full 128-bit lowercase with dashes)
    agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB", "0000110b-0000-1000-8000-00805f9b34fb")
    assert agent.authorized_services == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert agent.rejected_services == []


def test_agent_authorize_service_accepts_short_uuid_form():
    agent = _build_agent_iface(pin="0000")
    # BlueZ can pass 16-bit short form for well-known services.
    agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB", "110E")
    # AVRCP Controller — normalized and authorized
    assert agent.authorized_services == ["0000110e-0000-1000-8000-00805f9b34fb"]


def test_agent_authorize_service_rejects_unknown_uuid():
    from dbus_fast import DBusError

    agent = _build_agent_iface(pin="0000")
    # HID-over-GATT — intentionally outside the audio allow-list.
    with pytest.raises(DBusError) as exc_info:
        agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB", "00001812-0000-1000-8000-00805f9b34fb")

    assert "not in sendspin-bridge audio allow-list" in str(exc_info.value)
    assert agent.authorized_services == []
    assert agent.rejected_services == ["00001812-0000-1000-8000-00805f9b34fb"]


def test_agent_authorize_service_accepts_universal_services():
    agent = _build_agent_iface(pin="0000")
    # Device Information Service, Battery Service, Generic Access, Generic Attribute.
    for short_uuid in ("180A", "180F", "1800", "1801"):
        agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB", short_uuid)
    assert len(agent.authorized_services) == 4
    assert agent.rejected_services == []


def test_agent_telemetry_snapshot_matches_calls():
    agent = _build_agent_iface(pin="0000")
    agent.RequestConfirmation("/org/bluez/hci0/dev_AA_BB", 941189)
    agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB", "0000110b-0000-1000-8000-00805f9b34fb")

    assert agent.method_calls == ["RequestConfirmation", "AuthorizeService"]
    assert agent.last_passkey == 941189
    assert agent.authorized_services == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert agent.cancelled is False


def test_pairing_agent_telemetry_property_returns_stable_keys():
    # Even before __enter__ is called, telemetry should return a usable
    # empty snapshot with all documented keys — downstream parsers rely
    # on the dict shape being stable.
    agent = PairingAgent(capability="DisplayYesNo", pin="0000")
    snapshot = agent.telemetry
    assert set(snapshot.keys()) == {
        "capability",
        "method_calls",
        "last_passkey",
        "pin_attempted",
        "peer_cancelled",
        "authorized_services",
        "rejected_services",
    }
    assert snapshot["capability"] == "DisplayYesNo"
    assert snapshot["method_calls"] == []
    assert snapshot["last_passkey"] is None


def test_context_manager_registers_and_unregisters_with_mocked_bus():
    # A full end-to-end check without a real SystemBus: patch dbus_fast
    # MessageBus to a stub that records register_agent / request_default_agent
    # / unregister_agent calls. This catches obvious breakage in the
    # agent-manager call sequence (issue #168 regressions).
    calls: list[str] = []

    mgr_iface = MagicMock()
    mgr_iface.call_register_agent = AsyncMock(
        side_effect=lambda *a, **k: calls.append(f"register:{a}"),
    )
    mgr_iface.call_request_default_agent = AsyncMock(
        side_effect=lambda *a, **k: calls.append(f"default:{a}"),
    )
    mgr_iface.call_unregister_agent = AsyncMock(
        side_effect=lambda *a, **k: calls.append(f"unregister:{a}"),
    )
    proxy = MagicMock()
    proxy.get_interface = MagicMock(return_value=mgr_iface)

    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.export = MagicMock()
    bus.introspect = AsyncMock(return_value=object())
    bus.get_proxy_object = MagicMock(return_value=proxy)
    bus.disconnect = MagicMock()
    bus.wait_for_disconnect = AsyncMock()

    def _fake_message_bus(*args, **kwargs):
        bus_instance = MagicMock()
        bus_instance.connect = AsyncMock(return_value=bus)
        return bus_instance

    with (
        patch("dbus_fast.aio.MessageBus", new=_fake_message_bus),
        PairingAgent(capability="DisplayYesNo", pin="0000") as agent,
    ):
        assert agent.capability == "DisplayYesNo"

    assert any(c.startswith("register:") for c in calls)
    assert any(c.startswith("default:") for c in calls)
    assert any(c.startswith("unregister:") for c in calls)


def test_enter_raises_if_bus_connect_fails():
    def _broken_bus(*args, **kwargs):
        instance = MagicMock()
        instance.connect = AsyncMock(side_effect=ConnectionError("no SystemBus"))
        return instance

    with (
        patch("dbus_fast.aio.MessageBus", new=_broken_bus),
        pytest.raises(RuntimeError, match="PairingAgent start failed"),
    ):
        PairingAgent(capability="DisplayYesNo").__enter__()
