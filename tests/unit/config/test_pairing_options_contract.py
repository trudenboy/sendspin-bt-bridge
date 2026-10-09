"""Risky pairing choices are per-attempt request options, never persisted settings."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"


def test_hfp_is_a_per_pair_option_not_a_persisted_setting() -> None:
    from sendspin_bridge.api.routers.bluetooth import PairingIn, ResetIn

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "ALLOW_HFP_PROFILE" not in schema["properties"]
    assert PairingIn(mac="AA:BB:CC:DD:EE:FF").allow_hfp_profile is False
    assert ResetIn(mac="AA:BB:CC:DD:EE:FF").allow_hfp_profile is False


def test_legacy_global_just_works_setting_is_removed_from_schema() -> None:
    from sendspin_bridge.api.routers.bluetooth import PairingIn

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert "EXPERIMENTAL_PAIR_JUST_WORKS" not in schema["properties"]
    assert PairingIn(mac="AA:BB:CC:DD:EE:FF").no_input_no_output_agent is False
