"""Bridge-wide controls: restart, log level."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Literal

from sendspin_bridge.application.errors import config_write_error
from sendspin_bridge.application.runtime import submit_on_loop

logger = logging.getLogger(__name__)

#: Tests replace this to observe restarts instead of performing them.
restart_override = None


def detect_runtime() -> str:
    """systemd, ha_addon or docker (the fallback for any other launch)."""
    if os.path.exists("/etc/systemd/system/sendspin-client.service") or os.path.exists(
        "/run/systemd/system/sendspin-client.service"
    ):
        return "systemd"
    if os.path.exists("/data/options.json"):
        return "ha_addon"
    return "docker"


def running_in_container() -> bool:
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as cgroup_file:
            cgroup = cgroup_file.read().lower()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "libpod"))


def _terminate_pid1_or_self() -> None:
    try:
        os.kill(1, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        os.kill(os.getpid(), signal.SIGTERM)


def _restart_ha() -> None:
    import urllib.request as _ur

    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        os.kill(os.getpid(), signal.SIGTERM)
        return
    try:
        req = _ur.Request(
            "http://supervisor/addons/self/restart",
            data=b"{}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        _ur.urlopen(req, timeout=15)
    except Exception as exc:
        logger.warning("Supervisor restart failed: %s; falling back to SIGTERM", exc)
        _terminate_pid1_or_self()


def _restart_standalone() -> None:
    try:
        subprocess.Popen(
            [sys.executable, "-m", "sendspin_bridge.services.lifecycle.standalone_restart", str(os.getpid())],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except Exception:
        logger.exception("Could not launch standalone restart helper")
        return
    os.kill(os.getpid(), signal.SIGTERM)


def restart() -> str:
    """Restart under whatever supervises the bridge; returns that runtime's name.

    The restart happens half a second later so the response gets out first.
    """
    if restart_override is not None:
        restart_override()
        return "override"
    runtime = detect_runtime()
    if runtime == "docker" and not running_in_container():
        runtime = "standalone"
    actions = {
        "systemd": lambda: subprocess.run(
            ["systemctl", "restart", "sendspin-client"], capture_output=True, timeout=10, check=False
        ),
        "ha_addon": _restart_ha,
        "docker": _terminate_pid1_or_self,
        "standalone": _restart_standalone,
    }
    action = actions[runtime]

    def _later() -> None:
        time.sleep(0.5)
        action()

    threading.Thread(target=_later, name="bridge-restart", daemon=True).start()
    return runtime


def set_log_level(level: Literal["INFO", "DEBUG"]) -> None:
    """Persist, apply to this process, and push to every running speaker daemon."""
    from sendspin_bridge.config import update_config
    from sendspin_bridge.config.logging_setup import apply_log_level
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
    from sendspin_bridge.services.ipc.commands import SetLogLevel

    try:
        update_config(lambda cfg: cfg.__setitem__("LOG_LEVEL", level))
    except OSError as exc:
        raise config_write_error(exc, context=f"Cannot save log level {level}") from exc
    apply_log_level(level)
    command = SetLogLevel(level=level)
    for client in get_device_registry_snapshot().active_clients:
        if client.is_running():
            submit_on_loop(
                client._send_subprocess_command(command), description=f"set_log_level for {client.player_name}"
            )
