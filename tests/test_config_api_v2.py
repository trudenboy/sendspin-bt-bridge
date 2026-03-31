"""Tests for v2 players[] schema support in config API and validation."""

from __future__ import annotations

import json
import sys

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import config

    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config, "_config_load_logged_once", False, raising=False)
    config_file.write_text(json.dumps({}))

    # Also patch services.bluetooth._CONFIG_FILE so persist_device_enabled
    # writes to the same temp location.
    import services.bluetooth as _bt_mod

    monkeypatch.setattr(_bt_mod, "_CONFIG_FILE", config_file)

    # Patch module-level CONFIG_FILE in routes that import it directly
    try:
        import routes.api_config as _api_config_mod

        monkeypatch.setattr(_api_config_mod, "CONFIG_FILE", config_file)
    except Exception:
        pass


def _cancel_api_volume_timers():
    from routes import api as api_mod

    timers = getattr(api_mod, "_volume_persist_timers", {})
    pending = list(timers.values())
    if pending:
        timers.clear()
    for timer in pending:
        timer.cancel()


@pytest.fixture(autouse=True)
def _clear_volume_persist_timers():
    _cancel_api_volume_timers()
    yield
    _cancel_api_volume_timers()


@pytest.fixture()
def client():
    """Return a Flask test client with config_bp registered."""
    _stashed = {}
    for mod_name in [
        "routes.api",
        "routes.api_bt",
        "routes.api_config",
        "routes.api_ma",
        "routes.api_status",
        "routes.auth",
        "routes.views",
        "routes",
    ]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from flask import Flask

    from routes.api import api_bp
    from routes.api_bt import bt_bp
    from routes.api_config import config_bp
    from routes.api_ma import ma_bp
    from routes.api_status import status_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(ma_bp)
    app.register_blueprint(status_bp)

    yield app.test_client()

    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


def _write_config(tmp_path, data):
    (tmp_path / "config.json").write_text(json.dumps(data))


def _read_config(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


# ---------------------------------------------------------------------------
# Config GET — players key present
# ---------------------------------------------------------------------------


def test_config_get_includes_players_key(client, tmp_path):
    """GET /api/config response includes 'players' key."""
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [], "players": []})
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "players" in data


def test_config_get_includes_migrated_players(client, tmp_path):
    """GET /api/config returns players migrated from BLUETOOTH_DEVICES."""
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"},
            ],
        },
    )
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["players"]) == 1
    assert data["players"][0]["player_name"] == "Speaker1"
    assert data["players"][0]["backend"]["type"] == "bluetooth_a2dp"


# ---------------------------------------------------------------------------
# Config POST — players[] payload
# ---------------------------------------------------------------------------


def test_config_post_with_players_saves(client, tmp_path):
    """POST /api/config with players[] persists them to config file."""
    player = {
        "id": "test-id",
        "player_name": "TestSpeaker",
        "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
        "enabled": True,
    }
    payload = {
        "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "TestSpeaker"}],
        "players": [player],
    }
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200
    saved = _read_config(tmp_path)
    assert "players" in saved
    assert len(saved["players"]) == 1
    assert saved["players"][0]["player_name"] == "TestSpeaker"


def test_config_post_both_schemas_players_wins(client, tmp_path):
    """When POST includes both BLUETOOTH_DEVICES and players, players wins."""
    payload = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "DeviceSpeaker"},
        ],
        "players": [
            {"id": "p1", "player_name": "Player1", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}},
            {"id": "p2", "player_name": "Player2", "backend": {"type": "bluetooth_a2dp", "mac": "11:22:33:44:55:66"}},
        ],
    }
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200
    saved = _read_config(tmp_path)
    assert len(saved["players"]) == 2
    assert saved["players"][0]["player_name"] == "Player1"
    assert saved["players"][1]["player_name"] == "Player2"


def test_config_post_only_bt_devices_migration_produces_players(client, tmp_path):
    """POST with only BLUETOOTH_DEVICES triggers migration to produce players."""
    payload = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "LegacySpeaker"},
        ],
    }
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200
    saved = _read_config(tmp_path)
    assert "players" in saved
    assert len(saved["players"]) >= 1
    assert saved["players"][0]["player_name"] == "LegacySpeaker"
    assert saved["players"][0]["backend"]["type"] == "bluetooth_a2dp"


