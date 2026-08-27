"""The controller's verbs over D-Bus.

The same verdicts the bluetoothctl transport answers with, from BlueZ
directly: no subprocess, no transcript, and no chance of a `select` line
that did not take effect before the verb ran — the object path names the
controller, so an operation aimed at hci1 cannot land on hci0.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, Outcome
from sendspin_bridge.bluetooth.controller import DbusController
from tests.support.fake_dbus import FakeBlueZ

HCI0 = "/org/bluez/hci0"
HCI1 = "/org/bluez/hci1"
HCI0_MAC = "C0:FB:F9:62:D6:9D"
HCI1_MAC = "C0:FB:F9:62:D7:D6"
ENEBY = "6C:5C:3D:35:17:99"
ENEBY_PATH = f"{HCI1}/dev_6C_5C_3D_35_17_99"


@pytest.fixture
def bluez() -> FakeBlueZ:
    fake = FakeBlueZ()
    fake.add_adapter(HCI0, HCI0_MAC, powered=False)
    fake.add_adapter(HCI1, HCI1_MAC, powered=False)
    fake.add_device(ENEBY_PATH, ENEBY, connected=False)
    return fake


@pytest.fixture
def controller(bluez):
    return DbusController(bus_factory=bluez.bus)


# -- power ------------------------------------------------------------


def test_power_turns_on_the_controller_the_caller_named(controller, bluez):
    result = controller.power(True, Adapter.select("hci1"))

    assert result.changed is True
    assert result.powered is True
    assert bluez.objects[HCI1]["org.bluez.Adapter1"]["Powered"] is True
    assert bluez.objects[HCI0]["org.bluez.Adapter1"]["Powered"] is False


def test_power_accepts_a_controller_named_by_its_address(controller, bluez):
    controller.power(True, Adapter.select(HCI1_MAC))

    assert bluez.objects[HCI1]["org.bluez.Adapter1"]["Powered"] is True


def test_power_reports_a_controller_that_was_already_on(controller, bluez):
    bluez.objects[HCI1]["org.bluez.Adapter1"]["Powered"] = True

    result = controller.power(True, Adapter.select("hci1"))

    assert result.powered is True
    assert result.changed is True


def test_power_at_an_unknown_controller_is_unavailable_not_a_refusal(controller):
    # Nothing answered about that controller, so another transport may know
    # better — which is precisely what UNAVAILABLE means to the caller.
    result = controller.power(True, Adapter.select("hci7"))

    assert result.outcome is Outcome.UNAVAILABLE


# -- the device verbs --------------------------------------------------


def test_connect_asks_bluez_to_bring_up_the_link(controller, bluez):
    result = controller.connect(ENEBY, Adapter.select("hci1"))

    assert result.ok is True
    assert (ENEBY_PATH, "Connect", ()) in bluez.calls


def test_connect_carries_the_bluez_error_when_it_is_refused(controller, bluez):
    bluez.fail["Connect"] = RuntimeError("org.bluez.Error.Failed: br-connection-page-timeout")

    result = controller.connect(ENEBY, Adapter.select("hci1"))

    assert result.ok is False
    assert result.outcome is Outcome.FAILED
    assert "br-connection-page-timeout" in result.detail


def test_connect_to_a_speaker_bluez_has_no_object_for_is_unavailable(controller):
    result = controller.connect("AA:BB:CC:DD:EE:FF", Adapter.select("hci1"))

    assert result.outcome is Outcome.UNAVAILABLE


def test_disconnect_drops_the_link(controller, bluez):
    assert controller.disconnect(ENEBY, Adapter.select("hci1")).ok is True
    assert (ENEBY_PATH, "Disconnect", ()) in bluez.calls


def test_trust_lets_the_speaker_reconnect_on_its_own(controller, bluez):
    assert controller.trust(ENEBY, Adapter.select("hci1")).ok is True
    assert bluez.objects[ENEBY_PATH]["org.bluez.Device1"]["Trusted"] is True


def test_remove_forgets_the_speaker_through_its_controller(controller, bluez):
    result = controller.remove(ENEBY, Adapter.select("hci1"))

    assert result.removed is True
    assert (HCI1, "RemoveDevice", (ENEBY_PATH,)) in bluez.calls


def test_remove_of_a_speaker_bluez_does_not_know_is_already_done(controller):
    result = controller.remove("AA:BB:CC:DD:EE:FF", Adapter.select("hci1"))

    assert result.removed is False
    assert result.not_available is True


# -- the controller's own address --------------------------------------


def test_adapter_address_answers_from_the_kernel_named_object(controller):
    assert controller.adapter_address("hci1") == HCI1_MAC
    assert controller.adapter_address("hci0") == HCI0_MAC


def test_adapter_address_of_an_absent_controller_is_empty(controller):
    assert controller.adapter_address("hci9") == ""


# -- no bus ------------------------------------------------------------


def test_every_verb_is_unavailable_when_the_bus_is_not_there(bluez):
    bluez.connected = False
    controller = DbusController(bus_factory=bluez.bus)

    assert controller.connect(ENEBY, Adapter.select("hci1")).unavailable is True
    assert controller.disconnect(ENEBY, Adapter.select("hci1")).unavailable is True
    assert controller.trust(ENEBY, Adapter.select("hci1")).unavailable is True
    assert controller.remove(ENEBY, Adapter.select("hci1")).unavailable is True
    assert controller.power(True, Adapter.select("hci1")).unavailable is True
    assert controller.adapter_address("hci1") == ""


def test_many_threads_can_ask_at_once_over_one_connection(controller, bluez):
    """The read is asked from the Bluetooth executor, the loop and Flask workers.

    Its predecessor kept a private dbus-python connection per thread, because
    a shared one was only safe with a global initialisation this process
    never did.  The work now happens on the bridge loop, so there is one
    connection and no interleaving to guard against — but the answers still
    have to be right when the callers arrive together.
    """
    import threading

    answers: list[str] = []
    barrier = threading.Barrier(4)

    def ask(hci: str) -> None:
        barrier.wait(timeout=5)
        answers.append(controller.adapter_address(hci))

    threads = [threading.Thread(target=ask, args=(hci,)) for hci in ("hci0", "hci1", "hci0", "hci1")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(answers) == sorted([HCI0_MAC, HCI0_MAC, HCI1_MAC, HCI1_MAC])


def test_the_address_read_answers_from_the_loop_without_deadlocking(controller):
    """A caller already on the loop cannot wait for the loop.

    The verbs refuse it outright — waiting there is a deadlock and a
    programming error — but this read sits deep inside the scoping of every
    bluetoothctl command, where raising would break a path that used to
    work.  It answers from the last live read instead.
    """
    import asyncio

    from sendspin_bridge.bridge import state as bridge_state

    assert controller.adapter_address("hci1") == HCI1_MAC  # one live read, off the loop

    async def _from_the_loop() -> str:
        return controller.adapter_address("hci1")

    loop = bridge_state.get_main_loop()
    assert asyncio.run_coroutine_threadsafe(_from_the_loop(), loop).result(timeout=5) == HCI1_MAC


def test_an_address_never_read_is_empty_from_the_loop(controller):
    import asyncio

    from sendspin_bridge.bridge import state as bridge_state

    async def _from_the_loop() -> str:
        return controller.adapter_address("hci0")

    loop = bridge_state.get_main_loop()
    assert asyncio.run_coroutine_threadsafe(_from_the_loop(), loop).result(timeout=5) == ""


def test_an_empty_controller_name_is_not_asked_about(controller, bluez):
    assert controller.adapter_address("") == ""
