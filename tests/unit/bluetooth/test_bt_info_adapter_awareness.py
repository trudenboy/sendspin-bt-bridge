"""Adapter-awareness for ``/api/bt/info``.

A bond that lives on a non-default controller (``hci1`` on the production
HAOS VM) is invisible to ``bluetoothctl info`` unless ``select <mac>`` is
issued first — the BlueZ default controller replies ``Device … not
available`` and the info modal in the UI ends up showing only the MAC.

The fix mirrors what rc.4 and rc.5 did for reset/reconnect and add-pair:

* The endpoint accepts an ``adapter`` field and forwards it.
* Invalid adapter identifiers are rejected with 400.
* The helper resolves ``hciN`` → controller MAC before ``select``
  (``bluetoothctl select hci1`` fails on HAOS/LXC).
* When no adapter is supplied, the helper probes each known adapter in
  turn and returns the first response that actually contains device
  fields (``Name:``, ``Paired:``, …) — so existing UI call sites that
  don't yet pass an adapter still work.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_config):
    from sendspin_bridge.web.routes.api_bt import bt_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


_DEVICE_NOT_AVAILABLE = "Device AA:BB:CC:DD:EE:FF not available\n"
_DEVICE_INFO_FULL = (
    "Device AA:BB:CC:DD:EE:FF (public)\n"
    "\tName: Yandex mini 2\n"
    "\tAlias: Yandex mini 2 007 a\n"
    "\tPaired: yes\n"
    "\tTrusted: yes\n"
    "\tConnected: yes\n"
)


def test_get_bt_device_info_issues_select_before_info_when_adapter_provided(monkeypatch):
    """With an explicit adapter, `select <mac>\\ninfo <mac>\\n` must hit stdin."""
    import sendspin_bridge.web.routes.api_bt as module

    captured: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured.append(kw.get("input", "") or "")
        return _FakeCompletedProcess(stdout=_DEVICE_INFO_FULL)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    info = module._get_bt_device_info("AA:BB:CC:DD:EE:FF", adapter="C0:FB:F9:62:D7:D6")

    assert len(captured) == 1
    assert captured[0] == "select C0:FB:F9:62:D7:D6\ninfo AA:BB:CC:DD:EE:FF\n"
    assert info["name"] == "Yandex mini 2"
    assert info["paired"] == "yes"


def test_get_bt_device_info_translates_hci_name_to_controller_mac(monkeypatch):
    """``hci1`` must be resolved via ``list_bt_adapters`` — raw ``hci1``
    fails with ``Controller hci1 not available`` on HAOS/LXC."""
    import sendspin_bridge.web.routes.api_bt as module

    captured: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured.append(kw.get("input", "") or "")
        return _FakeCompletedProcess(stdout=_DEVICE_INFO_FULL)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        module,
        "list_bt_adapters",
        lambda: ["C0:FB:F9:62:D6:9D", "C0:FB:F9:62:D7:D6"],
    )

    module._get_bt_device_info("AA:BB:CC:DD:EE:FF", adapter="hci1")

    assert captured[0] == "select C0:FB:F9:62:D7:D6\ninfo AA:BB:CC:DD:EE:FF\n"


def test_get_bt_device_info_probes_every_adapter_when_none_given(monkeypatch):
    """Without an adapter the helper must try each controller until one
    returns a response with actual device fields (``Name:``/``Paired:``).
    Prior behaviour queried only the default controller."""
    import sendspin_bridge.web.routes.api_bt as module

    captured: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        inp = kw.get("input", "") or ""
        captured.append(inp)
        if "C0:FB:F9:62:D6:9D" in inp:
            return _FakeCompletedProcess(stdout=_DEVICE_NOT_AVAILABLE)
        return _FakeCompletedProcess(stdout=_DEVICE_INFO_FULL)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        module,
        "list_bt_adapters",
        lambda: ["C0:FB:F9:62:D6:9D", "C0:FB:F9:62:D7:D6"],
    )

    info = module._get_bt_device_info("AA:BB:CC:DD:EE:FF")

    # Both adapters probed, hci0 first (empty), hci1 second (hit).
    assert len(captured) == 2
    assert "select C0:FB:F9:62:D6:9D" in captured[0]
    assert "select C0:FB:F9:62:D7:D6" in captured[1]
    assert info["name"] == "Yandex mini 2"
    assert info["paired"] == "yes"


def test_get_bt_device_info_stops_at_first_adapter_with_fields(monkeypatch):
    """If the first adapter already returns a full record, don't keep probing."""
    import sendspin_bridge.web.routes.api_bt as module

    captured: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured.append(kw.get("input", "") or "")
        return _FakeCompletedProcess(stdout=_DEVICE_INFO_FULL)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        module,
        "list_bt_adapters",
        lambda: ["C0:FB:F9:62:D6:9D", "C0:FB:F9:62:D7:D6"],
    )

    module._get_bt_device_info("AA:BB:CC:DD:EE:FF")

    assert len(captured) == 1  # second adapter never probed


