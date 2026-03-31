"""Tests for services.player_model — Player dataclass and PlayerState enum."""

from __future__ import annotations

import uuid

import pytest

from services.audio_backend import BackendType
from services.player_model import Player, PlayerState, _player_id_from_mac

# ---------------------------------------------------------------------------
# PlayerState enum
# ---------------------------------------------------------------------------


class TestPlayerState:
    """PlayerState enum has all required lifecycle members."""

    @pytest.mark.parametrize(
        "member",
        [
            "INITIALIZING",
            "CONNECTING",
            "READY",
            "STREAMING",
            "STANDBY",
            "ERROR",
            "DISABLED",
            "OFFLINE",
        ],
    )
    def test_has_member(self, member: str) -> None:
        assert hasattr(PlayerState, member)

    def test_values_are_lowercase(self) -> None:
        for member in PlayerState:
            assert member.value == member.name.lower()

    def test_is_str_enum(self) -> None:
        assert isinstance(PlayerState.READY, str)
        assert PlayerState.READY == "ready"


# ---------------------------------------------------------------------------
# Player dataclass — fields & defaults
# ---------------------------------------------------------------------------


class TestPlayerFields:
    """Player dataclass carries all expected fields with correct defaults."""

    def test_required_fields(self) -> None:
        p = Player(id="test-id", player_name="Speaker")
        assert p.id == "test-id"
        assert p.player_name == "Speaker"

    def test_default_values(self) -> None:
        p = Player(id="x", player_name="X")
        assert p.backend_type == BackendType.BLUETOOTH_A2DP
        assert p.backend_config == {}
        assert p.enabled is True
        assert p.listen_port == 0
        assert p.static_delay_ms is None
        assert p.handoff_mode == "default"
        assert p.room_id is None
        assert p.room_name is None
        assert p.volume_controller == "pa"
        assert p.keepalive_enabled is False
        assert p.keepalive_interval == 30
        assert p.idle_disconnect_minutes == 0


# ---------------------------------------------------------------------------
# Player.mac / Player.adapter property shortcuts
# ---------------------------------------------------------------------------


class TestPlayerProperties:
    """Shortcut properties delegate to backend_config for BT backends."""

    def test_mac_returns_value_for_bt(self) -> None:
        p = Player(
            id="x",
            player_name="X",
            backend_type=BackendType.BLUETOOTH_A2DP,
            backend_config={"mac": "AA:BB:CC:DD:EE:FF"},
        )
        assert p.mac == "AA:BB:CC:DD:EE:FF"

    def test_mac_returns_none_when_missing(self) -> None:
        p = Player(
            id="x",
            player_name="X",
            backend_type=BackendType.BLUETOOTH_A2DP,
            backend_config={},
        )
        assert p.mac is None

    def test_adapter_returns_value_for_bt(self) -> None:
        p = Player(
            id="x",
            player_name="X",
            backend_type=BackendType.BLUETOOTH_A2DP,
            backend_config={"adapter": "hci0"},
        )
        assert p.adapter == "hci0"

    def test_adapter_returns_empty_when_missing(self) -> None:
        p = Player(
            id="x",
            player_name="X",
            backend_type=BackendType.BLUETOOTH_A2DP,
            backend_config={},
        )
        assert p.adapter == ""


# ---------------------------------------------------------------------------
# Player.to_dict() round-trip
# ---------------------------------------------------------------------------


class TestToDict:
    """to_dict() produces a serializable dict that round-trips."""

    def test_backend_type_serialized_as_string(self) -> None:
        p = Player(id="x", player_name="X")
        d = p.to_dict()
        assert d["backend_type"] == "bluetooth_a2dp"

    def test_round_trip_fields(self) -> None:
        p = Player(
            id="abc",
            player_name="ENEBY20",
            backend_config={"mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0"},
            listen_port=8928,
            static_delay_ms=-600.0,
            room_name="Kitchen",
        )
        d = p.to_dict()
        assert d["id"] == "abc"
        assert d["player_name"] == "ENEBY20"
        assert d["listen_port"] == 8928
        assert d["static_delay_ms"] == -600.0
        assert d["room_name"] == "Kitchen"
        assert d["backend_config"]["mac"] == "AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------------------
# Player.from_config() — v1 BT device config
# ---------------------------------------------------------------------------


