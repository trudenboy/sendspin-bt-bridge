"""dbus-python connections must not be shared across threads.

`dbus.SystemBus()` returns a process-wide shared connection, and dbus-python
only makes that safe once thread support is initialised — which this codebase
never did. Each thread gets its own private connection instead, which needs no
global initialisation and cannot interleave with another thread's call.

One reader still works this way: the controller address behind
`/org/bluez/hciN`, which is asked from the Bluetooth executor, the event loop
and Flask worker threads alike. Speakers moved to the async device module;
controllers follow with the next candidate, and this file goes with them.
"""

from __future__ import annotations

import threading

import pytest

from sendspin_bridge.bluetooth import adapter_address as bt_dbus


class _FakeProps:
    def Get(self, _iface, name):
        return {"Connected": True, "Address": "AA:BB:CC:DD:EE:FF"}.get(name, "")


class _FakeBus:
    def __init__(self, private: bool = False):
        self.private = private
        self.thread = threading.current_thread().name
        self.closed = False

    def get_object(self, _service, _path):
        return object()

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_dbus(monkeypatch):
    created: list[_FakeBus] = []

    class _DbusModule:
        @staticmethod
        def SystemBus(private: bool = False):
            bus = _FakeBus(private=private)
            created.append(bus)
            return bus

        @staticmethod
        def Interface(_obj, _iface):
            return _FakeProps()

    monkeypatch.setattr(bt_dbus, "dbus", _DbusModule)
    bt_dbus._reset_thread_buses()
    yield created
    bt_dbus._reset_thread_buses()


def test_each_thread_gets_its_own_private_connection(fake_dbus):
    def _read():
        bt_dbus._dbus_get_adapter_address("hci0")

    workers = [threading.Thread(target=_read, name=f"w{i}") for i in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert len(fake_dbus) == 3, "threads shared a dbus connection"
    assert {bus.thread for bus in fake_dbus} == {"w0", "w1", "w2"}
    assert all(bus.private for bus in fake_dbus), "connections were not private"


def test_the_same_thread_reuses_its_connection(fake_dbus):
    for _ in range(3):
        bt_dbus._dbus_get_adapter_address("hci0")

    assert len(fake_dbus) == 1


def test_property_reads_still_answer(fake_dbus):
    assert bt_dbus._dbus_get_adapter_address("hci0") == "AA:BB:CC:DD:EE:FF"
    assert bt_dbus._dbus_get_adapter_address("hci0") == "AA:BB:CC:DD:EE:FF"


def test_missing_dbus_module_answers_none(monkeypatch):
    monkeypatch.setattr(bt_dbus, "dbus", None)
    assert bt_dbus._dbus_get_adapter_address("hci0") is None
