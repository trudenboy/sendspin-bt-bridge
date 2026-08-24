"""The adapter session: one owner for every mutating BlueZ verb.

Mutating a controller requires an exclusive lease.  The lease is released by
token, not by thread, because the HTTP layer acquires it on a request thread
and hands it to a worker thread.  Link state is tri-state — a transport
failure is ``UNKNOWN``, never a confirmed disconnect.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from sendspin_bridge.bluetooth.adapter_session import AdapterHandle, LinkState

MAC = "6C:5C:3D:35:17:99"
ADAPTER_MAC = "C0:FB:F9:62:D7:D6"

_CONNECTED_INFO = (
    f"Device {MAC} (public)\n"
    "\tName: ENEBY Portable\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: yes\n"
)


@pytest.fixture()
def handle(fake_bluez):
    return AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control)


# ── link state ───────────────────────────────────────────────────────────


def test_link_state_reports_connected(handle, fake_bluez):
    fake_bluez.on(f"info {MAC}", stdout=_CONNECTED_INFO)

    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.CONNECTED


def test_link_state_reports_disconnected(handle, fake_bluez):
    # The frozen corpus answers ``Connected: no`` for this MAC.
    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.DISCONNECTED


def test_link_state_is_unknown_on_timeout(handle, fake_bluez):
    fake_bluez.timeout(f"info {MAC}")

    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.UNKNOWN


def test_link_state_is_unknown_when_the_transport_is_unavailable(handle, fake_bluez):
    fake_bluez.fail(f"info {MAC}")

    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.UNKNOWN


def test_link_state_of_an_unknown_device_is_disconnected(handle, fake_bluez):
    unknown = "AA:BB:CC:DD:EE:FF"

    with handle.lease("test") as lease:
        assert lease.session.link_state(unknown) is LinkState.DISCONNECTED


def test_link_state_prefers_an_injected_fast_probe(fake_bluez):
    calls: list[str] = []

    def _probe(mac: str) -> LinkState:
        calls.append(mac)
        return LinkState.CONNECTED

    handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control, link_probe=_probe)
    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.CONNECTED

    assert calls == [MAC]
    assert "info" not in fake_bluez.verbs()


def test_link_state_falls_back_to_the_transport_when_the_probe_cannot_answer(fake_bluez):
    handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control, link_probe=lambda mac: None)
    fake_bluez.on(f"info {MAC}", stdout=_CONNECTED_INFO)

    with handle.lease("test") as lease:
        assert lease.session.link_state(MAC) is LinkState.CONNECTED

    assert "info" in fake_bluez.verbs()


# ── leases ───────────────────────────────────────────────────────────────


def test_only_one_lease_at_a_time(handle):
    first = handle.try_lease("scan")
    assert first is not None
    try:
        assert handle.try_lease("pair") is None
    finally:
        first.release()

    second = handle.try_lease("pair")
    assert second is not None
    second.release()


def test_a_lease_may_be_released_from_another_thread(handle):
    lease = handle.try_lease("pair")
    assert lease is not None

    worker = threading.Thread(target=lease.release)
    worker.start()
    worker.join(timeout=2)

    assert not lease.active
    again = handle.try_lease("pair")
    assert again is not None
    again.release()


def test_double_release_does_not_free_someone_elses_lease(handle, caplog):
    first = handle.try_lease("scan")
    assert first is not None
    first.release()

    second = handle.try_lease("pair")
    assert second is not None

    first.release()  # stale release — must not touch the live lease

    assert second.active
    assert handle.try_lease("reconnect") is None
    second.release()


def test_a_second_handle_shares_the_process_wide_lease(fake_bluez):
    one = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control)
    two = AdapterHandle(adapter="C0:FB:F9:62:D6:9D", bluez=fake_bluez.control)

    lease = one.try_lease("scan")
    assert lease is not None
    try:
        assert two.try_lease("pair") is None
    finally:
        lease.release()


def test_lease_reports_the_holder(handle):
    with handle.lease("pairing ENEBY") as lease:
        assert lease.reason == "pairing ENEBY"
        assert handle.current_holder() == "pairing ENEBY"

    assert handle.current_holder() is None


def test_blocking_lease_times_out_while_held(handle):
    held = handle.try_lease("scan")
    assert held is not None
    try:
        with pytest.raises(TimeoutError):
            handle.lease("pair", timeout=0.05)
    finally:
        held.release()


# ── mutations are scoped to the handle's controller ──────────────────────


def test_mutations_select_the_handles_controller(handle, fake_bluez):
    with handle.lease("reconnect") as lease:
        lease.session.connect(MAC)
        lease.session.disconnect(MAC)

    assert [c.verb for c in fake_bluez.scoped(ADAPTER_MAC)] == ["connect", "disconnect"]


def test_default_adapter_never_emits_a_select_line(fake_bluez):
    handle = AdapterHandle(adapter="", bluez=fake_bluez.control)

    with handle.lease("reconnect") as lease:
        lease.session.connect(MAC)

    fake_bluez.assert_never_selected()


def _hciconfig(hci: str, mac: str) -> str:
    return f"{hci}:\tType: Primary  Bus: USB\n\tBD Address: {mac}  ACL MTU: 310:10  SCO MTU: 64:8\n\tUP RUNNING\n"


def test_hci_name_resolves_through_the_kernel_map(fake_bluez):
    fake_bluez.on("hciconfig", stdout=_hciconfig("hci1", ADAPTER_MAC))
    handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control)

    assert handle.hci_name == "hci1"
    assert handle.dbus_device_path(MAC) == "/org/bluez/hci1/dev_6C_5C_3D_35_17_99"


def test_dbus_path_is_none_until_the_adapter_resolves(fake_bluez):
    handle = AdapterHandle(adapter="C0:FB:F9:00:00:00", bluez=fake_bluez.control)

    assert handle.hci_name == ""
    assert handle.dbus_device_path(MAC) is None


def test_hci_name_is_retried_after_a_failed_resolution(fake_bluez):
    handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control)
    assert handle.hci_name == ""

    # bluetoothd came up late — the next read must see it.
    fake_bluez.on("hciconfig", stdout=_hciconfig("hci0", ADAPTER_MAC))
    assert handle.hci_name == "hci0"


def test_adapter_mac_passes_a_configured_mac_through(fake_bluez):
    handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control)

    assert handle.adapter_mac == ADAPTER_MAC


def test_adapter_mac_resolves_an_hci_identity_through_the_kernel_map(fake_bluez):
    fake_bluez.on("hciconfig", stdout=_hciconfig("hci1", ADAPTER_MAC))
    handle = AdapterHandle(adapter="hci1", bluez=fake_bluez.control)

    assert handle.adapter_mac == ADAPTER_MAC


def test_adapter_mac_is_empty_for_the_default_controller(fake_bluez):
    assert AdapterHandle(adapter="", bluez=fake_bluez.control).adapter_mac == ""


# ── async facade ─────────────────────────────────────────────────────────


def test_async_session_runs_blocking_verbs_off_the_event_loop(handle, fake_bluez):
    fake_bluez.on(f"info {MAC}", stdout=_CONNECTED_INFO)
    loop_thread = threading.current_thread().name
    seen: list[str] = []

    def _probe(mac: str) -> LinkState | None:
        seen.append(threading.current_thread().name)
        return None

    async_handle = AdapterHandle(adapter=ADAPTER_MAC, bluez=fake_bluez.control, link_probe=_probe)

    async def _run():
        with async_handle.lease("reconnect") as lease:
            return await lease.async_session.link_state(MAC)

    assert asyncio.run(_run()) is LinkState.CONNECTED
    assert seen and seen[0] != loop_thread
