"""Tests for the MA-identifier helper that drives device_registry merge.

The custom_component package ``__init__.py`` imports the ``homeassistant``
runtime, which isn't available in the bridge test env. Load
``_ma_compat`` directly via spec loader so the test stays light.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    path = ROOT / "custom_components" / "sendspin_bridge" / "_ma_compat.py"
    spec = importlib.util.spec_from_file_location("cc_ma_compat_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["cc_ma_compat_test"] = module
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
ma_identifier_for_player = _mod.ma_identifier_for_player


def test_ma_identifier_canonical_uuid():
    """UUIDv5(MAC) → ``up<hex_no_dashes>`` matches MA's identifier scheme.

    Reference fingerprint observed in HAOS production (issue #210
    follow-up): for ENEBY20 with MAC ``FC:58:FA:EB:08:6C`` the bridge's
    UUIDv5 player_id is ``fcc3c5f3-15b2-5ddb-99d2-f64b915d8c25`` and MA
    registers the device with identifier ``upfcc3c5f315b25ddb99d2f64b915d8c25``.
    """
    assert ma_identifier_for_player("fcc3c5f3-15b2-5ddb-99d2-f64b915d8c25") == "upfcc3c5f315b25ddb99d2f64b915d8c25"


def test_ma_identifier_uppercase_uuid_normalized():
    """Identifier is case-insensitive: uppercase input still produces the
    canonical lowercase MA form."""
    assert ma_identifier_for_player("FCC3C5F3-15B2-5DDB-99D2-F64B915D8C25") == "upfcc3c5f315b25ddb99d2f64b915d8c25"


def test_ma_identifier_non_uuid_player_id_returns_none():
    """Older configs may have used name-derived player_ids; those don't
    match MA's UUID-keyed entry, so returning ``None`` (skip) is safer
    than emitting a bogus identifier."""
    assert ma_identifier_for_player("eneby20") is None
    assert ma_identifier_for_player("eneby20-haos") is None
    assert ma_identifier_for_player("12345") is None


def test_ma_identifier_empty_or_none_returns_none():
    assert ma_identifier_for_player("") is None
    assert ma_identifier_for_player("   ") is None
    assert ma_identifier_for_player(None) is None  # type: ignore[arg-type]


def test_ma_identifier_partial_uuid_returns_none():
    """Truncated / malformed UUIDs must not silently produce a half-valid
    identifier."""
    assert ma_identifier_for_player("fcc3c5f3-15b2-5ddb-99d2-f64b915d8c2") is None
    assert ma_identifier_for_player("fcc3c5f3-15b2-5ddb-99d2-f64b915d8c2") is None
    assert ma_identifier_for_player("not-a-uuid-but-long-enough") is None


def test_ma_identifier_rejects_non_string():
    """Defensive: any non-string input collapses to ``None``."""
    assert ma_identifier_for_player(12345) is None  # type: ignore[arg-type]
    assert ma_identifier_for_player(b"abc") is None  # type: ignore[arg-type]
