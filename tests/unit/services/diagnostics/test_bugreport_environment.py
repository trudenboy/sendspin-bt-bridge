"""Tests for the bug-report environment payload.

The auto-attached diagnostics block (``_collect_environment``) feeds
both the in-UI Report dialog and the GitHub-issue body.  Every key
here ends up rendered as ``key: value`` in the issue markdown, so
each addition is also a documentation contract — operators see
exactly these fields when triaging.

These tests pin only the additions that aren't already covered by
the higher-level bug-report endpoint tests in
``test_api_endpoints.py``.  Specifically: the MA server version,
which the bridge learns at WS handshake time and used to be
invisible in reports (only the ``music-assistant-client`` *library*
version was present, leaving us guessing about the actual MA build).
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.music_assistant import ma_runtime_state
from sendspin_bridge.web.routes import api_status


@pytest.fixture(autouse=True)
def _reset_ma_server_version():
    """The MA server version is module-level singleton state shared
    with the rest of the suite (``test_demo_mode`` writes it too).
    Snapshot + restore around each test so we don't leak."""
    saved = ma_runtime_state.get_ma_server_version()
    yield
    ma_runtime_state.set_ma_server_version(saved)


def test_collect_environment_includes_ma_server_version_when_known():
    """After the WS handshake the bridge caches the MA server version
    via ``set_ma_server_version``.  ``_collect_environment`` must
    surface it under a stable key so the bug-report markdown shows
    something concrete (e.g. ``ma_server_version: 2.5.7``) instead of
    forcing maintainers to ask "what MA version are you on?" — that
    was a real gap exposed by issue #190 where only
    ``music-assistant-client=1.3.5`` (a Python lib pin) appeared."""
    ma_runtime_state.set_ma_server_version("2.5.7")

    env = api_status._collect_environment()

    assert env.get("ma_server_version") == "2.5.7"


def test_collect_environment_emits_unknown_when_ma_version_not_set():
    """Pre-handshake (or MA never connected) the cache returns ''.
    The key should still appear in the report — explicit "unknown"
    is more useful than a silently-missing field, and matches the
    pattern already used for ``bluez`` and ``audio_server``."""
    ma_runtime_state.set_ma_server_version("")

    env = api_status._collect_environment()

    assert env.get("ma_server_version") == "unknown"


def test_collect_environment_keeps_runtime_deps():
    """Sanity guard: adding the MA *server* version must not displace
    the existing ``music-assistant-client`` *library* version (they
    answer different questions — the lib pin tells us which API
    surface the bridge expects, the server version tells us what
    the operator actually runs)."""
    env = api_status._collect_environment()

    assert "music-assistant-client" in env
