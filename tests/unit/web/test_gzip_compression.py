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

    @test_app.route("/big-js")
    def big_js():
        # Flask's send_file infers ``text/javascript`` for .js (RFC
        # 9239 / modern IANA preferred).  The pre-fix gzip middleware
        # only matched ``application/javascript``, so app.js shipped
        # uncompressed while style.css (matched on ``text/css``) was
        # already gzipped — exactly the asymmetry seen in production
        # v2.66.11 cold-load smoke.
        return Response("var x = 1; " * 400, content_type="text/javascript")

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

    @test_app.route("/etagged")
    def etagged_text():
        # Mimics what send_from_directory produces: a gzippable body
        # with an ETag pinned to the uncompressed representation.
        # The gzip middleware must rewrite the ETag so a subsequent
        # If-None-Match can't match against the wrong body.
        resp = Response("x" * 4096, content_type="text/css")
        resp.set_etag("abc123def456")
        return resp

    return test_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Compression triggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/big-json", "/big-text", "/big-css", "/big-js"])
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


def test_gzip_skipped_when_client_disables_via_q_zero(client):
    """``Accept-Encoding: gzip;q=0`` is the explicit RFC 7231 way to
    opt out of gzip even when the client lists it.  Pre-fix the
    middleware did a substring check and compressed anyway."""
    resp = client.get("/big-json", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers


def test_gzip_output_is_deterministic(client):
    """Two requests for the same body produce identical bytes — the
    gzip header timestamp is pinned to 0 so any caching proxy can
    safely deduplicate.  Pre-fix Python's default ``mtime=now``
    leaked the request time into the header."""
    r1 = client.get("/big-json", headers={"Accept-Encoding": "gzip"})
    r2 = client.get("/big-json", headers={"Accept-Encoding": "gzip"})
    assert r1.headers.get("Content-Encoding") == "gzip"
    assert r2.headers.get("Content-Encoding") == "gzip"
    assert r1.data == r2.data, "gzip output must be deterministic across requests"


def test_gzip_rewrites_etag_to_distinguish_compressed_representation(client):
    """A response that arrives with an ETag (e.g. from
    ``send_from_directory``) must end up with a *different* ETag
    after gzipping so an ``If-None-Match`` from a gzip-supporting
    client doesn't match against the uncompressed cached entry.
    """
    resp = client.get("/etagged", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    etag = resp.headers.get("ETag", "")
    assert etag, "test fixture should always emit an ETag"
    assert "-gzip" in etag, f"ETag must be tagged for the gzipped variant; got {etag!r}"


def test_gzip_does_not_double_tag_etag_on_repeat_pass(client):
    """If somehow the middleware sees a response that already has the
    ``-gzip`` suffix, it must not append it again."""
    resp = client.get("/etagged", headers={"Accept-Encoding": "gzip"})
    etag = resp.headers.get("ETag", "")
    # Exactly one suffix.
    assert etag.count("-gzip") == 1, f"expected single -gzip suffix; got {etag!r}"


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
