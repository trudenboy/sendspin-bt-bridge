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
