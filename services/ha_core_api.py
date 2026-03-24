"""Helpers for fetching Home Assistant area/device registry data."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HA_URL = os.getenv("HA_CORE_URL", "http://homeassistant:8123").rstrip("/")
_MAC_COLON_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
_MAC_PLAIN_RE = re.compile(r"^[0-9A-F]{12}$")


class HaCoreApiError(RuntimeError):
    """Raised when Home Assistant registry access fails."""


def _ws_connect(url: str, **kwargs):
    """Connect to a WebSocket, tolerating older websockets versions."""
    from websockets.sync.client import connect as ws_connect

    try:
        return ws_connect(url, proxy=None, **kwargs)
    except TypeError:
        return ws_connect(url, **kwargs)


def _normalize_mac(raw_mac: object) -> str:
    if not isinstance(raw_mac, str):
        return ""
    trimmed = raw_mac.strip().upper()
    if _MAC_COLON_RE.fullmatch(trimmed):
        return trimmed
    collapsed = trimmed.replace("-", "").replace("_", "").replace(":", "").replace(".", "")
    if _MAC_PLAIN_RE.fullmatch(collapsed):
        return ":".join(collapsed[index : index + 2] for index in range(0, 12, 2))
    return ""


def _normalize_area_entries(raw_areas: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(raw_areas, Sequence):
        return normalized
    for raw_area in raw_areas:
        if not isinstance(raw_area, Mapping):
            continue
        area_id = str(raw_area.get("area_id") or raw_area.get("id") or "").strip()
        name = str(raw_area.get("name") or "").strip()
        if not area_id or not name:
            continue
        normalized.append({"area_id": area_id, "name": name})
    normalized.sort(key=lambda item: (item["name"].casefold(), item["area_id"]))
    return normalized


def _normalize_adapters(adapters: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(adapters, Sequence):
        return normalized
    for raw_adapter in adapters:
        if not isinstance(raw_adapter, Mapping):
            continue
        adapter_id = str(raw_adapter.get("id") or "").strip()
        adapter_mac = _normalize_mac(raw_adapter.get("mac"))
        adapter_name = str(raw_adapter.get("name") or "").strip()
        if not adapter_id and not adapter_mac and not adapter_name:
            continue
        normalized.append({"id": adapter_id, "mac": adapter_mac, "name": adapter_name})
    return normalized


def _device_candidate_macs(device: Mapping[str, Any]) -> set[str]:
    candidates: set[str] = set()
    for field_name in ("connections", "identifiers"):
        for entry in device.get(field_name) or []:
            if isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)) and len(entry) >= 2:
                normalized = _normalize_mac(entry[1])
                if normalized:
                    candidates.add(normalized)
    return candidates


def build_adapter_area_matches(
    adapters: object, raw_devices: object, areas_by_id: Mapping[str, Mapping[str, str]]
) -> list[dict[str, str]]:
    """Return best-effort adapter→area suggestions from the HA device registry."""
    normalized_adapters = _normalize_adapters(adapters)
    if not normalized_adapters or not isinstance(raw_devices, Sequence):
        return []

    matches_by_mac: dict[str, list[dict[str, str]]] = {}
    for raw_device in raw_devices:
        if not isinstance(raw_device, Mapping):
            continue
        area_id = str(raw_device.get("area_id") or "").strip()
        if not area_id or area_id not in areas_by_id:
            continue
        device_name = str(raw_device.get("name_by_user") or raw_device.get("name") or "").strip()
        for candidate_mac in _device_candidate_macs(raw_device):
            matches_by_mac.setdefault(candidate_mac, []).append(
                {
                    "area_id": area_id,
                    "area_name": str(areas_by_id[area_id].get("name") or "").strip(),
                    "device_name": device_name,
                }
            )

    adapter_matches: list[dict[str, str]] = []
    for adapter in normalized_adapters:
        adapter_mac = adapter.get("mac") or ""
        if not adapter_mac:
            continue
        candidates = matches_by_mac.get(adapter_mac, [])
        if not candidates:
            continue
        distinct_area_ids = {candidate["area_id"] for candidate in candidates if candidate.get("area_id")}
        if len(distinct_area_ids) != 1:
            logger.debug("Skipping ambiguous HA area match for adapter %s: %s", adapter_mac, distinct_area_ids)
            continue
        chosen = candidates[0]
        adapter_matches.append(
            {
                "adapter_id": adapter.get("id") or "",
                "adapter_mac": adapter_mac,
                "matched_area_id": chosen["area_id"],
                "matched_area_name": chosen["area_name"],
                "match_source": "device_registry_mac",
                "match_confidence": "high",
                "matched_device_name": chosen["device_name"],
                "suggested_name": chosen["area_name"],
            }
        )
    return adapter_matches


def _recv_json_message(ws, *, timeout: int) -> dict[str, Any]:
    message = json.loads(ws.recv(timeout=timeout))
    if not isinstance(message, dict):
        raise HaCoreApiError("Unexpected Home Assistant response")
    return message


def _fetch_registry_payloads(
    ha_token: str, *, include_devices: bool, ha_url: str | None = None
) -> tuple[object, object]:
    if not ha_token:
        raise HaCoreApiError("Home Assistant access token is required")

    if ha_url is not None:
        from urllib.parse import urlparse as _urlparse

        _parsed = _urlparse(ha_url)
        if _parsed.scheme not in {"http", "https"} or not (_parsed.hostname or "").strip():
            raise HaCoreApiError("ha_url must be an http:// or https:// URL with a valid host")

    target_ha_url = (ha_url or _DEFAULT_HA_URL).rstrip("/")
    ws_url = target_ha_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"
    request_frames: list[tuple[int, str]] = [(1, "config/area_registry/list")]
    if include_devices:
        request_frames.append((2, "config/device_registry/list"))

    try:
        with _ws_connect(ws_url, close_timeout=5) as ws:
            hello = _recv_json_message(ws, timeout=5)
            if hello.get("type") != "auth_required":
                raise HaCoreApiError("Home Assistant did not request authentication")

            ws.send(json.dumps({"type": "auth", "access_token": ha_token}))
            auth_response = _recv_json_message(ws, timeout=5)
            if auth_response.get("type") != "auth_ok":
                raise HaCoreApiError("Home Assistant token was rejected")

            responses: dict[int, object] = {}
            for request_id, request_type in request_frames:
                ws.send(json.dumps({"id": request_id, "type": request_type}))
                result = _recv_json_message(ws, timeout=10)
                if result.get("type") != "result" or result.get("id") != request_id:
                    raise HaCoreApiError("Unexpected Home Assistant registry response")
                if not result.get("success"):
                    error_message = str((result.get("error") or {}).get("message") or "Home Assistant request failed")
                    raise HaCoreApiError(error_message)
                responses[request_id] = result.get("result")
    except HaCoreApiError:
        raise
    except Exception as exc:
        raise HaCoreApiError(f"Could not fetch Home Assistant registry data: {exc}") from exc

    return responses.get(1, []), responses.get(2, [])


def fetch_ha_area_catalog(
    ha_token: str, *, include_devices: bool = False, adapters: object = None, ha_url: str | None = None
) -> dict[str, object]:
    """Fetch HA areas and optional adapter matches using a transient HA token."""
    raw_areas, raw_devices = _fetch_registry_payloads(ha_token, include_devices=include_devices, ha_url=ha_url)
    areas = _normalize_area_entries(raw_areas)
    areas_by_id = {area["area_id"]: area for area in areas}
    adapter_matches = build_adapter_area_matches(adapters or [], raw_devices, areas_by_id) if include_devices else []
    return {
        "source": "ingress_token",
        "areas": areas,
        "bridge_name_suggestions": [
            {"area_id": area["area_id"], "label": area["name"], "value": area["name"]} for area in areas
        ],
        "adapter_matches": adapter_matches,
    }
