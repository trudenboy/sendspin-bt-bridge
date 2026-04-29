"""Tests for routes/_helpers.py — get_client_or_error() and validate_mac()."""

from __future__ import annotations

import json
import types

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config so module-level imports succeed."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


def _make_client(name: str) -> types.SimpleNamespace:
    """Return a lightweight mock client with a player_name attribute."""
    return types.SimpleNamespace(player_name=name)


@pytest.fixture()
def app():
    """Minimal Flask app for request context (needed by jsonify)."""
    a = Flask(__name__)
    a.config["TESTING"] = True
    return a


# ---------------------------------------------------------------------------
# get_client_or_error
# ---------------------------------------------------------------------------


class TestGetClientOrError:
    """Tests for get_client_or_error()."""

    def test_valid_player_name(self, app, monkeypatch):
        """Returns the matching client when player_name matches."""
        import routes._helpers as helpers
        from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

        target = _make_client("kitchen")
        monkeypatch.setattr(
            helpers,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(active_clients=[_make_client("bedroom"), target]),
        )

        with app.app_context():
            client, err = helpers.get_client_or_error("kitchen")

        assert err is None
        assert client is target

    def test_invalid_player_name(self, app, monkeypatch):
        """Returns 400 with 'Unknown player' for a non-existent name."""
        import routes._helpers as helpers
        from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

        monkeypatch.setattr(
            helpers,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(active_clients=[_make_client("bedroom")]),
        )

        with app.app_context():
            client, err = helpers.get_client_or_error("nonexistent")

        assert client is None
        resp, status = err
        assert status == 400
        data = resp.get_json()
        assert "Unknown player" in data["error"]

    def test_no_name_single_client(self, app, monkeypatch):
        """With no player_name and one client, returns that client."""
        import routes._helpers as helpers
        from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

        only = _make_client("solo")
        monkeypatch.setattr(
            helpers,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(active_clients=[only]),
        )

        with app.app_context():
            client, err = helpers.get_client_or_error(None)

        assert err is None
        assert client is only

    def test_no_name_multiple_clients(self, app, monkeypatch):
        """With no player_name and multiple clients, returns 400."""
        import routes._helpers as helpers
        from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

        monkeypatch.setattr(
            helpers,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(active_clients=[_make_client("a"), _make_client("b")]),
        )

        with app.app_context():
            client, err = helpers.get_client_or_error(None)

        assert client is None
        resp, status = err
        assert status == 400
        assert "player_name" in resp.get_json()["error"].lower()

    def test_no_clients(self, app, monkeypatch):
        """With no clients configured, returns 503."""
        import routes._helpers as helpers
        from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

        monkeypatch.setattr(
            helpers,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(active_clients=[]),
        )

        with app.app_context():
            client, err = helpers.get_client_or_error("any")

        assert client is None
        resp, status = err
        assert status == 503
        assert "No clients" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# validate_mac
# ---------------------------------------------------------------------------


class TestValidateMac:
    """Tests for validate_mac()."""

    @pytest.mark.parametrize(
        "mac",
        [
            "AA:BB:CC:DD:EE:FF",
            "aa:bb:cc:dd:ee:ff",
            "00:11:22:33:44:55",
        ],
    )
    def test_valid_formats(self, mac):
        from routes._helpers import validate_mac

        assert validate_mac(mac) is True

    @pytest.mark.parametrize(
        "mac",
        [
            "not-a-mac",
            "AA:BB:CC:DD:EE",  # too short
            "AA:BB:CC:DD:EE:GG",  # invalid hex digit
            "",  # empty
            "AA:BB:CC:DD:EE:FF:00",  # extra octet
            "AA-BB-CC-DD-EE-FF",  # wrong separator
        ],
    )
    def test_invalid_formats(self, mac):
        from routes._helpers import validate_mac

        assert validate_mac(mac) is False

    @pytest.mark.parametrize(
        "mac",
        [
            "AA:BB:CC:DD:EE:FF\npower on",
            "AA:BB:CC:DD:EE:FF; rm -rf /",
            "AA:BB:CC:DD:EE:FF && echo pwned",
        ],
    )
    def test_command_injection_rejected(self, mac):
        from routes._helpers import validate_mac

        assert validate_mac(mac) is False
