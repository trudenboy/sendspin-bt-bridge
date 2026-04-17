"""Shared preflight/runtime collection helpers."""

from __future__ import annotations

import json
import os
import platform as _platform
import subprocess
from typing import Any

from config import get_runtime_version
from services.pulse import get_server_name, list_sinks


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


def collect_preflight_status(
    *,
    get_server_name_fn=None,
    list_sinks_fn=None,
    subprocess_module=None,
    runtime_version_fn=None,
    machine_fn=None,
    exists_fn=None,
    open_fn=None,
) -> dict[str, Any]:
    """Collect preflight runtime checks for reuse across routes and assistants."""
    get_server_name_fn = get_server_name if get_server_name_fn is None else get_server_name_fn
    list_sinks_fn = list_sinks if list_sinks_fn is None else list_sinks_fn
    subprocess_module = subprocess if subprocess_module is None else subprocess_module
    runtime_version_fn = get_runtime_version if runtime_version_fn is None else runtime_version_fn
    machine_fn = _platform.machine if machine_fn is None else machine_fn
    exists_fn = os.path.exists if exists_fn is None else exists_fn
    open_fn = open if open_fn is None else open_fn

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
    if pulse_sock:
        audio_info["socket"] = pulse_sock
        sock_path = pulse_sock.split("unix:", 1)[-1] if pulse_sock.startswith("unix:") else pulse_sock
        audio_info["socket_exists"] = bool(exists_fn(sock_path)) if sock_path else False
    try:
        srv = get_server_name_fn()
        if srv and "pipewire" in str(srv).lower():
            audio_info["system"] = "pipewire"
        elif srv:
            audio_info["system"] = "pulseaudio"
        audio_info["socket_reachable"] = True
        sinks = list_sinks_fn()
        audio_info["sinks"] = len(sinks) if sinks else 0
        collections_status["audio"] = collection_status_payload("ok", count=audio_info["sinks"])
    except Exception as exc:
        audio_info["last_error"] = str(exc) or type(exc).__name__
        if audio_info["socket_exists"]:
            audio_info["system"] = "unreachable"
            audio_info["socket_reachable"] = False
        failed_collections.append("audio")
        collections_status["audio"] = collection_status_payload("error", error=collection_error_payload(exc))

    bt_info: dict[str, Any] = {"controller": False, "adapter": None, "paired_devices": 0}
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

    return {
        "status": "degraded" if failed_collections else "ok",
        "failed_collections": failed_collections,
        "collections_status": collections_status,
        "platform": arch,
        "audio": audio_info,
        "bluetooth": bt_info,
        "dbus": dbus_ok,
        "memory_mb": mem_mb,
        "version": runtime_version_fn(),
    }
