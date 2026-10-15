from sendspin_sync.models import Profile, Snapshot, profile_from_dict, snapshot_from_dict, to_dict


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


def test_snapshot_round_trips_and_keeps_opaque_token():
    s = Snapshot(
        group_id="g",
        delays={"a": 1},
        volumes={"a": 40},
        muted={"a": False},
        playing=True,
        restore_token={"announcing": True},
    )
    d = to_dict(s)
    assert d["restore_token"] == {"announcing": True}
    assert snapshot_from_dict(d) == s
