"""BackendOrchestrator — manages AudioBackend lifecycle for all configured players.

Creates backend instances from Player configs, tracks PlayerState per player,
connects/disconnects backends on demand, and emits events on state transitions.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from services.backends import create_backend
from services.player_model import PlayerState

if TYPE_CHECKING:
    from services.audio_backend import AudioBackend
    from services.event_store import EventStore
    from services.internal_events import InternalEventPublisher
    from services.player_model import Player

logger = logging.getLogger(__name__)

_CONNECTED_STATES = frozenset({PlayerState.READY, PlayerState.STREAMING})


class BackendOrchestrator:
    """Manages AudioBackend lifecycle for all configured players."""

    def __init__(
        self,
        event_store: EventStore | None = None,
        event_publisher: InternalEventPublisher | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._players: dict[str, Player] = {}
        self._backends: dict[str, AudioBackend] = {}
        self._states: dict[str, PlayerState] = {}
        self._event_store = event_store
        self._event_publisher = event_publisher

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_player(
        self,
        player: Player,
        *,
        backend_type_override: str | None = None,
        **backend_kwargs: Any,
    ) -> None:
        """Register a player and create its backend instance.

        Args:
            player: Player config.
            backend_type_override: Override backend type (e.g. ``"mock"`` for tests).
            **backend_kwargs: Extra args forwarded to :func:`create_backend`.

        Raises:
            ValueError: If the player is already registered.
        """
        bt = backend_type_override or player.backend_type.value
        with self._lock:
            if player.id in self._players:
                raise ValueError(f"Player {player.id!r} already registered")
            backend = create_backend(bt, **backend_kwargs)
            self._players[player.id] = player
            self._backends[player.id] = backend
            initial_state = PlayerState.DISABLED if not player.enabled else PlayerState.INITIALIZING
            self._states[player.id] = initial_state

        self._emit_event(
            "player.registered",
            player.id,
            {
                "player_name": player.player_name,
                "backend_type": bt,
                "initial_state": initial_state.value,
            },
        )
        logger.info("Registered player %s (%s) → %s", player.id, player.player_name, initial_state.value)

    def register_player_with_backend(
        self,
        player: Player,
        backend: AudioBackend,
    ) -> None:
        """Register a player with a pre-created backend instance.

        Use this instead of :meth:`register_player` when the backend has
        already been constructed (e.g. during device initialization where
        the same backend is also assigned to the client).

        Raises:
            ValueError: If the player is already registered.
        """
        with self._lock:
            if player.id in self._players:
                raise ValueError(f"Player {player.id!r} already registered")
            self._players[player.id] = player
            self._backends[player.id] = backend
            initial_state = PlayerState.DISABLED if not player.enabled else PlayerState.INITIALIZING
            self._states[player.id] = initial_state

        self._emit_event(
            "player.registered",
            player.id,
            {
                "player_name": player.player_name,
                "backend_type": backend.backend_type.value,
                "initial_state": initial_state.value,
            },
        )
        logger.info(
            "Registered player %s (%s) with pre-created backend → %s",
            player.id,
            player.player_name,
            initial_state.value,
        )

    def unregister_player(self, player_id: str) -> None:
        """Unregister a player and clean up its backend."""
        with self._lock:
            if player_id not in self._players:
                return
            backend = self._backends.get(player_id)
            state = self._states.get(player_id)

        # Disconnect outside lock to avoid holding it during I/O
        if state in _CONNECTED_STATES and backend is not None:
            try:
                backend.disconnect()
            except Exception:
                logger.warning("Error disconnecting %s during unregister", player_id, exc_info=True)

        with self._lock:
            self._players.pop(player_id, None)
            self._backends.pop(player_id, None)
            self._states.pop(player_id, None)

        self._emit_event("player.unregistered", player_id)
        logger.info("Unregistered player %s", player_id)

    # ------------------------------------------------------------------
    # Connect / Disconnect
    # ------------------------------------------------------------------

    def connect_player(self, player_id: str) -> bool:
        """Connect the backend for a player. Returns True on success."""
        with self._lock:
            backend = self._backends.get(player_id)
            if backend is None:
                return False
            self._states[player_id] = PlayerState.CONNECTING

        self._emit_event(
            "player.state_changed",
            player_id,
            {
                "new_state": PlayerState.CONNECTING.value,
            },
        )

        try:
            ok = backend.connect()
        except Exception:
            logger.exception("Backend connect failed for %s", player_id)
            ok = False

        new_state = PlayerState.READY if ok else PlayerState.ERROR
        with self._lock:
            self._states[player_id] = new_state

        self._emit_event(
            "player.state_changed",
            player_id,
            {
                "new_state": new_state.value,
            },
        )
        return ok

    def disconnect_player(self, player_id: str) -> bool:
        """Disconnect the backend. Sets state to OFFLINE."""
        with self._lock:
            backend = self._backends.get(player_id)
            if backend is None:
                return False

        try:
            backend.disconnect()
        except Exception:
            logger.warning("Error disconnecting %s", player_id, exc_info=True)

        with self._lock:
            self._states[player_id] = PlayerState.OFFLINE

        self._emit_event(
            "player.state_changed",
            player_id,
            {
                "new_state": PlayerState.OFFLINE.value,
            },
        )
        return True

    # ------------------------------------------------------------------
    # State queries / mutations
    # ------------------------------------------------------------------

    def get_player_state(self, player_id: str) -> PlayerState | None:
        """Return current state for a player, or None if not registered."""
        with self._lock:
            return self._states.get(player_id)

    def set_player_state(self, player_id: str, state: PlayerState) -> None:
        """Manually set player state (e.g. STREAMING when audio starts)."""
        with self._lock:
            if player_id not in self._states:
                return
            self._states[player_id] = state

        self._emit_event(
            "player.state_changed",
            player_id,
            {
                "new_state": state.value,
            },
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_backend(self, player_id: str) -> AudioBackend | None:
        with self._lock:
            return self._backends.get(player_id)

    def get_player(self, player_id: str) -> Player | None:
        with self._lock:
            return self._players.get(player_id)

    def get_all_players(self) -> dict[str, Player]:
        with self._lock:
            return dict(self._players)

    def get_all_states(self) -> dict[str, PlayerState]:
        with self._lock:
            return dict(self._states)

    def get_status_summary(self) -> list[dict[str, Any]]:
        """Return status summary for all players (for API responses)."""
        with self._lock:
            snapshot = [
                (pid, player, self._backends.get(pid), self._states.get(pid)) for pid, player in self._players.items()
            ]

        result: list[dict[str, Any]] = []
        for pid, player, backend, state in snapshot:
            entry: dict[str, Any] = {
                "player_id": pid,
                "player_name": player.player_name,
                "state": state.value if state else None,
                "backend": backend.to_dict() if backend else None,
            }
            entry.update(player.to_dict())
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def player_count(self) -> int:
        with self._lock:
            return len(self._players)

    @property
    def connected_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._states.values() if s in _CONNECTED_STATES)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_event(
        self,
        event_type: str,
        player_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = detail or {}
        if self._event_publisher is not None:
            self._event_publisher.publish(
                event_type=event_type,
                category="orchestrator",
                subject_id=player_id,
                payload=payload,
            )
        if self._event_store is not None:
            from services.internal_events import InternalEvent

            self._event_store.record(
                InternalEvent(
                    event_type=event_type,
                    category="orchestrator",
                    subject_id=player_id,
                    payload=payload,
                )
            )
