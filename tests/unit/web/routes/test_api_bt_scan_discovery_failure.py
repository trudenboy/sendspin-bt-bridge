"""A controller that refuses to start discovery must not look like an empty room.

Field incident: a CSR8510 dongle wedged its firmware after a failed
pairing.  Every ``scan bredr`` was answered with
``Failed to start discovery: org.bluez.Error.InProgress`` and then
silence, so the scan modal reported "no devices found" for as long as the
adapter stayed stuck — with nothing pointing at the real cause.  The scan
result now carries that refusal.
"""

from __future__ import annotations

from unittest.mock import patch

from sendspin_bridge.bridge.state import create_scan_job, get_scan_job
from sendspin_bridge.web.routes import api_bt

ADAPTER = "A0:AD:9F:6E:B2:D5"
SPEAKER = "FC:58:FA:EB:08:6C"
IN_PROGRESS = "Failed to start discovery: org.bluez.Error.InProgress"


def _run_scan(job_id: str) -> dict:
    create_scan_job(job_id)
    with patch.object(api_bt, "list_bt_adapters", return_value=[ADAPTER]):
        api_bt._run_bt_scan(job_id, ADAPTER, audio_only=False)
    return get_scan_job(job_id) or {}


def test_refused_discovery_with_no_devices_reports_the_refusal(installed_bluez):
    installed_bluez.session_script([("scan bredr", [IN_PROGRESS])])
    installed_bluez.on("devices", stdout="")

    job = _run_scan("job-discovery-refused")

    assert job.get("devices") == []
    error = job.get("error", "")
    assert "discovery" in error.lower()
    # The operator needs the actionable part: the controller is stuck, not empty.
    assert "in progress" in error.lower() or "InProgress" in error
    assert "adapter" in error.lower()


def test_refused_discovery_still_returns_whatever_was_found(installed_bluez):
    """One controller refusing discovery must not discard results — the
    refusal is reported alongside them instead of replacing them."""
    installed_bluez.session_script([("scan bredr", [IN_PROGRESS, f"[NEW] Device {SPEAKER} ENEBY20"])])
    installed_bluez.on("devices", stdout="")

    job = _run_scan("job-discovery-partial")

    assert [d["mac"] for d in job.get("devices", [])] == [SPEAKER]
    assert not job.get("error")
    assert "InProgress" in job["stats"]["discovery_error"]
