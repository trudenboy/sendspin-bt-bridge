"""Follow-up hardening tests for the Music Assistant sign-in.

Covers two issues surfaced during the v2.58.0-rc.1 review:

* bug_004 — ``_get_ma_oauth_bootstrap`` used to trust the ``ha_base`` host
  parsed out of the MA server's ``authorization_url`` without re-running
  the SSRF check.  A malicious MA could redirect us at an internal HA.

* bug_003 — the HA auth popup page rendered ``ma_url`` into an inline
  ``<script>`` block via ``json.dumps``, which does not escape
  ``</script>`` — enabling reflected XSS.
"""

from __future__ import annotations

import json
import re

import pytest

from sendspin_bridge.application.music_assistant import auth as ma_auth_module

PAGE = "/api/v1/music-assistant/session/ha-auth-page"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def client():
    from tests.support.api_client import make_client

    return make_client()


def _csp_nonce(resp) -> str:
    match = re.search(r"'nonce-([^']+)'", resp.headers["Content-Security-Policy"])
    assert match, resp.headers["Content-Security-Policy"]
    return match.group(1)


class TestOauthBootstrapHaBaseValidated:
    """bug_004: reject MA-reported ha_base that fails is_safe_external_url."""

    def test_private_ha_base_from_ma_rejected(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

        checked: list[str] = []

        def _fake_is_safe(url: str) -> bool:
            checked.append(url)
            # ma_url itself is safe; ha_base returned by MA is private.
            return "127.0.0.1" not in url and "10." not in url.split("://", 1)[-1]

        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", _fake_is_safe)

        class _FakeResp:
            def __init__(self, location: str) -> None:
                self.headers = {"Location": location}

            def read(self):
                return b""

            def geturl(self):
                return ""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        evil_auth_url = "http://10.0.0.5:8123/auth/authorize?client_id=c&redirect_uri=http://x/&state=s"

        class _FakeOpener:
            def open(self, *a, **kw):
                return _FakeResp(evil_auth_url)

        monkeypatch.setattr(ma_auth_module, "safe_build_opener", lambda *a, **kw: _FakeOpener())

        # JSON-RPC fallback path exists: make safe_urlopen fail so the
        # handler falls through to the final "unavailable" error instead
        # of attempting real network I/O.
        def _raise(*a, **kw):
            raise ConnectionError("stub")

        monkeypatch.setattr(ma_auth_module, "safe_urlopen", _raise)

        oauth_info, err = ma_auth_module._get_ma_oauth_bootstrap("http://ma.example.com:8095")
        assert oauth_info is None
        assert err  # surfaced as unavailable
        # The ha_base was checked (not only ma_url)
        assert any(host in url for url in checked for host in ("10.0.0.5",))


class TestHaAuthPageNoScriptBreakout:
    """bug_003: ``</script>`` in ma_url must not escape the inline script."""

    def test_script_close_sequence_is_escaped(self, client, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        # Bypass SSRF check for this purely-rendering test
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)

        # Include an inline-script breakout attempt inside the URL
        payload = 'http://a.example/</script><script>alert("xss")</script>'
        resp = client.get(PAGE, params={"ma_url": payload})
        assert resp.status_code == 200
        body = resp.text

        # The raw </script> must not appear inside the response (it was
        # escaped to <\/script>).  If it does, the page renders two
        # <script> blocks and the second one executes.
        assert "</script><script>" not in body
        # The escaped form should be present
        assert "<\\/script>" in body


class TestHaAuthPageCspCompliance:
    """bug: popup inline script was blocked by per-request CSP ``script-src``
    nonce — the Sign-in form fell back to the browser's default GET submit,
    re-opened the popup URL without ``ma_url``, and the user saw their
    credentials silently "swallowed".
    """

    def test_popup_inline_script_has_csp_nonce(self, client, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)

        resp = client.get(PAGE, params={"ma_url": "http://a.example"})
        assert resp.status_code == 200
        body = resp.text

        # The popup's inline <script> must carry the nonce its own CSP
        # (`script-src 'self' 'nonce-<value>'`) allows, or it is blocked.
        assert f'nonce="{_csp_nonce(resp)}"' in body

    def test_popup_has_no_inline_event_handlers(self, client, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)

        resp = client.get(PAGE, params={"ma_url": "http://a.example"})
        body = resp.text

        # Inline event-handler attributes (``onsubmit=``, ``onclick=``, …)
        # are blocked by a CSP that uses a nonce without ``'unsafe-inline'``.
        # The popup must wire handlers via addEventListener instead.
        import re

        inline_handlers = re.findall(
            r"""\son[a-z]+\s*=\s*["']""",
            body,
            flags=re.IGNORECASE,
        )
        assert not inline_handlers, f"Popup still has inline event handlers: {inline_handlers}"

    def test_popup_placeholder_is_substituted(self, client, monkeypatch):
        """Regression guard: the raw placeholders must never leak into the
        rendered HTML."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)

        resp = client.get(PAGE, params={"ma_url": "http://a.example"})
        body = resp.text

        assert "__CSP_NONCE__" not in body
        assert "__CSRF_TOKEN__" not in body
        assert "__MA_URL__" not in body
