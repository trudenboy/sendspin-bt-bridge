"""The click track is fetched by Music Assistant without credentials; only the token guards it."""

from sendspin_bridge.sync_tuning import click_track_host as host
from tests.support.api_client import make_client


def test_signal_is_public_with_a_live_token_and_404_otherwise(monkeypatch):
    tokens = host.ClickTrackTokens()
    monkeypatch.setattr(host, "tokens", tokens)
    token = tokens.issue("s1")
    client = make_client({"AUTH_ENABLED": True})  # no session, no bearer
    resp = client.get(f"/api/v1/sync/signal/{token}.wav")
    assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav" and resp.content[:4] == b"RIFF"
    assert resp.headers["Cache-Control"] == "no-store"
    unknown = client.get("/api/v1/sync/signal/" + "0" * 64 + ".wav")
    assert unknown.status_code == 404
    tokens.release("s1")
    released = client.get(f"/api/v1/sync/signal/{token}.wav")
    assert released.status_code == 404
    assert released.content == unknown.content  # a released token is indistinguishable from an unknown one


def test_signal_is_reachable_through_ingress_and_not_swallowed_by_the_spa(monkeypatch, tmp_path):
    tokens = host.ClickTrackTokens()
    monkeypatch.setattr(host, "tokens", tokens)
    token = tokens.issue("s1")
    (tmp_path / "index.html").write_text("<html>spa</html>")
    client = make_client(spa_dir=tmp_path)
    assert client.get(f"/api/v1/sync/signal/{token}.wav").content[:4] == b"RIFF"
    bad = client.get("/api/v1/sync/signal/" + "1" * 64 + ".wav")
    assert bad.status_code == 404 and b"spa" not in bad.content
    other = client.get("/api/v1/sync/other")
    assert other.status_code == 404 and b"spa" not in other.content
    ingress_client = make_client(peer="172.30.32.2", spa_dir=tmp_path)
    ingress = ingress_client.get(
        f"/api/v1/sync/signal/{token}.wav", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"}
    )
    assert ingress.status_code == 200 and ingress.content[:4] == b"RIFF"


def test_signal_streams_the_prepared_file_without_compressing_it(monkeypatch, tmp_path):
    from sendspin_sync import click_track as ct

    tokens = host.ClickTrackTokens()
    monkeypatch.setattr(host, "tokens", tokens)
    track = host.ClickTrackFile(directory=lambda: tmp_path, duration_s=3)
    monkeypatch.setattr(host, "track", track)
    token = tokens.issue("s1")
    resp = make_client().get(f"/api/v1/sync/signal/{token}.wav", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200 and resp.headers.get("content-encoding") in (None, "identity")
    assert int(resp.headers["content-length"]) == ct.wav_size(duration_s=3)
    assert resp.content == track.path().read_bytes() == ct.render_wav(duration_s=3)
    assert resp.headers["Cache-Control"] == "no-store"


def test_listening_sounds_are_served_under_the_session_token(monkeypatch, tmp_path):
    from sendspin_sync import listening_tracks as lt

    tokens = host.ClickTrackTokens()
    monkeypatch.setattr(host, "tokens", tokens)
    monkeypatch.setattr(
        host, "tracks", {kind: host.ClickTrackFile(kind, directory=lambda: tmp_path, duration_s=2) for kind in lt.KINDS}
    )
    token = tokens.issue("s1")
    client = make_client({"AUTH_ENABLED": True})  # Music Assistant fetches without credentials
    for kind in lt.KINDS:
        resp = client.get(f"/api/v1/sync/signal/{token}/{kind}.wav")
        assert resp.status_code == 200 and resp.headers["content-type"] == "audio/wav", kind
        assert resp.content == lt.render_wav(kind, duration_s=2) and resp.headers["Cache-Control"] == "no-store"
    assert client.get(f"/api/v1/sync/signal/{token}/measure.wav").status_code == 404
    assert client.get(f"/api/v1/sync/signal/{token}/other.wav").status_code == 404
    assert client.get("/api/v1/sync/signal/" + "0" * 64 + "/clicks.wav").status_code == 404
    tokens.release("s1")
    assert client.get(f"/api/v1/sync/signal/{token}/clicks.wav").status_code == 404


def test_a_sound_file_that_cannot_be_written_is_a_problem_not_a_crash(monkeypatch):
    from sendspin_sync.ports import ControlError

    tokens = host.ClickTrackTokens()
    monkeypatch.setattr(host, "tokens", tokens)

    class Full:
        def ensure(self):
            raise ControlError("disk_full", "No space left for the sound file")

    monkeypatch.setattr(host, "tracks", {"clicks": Full()})
    token = tokens.issue("s1")
    resp = make_client().get(f"/api/v1/sync/signal/{token}/clicks.wav")
    assert resp.status_code == 507 and resp.json()["code"] == "disk_full"
