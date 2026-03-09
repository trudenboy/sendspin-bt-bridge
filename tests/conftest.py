"""Shared test fixtures for sendspin-bt-bridge."""

import pytest


@pytest.fixture
def tmp_config(tmp_path):
    """Provide a temporary config file and set CONFIG_FILE/CONFIG_DIR."""
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")
    import config as _cfg

    original_file = _cfg.CONFIG_FILE
    original_dir = _cfg.CONFIG_DIR
    _cfg.CONFIG_FILE = config_file
    _cfg.CONFIG_DIR = tmp_path
    yield config_file
    _cfg.CONFIG_FILE = original_file
    _cfg.CONFIG_DIR = original_dir
