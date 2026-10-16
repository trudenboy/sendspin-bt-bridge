import pytest

from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.ma_group_control import MaGroupControl
from sendspin_sync.ports import ControlError
from tests.support.sync_fakes import GROUPS, FakeMa


def make(ma=None):
    ma = ma or FakeMa()
    ctl = MaGroupControl(
        ma,
        groups=lambda: GROUPS,
        bridge_player_ids=lambda: {"spk-bridge"},
    )
    return ctl, ma


def test_members_resolve_origin_adjustability_and_delay():
    ctl, _ = make()
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    assert (
        by["spk-bridge"].origin,
        by["spk-bridge"].adjustable,
        by["spk-bridge"].delay_ms,
        by["spk-bridge"].client_id,
    ) == (
        "bridge",
        True,
        120,
        "spk-bridge",
    )
    assert (by["spk-kiosk"].origin, by["spk-kiosk"].adjustable) == ("sendspin", False)  # no delay entry
    assert (by["uni"].origin, by["uni"].adjustable, by["uni"].client_id, by["uni"].delay_ms) == (
        "sendspin",
        True,
        "uni-sendspin",
        0,
    )
    assert (by["cast"].origin, by["cast"].adjustable) == ("other", False)


def test_unknown_group_is_member_gone():
    ctl, _ = make()
    with pytest.raises(ControlError) as exc:
        ctl.members("nope")
    assert exc.value.code == "member_gone"


def test_set_delay_writes_the_protocol_key_form_for_a_universal_player():
    ctl, ma = make()
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    ctl.set_delay(by["uni"], 250)
    ctl.set_delay(by["spk-bridge"], 90)
    assert (
        "config/players/save",
        {"player_id": "uni", "values": {"uni-sendspin||protocol||sendspin_static_delay": 250}},
    ) in ma.calls
    assert ("config/players/save", {"player_id": "spk-bridge", "values": {"sendspin_static_delay": 90}}) in ma.calls
    with pytest.raises(ControlError) as exc:
        ctl.set_delay(by["cast"], 10)
    assert exc.value.code == "not_adjustable"


def test_delays_reads_the_current_values_not_the_cached_ones():
    ctl, _ = make()
    members = ctl.members("grp")
    by = {m.ma_player_id: m for m in members}
    ctl.set_delay(by["uni"], 250)
    assert ctl.delays(members) == {"spk-bridge": 120, "spk-kiosk": None, "uni": 250, "cast": None}


def test_forbidden_write_becomes_ma_forbidden():
    ma = FakeMa()
    ma.forbid_write = True
    ctl, _ = make(ma)
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    with pytest.raises(ControlError) as exc:
        ctl.set_delay(by["spk-bridge"], 1)
    assert exc.value.code == "ma_forbidden"


def test_ma_errors_surface_as_control_errors():
    class Down(FakeMa):
        def call(self, command, args, *, timeout=10.0):
            raise MaApiError("ma_unavailable", "gone")

        def fire(self, command, args):
            raise MaApiError("ma_unavailable", "gone")

    ctl, _ = make(Down())
    actions = (
        lambda: ctl.members("grp"),
        lambda: ctl.playback("grp"),
        lambda: ctl.start_clicker("grp", "u"),
        lambda: ctl.stop_group("grp"),
        lambda: ctl.set_volume("uni", 10),
    )
    for action in actions:
        with pytest.raises(ControlError) as exc:
            action()
        assert exc.value.code == "ma_unavailable"


def test_default_group_control_needs_credentials(monkeypatch):
    from sendspin_bridge.sync_tuning import ma_api, ma_group_control

    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("", ""))
    with pytest.raises(MaApiError) as exc:
        ma_group_control.default_group_control()
    assert exc.value.code == "ma_unavailable"


def test_close_closes_the_music_assistant_connection():
    class Closable(FakeMa):
        closed = 0

        def close(self):
            self.closed += 1

    ma = Closable()
    ctl, _ = make(ma)
    ctl.close()
    assert ma.closed == 1
    make()[0].close()  # an api without close() is fine


def test_the_session_less_control_shares_one_connection_and_never_closes_it(monkeypatch):
    from sendspin_bridge.sync_tuning import ma_api, ma_group_control

    class Closable(FakeMa):
        closed = 0

        def close(self):
            self.closed += 1

    shared = Closable()
    monkeypatch.setattr(ma_api, "shared_ma_api", lambda: shared)
    a, b = ma_group_control.shared_group_control(), ma_group_control.shared_group_control()
    assert a is not b  # per request: no member cache or solo state is shared
    a.close()
    b.close()
    assert shared.closed == 0  # the connection outlives the request


# -- option C: the clicker is queued on the group; solo mutes; no volume fallback ---------------------

