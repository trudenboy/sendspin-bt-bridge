"""Background version checker — polls stable GitHub releases and prerelease tags."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from typing import Any
from urllib.parse import quote

from config import DEFAULT_UPDATE_CHANNEL, load_config, normalize_update_channel

logger = logging.getLogger(__name__)

GITHUB_REPO = "trudenboy/sendspin-bt-bridge"
_REPO_WEB_URL = f"https://github.com/{GITHUB_REPO}"
CHECK_INTERVAL = 3600  # 1 hour
_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100"
_TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags?per_page=100"
_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<channel>rc|beta)\.(?P<serial>\d+))?$"
)
_CHANNEL_IMAGE_TAGS = {
    "stable": "stable",
    "rc": "rc",
    "beta": "beta",
}


def _extract_changelog_section(changelog_text: str, version: str) -> str:
    """Return the Keep a Changelog section body for *version*, without the heading."""
    header_re = re.compile(rf"^## \[{re.escape(version)}\](?:\s+-\s+.+)?$", flags=re.MULTILINE)
    match = header_re.search(changelog_text)
    if not match:
        return ""

    remainder = changelog_text[match.end() :].lstrip("\n")
    next_header = re.search(r"^## \[", remainder, flags=re.MULTILINE)
    section = remainder[: next_header.start()] if next_header else remainder
    return section.strip()


def _parse_version(v: str) -> tuple[int, int, int, int, int]:
    """Parse stable and prerelease semver tags into a comparable tuple."""
    match = _SEMVER_RE.match(str(v or "").strip())
    if not match:
        raise ValueError(f"Unsupported version: {v}")
    prerelease_channel = match.group("channel")
    stability_rank = {"beta": 0, "rc": 1, None: 2}[prerelease_channel]
    prerelease_serial = int(match.group("serial") or 0)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        stability_rank,
        prerelease_serial,
    )


def _classify_release_channel(tag: str) -> str | None:
    match = _SEMVER_RE.match(str(tag or "").strip())
    if not match:
        return None
    prerelease_channel = match.group("channel")
    if prerelease_channel in {"rc", "beta"}:
        return prerelease_channel
    return "stable"


def _is_newer_version(remote_version: str, current_version: str) -> bool:
    """Return True when remote_version is newer than current_version."""
    try:
        return _parse_version(remote_version) > _parse_version(current_version)
    except ValueError:
        logger.debug("Could not compare versions %r and %r", remote_version, current_version)
        return False


def _release_sort_key(release: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return _parse_version(str(release.get("tag_name", "")))


def _tag_sort_key(tag: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return _parse_version(str(tag.get("name", "")))


async def _fetch_url(url: str, accept: str = "application/vnd.github+json") -> tuple[bytes, dict[str, str]] | None:
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": "sendspin-bt-bridge"},
        )
        loop = asyncio.get_running_loop()

        def _fetch() -> tuple[bytes, dict[str, str]]:
            resp = urllib.request.urlopen(req, timeout=15)
            headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.read(), headers

        return await loop.run_in_executor(None, _fetch)
    except urllib.request.HTTPError as exc:
        if exc.code == 403 and "api.github.com" in url:
            logger.warning("GitHub API rate-limited (HTTP 403), will retry next cycle")
        else:
            logger.debug("Version check failed for %s: HTTP %d", url, exc.code)
        return None
    except Exception as exc:
        logger.debug("Version check failed for %s: %s", url, exc)
        return None


async def _fetch_json(url: str) -> Any | None:
    import json as _json

    response = await _fetch_url(url)
    if response is None:
        return None

    resp_text, headers = response
    remaining = headers.get("x-ratelimit-remaining")
    if remaining is not None and int(remaining) <= 1:
        import time

        reset_at = int(headers.get("x-ratelimit-reset", "0"))
        wait = max(reset_at - int(time.time()), 60)
        logger.warning("GitHub API rate limit nearly exhausted, backing off %ds", wait)
        await asyncio.sleep(wait)

    try:
        return _json.loads(resp_text)
    except Exception as exc:
        logger.debug("Version check failed: could not decode JSON from %s: %s", url, exc)
        return None


async def _fetch_releases() -> list[dict[str, Any]] | None:
    data = await _fetch_json(_RELEASES_URL)
    if isinstance(data, list):
        return [release for release in data if isinstance(release, dict)]
    logger.debug("Version check failed: unexpected releases payload %r", type(data).__name__)
    return None


async def _fetch_tags() -> list[dict[str, Any]] | None:
    data = await _fetch_json(_TAGS_URL)
    if isinstance(data, list):
        return [tag for tag in data if isinstance(tag, dict)]
    logger.debug("Version check failed: unexpected tags payload %r", type(data).__name__)
    return None


async def _fetch_changelog_section_for_tag(tag: str) -> str:
    response = await _fetch_url(
        f"https://raw.githubusercontent.com/{GITHUB_REPO}/{quote(tag, safe='')}/CHANGELOG.md",
        accept="text/plain",
    )
    if response is None:
        return ""

    resp_text, _headers = response
    try:
        changelog_text = resp_text.decode("utf-8")
    except UnicodeDecodeError:
        logger.debug("Version check failed: could not decode CHANGELOG.md for %s", tag)
        return ""

    return _extract_changelog_section(changelog_text, tag.lstrip("v"))


def _select_latest_release(releases: list[dict[str, Any]], channel: str) -> dict[str, Any] | None:
    normalized_channel = normalize_update_channel(channel)
    eligible: list[dict[str, Any]] = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = str(release.get("tag_name", "")).strip()
        release_channel = _classify_release_channel(tag)
        if release_channel != normalized_channel:
            continue
        if normalized_channel != "stable" and not release.get("prerelease", False):
            continue
        try:
            _parse_version(tag)
        except ValueError:
            continue
        eligible.append(release)

    if not eligible:
        return None
    return max(eligible, key=_release_sort_key)


def _select_latest_tag(tags: list[dict[str, Any]], channel: str) -> dict[str, Any] | None:
    normalized_channel = normalize_update_channel(channel)
    eligible: list[dict[str, Any]] = []
    for tag in tags:
        name = str(tag.get("name", "")).strip()
        if _classify_release_channel(name) != normalized_channel:
            continue
        try:
            _parse_version(name)
        except ValueError:
            continue
        eligible.append(tag)

    if not eligible:
        return None
    return max(eligible, key=_tag_sort_key)


def _release_to_payload(release: dict[str, Any], channel: str) -> dict[str, Any]:
    tag = str(release.get("tag_name", "")).strip()
    return {
        "version": tag.lstrip("v"),
        "tag": tag,
        "url": release.get("html_url", ""),
        "published_at": release.get("published_at", ""),
        "body": (release.get("body") or "")[:2000],
        "channel": channel,
        "target_ref": tag,
        "prerelease": bool(release.get("prerelease", False)),
    }


def _tag_to_payload(tag: dict[str, Any], channel: str, body: str) -> dict[str, Any]:
    tag_name = str(tag.get("name", "")).strip()
    return {
        "version": tag_name.lstrip("v"),
        "tag": tag_name,
        "url": f"{_REPO_WEB_URL}/tree/{quote(tag_name, safe='')}",
        "published_at": "",
        "body": body[:2000],
        "channel": channel,
        "target_ref": tag_name,
        "prerelease": channel != "stable",
    }


async def check_latest_version(channel: str | None = None) -> dict[str, Any] | None:
    """Fetch the newest stable release or prerelease tag for the requested channel."""
    effective_channel = normalize_update_channel(channel or load_config().get("UPDATE_CHANNEL"))
    if effective_channel == "stable":
        releases = await _fetch_releases()
        if not releases:
            return None
        release = _select_latest_release(releases, effective_channel)
        if not release:
            return None
        return _release_to_payload(release, effective_channel)

    tags = await _fetch_tags()
    if not tags:
        return None
    tag = _select_latest_tag(tags, effective_channel)
    if not tag:
        return None
    body = await _fetch_changelog_section_for_tag(str(tag.get("name", "")).strip())
    return _tag_to_payload(tag, effective_channel, body)


async def run_update_checker(current_version: str) -> None:
    """Long-running task: check for updates every CHECK_INTERVAL seconds."""
    import state

    await asyncio.sleep(30)

    while True:
        try:
            cfg = load_config()
            if not cfg.get("CHECK_UPDATES", True):
                await asyncio.sleep(CHECK_INTERVAL)
                continue

            channel = normalize_update_channel(cfg.get("UPDATE_CHANNEL"))
            latest = await check_latest_version(channel)
            if latest and _is_newer_version(latest["tag"], current_version):
                latest["current_version"] = current_version
                state.set_update_available(latest)
                logger.info(
                    "Update available on %s channel: %s → %s (%s)",
                    channel,
                    current_version,
                    latest["version"],
                    latest["url"],
                )
                if _should_auto_update():
                    await _auto_apply_update(latest)
            else:
                state.set_update_available(None)
                if latest:
                    logger.debug(
                        "Version %s is current for %s channel (latest: %s)",
                        current_version,
                        channel,
                        latest["version"],
                    )
        except Exception:
            logger.debug("Update check iteration failed", exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)


def _should_auto_update() -> bool:
    """Check if auto-update is enabled and runtime supports it."""
    cfg = load_config()
    if not cfg.get("AUTO_UPDATE", False):
        return False
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


def channel_image_tag(channel: str | None) -> str:
    """Return the container tag published for an update channel."""
    return _CHANNEL_IMAGE_TAGS[normalize_update_channel(channel or DEFAULT_UPDATE_CHANNEL)]


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


async def _auto_apply_update(latest: dict[str, Any]) -> None:
    """Queue upgrade.sh in a transient unit, pinned to the detected channel ref."""
    target_ref = latest.get("target_ref") or latest.get("tag") or latest.get("version")
    logger.info("Auto-update: queueing %s channel ref %s", latest.get("channel", "stable"), target_ref)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: _start_upgrade_job(target_ref))
        if result.get("success"):
            if result.get("already_running"):
                logger.info("Auto-update: upgrade already in progress (%s)", result.get("unit"))
            else:
                logger.info(
                    "Auto-update: started %s for %s",
                    result.get("unit"),
                    result.get("target_ref") or target_ref,
                )
        else:
            logger.error("Auto-update: failed to start upgrade: %s", result.get("error", "unknown error"))
    except Exception:
        logger.exception("Auto-update: unexpected error")
