"""Multi-adapter behaviour for GET /api/v1/bluetooth/devices and DELETE /api/v1/bluetooth/devices/{mac}.

Prior to this change, both endpoints only talked to the BlueZ default
controller, so bonds living on a non-default adapter were invisible and
could not be removed via the UI.  These tests lock in the new behaviour:

* ``GET /api/v1/bluetooth/devices`` enumerates every known adapter and reports which
  adapter(s) each device is bonded with.
* ``DELETE /api/v1/bluetooth/devices/{mac}`` accepts an optional ``adapter_mac`` and, when it is
  absent, removes the bond from *every* adapter rather than only the
  default one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def client(tmp_config):
    from tests.support.api_client import make_client

    return make_client()


def _make_paired_stdout(devices: list[tuple[str, str]]) -> str:
    return "\n".join(f"Device {mac} {name}" for mac, name in devices)


def test_paired_enumerates_every_adapter(client, monkeypatch, installed_bluez):
    """Two adapters, one device bonded on each → both surface with adapters[]."""

    import sendspin_bridge.application.bluetooth as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    installed_bluez.on_adapter("C0:FB:F9:62:D7:D6").on(
        "devices Paired", stdout=_make_paired_stdout([("AA:AA:AA:AA:AA:01", "Speaker Alpha")])
    )
    installed_bluez.on_adapter("00:15:83:FF:8F:2B").on(
        "devices Paired", stdout=_make_paired_stdout([("BB:BB:BB:BB:BB:02", "Speaker Bravo")])
    )

    resp = client.get("/api/v1/bluetooth/devices")
    assert resp.status_code == 200
    devices = resp.json()

    by_mac = {d["mac"]: d for d in devices}
    assert set(by_mac) == {"AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"}
    assert by_mac["AA:AA:AA:AA:AA:01"]["adapters"] == ["C0:FB:F9:62:D7:D6"]
    assert by_mac["BB:BB:BB:BB:BB:02"]["adapters"] == ["00:15:83:FF:8F:2B"]
    # Each adapter was enumerated via its own ``select <MAC>`` scope.
    assert any(c.verb == "devices" for c in installed_bluez.scoped("C0:FB:F9:62:D7:D6"))
    assert any(c.verb == "devices" for c in installed_bluez.scoped("00:15:83:FF:8F:2B"))


def test_paired_merges_device_bonded_on_multiple_adapters(client, monkeypatch, installed_bluez):
    """Same MAC visible on two adapters collapses to one entry with both MACs."""

    import sendspin_bridge.application.bluetooth as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    for adapter in adapters:
        installed_bluez.on_adapter(adapter).on(
            "devices Paired", stdout=_make_paired_stdout([("CC:CC:CC:CC:CC:03", "Shared Speaker")])
        )

    resp = client.get("/api/v1/bluetooth/devices")
    assert resp.status_code == 200
    devices = resp.json()

    assert len(devices) == 1
    entry = devices[0]
    assert entry["mac"] == "CC:CC:CC:CC:CC:03"
    assert sorted(entry["adapters"]) == sorted(adapters)


def test_paired_falls_back_when_adapter_list_is_empty(client, monkeypatch, installed_bluez):
    """Environments where ``bluetoothctl list`` fails still produce a list."""

    import sendspin_bridge.application.bluetooth as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    installed_bluez.on("devices", stdout=_make_paired_stdout([("DD:DD:DD:DD:DD:04", "Lone Speaker")]))

    resp = client.get("/api/v1/bluetooth/devices")
    assert resp.status_code == 200
    devices = resp.json()
    assert any(d["mac"] == "DD:DD:DD:DD:DD:04" for d in devices)
    # The unscoped fallback runs plain ``devices`` against the default
    # controller — no select line, and no "Paired" filter (legacy contract).
    devices_cmds = [c for c in installed_bluez.commands if c.verb == "devices"]
    assert devices_cmds and devices_cmds[0].script.strip() == "devices"
    assert devices_cmds[0].adapter_selected == ""


def test_paired_filters_unnamed_devices_by_default(client, monkeypatch, installed_bluez):
    import sendspin_bridge.application.bluetooth as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["11:11:11:11:11:11"])
    installed_bluez.on(
        "devices Paired",
        stdout=_make_paired_stdout(
            [
                ("AA:BB:CC:DD:EE:AA", "Real Speaker"),
                ("AA:BB:CC:DD:EE:BB", "AA-BB-CC-DD-EE-BB"),  # MAC-as-name → dropped
            ]
        ),
    )

    resp = client.get("/api/v1/bluetooth/devices")
    assert resp.status_code == 200
    macs = {d["mac"] for d in resp.json()}
    assert macs == {"AA:BB:CC:DD:EE:AA"}

    resp_all = client.get("/api/v1/bluetooth/devices?named_only=false")
    macs_all = {d["mac"] for d in resp_all.json()}
    assert macs_all == {"AA:BB:CC:DD:EE:AA", "AA:BB:CC:DD:EE:BB"}


def test_remove_without_adapter_mac_targets_every_adapter(client, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    recorded: list[tuple[str, str]] = []

    def fake_remove(mac: str, adapter_mac: str = "") -> None:
        recorded.append((mac, adapter_mac))

    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.delete("/api/v1/bluetooth/devices/AA:BB:CC:DD:EE:01")
    assert resp.status_code == 204

    assert sorted(recorded) == sorted([("AA:BB:CC:DD:EE:01", adapter) for adapter in adapters])


def test_remove_with_adapter_mac_only_targets_that_adapter(client, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.delete("/api/v1/bluetooth/devices/AA:BB:CC:DD:EE:01", params={"adapter_mac": "00:15:83:FF:8F:2B"})
    assert resp.status_code == 204
    fake_remove.assert_called_once_with("AA:BB:CC:DD:EE:01", "00:15:83:FF:8F:2B")


def test_remove_rejects_invalid_adapter_mac(client, monkeypatch):
    import sendspin_bridge.application.bluetooth as module

    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.delete("/api/v1/bluetooth/devices/AA:BB:CC:DD:EE:01", params={"adapter_mac": "not-a-mac"})
    assert resp.status_code == 400
    fake_remove.assert_not_called()


def test_remove_rejects_adapter_mac_not_present_on_host(client, monkeypatch):
    """A syntactically-valid adapter MAC that isn't reported by
    ``list_bt_adapters`` must return 400 instead of silently "succeeding"
    against the default controller while the ``select`` failed."""

    import sendspin_bridge.application.bluetooth as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.delete("/api/v1/bluetooth/devices/AA:BB:CC:DD:EE:01", params={"adapter_mac": "DE:AD:BE:EF:00:01"})
    assert resp.status_code == 400
    assert "adapter" in resp.json()["detail"].lower()
    fake_remove.assert_not_called()


def test_remove_without_adapters_still_calls_default(client, monkeypatch):
    """Pre-existing behaviour preserved when no adapters are known."""

    import sendspin_bridge.application.bluetooth as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    recorded: list[tuple[str, str]] = []

    def fake_remove(mac: str, adapter_mac: str = "") -> None:
        recorded.append((mac, adapter_mac))

    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.delete("/api/v1/bluetooth/devices/AA:BB:CC:DD:EE:02")
    assert resp.status_code == 204
    assert recorded == [("AA:BB:CC:DD:EE:02", "")]
