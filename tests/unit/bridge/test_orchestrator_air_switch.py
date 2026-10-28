"""EXPERIMENTAL_AIR_SENSOR picks who owns the HCI monitor socket at bridge start.

Switch off: the legacy asyncio ``HciAvrcpMonitor`` starts exactly as before and no hub exists.
Switch on: the ``HciMonitorHub`` starts with an ``AvrcpConsumer`` and the legacy monitor stays idle.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator


class FakeLegacyMonitor:
    def __init__(self):
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


class FakeHub:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.consumers: list[object] = []

    def add_consumer(self, c):
        self.consumers.append(c)

    def remove_consumer(self, c):
        self.consumers.remove(c)

    def peers(self):
        return {}

    def start(self):
        self.started = True

    def stop(self, timeout=2.0):
        self.stopped = True


class FakeSinkMonitor:
    async def start(self):
        pass

    async def stop(self):
        pass

    def start_presence_polling(self, interval):
        pass


@pytest.fixture
def orchestrator_with_fakes(monkeypatch):
    from sendspin_bridge.services.audio import sink_monitor as sink_monitor_mod
    from sendspin_bridge.services.audio import sink_suspend_policy
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor, hci_monitor_hub

    def build(**config):
        orchestrator = BridgeOrchestrator()
        env = SimpleNamespace(avrcp_monitor=FakeLegacyMonitor(), hub=FakeHub())
        monkeypatch.setattr(hci_avrcp_monitor, "get_monitor", lambda: env.avrcp_monitor)
        monkeypatch.setattr(hci_monitor_hub, "get_hub", lambda: env.hub)
        monkeypatch.setattr(sink_monitor_mod, "SinkMonitor", FakeSinkMonitor)

        async def no_probe(_clients):
            return None

        monkeypatch.setattr(sink_suspend_policy, "run_suspend_probe_loop", no_probe)

        async def no_sinks(_bootstrap):
            return []

        async def fake_start_web_server(clients, *, app_factory=None):
            return SimpleNamespace(should_exit=False)

        async def fake_configure_executor(device_count, *, web_thread_name=""):
            return 8

        async def fake_initialize_ma(config_data, clients, *, server_host):
            return SimpleNamespace(ma_monitor_task=None)

        async def fake_start_sync_tuning():
            return None

        async def fake_run_runtime(clients, **_kwargs):
            return "done"

        async def fake_duplicate_check(*_args, **_kwargs):
            return None

        monkeypatch.setattr(orchestrator, "read_local_sinks_for", no_sinks)
        monkeypatch.setattr(orchestrator, "initialize_devices", lambda *a, **k: SimpleNamespace(clients=[]))
        monkeypatch.setattr(orchestrator, "start_web_server", fake_start_web_server)
        monkeypatch.setattr(orchestrator, "configure_executor", fake_configure_executor)
        monkeypatch.setattr(orchestrator, "load_sync_tuning", lambda: None)
        monkeypatch.setattr(orchestrator, "install_signal_handlers", lambda *_a, **_k: None)
        monkeypatch.setattr(orchestrator, "initialize_ma_integration", fake_initialize_ma)
        monkeypatch.setattr(orchestrator, "start_sync_tuning", fake_start_sync_tuning)
        monkeypatch.setattr(orchestrator, "_run_duplicate_device_check", fake_duplicate_check)
        monkeypatch.setattr(orchestrator, "run_runtime", fake_run_runtime)

        bootstrap = SimpleNamespace(
            config=dict(config),
            base_listen_port=8928,
            bt_check_interval=15,
            demo_mode=False,
            server_host="auto",
        )

        def start():
            return asyncio.run(
                orchestrator.run_bridge_lifecycle(
                    bootstrap, version="1", client_factory=object, bt_manager_factory=object
                )
            )

        env.start = start
        return env

    return build


def test_switch_off_starts_the_legacy_avrcp_monitor_only(orchestrator_with_fakes):
    env = orchestrator_with_fakes(EXPERIMENTAL_AIR_SENSOR=False)
    env.start()
    assert env.avrcp_monitor.started and not env.hub.started
    assert env.avrcp_monitor.stopped and not env.hub.consumers


def test_switch_absent_behaves_as_off(orchestrator_with_fakes):
    env = orchestrator_with_fakes()
    env.start()
    assert env.avrcp_monitor.started and not env.hub.started


def test_switch_on_starts_the_hub_with_the_avrcp_consumer(orchestrator_with_fakes):
    env = orchestrator_with_fakes(EXPERIMENTAL_AIR_SENSOR=True)
    env.start()
    assert env.hub.started and not env.avrcp_monitor.started
    assert any(type(c).__name__ == "AvrcpConsumer" for c in env.hub.consumers)


def test_switch_on_registers_the_consumer_before_the_hub_starts_and_stops_it_at_shutdown(orchestrator_with_fakes):
    env = orchestrator_with_fakes(EXPERIMENTAL_AIR_SENSOR=True)
    seen_at_start: list[list[object]] = []
    original_start = env.hub.start

    def recording_start():
        seen_at_start.append(list(env.hub.consumers))
        original_start()

    env.hub.start = recording_start
    env.start()
    assert [type(c).__name__ for c in seen_at_start[0]] == ["AvrcpConsumer"]
    assert env.hub.stopped and not env.avrcp_monitor.stopped
