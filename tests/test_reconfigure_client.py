"""Tests for SendspinClient hot-apply / warm-restart flow."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sendspin_client import SendspinClient


class _FakeCommandService:
    def __init__(self):
        self.calls: list[tuple[object, dict]] = []

    async def send(self, proc, cmd):
        self.calls.append((proc, cmd))


def _make_client(**kwargs) -> SendspinClient:
    """Build a SendspinClient wired with a FakeCommandService and a running proc."""
    client = SendspinClient("Test Player", "localhost", 9000, **kwargs)
    client._command_service = _FakeCommandService()  # type: ignore[assignment]
    client._daemon_proc = SimpleNamespace(returncode=None, stdin=object())  # type: ignore[assignment]
    return client


# ---------------------------------------------------------------------------
# apply_hot_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_hot_config_sends_set_static_delay_ms_ipc():
    client = _make_client()

    applied = await client.apply_hot_config({"static_delay_ms": 450})

    assert applied == ["static_delay_ms"]
    assert client.static_delay_ms == pytest.approx(450.0)
    assert client._command_service.calls == [(client._daemon_proc, {"cmd": "set_static_delay_ms", "value": 450.0})]


@pytest.mark.asyncio
async def test_apply_hot_config_no_ipc_when_subprocess_not_running():
    client = _make_client()
    client._daemon_proc = None  # simulate not-running state

    applied = await client.apply_hot_config({"static_delay_ms": 300})

    assert applied == ["static_delay_ms"]
    assert client.static_delay_ms == pytest.approx(300.0)
    assert client._command_service.calls == []


@pytest.mark.asyncio
async def test_apply_hot_config_updates_idle_mode_without_ipc():
    client = _make_client()

    applied = await client.apply_hot_config({"idle_mode": "power_save"})

    assert applied == ["idle_mode"]
    assert client.idle_mode == "power_save"
    # No IPC command — idle mode is a parent-only concept.
    assert client._command_service.calls == []


@pytest.mark.asyncio
async def test_apply_hot_config_updates_keepalive_interval_with_floor():
    client = _make_client()

    applied = await client.apply_hot_config({"keepalive_interval": 10})
    # Values below 30 are clamped (see SendspinClient.__init__).
    assert client.keepalive_interval == 30
    assert applied == ["keepalive_interval"]


@pytest.mark.asyncio
async def test_apply_hot_config_ignores_invalid_static_delay_ms():
    client = _make_client()
    before = client.static_delay_ms

    applied = await client.apply_hot_config({"static_delay_ms": "not-a-number"})

    assert applied == []
    assert client.static_delay_ms == before
    assert client._command_service.calls == []


@pytest.mark.asyncio
async def test_apply_hot_config_multiple_fields_in_one_call():
    client = _make_client()

    applied = await client.apply_hot_config(
        {
            "static_delay_ms": 200,
            "idle_mode": "keep_alive",
            "idle_disconnect_minutes": 5,
        }
    )

    assert set(applied) == {"static_delay_ms", "idle_mode", "idle_disconnect_minutes"}
    assert client.static_delay_ms == pytest.approx(200.0)
    assert client.idle_mode == "keep_alive"
    assert client.idle_disconnect_minutes == 5
    assert client.keepalive_enabled is True  # keep_alive implies keepalive_enabled
    # Only one IPC (static_delay_ms); idle/disconnect are parent-only.
    assert client._command_service.calls == [(client._daemon_proc, {"cmd": "set_static_delay_ms", "value": 200.0})]


# ---------------------------------------------------------------------------
# warm_restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_restart_stops_and_starts_subprocess(monkeypatch):
    client = _make_client()
    client.running = True
    stops: list[None] = []
    starts: list[None] = []

    async def _fake_stop(self=client):
        stops.append(None)

    async def _fake_start(self=client):
        starts.append(None)

    monkeypatch.setattr(client, "stop_sendspin", _fake_stop)
    monkeypatch.setattr(client, "_start_sendspin_inner", _fake_start)

    await client.warm_restart(
        {
            "listen_port": 8931,
            "preferred_format": "flac:48000:16:2",
            "static_delay_ms": 123,
        }
    )

    assert len(stops) == 1
    assert len(starts) == 1
    assert client.listen_port == 8931
    assert client.preferred_format == "flac:48000:16:2"
    assert client.static_delay_ms == pytest.approx(123.0)
    # Reloading flag is cleared after a successful restart.
    assert client.status.get("reloading") is False


@pytest.mark.asyncio
async def test_warm_restart_skips_start_when_not_running(monkeypatch):
    client = _make_client()
    client.running = False
    stops: list[None] = []
    starts: list[None] = []

    async def _fake_stop(self=client):
        stops.append(None)

    async def _fake_start(self=client):
        starts.append(None)

    monkeypatch.setattr(client, "stop_sendspin", _fake_stop)
    monkeypatch.setattr(client, "_start_sendspin_inner", _fake_start)

    await client.warm_restart({"listen_port": 8940})

    assert len(stops) == 1
    assert len(starts) == 0


@pytest.mark.asyncio
async def test_warm_restart_clears_reloading_on_failure(monkeypatch):
    client = _make_client()
    client.running = True

    async def _fake_stop(self=client):
        pass

    async def _boom(self=client):
        raise RuntimeError("boom")

    monkeypatch.setattr(client, "stop_sendspin", _fake_stop)
    monkeypatch.setattr(client, "_start_sendspin_inner", _boom)

    with pytest.raises(RuntimeError):
        await client.warm_restart({"listen_port": 8999})

    assert client.status.get("reloading") is False


@pytest.mark.asyncio
async def test_warm_restart_preserves_bridge_suffix_on_rename():
    client = SendspinClient(
        "Kitchen @ bridge-main",
        "localhost",
        9000,
        effective_bridge="bridge-main",
    )
    client._command_service = _FakeCommandService()
    client.running = False  # skip start call

    async def _noop(self=client):
        pass

    client.stop_sendspin = _noop  # type: ignore[assignment]
    client._start_sendspin_inner = _noop  # type: ignore[assignment]

    await client.warm_restart({"player_name": "Kitchen Pro"})

    assert client.player_name == "Kitchen Pro @ bridge-main"
