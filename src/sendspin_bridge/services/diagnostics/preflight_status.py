"""Shared preflight/runtime collection helpers."""

from __future__ import annotations

import json
import os
import platform as _platform
import socket as _socket
import subprocess
from typing import Any

from sendspin_bridge.config import get_runtime_version
from sendspin_bridge.services.audio.pulse import get_server_name, list_sinks


def _default_connect_fn(sock_path: str, timeout: float = 1.0) -> None:
    """Probe a PulseAudio/PipeWire Unix socket for reachability.

    Raises ``ConnectionRefusedError`` when the server is not listening
    (classic headless-linger symptom), ``PermissionError`` when the caller
    cannot open the socket, and ``OSError`` for protocol/other failures.
    The caller is expected to distinguish "refused" from other failures.
    """
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock_path)
    finally:
        s.close()


def collection_error_payload(exc: Exception) -> dict[str, str]:
    """Return a structured diagnostics error payload."""
    if isinstance(exc, subprocess.TimeoutExpired):
        cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(str(part) for part in exc.cmd or ())
        message = f"{cmd or 'command'} timed out after {exc.timeout}s"
        code = "timeout"
    elif isinstance(exc, PermissionError):
        message = str(exc) or "permission denied"
        code = "permission_denied"
    elif isinstance(exc, FileNotFoundError):
        message = str(exc) or "command not found"
        code = "not_found"
    elif isinstance(exc, (ValueError, json.JSONDecodeError)):
        message = str(exc) or "failed to parse diagnostic data"
        code = "parse_error"
    else:
        message = str(exc) or "diagnostic collection failed"
        code = "unknown"
    return {
        "code": code,
        "message": message,
        "exception_type": type(exc).__name__,
    }


def collection_status_payload(
    status: str, *, count: int | None = None, error: dict[str, str] | None = None
) -> dict[str, Any]:
    """Build a compact diagnostics collection status payload."""
    payload: dict[str, Any] = {"status": status}
    if count is not None:
        payload["count"] = count
    if error is not None:
        payload["error"] = error
    return payload


def _parse_bluetoothctl_adapter(stdout: str) -> str | None:
    parts = stdout.split()
    if len(parts) < 2:
        return None
    return parts[1]


def _parse_memtotal_mb(line: str) -> int | None:
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) // 1024
    except (TypeError, ValueError):
        return None


def _probe_config_writable(config_dir) -> None:
    """Touch + remove a probe file in ``config_dir``.  Raises whatever
    OSError-class exception the underlying touch/remove emits — caller
    classifies via ``collection_error_payload`` so the canonical
    ``permission_denied`` / ``not_found`` codes apply.

    Indirection seam: tests monkey-patch this without faking the
    filesystem itself, which would require root or chmod 555 dance
    that's flaky in CI."""
    import os as _os
    from pathlib import Path

    probe = Path(config_dir) / f".sendspin-preflight-write-test-{_os.getpid()}"
    try:
        probe.touch()
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def _build_config_writable_payload(config_dir) -> dict[str, Any]:
    """Return the ``config_writable`` slice of preflight status.

    On success: ``status=ok``, ``writable=True``, ``remediation=None``.
    On any OSError: ``status=degraded``, ``writable=False``, error
    payload (with canonical code), and a chown/remount remediation
    string the UI renders verbatim.

    Missing ``config_dir`` is treated as ``status=ok`` (no opinion) —
    the entrypoint already creates the dir at startup, so a missing
    dir at runtime means the operator deleted the bind-mount target
    while running, which is a different category surfaced by the
    Config status check.  Avoids polluting the recovery banner during
    tests that don't bother to monkeypatch ``CONFIG_DIR``.

    Always records ``config_dir`` (the actual path probed) and ``uid``
    (the process UID) so a bug-report attached blob is self-evident.
    """
    import errno as _errno
    import os as _os
    from pathlib import Path

    payload: dict[str, Any] = {
        "config_dir": str(config_dir),
        "uid": _os.getuid(),
    }
    # Missing directory: try to create it (entrypoint normally handles
    # this at startup, but a runtime-time delete or a non-container
    # deployment that never had startup-side mkdir would land here).
    # mkdir failures are themselves diagnostic — surface them with the
    # same canonical reason codes as the touch probe.
    cfg_path = Path(config_dir)
    if not cfg_path.is_dir():
        try:
            cfg_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            payload["status"] = "degraded"
            payload["writable"] = False
            payload["error"] = collection_error_payload(exc)
            payload["remediation"] = (
                f"Create {config_dir} on the host and chown -R {_os.getuid()}:{_os.getgid()} <bind-mount target>"
            )
            return payload
    try:
        _probe_config_writable(config_dir)
    except OSError as exc:
        payload["status"] = "degraded"
        payload["writable"] = False
        payload["error"] = collection_error_payload(exc)
        if getattr(exc, "errno", None) == _errno.EACCES or isinstance(exc, PermissionError):
            payload["remediation"] = f"chown -R {_os.getuid()}:{_os.getgid()} <bind-mount target for {config_dir}>"
        elif getattr(exc, "errno", None) == _errno.EROFS:
            payload["remediation"] = (
                f"Remount {config_dir} read-write — read-only filesystem can't persist runtime config"
            )
        else:
            payload["remediation"] = ""
        return payload
    payload["status"] = "ok"
    payload["writable"] = True
    payload["remediation"] = None
    return payload