def test_config_post_bt_devices_still_preserved_with_players(client, tmp_path):
    """POST with players still keeps BLUETOOTH_DEVICES for backward compat."""
    payload = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"},
        ],
        "players": [
            {"id": "p1", "player_name": "Speaker1", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}},
        ],
    }
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200
    saved = _read_config(tmp_path)
    assert "BLUETOOTH_DEVICES" in saved
    assert "players" in saved


# ---------------------------------------------------------------------------
# validate_uploaded_config — v2 player entries
# ---------------------------------------------------------------------------


def test_validate_accepts_valid_bluetooth_player():
    """Valid bluetooth_a2dp player passes validation."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"}],
        "players": [
            {
                "id": "p1",
                "player_name": "Speaker1",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                "enabled": True,
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert not player_errors, f"Unexpected player errors: {player_errors}"


def test_validate_accepts_local_sink_player():
    """Valid local_sink player passes validation."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "LocalOut",
                "backend": {"type": "local_sink", "sink_name": "default"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert not player_errors, f"Unexpected player errors: {player_errors}"


def test_validate_accepts_snapcast_player():
    """Valid snapcast player passes validation."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "SnapOut",
                "backend": {"type": "snapcast"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert not player_errors, f"Unexpected player errors: {player_errors}"


def test_validate_rejects_player_missing_player_name():
    """Player without player_name is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("player_name" in e.message for e in player_errors)


def test_validate_rejects_player_empty_player_name():
    """Player with empty string player_name is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("player_name" in e.message for e in player_errors)


def test_validate_rejects_player_invalid_backend_type():
    """Player with unknown backend type is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "Bad",
                "backend": {"type": "unknown_type"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("backend.type" in e.message or "type" in e.message for e in player_errors)


def test_validate_rejects_player_missing_backend():
    """Player without backend dict is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "NoBE",
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("backend" in e.message for e in player_errors)


def test_validate_rejects_bluetooth_player_missing_mac():
    """bluetooth_a2dp player without backend.mac is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "NoMac",
                "backend": {"type": "bluetooth_a2dp"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("mac" in e.message.lower() for e in player_errors)


def test_validate_rejects_bluetooth_player_invalid_mac():
    """bluetooth_a2dp player with invalid MAC format is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "BadMac",
                "backend": {"type": "bluetooth_a2dp", "mac": "INVALID"},
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("mac" in e.message.lower() for e in player_errors)


def test_validate_rejects_non_dict_player():
    """Non-dict entry in players[] is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": ["not-a-dict"],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1


def test_validate_rejects_non_list_players():
    """players that is not a list is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": "not-a-list",
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1


def test_validate_player_enabled_must_be_bool():
    """Player with non-bool enabled field is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "Test",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                "enabled": "yes",
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("enabled" in e.message for e in player_errors)


def test_validate_player_listen_port_range():
    """Player with out-of-range listen_port is rejected."""
    from services.config_validation import validate_uploaded_config

    cfg = {
        "players": [
            {
                "id": "p1",
                "player_name": "Test",
                "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                "listen_port": 99999,
            }
        ],
    }
    result = validate_uploaded_config(cfg)
    player_errors = [e for e in result.errors if "players" in e.field]
    assert len(player_errors) >= 1
    assert any("listen_port" in e.message for e in player_errors)


# ---------------------------------------------------------------------------
# persist_device_enabled — players[] sync
# ---------------------------------------------------------------------------


def test_persist_device_enabled_updates_players(tmp_path):
    """persist_device_enabled updates the enabled flag in players[] too."""
    from services.bluetooth import persist_device_enabled

    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1", "enabled": True},
            ],
            "players": [
                {
                    "id": "p1",
                    "player_name": "Speaker1",
                    "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                    "enabled": True,
                }
            ],
        },
    )
    persist_device_enabled("Speaker1", False)
    saved = _read_config(tmp_path)
    assert saved["BLUETOOTH_DEVICES"][0]["enabled"] is False
    assert saved["players"][0]["enabled"] is False


# ---------------------------------------------------------------------------
# persist_device_released — players[] sync
# ---------------------------------------------------------------------------


def test_persist_device_released_updates_players(tmp_path):
    """persist_device_released updates the released flag in players[] too."""
    from services.bluetooth import persist_device_released

    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"},
            ],
            "players": [
                {
                    "id": "p1",
                    "player_name": "Speaker1",
                    "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                }
            ],
        },
    )
    persist_device_released("Speaker1", True)
    saved = _read_config(tmp_path)
    assert saved["BLUETOOTH_DEVICES"][0]["released"] is True
    assert saved["players"][0]["released"] is True