URL = "http://10.0.0.5:8080/api/v1/sync/signal/" + "ab" * 32 + ".wav"


def test_snapshot_records_each_members_mute():
    ctl, _ = make()
    snap = ctl.snapshot("grp")
    assert snap.delays == {"spk-bridge": 120, "spk-kiosk": 0, "uni": 0, "cast": 0}
    # a kiosk reports no mute state at all (null): not muted
    assert snap.muted == {"spk-bridge": False, "spk-kiosk": False, "uni": False, "cast": False}


def test_playback_reads_the_group_state_its_media_and_the_member_volumes():
    ctl, _ = make()
    pb = ctl.playback("grp")
    assert pb.state == "playing" and pb.media_uri == "library://track/7"
    assert pb.volumes == {"spk-bridge": 40, "spk-kiosk": 30, "uni": 50, "cast": 20}
    assert not pb.plays(URL)


def test_playback_of_an_unknown_group_is_member_gone():
    ctl, ma = make()
    del ma.players["grp"]
    with pytest.raises(ControlError) as exc:
        ctl.playback("grp")
    assert exc.value.code == "member_gone"


def test_stop_group_stops_the_group_player():
    ctl, ma = make()
    ctl.stop_group("grp")
    assert ("players/cmd/stop", {"player_id": "grp"}) in ma.calls
    assert ctl.playback("grp").state == "idle"


def test_the_clicker_replaces_the_groups_queue_with_the_click_track():
    ctl, ma = make()
    ctl.stop_group("grp")
    ctl.start_clicker("grp", URL)
    assert ("player_queues/play_media", {"queue_id": "grp", "media": URL, "option": "replace"}) in ma.calls
    assert ma.timeouts["player_queues/play_media"] >= 20.0  # MA answers once the item is loaded
    assert ctl.playback("grp").plays(URL)
    assert [i["name"] for i in ma.queue] == ["Sendspin sync clicks"]  # nothing to play on after it


def test_mute_and_unmute_a_member_through_music_assistant():
    ctl, ma = make()
    ctl.mute("uni", True)
    assert ma.players["uni"]["volume_muted"] is True
    ctl.mute("uni", False)
    assert ma.players["uni"]["volume_muted"] is False
    assert [c for c in ma.calls if c[0] == "players/cmd/volume_mute"] == [
        ("players/cmd/volume_mute", {"player_id": "uni", "muted": True}),
        ("players/cmd/volume_mute", {"player_id": "uni", "muted": False}),
    ]


def test_a_player_without_mute_is_named_and_never_sent_a_mute_or_a_volume():
    ctl, ma = make()
    with pytest.raises(ControlError) as exc:
        ctl.mute("spk-kiosk", True)
    assert exc.value.code == "sync_mute_unsupported" and exc.value.member == "spk-kiosk"
    assert "Kiosk" in exc.value.detail
    assert not [c for c in ma.calls if c[0] in ("players/cmd/volume_mute", "players/cmd/volume_set")]
    ctl.mute("spk-kiosk", False)  # nothing to unmute: never muted
    assert not [c for c in ma.calls if c[0] == "players/cmd/volume_mute"]


def test_a_mute_music_assistant_refuses_is_named_too():
    ctl, ma = make()
    ma.players["uni"].pop("supported_features")  # no feature list: the mute is tried, then refused

    real = ma.call

    def refuse(command, args, *, timeout=10.0):
        if command == "players/cmd/volume_mute":
            raise MaApiError("ma_error", "Player Hall does not support feature volume_mute")
        return real(command, args, timeout=timeout)

    ma.call = refuse
    with pytest.raises(ControlError) as exc:
        ctl.mute("uni", True)
    assert exc.value.code == "sync_mute_unsupported" and exc.value.member == "uni"


def test_an_unreachable_music_assistant_is_not_mistaken_for_a_missing_mute():
    ctl, ma = make()
    real = ma.call

    def down(command, args, *, timeout=10.0):
        if command == "players/cmd/volume_mute":
            raise MaApiError("ma_unavailable", "gone")
        return real(command, args, timeout=timeout)

    ma.call = down
    with pytest.raises(ControlError) as exc:
        ctl.mute("uni", True)
    assert exc.value.code == "ma_unavailable" and exc.value.member == "uni"


def test_unmuting_a_player_that_left_music_assistant_is_retryable_not_dropped():
    # after a restart a bridge player may be missing until its daemon registers again: the unmute stays owed
    ctl, ma = make()
    del ma.players["uni"]
    for muted in (False, True):
        with pytest.raises(ControlError) as exc:
            ctl.mute("uni", muted)
        assert exc.value.code == "member_gone" and exc.value.member == "uni"


