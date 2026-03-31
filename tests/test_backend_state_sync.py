"""Tests for BackendOrchestrator state sync from SendspinClient."""

from unittest.mock import patch

import pytest

from services.audio_backend import BackendType
from services.backends.mock_backend import MockAudioBackend
from services.player_model import Player, PlayerState


@pytest.fixture
def _patch_state(monkeypatch):
    """Prevent state module side effects."""
    import state

    monkeypatch.setattr(state, "notify_status_changed", lambda: None)


@pytest.fixture
def client(_patch_state):
    from sendspin_client import SendspinClient

    c = SendspinClient("TestSpeaker", "localhost", 9000, listen_port=8928)
    return c


@pytest.fixture
def client_with_player(client):
    player = Player(
        id=client.player_id,
        player_name="TestSpeaker",
        backend_type=BackendType.BLUETOOTH_A2DP,
        backend_config={"mac": "AA:BB:CC:DD:EE:FF"},
    )
    client._player = player

    # Register in orchestrator so set_player_state works
    from state import get_backend_orchestrator

    orch = get_backend_orchestrator()
    backend = MockAudioBackend(backend_id="test")
    try:
        orch.register_player_with_backend(player, backend)
    except ValueError:
        pass  # Already registered from previous test

    return client


# -- _derive_player_state tests --


class TestDerivePlayerState:
    def test_streaming(self, client_with_player):
        """audio_streaming=True → STREAMING."""
        client_with_player.status.update(
            {"audio_streaming": True, "server_connected": True, "bluetooth_connected": True}
        )
        assert client_with_player._derive_player_state() == PlayerState.STREAMING

    def test_ready(self, client_with_player):
        """server_connected=True (not streaming) → READY."""
        client_with_player.status.update(
            {"audio_streaming": False, "server_connected": True, "bluetooth_connected": True}
        )
        assert client_with_player._derive_player_state() == PlayerState.READY

    def test_connecting(self, client_with_player):
        """bluetooth_connected=True only → CONNECTING."""
        client_with_player.status.update(
            {"audio_streaming": False, "server_connected": False, "bluetooth_connected": True}
        )
        assert client_with_player._derive_player_state() == PlayerState.CONNECTING

    def test_error(self, client_with_player):
        """last_error set → ERROR."""
        client_with_player.status.update(
            {
                "audio_streaming": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "last_error": "something broke",
            }
        )
        assert client_with_player._derive_player_state() == PlayerState.ERROR

    def test_offline(self, client_with_player):
        """Nothing set → OFFLINE."""
        client_with_player.status.update(
            {
                "audio_streaming": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "last_error": "",
            }
        )
        assert client_with_player._derive_player_state() == PlayerState.OFFLINE

    def test_no_player(self, client):
        """No _player → None."""
        assert client._player is None
        assert client._derive_player_state() is None


# -- _update_status orchestrator sync tests --


class TestUpdateStatusOrchestratorSync:
    def test_syncs_to_orchestrator(self, client_with_player):
        """_update_status with audio_streaming=True pushes STREAMING to orchestrator."""
        from state import get_backend_orchestrator

        orch = get_backend_orchestrator()

        client_with_player._update_status({"audio_streaming": True, "server_connected": True})

        assert orch.get_player_state(client_with_player.player_id) == PlayerState.STREAMING

    def test_no_sync_without_player(self, client, _patch_state):
        """Without _player, orchestrator is not called."""
        from state import get_backend_orchestrator

        orch = get_backend_orchestrator()

        with patch.object(orch, "set_player_state") as mock_set:
            client._update_status({"audio_streaming": True})
            mock_set.assert_not_called()

    def test_sync_failure_does_not_break(self, client_with_player):
        """Orchestrator error doesn't break status update."""
        from state import get_backend_orchestrator

        orch = get_backend_orchestrator()

        with patch.object(orch, "set_player_state", side_effect=RuntimeError("boom")):
            # Should not raise
            client_with_player._update_status({"audio_streaming": True})

        # Status itself should still be updated
        assert client_with_player.status.get("audio_streaming") is True

    def test_state_transition_only_on_change(self, client_with_player):
        """If state doesn't change, no orchestrator set_player_state call."""
        from state import get_backend_orchestrator

        orch = get_backend_orchestrator()

        # First update: sets STREAMING
        client_with_player._update_status({"audio_streaming": True, "server_connected": True})
        assert orch.get_player_state(client_with_player.player_id) == PlayerState.STREAMING

        # Second update: still STREAMING — set_player_state should NOT be called again
        with patch.object(orch, "set_player_state") as mock_set:
            client_with_player._update_status({"volume": 80})
            mock_set.assert_not_called()
