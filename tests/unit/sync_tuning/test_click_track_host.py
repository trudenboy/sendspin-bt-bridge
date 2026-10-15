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
