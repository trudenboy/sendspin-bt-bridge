"""Multi-adapter behaviour for /api/bt/paired and /api/bt/remove.

Prior to this change, both endpoints only talked to the BlueZ default
controller, so bonds living on a non-default adapter were invisible and
could not be removed via the UI.  These tests lock in the new behaviour:

* ``/api/bt/paired`` enumerates every known adapter and reports which
  adapter(s) each device is bonded with.
* ``/api/bt/remove`` accepts an optional ``adapter_mac`` and, when it is
  absent, removes the bond from *every* adapter rather than only the
  default one.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def client(tmp_config):
    from routes.api_bt import bt_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


def _make_paired_stdout(devices: list[tuple[str, str]]) -> str:
    return "\n".join(f"Device {mac} {name}" for mac, name in devices)


def _extract_selected_adapter(input_text: str) -> str:
    for line in str(input_text or "").splitlines():
        clean = _ANSI_RE.sub("", line).strip()
        if clean.startswith("select "):
            return clean.split(" ", 1)[1].strip().upper()
    return ""


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_paired_enumerates_every_adapter(client, monkeypatch):
    """Two adapters, one device bonded on each → both surface with adapters[]."""

    import routes.api_bt as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    per_adapter: dict[str, list[tuple[str, str]]] = {
        "C0:FB:F9:62:D7:D6": [("AA:AA:AA:AA:AA:01", "Speaker Alpha")],
        "00:15:83:FF:8F:2B": [("BB:BB:BB:BB:BB:02", "Speaker Bravo")],
    }

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        input_text = kw.get("input", "") or ""
        selected = _extract_selected_adapter(input_text)
        return _FakeCompletedProcess(stdout=_make_paired_stdout(per_adapter.get(selected, [])))

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    resp = client.get("/api/bt/paired")
    assert resp.status_code == 200
    devices = resp.get_json()["devices"]

    by_mac = {d["mac"]: d for d in devices}
    assert set(by_mac) == {"AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"}
    assert by_mac["AA:AA:AA:AA:AA:01"]["adapters"] == ["C0:FB:F9:62:D7:D6"]
    assert by_mac["BB:BB:BB:BB:BB:02"]["adapters"] == ["00:15:83:FF:8F:2B"]


def test_paired_merges_device_bonded_on_multiple_adapters(client, monkeypatch):
    """Same MAC visible on two adapters collapses to one entry with both MACs."""

    import routes.api_bt as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        input_text = kw.get("input", "") or ""
        selected = _extract_selected_adapter(input_text)
        if selected in adapters:
            return _FakeCompletedProcess(stdout=_make_paired_stdout([("CC:CC:CC:CC:CC:03", "Shared Speaker")]))
        return _FakeCompletedProcess()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    resp = client.get("/api/bt/paired")
    assert resp.status_code == 200
    devices = resp.get_json()["devices"]

    assert len(devices) == 1
    entry = devices[0]
    assert entry["mac"] == "CC:CC:CC:CC:CC:03"
    assert sorted(entry["adapters"]) == sorted(adapters)


def test_paired_falls_back_when_adapter_list_is_empty(client, monkeypatch):
    """Environments where ``bluetoothctl list`` fails still produce a list."""

    import routes.api_bt as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(stdout=_make_paired_stdout([("DD:DD:DD:DD:DD:04", "Lone Speaker")]))

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    resp = client.get("/api/bt/paired")
    assert resp.status_code == 200
    devices = resp.get_json()["devices"]
    assert any(d["mac"] == "DD:DD:DD:DD:DD:04" for d in devices)


def test_paired_filters_unnamed_devices_by_default(client, monkeypatch):
    import routes.api_bt as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["11:11:11:11:11:11"])

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(
            stdout=_make_paired_stdout(
                [
                    ("AA:BB:CC:DD:EE:AA", "Real Speaker"),
                    ("AA:BB:CC:DD:EE:BB", "AA-BB-CC-DD-EE-BB"),  # MAC-as-name → dropped
                ]
            )
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    resp = client.get("/api/bt/paired")
    assert resp.status_code == 200
    macs = {d["mac"] for d in resp.get_json()["devices"]}
    assert macs == {"AA:BB:CC:DD:EE:AA"}

    resp_all = client.get("/api/bt/paired?filter=0")
    macs_all = {d["mac"] for d in resp_all.get_json()["devices"]}
    assert macs_all == {"AA:BB:CC:DD:EE:AA", "AA:BB:CC:DD:EE:BB"}


def test_remove_without_adapter_mac_targets_every_adapter(client, monkeypatch):
    import routes.api_bt as module

    adapters = ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"]
    monkeypatch.setattr(module, "list_bt_adapters", lambda: list(adapters))

    recorded: list[tuple[str, str]] = []

    def fake_remove(mac: str, adapter_mac: str = "") -> None:
        recorded.append((mac, adapter_mac))

    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.post("/api/bt/remove", json={"mac": "AA:BB:CC:DD:EE:01"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["mac"] == "AA:BB:CC:DD:EE:01"

    assert sorted(recorded) == sorted([("AA:BB:CC:DD:EE:01", adapter) for adapter in adapters])


def test_remove_with_adapter_mac_only_targets_that_adapter(client, monkeypatch):
    import routes.api_bt as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.post(
        "/api/bt/remove",
        json={"mac": "AA:BB:CC:DD:EE:01", "adapter_mac": "00:15:83:FF:8F:2B"},
    )
    assert resp.status_code == 200
    fake_remove.assert_called_once_with("AA:BB:CC:DD:EE:01", "00:15:83:FF:8F:2B")


def test_remove_rejects_invalid_adapter_mac(client, monkeypatch):
    import routes.api_bt as module

    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.post(
        "/api/bt/remove",
        json={"mac": "AA:BB:CC:DD:EE:01", "adapter_mac": "not-a-mac"},
    )
    assert resp.status_code == 400
    fake_remove.assert_not_called()


def test_remove_rejects_adapter_mac_not_present_on_host(client, monkeypatch):
    """A syntactically-valid adapter MAC that isn't reported by
    ``list_bt_adapters`` must return 400 instead of silently "succeeding"
    against the default controller while the ``select`` failed."""

    import routes.api_bt as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: ["C0:FB:F9:62:D7:D6", "00:15:83:FF:8F:2B"])
    fake_remove = MagicMock()
    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.post(
        "/api/bt/remove",
        json={"mac": "AA:BB:CC:DD:EE:01", "adapter_mac": "DE:AD:BE:EF:00:01"},
    )
    assert resp.status_code == 400
    body = resp.get_json() or {}
    assert "adapter" in (body.get("error") or "").lower()
    fake_remove.assert_not_called()


def test_remove_without_adapters_still_calls_default(client, monkeypatch):
    """Pre-existing behaviour preserved when no adapters are known."""

    import routes.api_bt as module

    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    recorded: list[tuple[str, str]] = []

    def fake_remove(mac: str, adapter_mac: str = "") -> None:
        recorded.append((mac, adapter_mac))

    monkeypatch.setattr(module, "_bt_remove_device", fake_remove)

    resp = client.post("/api/bt/remove", json={"mac": "AA:BB:CC:DD:EE:02"})
    assert resp.status_code == 200
    assert recorded == [("AA:BB:CC:DD:EE:02", "")]
