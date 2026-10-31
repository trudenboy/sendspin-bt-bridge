"""DEMO_MODE feeds the hardware page from the demo fixtures, not the host."""

from __future__ import annotations

import pytest

from demo.fixtures import DEMO_ADAPTERS, DEMO_BT_DEVICE_INFO
from sendspin_bridge.services.hardware import inventory
from tests.support.api_client import make_client


@pytest.fixture(autouse=True)
def _fresh_inventory(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    original = inventory._refresh
    inventory.clear_cache()
    yield
    inventory._refresh = original
    inventory.clear_cache()


def test_demo_mode_lists_the_controllers_and_paired_devices_the_page_needs():
    from demo import install_inventory

    install_inventory()

    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 200
    body = response.json()
    assert {controller["mac"] for controller in body["controllers"]} == {adapter["mac"] for adapter in DEMO_ADAPTERS}
    assert {device["mac"] for device in body["bluetooth_devices"]} == {
        str(device["mac"]) for device in DEMO_BT_DEVICE_INFO
    }
    assert body["capabilities"]["bluetooth"] is True
    assert body["capabilities"]["dbus"] is True
    assert body["degraded"] == []