def test_api_bt_info_forwards_adapter_field(client, monkeypatch):
    """The ``adapter`` field on the POST body must reach the helper."""
    import sendspin_bridge.web.routes.api_bt as module

    captured: dict[str, Any] = {}

    def fake_helper(mac: str, adapter: str = "") -> dict[str, Any]:
        captured["mac"] = mac
        captured["adapter"] = adapter
        return {"mac": mac}

    monkeypatch.setattr(module, "_get_bt_device_info", fake_helper)

    resp = client.post(
        "/api/bt/info",
        json={"mac": "AA:BB:CC:DD:EE:FF", "adapter": "C0:FB:F9:62:D7:D6"},
    )

    assert resp.status_code == 200
    assert captured == {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "C0:FB:F9:62:D7:D6"}


_DEVICE_INFO_FULL_WITH_UUIDS = (
    "Agent registered\n"
    "[bluetooth]# select C0:FB:F9:62:D7:D6\n"
    "[bluetooth]# info AA:BB:CC:DD:EE:FF\n"
    "Device AA:BB:CC:DD:EE:FF (public)\n"
    "\tName: VAPPEBY Outdoor\n"
    "\tAlias: VAPPEBY Outdoor\n"
    "\tClass: 0x00240404\n"
    "\tIcon: audio-headset\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: no\n"
    "\tLegacyPairing: no\n"
    "\tUUID: Vendor specific           (00000000-deca-fade-deca-deafdecacaff)\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tUUID: A/V Remote Control Target (0000110c-0000-1000-8000-00805f9b34fb)\n"
    "\tUUID: A/V Remote Control        (0000110e-0000-1000-8000-00805f9b34fb)\n"
    "\tUUID: PnP Information           (00001200-0000-1000-8000-00805f9b34fb)\n"
    "\tModalias: bluetooth:vE003p3528d0001\n"
)


def test_get_bt_device_info_raw_includes_uuids_modalias_and_legacypairing(monkeypatch):
    """The info modal in the UI now renders ``info["raw"]`` directly so
    operators see everything ``bluetoothctl info`` outputs.  Pin the
    parser contract: every non-prompt line — including UUIDs (the
    A2DP Sink / AVRCP / etc. service UUID list that's load-bearing
    for diagnosing why a speaker won't play), Modalias (vendor /
    product / version), LegacyPairing, and the ``Class`` octet —
    must reach the ``raw`` array.  Without UUIDs in the modal,
    issue #168 would have taken another round of asking the
    reporter for ``bluetoothctl info`` over SSH."""
    import sendspin_bridge.web.routes.api_bt as module

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_a, **_kw: _FakeCompletedProcess(stdout=_DEVICE_INFO_FULL_WITH_UUIDS),
    )

    info = module._get_bt_device_info("AA:BB:CC:DD:EE:FF", adapter="C0:FB:F9:62:D7:D6")

    raw = info.get("raw") or []
    raw_text = "\n".join(raw)
    # Header + every diagnostic field the parser used to drop
    assert "Device AA:BB:CC:DD:EE:FF (public)" in raw
    assert "Class: 0x00240404" in raw_text
    assert "LegacyPairing: no" in raw_text
    # All five UUIDs must be present verbatim — UI treats them as
    # the load-bearing diagnostic for "does this speaker actually
    # advertise A2DP Sink".
    assert "UUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)" in raw_text
    assert "UUID: A/V Remote Control Target (0000110c-0000-1000-8000-00805f9b34fb)" in raw_text
    assert "UUID: A/V Remote Control        (0000110e-0000-1000-8000-00805f9b34fb)" in raw_text
    assert "UUID: PnP Information           (00001200-0000-1000-8000-00805f9b34fb)" in raw_text
    assert "UUID: Vendor specific           (00000000-deca-fade-deca-deafdecacaff)" in raw_text
    # Vendor identity for the speaker
    assert "Modalias: bluetooth:vE003p3528d0001" in raw_text


def test_api_bt_info_rejects_invalid_adapter(client, monkeypatch):
    """Garbage adapter strings must 400 before touching bluetoothctl."""
    import sendspin_bridge.web.routes.api_bt as module

    called = {"n": 0}

    def fake_helper(*_a: Any, **_kw: Any) -> dict[str, Any]:
        called["n"] += 1
        return {}

    monkeypatch.setattr(module, "_get_bt_device_info", fake_helper)

    resp = client.post(
        "/api/bt/info",
        json={"mac": "AA:BB:CC:DD:EE:FF", "adapter": "not-a-mac"},
    )

    assert resp.status_code == 400
    assert called["n"] == 0
