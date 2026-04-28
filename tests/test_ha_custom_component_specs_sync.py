"""Verifies the custom_component's spec mirror matches the bridge catalog.

The HA custom_component lives in ``custom_components/sendspin_bridge/`` and
ships separately via HACS, so it can't import from the bridge's
``services/`` package.  Its ``_specs.py`` duplicates the catalog as plain
data; this test fails the build the moment they drift.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_specs_module():
    """Load custom_components/sendspin_bridge/_specs.py without triggering
    the package's ``__init__.py`` (which imports homeassistant)."""
    spec_path = ROOT / "custom_components" / "sendspin_bridge" / "_specs.py"
    spec = importlib.util.spec_from_file_location("cc_specs_test", spec_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # ``dataclasses`` walks sys.modules during processing, so we must
    # register the synthetic module under the same name we passed to
    # ``spec_from_file_location``.
    sys.modules["cc_specs_test"] = module
    spec.loader.exec_module(module)
    return module


cc_specs = _load_specs_module()
from services.ha_entity_model import (  # noqa: E402
    BRIDGE_ENTITIES,
    DEVICE_ENTITIES,
)


def _bridge_index(specs):
    return {s.object_id: s for s in specs}


def _cc_index(specs):
    return {s.object_id: s for s in specs}


@pytest.mark.parametrize("scope", ["device", "bridge"])
def test_object_ids_match(scope):
    if scope == "device":
        bridge_ids = {s.object_id for s in DEVICE_ENTITIES}
        cc_ids = {s.object_id for s in cc_specs.DEVICE_ENTITIES}
    else:
        bridge_ids = {s.object_id for s in BRIDGE_ENTITIES}
        cc_ids = {s.object_id for s in cc_specs.BRIDGE_ENTITIES}
    missing = bridge_ids - cc_ids
    extra = cc_ids - bridge_ids
    assert not missing, f"custom_component is missing specs: {missing}"
    assert not extra, f"custom_component has unknown specs: {extra}"


@pytest.mark.parametrize("scope", ["device", "bridge"])
def test_kinds_and_commands_match(scope):
    if scope == "device":
        bridge_idx = _bridge_index(DEVICE_ENTITIES)
        cc_idx = _cc_index(cc_specs.DEVICE_ENTITIES)
    else:
        bridge_idx = _bridge_index(BRIDGE_ENTITIES)
        cc_idx = _cc_index(cc_specs.BRIDGE_ENTITIES)

    for object_id, bridge_spec in bridge_idx.items():
        cc_spec = cc_idx[object_id]
        # Kind names must match — bridge uses the EntityKind enum, the
        # custom_component stores plain strings.
        assert cc_spec.kind == bridge_spec.kind.value, (
            f"{object_id}: kind drift {cc_spec.kind} vs {bridge_spec.kind.value}"
        )
        assert cc_spec.command == bridge_spec.command, (
            f"{object_id}: command drift {cc_spec.command} vs {bridge_spec.command}"
        )
        assert cc_spec.availability_class == bridge_spec.availability_class, (
            f"{object_id}: availability_class drift "
            f"{cc_spec.availability_class!r} vs {bridge_spec.availability_class!r}"
        )


def test_select_options_match():
    bridge_idx = _bridge_index(DEVICE_ENTITIES)
    cc_idx = _cc_index(cc_specs.DEVICE_ENTITIES)
    for object_id, bridge_spec in bridge_idx.items():
        if bridge_spec.kind.value == "select":
            assert tuple(cc_idx[object_id].options) == tuple(bridge_spec.options), f"{object_id}: select options drift"


def test_number_bounds_match():
    bridge_idx = _bridge_index(DEVICE_ENTITIES)
    cc_idx = _cc_index(cc_specs.DEVICE_ENTITIES)
    for object_id, bridge_spec in bridge_idx.items():
        if bridge_spec.kind.value == "number":
            cc_spec = cc_idx[object_id]
            assert cc_spec.min_value == bridge_spec.min_value
            assert cc_spec.max_value == bridge_spec.max_value
            assert cc_spec.step == bridge_spec.step


def test_no_media_player_in_custom_component():
    """Mirror the MA-deduplication invariant on the HA side too."""
    for spec in cc_specs.DEVICE_ENTITIES + cc_specs.BRIDGE_ENTITIES:
        assert spec.kind != "media_player", f"custom_component must not declare media_player: {spec.object_id}"


def test_manifest_references_zeroconf_service_type():
    import json

    manifest_path = ROOT / "custom_components" / "sendspin_bridge" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "sendspin_bridge"
    assert manifest["config_flow"] is True
    assert any(z.get("type") == "_sendspin-bridge._tcp.local." for z in manifest.get("zeroconf", []))


def test_hacs_json_exists_and_well_formed():
    import json

    path = ROOT / "hacs.json"
    assert path.exists(), "hacs.json missing at repo root — HACS won't index this repo"
    payload = json.loads(path.read_text())
    assert payload.get("name")
    assert payload.get("content_in_root") is False
