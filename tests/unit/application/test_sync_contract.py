"""The sync-contract JSON Schema is generated from the API models and kept in the repo."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs" / "contracts" / "sync-contract-v1.json"


def test_contract_file_is_current():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_sync_contract.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_contract_names_every_resource_and_error_code():
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    for name in ("SyncGroup", "SyncSessionOut", "MeasurementOut", "ProposalOut", "ProfileOut", "PendingRestoreOut"):
        assert name in doc["$defs"], name
    assert set(doc["$defs"]["ErrorCode"]["enum"]) >= {
        "ma_unavailable",
        "ma_forbidden",
        "session_active",
        "session_expired",
        "member_gone",
        "not_adjustable",
        "recording_invalid",
        "restore_pending",
    }
    delays = doc["$defs"]["DelaysIn"]["properties"]["delays"]["additionalProperties"]
    assert delays["minimum"] == 0 and delays["maximum"] == 5000
