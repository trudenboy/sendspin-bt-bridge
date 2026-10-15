from sendspin_bridge.sync_tuning.click_track_host import ClickTrackTokens, FastApiClickTrackHost, track_wav


def test_tokens_are_random_session_scoped_and_released():
    t = ClickTrackTokens()
    a, b = t.issue("s1"), t.issue("s2")
    assert len(a) == 64 and a != b and t.session_for(a) == "s1"
    assert t.issue("s1") != a and t.session_for(a) is None  # re-issue invalidates the old token
    t.release("s2")
    assert t.session_for(b) is None
    assert t.session_for("nope") is None


def test_url_uses_the_lan_address_and_web_port():
    t = ClickTrackTokens()
    host = FastApiClickTrackHost(tokens=t, base_url=lambda: "http://192.168.1.2:8080")
    url = host.url_for("s1")
    assert url.startswith("http://192.168.1.2:8080/api/v1/sync/signal/") and url.endswith(".wav") and "tts" not in url
    host.release("s1")
    assert t.session_for(url.rsplit("/", 1)[1][:-4]) is None


def test_track_is_rendered_once():
    assert track_wav() is track_wav() and track_wav()[:4] == b"RIFF"


def test_the_cached_track_is_dropped_once_no_token_is_live():
    t = ClickTrackTokens()
    host = FastApiClickTrackHost(tokens=t, base_url=lambda: "http://h:1")
    host.url_for("s1")
    host.url_for("s2")
    track_wav()
    host.release("s1")
    assert track_wav.cache_info().currsize == 1  # s2 still plays it
    host.release("s2")
    assert track_wav.cache_info().currsize == 0


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
