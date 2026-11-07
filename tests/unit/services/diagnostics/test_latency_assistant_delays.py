"""The latency assistant sees the delays the speakers have, whatever name the bridge runs them under.

Runtime player names carry the bridge's suffix (" @ <bridge>"); the config names a player without
it. Looked up by name, every configured delay was missed, and a tuned multi-room group was told it
had no per-device delays and should change the PulseAudio buffer.
"""

from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from tests.support.config_v6 import v6_config

ONBOARDING = {"checklist": {"steps": []}, "checks": []}


def _device(name: str, mac: str):
    facts = {"connected": True, "reconnect_attempt": 0, "max_reconnect_fails": 5}
    return SimpleNamespace(
        player_name=name,
        enabled=True,
        bluetooth_mac=mac,
        extra={"bluetooth_connected": True, **facts},
        state_model={"bluetooth": facts},
        recent_events=[],
        health_summary=None,
    )


def _latency(config, devices):
    state = build_bridge_state_model(
        config=config, devices=devices, runtime_mode="docker", ma_connected=True, preflight={}
    )
    snapshot = build_recovery_assistant_snapshot(
        config=config, devices=devices, onboarding_assistant=ONBOARDING, startup_progress={}, bridge_state=state
    )
    return snapshot.to_dict()["latency_assistant"]


def test_delays_of_suffixed_runtime_names_are_found():
    config = v6_config(
        [
            {"player_name": "Kitchen", "mac": "AA:AA:AA:AA:AA:01", "static_delay_ms": 0},
            {"player_name": "Office", "mac": "AA:AA:AA:AA:AA:02", "static_delay_ms": 40},
        ],
        PULSE_LATENCY_MSEC=600,
    )
    devices = [_device("Kitchen @ bridge", "AA:AA:AA:AA:AA:01"), _device("Office @ bridge", "AA:AA:AA:AA:AA:02")]

    latency = _latency(config, devices)

    assert latency["code"] != "multi_no_delays"


def test_a_group_without_delays_is_still_told_so():
    config = v6_config(
        [
            {"player_name": "Kitchen", "mac": "AA:AA:AA:AA:AA:01"},
            {"player_name": "Office", "mac": "AA:AA:AA:AA:AA:02"},
        ],
        PULSE_LATENCY_MSEC=600,
    )
    devices = [_device("Kitchen @ bridge", "AA:AA:AA:AA:AA:01"), _device("Office @ bridge", "AA:AA:AA:AA:AA:02")]

    assert _latency(config, devices)["code"] == "multi_no_delays"
