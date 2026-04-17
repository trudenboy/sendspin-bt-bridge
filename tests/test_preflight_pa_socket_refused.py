"""Preflight audio probe: distinguish socket-refused from socket-missing.

Added to support issue #151 — headless PipeWire hosts bind-mount the socket
path into the container, but the user-session daemon has been stopped by
logind (no ``loginctl enable-linger``). The pre-existing probe collapsed
both "no socket" and "socket but refused" into ``system=unknown``; the new
behaviour surfaces ``system=unreachable`` whenever the socket file exists
yet ``get_server_name()`` raises.
"""

from __future__ import annotations

from services.preflight_status import collect_preflight_status


def _runtime_version_stub() -> str:
    return "test"


def test_socket_exists_connection_refused_sets_system_unreachable(monkeypatch):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _raise(*_a, **_kw):
        raise ConnectionRefusedError("Connection refused")

    result = collect_preflight_status(
        get_server_name_fn=_raise,
        list_sinks_fn=lambda: [],
        subprocess_module=type("S", (), {"run": lambda *a, **kw: type("R", (), {"stdout": ""})()})(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: path == "/run/user/1000/pulse/native",
        open_fn=lambda *_a, **_kw: __import__("io").StringIO(""),
    )

    audio = result["audio"]
    assert audio["system"] == "unreachable"
    assert audio["socket"] == "unix:/run/user/1000/pulse/native"
    assert audio["socket_exists"] is True
    assert audio["socket_reachable"] is False
    assert audio["last_error"] and "refused" in audio["last_error"].lower()
    assert "audio" in result["failed_collections"]


def test_no_socket_and_no_server_sets_system_unknown(monkeypatch):
    monkeypatch.delenv("PULSE_SERVER", raising=False)

    def _raise(*_a, **_kw):
        raise RuntimeError("no server")

    result = collect_preflight_status(
        get_server_name_fn=_raise,
        list_sinks_fn=lambda: [],
        subprocess_module=type("S", (), {"run": lambda *a, **kw: type("R", (), {"stdout": ""})()})(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: False,
        open_fn=lambda *_a, **_kw: __import__("io").StringIO(""),
    )

    audio = result["audio"]
    assert audio["system"] == "unknown"
    assert audio["socket_exists"] is False
    assert audio["socket_reachable"] is False or audio["socket_reachable"] is None


def test_server_responds_sets_system_pipewire(monkeypatch):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    result = collect_preflight_status(
        get_server_name_fn=lambda: "PulseAudio (on PipeWire 1.4.2)",
        list_sinks_fn=lambda: [object(), object()],
        subprocess_module=type("S", (), {"run": lambda *a, **kw: type("R", (), {"stdout": ""})()})(),
        runtime_version_fn=_runtime_version_stub,
        machine_fn=lambda: "x86_64",
        exists_fn=lambda path: True,
        open_fn=lambda *_a, **_kw: __import__("io").StringIO(""),
    )

    audio = result["audio"]
    assert audio["system"] == "pipewire"
    assert audio["socket_reachable"] is True
    assert audio["sinks"] == 2
