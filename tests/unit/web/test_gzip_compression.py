"""Tests for the gzip ``after_request`` middleware in ``web/interface.py``.

The middleware was added in v2.66.11 to compress text-ish responses
(JS, CSS, JSON, HTML) when the client advertises ``Accept-Encoding:
gzip``.  Without it, every cold load through HA Ingress shipped
~960 KB of uncompressed JS+CSS before the bridge UI could render —
which felt indistinguishable from "the addon never started".

The tests build a minimal Flask app and attach the real
``_gzip_response`` (and ``_set_cache_headers`` for parity) so the
behaviour is exercised in isolation, without standing up the full
bridge route surface.
"""

from __future__ import annotations

import gzip
import json
import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    from flask import Flask, Response, jsonify

    from sendspin_bridge.web.interface import _gzip_response, _set_cache_headers

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    web_pkg = os.path.join(project_root, "src", "sendspin_bridge", "web")

    test_app = Flask(
        __name__,
        template_folder=os.path.join(web_pkg, "templates"),
        static_folder=os.path.join(web_pkg, "static"),
    )
    test_app.secret_key = "test-secret"
    test_app.config["TESTING"] = True

    test_app.after_request(_set_cache_headers)
    test_app.after_request(_gzip_response)

    @test_app.route("/big-json")
    def big_json():
        # Larger than _GZIP_MIN_BYTES (1024) so it triggers compression.
        return jsonify({"data": "x" * 4096})

    @test_app.route("/tiny-json")
    def tiny_json():
        return jsonify({"ok": True})

    @test_app.route("/big-text")
    def big_text():
        return Response("a" * 4096, content_type="text/plain")

    @test_app.route("/big-css")
    def big_css():
        return Response("body { color: red; } " * 200, content_type="text/css")

    @test_app.route("/big-binary")
    def big_binary():
        return Response(b"\x00" * 4096, content_type="application/octet-stream")

    @test_app.route("/sse")
    def sse_stream():
        # Real SSE — direct_passthrough must keep this path uncompressed
        # so the v2.63.0-rc.4 ingress no-transform handshake stays intact.
        def _gen():
            yield "data: x\n\n" * 200

        resp = Response(_gen(), content_type="text/event-stream")
        return resp

    return test_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Compression triggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/big-json", "/big-text", "/big-css"])
def test_gzip_applied_when_client_supports_and_body_is_text(client, path):
    """JS/CSS/JSON/text bodies above the threshold get gzipped when the
    request advertises ``Accept-Encoding: gzip``."""
    resp = client.get(path, headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in resp.headers.get("Vary", "")
    # Body must round-trip back through gunzip.
    raw = gzip.decompress(resp.data)
    assert len(raw) > 1024


def test_gzip_skipped_when_client_does_not_advertise_support(client):
    resp = client.get("/big-json")
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers


def test_gzip_skipped_for_tiny_bodies(client):
    resp = client.get("/tiny-json", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    # Body is ~12 bytes — below threshold, no compression.
    assert "Content-Encoding" not in resp.headers


def test_gzip_skipped_for_binary_content_types(client):
    resp = client.get("/big-binary", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers


def test_gzip_skipped_for_sse_streams(client):
    """SSE must remain uncompressed — HA Ingress's no-transform handshake
    is built on the assumption that the byte stream is plain text."""
    resp = client.get("/sse", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers


# ---------------------------------------------------------------------------
# Static asset compression — the headline use-case
# ---------------------------------------------------------------------------


def test_vstatic_flips_direct_passthrough_so_gzip_can_run():
    """The production ``vstatic`` route MUST set ``direct_passthrough =
    False`` on its response — otherwise the gzip middleware short-
    circuits and the static assets ship uncompressed.  This was the
    v2.66.10 status quo (~620 KB ``app.js`` over the wire on every
    cold load).

    Cheaper than mounting Flask's static handler in a test app: just
    grep the production source for the explicit flag flip.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "web" / "interface.py"
    body = src.read_text(encoding="utf-8")
    # Find the vstatic function and assert direct_passthrough is set to False.
    assert "def vstatic(" in body
    vstatic_idx = body.index("def vstatic(")
    next_def_idx = body.index("\ndef ", vstatic_idx + 1)
    vstatic_body = body[vstatic_idx:next_def_idx]
    assert "direct_passthrough = False" in vstatic_body, (
        "vstatic must set resp.direct_passthrough = False so the gzip middleware can read and compress the body"
    )
