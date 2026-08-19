"""Post-scan device→adapter attribution (issue #340, secondary defect).

Per-adapter device enumeration must run in dedicated bluetoothctl
sessions.  In the long-lived scan session the ``select <MAC>; show``
marker lines interleave with async discovery notifications on piped
stdin — the same unreliability that produced the alias swap in #193 —
so devices could be attributed to whichever controller happened to be
selected when the output flushed.

The scan itself now runs as the transport's scan composite; these tests
assert the endpoint still attributes each discovered device to the
controller that actually enumerated it.
"""

from __future__ import annotations

from unittest.mock import patch

from sendspin_bridge.bridge.state import create_scan_job, get_scan_job
from sendspin_bridge.web.routes import api_bt

ADAPTER_A = "A0:AD:9F:6E:B2:D5"
ADAPTER_B = "88:A2:9E:C0:07:0D"
SPEAKER_A = "FC:58:FA:EB:08:6C"
SPEAKER_B = "30:21:0E:0A:AE:5A"


def _enum_output(adapter_mac: str, device_mac: str, device_name: str) -> str:
    return f"Controller {adapter_mac} (public)\n\tPowered: yes\nDevice {device_mac} {device_name}\n"


def _scan_devices(installed_bluez, adapter: str) -> list[dict]:
    """Run the background scan routine and return its result devices."""
    job_id = f"job-{adapter}"
    create_scan_job(job_id)
    with patch.object(api_bt, "list_bt_adapters", return_value=[ADAPTER_A, ADAPTER_B]):
        api_bt._run_bt_scan(job_id, adapter, audio_only=False)
    job = get_scan_job(job_id) or {}
    return job.get("devices", [])


def test_enumeration_runs_in_dedicated_sessions_and_attributes_devices(installed_bluez):
    installed_bluez.session_script(
        [
            (
                "scan bredr",
                [f"[NEW] Device {SPEAKER_A} ENEBY20", f"[NEW] Device {SPEAKER_B} Lenco LS-500"],
            )
        ]
    )
    installed_bluez.on_adapter(ADAPTER_A).on("devices", stdout=_enum_output(ADAPTER_A, SPEAKER_A, "ENEBY20"))
    installed_bluez.on_adapter(ADAPTER_B).on("devices", stdout=_enum_output(ADAPTER_B, SPEAKER_B, "Lenco LS-500"))

    devices = _scan_devices(installed_bluez, ADAPTER_A)

    # The enumeration ran as its own adapter-scoped one-shot, not inside the
    # long-lived scan session.
    enums = [c for c in installed_bluez.commands if c.kind == "run" and "devices" in c.script]
    assert [c.adapter_selected for c in enums] == [ADAPTER_A]

    by_mac = {d["mac"]: d for d in devices}
    assert by_mac[SPEAKER_A]["adapter"] == ADAPTER_A
    assert by_mac[SPEAKER_A]["name"] == "ENEBY20"


def test_enumeration_failure_degrades_to_unattributed(installed_bluez):
    installed_bluez.session_script([("scan bredr", [f"[NEW] Device {SPEAKER_A} ENEBY20"])])
    installed_bluez.fail("devices", OSError("bluetoothctl unavailable"))

    devices = _scan_devices(installed_bluez, ADAPTER_A)

    # Scan results survive; devices simply stay unattributed.
    by_mac = {d["mac"]: d for d in devices}
    assert SPEAKER_A in by_mac
    assert by_mac[SPEAKER_A]["adapter"] == ""
