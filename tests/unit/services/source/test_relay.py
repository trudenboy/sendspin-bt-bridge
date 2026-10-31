"""The phone relay remembers which speakers should play it (#563)."""

from __future__ import annotations

import pytest

from sendspin_bridge.services.source import relay


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(relay, "_armed", {})
    monkeypatch.setattr(relay, "_running", {})
    relay.configure(tmp_path / "relays.json")


def test_an_armed_speaker_is_remembered_across_a_restart():
    relay.arm("device-1", "bluez_output.1")
    relay._armed.clear()  # a new bridge process starts with nothing in memory
    relay.load()
    assert relay.armed_devices() == ["device-1"]
    assert relay.armed_devices() == ["device-1"]  # the sink is kept in the file, not exposed here


def test_disarming_removes_the_speaker_from_the_saved_list():
    relay.arm("device-1", "bluez_output.1")
    relay.disarm("device-1")
    relay._armed.clear()
    relay.load()
    assert relay.armed_devices() == []
