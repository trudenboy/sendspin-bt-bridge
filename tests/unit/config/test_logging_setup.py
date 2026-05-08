"""Tests for the shared ``apply_log_level`` helper."""

from __future__ import annotations

import logging
import os

import pytest

from sendspin_bridge.config.logging_setup import apply_log_level


@pytest.fixture(autouse=True)
def _restore_root_logger_level():
    saved = logging.getLogger().level
    saved_env = os.environ.get("LOG_LEVEL")
    yield
    logging.getLogger().setLevel(saved)
    if saved_env is None:
        os.environ.pop("LOG_LEVEL", None)
    else:
        os.environ["LOG_LEVEL"] = saved_env


def test_apply_log_level_normalizes_lowercase_input():
    assert apply_log_level("debug") == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG
    assert os.environ.get("LOG_LEVEL") == "DEBUG"


def test_apply_log_level_falls_back_to_info_for_invalid_input():
    # Historical behaviour: only INFO and DEBUG are accepted on the surface,
    # anything else (TRACE, WARNING typed by mistake, empty) resolves to INFO.
    for bad in ("TRACE", "warning", "garbage", "", None):
        assert apply_log_level(bad) == "INFO"
        assert logging.getLogger().level == logging.INFO


def test_apply_log_level_strips_whitespace():
    assert apply_log_level("  Debug \t") == "DEBUG"
    assert logging.getLogger().level == logging.DEBUG


def test_apply_log_level_syncs_environment_variable():
    apply_log_level("DEBUG")
    assert os.environ.get("LOG_LEVEL") == "DEBUG"
    apply_log_level("INFO")
    assert os.environ.get("LOG_LEVEL") == "INFO"
