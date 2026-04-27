"""Safe, rerunnable operator checks for diagnostics and onboarding."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from datetime import datetime, timezone
from typing import Any

from services.device_registry import get_device_registry_snapshot
from services.ma_client import discover_ma_groups
from services.ma_runtime_state import set_ma_groups
from services.preflight_status import collect_preflight_status
from services.status_snapshot import build_device_snapshot

logger = logging.getLogger(__name__)

UTC = timezone.utc


def _normalize_device_names(device_names: list[str] | None) -> list[str]:
    return [str(name).strip() for name in (device_names or []) if str(name).strip()]


def _bridge_players(active_clients: list[Any]) -> list[dict[str, str]]:
    players: list[dict[str, str]] = []
    for client in active_clients:
        snapshot = build_device_snapshot(client)
        players.append(
            {
                "player_id": str(getattr(client, "player_id", "") or ""),
                "player_name": str(snapshot.player_name or snapshot.bluetooth_mac or "Unknown"),
            }
        )
    return players


def _target_clients(active_clients: list[Any], device_names: list[str]) -> list[Any]:
    if not device_names:
        return list(active_clients)
    wanted = {name.casefold(): name for name in device_names}
    return [
        client
        for client in active_clients
        if str(getattr(client, "player_name", "") or "").strip().casefold() in wanted
    ]


def _result(status: str, check_key: str, summary: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "check_key": check_key,
        "summary": summary,
        "ran_at": datetime.now(tz=UTC).isoformat(),
        **extra,
    }


def _run_preflight_check(check_key: str) -> dict[str, Any]:
    preflight = collect_preflight_status()
    collections = preflight.get("collections_status") or {}
    bluetooth = preflight.get("bluetooth") or {}
    audio = preflight.get("audio") or {}
    if check_key == "runtime_access":
        dbus_ok = bool(preflight.get("dbus"))
        if dbus_ok and preflight.get("status") == "ok":
            return _result("ok", check_key, "Runtime host access looks healthy.", preflight=preflight)
        if not dbus_ok:
            return _result(
                "error", check_key, "D-Bus access is unavailable from the bridge runtime.", preflight=preflight
            )
        return _result(
            "warning",
            check_key,
            "Runtime access is partially degraded; review failed collections.",
            preflight=preflight,
        )
    if check_key == "bluetooth":
        if bluetooth.get("controller"):
            return _result(
                "ok",
                check_key,
                f"Bluetooth controller responded with {int(bluetooth.get('paired_devices') or 0)} paired devices.",
                preflight=preflight,
            )
        status = collections.get("bluetooth", {}).get("status")
        return _result(
            "error" if status == "error" else "warning",
            check_key,
            "Bluetooth controller probe did not return a usable adapter.",
            preflight=preflight,
        )
    if check_key == "audio":
        sinks = int(audio.get("sinks") or 0)
        status = collections.get("audio", {}).get("status")
        if status == "ok" and sinks > 0:
            return _result("ok", check_key, f"Audio backend responded with {sinks} sink(s).", preflight=preflight)
        if status == "error":
            return _result("error", check_key, "Audio backend probe failed.", preflight=preflight)
        return _result(
            "warning", check_key, "Audio backend responded, but no sinks were visible yet.", preflight=preflight
        )
    if check_key == "config_writable":
        # Issue #190 — bind-mount target left as ``root:root`` while
        # the bridge runs as a dropped UID.  The "Re-run check" button
        # in Diagnostics flips green as soon as the operator runs the
        # suggested chown on the host.
        config_writable = preflight.get("config_writable") or {}
        if config_writable.get("status") == "ok":
            return _result(
                "ok",
                check_key,
                f"{config_writable.get('config_dir')} is writable by UID {config_writable.get('uid')}.",
                preflight=preflight,
            )
        remediation = config_writable.get("remediation") or ""
        return _result(
            "error",
            check_key,
            (
                f"{config_writable.get('config_dir')} is not writable by UID "
                f"{config_writable.get('uid')}. {remediation}".strip()
            ),
            preflight=preflight,
        )
    return _result("error", check_key, "Unsupported safe check.", preflight=preflight)


def _run_sink_verification(device_names: list[str] | None = None) -> dict[str, Any]:
    registry = get_device_registry_snapshot()
    targets = _target_clients(list(registry.active_clients), _normalize_device_names(device_names))
    if not targets:
        return _result(
            "warning", "sink_verification", "No active bridge speakers matched this sink check.", device_results=[]
        )

    device_results: list[dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    for client in targets:
        snapshot_before = build_device_snapshot(client)
        name = str(snapshot_before.player_name or snapshot_before.bluetooth_mac or "Unknown")
        if not snapshot_before.bluetooth_connected:
            warning_count += 1
            device_results.append(
                {
                    "device_name": name,
                    "status": "warning",
                    "summary": "Speaker is disconnected, so the sink could not be re-verified.",
                }
            )
            continue
        bt_manager = getattr(client, "bt_manager", None)
        if bt_manager is None or not hasattr(bt_manager, "configure_bluetooth_audio"):
            error_count += 1
            device_results.append(
                {
                    "device_name": name,
                    "status": "error",
                    "summary": "Bluetooth manager is unavailable for sink verification.",
                }
            )
            continue
        try:
            success = bool(bt_manager.configure_bluetooth_audio())
        except Exception:
            logger.exception("Sink verification failed for %s", name)
            error_count += 1
            device_results.append(
                {
                    "device_name": name,
                    "status": "error",
                    "summary": "Exception during Bluetooth audio configuration.",
                }
            )
            continue
        snapshot_after = build_device_snapshot(client)
        if success and snapshot_after.sink_name:
            device_results.append(
                {
                    "device_name": name,
                    "status": "ok",
                    "summary": f"Sink resolved as {snapshot_after.sink_name}.",
                    "sink": snapshot_after.sink_name,
                }
            )
            continue
        error_count += 1
        device_results.append(
            {
                "device_name": name,
                "status": "error",
                "summary": "Connected speaker still has no resolved Bluetooth sink.",
            }
        )
    if error_count:
        status = "error"
        summary = "One or more speakers still failed sink verification."
    elif warning_count:
        status = "warning"
        summary = "Sink verification completed, but some speakers were offline."
    else:
        status = "ok"
        summary = "Bluetooth sinks verified for the selected speakers."
    return _result(status, "sink_verification", summary, device_results=device_results)


def _run_ma_validation(config: dict[str, Any]) -> dict[str, Any]:
    ma_url = str(config.get("MA_API_URL") or "").strip()
    ma_token = str(config.get("MA_API_TOKEN") or "").strip()
    if not (ma_url and ma_token):
        return _result("error", "ma_auth", "Music Assistant is not configured yet.", groups=[])
    registry = get_device_registry_snapshot()
    bridge_players = _bridge_players(list(registry.active_clients))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, discover_ma_groups(ma_url, ma_token, bridge_players))
            name_map, all_groups = future.result(timeout=15)
    except Exception as exc:
        return _result("error", "ma_auth", f"Music Assistant discovery failed: {exc}", groups=[])
    set_ma_groups(name_map, all_groups)
    if all_groups and name_map:
        return _result(
            "ok",
            "ma_auth",
            f"Discovered {len(all_groups)} Music Assistant group(s) and matched {len(name_map)} bridge player(s).",
            groups=all_groups,
            matched_players=len(name_map),
        )
    if all_groups:
        return _result(
            "warning",
            "ma_auth",
            f"Discovered {len(all_groups)} Music Assistant group(s), but none matched the current bridge players.",
            groups=all_groups,
            matched_players=0,
        )
    return _result(
        "warning",
        "ma_auth",
        "Music Assistant responded, but no sync groups were discovered.",
        groups=[],
        matched_players=0,
    )


def run_safe_check(
    check_key: str, *, device_names: list[str] | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one rerunnable safe check and return a structured summary."""
    normalized_key = str(check_key or "").strip()
    config = {} if config is None else config
    if normalized_key in {"runtime_access", "bluetooth", "audio", "config_writable"}:
        return _run_preflight_check(normalized_key)
    if normalized_key == "sink_verification":
        return _run_sink_verification(device_names=device_names)
    if normalized_key == "ma_auth":
        return _run_ma_validation(config)
    return _result("error", normalized_key or "unknown", "Unknown safe check requested.")
