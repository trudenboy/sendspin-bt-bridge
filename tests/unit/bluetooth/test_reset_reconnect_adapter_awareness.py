"""Adapter-awareness for /api/bt/reset_reconnect.

Bonds that live on a non-default adapter (e.g. hci1) must be reset
against that same adapter, otherwise ``remove`` / ``power off`` / ``pair``
all run on the BlueZ default controller and silently fail.

* The endpoint accepts an ``adapter`` field and forwards it to the
  background job.
* Invalid adapter identifiers are rejected up-front.
* Backwards compatibility: a missing ``adapter`` preserves the historic
  "default controller" behaviour (empty string).
* The background routine scopes every phase to that controller — the
  remove and power-cycle verbs via the BlueZ transport's adapter scoping,
  the pair + trust + connect session via its own ``select`` line.
"""

from __future__ import annotations

import re
import threading
from typing import Any

import pytest
from flask import Flask

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def client(tmp_config):
    from sendspin_bridge.web.routes.api_bt import bt_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


def _extract_select_lines(input_text: str) -> list[str]:
    adapters: list[str] = []
    for line in str(input_text or "").splitlines():
        clean = _ANSI_RE.sub("", line).strip()
        if clean.startswith("select "):
            adapters.append(clean.split(" ", 1)[1].strip().upper())
    return adapters


def _scoped_verbs(fake_bluez, mac: str) -> list[str]:
    """Verbs the transport ran under ``select <mac>``, in order."""
    return [c.verb for c in fake_bluez.scoped(mac) if c.kind == "run"]


def test_reset_reconnect_accepts_adapter_and_forwards_it(client, monkeypatch):
    """POST body's ``adapter`` must reach the background job verbatim."""

    import sendspin_bridge.web.routes.api_bt as module

    captured: dict[str, Any] = {}
    done = threading.Event()

    def fake_run(
        job_id: str,
        mac: str,
        adapter: str,
        *,
        no_input_no_output_agent: bool = False,
        allow_hfp_profile: bool = False,
    ) -> None:
        captured["mac"] = mac
        captured["adapter"] = adapter
        captured["no_io"] = no_input_no_output_agent
        captured["allow_hfp"] = allow_hfp_profile
        module.finish_scan_job(job_id, {"success": True})
        done.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post(
        "/api/bt/reset_reconnect",
        json={"mac": "AA:BB:CC:DD:EE:01", "adapter": "C0:FB:F9:62:D7:D6"},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("job_id")
    assert done.wait(2.0), "background thread never invoked _run_reset_reconnect"
    assert captured == {
        "mac": "AA:BB:CC:DD:EE:01",
        "adapter": "C0:FB:F9:62:D7:D6",
        "no_io": False,
        "allow_hfp": False,
    }


def test_reset_reconnect_preserves_default_adapter_when_omitted(client, monkeypatch):
    """Missing ``adapter`` → empty string (pre-existing behaviour)."""

    import sendspin_bridge.web.routes.api_bt as module

    captured: dict[str, Any] = {}
    done = threading.Event()

    def fake_run(job_id: str, mac: str, adapter: str, **_pair_options: Any) -> None:
        captured["adapter"] = adapter
        module.finish_scan_job(job_id, {"success": True})
        done.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post("/api/bt/reset_reconnect", json={"mac": "AA:BB:CC:DD:EE:02"})
    assert resp.status_code == 200
    assert done.wait(2.0)
    assert captured["adapter"] == ""


def test_reset_reconnect_rejects_invalid_adapter(client, monkeypatch):
    """Garbage adapter strings must 400 before spawning the job thread."""

    import sendspin_bridge.web.routes.api_bt as module

    called = threading.Event()

    def fake_run(*_a: Any, **_kw: Any) -> None:
        called.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post(
        "/api/bt/reset_reconnect",
        json={"mac": "AA:BB:CC:DD:EE:03", "adapter": "not-a-mac"},
    )
    assert resp.status_code == 400
    assert not called.is_set()


def _session_selects(fake_bluez) -> list[str]:
    """Controllers the interactive pair session scoped itself to."""
    return [c.adapter_selected for c in fake_bluez.commands if c.kind in ("popen", "send") and c.adapter_selected]


def _pair_ok_script(fake_bluez, mac: str) -> None:
    fake_bluez.session_script(
        [
            ("scan bredr", [f"[NEW] Device {mac} Speaker"]),
            (f"pair {mac}", ["Pairing successful"]),
            (f"connect {mac}", ["Connection successful"]),
        ]
    )


def test_run_reset_reconnect_threads_select_adapter_through_every_phase(monkeypatch, installed_bluez):
    """Remove, power-cycle *and* the pair/trust/connect session must all be
    scoped to the requested controller — otherwise the power cycle hits the
    default controller and pairing happens on the wrong radio.
    """

    import sendspin_bridge.web.routes.api_bt as module

    mac = "AA:BB:CC:DD:EE:04"
    _pair_ok_script(installed_bluez, mac)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)

    job_id = "job-test-1"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, mac, "C0:FB:F9:62:D7:D6")

    # Remove + power-cycle phases, both scoped to the requested controller.
    assert _scoped_verbs(installed_bluez, "C0:FB:F9:62:D7:D6")[:2] == ["remove", "power"]

    # The pair session scopes itself to the same controller…
    assert set(_session_selects(installed_bluez)) == {"C0:FB:F9:62:D7:D6"}
    # …and still actually pairs, trusts and connects.
    sent = " ".join(c.script for c in installed_bluez.commands if c.kind == "send")
    assert f"pair {mac}" in sent
    assert f"trust {mac}" in sent
    assert f"connect {mac}" in sent

    result = module.get_scan_job(job_id)
    assert result["success"] is True
    assert result["connected"] is True