def collect_preflight_status(
    *,
    get_server_name_fn=None,
    list_sinks_fn=None,
    subprocess_module=None,
    runtime_version_fn=None,
    machine_fn=None,
    exists_fn=None,
    open_fn=None,
    connect_fn=None,
) -> dict[str, Any]:
    """Collect preflight runtime checks for reuse across routes and assistants."""
    get_server_name_fn = get_server_name if get_server_name_fn is None else get_server_name_fn
    list_sinks_fn = list_sinks if list_sinks_fn is None else list_sinks_fn
    subprocess_module = subprocess if subprocess_module is None else subprocess_module
    runtime_version_fn = get_runtime_version if runtime_version_fn is None else runtime_version_fn
    machine_fn = _platform.machine if machine_fn is None else machine_fn
    exists_fn = os.path.exists if exists_fn is None else exists_fn
    open_fn = open if open_fn is None else open_fn
    connect_fn = _default_connect_fn if connect_fn is None else connect_fn

    arch = machine_fn()
    collections_status: dict[str, dict[str, Any]] = {}
    failed_collections: list[str] = []

    audio_info: dict[str, Any] = {
        "system": "unknown",
        "socket": None,
        "socket_exists": False,
        "socket_reachable": None,
        "sinks": 0,
        "last_error": None,
    }
    pulse_sock = os.environ.get("PULSE_SERVER", "")
    sock_path = ""
    if pulse_sock:
        audio_info["socket"] = pulse_sock
        sock_path = pulse_sock.split("unix:", 1)[-1] if pulse_sock.startswith("unix:") else pulse_sock
        audio_info["socket_exists"] = bool(exists_fn(sock_path)) if sock_path else False

    # Explicit reachability probe: services.pulse.get_server_name() swallows
    # connect errors and returns "not available", so relying on it to raise
    # would leave the "unreachable" path dead in production. Connect-probe
    # the Unix socket directly and classify the failure: ConnectionRefused
    # → linger-specific issue; everything else → generic audio failure.
    probe_exc: Exception | None = None
    if audio_info["socket_exists"] and sock_path:
        try:
            connect_fn(sock_path)
            audio_info["socket_reachable"] = True
        except ConnectionRefusedError as exc:
            probe_exc = exc
            audio_info["socket_reachable"] = False
            audio_info["system"] = "unreachable"
            audio_info["last_error"] = str(exc) or "Connection refused"
        except (PermissionError, FileNotFoundError, OSError) as exc:
            probe_exc = exc
            audio_info["socket_reachable"] = False
            audio_info["last_error"] = str(exc) or type(exc).__name__

    if probe_exc is not None:
        failed_collections.append("audio")
        collections_status["audio"] = collection_status_payload("error", error=collection_error_payload(probe_exc))
    else:
        try:
            srv = get_server_name_fn()
            srv_text = str(srv or "").strip()
            if srv_text and "pipewire" in srv_text.lower():
                audio_info["system"] = "pipewire"
            elif srv_text and srv_text.lower() != "not available":
                audio_info["system"] = "pulseaudio"
            sinks = list_sinks_fn()
            audio_info["sinks"] = len(sinks) if sinks else 0
            collections_status["audio"] = collection_status_payload("ok", count=audio_info["sinks"])
        except Exception as exc:
            audio_info["last_error"] = str(exc) or type(exc).__name__
            failed_collections.append("audio")
            collections_status["audio"] = collection_status_payload("error", error=collection_error_payload(exc))

    bt_info: dict[str, Any] = {
        "controller": False,
        "adapter": None,
        "paired_devices": 0,
        # Populated only when ``bluetoothctl list`` returns no Controller —
        # distinguishes "BlueZ daemon is down" (most common cause: host
        # ``bluetooth.service`` not running) from "daemon is up but no
        # adapter passed through" (Docker passthrough / rfkill / etc).
        # ``"active"`` / ``"inactive"`` / ``"failed"`` / ``"unknown"`` /
        # empty string when the probe itself didn't run.
        "daemon": "",
    }
    try:
        result = subprocess_module.run(
            ["bluetoothctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "Controller" in result.stdout:
            bt_info["controller"] = True
            bt_info["adapter"] = _parse_bluetoothctl_adapter(result.stdout)
            bt_info["daemon"] = "active"
        else:
            # No controller surfaced — probe systemd to disambiguate
            # "daemon down" from "daemon up but no adapter".  Treat any
            # subprocess failure as "unknown" so we don't false-flag a
            # non-systemd host.
            try:
                daemon_probe = subprocess_module.run(
                    ["systemctl", "is-active", "bluetooth"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                bt_info["daemon"] = (daemon_probe.stdout or "").strip() or "unknown"
            except Exception:
                bt_info["daemon"] = "unknown"
        paired = subprocess_module.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bt_info["paired_devices"] = paired.stdout.strip().count("Device")
        collections_status["bluetooth"] = collection_status_payload("ok", count=bt_info["paired_devices"])
    except Exception as exc:
        failed_collections.append("bluetooth")
        collections_status["bluetooth"] = collection_status_payload("error", error=collection_error_payload(exc))

    dbus_ok = exists_fn("/var/run/dbus/system_bus_socket") or exists_fn("/run/dbus/system_bus_socket")

    mem_mb = 0
    try:
        with open_fn("/proc/meminfo") as meminfo:
            for line in meminfo:
                if line.startswith("MemTotal:"):
                    parsed_mem = _parse_memtotal_mb(line)
                    if parsed_mem is not None:
                        mem_mb = parsed_mem
                    break
        collections_status["memory"] = collection_status_payload("ok")
    except Exception as exc:
        failed_collections.append("memory")
        collections_status["memory"] = collection_status_payload("error", error=collection_error_payload(exc))

    # Issue #190 root cause: bind-mount target left as ``root:root``
    # while the bridge runs as UID 1000 → first config write raises
    # PermissionError → handler returns generic 500.  Surfacing this
    # in preflight makes it visible in the Diagnostics panel without
    # operators reading container logs.
    from sendspin_bridge.config import CONFIG_DIR

    config_writable_payload = _build_config_writable_payload(CONFIG_DIR)
    if config_writable_payload["status"] == "degraded":
        failed_collections.append("config_writable")
        collections_status["config_writable"] = collection_status_payload(
            "error", error=config_writable_payload.get("error")
        )
    else:
        collections_status["config_writable"] = collection_status_payload("ok")

    return {
        "status": "degraded" if failed_collections else "ok",
        "failed_collections": failed_collections,
        "collections_status": collections_status,
        "platform": arch,
        "audio": audio_info,
        "bluetooth": bt_info,
        "dbus": dbus_ok,
        "memory_mb": mem_mb,
        "config_writable": config_writable_payload,
        "version": runtime_version_fn(),
    }
