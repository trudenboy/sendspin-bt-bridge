"""Installation-type detection shared by status and lifecycle diagnostics."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


def classify_installation(
    *,
    supervisor_token: str = "",
    ha_options: bool = False,
    docker_marker: bool = False,
    container_hint: str = "",
    cgroup_text: str = "",
    systemd_service: bool = False,
) -> str:
    """Classify an installation from already-collected runtime markers.

    A bare Python process is deliberately ``standalone``.  LXC is reported
    only when the OS exposes an LXC virtualization marker; merely not being
    Docker or a Home Assistant add-on is not evidence of LXC.
    """
    if supervisor_token or ha_options:
        return "ha-addon"

    hint = container_hint.strip().lower()
    cgroup = cgroup_text.lower()
    if docker_marker or any(marker in hint for marker in ("docker", "podman", "containerd")):
        return "docker"
    if hint == "lxc" or hint.startswith("lxc-") or "/lxc/" in cgroup or "lxc.payload" in cgroup:
        return "lxc"
    if systemd_service:
        return "systemd"
    return "standalone"


def _read_text(path: str, *, binary: bool = False) -> str:
    try:
        if binary:
            return Path(path).read_bytes().replace(b"\0", b"\n").decode(errors="replace")
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def _container_hint() -> str:
    """Return the most specific container technology hint available."""
    # systemd documents this marker with a lowercase name.
    hint = os.environ.get("container", "").strip()  # noqa: SIM112
    if hint:
        return hint

    hint = _read_text("/run/systemd/container").strip()
    if hint:
        return hint

    init_environment = _read_text("/proc/1/environ", binary=True)
    for entry in init_environment.splitlines():
        if entry.startswith("container="):
            return entry.partition("=")[2]

    try:
        result = subprocess.run(
            ["systemd-detect-virt", "--container"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@lru_cache(maxsize=1)
def detect_installation_type() -> str:
    """Detect the bridge installation type from stable host markers."""
    return classify_installation(
        supervisor_token=os.environ.get("SUPERVISOR_TOKEN", ""),
        ha_options=Path("/data/options.json").exists(),
        docker_marker=Path("/.dockerenv").exists() or Path("/run/.containerenv").exists(),
        container_hint=_container_hint(),
        cgroup_text=_read_text("/proc/1/cgroup"),
        systemd_service=Path("/etc/systemd/system/sendspin-client.service").exists()
        or Path("/run/systemd/system/sendspin-client.service").exists(),
    )
