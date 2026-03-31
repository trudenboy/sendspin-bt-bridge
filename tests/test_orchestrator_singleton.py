"""Tests for BackendOrchestrator singleton wiring in state.py."""


def test_get_backend_orchestrator_returns_instance():
    from services.backend_orchestrator import BackendOrchestrator
    from state import get_backend_orchestrator

    orch = get_backend_orchestrator()
    assert isinstance(orch, BackendOrchestrator)


def test_get_backend_orchestrator_is_singleton():
    from state import get_backend_orchestrator

    assert get_backend_orchestrator() is get_backend_orchestrator()


def test_orchestrator_has_event_store():
    """Orchestrator is wired to the global EventStore."""
    from state import get_backend_orchestrator, get_event_store

    orch = get_backend_orchestrator()
    assert orch._event_store is get_event_store()


def test_orchestrator_has_event_publisher():
    """Orchestrator is wired to the global InternalEventPublisher."""
    from state import _internal_event_publisher, get_backend_orchestrator

    orch = get_backend_orchestrator()
    assert orch._event_publisher is _internal_event_publisher
