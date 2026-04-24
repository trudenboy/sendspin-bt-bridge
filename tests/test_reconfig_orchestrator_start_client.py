"""Tests for ReconfigOrchestrator.START_CLIENT online activation path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.config_diff import ActionKind, ReconfigAction
from services.device_activation import ActivationResult, DeviceActivationContext
from services.reconfig_orchestrator import ReconfigOrchestrator


class _FakeSnapshot:
    def __init__(self, active_clients: list[object]):
        self.active_clients = list(active_clients)

    def client_map_by_mac(self) -> dict[str, object]:
        return {
            mac: client
            for client in self.active_clients
            if (mac := getattr(getattr(client, "bt_manager", None), "mac_address", None))
        }


def _make_context(**overrides) -> DeviceActivationContext:
    base = DeviceActivationContext(
        server_host="auto",
        server_port=9000,
        effective_bridge="",
        prefer_sbc=True,
        bt_check_interval=15,
        bt_max_reconnect_fails=10,
        bt_churn_threshold=0,
        bt_churn_window=300.0,
        enable_a2dp_sink_recovery_dance=False,
        enable_pa_module_reload=False,
        enable_adapter_auto_recovery=False,
        base_listen_port=8928,
        client_factory=MagicMock(),
        bt_manager_factory=MagicMock(),
        load_saved_volume_fn=None,
        persist_enabled_fn=None,
    )
    if not overrides:
        return base
    from dataclasses import replace

    return replace(base, **overrides)


def _make_new_device_action(
    mac: str = "AA:BB:CC:DD:EE:FF",
    *,
    label: str = "Kitchen",
) -> ReconfigAction:
    device = {"mac": mac, "adapter": "hci0", "player_name": label, "enabled": True}
    return ReconfigAction(
        kind=ActionKind.START_CLIENT,
        mac=mac,
        fields=["device"],
        payload={"device": device},
        label=label,
    )


def test_start_client_activates_via_factory_and_appends_to_registry(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        snapshot = _FakeSnapshot([])
        ctx = _make_context()
        orch = ReconfigOrchestrator(loop, snapshot, activation_context=ctx)

        fake_client = SimpleNamespace(
            run=lambda: _empty_coro(),
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        )
        activate_mock = MagicMock(
            return_value=ActivationResult(
                client=fake_client,
                bt_manager=fake_client.bt_manager,
                bt_available=True,
                listen_port=8928,
            )
        )
        monkeypatch.setattr("services.device_activation.activate_device", activate_mock)

        set_clients_calls: list[list[object]] = []
        monkeypatch.setattr(
            "state.set_clients",
            lambda clients: set_clients_calls.append(list(clients)),
        )

        schedule_calls: list[object] = []

        class _StubFuture:
            def add_done_callback(self, cb):
                self.cb = cb

        def _fake_schedule(coro, _loop):
            # Drain the coroutine so Python doesn't warn about never-awaited.
            coro.close()
            fut = _StubFuture()
            schedule_calls.append(fut)
            return fut

        monkeypatch.setattr(
            "services.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _fake_schedule,
        )

        summary = orch.apply([_make_new_device_action()])

        activate_mock.assert_called_once()
        assert len(set_clients_calls) == 1
        assert set_clients_calls[0] == [fake_client]
        assert len(schedule_calls) == 1
        assert len(summary.started) == 1
        assert summary.started[0]["mac"] == "AA:BB:CC:DD:EE:FF"
        assert summary.restart_required == []
        assert summary.errors == []
    finally:
        loop.close()


def test_start_client_without_activation_context_falls_back_to_restart_required():
    loop = asyncio.new_event_loop()
    try:
        orch = ReconfigOrchestrator(loop, _FakeSnapshot([]))  # no activation_context

        summary = orch.apply([_make_new_device_action()])

        assert summary.started == []
        assert len(summary.restart_required) == 1
        assert summary.restart_required[0]["mac"] == "AA:BB:CC:DD:EE:FF"
    finally:
        loop.close()


def test_start_client_without_running_loop_falls_back_to_restart_required():
    orch = ReconfigOrchestrator(None, _FakeSnapshot([]), activation_context=_make_context())

    summary = orch.apply([_make_new_device_action()])

    assert summary.started == []
    assert len(summary.restart_required) == 1


def test_start_client_handles_factory_exception(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        orch = ReconfigOrchestrator(loop, _FakeSnapshot([]), activation_context=_make_context())

        def _raising(*args, **kwargs):
            raise RuntimeError("adapter missing")

        monkeypatch.setattr("services.device_activation.activate_device", _raising)
        registry_touched: list[object] = []
        monkeypatch.setattr("state.set_clients", lambda c: registry_touched.append(c))

        summary = orch.apply([_make_new_device_action()])

        assert summary.started == []
        assert len(summary.errors) == 1
        assert "adapter missing" in summary.errors[0]["error"]
        assert registry_touched == []  # registry not mutated on factory failure
    finally:
        loop.close()


def test_start_client_idempotent_when_mac_already_active(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        existing = SimpleNamespace(
            bt_management_enabled=True,
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
            set_bt_management_enabled=MagicMock(),
        )
        orch = ReconfigOrchestrator(
            loop,
            _FakeSnapshot([existing]),
            activation_context=_make_context(),
        )
        activate_mock = MagicMock()
        monkeypatch.setattr("services.device_activation.activate_device", activate_mock)

        summary = orch.apply([_make_new_device_action()])

        activate_mock.assert_not_called()
        existing.set_bt_management_enabled.assert_not_called()
        assert summary.started == []
        assert summary.errors == []
        assert summary.restart_required == []
    finally:
        loop.close()


def test_start_client_reclaims_bt_management_for_live_disabled_client(monkeypatch):
    # When the user toggles ``enabled: false`` at runtime we keep the client
    # in the registry with ``bt_management_enabled=False``.  A later
    # ``enabled: true`` flip emits START_CLIENT — but we must NOT build a
    # fresh SendspinClient (that would leak the disabled one and cause two
    # clients to fight for the same adapter).  Reclaim the existing one
    # instead by flipping BT management back on.
    loop = asyncio.new_event_loop()
    try:
        released = SimpleNamespace(
            bt_management_enabled=False,
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
            set_bt_management_enabled=MagicMock(),
        )
        orch = ReconfigOrchestrator(
            loop,
            _FakeSnapshot([released]),
            activation_context=_make_context(),
        )
        activate_mock = MagicMock()
        monkeypatch.setattr("services.device_activation.activate_device", activate_mock)

        summary = orch.apply([_make_new_device_action()])

        released.set_bt_management_enabled.assert_called_once_with(True)
        activate_mock.assert_not_called()  # no new client constructed
        assert len(summary.started) == 1
        assert summary.started[0]["mac"] == "AA:BB:CC:DD:EE:FF"
        assert summary.errors == []
    finally:
        loop.close()


def test_start_client_reports_error_when_reclaim_raises(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        released = SimpleNamespace(
            bt_management_enabled=False,
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
            set_bt_management_enabled=MagicMock(side_effect=RuntimeError("adapter busy")),
        )
        orch = ReconfigOrchestrator(
            loop,
            _FakeSnapshot([released]),
            activation_context=_make_context(),
        )
        activate_mock = MagicMock()
        monkeypatch.setattr("services.device_activation.activate_device", activate_mock)

        summary = orch.apply([_make_new_device_action()])

        activate_mock.assert_not_called()
        assert summary.started == []
        assert len(summary.errors) == 1
        assert "re-enable failed" in summary.errors[0]["error"]
    finally:
        loop.close()


def test_start_client_rollback_on_run_task_failure(monkeypatch):
    loop = asyncio.new_event_loop()
    try:
        snapshot = _FakeSnapshot([])
        ctx = _make_context()
        orch = ReconfigOrchestrator(loop, snapshot, activation_context=ctx)

        fake_client = SimpleNamespace(
            run=lambda: _empty_coro(),
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        )
        monkeypatch.setattr(
            "services.device_activation.activate_device",
            lambda *a, **k: ActivationResult(
                client=fake_client,
                bt_manager=fake_client.bt_manager,
                bt_available=True,
                listen_port=8928,
            ),
        )

        clients_history: list[list[object]] = []
        monkeypatch.setattr("state.set_clients", lambda c: clients_history.append(list(c)))
        monkeypatch.setattr(
            "state.get_clients_snapshot",
            lambda: clients_history[-1] if clients_history else [],
        )
        notify_calls: list[int] = []
        monkeypatch.setattr("state.notify_status_changed", lambda: notify_calls.append(1))

        captured_cb: dict[str, object] = {}

        class _StubFuture:
            def add_done_callback(self, cb):
                captured_cb["cb"] = cb

            def result(self):
                raise RuntimeError("run() crashed on startup")

        def _fake_schedule(coro, _loop):
            coro.close()
            return _StubFuture()

        monkeypatch.setattr(
            "services.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _fake_schedule,
        )

        summary = orch.apply([_make_new_device_action()])

        assert len(summary.started) == 1  # reported at schedule time
        assert clients_history == [[fake_client]]  # original append only

        # Simulate run() exiting with an exception; rollback should prune
        # the client from the registry.
        fut_cb = captured_cb["cb"]
        fut_cb(_StubFuture())  # invoke done callback with a future whose .result() raises
        assert clients_history[-1] == []
        assert notify_calls  # rollback broadcasted SSE update
    finally:
        loop.close()


async def _empty_coro() -> None:
    return None
