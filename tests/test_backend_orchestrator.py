"""Tests for BackendOrchestrator — manages AudioBackend lifecycle per Player."""

import threading

import pytest

from services.audio_backend import BackendType
from services.backend_orchestrator import BackendOrchestrator
from services.event_store import EventStore
from services.internal_events import InternalEventPublisher
from services.player_model import Player, PlayerState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_store():
    return EventStore()


@pytest.fixture
def publisher():
    return InternalEventPublisher()


@pytest.fixture
def orchestrator(event_store, publisher):
    return BackendOrchestrator(event_store=event_store, event_publisher=publisher)


def _make_player(
    player_id: str = "test-player-1",
    name: str = "Test Speaker",
    enabled: bool = True,
) -> Player:
    return Player(
        id=player_id,
        player_name=name,
        backend_type=BackendType.BLUETOOTH_A2DP,
        backend_config={"mac": "AA:BB:CC:DD:EE:FF"},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterPlayer:
    def test_register_player_mock(self, orchestrator):
        player = _make_player(enabled=True)
        orchestrator.register_player(player, backend_type_override="mock")

        assert orchestrator.get_player_state(player.id) == PlayerState.INITIALIZING
        assert orchestrator.get_backend(player.id) is not None
        assert orchestrator.get_player(player.id) is player

    def test_register_player_disabled(self, orchestrator):
        player = _make_player(enabled=False)
        orchestrator.register_player(player, backend_type_override="mock")

        assert orchestrator.get_player_state(player.id) == PlayerState.DISABLED

    def test_register_duplicate_raises(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        with pytest.raises(ValueError, match="already registered"):
            orchestrator.register_player(player, backend_type_override="mock")


# ---------------------------------------------------------------------------
# Unregistration
# ---------------------------------------------------------------------------


class TestUnregisterPlayer:
    def test_unregister_player(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.unregister_player(player.id)

        assert orchestrator.get_player(player.id) is None
        assert orchestrator.get_backend(player.id) is None
        assert orchestrator.get_player_state(player.id) is None
        assert orchestrator.player_count == 0

    def test_unregister_connected_disconnects_first(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player(player.id)
        assert orchestrator.get_player_state(player.id) == PlayerState.READY

        backend = orchestrator.get_backend(player.id)
        orchestrator.unregister_player(player.id)

        assert "disconnect" in backend.call_log

    def test_unregister_unknown_no_error(self, orchestrator):
        orchestrator.unregister_player("no-such-player")  # should not raise


# ---------------------------------------------------------------------------
# Connect / Disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    def test_connect_player_success(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        result = orchestrator.connect_player(player.id)

        assert result is True
        assert orchestrator.get_player_state(player.id) == PlayerState.READY
        backend = orchestrator.get_backend(player.id)
        assert "connect" in backend.call_log

    def test_connect_player_failure(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(
            player,
            backend_type_override="mock",
            fail_connect=True,
        )

        result = orchestrator.connect_player(player.id)

        assert result is False
        assert orchestrator.get_player_state(player.id) == PlayerState.ERROR

    def test_connect_unknown_player(self, orchestrator):
        assert orchestrator.connect_player("no-such-player") is False

    def test_disconnect_player(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player(player.id)

        result = orchestrator.disconnect_player(player.id)

        assert result is True
        assert orchestrator.get_player_state(player.id) == PlayerState.OFFLINE
        backend = orchestrator.get_backend(player.id)
        assert "disconnect" in backend.call_log

    def test_disconnect_unknown_player(self, orchestrator):
        assert orchestrator.disconnect_player("no-such-player") is False


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


class TestStateQueries:
    def test_get_player_state(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        assert orchestrator.get_player_state(player.id) == PlayerState.INITIALIZING

    def test_get_player_state_unknown(self, orchestrator):
        assert orchestrator.get_player_state("no-such-player") is None

    def test_set_player_state(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        orchestrator.set_player_state(player.id, PlayerState.STREAMING)

        assert orchestrator.get_player_state(player.id) == PlayerState.STREAMING

    def test_set_player_state_emits_event(self, orchestrator, event_store):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        orchestrator.set_player_state(player.id, PlayerState.STREAMING)

        events = event_store.query(player_id=player.id, event_types=["player.state_changed"])
        assert len(events) >= 1
        last = events[-1]
        assert last.payload["new_state"] == PlayerState.STREAMING.value


# ---------------------------------------------------------------------------
# Backend & Player accessors
# ---------------------------------------------------------------------------


class TestAccessors:
    def test_get_backend(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        backend = orchestrator.get_backend(player.id)
        assert backend is not None
        assert backend.backend_id is not None

    def test_get_backend_unknown(self, orchestrator):
        assert orchestrator.get_backend("no-such-player") is None

    def test_get_all_players(self, orchestrator):
        p1 = _make_player("p1", "Speaker 1")
        p2 = _make_player("p2", "Speaker 2")
        orchestrator.register_player(p1, backend_type_override="mock")
        orchestrator.register_player(p2, backend_type_override="mock")

        all_players = orchestrator.get_all_players()
        assert len(all_players) == 2
        assert "p1" in all_players and "p2" in all_players
        # Must be a copy — mutation should not affect internals
        all_players.pop("p1")
        assert orchestrator.get_player("p1") is not None

    def test_get_all_states(self, orchestrator):
        p1 = _make_player("p1", "Speaker 1")
        p2 = _make_player("p2", "Speaker 2", enabled=False)
        orchestrator.register_player(p1, backend_type_override="mock")
        orchestrator.register_player(p2, backend_type_override="mock")

        states = orchestrator.get_all_states()
        assert states["p1"] == PlayerState.INITIALIZING
        assert states["p2"] == PlayerState.DISABLED
        # Must be a copy
        states["p1"] = PlayerState.ERROR
        assert orchestrator.get_player_state("p1") == PlayerState.INITIALIZING


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------


class TestStatusSummary:
    def test_get_status_summary(self, orchestrator):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")

        summary = orchestrator.get_status_summary()
        assert len(summary) == 1
        entry = summary[0]
        assert entry["player_id"] == player.id
        assert entry["player_name"] == player.player_name
        assert entry["state"] == PlayerState.INITIALIZING.value
        assert "backend" in entry


# ---------------------------------------------------------------------------
# Count properties
# ---------------------------------------------------------------------------


class TestCounts:
    def test_player_count(self, orchestrator):
        assert orchestrator.player_count == 0
        orchestrator.register_player(_make_player("p1"), backend_type_override="mock")
        assert orchestrator.player_count == 1
        orchestrator.register_player(_make_player("p2"), backend_type_override="mock")
        assert orchestrator.player_count == 2

    def test_connected_count(self, orchestrator):
        p1 = _make_player("p1")
        p2 = _make_player("p2")
        orchestrator.register_player(p1, backend_type_override="mock")
        orchestrator.register_player(p2, backend_type_override="mock")

        assert orchestrator.connected_count == 0
        orchestrator.connect_player("p1")
        assert orchestrator.connected_count == 1
        orchestrator.set_player_state("p1", PlayerState.STREAMING)
        assert orchestrator.connected_count == 1
        orchestrator.connect_player("p2")
        assert orchestrator.connected_count == 2
        orchestrator.disconnect_player("p1")
        assert orchestrator.connected_count == 1


# ---------------------------------------------------------------------------
# Event integration
# ---------------------------------------------------------------------------


class TestEventIntegration:
    def test_events_recorded_in_store(self, orchestrator, event_store):
        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player(player.id)

        events = event_store.query(player_id=player.id)
        types = [e.event_type for e in events]
        assert "player.registered" in types
        assert "player.state_changed" in types

    def test_events_published(self, orchestrator, publisher):
        received = []
        publisher.subscribe(lambda evt: received.append(evt))

        player = _make_player()
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player(player.id)

        types = [e.event_type for e in received]
        assert "player.registered" in types
        assert "player.state_changed" in types

    def test_no_event_store_still_works(self):
        """Orchestrator works fine without event_store or publisher."""
        orch = BackendOrchestrator()
        player = _make_player()
        orch.register_player(player, backend_type_override="mock")
        assert orch.connect_player(player.id) is True
        assert orch.get_player_state(player.id) == PlayerState.READY


# ---------------------------------------------------------------------------
# Thread safety smoke test
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_register_connect(self, orchestrator):
        """Register and connect many players concurrently — no crashes."""
        errors = []

        def worker(pid):
            try:
                p = _make_player(pid, f"Speaker {pid}")
                orchestrator.register_player(p, backend_type_override="mock")
                orchestrator.connect_player(pid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"p{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert orchestrator.player_count == 20
        assert orchestrator.connected_count == 20
