"""Background version checker — polls GitHub releases API periodically."""

from __future__ import annotations

import asyncio
import logging
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
        resp_text = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=15).read())
        data = _json.loads(resp_text)
        tag = data.get("tag_name", "")
        return {
            "version": tag.lstrip("v"),
            "tag": tag,
            "url": data.get("html_url", ""),
            "published_at": data.get("published_at", ""),
            "body": (data.get("body") or "")[:2000],
        }
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
    import os

    from config import load_config

    cfg = load_config()
    if not cfg.get("AUTO_UPDATE", False):
        return False
    # Only auto-update on LXC/systemd (not Docker or HA addon)
    if os.environ.get("SUPERVISOR_TOKEN") or os.path.isfile("/.dockerenv"):
        return False
    return True


async def _auto_apply_update(new_version: str) -> None:
    """Run upgrade.sh automatically in background."""
    import os

    upgrade_script = "/opt/sendspin-client/lxc/upgrade.sh"
    if not os.path.isfile(upgrade_script):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        upgrade_script = os.path.join(base, "lxc", "upgrade.sh")
    if not os.path.isfile(upgrade_script):
        logger.warning("Auto-update: upgrade.sh not found, skipping")
        return

    logger.info("Auto-update: applying v%s via %s", new_version, upgrade_script)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["bash", upgrade_script],
                capture_output=True,
                text=True,
                timeout=120,
            ),
        )
        if result.returncode == 0:
            logger.info("Auto-update: successfully upgraded to v%s", new_version)
        else:
            logger.error(
                "Auto-update: upgrade.sh failed (rc=%d): %s",
                result.returncode,
                result.stderr[-500:] if result.stderr else "",
            )
    except subprocess.TimeoutExpired:
        logger.error("Auto-update: upgrade.sh timed out (120s)")
    except Exception:
        logger.exception("Auto-update: unexpected error")
