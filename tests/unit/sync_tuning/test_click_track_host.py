import threading

from sendspin_bridge.sync_tuning.click_track_host import ClickTrackFile, ClickTrackTokens, FastApiClickTrackHost
from sendspin_sync import click_track as ct


def test_tokens_are_random_session_scoped_and_released():
    t = ClickTrackTokens()
    a, b = t.issue("s1"), t.issue("s2")
    assert len(a) == 64 and a != b and t.session_for(a) == "s1"
    assert t.issue("s1") != a and t.session_for(a) is None  # re-issue invalidates the old token
    t.release("s2")
    assert t.session_for(b) is None
    assert t.session_for("nope") is None


class RecordingTrack:
    def __init__(self, calls):
        self.calls = calls

    def ensure(self):
        self.calls.append("ensure")


def test_url_uses_the_lan_address_and_web_port():
    t = ClickTrackTokens()
    host = FastApiClickTrackHost(tokens=t, base_url=lambda: "http://192.168.1.2:8080", track=RecordingTrack([]))
    url = host.url_for("s1")
    assert url.startswith("http://192.168.1.2:8080/api/v1/sync/signal/") and url.endswith(".wav") and "tts" not in url
    host.release("s1")
    assert t.session_for(url.rsplit("/", 1)[1][:-4]) is None


def test_the_url_is_handed_out_only_once_the_file_is_ready():
    calls = []

    def base_url():
        calls.append("url")
        return "http://h:1"

    host = FastApiClickTrackHost(tokens=ClickTrackTokens(), base_url=base_url, track=RecordingTrack(calls))
    host.url_for("s1")
    assert calls == ["ensure", "url"]  # Music Assistant gets the URL after the file exists


