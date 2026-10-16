import pytest

from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.ma_group_control import AnnouncementSignal, MaGroupControl, MaMuteSolo
from sendspin_sync.ports import ControlError
from tests.support.sync_fakes import GROUPS, FakeMa


def make(ma=None):
    ma = ma or FakeMa()
    ctl = MaGroupControl(
        ma,
        signal=AnnouncementSignal(ma),
        solo=MaMuteSolo(ma),
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


def test_snapshot_solo_and_restore_only_touch_what_changed():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    assert snap.muted == {"spk-bridge": False, "spk-kiosk": True, "uni": False, "cast": False} and snap.playing
    assert snap.delays == {"spk-bridge": 120, "spk-kiosk": 0, "uni": 0, "cast": 0}
    ctl.solo("grp", "spk-bridge")
    assert ma.players["uni"]["volume_muted"] and ma.players["cast"]["volume_muted"]
    assert not ma.players["spk-bridge"]["volume_muted"]
    ctl.solo("grp", None)
    assert not ma.players["uni"]["volume_muted"] and ma.players["spk-kiosk"]["volume_muted"]  # kiosk stays muted
    ma.players["uni"]["volume_muted"] = True  # pretend MA's own restore missed it
    ctl.restore(snap)
    assert not ma.players["uni"]["volume_muted"] and ma.players["spk-kiosk"]["volume_muted"]
    assert ("players/cmd/volume_mute", {"player_id": "spk-kiosk", "muted": False}) not in ma.calls


def test_solo_switch_unmutes_the_previous_soloist_target_only_if_we_muted_it():
    ctl, ma = make()
    ctl.solo("grp", "spk-bridge")
    ctl.solo("grp", "uni")
    assert not ma.players["uni"]["volume_muted"] and ma.players["spk-bridge"]["volume_muted"]
    assert ma.players["spk-kiosk"]["volume_muted"]
    ctl.solo("grp", None)
    assert [p for p in ("spk-bridge", "uni", "cast") if ma.players[p]["volume_muted"]] == []
    assert ma.players["spk-kiosk"]["volume_muted"]


def test_restore_resets_a_changed_volume():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    ma.players["uni"]["volume_level"] = 80
    ctl.restore(snap)
    assert ("players/cmd/volume_set", {"player_id": "uni", "volume_level": 50}) in ma.calls
    assert [c for c in ma.calls if c[0] == "players/cmd/volume_set"] == [
        ("players/cmd/volume_set", {"player_id": "uni", "volume_level": 50})
    ]


def test_announcement_signal_is_fired_not_awaited_and_stop_stops_the_group():
    ctl, ma = make()
    ctl.start_signal("grp", "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav")
    assert ma.calls[-1] == (
        "players/cmd/play_announcement",
        {"player_id": "grp", "url": "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav", "pre_announce": False},
    )
    ctl.stop_signal("grp")
    assert ma.calls[-1] == ("players/cmd/stop", {"player_id": "grp"}) and ma.players["grp"]["playback_state"] == "idle"


def test_ma_errors_surface_as_control_errors():
    class Down(FakeMa):
        def call(self, command, args, *, timeout=10.0):
            raise MaApiError("ma_unavailable", "gone")

        def fire(self, command, args):
            raise MaApiError("ma_unavailable", "gone")

    ctl, _ = make(Down())
    for action in (lambda: ctl.members("grp"), lambda: ctl.start_signal("grp", "u"), lambda: ctl.stop_signal("grp")):
        with pytest.raises(ControlError) as exc:
            action()
        assert exc.value.code == "ma_unavailable"


def test_default_group_control_needs_credentials(monkeypatch):
    from sendspin_bridge.sync_tuning import ma_api, ma_group_control

    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("", ""))
    with pytest.raises(MaApiError) as exc:
        ma_group_control.default_group_control()
    assert exc.value.code == "ma_unavailable"


def test_stopping_the_signal_waits_out_the_group_playback_lock():
    # MA holds the group's playback lock for the whole announcement; cmd_stop waits up to 30 s for it
    ctl, ma = make()
    ctl.start_signal("grp", "http://bridge/x.wav")
    ctl.stop_signal("grp")
    assert ma.timeouts["players/cmd/stop"] >= 40.0


def test_solo_of_a_player_outside_the_group_is_member_gone_and_mutes_nobody():
    ctl, ma = make()
    with pytest.raises(ControlError) as exc:
        ctl.solo("grp", "someone-else")
    assert exc.value.code == "member_gone"
    assert not [c for c in ma.calls if c[0] == "players/cmd/volume_mute"]


def test_restore_forgets_what_the_solo_muted():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    ctl.solo("grp", "spk-bridge")
    ctl.restore(snap)
    assert [p for p in ("spk-bridge", "uni", "cast") if ma.players[p]["volume_muted"]] == []
    ma.players["uni"]["volume_muted"] = True  # the user mutes the hall after the session
    before = len(ma.calls)
    ctl.solo("grp", None)  # a late clear must not undo the user's mute
    assert ma.players["uni"]["volume_muted"]
    assert not [c for c in ma.calls[before:] if c[0] == "players/cmd/volume_mute"]


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
