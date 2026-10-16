import json

from sendspin_bridge.sync_tuning.profiles import ConfigProfileRepository
from sendspin_sync.models import Profile


def _profile(pid="p1", group="g1", name="Evening"):
    return Profile(
        id=pid,
        name=name,
        group_id=group,
        kind="device",
        delays={"a": 120, "b": 0},
        created_at="2026-10-15T10:00:00Z",
    )


def test_save_list_get_delete_round_trip(tmp_config):
    repo = ConfigProfileRepository()
    repo.save(_profile())
    repo.save(_profile("p2", "g2"))
    assert [p.id for p in repo.list("g1")] == ["p1"]
    got = repo.get("p2")
    assert got is not None and got.group_id == "g2"
    assert json.loads(tmp_config.read_text())["SYNC_PROFILES"][0]["delays"] == {"a": 120, "b": 0}
    repo.save(_profile(name="Evening 2"))  # same id -> replaced
    assert [p.name for p in repo.list("g1")] == ["Evening 2"]
    repo.delete("p1")
    assert repo.list("g1") == []
    assert repo.get("p1") is None


def test_default_config_has_an_empty_profile_list():
    from sendspin_bridge.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["SYNC_PROFILES"] == []


def test_hand_edited_garbage_is_coerced_or_skipped(tmp_config):
    rows = [
        "not-an-object",
        {"id": "ok", "name": "N", "group_id": "g", "kind": "bogus", "delays": {"a": 5}},
        {"id": "no-name", "group_id": "g"},
        {"name": "no-id", "group_id": "g"},
        {"id": "no-group", "name": "N"},
        {"id": "pos", "name": "P", "group_id": "g", "kind": "position"},
    ]
    tmp_config.write_text(json.dumps({"SYNC_PROFILES": rows}))
    repo = ConfigProfileRepository()
    listed = repo.list("g")
    assert [(p.id, p.kind) for p in listed] == [("ok", "device"), ("pos", "position")]


def test_save_survives_garbage_rows_in_config(tmp_config):
    tmp_config.write_text(json.dumps({"SYNC_PROFILES": ["junk", 3]}))
    repo = ConfigProfileRepository()
    repo.save(_profile())
    repo.delete("nothing")
    assert [p.id for p in repo.list("g1")] == ["p1"]


def test_sync_profiles_not_settable_through_generic_config_put():
    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS

    assert "SYNC_PROFILES" not in _ALLOWED_POST_CONFIG_KEYS
