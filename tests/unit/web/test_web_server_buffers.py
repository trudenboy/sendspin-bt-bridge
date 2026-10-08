"""Long-lived responses must not keep every byte they ever sent.

waitress keeps a streaming response's sent bytes in an in-memory buffer —
``skip()`` only moves the read position — and frees them only when it
rotates to a new buffer after ``outbuf_high_watermark`` bytes (16 MB by
default). The status stream (SSE) never ends, so every open dashboard tab
grew the bridge by up to 16 MB, in a sawtooth that read as a leak: measured
on a live bridge, the only growing allocation was waitress/buffers.py.
"""

from __future__ import annotations

import sendspin_bridge.web.interface as interface

_ONE_MIB = 1024 * 1024


def _serve_calls(monkeypatch, *, additional_port):
    calls: list[dict] = []

    def _fake_serve(*args, **kwargs):
        calls.append(kwargs)

    class _InlineThread:
        def __init__(self, target, kwargs, **_kw):
            self._target, self._kwargs = target, kwargs

        def start(self):
            self._target(**self._kwargs)

    monkeypatch.setattr(interface, "serve", _fake_serve)
    monkeypatch.setattr(interface.threading, "Thread", _InlineThread)
    monkeypatch.setattr(interface, "resolve_web_port", lambda: 8080)
    monkeypatch.setattr(interface, "resolve_additional_web_port", lambda: additional_port)
    interface.main()
    return calls


def test_every_web_listener_rotates_its_output_buffer_early(monkeypatch):
    calls = _serve_calls(monkeypatch, additional_port=8081)

    assert len(calls) == 2
    for kwargs in calls:
        assert 0 < kwargs.get("outbuf_high_watermark", 16 * _ONE_MIB) <= _ONE_MIB
