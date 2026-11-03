"""Picking a local input versus the Bluetooth auto/preferred policy."""

from __future__ import annotations

from sendspin_bridge.services.source.capture import CaptureEndpoint
from sendspin_bridge.services.source.capture.router import SourceInputRouter


class FakeBase:
    name = "pulseaudio"

    def __init__(self) -> None:
        self.preferred: str | None = None
        self.calls = 0
        self.endpoint = CaptureEndpoint(
            node="bluez_source.34_C7_E9_25_A3_A3", backend="pulseaudio", phone_mac="34:C7:E9:25:A3:A3"
        )
        self.opened: list[CaptureEndpoint] = []

    def set_preferred_mac(self, mac: str | None) -> None:
        self.preferred = mac

    async def find(self):
        self.calls += 1
        return self.endpoint

    async def open(self, endpoint):
        self.opened.append(endpoint)
        return object()


async def test_a_local_input_is_returned_without_asking_the_bluetooth_finder():
    base = FakeBase()
    seen: list[tuple[str, str]] = []

    async def probe(engine, node):
        seen.append((engine, node))
        return True

    router = SourceInputRouter(base, "local:alsa_input.usb", probe=probe)

    endpoint = await router.find()

    assert endpoint is not None and endpoint.node == "alsa_input.usb"
    assert endpoint.phone_mac is None
    assert seen == [("pulseaudio", "alsa_input.usb")]
    assert base.calls == 0


async def test_a_missing_local_input_returns_no_endpoint():
    async def probe(_engine, _node):
        return False

    router = SourceInputRouter(FakeBase(), "local:gone", probe=probe)

    assert await router.find() is None


async def test_a_pinned_bluetooth_input_sets_the_base_preference():
    base = FakeBase()
    router = SourceInputRouter(base, "bluetooth:34:C7:E9:25:A3:A3", probe=lambda *_a: None)

    endpoint = await router.find()

    assert base.preferred == "34:C7:E9:25:A3:A3"
    assert endpoint is base.endpoint


async def test_no_source_is_distinct_from_the_auto_policy():
    base = FakeBase()
    router = SourceInputRouter(base, "none", probe=lambda *_a: None)

    assert await router.find() is None
    assert base.calls == 0

    auto = SourceInputRouter(base, None, probe=lambda *_a: None)
    assert await auto.find() is base.endpoint
