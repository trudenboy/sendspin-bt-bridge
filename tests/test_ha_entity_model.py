"""Catalog invariants for ``services/ha_entity_model.py``.

These tests enforce the contract that the catalog cannot grow into MA-owned
territory (no ``media_player``, no playback fields) and that every spec is
internally consistent (unique object_id, valid extractor signature, sensible
constraints for number/select).
"""

from __future__ import annotations

import inspect

import pytest

from services import ha_entity_model as M

# ---------------------------------------------------------------------------
# Catalog uniqueness & coverage
# ---------------------------------------------------------------------------


def test_device_object_ids_unique():
    ids = [spec.object_id for spec in M.DEVICE_ENTITIES]
    assert len(ids) == len(set(ids)), f"duplicate object_id in DEVICE_ENTITIES: {ids}"


def test_bridge_object_ids_unique():
    ids = [spec.object_id for spec in M.BRIDGE_ENTITIES]
    assert len(ids) == len(set(ids)), f"duplicate object_id in BRIDGE_ENTITIES: {ids}"


def test_no_overlap_between_device_and_bridge_object_ids():
    device_ids = {spec.object_id for spec in M.DEVICE_ENTITIES}
    bridge_ids = {spec.object_id for spec in M.BRIDGE_ENTITIES}
    overlap = device_ids & bridge_ids
    assert not overlap, f"object_id collision device∩bridge: {overlap}"


# ---------------------------------------------------------------------------
# MA-deduplication contract
# ---------------------------------------------------------------------------


def test_no_media_player_in_catalog():
    """Hard rule: MA's HA integration owns ``media_player.<name>``.

    Our catalog must never add one — duplicates would create two entities
    with the same role on one HA Device card.
    """
    for spec in [*M.DEVICE_ENTITIES, *M.BRIDGE_ENTITIES]:
        assert spec.kind.value not in M.MA_OWNED_KINDS, f"spec {spec.object_id!r} uses MA-owned kind {spec.kind.value}"


@pytest.mark.parametrize(
    "field_name",
    sorted(M.MA_OWNED_DEVICE_FIELDS),
)
def test_no_extractor_reads_ma_owned_field(field_name):
    """Static check: source of every device extractor must not reference
    MA-owned DeviceStatus keys.

    This catches drift where someone adds an extractor like
    ``lambda d, _: d.get("volume")`` that would shadow MA's volume control.
    """
    for spec in M.DEVICE_ENTITIES:
        if spec.extractor is None:
            continue
        try:
            source = inspect.getsource(spec.extractor)
        except (OSError, TypeError):
            continue
        # Match the key as a string literal — avoid flagging identifier names.
        bad = f'"{field_name}"' in source or f"'{field_name}'" in source
        assert not bad, (
            f"extractor for {spec.object_id!r} references MA-owned field {field_name!r}; "
            "MA's HA integration already exposes it via media_player"
        )


# ---------------------------------------------------------------------------
# Per-spec consistency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_device_spec_has_state_or_command(spec):
    """Every spec must either produce state (extractor) or accept a command,
    or both. A pure ornamental entity is meaningless."""
    assert spec.extractor is not None or spec.command is not None, (
        f"spec {spec.object_id!r} has neither extractor nor command"
    )


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_select_specs_have_options(spec):
    if spec.kind is M.EntityKind.SELECT:
        assert spec.options, f"select {spec.object_id!r} has no options"
        # No empty / duplicate options.
        assert len(set(spec.options)) == len(spec.options)
        for opt in spec.options:
            assert opt and opt.strip(), f"empty option in {spec.object_id!r}"


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_number_specs_have_bounds(spec):
    if spec.kind is M.EntityKind.NUMBER:
        assert spec.min_value is not None, f"number {spec.object_id!r} missing min_value"
        assert spec.max_value is not None, f"number {spec.object_id!r} missing max_value"
        assert spec.min_value <= spec.max_value
        assert spec.step is not None and spec.step > 0


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_button_specs_have_command_no_extractor(spec):
    if spec.kind is M.EntityKind.BUTTON:
        assert spec.command, f"button {spec.object_id!r} missing command"
        assert spec.extractor is None, f"button {spec.object_id!r} should not have an extractor"


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_writable_specs_have_command(spec):
    if spec.kind in (M.EntityKind.SWITCH, M.EntityKind.SELECT, M.EntityKind.NUMBER):
        assert spec.command, f"writable {spec.kind.value} {spec.object_id!r} missing command"


