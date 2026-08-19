"""Preflight audio probe: distinguish socket-refused from socket-missing.

Issue #151 — headless PipeWire hosts bind-mount the socket path into the
container, but the user-session daemon has been stopped by logind (no
``loginctl enable-linger``). The pre-existing probe collapsed both "no
socket" and "socket but refused" into ``system=unknown``; the new behaviour
is driven by an explicit ``connect_fn(sock_path)`` probe so the check does
not depend on ``services.pulse.get_server_name`` raising (the real
implementation swallows connect errors and returns "not available").

Batch 1 (BluezControl migration): the Bluetooth collector's seam moved
from ``subprocess_module`` to an injected ``bluez`` — these audio-focused
tests pass the shared fake so no real bluetoothctl runs.
"""

from __future__ import annotations

from sendspin_bridge.services.diagnostics.preflight_status import collect_preflight_status


def _runtime_version_stub() -> str:
    return "test"


def _open_stub(*_a, **_kw):
    return __import__("io").StringIO("")


def _base_kwargs(fake_bluez, **overrides):
    kwargs = {
        "bluez": fake_bluez.control,
        "daemon_state_fn": lambda: "unknown",
        "runtime_version_fn": _runtime_version_stub,
        "machine_fn": lambda: "x86_64",
        "open_fn": _open_stub,
    }
    kwargs.update(overrides)
    return kwargs


def test_socket_exists_connection_refused_sets_system_unreachable(monkeypatch, fake_bluez):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _connect_refused(_sock_path):
        raise ConnectionRefusedError("Connection refused")

    result = collect_preflight_status(
        **_base_kwargs(
            fake_bluez,
            get_server_name_fn=lambda: "should-not-be-called",
            list_sinks_fn=lambda: ["should-not-be-called"],
            exists_fn=lambda path: path == "/run/user/1000/pulse/native",
            connect_fn=_connect_refused,
        )
    )

    audio = result["audio"]
    assert audio["system"] == "unreachable"
    assert audio["socket"] == "unix:/run/user/1000/pulse/native"
    assert audio["socket_exists"] is True
    assert audio["socket_reachable"] is False
    assert audio["last_error"] and "refused" in audio["last_error"].lower()
    assert "audio" in result["failed_collections"]


def test_socket_exists_permission_denied_does_not_mark_linger(monkeypatch, fake_bluez):
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
        **_base_kwargs(
            fake_bluez,
            get_server_name_fn=lambda: "should-not-be-called",
            list_sinks_fn=lambda: [],
            exists_fn=lambda path: True,
            connect_fn=_connect_denied,
        )
    )

    audio = result["audio"]
    assert audio["socket_exists"] is True
    assert audio["socket_reachable"] is False
    assert "refused" not in (audio["last_error"] or "").lower()
    # Crucially not "unreachable" — that signal is reserved for refused sockets
    # so downstream onboarding/guidance does not offer linger instructions.
    assert audio["system"] != "unreachable"
    assert "audio" in result["failed_collections"]


def test_no_socket_and_no_server_sets_system_unknown(monkeypatch, fake_bluez):
    monkeypatch.delenv("PULSE_SERVER", raising=False)

    probe_calls: list[str] = []

    def _probe(sock_path):
        probe_calls.append(sock_path)

    result = collect_preflight_status(
        **_base_kwargs(
            fake_bluez,
            get_server_name_fn=lambda: "not available",
            list_sinks_fn=lambda: [],
            exists_fn=lambda path: False,
            connect_fn=_probe,
        )
    )

    audio = result["audio"]
    assert audio["system"] == "unknown"
    assert audio["socket_exists"] is False
    # Probe must not be invoked without a socket path.
    assert probe_calls == []


def test_server_responds_sets_system_pipewire(monkeypatch, fake_bluez):
    monkeypatch.setenv("PULSE_SERVER", "unix:/run/user/1000/pulse/native")

    def _probe_ok(_sock_path):
        return None

    result = collect_preflight_status(
        **_base_kwargs(
            fake_bluez,
            get_server_name_fn=lambda: "PulseAudio (on PipeWire 1.4.2)",
            list_sinks_fn=lambda: [object(), object()],
            exists_fn=lambda path: True,
            connect_fn=_probe_ok,
        )
    )

    audio = result["audio"]
    assert audio["system"] == "pipewire"
    assert audio["socket_reachable"] is True
    assert audio["sinks"] == 2
