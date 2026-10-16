import pytest

from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.ma_group_control import AnnouncementSignal, MaGroupControl
from sendspin_sync.ports import ControlError
from tests.support.sync_fakes import GROUPS, FakeMa


def make(ma=None):
    ma = ma or FakeMa()
    ctl = MaGroupControl(
        ma,
        signal=AnnouncementSignal(ma),
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


def test_restore_resets_a_changed_volume():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    ma.players["uni"]["volume_level"] = 80
    ctl.restore(snap)
    assert ("players/cmd/volume_set", {"player_id": "uni", "volume_level": 50}) in ma.calls
    assert [c for c in ma.calls if c[0] == "players/cmd/volume_set"] == [
        ("players/cmd/volume_set", {"player_id": "uni", "volume_level": 50})
    ]


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


def test_solo_of_a_player_outside_the_group_is_member_gone_and_silences_nobody():
    ctl, ma = make()
    with pytest.raises(ControlError) as exc:
        ctl.solo("grp", "someone-else")
    assert exc.value.code == "member_gone"
    assert not [c for c in ma.calls if c[0] in ("players/cmd/volume_mute", "players/cmd/volume_set")]


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


# -- stage 0, option A: solo and stop by volume; the announcement is never stopped ------------------


def volume_sets(ma, since=0):
    return {c[1]["player_id"]: c[1]["volume_level"] for c in ma.calls[since:] if c[0] == "players/cmd/volume_set"}


def test_snapshot_records_each_members_volume_and_mute():
    ctl, _ = make()
    snap = ctl.snapshot("grp")
    assert snap.volumes == {"spk-bridge": 40, "spk-kiosk": 30, "uni": 50, "cast": 20}
    assert snap.muted == {"spk-bridge": False, "spk-kiosk": True, "uni": False, "cast": False} and snap.playing
    assert snap.delays == {"spk-bridge": 120, "spk-kiosk": 0, "uni": 0, "cast": 0}


def test_solo_sets_every_other_member_to_volume_zero_and_the_soloed_one_to_its_level():
    ctl, ma = make()
    ctl.snapshot("grp")
    ma.players["spk-bridge"]["volume_level"] = 0  # silenced by an earlier stop
    ctl.solo("grp", "spk-bridge")
    assert {p: ma.players[p]["volume_level"] for p in ("spk-bridge", "spk-kiosk", "uni", "cast")} == {
        "spk-bridge": 40,
        "spk-kiosk": 0,
        "uni": 0,
        "cast": 0,
    }
    ctl.solo("grp", "uni")
    assert (ma.players["spk-bridge"]["volume_level"], ma.players["uni"]["volume_level"]) == (0, 50)
    # web kiosks refuse volume_mute (UnsupportedFeaturedException): solo never uses it
    assert not [c for c in ma.calls if c[0] == "players/cmd/volume_mute"]


def test_clearing_the_solo_puts_every_member_back_to_its_session_level():
    ctl, ma = make()
    ctl.snapshot("grp")
    ctl.solo("grp", "uni")
    before = len(ma.calls)
    ctl.solo("grp", None)
    assert volume_sets(ma, before) == {"spk-bridge": 40, "spk-kiosk": 30, "cast": 20}  # uni is already there
    assert ma.players["spk-kiosk"]["volume_muted"]  # a mute the user set is left alone


def test_a_control_without_a_snapshot_reads_the_levels_before_it_solos():
    ctl, ma = make()
    ctl.solo("grp", "cast")
    assert (ma.players["cast"]["volume_level"], ma.players["uni"]["volume_level"]) == (20, 0)
    ctl.solo("grp", None)
    assert ma.players["uni"]["volume_level"] == 50  # the level from before the solo, not 0


def test_a_member_without_a_volume_is_left_alone():
    ma = FakeMa()
    ma.players["cast"]["volume_level"] = None
    ctl, _ = make(ma)
    ctl.snapshot("grp")
    ctl.solo("grp", "uni")
    ctl.stop_signal("grp")
    assert "cast" not in volume_sets(ma)


def test_the_announcement_is_fired_not_awaited_and_stop_silences_without_stopping_it():
    # players/cmd/stop does not end an announcement: it blocks until the clip ends
    ctl, ma = make()
    ctl.start_signal("grp", "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav")
    assert ma.calls[-1] == (
        "players/cmd/play_announcement",
        {"player_id": "grp", "url": "http://192.168.1.2:8080/api/v1/sync/signal/abc.wav", "pre_announce": False},
    )
    ctl.stop_signal("grp")
    assert volume_sets(ma) == {"spk-bridge": 0, "spk-kiosk": 0, "uni": 0, "cast": 0}
    assert "players/cmd/stop" not in [c[0] for c in ma.calls]
    assert ma.players["grp"]["playback_state"] == "playing"


def test_restore_puts_back_the_snapshot_volumes_and_never_touches_mute():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    ctl.solo("grp", "spk-bridge")
    ma.players["uni"]["volume_muted"] = True  # the user muted the hall meanwhile
    before = len(ma.calls)
    ctl.restore(snap)
    assert volume_sets(ma, before) == {"spk-kiosk": 30, "uni": 50, "cast": 20}
    assert ma.players["uni"]["volume_muted"]
    assert not [c for c in ma.calls if c[0] == "players/cmd/volume_mute"]


def test_a_fresh_control_restores_from_the_persisted_snapshot_alone():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    for p in ("spk-bridge", "uni"):
        ma.players[p]["volume_level"] = 0  # left at 0 when the bridge restarted
    fresh, _ = make(ma)
    fresh.restore(snap)
    assert (ma.players["spk-bridge"]["volume_level"], ma.players["uni"]["volume_level"]) == (40, 50)


# -- stage 0 E5: a member's Sendspin protocol player keeps its own volume --------------------------


def test_the_snapshot_records_the_sendspin_protocol_players_volume_too():
    ctl, _ = make()
    snap = ctl.snapshot("grp")
    assert snap.protocol_volumes == {"uni-sendspin": 50}  # only members with a separate protocol player


def test_after_the_clip_the_restore_fixes_a_protocol_player_ma_left_at_zero():
    ctl, ma = make()
    snap = ctl.snapshot("grp")
    ctl.solo("grp", "spk-bridge")  # uni (and through it uni-sendspin) to 0
    assert ma.players["uni-sendspin"]["volume_level"] == 0
    ma.announcement_restore({"uni": 50})  # MA restores the player, not its protocol player
    before = len(ma.calls)
    ctl.restore(snap)
    assert volume_sets(ma, before) == {"spk-kiosk": 30, "cast": 20, "uni-sendspin": 50}  # the soloed one is at 40
    assert ma.players["uni-sendspin"]["volume_level"] == 50


def test_the_restore_puts_back_a_player_ma_left_louder_than_before():
    # ENEBY Portable: 46 before the session, 100 after MA's restore
    ma = FakeMa()
    ma.players["uni"]["volume_level"] = 46
    ma.players["uni-sendspin"]["volume_level"] = 46
    ctl, _ = make(ma)
    snap = ctl.snapshot("grp")
    ma.players["uni"]["volume_level"] = 100
    ma.players["uni-sendspin"]["volume_level"] = 100
    ctl.restore(snap)
    assert (ma.players["uni"]["volume_level"], ma.players["uni-sendspin"]["volume_level"]) == (46, 46)


def test_solo_reads_both_levels_back_and_corrects_a_protocol_player_left_at_zero():
    ctl, ma = make()
    ctl.snapshot("grp")
    ma.players["uni-sendspin"]["volume_level"] = 0  # left at 0 by an earlier announcement
    ctl.solo("grp", "uni")
    assert (ma.players["uni"]["volume_level"], ma.players["uni-sendspin"]["volume_level"]) == (50, 50)
    ctl.solo("grp", "spk-bridge")  # a silenced member's protocol player is not touched
    assert ma.players["uni"]["volume_level"] == 0


def test_a_fresh_control_restores_the_protocol_player_from_the_persisted_snapshot():
    from sendspin_sync.models import snapshot_from_dict, to_dict

    ctl, ma = make()
    snap = snapshot_from_dict(to_dict(ctl.snapshot("grp")))
    assert snap.protocol_volumes == {"uni-sendspin": 50}
    ma.players["uni-sendspin"]["volume_level"] = 0
    fresh, _ = make(ma)
    fresh.restore(snap)
    assert ma.players["uni-sendspin"]["volume_level"] == 50
