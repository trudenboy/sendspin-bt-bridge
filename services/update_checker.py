"""Background version checker — polls GitHub releases API periodically."""

from __future__ import annotations

import asyncio
import logging
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
                else:
                    state.set_update_available(None)
                    logger.debug("Version %s is current (latest: %s)", current_version, latest["version"])
        except Exception:
            logger.debug("Update check iteration failed", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)
