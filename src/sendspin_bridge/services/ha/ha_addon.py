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

# Official Mosquitto broker add-on slug.  Stable since the add-on was
# introduced — used to query install/start state and to build the
# my.home-assistant.io deep-link the UI surfaces when the broker is
# missing.
MOSQUITTO_ADDON_SLUG = "core_mosquitto"
MOSQUITTO_ADDON_DEEP_LINK = "https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_mosquitto"


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


def get_mqtt_addon_credentials(timeout: float = 5.0) -> dict[str, Any] | None:
    """Auto-detect a Mosquitto add-on broker on HAOS via Supervisor API.

    Supervisor exposes installed services at ``/services/<service_type>``;
    when the user has the official Mosquitto add-on installed and started,
    ``GET /services/mqtt`` returns broker credentials in ``data.<…>``.

    Returns a dict ``{"host", "port", "username", "password", "ssl"}`` on
    success, ``None`` when the service is unavailable (no Supervisor token,
    no MQTT add-on installed, or running outside HAOS).  Callers — the
    publisher and the web UI auto-fill button — must surface a
    user-actionable hint ("install Mosquitto add-on") when this returns
    ``None`` and ``HA_INTEGRATION.mqtt.broker == "auto"``.
    """
    payload = _get_supervisor_payload("/services/mqtt", timeout=timeout)
    if not payload:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    # Normalise the keys we care about; Supervisor sometimes wraps service
    # data under nested objects depending on which integration registered
    # the service.  Be lenient and look for both shapes.
    inner = data.get("services", {})
    if isinstance(inner, dict) and "mqtt" in inner and isinstance(inner["mqtt"], dict):
        data = inner["mqtt"]
    host = data.get("host") or data.get("addon_address")
    port = data.get("port") or 1883
    username = data.get("username") or ""
    password = data.get("password") or ""
    ssl = bool(data.get("ssl", False))
    if not host:
        return None
    return {
        "host": str(host),
        "port": int(port),
        "username": str(username),
        "password": str(password),
        "ssl": ssl,
    }


def derive_mqtt_broker_from_ma_url(ma_api_url: str) -> dict[str, Any] | None:
    """Derive a *suggested* MQTT broker host from a configured MA URL.

    Standalone bridges can't query Supervisor for the Mosquitto add-on's
    broker info, but a user who already configured Music Assistant has
    its URL on file (e.g. ``http://192.168.10.10:8095``).  When MA runs
    as an HA add-on, the Mosquitto broker is on the same host — so the
    MA host is a strong heuristic for the broker host.

    Returns a dict with the same shape as
    :func:`get_mqtt_addon_credentials` but **without** username /
    password (the operator must enter Mosquitto credentials manually)
    and with ``source="ma_url"`` so callers can flag the value as a
    suggestion rather than authoritative.

    Returns ``None`` when ``ma_api_url`` is empty / unparseable.
    """
    if not ma_api_url:
        return None
    try:
        from urllib.parse import urlparse

        parsed = urlparse(ma_api_url.strip())
    except Exception:
        return None
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": 1883,
        "username": "",
        "password": "",
        "ssl": False,
        "source": "ma_url",
    }


def get_mosquitto_addon_state(timeout: float = 5.0) -> dict[str, Any]:
    """Report the install/start state of the official Mosquitto add-on.

    Used by the web UI's HA panel to decide whether to show the install
    banner (with a deep-link to the HA add-on store), the "start the
    add-on" hint, or the "auto-configure" CTA.

    Returns a dict with stable shape (always populated):

      ``available``: ``True`` when running in HA addon mode (Supervisor
        token present); ``False`` otherwise.  When ``False`` the other
        fields are best-effort defaults — callers should hide the banner.
      ``installed``: ``True`` when Supervisor reports the add-on as
        present (any state).
      ``started``: ``True`` when ``state == "started"``.
      ``slug``: the add-on slug (``core_mosquitto``).
      ``install_url``: my.home-assistant.io deep-link to the add-on page,
        suitable for an "Install" / "Open Mosquitto add-on" button.
      ``error``: optional string when the Supervisor query failed for a
        reason other than "add-on not installed" (e.g. permission denied).

    Outside HA addon mode this returns ``{"available": False, ...}``
    rather than ``None`` so the UI doesn't have to special-case the
    response shape.
    """
    base: dict[str, Any] = {
        "available": False,
        "installed": False,
        "started": False,
        "slug": MOSQUITTO_ADDON_SLUG,
        "install_url": MOSQUITTO_ADDON_DEEP_LINK,
        "error": None,
    }
    if not os.environ.get("SUPERVISOR_TOKEN", "").strip():
        return base
    base["available"] = True
    info = get_supervisor_addon_info(MOSQUITTO_ADDON_SLUG, timeout=timeout)
    if info is None:
        # Supervisor returns 400 for unknown / not-installed add-ons.  We
        # can't tell a permission-denied response from "not installed"
        # at this layer (``_get_supervisor_payload`` swallows both into
        # ``None``), but treating "no info" as "not installed" matches
        # what the operator would see in the HA add-on list and keeps
        # the install button useful in either case.
        return base
    base["installed"] = True
    base["started"] = str(info.get("state") or "").lower() == "started"
    return base


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

    try:
        port = int(data.get("ingress_port") or 8094)
    except (ValueError, TypeError):
        port = 8094
    return f"http://{hostname}:{port}"


def get_ma_addon_discovery_candidates() -> list[dict[str, str]]:
    """Return prioritized MA API discovery candidates for HA-aware runtimes."""
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def _append(url: str, source: str, summary: str) -> None:
        normalized = str(url or "").strip().rstrip("/")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append({"url": normalized, "source": source, "summary": summary})

    addon = find_started_ma_addon_info()
    hostname = str((addon or {}).get("hostname") or (addon or {}).get("slug") or "").strip()
    if hostname:
        _append(
            f"http://{hostname}:8095",
            "ha_addon_hostname",
            "Home Assistant Supervisor reported a running Music Assistant add-on.",
        )

    _append(
        "http://homeassistant.local:8095",
        "ha_supervisor_dns",
        "Home Assistant local hostname is the next MA discovery fallback.",
    )
    _append(
        "http://localhost:8095",
        "local_fallback",
        "Localhost is the final HA add-on Music Assistant fallback.",
    )
    return candidates
