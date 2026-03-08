"""Unit tests for the DeviceStatus dataclass in sendspin_client.py."""

import pytest

from sendspin_client import DeviceStatus


def test_getitem_known_key():
    status = DeviceStatus(volume=42)
    assert status["volume"] == 42


def test_getitem_unknown_key_raises():
    status = DeviceStatus()
    with pytest.raises(KeyError):
        status["nonexistent"]


def test_setitem_known_key():
    status = DeviceStatus()
    status["volume"] = 55
    assert status.volume == 55


def test_setitem_unknown_key_ignored():
    status = DeviceStatus()
    status["bogus_key"] = "value"
    assert "bogus_key" not in status


def test_get_with_default():
    status = DeviceStatus()
    assert status.get("no_such_field", "fallback") == "fallback"
    assert status.get("volume") == 100


def test_update_known_keys():
    status = DeviceStatus()
    status.update({"volume": 75, "muted": True, "hostname": "myhost"})
    assert status.volume == 75
    assert status.muted is True
    assert status.hostname == "myhost"


def test_update_ignores_unknown():
    status = DeviceStatus()
    status.update({"volume": 80, "unknown_field": 123})
    assert status.volume == 80
    assert "unknown_field" not in status


def test_contains():
    status = DeviceStatus()
    assert "volume" in status
    assert "bogus" not in status


def test_copy_returns_dict():
    status = DeviceStatus()
    result = status.copy()
    assert isinstance(result, dict)
    assert not isinstance(result, DeviceStatus)


def test_copy_excludes_field_names():
    status = DeviceStatus()
    result = status.copy()
    assert "_field_names" not in result


def test_field_names_cached():
    status = DeviceStatus()
    assert isinstance(status._field_names, frozenset)
    assert "volume" in status._field_names
    assert "connected" in status._field_names
    assert "hostname" in status._field_names
    assert "_field_names" not in status._field_names
