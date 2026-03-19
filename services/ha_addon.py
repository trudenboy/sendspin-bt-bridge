"""Helpers for Home Assistant add-on Supervisor integration."""

from __future__ import annotations

import json
import logging
import os
import urllib.request as _ur
from typing import Any

logger = logging.getLogger(__name__)

HA_ADDON_BASE_SLUG = "sendspin_bt_bridge"

KNOWN_MA_ADDON_SLUGS = (
    "d5369777_music_assistant",
    "d5369777_music_assistant_beta",
    "d5369777_music_assistant_dev",
)


def _get_supervisor_payload(path: str, timeout: float = 5.0) -> dict[str, Any] | None:
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        return None

    try:
        req = _ur.Request(
            f"http://supervisor{path}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with _ur.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except (OSError, ValueError) as exc:
        logger.debug("Supervisor request failed for %s: %s", path, exc)
        return None

    return payload if isinstance(payload, dict) else None


def get_supervisor_addon_info(slug: str, timeout: float = 5.0) -> dict[str, Any] | None:
    payload = _get_supervisor_payload(f"/addons/{slug}/info", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def get_self_addon_info(timeout: float = 5.0) -> dict[str, Any] | None:
    payload = _get_supervisor_payload("/addons/self/info", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def detect_delivery_channel_from_slug(slug: str) -> str | None:
    normalized = str(slug or "").strip().lower()
    if not normalized:
        return None
    if normalized.endswith(f"{HA_ADDON_BASE_SLUG}_beta"):
        return "beta"
    if normalized.endswith(f"{HA_ADDON_BASE_SLUG}_rc"):
        return "rc"
    if normalized.endswith(HA_ADDON_BASE_SLUG):
        return "stable"
    return None


def get_self_delivery_channel() -> str:
    data = get_self_addon_info()
    if data:
        detected = detect_delivery_channel_from_slug(str(data.get("slug") or ""))
        if detected:
            return detected

    hostname = str(os.environ.get("HOSTNAME") or "").strip().lower()
    if hostname.endswith("-beta"):
        return "beta"
    if hostname.endswith("-rc"):
        return "rc"
    return "stable"


def find_started_ma_addon_info() -> dict[str, Any] | None:
    for slug in KNOWN_MA_ADDON_SLUGS:
        data = get_supervisor_addon_info(slug)
        if not data or data.get("state") != "started":
            continue
        return data
    return None


def get_ma_addon_ui_url() -> str:
    data = find_started_ma_addon_info()
    if not data or not data.get("ingress"):
        return ""
    return str(data.get("ingress_url") or data.get("ingress_entry") or "").rstrip("/")


def get_ma_addon_internal_ingress_url() -> str:
    data = find_started_ma_addon_info()
    if not data:
        return "http://localhost:8094"

    hostname = str(data.get("hostname") or data.get("slug") or "").strip()
    if not hostname:
        return "http://localhost:8094"

    port = int(data.get("ingress_port") or 8094)
    return f"http://{hostname}:{port}"