def test_an_unmute_goes_to_any_muted_player_and_to_no_other():
    ctl, ma = make()
    ma.players["spk-kiosk"]["volume_muted"] = True  # muted, though MA lists no mute feature right now
    ctl.mute("spk-kiosk", False)  # tried; MA's refusal ends it (nothing more can unmute it)
    ctl.mute("uni", False)  # not muted (the operator unmuted it already): nothing to send
    assert [c for c in ma.calls if c[0] == "players/cmd/volume_mute"] == [
        ("players/cmd/volume_mute", {"player_id": "spk-kiosk", "muted": False})
    ]


def test_playback_reads_each_members_mute():
    ctl, ma = make()
    ma.players["cast"]["volume_muted"] = True
    assert ctl.playback("grp").muted == {"spk-bridge": False, "spk-kiosk": False, "uni": False, "cast": True}


def test_set_volume_sets_the_members_volume():
    ctl, ma = make()
    ctl.set_volume("spk-kiosk", 55)
    assert ("players/cmd/volume_set", {"player_id": "spk-kiosk", "volume_level": 55}) in ma.calls


def test_a_whole_session_over_music_assistant_leaves_the_group_stopped_and_unmuted():
    from sendspin_sync.session import SyncSession
    from tests.support.sync_fakes import FakeHost

    class Host(FakeHost):
        def url_for(self, session_id, track="measure"):
            return URL

    ma = FakeMa()
    ctl, _ = make(ma)
    s = SyncSession(session_id="s1", group_id="grp", mode="manual", point="device", control=ctl, host=Host())
    s.start()
    s.stop_group()
    s.start_clicker()
    with pytest.raises(ControlError) as exc:  # the kiosk cannot be muted: the bridge speaker cannot solo
        s.solo("spk-bridge")
    assert exc.value.code == "sync_mute_unsupported" and exc.value.member == "spk-kiosk"
    assert not any(p.get("volume_muted") for p in ma.players.values())
    s.solo("spk-kiosk")  # the kiosk alone: everyone else muted
    assert [ma.players[p]["volume_muted"] for p in ("spk-bridge", "uni", "cast")] == [True, True, True]
    s.cancel()
    assert s.restore_tick() is True
    assert [ma.players[p]["volume_muted"] for p in ("spk-bridge", "uni", "cast")] == [False, False, False]
    assert ma.players["grp"]["playback_state"] == "idle"


def test_a_member_whose_sendspin_player_left_music_assistant_does_not_break_the_group():
    """A web kiosk page that closed leaves its Sendspin protocol player unknown to Music Assistant
    (live, MA dev 2026-10-16: "Player MttX… not found"); the group must still open."""

    class GoneProtocol(FakeMa):
        def call(self, command, args, *, timeout=10.0):
            if command.startswith("config/players/") and args.get("player_id") == "uni-sendspin":
                raise MaApiError("ma_error", "Player uni-sendspin not found")
            return super().call(command, args, timeout=timeout)

    ctl, _ = make(GoneProtocol())
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    assert (by["uni"].adjustable, by["uni"].delay_ms) == (False, None)
    assert (by["spk-bridge"].adjustable, by["spk-bridge"].delay_ms) == (True, 120)


def test_an_unreachable_music_assistant_still_fails_the_group():
    class Down(FakeMa):
        def call(self, command, args, *, timeout=10.0):
            if command == "config/players/get_entries":
                raise MaApiError("ma_unavailable", "connection refused")
            return super().call(command, args, timeout=timeout)

    ctl, _ = make(Down())
    with pytest.raises(ControlError) as exc:
        ctl.members("grp")
    assert exc.value.code == "ma_unavailable"


def test_members_report_what_could_stop_a_measurement_before_it_starts():
    """UX round: mute support, availability, volume and mute state per member, read with the group."""
    ma = FakeMa()
    ma.players["cast"]["available"] = False
    ma.players["uni"]["volume_level"] = 0
    ma.players["spk-bridge"]["volume_muted"] = True
    ctl, _ = make(ma)
    by = {m.ma_player_id: m for m in ctl.members("grp")}
    assert (by["spk-kiosk"].can_mute, by["spk-bridge"].can_mute) == (False, True)
    assert (by["cast"].available, by["spk-bridge"].available) == (False, True)
    assert (by["uni"].volume, by["spk-kiosk"].volume) == (0, 30)
    assert (by["spk-bridge"].muted, by["uni"].muted, by["spk-kiosk"].muted) == (True, False, None)


def test_a_member_music_assistant_does_not_list_is_unavailable_with_nothing_known():
    ma = FakeMa()
    del ma.players["cast"]
    ctl, _ = make(ma)
    cast = next(m for m in ctl.members("grp") if m.ma_player_id == "cast")
    assert (cast.available, cast.can_mute, cast.volume, cast.muted) == (False, None, None, None)
