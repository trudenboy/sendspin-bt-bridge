from __future__ import annotations

from sendspin_bridge.services.audio.playback_health import PlaybackHealthMonitor


def test_observe_status_update_tracks_play_session_and_streaming():
    monitor = PlaybackHealthMonitor()

    monitor.observe_status_update(previous_playing=False, updates={"playing": True}, now=100.0)
    assert monitor.playing_since == 100.0
    assert monitor.has_streamed is False

    monitor.observe_status_update(previous_playing=True, updates={"audio_streaming": True}, now=101.0)
    assert monitor.has_streamed is True
    assert monitor.restart_count == 0

    monitor.observe_status_update(previous_playing=True, updates={"playing": False}, now=102.0)
    assert monitor.playing_since is None
    assert monitor.has_streamed is False
    assert monitor.restart_count == 0


def test_check_zombie_playback_requests_restart_after_timeout():
    monitor = PlaybackHealthMonitor()
    monitor.playing_since = 100.0

    should_restart, elapsed, restart_count = monitor.check_zombie_playback(
        is_playing=True,
        is_streaming=False,
        daemon_alive=True,
        now=116.0,
    )

    assert should_restart is True
    assert elapsed == 16.0
    assert restart_count == 1
    assert monitor.playing_since is None


def test_check_zombie_playback_skips_when_audio_already_streamed():
    monitor = PlaybackHealthMonitor()
    monitor.playing_since = 100.0
    monitor.has_streamed = True

    should_restart, elapsed, restart_count = monitor.check_zombie_playback(
        is_playing=True,
        is_streaming=False,
        daemon_alive=True,
        now=130.0,
    )

    assert should_restart is False
    assert elapsed == 0.0
    assert restart_count == 0
