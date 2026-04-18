"""Tests for ReconfigOrchestrator dispatch behaviour."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from unittest.mock import MagicMock

import pytest

from services.config_diff import ActionKind, ReconfigAction
from services.reconfig_orchestrator import ReconfigOrchestrator


class _FakeSnapshot:
    def __init__(self, client_map: dict[str, object]):
        self._map = client_map

    def client_map_by_mac(self) -> dict[str, object]:
        return self._map


def _fake_loop() -> asyncio.AbstractEventLoop:
    # Return an already-closed loop stand-in; the orchestrator only uses it as
    # a truthy sentinel plus as the target for asyncio.run_coroutine_threadsafe,
    # which we monkeypatch at the module level in the timeout test.
    return asyncio.new_event_loop()


class _ThrowingFuture:
    """Stand-in for a concurrent.futures.Future whose .result() raises."""

    def __init__(self, exc: Exception):
        self._exc = exc
        self.callbacks: list = []

    def result(self, timeout: float | None = None):
        raise self._exc

    def add_done_callback(self, cb):
        self.callbacks.append(cb)

    def exception(self):
        return self._exc


def test_apply_hot_reports_timeout_as_error_not_applied(monkeypatch):
    client = MagicMock()

    # The orchestrator calls client.apply_hot_config(...) to build the coroutine
    # handed to run_coroutine_threadsafe; we just need the call to return
    # something closeable.
    async def _coro(*args, **kwargs):
        return []

    client.apply_hot_config = _coro

    snapshot = _FakeSnapshot({"AA:BB:CC:DD:EE:FF": client})
    loop = _fake_loop()
    try:
        orch = ReconfigOrchestrator(loop, snapshot)  # type: ignore[arg-type]

        def _raising(coro, _loop):
            # The orchestrator awaits this future; simulate a slow IPC flush
            # by returning a future that times out.
            coro.close()
            return _ThrowingFuture(FutureTimeoutError("simulated timeout"))

        monkeypatch.setattr(
            "services.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _raising,
        )

        action = ReconfigAction(
            kind=ActionKind.HOT_APPLY,
            mac="AA:BB:CC:DD:EE:FF",
            fields=["static_delay_ms"],
            payload={"static_delay_ms": 250.0},
            label="Test Speaker",
        )

        summary = orch.apply([action])

        assert summary.hot_applied == []  # NOT reported as "Applied live"
        assert len(summary.errors) == 1
        assert summary.errors[0]["kind"] == "hot_apply"
        assert summary.errors[0]["mac"] == "AA:BB:CC:DD:EE:FF"
        assert "pending" in summary.errors[0]["error"].lower()
    finally:
        loop.close()


def test_apply_stop_releases_bt_management_for_durable_stop(monkeypatch):
    client = MagicMock()
    # set_bt_management_enabled(False) is the durable path — it cancels the
    # bt_monitor reconnect loop AND kills the daemon, so the subprocess cannot
    # be revived by a subsequent BT reconnect.
    client.set_bt_management_enabled = MagicMock()

    snapshot = _FakeSnapshot({"AA:BB:CC:DD:EE:FF": client})
    loop = _fake_loop()
    try:
        orch = ReconfigOrchestrator(loop, snapshot)  # type: ignore[arg-type]

        action = ReconfigAction(
            kind=ActionKind.STOP_CLIENT,
            mac="AA:BB:CC:DD:EE:FF",
            fields=["enabled"],
            label="Test Speaker",
        )

        summary = orch.apply([action])

        client.set_bt_management_enabled.assert_called_once_with(False)
        assert len(summary.stopped) == 1
        assert summary.stopped[0]["label"] == "Test Speaker"
    finally:
        loop.close()


def test_apply_stop_missing_client_just_records_summary():
    snapshot = _FakeSnapshot({})
    orch = ReconfigOrchestrator(None, snapshot)

    action = ReconfigAction(
        kind=ActionKind.STOP_CLIENT,
        mac="AA:BB:CC:DD:EE:FF",
        fields=["removed"],
        label="Gone Speaker",
    )

    summary = orch.apply([action])
    assert len(summary.stopped) == 1


@pytest.mark.asyncio
async def test_apply_stop_falls_back_to_stop_sendspin_on_bt_release_failure(monkeypatch):
    # If set_bt_management_enabled raises (e.g., missing BT manager), the
    # orchestrator should still schedule stop_sendspin so the daemon dies.
    client = MagicMock()
    client.set_bt_management_enabled = MagicMock(side_effect=RuntimeError("no bt manager"))
    stop_calls: list = []

    async def _fake_stop():
        stop_calls.append(None)

    client.stop_sendspin = _fake_stop

    snapshot = _FakeSnapshot({"AA:BB:CC:DD:EE:FF": client})

    loop = asyncio.get_running_loop()
    orch = ReconfigOrchestrator(loop, snapshot)  # type: ignore[arg-type]

    action = ReconfigAction(
        kind=ActionKind.STOP_CLIENT,
        mac="AA:BB:CC:DD:EE:FF",
        fields=["removed"],
        label="Test",
    )

    summary = orch.apply([action])
    # Give the scheduled background coroutine a chance to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    client.set_bt_management_enabled.assert_called_once_with(False)
    assert stop_calls == [None]
    assert len(summary.stopped) == 1


def test_apply_hot_closes_coroutine_when_scheduling_fails(monkeypatch):
    # Regression: if run_coroutine_threadsafe raises (loop shutting down),
    # the coroutine built by client.apply_hot_config() must be closed so it
    # doesn't leak / trigger "coroutine was never awaited" warnings.
    client = MagicMock()
    created_coros: list = []

    async def _apply_hot_config(payload):
        return list(payload.keys())

    def _spy(*args, **kwargs):
        coro = _apply_hot_config(*args, **kwargs)
        created_coros.append(coro)
        return coro

    client.apply_hot_config = _spy

    snapshot = _FakeSnapshot({"AA:BB:CC:DD:EE:FF": client})
    loop = _fake_loop()
    try:
        orch = ReconfigOrchestrator(loop, snapshot)  # type: ignore[arg-type]

        def _raise_runtime(coro, _loop):
            raise RuntimeError("loop is closed")

        monkeypatch.setattr(
            "services.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _raise_runtime,
        )

        action = ReconfigAction(
            kind=ActionKind.HOT_APPLY,
            mac="AA:BB:CC:DD:EE:FF",
            fields=["static_delay_ms"],
            payload={"static_delay_ms": 250.0},
            label="Test Speaker",
        )

        summary = orch.apply([action])

        assert len(created_coros) == 1
        # Coroutine was closed — cr_frame is None once close() has run.
        assert created_coros[0].cr_frame is None
        assert summary.hot_applied == []
        assert len(summary.errors) == 1
        assert "schedule failed" in summary.errors[0]["error"]
    finally:
        loop.close()


def test_build_device_snapshot_includes_keepalive_enabled():
    # Regression: _apply_warm_restart_fields re-derives keepalive_enabled
    # whenever idle_mode is in the device payload, and the snapshot always
    # includes idle_mode. So the snapshot must also carry the current
    # keepalive_enabled flag, otherwise GLOBAL_RESTART on a legacy config
    # (idle_mode=default + keepalive_enabled=True) would silently flip it off.
    client = MagicMock()
    client.player_name = "Test"
    client.listen_port = 8928
    client.listen_host = "0.0.0.0"
    client.preferred_format = None
    client.static_delay_ms = 0.0
    client.idle_mode = "default"
    client.keepalive_enabled = True  # legacy explicit flag
    client.keepalive_interval = 30
    client.idle_disconnect_minutes = 0
    client.power_save_delay_minutes = 1

    snapshot = ReconfigOrchestrator._build_device_snapshot_from_client(client)

    assert snapshot["idle_mode"] == "default"
    assert snapshot["keepalive_enabled"] is True
