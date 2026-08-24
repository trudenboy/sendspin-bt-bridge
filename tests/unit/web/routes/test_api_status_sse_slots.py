"""SSE listener-slot accounting for ``/api/status/stream``.

Flask auto-registers ``HEAD`` for every ``GET`` rule, and Werkzeug discards
the body of a HEAD response without iterating the generator.  A slot that is
reserved in the view but released in the generator's ``finally`` therefore
leaks on every HEAD request, and ``_MAX_SSE`` such requests disable live
status for the rest of the process lifetime.
"""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def status_mod(monkeypatch):
    # test_ingress_middleware.py installs module stubs at collection time;
    # drop one if it is still in place so we import the real module.
    if "sendspin_bridge.web.routes.api_status" in sys.modules:
        stub = sys.modules["sendspin_bridge.web.routes.api_status"]
        if getattr(stub, "__file__", None) is None:
            sys.modules.pop("sendspin_bridge.web.routes.api_status")

    import sendspin_bridge.web.routes.api_status as mod

    monkeypatch.setattr(mod, "_sse_count", 0)
    return mod


@pytest.fixture()
def client(status_mod):
    app = Flask(__name__)
    app.register_blueprint(status_mod.status_bp)
    return app.test_client()


def test_head_requests_do_not_consume_listener_slots(client, status_mod):
    for _ in range(status_mod._MAX_SSE):
        response = client.head("/api/status/stream")
        response.close()
        assert response.status_code == 200

    assert status_mod._sse_count == 0


def test_stream_still_available_after_head_requests(client, status_mod):
    for _ in range(status_mod._MAX_SSE):
        client.head("/api/status/stream").close()

    response = client.get("/api/status/stream", buffered=False)
    try:
        assert response.status_code == 200
    finally:
        response.close()


def test_slot_is_released_when_the_stream_closes(client, status_mod):
    response = client.get("/api/status/stream", buffered=False)
    next(response.response)  # start the generator so the slot is taken
    assert status_mod._sse_count == 1
    response.close()

    assert status_mod._sse_count == 0