class TestFromConfigV1:
    """from_config() handles v1 BLUETOOTH_DEVICES entries."""

    V1_FULL = {
        "mac": "AA:BB:CC:DD:EE:FF",
        "player_name": "ENEBY20",
        "adapter": "hci0",
        "listen_port": 8928,
        "delay_ms": -600,
        "enabled": True,
        "volume_controller": "pa",
        "handoff_mode": "relay",
        "keepalive_enabled": True,
        "keepalive_interval": 60,
        "idle_disconnect_minutes": 15,
        "room_id": "kitchen",
        "room_name": "Kitchen",
    }

    def test_basic_v1(self) -> None:
        cfg = {
            "mac": "AA:BB:CC:DD:EE:FF",
            "player_name": "ENEBY20",
            "adapter": "hci0",
            "listen_port": 8928,
            "enabled": True,
        }
        p = Player.from_config(cfg)
        assert p.player_name == "ENEBY20"
        assert p.backend_type == BackendType.BLUETOOTH_A2DP
        assert p.backend_config["mac"] == "AA:BB:CC:DD:EE:FF"
        assert p.backend_config["adapter"] == "hci0"
        assert p.listen_port == 8928
        assert p.enabled is True

    def test_v1_id_generated_from_mac(self) -> None:
        cfg = {"mac": "aa:bb:cc:dd:ee:ff", "player_name": "Test"}
        p = Player.from_config(cfg)
        expected = _player_id_from_mac("aa:bb:cc:dd:ee:ff")
        assert p.id == expected

    def test_v1_explicit_id_preserved(self) -> None:
        cfg = {"id": "custom-id", "mac": "AA:BB:CC:DD:EE:FF", "player_name": "Test"}
        p = Player.from_config(cfg)
        assert p.id == "custom-id"

    def test_v1_mac_normalized_uppercase(self) -> None:
        cfg = {"mac": "  aa:bb:cc:dd:ee:ff  ", "player_name": "X"}
        p = Player.from_config(cfg)
        assert p.backend_config["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_v1_full_fields(self) -> None:
        p = Player.from_config(self.V1_FULL)
        assert p.static_delay_ms == -600
        assert p.handoff_mode == "relay"
        assert p.keepalive_enabled is True
        assert p.keepalive_interval == 60
        assert p.idle_disconnect_minutes == 15
        assert p.room_id == "kitchen"
        assert p.room_name == "Kitchen"
        assert p.volume_controller == "pa"

    def test_v1_defaults(self) -> None:
        cfg = {"mac": "AA:BB:CC:DD:EE:FF"}
        p = Player.from_config(cfg)
        assert p.enabled is True
        assert p.listen_port == 0
        assert p.handoff_mode == "default"
        assert p.keepalive_enabled is False
        assert p.keepalive_interval == 30
        assert p.idle_disconnect_minutes == 0
        assert p.volume_controller == "pa"

    def test_v1_disabled(self) -> None:
        cfg = {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "X", "enabled": False}
        p = Player.from_config(cfg)
        assert p.enabled is False


# ---------------------------------------------------------------------------
# Player.from_config() — v2 player config
# ---------------------------------------------------------------------------


class TestFromConfigV2:
    """from_config() handles v2 players[] entries with backend block."""

    def test_basic_v2(self) -> None:
        cfg = {
            "id": "some-id",
            "player_name": "ENEBY20",
            "backend": {
                "type": "bluetooth_a2dp",
                "mac": "AA:BB:CC:DD:EE:FF",
                "adapter": "hci0",
            },
            "listen_port": 8928,
        }
        p = Player.from_config(cfg)
        assert p.id == "some-id"
        assert p.player_name == "ENEBY20"
        assert p.backend_type == BackendType.BLUETOOTH_A2DP
        assert p.backend_config["mac"] == "AA:BB:CC:DD:EE:FF"
        assert p.backend_config["adapter"] == "hci0"
        assert "type" not in p.backend_config
        assert p.listen_port == 8928

    def test_v2_id_generated_from_mac_when_missing(self) -> None:
        cfg = {
            "player_name": "Test",
            "backend": {"type": "bluetooth_a2dp", "mac": "aa:bb:cc:dd:ee:ff"},
        }
        p = Player.from_config(cfg)
        expected = _player_id_from_mac("aa:bb:cc:dd:ee:ff")
        assert p.id == expected

    def test_v2_mac_normalized_uppercase(self) -> None:
        cfg = {
            "player_name": "X",
            "backend": {"type": "bluetooth_a2dp", "mac": " aa:bb:cc:dd:ee:ff "},
        }
        p = Player.from_config(cfg)
        assert p.backend_config["mac"] == "AA:BB:CC:DD:EE:FF"

    def test_v2_static_delay_ms_field(self) -> None:
        cfg = {
            "player_name": "X",
            "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
            "static_delay_ms": -300,
        }
        p = Player.from_config(cfg)
        assert p.static_delay_ms == -300

    def test_v2_defaults(self) -> None:
        cfg = {
            "player_name": "X",
            "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
        }
        p = Player.from_config(cfg)
        assert p.enabled is True
        assert p.listen_port == 0
        assert p.handoff_mode == "default"


# ---------------------------------------------------------------------------
# Player.from_config() — id generation fallback
# ---------------------------------------------------------------------------


class TestIdGeneration:
    """ID generation falls back through mac → player_name."""

    def test_id_from_mac_is_uuid5(self) -> None:
        mac = "AA:BB:CC:DD:EE:FF"
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "AA-BB-CC-DD-EE-FF"))
        assert _player_id_from_mac(mac) == expected

    def test_v1_no_mac_falls_back_to_name(self) -> None:
        cfg = {"player_name": "AirPlay Speaker"}
        p = Player.from_config(cfg)
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "airplay speaker"))
        assert p.id == expected

    def test_v2_no_mac_falls_back_to_name(self) -> None:
        cfg = {
            "player_name": "AirPlay Speaker",
            "backend": {"type": "bluetooth_a2dp"},
        }
        p = Player.from_config(cfg)
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "airplay speaker"))
        assert p.id == expected
