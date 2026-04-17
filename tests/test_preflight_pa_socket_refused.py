"""Preflight audio probe: distinguish socket-refused from socket-missing.

Issue #151 — headless PipeWire hosts bind-mount the socket path into the
container, but the user-session daemon has been stopped by logind (no
``loginctl enable-linger``). The pre-existing probe collapsed both "no
socket" and "socket but refused" into ``system=unknown``; the new behaviour
is driven by an explicit ``connect_fn(sock_path)`` probe so the check does
not depend on ``services.pulse.get_server_name`` raising (the real
implementation swallows connect errors and returns "not available").
"""

from __future__ import annotations

from services.preflight_status import collect_preflight_status


def _runtime_version_stub() -> str:
    return "test"


def _subprocess_stub():
    return type("S", (), {"run": lambda *a, **kw: type("R", (), {"stdout": ""})()})()


def _open_stub(*_a, **_kw):
    return __import__("io").StringIO("")


def test_socket_exists_connection_refused_sets_system_unreachable(monkeypatch):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _connect_refused(_sock_path):
        raise ConnectionRefusedError("Connection refused")

    result = collect_preflight_status(
        get_server_name_fn=lambda: "should-not-be-called",
        list_sinks_fn=lambda: ["should-not-be-called"],
        subprocess_module=_subprocess_stub(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: path == "/run/user/1000/pulse/native",
        open_fn=_open_stub,
        connect_fn=_connect_refused,
    )

    audio = result["audio"]
    assert audio["system"] == "unreachable"
    assert audio["socket"] == "unix:/run/user/1000/pulse/native"
    assert audio["socket_exists"] is True
    assert audio["socket_reachable"] is False
    assert audio["last_error"] and "refused" in audio["last_error"].lower()
    assert "audio" in result["failed_collections"]


def test_socket_exists_permission_denied_does_not_mark_linger(monkeypatch):
    """PermissionError from the probe must NOT map to the linger-specific path.

    The onboarding layer branches on ``last_error`` text containing "refused";
    other socket errors (permission, ENOPROTOOPT, …) are still audio failures
    but should keep ``system`` generic so operator_guidance does not emit the
    ``pa_socket_refused`` issue.
    """
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _connect_denied(_sock_path):
        raise PermissionError("Permission denied")

    result = collect_preflight_status(
        get_server_name_fn=lambda: "should-not-be-called",
        list_sinks_fn=lambda: [],
        subprocess_module=_subprocess_stub(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: True,
        open_fn=_open_stub,
        connect_fn=_connect_denied,
    )

    audio = result["audio"]
    assert audio["socket_exists"] is True
    assert audio["socket_reachable"] is False
    assert "refused" not in (audio["last_error"] or "").lower()
    # Crucially not "unreachable" — that signal is reserved for refused sockets
    # so downstream onboarding/guidance does not offer linger instructions.
    assert audio["system"] != "unreachable"
    assert "audio" in result["failed_collections"]


def test_no_socket_and_no_server_sets_system_unknown(monkeypatch):
    monkeypatch.delenv("PULSE_SERVER", raising=False)

    probe_calls: list[str] = []

    def _probe(sock_path):
        probe_calls.append(sock_path)

    result = collect_preflight_status(
        get_server_name_fn=lambda: "not available",
        list_sinks_fn=lambda: [],
        subprocess_module=_subprocess_stub(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: False,
        open_fn=_open_stub,
        connect_fn=_probe,
    )

    audio = result["audio"]
    assert audio["system"] == "unknown"
    assert audio["socket_exists"] is False
    # Probe must not be invoked without a socket path.
    assert probe_calls == []


def test_server_responds_sets_system_pipewire(monkeypatch):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _probe_ok(_sock_path):
        return None

    result = collect_preflight_status(
        get_server_name_fn=lambda: "PulseAudio (on PipeWire 1.4.2)",
        list_sinks_fn=lambda: [object(), object()],
        subprocess_module=_subprocess_stub(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: True,
        open_fn=_open_stub,
        connect_fn=_probe_ok,
    )

    audio = result["audio"]
    assert audio["system"] == "pipewire"
    assert audio["socket_reachable"] is True
    assert audio["sinks"] == 2
