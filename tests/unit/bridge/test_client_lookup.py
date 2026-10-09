"""Device lookup by id, and MAC validation."""

from __future__ import annotations

import json
import types

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config so module-level imports succeed."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


def _make_client(player_id: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(player_id=player_id, player_name=player_id.title())


def _register(monkeypatch, clients) -> None:
    import sendspin_bridge.application.status as status
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=clients))


# ---------------------------------------------------------------------------
# find_client
# ---------------------------------------------------------------------------


class TestFindClient:
    """Devices are addressed by their stable player id, never by name or index."""

    def test_returns_the_matching_client(self, monkeypatch):
        from sendspin_bridge.application.status import find_client

        target = _make_client("kitchen")
        _register(monkeypatch, [_make_client("bedroom"), target])

        assert find_client("kitchen") is target

    def test_unknown_id_is_404(self, monkeypatch):
        from sendspin_bridge.application.errors import UseCaseError
        from sendspin_bridge.application.status import find_client

        _register(monkeypatch, [_make_client("bedroom")])

        with pytest.raises(UseCaseError) as exc:
            find_client("kitchen")
        assert exc.value.status == 404
        assert exc.value.code == "unknown_device"

    def test_a_player_name_is_not_an_id(self, monkeypatch):
        from sendspin_bridge.application.errors import UseCaseError
        from sendspin_bridge.application.status import find_client

        _register(monkeypatch, [_make_client("kitchen")])

        with pytest.raises(UseCaseError):
            find_client("Kitchen")

    def test_no_clients_is_404(self, monkeypatch):
        from sendspin_bridge.application.errors import UseCaseError
        from sendspin_bridge.application.status import find_client

        _register(monkeypatch, [])

        with pytest.raises(UseCaseError):
            find_client("any")


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
        from sendspin_bridge.application.errors import validate_mac

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
        from sendspin_bridge.application.errors import validate_mac

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
        from sendspin_bridge.application.errors import validate_mac

        assert validate_mac(mac) is False