def test_the_track_is_written_once_to_a_file_and_kept(tmp_path):
    renders = []

    def writer(out, **kw):
        renders.append(kw)
        ct.write_wav(out, **kw)

    track = ClickTrackFile(directory=lambda: tmp_path, duration_s=3, writer=writer)
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        track.ensure()

    threads = [threading.Thread(target=go) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(renders) == 1  # concurrent first fetches render once
    assert track.path().read_bytes() == ct.render_wav(duration_s=3)
    assert [p.name for p in tmp_path.iterdir()] == [track.path().name]  # no temp file left behind
    host = FastApiClickTrackHost(tokens=ClickTrackTokens(), base_url=lambda: "http://h:1", track=track)
    host.url_for("s1")
    host.release("s1")
    assert track.path().exists() and len(renders) == 1  # kept on disk: the next session renders nothing
    track.ensure()
    assert len(renders) == 1


def test_a_truncated_file_is_written_again(tmp_path):
    track = ClickTrackFile(directory=lambda: tmp_path, duration_s=2)
    tmp_path.joinpath(track.path().name).write_bytes(b"RIFF")
    assert track.ensure() == track.path()
    assert track.path().read_bytes() == ct.render_wav(duration_s=2)


def test_base_url_is_logged_once_and_the_loopback_fallback_warns(monkeypatch, caplog):
    import logging

    from sendspin_bridge.sync_tuning import click_track_host as cth

    monkeypatch.setattr(cth, "_announced_base_url", None)
    monkeypatch.setattr(cth, "resolve_web_port", lambda: 8080)
    monkeypatch.setattr(cth, "get_local_ip", lambda: "192.168.1.5")
    with caplog.at_level(logging.INFO, logger=cth.__name__):
        assert cth.default_base_url() == "http://192.168.1.5:8080"
        cth.default_base_url()
    assert [r.levelno for r in caplog.records] == [logging.INFO] and "192.168.1.5:8080" in caplog.text
    caplog.clear()
    monkeypatch.setattr(cth, "get_local_ip", lambda: None)
    with caplog.at_level(logging.INFO, logger=cth.__name__):
        assert cth.default_base_url() == "http://127.0.0.1:8080"
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_base_url_uses_the_interface_that_routes_to_music_assistant(monkeypatch):
    """A VPN can own the default route; Music Assistant must get the address on its own network."""
    from sendspin_bridge.sync_tuning import click_track_host as cth

    monkeypatch.setattr(cth, "_announced_base_url", None)
    monkeypatch.setattr(cth, "resolve_web_port", lambda: 8080)
    monkeypatch.setattr(cth, "get_local_ip", lambda: "10.252.122.246")  # the default route's (VPN) address
    monkeypatch.setattr(cth, "get_ma_api_credentials", lambda: ("http://127.0.0.1:8095", "token"))
    assert cth.default_base_url() == "http://127.0.0.1:8080"


def test_base_url_falls_back_to_the_default_route_without_a_music_assistant_url(monkeypatch):
    from sendspin_bridge.sync_tuning import click_track_host as cth

    monkeypatch.setattr(cth, "_announced_base_url", None)
    monkeypatch.setattr(cth, "resolve_web_port", lambda: 8080)
    monkeypatch.setattr(cth, "get_local_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(cth, "get_ma_api_credentials", lambda: ("", ""))
    assert cth.default_base_url() == "http://192.168.1.5:8080"


def test_a_session_keeps_one_url_so_a_clicker_started_again_never_cuts_off_the_one_playing():
    from sendspin_bridge.sync_tuning import click_track_host as host_mod

    tokens = host_mod.ClickTrackTokens()

    class Ready:
        def ensure(self):
            return None

    host = host_mod.FastApiClickTrackHost(tokens=tokens, base_url=lambda: "http://10.0.0.5:8080", track=Ready())
    first = host.url_for("s1")
    assert host.url_for("s1") == first and tokens.session_for(first.rsplit("/", 1)[-1][:-4]) == "s1"
    host.release("s1")
    assert host.url_for("s1") != first


# -- the listening sounds of manual tuning ------------------------------------------------------


def test_each_listening_sound_has_its_url_under_the_session_token():
    from sendspin_sync import listening_tracks as lt

    tokens = ClickTrackTokens()
    calls = []
    host = FastApiClickTrackHost(tokens=tokens, base_url=lambda: "http://10.0.0.5:8080", track=RecordingTrack(calls))
    measure = host.url_for("s1")
    token = measure.rsplit("/", 1)[-1][:-4]
    assert measure == f"http://10.0.0.5:8080/api/v1/sync/signal/{token}.wav"  # unchanged for the measurement
    for kind in lt.KINDS:
        assert host.url_for("s1", kind) == f"http://10.0.0.5:8080/api/v1/sync/signal/{token}/{kind}.wav"
    assert calls == ["ensure"] * 4  # each one only once its file is ready
    other = host.url_for("s2", "noise")
    assert token not in other and tokens.session_for(other.split("/")[-2]) == "s2"
    host.release("s1")
    assert tokens.session_for(token) is None


def test_each_listening_sound_is_its_own_file(tmp_path):
    from sendspin_sync import listening_tracks as lt

    files = {kind: ClickTrackFile(kind, directory=lambda: tmp_path, duration_s=2) for kind in lt.KINDS}
    measure = ClickTrackFile(directory=lambda: tmp_path, duration_s=2)
    paths = {track.ensure() for track in (*files.values(), measure)}
    assert len(paths) == 4
    for kind, track in files.items():
        assert track.path().read_bytes() == lt.render_wav(kind, duration_s=2)
    assert measure.path().read_bytes() == ct.render_wav(duration_s=2)
    assert measure.path().name.startswith("click-") and files["noise"].path().name.startswith("listen-noise-")


def test_a_changed_generator_writes_a_new_file_and_removes_the_old_one(tmp_path, monkeypatch):
    """The file name carries a digest of the generator: an upgrade that changes the sound never serves the
    old file kept in /var/tmp."""
    from sendspin_bridge.sync_tuning import click_track_host as cth

    old = ClickTrackFile("clicks", directory=lambda: tmp_path, duration_s=2)
    old_path = old.ensure()
    keep = ClickTrackFile("noise", directory=lambda: tmp_path, duration_s=2).ensure()
    measure = ClickTrackFile(directory=lambda: tmp_path, duration_s=2).ensure()
    monkeypatch.setattr(cth, "generator_version", lambda kind: "feedc0de00" if kind == "clicks" else cth._digest(kind))
    new = ClickTrackFile("clicks", directory=lambda: tmp_path, duration_s=2)
    assert new.path() != old_path and "feedc0de00" in new.path().name
    new.ensure()
    assert new.path().exists() and not old_path.exists()
    assert keep.exists() and measure.exists()  # other sounds are left alone


def test_the_version_follows_the_generator_source():
    from sendspin_bridge.sync_tuning import click_track_host as cth

    assert cth.generator_version("measure") != cth.generator_version("clicks")
    assert cth.generator_version("clicks") == cth.generator_version("noise")  # one module makes all three
    assert len(cth.generator_version("measure")) == 10


def test_stale_partial_files_are_removed_before_a_write(tmp_path):
    import os
    import time

    stale = tmp_path / ".click-abc.part"
    stale.write_bytes(b"x" * 10)
    old = time.time() - 3600
    os.utime(stale, (old, old))
    fresh = tmp_path / ".listen-noise-def.part"  # maybe another worker writing right now
    fresh.write_bytes(b"x")
    ClickTrackFile("clicks", directory=lambda: tmp_path, duration_s=1).ensure()
    assert not stale.exists() and fresh.exists()


def test_a_full_disk_is_a_coded_error(tmp_path):
    import errno

    import pytest

    from sendspin_sync.ports import ControlError

    def full(out, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    def broken(out, **kw):
        raise OSError(errno.EACCES, "Permission denied")

    with pytest.raises(ControlError) as exc:
        ClickTrackFile(directory=lambda: tmp_path, duration_s=1, writer=full).ensure()
    assert exc.value.code == "disk_full"
    with pytest.raises(ControlError) as exc:
        ClickTrackFile(directory=lambda: tmp_path, duration_s=1, writer=broken).ensure()
    assert exc.value.code == "track_unavailable"
    assert list(tmp_path.iterdir()) == []  # no partial file left behind


def test_prepare_writes_the_file_without_issuing_a_token():
    tokens = ClickTrackTokens()
    calls = []
    host = FastApiClickTrackHost(tokens=tokens, base_url=lambda: "http://h:1", track=RecordingTrack(calls))
    host.prepare("noise")
    assert calls == ["ensure"] and tokens.current("s1") is None
