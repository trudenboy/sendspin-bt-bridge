"""Background version checker — polls GitHub releases API periodically."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_REPO = "trudenboy/sendspin-bt-bridge"
CHECK_INTERVAL = 3600  # 1 hour
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse 'v2.22.3' or '2.22.3' into a comparable tuple."""
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


async def check_latest_version() -> dict[str, Any] | None:
    """Fetch latest release from GitHub API. Returns dict or None on error."""
    import json as _json
    import urllib.request

    try:
        req = urllib.request.Request(
            _RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "sendspin-bt-bridge"},
        )
        loop = asyncio.get_running_loop()

        def _fetch() -> tuple[bytes, dict[str, str]]:
            resp = urllib.request.urlopen(req, timeout=15)
            headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.read(), headers

        resp_text, headers = await loop.run_in_executor(None, _fetch)

        # Respect GitHub rate limits
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None and int(remaining) <= 1:
            reset_at = int(headers.get("x-ratelimit-reset", "0"))
            import time

            wait = max(reset_at - int(time.time()), 60)
            logger.warning("GitHub API rate limit nearly exhausted, backing off %ds", wait)
            await asyncio.sleep(wait)

        data = _json.loads(resp_text)
        tag = data.get("tag_name", "")
        return {
            "version": tag.lstrip("v"),
            "tag": tag,
            "url": data.get("html_url", ""),
            "published_at": data.get("published_at", ""),
            "body": (data.get("body") or "")[:2000],
        }
    except urllib.request.HTTPError as exc:
        if exc.code == 403:
            logger.warning("GitHub API rate-limited (HTTP 403), will retry next cycle")
        else:
            logger.debug("Version check failed: HTTP %d", exc.code)
        return None
    except Exception as exc:
        logger.debug("Version check failed: %s", exc)
        return None


async def run_update_checker(current_version: str) -> None:
    """Long-running task: check for updates every CHECK_INTERVAL seconds."""
    import state

    current = _parse_version(current_version)
    # Initial delay — let the app fully start before first check
    await asyncio.sleep(30)

    while True:
        try:
            # Skip check if disabled in config
            from config import load_config

            if not load_config().get("CHECK_UPDATES", True):
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            latest = await check_latest_version()
            if latest:
                remote = _parse_version(latest["version"])
                if remote > current:
                    latest["current_version"] = current_version
                    state.set_update_available(latest)
                    logger.info(
                        "Update available: %s → %s (%s)",
                        current_version,
                        latest["version"],
                        latest["url"],
                    )
                    # Auto-apply if enabled and on LXC/systemd
                    if _should_auto_update():
                        await _auto_apply_update(latest["version"])
                else:
                    state.set_update_available(None)
                    logger.debug("Version %s is current (latest: %s)", current_version, latest["version"])
        except Exception:
            logger.debug("Update check iteration failed", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)


def _should_auto_update() -> bool:
    """Check if auto-update is enabled and runtime supports it."""
    from config import load_config

    cfg = load_config()
    if not cfg.get("AUTO_UPDATE", False):
        return False
    # Only auto-update on LXC/systemd (not Docker or HA addon)
    if os.environ.get("SUPERVISOR_TOKEN") or os.path.isfile("/.dockerenv"):
        return False
    return True


def _normalize_update_ref(target_ref: str | None) -> str | None:
    """Normalize UI/API version input to the tag/branch format expected by upgrade.sh."""
    if target_ref is None:
        return None
    normalized = str(target_ref).strip()
    if not normalized:
        return None
    if normalized.startswith("v") or normalized.startswith("release/"):
        return normalized
    if normalized[0].isdigit():
        return f"v{normalized}"
    return normalized


def _resolve_upgrade_script() -> str | None:
    """Resolve upgrade.sh location for installed and dev environments."""
    upgrade_script = "/opt/sendspin-client/lxc/upgrade.sh"
    if os.path.isfile(upgrade_script):
        return upgrade_script
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base, "lxc", "upgrade.sh")
    if os.path.isfile(candidate):
        return candidate
    return None


def _start_upgrade_job(target_ref: str | None = None) -> dict[str, Any]:
    """Launch upgrade.sh in a transient unit so restart/rollback survives service restart."""
    upgrade_script = _resolve_upgrade_script()
    if not upgrade_script:
        return {"success": False, "error": "upgrade.sh not found"}
    workdir = os.path.dirname(os.path.dirname(upgrade_script))

    unit_name = "sendspin-upgrade"
    active = subprocess.run(
        ["systemctl", "show", f"{unit_name}.service", "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if active.returncode == 0 and active.stdout.strip() in {"activating", "active"}:
        return {
            "success": True,
            "started": False,
            "already_running": True,
            "unit": f"{unit_name}.service",
        }

    subprocess.run(
        ["systemctl", "reset-failed", f"{unit_name}.service"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    command = [
        "systemd-run",
        f"--unit={unit_name}",
        "--service-type=oneshot",
        "--collect",
        "--no-block",
        f"--property=WorkingDirectory={workdir}",
        "--property=StandardOutput=journal",
        "--property=StandardError=journal",
        "bash",
        upgrade_script,
    ]
    normalized_ref = _normalize_update_ref(target_ref)
    if normalized_ref:
        command.extend(["--branch", normalized_ref])

    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "systemd-run failed").strip()
        return {"success": False, "error": error[-500:]}
    return {
        "success": True,
        "started": True,
        "already_running": False,
        "unit": f"{unit_name}.service",
        "target_ref": normalized_ref,
    }


async def _auto_apply_update(new_version: str) -> None:
    """Queue upgrade.sh in a transient unit, pinned to the detected release tag."""
    logger.info("Auto-update: queueing v%s", new_version)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: _start_upgrade_job(new_version))
        if result.get("success"):
            if result.get("already_running"):
                logger.info("Auto-update: upgrade already in progress (%s)", result.get("unit"))
            else:
                logger.info(
                    "Auto-update: started %s for %s",
                    result.get("unit"),
                    result.get("target_ref") or f"v{new_version}",
                )
        else:
            logger.error("Auto-update: failed to start upgrade: %s", result.get("error", "unknown error"))
    except Exception:
        logger.exception("Auto-update: unexpected error")
