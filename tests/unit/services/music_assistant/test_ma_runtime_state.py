"""Tests for services.ma_runtime_state logging behavior."""

import logging

import pytest

import sendspin_bridge.services.music_assistant.ma_runtime_state as ma_runtime_state


@pytest.fixture(autouse=True)
def _reset_ma_groups():
    ma_runtime_state.set_ma_groups({}, [])
    ma_runtime_state.replace_ma_now_playing({})
    yield
    ma_runtime_state.set_ma_groups({}, [])
    ma_runtime_state.replace_ma_now_playing({})


def test_set_ma_groups_logs_info_when_cache_changes(caplog):
    mapping = {"player-1": {"id": "syncgroup_1", "name": "Kitchen"}}
    all_groups = [{"id": "syncgroup_1", "name": "Kitchen", "members": []}]

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="sendspin_bridge.services.music_assistant.ma_runtime_state"):
        ma_runtime_state.set_ma_groups(mapping, all_groups)

    assert any(
        record.levelno == logging.INFO and "MA syncgroup cache updated" in record.message for record in caplog.records
    )
    # Verify cache actually stored the data
    assert ma_runtime_state.get_ma_groups() == all_groups
    assert ma_runtime_state.get_ma_group_for_player("player-1") == {"id": "syncgroup_1", "name": "Kitchen"}


def test_set_ma_groups_logs_debug_when_cache_is_unchanged(caplog):
    mapping = {"player-1": {"id": "syncgroup_1", "name": "Kitchen"}}
    all_groups = [{"id": "syncgroup_1", "name": "Kitchen", "members": []}]
    ma_runtime_state.set_ma_groups(mapping, all_groups)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="sendspin_bridge.services.music_assistant.ma_runtime_state"):
        ma_runtime_state.set_ma_groups(mapping, all_groups)

    assert any(
        record.levelno == logging.DEBUG and "MA syncgroup cache unchanged" in record.message
        for record in caplog.records
    )
    assert not any(record.levelno == logging.INFO for record in caplog.records)


# ── Multi-syncgroup membership: prefer the actively playing one ──────────


def _eneby_in_two_groups():
    """Set up the bug repro state: ENEBY @ DOCKER is a member of two MA
    syncgroups simultaneously — Sendspin BT (idle) and Sendspin RC (playing).
    Mirrors the VM 105 production state that exposed the first-write-wins
    behaviour in get_ma_group_for_player_id.
    """
    pid = "eneby-player-id"
    all_groups = [
        {
            "id": "syncgroup_eycpsxu4",
            "name": "Sendspin BT",
            "members": [{"id": pid, "name": "ENEBY Portable @ DOCKER"}],
        },
        {
            "id": "syncgroup_hpr5kpb4",
            "name": "Sendspin RC",
            "members": [{"id": pid, "name": "ENEBY Portable @ DOCKER"}],
        },
    ]
    # The mapping arg to set_ma_groups carries the existing first-write-wins
    # mapping that _refresh_groups_via_ws produced; we deliberately seed it
    # with the *wrong* group to verify the lookup helper now overrides it.
    ma_runtime_state.set_ma_groups(
        {pid: {"id": "syncgroup_eycpsxu4", "name": "Sendspin BT"}},
        all_groups,
    )
    return pid


def test_get_ma_group_for_player_prefers_actively_playing_group():
    """When a player is in two syncgroups and one is currently playing while
    the other is idle, the lookup must return the playing one — even if the
    cached mapping points at the idle one."""
    pid = _eneby_in_two_groups()
    ma_runtime_state.replace_ma_now_playing(
        {
            "syncgroup_eycpsxu4": {"state": "idle", "syncgroup_id": "syncgroup_eycpsxu4"},
            "syncgroup_hpr5kpb4": {"state": "playing", "syncgroup_id": "syncgroup_hpr5kpb4"},
        }
    )

    group = ma_runtime_state.get_ma_group_for_player_id(pid)

    assert group is not None
    assert group["id"] == "syncgroup_hpr5kpb4"
    assert group["name"] == "Sendspin RC"


def test_get_ma_group_for_player_prefers_paused_over_idle():
    """``paused`` is also an active state from the operator's perspective —
    it means the speaker is selected and the listener can resume.  Prefer
    it over a sibling idle syncgroup."""
    pid = _eneby_in_two_groups()
    ma_runtime_state.replace_ma_now_playing(
        {
            "syncgroup_eycpsxu4": {"state": "idle"},
            "syncgroup_hpr5kpb4": {"state": "paused"},
        }
    )

    group = ma_runtime_state.get_ma_group_for_player_id(pid)

    assert group is not None and group["id"] == "syncgroup_hpr5kpb4"


def test_get_ma_group_for_player_falls_back_when_all_idle():
    """If neither syncgroup is active, the lookup should fall back to the
    cached mapping (first-write-wins) so behaviour matches the pre-fix
    legacy contract for the no-state case."""
    pid = _eneby_in_two_groups()
    ma_runtime_state.replace_ma_now_playing(
        {
            "syncgroup_eycpsxu4": {"state": "idle"},
            "syncgroup_hpr5kpb4": {"state": "idle"},
        }
    )

    group = ma_runtime_state.get_ma_group_for_player_id(pid)

    assert group is not None and group["id"] == "syncgroup_eycpsxu4"


def test_get_ma_group_for_player_single_group_unaffected():
    """The lookup must not regress the single-syncgroup case: if the player
    is only in one group, return that group regardless of now-playing state."""
    ma_runtime_state.set_ma_groups(
        {"solo-pid": {"id": "syncgroup_solo", "name": "Just One"}},
        [{"id": "syncgroup_solo", "name": "Just One", "members": [{"id": "solo-pid", "name": "Solo"}]}],
    )

    group = ma_runtime_state.get_ma_group_for_player_id("solo-pid")

    assert group is not None and group["id"] == "syncgroup_solo"


def test_get_ma_group_for_player_unknown_player_returns_none():
    pid = _eneby_in_two_groups()
    assert ma_runtime_state.get_ma_group_for_player_id("not-a-real-pid") is None
    # Sanity: existing player still resolves.
    assert ma_runtime_state.get_ma_group_for_player_id(pid) is not None
