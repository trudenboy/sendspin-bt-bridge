"""Task 1's use case: the sync wizard listens through one of the bridge's own inputs.

The input list is the Sources UI's list (``application.source.inputs()``); the recording
length comes from the session's sequence, not from the client's number.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_sync import estimator
from sendspin_sync import sequence as seq

LOCAL = {
    "id": "local:alsa_input.usb-mic",
    "kind": "local",
    "name": "USB microphone",
    "available": True,
    "node": "alsa_input.usb-mic",
    "mac": None,
}
PHONE = {
    "id": "bluetooth:AA:BB:CC:DD:EE:FF",
    "kind": "bluetooth",
    "name": "Phone",
    "available": True,
    "connected": True,
    "node": None,
    "mac": "AA:BB:CC:DD:EE:FF",
}
HEADSET = {
    "id": "headset-mic:11:22:33:44:55:66",
    "kind": "headset_microphone",
    "name": "Headset",
    "available": True,
    "node": None,
    "mac": "11:22:33:44:55:66",
}
CANDIDATE = {
    "id": "local:alsa_input.builtin-mic",
    "kind": "local",
    "name": "Built-in microphone",
    "available": True,
    "node": "alsa_input.builtin-mic",
    "mac": None,
}


@pytest.fixture
def source_view(monkeypatch):
    """The Sources UI's answer; the endpoint must mirror it, not invent a list of its own."""
    from sendspin_bridge.application import source as source_uc

    view = {
        "active_input": None,
        "inputs": [dict(LOCAL), dict(PHONE), dict(HEADSET)],
        "local_candidates": [dict(CANDIDATE)],
        "capture_available": True,
        "capture_kind": "pipewire",
    }

    async def fake_inputs(*, include_monitors: bool = False):
        return view

    monkeypatch.setattr(source_uc, "inputs", fake_inputs)
    return view


def test_the_listening_devices_mirror_the_source_inputs(source_view):
    devices = asyncio.run(uc.list_listening_devices()).devices
    assert [d.model_dump() for d in devices] == [
        {"id": LOCAL["id"], "name": "USB microphone", "kind": "local", "available": True},
        {"id": PHONE["id"], "name": "Phone", "kind": "bluetooth", "available": True},
        {"id": HEADSET["id"], "name": "Headset", "kind": "headset_microphone", "available": True},
        {"id": CANDIDATE["id"], "name": "Built-in microphone", "kind": "local", "available": True},
    ]


def test_a_node_that_is_not_present_is_listed_as_unavailable(source_view):
    source_view["inputs"][0]["available"] = False  # the saved local input's node is gone
    source_view["inputs"][1]["connected"] = False  # a bonded phone is not connected
    devices = asyncio.run(uc.list_listening_devices()).devices
    assert [(d.id, d.available) for d in devices] == [
        (LOCAL["id"], False),
        (PHONE["id"], False),
        (HEADSET["id"], True),
        (CANDIDATE["id"], True),
    ]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, seq.length_s(2, "device")),  # the sequence itself: 22 s, longer than one solo
        (1.0, estimator.MIN_DURATION_S),
        (1000.0, seq.max_duration_s(2, "device")),  # the phone upload's own bound
        (6.0, 6.0),
    ],
)
def test_the_recording_length_is_the_sequences_and_is_clamped(seconds, expected):
    sequence = {"members": ["a", "b"], "lead_s": seq.LEAD_S}
    assert uc._recording_seconds(seconds, sequence, "device") == pytest.approx(expected)


def test_the_default_recording_covers_the_whole_sequence():
    """A two-member sequence plays 22 s; the estimator's 20 s per-member window must not cut it."""
    sequence = {"members": ["a", "b"], "lead_s": seq.LEAD_S}
    assert uc._recording_seconds(None, sequence, "device") >= seq.length_s(2, "device")
    assert uc._recording_seconds(None, sequence, "position") >= seq.length_s(2, "position")


def test_a_non_finite_length_falls_back_to_the_sequences_length():
    sequence = {"members": ["a"], "lead_s": seq.LEAD_S}
    assert uc._recording_seconds(float("nan"), sequence, "device") == pytest.approx(seq.length_s(1, "device"))


def test_a_paired_phone_resolves_to_its_capture_node(monkeypatch):
    monkeypatch.setattr(uc, "_audio_source_nodes", lambda: ("bluez_source.AA_BB_CC_DD_EE_FF", "alsa_input.usb-mic"))
    assert asyncio.run(uc._input_node(PHONE)) == "bluez_source.AA_BB_CC_DD_EE_FF"
    assert asyncio.run(uc._input_node(HEADSET)) is None  # no node for its address
    assert asyncio.run(uc._input_node(LOCAL)) == "alsa_input.usb-mic"  # a local input is its node
