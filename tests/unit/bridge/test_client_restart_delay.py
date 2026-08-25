"""The restart backoff a bug report shows must be the one in force.

The supervisor took ownership of the backoff, but the diagnostics bundle and
the demo dashboard still read the client's old private attribute.  With that
attribute gone, `getattr(..., 1.0)` fell through to its default, so a daemon
that had backed off to 30 s was reported as restarting in 1 s — in exactly
the bundle someone attaches when asking why a speaker keeps dying.
"""

from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.bridge.client import SendspinClient


def _client() -> SendspinClient:
    return SendspinClient("Test Player", "localhost", 9000)


def test_the_client_reports_the_supervisor_s_current_delay():
    client = _client()

    assert client.restart_delay == 1.0

    client._supervisor.on_death(bt_connected=True)
    client._supervisor.on_death(bt_connected=True)

    assert client.restart_delay == 4.0


def test_a_live_daemon_resets_the_reported_delay():
    client = _client()
    client._supervisor.on_death(bt_connected=True)

    client._supervisor.on_alive()

    assert client.restart_delay == 1.0


def test_the_diagnostics_bundle_reports_the_backoff_in_force(monkeypatch):
    import sendspin_bridge.web.routes.api_status as api_status

    client = _client()
    client._supervisor.on_death(bt_connected=True)
    client._supervisor.on_death(bt_connected=True)

    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[client], disabled_devices=[]),
    )

    entry = api_status._collect_subprocess_info()[0]

    assert entry["restart_delay"] == 4.0
