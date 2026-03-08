"""Tests for _IngressMiddleware in web_interface.py.

web_interface imports route blueprints that may require Python 3.11+
features (datetime.UTC).  We stub those modules so the middleware class
can be imported on any Python version the test runner uses.
"""

import json
import sys
import types

import pytest

# Stub route blueprint modules so web_interface can be imported regardless of
# the Python version available on the test runner.
for _mod_name in ("routes.api", "routes.auth", "routes.views"):
    if _mod_name not in sys.modules:
        _stub = types.ModuleType(_mod_name)
        # web_interface expects blueprint objects with a register() method.
        _bp = type("FakeBP", (), {"register": lambda *a, **kw: None})()
        for _attr in ("api_bp", "auth_bp", "views_bp"):
            setattr(_stub, _attr, _bp)
        sys.modules[_mod_name] = _stub


@pytest.fixture(autouse=True)
def _mock_config(tmp_path, monkeypatch):
    """Ensure config points at a temp directory so web_interface can import."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def _default_trusted(monkeypatch):
    """Pin _TRUSTED_PROXIES to the well-known defaults for deterministic tests."""
    import web_interface

    monkeypatch.setattr(web_interface, "_TRUSTED_PROXIES", {"127.0.0.1", "::1", "172.30.32.2"})


def _make_environ(remote_addr="127.0.0.1", path_info="/", ingress_path=None):
    """Build a minimal WSGI environ dict."""
    env = {
        "REQUEST_METHOD": "GET",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "PATH_INFO": path_info,
        "SCRIPT_NAME": "",
        "REMOTE_ADDR": remote_addr,
    }
    if ingress_path is not None:
        env["HTTP_X_INGRESS_PATH"] = ingress_path
    return env


def _dummy_app(environ, start_response):
    start_response("200 OK", [])
    return [b"ok"]


# ------------------------------------------------------------------


@pytest.mark.usefixtures("_default_trusted")
def test_sets_script_name_from_trusted_proxy():
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return _dummy_app(environ, start_response)

    mw = _IngressMiddleware(spy)
    env = _make_environ(remote_addr="127.0.0.1", ingress_path="/api/hassio_ingress/abc")
    mw(env, lambda *a: None)

    assert captured["SCRIPT_NAME"] == "/api/hassio_ingress/abc"


@pytest.mark.usefixtures("_default_trusted")
def test_ignores_untrusted_proxy():
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return _dummy_app(environ, start_response)

    mw = _IngressMiddleware(spy)
    env = _make_environ(remote_addr="10.0.0.1", ingress_path="/api/hassio_ingress/abc")
    mw(env, lambda *a: None)

    assert captured["SCRIPT_NAME"] == ""


@pytest.mark.usefixtures("_default_trusted")
def test_strips_ingress_prefix_from_path_info():
    """SCRIPT_NAME is set; PATH_INFO is left unchanged by the middleware."""
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return _dummy_app(environ, start_response)

    mw = _IngressMiddleware(spy)
    env = _make_environ(
        remote_addr="127.0.0.1",
        path_info="/status",
        ingress_path="/api/hassio_ingress/abc",
    )
    mw(env, lambda *a: None)

    assert captured["SCRIPT_NAME"] == "/api/hassio_ingress/abc"
    assert captured["PATH_INFO"] == "/status"


@pytest.mark.usefixtures("_default_trusted")
def test_no_header_passthrough():
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return _dummy_app(environ, start_response)

    mw = _IngressMiddleware(spy)
    env = _make_environ(remote_addr="127.0.0.1")  # no ingress header
    mw(env, lambda *a: None)

    assert captured["SCRIPT_NAME"] == ""
    assert "HTTP_X_INGRESS_PATH" not in captured


@pytest.mark.usefixtures("_default_trusted")
def test_ipv6_trusted():
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return _dummy_app(environ, start_response)

    mw = _IngressMiddleware(spy)
    env = _make_environ(remote_addr="::1", ingress_path="/ingress/xyz")
    mw(env, lambda *a: None)

    assert captured["SCRIPT_NAME"] == "/ingress/xyz"
