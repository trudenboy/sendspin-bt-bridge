"""Which Music Assistant player is this bridge client?

The bridge derived a solo player's MA queue id from its own client id —
`up` + the id with the dashes stripped.  Music Assistant used to number
players that way; it no longer does.  Measured against a live server:

    player_queues/get up8e34f1108d9b5eeeb08e7dad2a4ae478 -> null
    player_queues/get 8e34f110-8d9b-5eee-b08e-7dad2a4ae478 -> null
    player_queues/get up4098e820 -> ENEBY Portable @ local-test

So every queue command aimed at an ungrouped speaker addressed a queue that
does not exist, and the queue never appeared in the now-playing cache — which
is what made the device card report Music Assistant as disconnected while it
was connected.

MA does publish the link: the universal player that fronts our speaker lists
our client id as one of its output protocols.  It is looked up, not guessed.
"""

from __future__ import annotations

from sendspin_bridge.services.music_assistant.ma_player_map import learn_ma_player_ids

CLIENT_ID = "8e34f110-8d9b-5eee-b08e-7dad2a4ae478"

# Shapes taken from a live MA 2026.x `players/all` response.
PLAYERS = [
    {
        "player_id": "up4098e820",
        "name": "ENEBY Portable @ local-test",
        "display_name": "ENEBY Portable @ local-test",
        "provider": "universal_player",
        "output_protocols": [{"output_protocol_id": CLIENT_ID, "name": "Sendspin", "protocol_domain": "sendspin"}],
    },
    {
        "player_id": "up366c5814",
        "name": "ENEBY Portable @ 85b1ecde-sendspin-bt-bridge-rc",
        "display_name": "ENEBY Portable @ 85b1ecde-sendspin-bt-bridge-rc",
        "provider": "universal_player",
        "output_protocols": [{"output_protocol_id": "8e718155-dd65-50df-ab9b-fd166b2a572d", "name": "Sendspin"}],
    },
    {
        "player_id": "ys_MK0000000000000131310000f057007a",
        "name": "yandexioreceiver",
        "provider": "yandex_station",
        "output_protocols": [{"output_protocol_id": "native", "name": "Yandex Station"}],
    },
]


def test_the_player_that_carries_our_output_protocol_is_ours():
    mapping = learn_ma_player_ids(PLAYERS, [{"player_id": CLIENT_ID, "player_name": "ENEBY Portable @ local-test"}])

    assert mapping == {CLIENT_ID: "up4098e820"}


def test_another_bridge_serving_the_same_speaker_is_not_ours():
    """Two bridges expose the same speaker; only our client id decides."""
    mapping = learn_ma_player_ids(PLAYERS, [{"player_id": CLIENT_ID, "player_name": "ENEBY Portable @ local-test"}])

    assert "up366c5814" not in mapping.values()


def test_a_client_music_assistant_does_not_know_is_absent():
    mapping = learn_ma_player_ids(PLAYERS, [{"player_id": "no-such-client", "player_name": "Nowhere"}])

    assert mapping == {}


def test_an_exact_display_name_stands_in_when_no_protocol_matches():
    """Older servers list no output protocols; the name is then all there is."""
    players = [{"player_id": "up111", "display_name": "Study", "provider": "universal_player"}]

    mapping = learn_ma_player_ids(players, [{"player_id": "client-1", "player_name": "Study"}])

    assert mapping == {"client-1": "up111"}


def test_a_near_miss_on_the_name_is_not_a_match():
    """ "Study" and "Study @ other-bridge" are different speakers."""
    players = [{"player_id": "up111", "display_name": "Study @ other-bridge", "provider": "universal_player"}]

    mapping = learn_ma_player_ids(players, [{"player_id": "client-1", "player_name": "Study"}])

    assert mapping == {}


def test_the_protocol_match_wins_over_a_name_that_points_elsewhere():
    players = [
        {"player_id": "up111", "display_name": "Kitchen", "provider": "universal_player"},
        {
            "player_id": "up222",
            "display_name": "Kitchen",
            "provider": "universal_player",
            "output_protocols": [{"output_protocol_id": "client-1"}],
        },
    ]

    mapping = learn_ma_player_ids(players, [{"player_id": "client-1", "player_name": "Kitchen"}])

    assert mapping == {"client-1": "up222"}


# ── what a queue command is aimed at ─────────────────────────────────────


def test_the_learned_id_is_the_first_queue_a_command_is_aimed_at(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_runtime_state
    from sendspin_bridge.services.music_assistant.ma_monitor import solo_queue_candidates

    ma_runtime_state.set_ma_player_ids({CLIENT_ID: "up4098e820"})
    try:
        candidates = solo_queue_candidates(CLIENT_ID)
    finally:
        ma_runtime_state.set_ma_player_ids({})

    assert candidates[0] == "up4098e820"


def test_the_derived_ids_remain_as_a_fallback():
    """Servers that do number players our way must keep working."""
    from sendspin_bridge.services.music_assistant.ma_monitor import solo_queue_candidates

    candidates = solo_queue_candidates(CLIENT_ID)

    assert "up8e34f1108d9b5eeeb08e7dad2a4ae478" in candidates


def test_a_learned_id_is_not_repeated_among_the_fallbacks():
    from sendspin_bridge.services.music_assistant import ma_runtime_state
    from sendspin_bridge.services.music_assistant.ma_monitor import solo_queue_candidates

    ma_runtime_state.set_ma_player_ids({CLIENT_ID: "up8e34f1108d9b5eeeb08e7dad2a4ae478"})
    try:
        candidates = solo_queue_candidates(CLIENT_ID)
    finally:
        ma_runtime_state.set_ma_player_ids({})

    assert candidates.count("up8e34f1108d9b5eeeb08e7dad2a4ae478") == 1


# ── the monitor learns it on every refresh ───────────────────────────────


def test_the_groups_refresh_also_learns_the_player_ids(monkeypatch):
    """One `players/all` answer carries both facts; both are kept."""
    import asyncio
    from types import SimpleNamespace

    from sendspin_bridge.services.music_assistant import ma_runtime_state
    from sendspin_bridge.services.music_assistant.ma_monitor import MaMonitor

    monitor = MaMonitor.__new__(MaMonitor)
    monitor._next_id = lambda: 1
    monitor._defer_incoming_event = lambda _evt: None
    monitor._track_background_task = lambda task: task

    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._send",
        lambda *_a, **_kw: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._recv",
        lambda *_a, **_kw: _answer(),
    )
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._active_bridge_clients",
        lambda: [SimpleNamespace(player_id=CLIENT_ID, player_name="ENEBY Portable @ local-test")],
    )

    async def _answer():
        return {"message_id": 1, "result": PLAYERS}

    ma_runtime_state.set_ma_player_ids({})
    try:
        asyncio.run(monitor._refresh_groups_via_ws(object()))
        assert ma_runtime_state.get_ma_player_id(CLIENT_ID) == "up4098e820"
    finally:
        ma_runtime_state.set_ma_player_ids({})
