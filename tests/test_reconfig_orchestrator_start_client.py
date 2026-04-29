"""Tests for ReconfigOrchestrator.START_CLIENT online activation path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from sendspin_bridge.services.bluetooth.device_activation import ActivationResult, DeviceActivationContext
from sendspin_bridge.services.infrastructure.config_diff import ActionKind, ReconfigAction
from sendspin_bridge.services.lifecycle.reconfig_orchestrator import ReconfigOrchestrator


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


def _patch_registry(monkeypatch, initial: list | None = None) -> list:
    """Install a fake live registry that satisfies the orchestrator contract.

    Returns the underlying list — tests can read it after ``apply()`` to
    see what was appended / removed.  Patches ``state.set_clients``,
    ``state.get_clients_snapshot``, and the atomic
    ``services.device_registry.mutate_active_clients`` the orchestrator
    now goes through to avoid the read-modify-write race.
    """
    live: list = list(initial or [])

    def _set_clients(clients):
        live[:] = list(clients)

    def _get_snapshot():
        return list(live)

    def _mutate(mutator):
        live[:] = list(mutator(list(live)))
        return SimpleNamespace(active_clients=list(live), disabled_devices=[])

    monkeypatch.setattr("state.set_clients", _set_clients)
    monkeypatch.setattr("state.get_clients_snapshot", _get_snapshot)
    monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_registry.mutate_active_clients", _mutate)
    return live


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
        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", activate_mock)
        live = _patch_registry(monkeypatch)

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
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _fake_schedule,
        )

        summary = orch.apply([_make_new_device_action()])

        activate_mock.assert_called_once()
        assert live == [fake_client]
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

        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", _raising)
        live = _patch_registry(monkeypatch)

        summary = orch.apply([_make_new_device_action()])

        assert summary.started == []
        assert len(summary.errors) == 1
        assert "adapter missing" in summary.errors[0]["error"]
        assert live == []  # registry not mutated on factory failure
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
        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", activate_mock)

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
        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", activate_mock)

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
        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", activate_mock)

        summary = orch.apply([_make_new_device_action()])

        activate_mock.assert_not_called()
        assert summary.started == []
        assert len(summary.errors) == 1
        assert "re-enable failed" in summary.errors[0]["error"]
    finally:
        loop.close()


def test_start_client_accumulates_multiple_devices_in_single_apply(monkeypatch):
    # Regression test for a data-loss bug: the original _apply_start_client
    # captured ``existing_clients`` from the initial snapshot and did
    # ``set_clients([*captured, new])`` every iteration — so a diff with 3
    # added devices ended up with only the last one in the registry because
    # each call overwrote the previous append.  Fix reads the live registry
    # snapshot on every call (and now uses an atomic mutate to avoid the
    # cross-request race too).
    loop = asyncio.new_event_loop()
    try:
        snapshot = _FakeSnapshot([])
        orch = ReconfigOrchestrator(loop, snapshot, activation_context=_make_context())

        live_registry = _patch_registry(monkeypatch)

        def _make_fake_client(mac: str) -> SimpleNamespace:
            return SimpleNamespace(
                run=lambda: _empty_coro(),
                bt_manager=SimpleNamespace(mac_address=mac),
            )

        activation_index = [0]

        def _fake_activate(device, *, index, context, default_player_name):
            mac = device["mac"].upper()
            activation_index[0] += 1
            return ActivationResult(
                client=_make_fake_client(mac),
                bt_manager=None,
                bt_available=True,
                listen_port=8928 + index,
            )

        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", _fake_activate)

        class _StubFuture:
            def add_done_callback(self, cb):
                pass

        monkeypatch.setattr(
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            lambda coro, _loop: (coro.close(), _StubFuture())[1],
        )

        actions = [
            _make_new_device_action(mac="AA:AA:AA:AA:AA:01", label="Kitchen"),
            _make_new_device_action(mac="BB:BB:BB:BB:BB:02", label="Bedroom"),
            _make_new_device_action(mac="CC:CC:CC:CC:CC:03", label="Garage"),
        ]

        summary = orch.apply(actions)

        assert len(summary.started) == 3
        assert [c.bt_manager.mac_address for c in live_registry] == [
            "AA:AA:AA:AA:AA:01",
            "BB:BB:BB:BB:BB:02",
            "CC:CC:CC:CC:CC:03",
        ]
    finally:
        loop.close()


def test_start_client_uses_context_default_player_name_when_device_has_none(monkeypatch):
    # Regression for a restart-vs-live-add inconsistency: when the added
    # device omits ``player_name``, online activation must use the same
    # default the startup path captured (``Sendspin-<hostname>`` or the
    # ``$SENDSPIN_NAME`` override) — otherwise naming drifts between a
    # live save and the next bridge restart, breaking MA/UI identity
    # mapping for the affected client.
    loop = asyncio.new_event_loop()
    try:
        ctx = _make_context(default_player_name="Sendspin-homelab")
        orch = ReconfigOrchestrator(loop, _FakeSnapshot([]), activation_context=ctx)

        captured_default: dict[str, str] = {}

        def _fake_activate(device, *, index, context, default_player_name):
            captured_default["name"] = default_player_name
            return ActivationResult(
                client=SimpleNamespace(
                    run=lambda: _empty_coro(),
                    bt_manager=SimpleNamespace(mac_address=device["mac"].upper()),
                ),
                bt_manager=None,
                bt_available=True,
                listen_port=8928,
            )

        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", _fake_activate)
        _patch_registry(monkeypatch)

        class _StubFuture:
            def add_done_callback(self, cb):
                pass

        monkeypatch.setattr(
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            lambda coro, _loop: (coro.close(), _StubFuture())[1],
        )

        # Build an action whose device dict has no player_name.
        action = ReconfigAction(
            kind=ActionKind.START_CLIENT,
            mac="AA:BB:CC:DD:EE:FF",
            fields=["added"],
            payload={"device": {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0"}},
            label="",
        )

        orch.apply([action])

        assert captured_default["name"] == "Sendspin-homelab"
    finally:
        loop.close()


def test_start_client_uses_device_index_from_payload(monkeypatch):
    # device_index in the action payload is the position the device occupies
    # in BLUETOOTH_DEVICES on disk.  Preferring it over len(existing_clients)
    # matches the startup-path listen_port computation (base_listen_port +
    # index) even when the config has disabled devices before it.
    loop = asyncio.new_event_loop()
    try:
        orch = ReconfigOrchestrator(loop, _FakeSnapshot([]), activation_context=_make_context())

        captured_index: dict[str, int] = {}

        def _fake_activate(device, *, index, context, default_player_name):
            captured_index["index"] = index
            return ActivationResult(
                client=SimpleNamespace(
                    run=lambda: _empty_coro(),
                    bt_manager=SimpleNamespace(mac_address=device["mac"].upper()),
                ),
                bt_manager=None,
                bt_available=True,
                listen_port=8928 + index,
            )

        monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", _fake_activate)
        _patch_registry(monkeypatch)

        class _StubFuture:
            def add_done_callback(self, cb):
                pass

        monkeypatch.setattr(
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            lambda coro, _loop: (coro.close(), _StubFuture())[1],
        )

        action = _make_new_device_action()
        # Simulate diff_configs attaching the config index (device is 4th in
        # BLUETOOTH_DEVICES with 3 disabled entries before it).
        action.payload["device_index"] = 3

        orch.apply([action])

        assert captured_index["index"] == 3
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
            "sendspin_bridge.services.bluetooth.device_activation.activate_device",
            lambda *a, **k: ActivationResult(
                client=fake_client,
                bt_manager=fake_client.bt_manager,
                bt_available=True,
                listen_port=8928,
            ),
        )

        live = _patch_registry(monkeypatch)
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
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _fake_schedule,
        )

        summary = orch.apply([_make_new_device_action()])

        assert len(summary.started) == 1  # reported at schedule time
        assert live == [fake_client]  # appended atomically

        # Simulate run() exiting with an exception; rollback should prune
        # the client from the registry via the same atomic mutate path.
        fut_cb = captured_cb["cb"]
        fut_cb(_StubFuture())  # invoke done callback with a future whose .result() raises
        assert live == []
        assert notify_calls  # rollback broadcasted SSE update
    finally:
        loop.close()


def test_start_client_atomic_mutate_drops_duplicate_when_peer_request_won_race(monkeypatch):
    # Concurrency regression: two parallel POST /api/config request threads
    # (Waitress runs WEB_THREADS=8 by default) can both diff the same MAC
    # as a new device.  The atomic mutate_active_clients now sees the peer
    # append inside the registry lock and our race-guard drops the
    # duplicate so the registry never holds two clients for one adapter.
    loop = asyncio.new_event_loop()
    try:
        ctx = _make_context()
        orch = ReconfigOrchestrator(loop, _FakeSnapshot([]), activation_context=ctx)

        peer_client = SimpleNamespace(
            run=lambda: _empty_coro(),
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        )
        # Live registry already contains the peer-appended client by the
        # time our mutate runs (simulates the peer winning the race).
        live = _patch_registry(monkeypatch, initial=[peer_client])

        our_client = SimpleNamespace(
            run=lambda: _empty_coro(),
            bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        )
        # The orchestrator's clients_by_mac lookup uses the snapshot taken
        # at apply() entry — ours was empty.  But by the time we hit the
        # mutator, the live registry already has the peer's client.
        monkeypatch.setattr(
            "sendspin_bridge.services.bluetooth.device_activation.activate_device",
            lambda *a, **k: ActivationResult(
                client=our_client,
                bt_manager=our_client.bt_manager,
                bt_available=True,
                listen_port=8928,
            ),
        )

        schedule_calls: list[object] = []

        class _StubFuture:
            def add_done_callback(self, cb):
                pass

        def _fake_schedule(coro, _loop):
            coro.close()
            schedule_calls.append(_StubFuture())
            return schedule_calls[-1]

        monkeypatch.setattr(
            "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe",
            _fake_schedule,
        )

        orch.apply([_make_new_device_action()])

        # Live registry still has only the peer's client — ours got dropped.
        assert live == [peer_client]
        # And our client.run() must NOT have been scheduled, because two
        # daemons fighting for the same adapter is the bug we're guarding.
        assert schedule_calls == []
    finally:
        loop.close()


async def _empty_coro() -> None:
    return None
