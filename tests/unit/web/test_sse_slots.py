"""The listener budget, and the two streams that share it.

A slot claimed in a view function but released in the generator's `finally`
leaks whenever the body is never iterated.  Flask registers HEAD for every
GET rule and Werkzeug discards a HEAD body without touching the generator, so
four HEAD requests — an uptime probe, a link preview, a crawler — used to
refuse every later listener until the process restarted.

`/api/status/stream` was fixed that way once.  `/api/status/events` reached
into its neighbour's module globals and repeated the same shape, so it still
leaked; both now share one pool.
"""

from __future__ import annotations

import json
import threading

import pytest
from flask import Flask, Response

from sendspin_bridge.web.sse_slots import SseSlotPool

# ── the pool ─────────────────────────────────────────────────────────────


def test_slots_are_granted_up_to_the_budget():
    pool = SseSlotPool(2)
    granted = []

    with pool.claim() as a, pool.claim() as b:
        granted = [a, b]
        assert pool.in_use == 2

    assert granted == [True, True]
    assert pool.in_use == 0


def test_the_listener_past_the_budget_is_refused():
    pool = SseSlotPool(1)

    with pool.claim() as first:
        assert first is True
        with pool.claim() as second:
            assert second is False

    assert pool.in_use == 0


def test_a_refused_claim_does_not_consume_a_slot():
    pool = SseSlotPool(1)

    with pool.claim() as held:
        assert held is True
        for _ in range(5):
            with pool.claim() as refused:
                assert refused is False
        assert pool.in_use == 1


def test_a_slot_is_released_even_when_the_body_raises():
    pool = SseSlotPool(1)

    with pytest.raises(RuntimeError), pool.claim():
        raise RuntimeError("generator exploded")

    assert pool.in_use == 0


def test_capacity_is_reported_without_claiming():
    pool = SseSlotPool(1)

    assert pool.has_capacity() is True
    with pool.claim():
        assert pool.has_capacity() is False
    assert pool.has_capacity() is True


def test_concurrent_claims_never_exceed_the_budget():
    pool = SseSlotPool(3)
    peak = []
    barrier = threading.Barrier(8, timeout=5)

    def _listener():
        barrier.wait()
        with pool.claim() as granted:
            if granted:
                peak.append(pool.in_use)

    threads = [threading.Thread(target=_listener) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert peak, "nobody was admitted"
    assert max(peak) <= 3
    assert pool.in_use == 0


# ── the endpoints that share it ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def streams():
    """A client over both event-stream endpoints, sharing one fresh pool."""
    import sendspin_bridge.web.routes.api_ha as ha_mod
    import sendspin_bridge.web.routes.api_status as status_mod
    from sendspin_bridge.web.sse_slots import SseSlotPool as Pool

    pool = Pool(2)
    original = status_mod._sse_pool
    status_mod._sse_pool = pool
    ha_mod._sse_pool = pool

    app = Flask(__name__)
    app.register_blueprint(status_mod.status_bp)
    app.register_blueprint(ha_mod.ha_bp)
    try:
        yield app.test_client(), pool
    finally:
        status_mod._sse_pool = original
        ha_mod._sse_pool = original


@pytest.mark.parametrize("path", ["/api/status/stream", "/api/status/events"])
def test_head_requests_do_not_consume_slots(streams, path):
    client, pool = streams

    for _ in range(pool.max_slots + 2):
        client.head(path).close()

    assert pool.in_use == 0


@pytest.mark.parametrize("path", ["/api/status/stream", "/api/status/events"])
def test_a_stream_still_opens_after_head_requests(streams, path):
    client, pool = streams
    for _ in range(pool.max_slots + 2):
        client.head(path).close()

    response = client.get(path, buffered=False)
    try:
        assert response.status_code == 200
    finally:
        response.close()


def test_the_two_streams_draw_on_the_same_budget(streams):
    """The HA coordinator opens both; together they must not exceed the pool."""
    client, pool = streams
    held: list[Response] = []
    try:
        for path in ("/api/status/stream", "/api/status/events"):
            response = client.get(path, buffered=False)
            next(response.response)  # start the generator so the slot is claimed
            held.append(response)

        assert pool.in_use == 2
        assert pool.has_capacity() is False
    finally:
        for response in held:
            response.close()

    assert pool.in_use == 0
