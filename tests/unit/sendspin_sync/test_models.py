from sendspin_sync.models import GroupPlayback, Profile, Snapshot, profile_from_dict, snapshot_from_dict, to_dict


def test_profile_round_trips_through_json_shape():
    p = Profile(
        id="p1",
        name="Evening",
        group_id="g",
        kind="device",
        delays={"a": 10, "b": 0},
        created_at="2026-10-15T10:00:00Z",
    )
    assert profile_from_dict(to_dict(p)) == p


def test_snapshot_round_trips():
    s = Snapshot(group_id="g", delays={"a": 1}, muted={"a": True})
    assert snapshot_from_dict(to_dict(s)) == s


def test_an_older_snapshot_with_volumes_still_reads():
    old = {"group_id": "g", "delays": {"a": 1}, "volumes": {"a": 40}, "muted": {}, "playing": True}
    assert snapshot_from_dict(old) == Snapshot(group_id="g", delays={"a": 1})


URL = "http://10.0.0.5:8080/api/v1/sync/signal/" + "ab" * 32 + ".wav"


def test_the_group_plays_the_clicker_only_while_playing_our_url():
    assert GroupPlayback("playing", URL).plays(URL)
    assert GroupPlayback("playing", f"builtin://sound_effect/{URL}").plays(URL)  # what live MA reports
    # Music Assistant may report the item under its own uri: the token still matches
    assert GroupPlayback("playing", "builtin://track/" + "ab" * 32).plays(URL)
    assert not GroupPlayback("idle", URL).plays(URL)
    assert not GroupPlayback("playing", "library://track/1").plays(URL)
    assert not GroupPlayback("playing", None).plays(URL)
    assert not GroupPlayback("playing", URL).plays(None)


def test_any_of_the_sessions_tracks_counts_as_its_clicker():
    """Every sound of one session is served under its one token: a switch of sound (or a start that timed
    out) still leaves whatever of ours plays recognised, and stopped at the end."""
    base = "http://10.0.0.5:8080/api/v1/sync/signal/" + "ab" * 32
    clicks, noise = base + "/clicks.wav", base + "/noise.wav"
    assert GroupPlayback("playing", f"builtin://sound_effect/{noise}").plays(clicks)
    assert GroupPlayback("playing", f"builtin://sound_effect/{URL}").plays(noise)
    assert GroupPlayback("playing", "builtin://track/" + "ab" * 32).plays(noise)
    other = "http://10.0.0.5:8080/api/v1/sync/signal/" + "cd" * 32 + "/clicks.wav"
    assert not GroupPlayback("playing", other).plays(clicks)
    assert not GroupPlayback("playing", "library://track/clicks.wav").plays(clicks)


def test_the_token_is_read_from_our_own_path_even_behind_a_prefix_with_signal_in_it():
    token = "ef" * 32
    url = f"http://proxy/signal/x/api/v1/sync/signal/{token}/clicks.wav"
    assert GroupPlayback("playing", "builtin://track/" + token).plays(url)
    assert not GroupPlayback("playing", "builtin://track/x").plays(url)
