"""The controller's verbs answer with a verdict, not with a transcript.

Connect, disconnect, trust, remove and power are the verbs that act on a
controller.  Their callers used to receive the bluetoothctl transcript and
scrape it themselves — the web layer decided a disconnect had worked by
looking for the word "successful", the manager read the failure line off a
different field, and every one of them had to know what BlueZ prints.  A
verdict is what a caller actually acts on, and it is the same verdict
whichever transport produced it.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, Outcome

ADAPTER_MAC = "C0:FB:F9:62:D7:D6"
ENEBY_MAC = "6C:5C:3D:35:17:99"


@pytest.fixture
def bluez(fake_bluez):
    return fake_bluez.control


def test_connect_reports_the_bluez_error_as_the_verdict_detail(bluez, fake_bluez):
    fake_bluez.on(
        f"connect {ENEBY_MAC}",
        stdout="Attempting to connect to 6C:5C:3D:35:17:99\nFailed to connect: org.bluez.Error.Failed br-connection-page-timeout\n",
    )

    result = bluez.connect(ENEBY_MAC, Adapter.select(ADAPTER_MAC))

    assert result.ok is False
    assert result.outcome is Outcome.FAILED
    assert "br-connection-page-timeout" in result.detail


def test_connect_that_succeeded_is_ok_with_nothing_to_report(bluez, fake_bluez):
    fake_bluez.on(f"connect {ENEBY_MAC}", stdout="Attempting to connect to 6C:5C:3D:35:17:99\nConnection successful\n")

    result = bluez.connect(ENEBY_MAC)

    assert result.ok is True
    assert result.outcome is Outcome.OK


def test_connect_that_never_returned_is_not_a_refusal(bluez, fake_bluez):
    fake_bluez.timeout(f"connect {ENEBY_MAC}")

    result = bluez.connect(ENEBY_MAC)

    assert result.ok is False
    assert result.outcome is Outcome.TIMEOUT


def test_disconnect_is_ok_when_bluez_says_nothing(bluez, fake_bluez):
    # BlueZ >= 5.72 prints the attempt and stays silent on success.
    fake_bluez.on(f"disconnect {ENEBY_MAC}", stdout="Attempting to disconnect from 6C:5C:3D:35:17:99\n")

    assert bluez.disconnect(ENEBY_MAC).ok is True


def test_disconnect_that_bluez_refused_is_not_ok(bluez, fake_bluez):
    fake_bluez.on(
        f"disconnect {ENEBY_MAC}",
        stdout="Attempting to disconnect from 6C:5C:3D:35:17:99\nFailed to disconnect: org.bluez.Error.NotConnected\n",
    )

    result = bluez.disconnect(ENEBY_MAC)

    assert result.ok is False
    assert result.outcome is Outcome.FAILED
    assert "NotConnected" in result.detail


def test_trust_that_bluez_refused_is_not_ok(bluez, fake_bluez):
    fake_bluez.on(f"trust {ENEBY_MAC}", stdout="Failed to trust: org.bluez.Error.DoesNotExist\n")

    assert bluez.trust(ENEBY_MAC).ok is False


def test_trust_that_succeeded_is_ok(bluez, fake_bluez):
    fake_bluez.on(f"trust {ENEBY_MAC}", stdout="Changing 6C:5C:3D:35:17:99 trust succeeded\n")

    assert bluez.trust(ENEBY_MAC).ok is True


def test_remove_states_the_reason_it_could_not(bluez, fake_bluez):
    fake_bluez.on(f"remove {ENEBY_MAC}", stdout="Device 6C:5C:3D:35:17:99 not available\n")

    result = bluez.remove(ENEBY_MAC)

    assert result.removed is False
    assert result.not_available is True
    assert "not available" in result.detail.lower()


def test_power_carries_its_own_detail_rather_than_a_transcript(bluez, fake_bluez):
    fake_bluez.on("power on", stdout="Changing power on succeeded\n")

    result = bluez.power(True, Adapter.select(ADAPTER_MAC))

    assert result.applied is True
    assert result.powered is True
    assert "succeeded" in result.detail.lower()
