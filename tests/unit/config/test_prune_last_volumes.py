"""``_prune_last_volumes`` must match saved volumes to devices case-insensitively.

MAC addresses are compared elsewhere without case sensitivity; if the saved
``LAST_VOLUMES`` key differs in case from the configured device MAC, the volume
must not be pruned (the user would silently lose their saved level on restart).
"""

from __future__ import annotations

from sendspin_bridge.config.migration import _prune_last_volumes

_DEFAULTS = {"LAST_VOLUMES": {}}


def test_saved_volume_survives_mac_case_mismatch():
    config = {
        "BLUETOOTH_DEVICES": [{"mac": "FC:58:FA:EB:08:6C"}],
        "LAST_VOLUMES": {"fc:58:fa:eb:08:6c": 42},  # lowercase saved key
    }
    _prune_last_volumes(config, defaults=_DEFAULTS)
    assert 42 in config["LAST_VOLUMES"].values()


def test_volume_for_unconfigured_device_is_pruned():
    config = {
        "BLUETOOTH_DEVICES": [{"mac": "FC:58:FA:EB:08:6C"}],
        "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 30},
    }
    _prune_last_volumes(config, defaults=_DEFAULTS)
    assert config["LAST_VOLUMES"] == {}


def test_invalid_volume_is_dropped():
    config = {
        "BLUETOOTH_DEVICES": [{"mac": "FC:58:FA:EB:08:6C"}],
        "LAST_VOLUMES": {"FC:58:FA:EB:08:6C": 999},
    }
    _prune_last_volumes(config, defaults=_DEFAULTS)
    assert config["LAST_VOLUMES"] == {}
