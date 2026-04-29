"""Unit tests for services.ha_core_api."""

from sendspin_bridge.services.ha.ha_core_api import build_adapter_area_matches, fetch_ha_area_catalog


def test_build_adapter_area_matches_uses_exact_mac_match():
    adapters = [{"id": "hci0", "mac": "aa:bb:cc:dd:ee:ff"}]
    devices = [
        {
            "area_id": "living-room",
            "name_by_user": "USB Bluetooth",
            "connections": [["mac", "AA:BB:CC:DD:EE:FF"]],
        }
    ]
    areas_by_id = {"living-room": {"area_id": "living-room", "name": "Living Room"}}

    matches = build_adapter_area_matches(adapters, devices, areas_by_id)

    assert matches == [
        {
            "adapter_id": "hci0",
            "adapter_mac": "AA:BB:CC:DD:EE:FF",
            "matched_area_id": "living-room",
            "matched_area_name": "Living Room",
            "match_source": "device_registry_mac",
            "match_confidence": "high",
            "matched_device_name": "USB Bluetooth",
            "suggested_name": "Living Room",
        }
    ]


def test_build_adapter_area_matches_skips_ambiguous_areas():
    adapters = [{"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}]
    devices = [
        {"area_id": "living-room", "connections": [["mac", "AA:BB:CC:DD:EE:FF"]]},
        {"area_id": "kitchen", "connections": [["mac", "AA:BB:CC:DD:EE:FF"]]},
    ]
    areas_by_id = {
        "living-room": {"area_id": "living-room", "name": "Living Room"},
        "kitchen": {"area_id": "kitchen", "name": "Kitchen"},
    }

    assert build_adapter_area_matches(adapters, devices, areas_by_id) == []


def test_fetch_ha_area_catalog_normalizes_payload(monkeypatch):
    import sendspin_bridge.services.ha.ha_core_api as ha_core_api

    monkeypatch.setattr(
        ha_core_api,
        "_fetch_registry_payloads",
        lambda ha_token, include_devices, ha_url=None: (
            [
                {"area_id": "kitchen", "name": "Kitchen"},
                {"area_id": "living-room", "name": "Living Room"},
            ],
            [{"area_id": "kitchen", "connections": [["mac", "AA:BB:CC:DD:EE:FF"]]}],
        ),
    )

    payload = fetch_ha_area_catalog(
        "token",
        include_devices=True,
        adapters=[{"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}],
    )

    assert payload["source"] == "ingress_token"
    assert [area["name"] for area in payload["areas"]] == ["Kitchen", "Living Room"]
    assert payload["bridge_name_suggestions"][0]["value"] == "Kitchen"
    assert payload["adapter_matches"][0]["matched_area_name"] == "Kitchen"