# ---------------------------------------------------------------------------
# Extractor smoke: every extractor returns a sane value on synthetic input
# ---------------------------------------------------------------------------


_SYNTHETIC_DEVICE = {
    "bluetooth_connected": True,
    "audio_streaming": True,
    "reanchoring": False,
    "reconnecting": False,
    "bt_standby": False,
    "bt_power_save": False,
    "bt_management_enabled": True,
    "enabled": True,
    "rssi_dbm": -55,
    "battery_level": 87,
    "audio_format": "SBC",
    "reanchor_count": 3,
    "last_sync_error_ms": 12.5,
    "reconnect_attempt": 0,
    "last_error": None,
    "health_summary": {"state": "streaming"},
    "idle_mode": "power_save",
    "keep_alive_method": "infrasound",
    "static_delay_ms": 250,
    "power_save_delay_minutes": 5,
}

_SYNTHETIC_BRIDGE = {
    "version": "2.65.0",
    "ma_connected": True,
    "startup_progress": {"phase": "ready"},
    "runtime_mode": "production",
    "update_available": {"available": True, "latest": "2.65.1"},
}


@pytest.mark.parametrize("spec", M.DEVICE_ENTITIES, ids=lambda s: s.object_id)
def test_device_extractor_callable(spec):
    if spec.extractor is None:
        return
    value = spec.extractor(_SYNTHETIC_DEVICE, _SYNTHETIC_BRIDGE)
    # The synthetic dict has everything populated; result must not be a
    # raw exception sentinel and types must match the kind.
    assert value is not None or spec.kind is M.EntityKind.SENSOR  # sensors may return None


@pytest.mark.parametrize("spec", M.BRIDGE_ENTITIES, ids=lambda s: s.object_id)
def test_bridge_extractor_callable(spec):
    if spec.extractor is None:
        return
    spec.extractor(_SYNTHETIC_BRIDGE)  # must not raise


def test_extractors_handle_empty_dicts():
    """Critical: missing fields must return safe defaults, not blow up.

    Real DeviceSnapshot dicts have optional fields all over the place
    (e.g. battery_level only present for some speakers).
    """
    for spec in M.DEVICE_ENTITIES:
        if spec.extractor is None:
            continue
        spec.extractor({}, {})  # must not raise
    for spec in M.BRIDGE_ENTITIES:
        if spec.extractor is None:
            continue
        spec.extractor({})  # must not raise


# ---------------------------------------------------------------------------
# unique_id helpers
# ---------------------------------------------------------------------------


def test_device_unique_id_format():
    spec = M.DEVICE_ENTITIES[0]
    uid = M.device_unique_id("abcdef-1234", spec)
    assert uid == f"sendspin_abcdef-1234_{spec.object_id}"


def test_bridge_unique_id_format():
    spec = M.BRIDGE_ENTITIES[0]
    uid = M.bridge_unique_id("bridge-haos", spec)
    assert uid == f"sendspin_bridge_bridge-haos_{spec.object_id}"


def test_entity_index_lookup_full_coverage():
    table = M.entity_index_by_object_id()
    expected = {s.object_id for s in M.DEVICE_ENTITIES} | {s.object_id for s in M.BRIDGE_ENTITIES}
    assert set(table.keys()) == expected


def test_command_specs_only_yield_specs_with_commands():
    for cmd, spec in M.device_command_specs().items():
        assert spec.command == cmd
    for cmd, spec in M.bridge_command_specs().items():
        assert spec.command == cmd


def test_command_names_unique_across_device_catalog():
    cmds = [s.command for s in M.DEVICE_ENTITIES if s.command]
    assert len(cmds) == len(set(cmds))


def test_command_names_unique_across_bridge_catalog():
    cmds = [s.command for s in M.BRIDGE_ENTITIES if s.command]
    assert len(cmds) == len(set(cmds))
