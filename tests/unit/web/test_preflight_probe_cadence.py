"""The host probe runs on its own, slow clock.

Each probe spawned bluetoothctl three times and opened two PulseAudio
sessions; at a 2 s cadence with any dashboard tab open that was most of the
bridge's idle work and ~15 MB/s of allocator churn. The host it describes
changes on the order of minutes, and the moments it does change — a speaker
connecting or leaving — invalidate the sample instead.
"""

from __future__ import annotations

import sendspin_bridge.web.routes.api_status as api_status


def test_the_host_probe_is_sampled_at_most_every_thirty_seconds():
    assert api_status._preflight_probe._min_interval_s >= 30.0


def test_one_probe_opens_one_audio_session(monkeypatch):
    opened: list[int] = []

    def _snapshot():
        opened.append(1)
        return "pulseaudio", [{"name": "bluez_sink.x"}]

    def _collect(**fns):
        assert fns["get_server_name_fn"]() == "pulseaudio"
        assert fns["list_sinks_fn"]() == [{"name": "bluez_sink.x"}]
        return {}

    monkeypatch.setattr(api_status, "get_audio_server_snapshot", _snapshot)
    monkeypatch.setattr(api_status, "_shared_collect_preflight_status", _collect)

    api_status._collect_preflight_status()

    assert opened == [1]


def test_a_speaker_connecting_invalidates_the_sample(monkeypatch):
    from sendspin_bridge.services.diagnostics import preflight_status

    api_status.reset_preflight_probe()
    probes: list[int] = []
    monkeypatch.setattr(api_status, "_collect_preflight_status", lambda: probes.append(1) or {})

    api_status._sampled_preflight_status()
    api_status._sampled_preflight_status()
    preflight_status.notify_host_changed()
    api_status._sampled_preflight_status()

    assert probes == [1, 1]
