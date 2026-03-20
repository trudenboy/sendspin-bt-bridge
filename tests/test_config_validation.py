from __future__ import annotations

from services.config_validation import validate_uploaded_config


def test_validate_uploaded_config_adds_schema_version_warning_when_missing():
    result = validate_uploaded_config(
        {
            "SENDSPIN_PORT": "9000",
            "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
        }
    )

    assert result.is_valid is True
    assert result.normalized_config["CONFIG_SCHEMA_VERSION"] == 1
    assert result.normalized_config["SENDSPIN_PORT"] == 9000
    assert result.normalized_config["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert result.warnings[0].field == "CONFIG_SCHEMA_VERSION"


def test_validate_uploaded_config_reports_duplicate_device_macs():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF"},
                {"mac": "aa:bb:cc:dd:ee:ff"},
            ],
        }
    )

    assert result.is_valid is False
    assert result.errors[0].field == "BLUETOOTH_DEVICES[1].mac"
    assert result.errors[0].message == "Duplicate MAC address: AA:BB:CC:DD:EE:FF"


def test_validate_uploaded_config_normalizes_update_channel():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "UPDATE_CHANNEL": "RC",
            "BLUETOOTH_DEVICES": [],
        }
    )

    assert result.is_valid is True
    assert result.normalized_config["UPDATE_CHANNEL"] == "rc"


def test_validate_uploaded_config_normalizes_optional_top_level_ports():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "WEB_PORT": "18080",
            "BASE_LISTEN_PORT": "19000",
            "BLUETOOTH_DEVICES": [],
        }
    )

    assert result.is_valid is True
    assert result.normalized_config["WEB_PORT"] == 18080
    assert result.normalized_config["BASE_LISTEN_PORT"] == 19000


def test_validate_uploaded_config_rejects_invalid_optional_top_level_ports():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "WEB_PORT": "99999",
            "BLUETOOTH_DEVICES": [],
        }
    )

    assert result.is_valid is False
    assert result.errors[0].field == "WEB_PORT"
    assert result.errors[0].message == "Invalid WEB_PORT: 99999"


def test_validate_uploaded_config_rejects_invalid_update_channel():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "UPDATE_CHANNEL": "nightly",
            "BLUETOOTH_DEVICES": [],
        }
    )

    assert result.is_valid is False
    assert result.errors[0].field == "UPDATE_CHANNEL"
    assert result.errors[0].message == "Invalid UPDATE_CHANNEL: nightly"


def test_validate_uploaded_config_rejects_future_schema_version():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 999,
            "BLUETOOTH_DEVICES": [],
        }
    )

    assert result.is_valid is False
    assert result.errors[0].field == "CONFIG_SCHEMA_VERSION"
    assert "Unsupported CONFIG_SCHEMA_VERSION" in result.errors[0].message


def test_validate_uploaded_config_migrates_legacy_shape():
    result = validate_uploaded_config(
        {
            "BLUETOOTH_MAC": "aa:bb:cc:dd:ee:ff",
            "LAST_VOLUME": 40,
        }
    )

    assert result.is_valid is True
    assert result.normalized_config["BLUETOOTH_DEVICES"] == [
        {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "", "player_name": "Sendspin Player"}
    ]
    assert result.normalized_config["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 40}
    assert any(issue.field == "BLUETOOTH_DEVICES" for issue in result.warnings)
