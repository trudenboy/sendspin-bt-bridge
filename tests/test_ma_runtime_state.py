"""Tests for services.ma_runtime_state logging behavior."""

import logging

import pytest

import services.ma_runtime_state as ma_runtime_state


@pytest.fixture(autouse=True)
def _reset_ma_groups():
    ma_runtime_state.set_ma_groups({}, [])
    yield
    ma_runtime_state.set_ma_groups({}, [])


def test_set_ma_groups_logs_info_when_cache_changes(caplog):
    mapping = {"player-1": {"id": "syncgroup_1", "name": "Kitchen"}}
    all_groups = [{"id": "syncgroup_1", "name": "Kitchen", "members": []}]

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="services.ma_runtime_state"):
        ma_runtime_state.set_ma_groups(mapping, all_groups)

    assert any(
        record.levelno == logging.INFO and "MA syncgroup cache updated" in record.message for record in caplog.records
    )


def test_set_ma_groups_logs_debug_when_cache_is_unchanged(caplog):
    mapping = {"player-1": {"id": "syncgroup_1", "name": "Kitchen"}}
    all_groups = [{"id": "syncgroup_1", "name": "Kitchen", "members": []}]
    ma_runtime_state.set_ma_groups(mapping, all_groups)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="services.ma_runtime_state"):
        ma_runtime_state.set_ma_groups(mapping, all_groups)

    assert any(
        record.levelno == logging.DEBUG and "MA syncgroup cache unchanged" in record.message
        for record in caplog.records
    )
    assert not any(record.levelno == logging.INFO for record in caplog.records)
