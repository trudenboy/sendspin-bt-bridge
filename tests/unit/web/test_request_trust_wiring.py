"""Every consumer asks the same policy, and asks it after a settings save.

`interface.py` built its trust set at import time.  `TRUSTED_PROXIES` is
writable through `POST /api/config`, so from the first settings save onwards
the auth gate and the login rate-limiter disagreed about who was trusted —
until a restart.  With `TRUSTED_PROXIES` in the POST allow-list, an
authenticated user could widen the set and have `/api/auth/ha-pair` honour it
while `_check_auth` still refused, which makes the divergence invisible in
testing.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from sendspin_bridge.web.request_identity import current_trust_policy

INGRESS = "172.30.33.7"
LATER = "10.9.9.9"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    return tmp_path / "config.json"


@pytest.fixture()
def app():
    return Flask(__name__)


def test_the_policy_is_built_from_the_live_config(app, monkeypatch, _isolated_config):
    import sendspin_bridge.web.request_identity as mod

    monkeypatch.setattr(mod, "load_config", lambda: {"TRUSTED_PROXIES": [LATER]})

    with app.test_request_context("/"):
        assert current_trust_policy().is_trusted(LATER) is True


def test_a_settings_save_is_visible_to_the_next_request(app, monkeypatch):
    """The import-time snapshot went stale here and stayed stale."""
    import sendspin_bridge.web.request_identity as mod

    config = {"TRUSTED_PROXIES": []}
    monkeypatch.setattr(mod, "load_config", lambda: config)

    with app.test_request_context("/"):
        assert current_trust_policy().is_trusted(LATER) is False

    config["TRUSTED_PROXIES"] = [LATER]

    with app.test_request_context("/"):
        assert current_trust_policy().is_trusted(LATER) is True


def test_one_request_reads_the_config_once(app, monkeypatch):
    """Several consumers ask per request; they must not each hit the disk."""
    import sendspin_bridge.web.request_identity as mod

    reads: list[int] = []

    def _counting_load():
        reads.append(1)
        return {"TRUSTED_PROXIES": [LATER]}

    monkeypatch.setattr(mod, "load_config", _counting_load)

    with app.test_request_context("/"):
        first = current_trust_policy()
        second = current_trust_policy()

    assert first is second
    assert len(reads) == 1


def test_outside_a_request_the_policy_still_answers(monkeypatch):
    """Startup logging and background tasks have no request context."""
    import sendspin_bridge.web.request_identity as mod

    monkeypatch.setattr(mod, "load_config", lambda: {"TRUSTED_PROXIES": [LATER]})

    assert current_trust_policy().is_trusted(LATER) is True


def test_the_auth_gate_and_the_rate_limiter_agree(app, monkeypatch):
    """The property `trusted_proxies.py` was written to guarantee."""
    import sendspin_bridge.web.interface as interface
    import sendspin_bridge.web.request_identity as mod
    import sendspin_bridge.web.routes.auth as auth

    monkeypatch.setattr(mod, "load_config", lambda: {"TRUSTED_PROXIES": [LATER]})

    with app.test_request_context("/", environ_base={"REMOTE_ADDR": LATER}):
        assert interface._peer_trusted(LATER) is True
        assert auth._peer_is_trusted(LATER) is True


def test_the_ingress_peer_is_trusted_by_range_everywhere(app, monkeypatch):
    import sendspin_bridge.web.interface as interface
    import sendspin_bridge.web.request_identity as mod
    import sendspin_bridge.web.routes.auth as auth

    monkeypatch.setattr(mod, "load_config", lambda: {})

    with app.test_request_context("/"):
        assert interface._peer_trusted(INGRESS) is True
        assert auth._peer_is_trusted(INGRESS) is True


def test_the_rate_limit_bucket_uses_the_forwarded_client(app, monkeypatch):
    import sendspin_bridge.web.request_identity as mod
    import sendspin_bridge.web.routes.auth as auth

    monkeypatch.setattr(mod, "load_config", lambda: {})

    with app.test_request_context(
        "/login",
        method="POST",
        environ_base={"REMOTE_ADDR": INGRESS},
        headers={"X-Forwarded-For": "192.168.10.55"},
        data={"username": "alice"},
    ):
        assert auth._get_rate_limit_client_id() == "192.168.10.55"


def test_the_bug_report_limiter_buckets_by_the_same_policy(app, monkeypatch):
    """The fourth construction of the trust set lived in this route.

    It rebuilt the defaults, re-read `TRUSTED_PROXIES` and called the
    resolver itself, so it could drift from every other consumer.  Here the
    policy trusts a proxy the route's own config does not name: bucketing by
    the forwarded client can only happen if the route asks the policy.
    """
    import sendspin_bridge.services.diagnostics.github_issue_proxy as issue_proxy
    import sendspin_bridge.web.request_identity as identity
    import sendspin_bridge.web.routes.api_status as api_status

    monkeypatch.setattr(identity, "load_config", lambda: {"TRUSTED_PROXIES": [LATER]})
    monkeypatch.setattr(api_status, "load_config", lambda: {})

    buckets: list[str] = []

    class _Proxy:
        available = True

        def check_rate_limit(self, client_ip):
            buckets.append(client_ip)
            return "rate limited"

    monkeypatch.setattr(issue_proxy, "get_issue_proxy", lambda: _Proxy())

    app.register_blueprint(api_status.status_bp)
    app.test_client().post(
        "/api/bugreport/submit",
        json={"title": "Speaker drops", "description": "It drops every hour.", "email": "a@b.c"},
        environ_base={"REMOTE_ADDR": LATER},
        headers={"X-Forwarded-For": "192.168.10.55"},
    )

    assert buckets == ["192.168.10.55"]


def test_the_middleware_and_the_request_share_one_policy(monkeypatch):
    """The ingress middleware runs before Flask pushes a request context.

    It therefore could not read the per-request cache and built a throwaway
    policy of its own, so one request read the trust configuration twice —
    and a save landing between the two reads gave the ingress decision and
    the auth decision different answers.
    """
    import sendspin_bridge.web.interface as interface
    import sendspin_bridge.web.request_identity as identity

    reads: list[int] = []

    def _counting_load():
        reads.append(1)
        return {"TRUSTED_PROXIES": [LATER]}

    monkeypatch.setattr(identity, "load_config", _counting_load)

    seen: list[object] = []

    def _app(environ, _start_response):
        from flask import Flask

        flask_app = Flask(__name__)
        with flask_app.request_context(environ):
            seen.append(identity.current_trust_policy())
        return []

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "wsgi.url_scheme": "http",
        "REMOTE_ADDR": INGRESS,
        "HTTP_X_INGRESS_PATH": "/api/hassio_ingress/abc",
    }
    middleware = interface._IngressMiddleware(_app)
    middleware(environ, lambda *_a, **_kw: None)

    assert environ["SCRIPT_NAME"] == "/api/hassio_ingress/abc"
    assert len(reads) == 1, "the trust configuration was read twice in one request"
    assert seen and seen[0] is environ.get("sendspin.trust_policy")