def test_run_reset_reconnect_translates_hci_name_to_controller_mac(monkeypatch, installed_bluez, tmp_path):
    """``bluetoothctl select hci1`` fails on HAOS / LXC with ``Controller
    hci1 not available`` — only the controller MAC is accepted. The fleet
    row's ``<select>`` sends ``hci0``/``hci1`` as its value, so the reset
    flow must translate it to a MAC before issuing ``select`` or the
    entire sequence silently runs against the default controller.

    The translation uses the sysfs-backed kernel map (issue #340): the two
    fake controllers are listed with hci1's MAC first (BlueZ registration
    order), while the kernel labels them hci0=C0:FB:F9:62:D6:9D and
    hci1=C0:FB:F9:62:D7:D6.
    """

    import sendspin_bridge.web.routes.api_bt as module

    mac = "AA:BB:CC:DD:EE:05"
    _pair_ok_script(installed_bluez, mac)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)
    # Pretend the host reports two controllers — hci1's MAC listed FIRST
    # (BlueZ registration order diverges from kernel hciN numbering).
    monkeypatch.setattr(
        module,
        "list_bt_adapters",
        lambda: ["C0:FB:F9:62:D7:D6", "C0:FB:F9:62:D6:9D"],
    )
    # Kernel hciN map: build the installed fake's control with a fake sysfs
    # tree (``fake.control`` is a fresh instance per access, so patch the
    # singleton that get_bluez() actually serves).
    sysfs = tmp_path / "bluetooth"
    for hci, addr in (("hci0", "C0:FB:F9:62:D6:9D"), ("hci1", "C0:FB:F9:62:D7:D6")):
        d = sysfs / hci
        d.mkdir(parents=True)
        (d / "address").write_text(addr + "\n")
    from sendspin_bridge.bluetooth.bluez import BluezControl, set_bluez

    set_bluez(BluezControl(spawner=installed_bluez, sysfs_dir=sysfs))

    job_id = "job-test-hci"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, mac, "hci1")

    # Every phase must name the resolved MAC, never the hciN alias.
    assert _scoped_verbs(installed_bluez, "C0:FB:F9:62:D7:D6")[:2] == ["remove", "power"]
    assert set(_session_selects(installed_bluez)) == {"C0:FB:F9:62:D7:D6"}
    selected = {c.adapter_selected for c in installed_bluez.commands if c.adapter_selected}
    assert "hci1" not in selected


def test_run_reset_reconnect_keeps_hci_name_when_resolution_fails(monkeypatch, installed_bluez, tmp_path):
    """If no controller can be resolved (e.g. all adapters down mid-flow),
    fall back to the supplied ``hciN`` instead of dropping the ``select``
    prefix entirely.  The command may still fail at bluetoothctl layer,
    but silently running against the default controller is worse — a
    failed ``select`` surfaces as the natural "not paired" outcome.
    """

    import sendspin_bridge.web.routes.api_bt as module

    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])
    # No sysfs visibility: the kernel map is empty, so nothing can resolve
    # ``hci0`` and it must pass through unchanged.
    monkeypatch.setattr(installed_bluez.control, "_sysfs_dir", tmp_path / "missing")
    # …and D-Bus can't answer either (the last resolution step before the
    # pass-through; on a host with a live controller it would resolve).
    monkeypatch.setattr(module, "build_hci_map", lambda: {})
    # No controllers anywhere: the endpoint keeps ``hci0`` and the transport
    # has nothing to resolve it against either.
    installed_bluez.on("list", stdout="")

    job_id = "job-test-fallback"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, "AA:BB:CC:DD:EE:06", "hci0")

    removes = [c for c in installed_bluez.commands if c.kind == "run" and c.verb == "remove"]
    assert removes, "remove phase never ran"
    assert removes[0].adapter_selected == "hci0"


def test_run_reset_reconnect_retries_the_pin_ladder(monkeypatch, installed_bluez):
    """Reset-and-reconnect used to give up on the first PIN. Sharing the
    pairing composite means a legacy speaker gets the same ladder the
    manual pair flow has always had.
    """

    import sendspin_bridge.web.routes.api_bt as module

    mac = "AA:BB:CC:DD:EE:07"
    installed_bluez.session_script(
        [
            (f"pair {mac}", ["[agent] Enter PIN code:"]),
            ("0000", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1234", ["Pairing successful"]),
        ]
    )
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)

    job_id = "job-test-pins"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, mac, "C0:FB:F9:62:D7:D6")

    replied = [c.script.strip() for c in installed_bluez.commands if c.kind == "reply"]
    assert replied == ["0000", "1234"]
    assert module.get_scan_job(job_id)["success"] is True
