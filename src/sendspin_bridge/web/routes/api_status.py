"""
Status and diagnostics API Blueprint for sendspin-bt-bridge.

Routes for device status, groups, SSE stream, diagnostics, health,
bug reports, and preflight checks.
"""

from __future__ import annotations

import json
import logging
import os
import platform as _platform
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from sendspin_bridge.config import (
    BUILD_DATE,
    CONFIG_SCHEMA_VERSION,
    RUNTIME_STATE_CONFIG_KEYS,
    SENSITIVE_CONFIG_KEYS,
    get_runtime_version,
    load_config,
    update_config,
)
from sendspin_bridge.config import (
    VERSION as _CONFIG_VERSION,
)
from sendspin_bridge.services.audio.pulse import get_server_name, list_cards, list_sinks
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.diagnostics.event_hooks import get_event_hook_registry
from sendspin_bridge.services.diagnostics.log_analysis import summarize_issue_logs
from sendspin_bridge.services.diagnostics.onboarding_assistant import build_onboarding_assistant_snapshot
from sendspin_bridge.services.diagnostics.operator_check_runner import run_safe_check
from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot
from sendspin_bridge.services.diagnostics.preflight_status import (
    collect_preflight_status as _shared_collect_preflight_status,
)
from sendspin_bridge.services.diagnostics.preflight_status import (
    collection_error_payload as _shared_collection_error_payload,
)
from sendspin_bridge.services.diagnostics.preflight_status import (
    collection_status_payload as _shared_collection_status_payload,
)
from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot
from sendspin_bridge.services.diagnostics.recovery_timeline import (
    build_recovery_timeline_csv,
    build_recovery_timeline_excerpt,
    build_recovery_timeline_text,
)
from sendspin_bridge.services.diagnostics.sendspin_compat import get_runtime_dependency_versions, query_audio_devices
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION
from sendspin_bridge.services.lifecycle.bridge_runtime_state import (
    get_bridge_uptime,
    get_bridge_uptime_seconds,
    get_bridge_uptime_text,
    get_status_version,
    wait_for_status_change,
)
from sendspin_bridge.services.lifecycle.status_snapshot import (
    build_bridge_snapshot,
    build_device_snapshot,
    build_device_snapshot_pairs,
    build_group_snapshots,
    build_mock_runtime_snapshot,
    build_startup_progress_snapshot,
)
from sendspin_bridge.services.music_assistant.ma_runtime_state import (
    get_ma_api_credentials,
    get_ma_groups,
    get_ma_now_playing_for_group,
    get_ma_server_version,
    is_ma_connected,
)

UTC = timezone.utc

logger = logging.getLogger(__name__)

status_bp = Blueprint("api_status", __name__)
VERSION = _CONFIG_VERSION

# ---------------------------------------------------------------------------
# SSE connection limiting — prevent resource exhaustion
# ---------------------------------------------------------------------------

_sse_count = 0
_sse_lock = threading.Lock()
# v2.65.0: bumped from 4 to 6 because the HA custom_component coordinator
# may open both /api/status/stream (snapshots) and /api/status/events
# (typed events) per HA host.  4 was enough for the web UI alone.
_MAX_SSE = 6
_SSE_MAX_LIFETIME = 1800  # 30 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sink_input_id(line: str) -> str | None:
    """Return the sink input ID from a ``pactl list sink-inputs`` header line."""
    _prefix, sep, tail = line.partition("#")
    if not sep:
        return None
    sink_input_id = tail.strip()
    return sink_input_id or None


def _parse_audio_server_name(line: str) -> str | None:
    """Extract the audio server name from ``pactl info`` output."""
    _prefix, sep, tail = line.partition(":")
    if not sep:
        return None
    value = tail.strip()
    return value or None


def _parse_bluetoothctl_adapter(stdout: str) -> str | None:
    """Extract the adapter identifier from ``bluetoothctl list`` output."""
    parts = stdout.split()
    if len(parts) < 2:
        return None
    return parts[1]


def _parse_memtotal_mb(line: str) -> int | None:
    """Extract ``MemTotal`` from /proc/meminfo and convert it to MiB."""
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) // 1024
    except (TypeError, ValueError):
        return None


def _collect_preflight_status() -> dict:
    """Collect preflight runtime checks for reuse across helper routes."""
    return _shared_collect_preflight_status(
        get_server_name_fn=get_server_name,
        list_sinks_fn=list_sinks,
        subprocess_module=subprocess,
        runtime_version_fn=get_runtime_version,
        machine_fn=_platform.machine,
        exists_fn=os.path.exists,
        open_fn=open,
    )


def _collection_error_payload(exc: Exception) -> dict[str, str]:
    """Return a structured diagnostics error payload."""
    return _shared_collection_error_payload(exc)


def _collection_status_payload(status: str, *, count: int | None = None, error: dict[str, str] | None = None) -> dict:
    """Build a compact diagnostics collection status payload."""
    return _shared_collection_status_payload(status, count=count, error=error)


def _collect_bluetooth_daemon_status() -> str:
    r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
    if r.returncode == 0 and "Controller" in r.stdout:
        return "active"
    r2 = subprocess.run(
        ["systemctl", "is-active", "bluetooth"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    return r2.stdout.strip() or "inactive"


def _collect_adapter_diagnostics() -> list[dict]:
    r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
    adapters = []
    for i, line in enumerate(r.stdout.splitlines()):
        if "Controller" not in line:
            continue
        parts = line.split()
        mac = next((p for p in parts if len(p) == 17 and p.count(":") == 5), "")
        adapters.append(
            {
                "id": f"hci{i}",
                "mac": mac,
                "default": "default" in line.lower(),
            }
        )
    return adapters


def _collect_sink_input_diagnostics() -> list[dict]:
    r = subprocess.run(
        ["pactl", "list", "sink-inputs"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    sink_inputs = []
    current: dict = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Sink Input #"):
            if current:
                sink_inputs.append(current)
            sink_input_id = _parse_sink_input_id(line)
            current = {"id": sink_input_id} if sink_input_id else {}
        elif ":" in line or "=" in line:
            separator = ":" if ":" in line else "="
            key, _, val = line.partition(separator)
            key = key.strip().lower().replace(" ", "_").replace(".", "_")
            if key in (
                "sink",
                "state",
                "application_name",
                "application_process_binary",
                "media_name",
                "media_title",
            ):
                current[key] = val.strip().strip('"')
    if current:
        sink_inputs.append(current)
    return sink_inputs


def _collect_portaudio_device_diagnostics() -> list[dict]:
    return [
        {"index": d.index, "name": d.name, "is_default": d.is_default}
        for d in query_audio_devices()
        if d.output_channels > 0
    ]


def _build_onboarding_assistant_payload(
    preflight: dict | None = None,
    *,
    config: dict | None = None,
    devices: list | None = None,
    runtime_mode: str | None = None,
    ma_connected: bool | None = None,
    bridge_state: Any = None,
) -> dict:
    """Build the operator-facing onboarding assistant payload."""
    if preflight is None:
        preflight = _collect_preflight_status()
    if config is None:
        config = load_config()
    if devices is None:
        registry = get_device_registry_snapshot()
        devices = [build_device_snapshot(client) for client in registry.active_clients]
    if runtime_mode is None:
        runtime_mode = build_mock_runtime_snapshot().mode
    if ma_connected is None:
        ma_connected = is_ma_connected()
    normalized_bridge_state = (
        bridge_state
        if bridge_state is not None
        else build_bridge_state_model(
            config=config,
            preflight=preflight,
            devices=devices,
            ma_connected=ma_connected,
            runtime_mode=runtime_mode,
        )
    )
    assistant = build_onboarding_assistant_snapshot(
        config=config,
        preflight=preflight,
        devices=devices,
        ma_connected=ma_connected,
        runtime_mode=runtime_mode,
        bridge_state=normalized_bridge_state,
    )
    return assistant.to_dict()


def _build_recovery_assistant_payload(
    *,
    preflight: dict | None = None,
    config: dict | None = None,
    devices: list | None = None,
    onboarding_assistant: dict | None = None,
    startup_progress: dict | None = None,
    bridge_state: Any = None,
) -> dict:
    """Build the recovery/latency guidance payload used by diagnostics and the UI."""
    if config is None:
        config = load_config()
    if devices is None:
        registry = get_device_registry_snapshot()
        devices = [build_device_snapshot(client) for client in registry.active_clients]
    if onboarding_assistant is None:
        onboarding_assistant = _build_onboarding_assistant_payload(
            preflight=preflight,
            config=config,
            devices=devices,
            bridge_state=bridge_state,
        )
    if startup_progress is None:
        startup_progress = build_startup_progress_snapshot().to_dict()
    recovery = build_recovery_assistant_snapshot(
        config=config,
        devices=devices,
        onboarding_assistant=onboarding_assistant,
        startup_progress=startup_progress,
        bridge_state=bridge_state,
        # Reuse the already-collected preflight payload so the recovery
        # snapshot builder doesn't rerun the bluetoothctl + audio probes.
        preflight=preflight,
    )
    return recovery.to_dict()


def _build_operator_guidance_payload(
    *,
    config: dict | None = None,
    devices: list | None = None,
    disabled_devices: list[dict] | None = None,
    onboarding_assistant: dict | None = None,
    recovery_assistant: dict | None = None,
    startup_progress: dict | None = None,
    preflight: dict | None = None,
    runtime_mode: str | None = None,
    ma_connected: bool | None = None,
    bridge_state: Any = None,
) -> dict:
    """Build the unified top-level operator guidance payload."""
    if config is None:
        config = load_config()
    if devices is None:
        registry = get_device_registry_snapshot()
        devices = [build_device_snapshot(client) for client in registry.active_clients]
        if disabled_devices is None:
            disabled_devices = registry.disabled_devices
    elif disabled_devices is None:
        disabled_devices = []
    if startup_progress is None:
        startup_progress = build_startup_progress_snapshot().to_dict()
    if onboarding_assistant is None:
        onboarding_assistant = _build_onboarding_assistant_payload(
            preflight=preflight,
            config=config,
            devices=devices,
            runtime_mode=runtime_mode,
            ma_connected=ma_connected,
            bridge_state=bridge_state,
        )
    if recovery_assistant is None:
        recovery_assistant = _build_recovery_assistant_payload(
            preflight=preflight,
            config=config,
            devices=devices,
            onboarding_assistant=onboarding_assistant,
            startup_progress=startup_progress,
            bridge_state=bridge_state,
        )
    return build_operator_guidance_snapshot(
        config=config,
        onboarding_assistant=onboarding_assistant,
        recovery_assistant=recovery_assistant,
        startup_progress=startup_progress,
        devices=devices,
        disabled_devices=disabled_devices,
    ).to_dict()


def _build_status_payload() -> dict:
    """Build the full `/api/status` payload including unified operator guidance."""
    registry = get_device_registry_snapshot()
    bridge_snapshot = build_bridge_snapshot(registry.active_clients)
    payload = bridge_snapshot.to_status_payload()
    config = load_config()
    preflight = _collect_preflight_status()
    startup_progress = bridge_snapshot.startup_progress.to_dict() if bridge_snapshot.startup_progress else {}
    bridge_state = build_bridge_state_model(
        config=config,
        preflight=preflight,
        devices=bridge_snapshot.devices,
        ma_connected=bridge_snapshot.ma_connected,
        runtime_mode=bridge_snapshot.runtime_mode,
        startup_progress=startup_progress,
        update_available=bool(bridge_snapshot.update_available),
        disabled_devices=bridge_snapshot.disabled_devices,
    )
    onboarding_assistant = _build_onboarding_assistant_payload(
        preflight=preflight,
        config=config,
        devices=bridge_snapshot.devices,
        runtime_mode=bridge_snapshot.runtime_mode,
        ma_connected=bridge_snapshot.ma_connected,
        bridge_state=bridge_state,
    )
    recovery_assistant = _build_recovery_assistant_payload(
        preflight=preflight,
        config=config,
        devices=bridge_snapshot.devices,
        onboarding_assistant=onboarding_assistant,
        startup_progress=startup_progress,
        bridge_state=bridge_state,
    )
    payload["preflight"] = preflight
    payload["state_model"] = bridge_state.to_dict()
    payload["onboarding_assistant"] = onboarding_assistant
    payload["recovery_assistant"] = recovery_assistant
    payload["operator_guidance"] = _build_operator_guidance_payload(
        config=config,
        devices=bridge_snapshot.devices,
        disabled_devices=bridge_snapshot.disabled_devices,
        onboarding_assistant=onboarding_assistant,
        recovery_assistant=recovery_assistant,
        startup_progress=startup_progress,
        preflight=preflight,
        runtime_mode=bridge_snapshot.runtime_mode,
        ma_connected=bridge_snapshot.ma_connected,
        bridge_state=bridge_state,
    )
    return payload


def get_client_status_for(client):
    """Get status dict for a specific client."""
    try:
        status = build_device_snapshot(client).to_dict()
        logger.debug("Status retrieved: %s", status)
        return status

    except Exception as e:
        logger.exception("Error getting client status: %s", e)
        return {
            "connected": False,
            "server_connected": False,
            "bluetooth_connected": False,
            "bluetooth_available": False,
            "playing": False,
            "error": "Failed to retrieve status",
            "version": get_runtime_version(),
            "build_date": BUILD_DATE,
            "bluetooth_mac": None,
        }


def _build_groups_summary(clients: list) -> list[dict]:
    """Build a list of group objects from the current client list.

    Players sharing the same non-None group_id are merged into one group entry.
    Solo players (group_id=None) each appear as their own single-member group.

    When MA API group data is available, entries that resolve to the same MA
    syncgroup are merged (Sendspin assigns unique UUIDs per session, so two
    local devices in the same MA syncgroup have different group_ids).  Each
    merged entry is then enriched with ``external_members`` (players from
    other bridges) and ``external_count``.
    """
    return [group.to_dict() for group in build_group_snapshots(clients)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@status_bp.route("/api/status")
def api_status():
    """Return status for all client instances."""
    payload = _build_status_payload()
    auth_enabled = bool(current_app.config.get("AUTH_ENABLED", False))
    payload["auth_enabled"] = auth_enabled
    if not auth_enabled:
        remote = request.remote_addr or ""
        if remote not in ("127.0.0.1", "::1"):
            payload["auth_warning"] = (
                "Authentication is disabled and this endpoint is being accessed "
                "from a non-loopback IP. Consider enabling auth to protect sensitive data."
            )
    return jsonify(payload)


@status_bp.route("/api/groups")
def api_groups():
    """Return a list of MA player groups with their members.

    Players sharing the same group_id (assigned by MA when placed in a Sync Group)
    are returned as one entry. Solo players (not in any MA group) each appear as
    their own single-member entry with group_id=null.
    """
    registry = get_device_registry_snapshot()
    return jsonify(_build_groups_summary(registry.active_clients))


@status_bp.route("/api/startup-progress")
def api_startup_progress():
    """Return bridge startup progress for operators and the UI."""
    return jsonify(build_startup_progress_snapshot().to_dict())


@status_bp.route("/api/runtime-info")
def api_runtime_info():
    """Return bridge runtime-mode and mock-runtime explainability metadata."""
    return jsonify(build_mock_runtime_snapshot().to_dict())


@status_bp.route("/api/bridge/telemetry")
def api_bridge_telemetry():
    """Return bridge resource telemetry and runtime-scoped hook activity."""
    return jsonify(_build_bridge_telemetry_payload())


@status_bp.route("/api/hooks")
def api_hook_registry_status():
    """Return registered runtime webhooks and recent delivery results."""
    return jsonify(get_event_hook_registry().snapshot())


@status_bp.route("/api/hooks", methods=["POST"])
def api_hook_register():
    """Register a runtime-scoped webhook for bridge or device events."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    timeout_raw = payload.get("timeout_sec")
    if timeout_raw in (None, ""):
        timeout_sec = 5.0
    elif isinstance(timeout_raw, (int, float, str)):
        try:
            timeout_sec = float(timeout_raw)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"Invalid timeout_sec: {exc}"}), 400
    else:
        return jsonify({"error": "Invalid timeout_sec: must be a number"}), 400
    try:
        hook = get_event_hook_registry().register(
            url=str(payload.get("url") or ""),
            categories=payload.get("categories") if isinstance(payload.get("categories"), list) else None,
            event_types=payload.get("event_types") if isinstance(payload.get("event_types"), list) else None,
            timeout_sec=timeout_sec,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"success": True, "hook": hook}), 201


@status_bp.route("/api/hooks/<hook_id>", methods=["DELETE"])
def api_hook_unregister(hook_id: str):
    """Remove a runtime-scoped webhook subscription."""
    if not get_event_hook_registry().unregister(hook_id):
        return jsonify({"error": "Hook not found"}), 404
    return jsonify({"success": True})


@status_bp.route("/api/status/stream")
def api_status_stream():
    """Server-Sent Events endpoint — pushes status when it changes.

    Clients connect once and receive real-time updates instead of polling
    /api/status every 2 seconds.  A heartbeat comment is sent every 15 s to
    keep the connection alive through proxies (including HA ingress).

    Uses ``threading.Condition.wait_for()`` to avoid the race between reading
    ``_status_version`` and blocking: the Condition lock ensures that any
    ``notify_status_changed()`` call either happens before we start waiting
    (so ``wait_for`` returns immediately) or wakes us up cleanly.
    """
    global _sse_count
    with _sse_lock:
        if _sse_count >= _MAX_SSE:
            return 'data: {"error": "too many listeners"}\n\n', 503, {"Content-Type": "text/event-stream"}
        _sse_count += 1

    def _generate():
        global _sse_count
        try:

            def _build_snapshot():
                return _build_status_payload()

            # Send current status immediately so the client doesn't have to wait
            # for the first change event (important through HA ingress proxy).
            #
            # Leading 2 KB padding flushes proxy buffers (Nginx, HA Ingress,
            # Cloudflare) so they start streaming instead of buffering the
            # entire response.
            yield ": " + " " * 2048 + "\n\n"

            initial = _build_snapshot()
            if initial:
                yield f"data: {json.dumps(initial)}\n\n"

            last_version = get_status_version()
            started = time.monotonic()
            while True:
                if time.monotonic() - started >= _SSE_MAX_LIFETIME:
                    yield 'data: {"error": "session expired"}\n\n'
                    break

                changed, last_version = wait_for_status_change(last_version, timeout=15)

                if changed:
                    data = _build_snapshot()
                    if data:
                        yield f"data: {json.dumps(data)}\n\n"
                else:
                    # 15 s timeout — send a keepalive comment so proxies don't close
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                _sse_count -= 1

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            # ``no-cache`` keeps browsers from caching SSE chunks.
            # ``no-transform`` (RFC 7234 §5.2.2.4) instructs every
            # intermediary — explicitly including the HA Supervisor
            # ingress proxy — NOT to apply deflate / gzip compression
            # on the response body.  Without this, ingress compresses
            # the SSE chunks and the browser receives garbled
            # event-stream frames (was the root cause behind the
            # rc.3 WebSocket migration attempt; see CHANGELOG rc.4).
            "Cache-Control": "no-cache, no-transform",
            # Force-set the encoding so the compression middleware
            # in aiohttp / nginx / cloudflare doesn't try to wrap us.
            "Content-Encoding": "identity",
            # nginx-style hint to flush each chunk immediately.
            "X-Accel-Buffering": "no",
        },
    )


@status_bp.route("/api/diagnostics")
def api_diagnostics():
    """Return structured health diagnostics."""
    try:
        collections_status: dict[str, dict] = {}
        failed_collections: list[str] = []

        def _record_success(name: str, *, count: int | None = None) -> None:
            collections_status[name] = _collection_status_payload("ok", count=count)

        def _record_failure(name: str, exc: Exception, *, fallback, log_message: str):
            logger.exception(log_message)
            failed_collections.append(name)
            collections_status[name] = _collection_status_payload("error", error=_collection_error_payload(exc))
            return fallback

        # Runtime detection
        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        uptime_str = get_bridge_uptime_text()

        diag: dict = {
            "version": get_runtime_version(),
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "auth_enabled": bool(current_app.config.get("AUTH_ENABLED", False)),
            "contract_versions": {
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
            },
            "environment": {},
            "startup_progress": {},
            "runtime_info": {},
        }

        try:
            diag["environment"] = _collect_environment()
            _record_success("environment")
        except Exception as exc:
            diag["environment"] = _record_failure(
                "environment",
                exc,
                fallback={"error": "Failed to collect environment"},
                log_message="Failed to collect environment for diagnostics",
            )

        try:
            diag["startup_progress"] = build_startup_progress_snapshot().to_dict()
            _record_success("startup_progress")
        except Exception as exc:
            diag["startup_progress"] = _record_failure(
                "startup_progress",
                exc,
                fallback={"error": "Failed to collect startup progress"},
                log_message="Failed to collect startup progress for diagnostics",
            )

        try:
            diag["runtime_info"] = build_mock_runtime_snapshot().to_dict()
            _record_success("runtime_info")
        except Exception as exc:
            diag["runtime_info"] = _record_failure(
                "runtime_info",
                exc,
                fallback={"mode": "unknown"},
                log_message="Failed to collect runtime info for diagnostics",
            )

        try:
            diag["bluetooth_daemon"] = _collect_bluetooth_daemon_status()
            _record_success("bluetooth_daemon")
        except Exception as exc:
            diag["bluetooth_daemon"] = _record_failure(
                "bluetooth_daemon",
                exc,
                fallback="unknown",
                log_message="Failed to collect bluetooth daemon status for diagnostics",
            )

        dbus_env = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
        dbus_path = dbus_env.replace("unix:path=", "") if dbus_env else "/run/dbus/system_bus_socket"
        diag["dbus_available"] = os.path.exists(dbus_path)

        try:
            diag["adapters"] = _collect_adapter_diagnostics()
            _record_success("adapters", count=len(diag["adapters"]))
        except Exception as exc:
            diag["adapters"] = _record_failure(
                "adapters",
                exc,
                fallback=[{"error": "Failed to enumerate adapters"}],
                log_message="Failed to enumerate adapters for diagnostics",
            )

        try:
            diag["pulseaudio"] = get_server_name()
            _record_success("pulseaudio")
        except Exception as exc:
            diag["pulseaudio"] = _record_failure(
                "pulseaudio",
                exc,
                fallback="not available",
                log_message="Failed to collect PulseAudio server name for diagnostics",
            )

        try:
            diag["sinks"] = [s["name"] for s in list_sinks() if "bluez" in s["name"].lower()]
            _record_success("sinks", count=len(diag["sinks"]))
        except Exception as exc:
            diag["sinks"] = _record_failure(
                "sinks",
                exc,
                fallback=[],
                log_message="Failed to list sinks for diagnostics",
            )

        try:
            diag["cards"] = list_cards()
            _record_success("cards", count=len(diag["cards"]))
        except Exception as exc:
            diag["cards"] = _record_failure(
                "cards",
                exc,
                fallback=[],
                log_message="Failed to list cards for diagnostics",
            )

        device_diag = []
        registry = get_device_registry_snapshot()
        snapshot_pairs = build_device_snapshot_pairs(registry.active_clients)
        for _client, device in snapshot_pairs:
            device_diag.append(
                {
                    "name": device.player_name or "Unknown",
                    "mac": device.bluetooth_mac,
                    "connected": device.bluetooth_connected,
                    "enabled": device.bt_management_enabled,
                    "playing": device.playing,
                    "sink": device.sink_name,
                    "last_error": device.extra.get("last_error"),
                    "health_summary": device.health_summary,
                    "capabilities": device.capabilities,
                    "recent_events": device.recent_events,
                }
            )
        diag["devices"] = device_diag

        # MA API integration status
        ma_url, ma_token = get_ma_api_credentials()
        ma_groups = get_ma_groups()

        # Build a player_id→client lookup for matching MA members to bridge devices
        bridge_by_id = {
            getattr(client, "player_id", ""): (client, device)
            for client, device in snapshot_pairs
            if getattr(client, "player_id", "")
        }

        enriched_groups = []
        for g in ma_groups:
            members_detail = []
            for m in g.get("members", []):
                mid = m.get("id", "")
                bridge_entry = bridge_by_id.get(mid)
                member_info: dict = {
                    "id": mid,
                    "name": m.get("name", m.get("id", "")),
                    "state": m.get("state"),
                    "volume": m.get("volume"),
                    "available": m.get("available", True),
                    "is_bridge": bridge_entry is not None,
                }
                if bridge_entry:
                    bridge_client, bridge_device = bridge_entry
                    member_info["enabled"] = getattr(bridge_client, "bt_management_enabled", True)
                    member_info["bt_connected"] = bridge_device.bluetooth_connected
                    member_info["server_connected"] = bridge_device.server_connected
                    member_info["playing"] = bridge_device.playing
                    member_info["sink"] = bridge_device.sink_name
                    member_info["bt_mac"] = (
                        getattr(bridge_client.bt_manager, "mac_address", None) if bridge_client.bt_manager else None
                    )
                members_detail.append(member_info)

            np = get_ma_now_playing_for_group(g["id"])
            group_info: dict = {
                "id": g["id"],
                "name": g.get("name", ""),
                "members": members_detail,
            }
            if np:
                group_info["now_playing"] = {
                    "title": np.get("title"),
                    "artist": np.get("artist"),
                    "state": np.get("state"),
                }
            enriched_groups.append(group_info)

        diag["ma_integration"] = {
            "configured": bool(ma_url and ma_token),
            "connected": is_ma_connected(),
            "version": get_ma_server_version(),
            "url": ma_url or "",
            "syncgroups": enriched_groups,
        }

        # PA sink-inputs with properties (for routing diagnostics)
        try:
            diag["sink_inputs"] = _collect_sink_input_diagnostics()
            _record_success("sink_inputs", count=len(diag["sink_inputs"]))
        except Exception as exc:
            diag["sink_inputs"] = _record_failure(
                "sink_inputs",
                exc,
                fallback=[{"error": "Failed to list sink inputs"}],
                log_message="Failed to list sink inputs for diagnostics",
            )

        # PortAudio devices available inside the container
        try:
            diag["portaudio_devices"] = _collect_portaudio_device_diagnostics()
            _record_success("portaudio_devices", count=len(diag["portaudio_devices"]))
        except Exception as exc:
            diag["portaudio_devices"] = _record_failure(
                "portaudio_devices",
                exc,
                fallback=[{"error": "Failed to list PortAudio devices"}],
                log_message="Failed to list PortAudio devices for diagnostics",
            )

        try:
            diag["subprocesses"] = _collect_subprocess_info()
            _record_success("subprocesses", count=len(diag["subprocesses"]))
        except Exception as exc:
            diag["subprocesses"] = _record_failure(
                "subprocesses",
                exc,
                fallback=[{"error": "Failed to collect subprocess info"}],
                log_message="Failed to collect subprocess info for diagnostics",
            )

        try:
            diag["event_hooks"] = get_event_hook_registry().snapshot()
            _record_success("event_hooks")
        except Exception as exc:
            diag["event_hooks"] = _record_failure(
                "event_hooks",
                exc,
                fallback={"error": "Failed to collect event hooks"},
                log_message="Failed to collect event hooks for diagnostics",
            )
        try:
            onboarding_assistant = _build_onboarding_assistant_payload(
                config=load_config(),
                devices=[device for _client, device in snapshot_pairs],
                runtime_mode=diag["runtime_info"].get("mode", "unknown"),
                ma_connected=is_ma_connected(),
            )
            diag["onboarding_assistant"] = onboarding_assistant
            _record_success("onboarding_assistant")
        except Exception as exc:
            onboarding_assistant = {"error": "Failed to build onboarding assistant"}
            diag["onboarding_assistant"] = _record_failure(
                "onboarding_assistant",
                exc,
                fallback=onboarding_assistant,
                log_message="Failed to build onboarding assistant for diagnostics",
            )
        try:
            diag["recovery_assistant"] = _build_recovery_assistant_payload(
                config=load_config(),
                devices=[device for _client, device in snapshot_pairs],
                onboarding_assistant=onboarding_assistant,
                startup_progress=diag["startup_progress"],
            )
            _record_success("recovery_assistant")
        except Exception as exc:
            diag["recovery_assistant"] = _record_failure(
                "recovery_assistant",
                exc,
                fallback={"error": "Failed to build recovery assistant"},
                log_message="Failed to build recovery assistant for diagnostics",
            )
        try:
            diag["operator_guidance"] = _build_operator_guidance_payload(
                config=load_config(),
                devices=[device for _client, device in snapshot_pairs],
                onboarding_assistant=onboarding_assistant,
                recovery_assistant=diag["recovery_assistant"],
                startup_progress=diag["startup_progress"],
                runtime_mode=diag["runtime_info"].get("mode", "unknown"),
                ma_connected=is_ma_connected(),
            )
            _record_success("operator_guidance")
        except Exception as exc:
            diag["operator_guidance"] = _record_failure(
                "operator_guidance",
                exc,
                fallback={"error": "Failed to build operator guidance"},
                log_message="Failed to build operator guidance for diagnostics",
            )
        try:
            diag["telemetry"] = _build_bridge_telemetry_payload(
                environment=diag["environment"],
                subprocesses=diag["subprocesses"],
                startup_progress=diag["startup_progress"],
                runtime_info=diag["runtime_info"],
                event_hooks=diag["event_hooks"],
            )
            _record_success("telemetry")
        except Exception as exc:
            diag["telemetry"] = _record_failure(
                "telemetry",
                exc,
                fallback={"error": "Failed to build telemetry payload"},
                log_message="Failed to build telemetry payload for diagnostics",
            )

        # Surface the prior-run breadcrumb (boot.prev.json + exit.prev.json
        # paired into a derived ``exit_kind``) so the live diagnostics UI
        # can render a "Last run" card without needing the user to download
        # the text bundle. Always include the key so the frontend can
        # detect the absence of a prior run cleanly (value is ``None``).
        try:
            diag["last_run"] = _collect_last_run_summary()
        except Exception:
            diag["last_run"] = None

        diag["status"] = "degraded" if failed_collections else "ok"
        diag["failed_collections"] = failed_collections
        diag["collections_status"] = collections_status
        return jsonify(diag)
    except Exception:
        logger.exception("Diagnostics collection failed")
        return jsonify(
            {
                "error": "Internal error",
                "status": "failed",
                "failed_collections": ["diagnostics"],
                "collections_status": {
                    "diagnostics": _collection_status_payload(
                        "error",
                        error=_collection_error_payload(RuntimeError("diagnostics collection failed")),
                    )
                },
            }
        ), 500


# ---------------------------------------------------------------------------
# (Logs endpoint lives in api_config.py — reads journalctl / supervisor / docker)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/bugreport — assembled bug report with masked sensitive data
# ---------------------------------------------------------------------------

_ANSI_RE_STATUS = re.compile(r"\x1b\[[0-9;]*m")

_MAC_RE = re.compile(
    r"([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})"
)
_IPV4_RE = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


def _mask_mac(m: re.Match) -> str:
    """AA:BB:**:**:**:FF"""
    g = m.groups()
    return f"{g[0]}:{g[1]}:**:**:**:{g[5]}"


def _mask_ip(m: re.Match) -> str:
    """192.168.*.*"""
    return f"{m.group(1)}.{m.group(2)}.*.*"


def _mask_text(text: str) -> str:
    """Mask MAC and IPv4 addresses in arbitrary text."""
    text = _MAC_RE.sub(_mask_mac, text)
    return _IPV4_RE.sub(_mask_ip, text)


def _mask_obj(obj: object) -> object:
    """Recursively mask MAC/IP in dicts, lists, and strings."""
    if isinstance(obj, str):
        return _mask_text(obj)
    if isinstance(obj, dict):
        return {k: _mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask_obj(item) for item in obj]
    return obj


def _collect_environment() -> dict:
    """Gather system environment info for bug reports."""
    env: dict = {
        "python": sys.version,
        "platform": _platform.platform(),
        "arch": _platform.machine(),
        "kernel": _platform.release(),
    }

    # BlueZ version
    try:
        r = subprocess.run(["bluetoothctl", "--version"], capture_output=True, text=True, timeout=3)
        env["bluez"] = r.stdout.strip()
    except Exception:
        env["bluez"] = "unknown"

    # PulseAudio / PipeWire version
    for cmd in [["pulseaudio", "--version"], ["pipewire", "--version"]]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                env["audio_server"] = r.stdout.strip()
                break
        except FileNotFoundError:
            continue
    else:
        try:
            r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if "Server Name" in line:
                    audio_server = _parse_audio_server_name(line)
                    if audio_server:
                        env["audio_server"] = audio_server
                        break
        except Exception:
            env["audio_server"] = "unknown"

    # Process memory (RSS)
    try:
        import resource

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux reports KB
        if sys.platform == "darwin":
            rss_kb //= 1024
        env["process_rss_mb"] = round(rss_kb / 1024, 1)
    except Exception:
        pass

    env.update(get_runtime_dependency_versions())

    # MA *server* version (vs. ``music-assistant-client`` library
    # version, which lives in the runtime-deps pin).  Cached at WS
    # handshake; pre-handshake it's an empty string.  Surfaced as
    # "unknown" so the bug-report markdown stays consistent with the
    # other "?" fields rather than silently dropping the key — issue
    # #190 was diagnosed slowly because we couldn't tell which MA
    # build the operator was running.
    env["ma_server_version"] = get_ma_server_version() or "unknown"

    return env


def _collect_subprocess_info() -> list[dict]:
    """Gather per-device subprocess info."""
    info = []
    snapshot = get_device_registry_snapshot().active_clients
    for client in snapshot:
        proc = getattr(client, "_daemon_proc", None)
        entry: dict = {
            "name": getattr(client, "player_name", "?"),
            "pid": proc.pid if proc else None,
            "alive": proc is not None and proc.returncode is None if proc else False,
            "running": getattr(client, "running", False),
            "restart_delay": getattr(client, "_restart_delay", 1.0),
            "zombie_restarts": getattr(client, "_zombie_restart_count", 0),
        }
        # Reconnect info from status
        status = getattr(client, "status", None)
        if status:
            entry["reconnecting"] = status.get("reconnecting", False)
            entry["reconnect_attempt"] = status.get("reconnect_attempt", 0)
            entry["last_error"] = status.get("last_error")
            entry["last_error_at"] = status.get("last_error_at")
        entry["process_rss_mb"] = _collect_process_rss_mb(entry["pid"])
        info.append(entry)
    return info


def _collect_process_rss_mb(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        rss_kb = int(result.stdout.strip() or "0")
    except (OSError, ValueError):
        return None
    return round(rss_kb / 1024, 1)


def _build_bridge_telemetry_payload(
    *,
    environment: dict | None = None,
    subprocesses: list[dict] | None = None,
    startup_progress: dict | None = None,
    runtime_info: dict | None = None,
    event_hooks: dict | None = None,
) -> dict:
    environment = _collect_environment() if environment is None else environment
    subprocesses = _collect_subprocess_info() if subprocesses is None else subprocesses
    startup_progress = build_startup_progress_snapshot().to_dict() if startup_progress is None else startup_progress
    runtime_info = build_mock_runtime_snapshot().to_dict() if runtime_info is None else runtime_info
    event_hooks = get_event_hook_registry().snapshot() if event_hooks is None else event_hooks
    uptime_seconds = get_bridge_uptime_seconds()
    return {
        "bridge": {
            "uptime_seconds": uptime_seconds,
            "process_rss_mb": environment.get("process_rss_mb"),
            "python": environment.get("python"),
            "platform": environment.get("platform"),
            "arch": environment.get("arch"),
            "kernel": environment.get("kernel"),
            "audio_server": environment.get("audio_server"),
            "bluez": environment.get("bluez"),
        },
        "startup_progress": startup_progress,
        "runtime_info": runtime_info,
        "subprocesses": subprocesses,
        "event_hooks": event_hooks,
    }


def _sanitized_config() -> dict:
    """Return config with secrets redacted."""
    try:
        cfg = load_config()
    except Exception:
        return {"error": "could not load config"}

    redacted_keys = SENSITIVE_CONFIG_KEYS | RUNTIME_STATE_CONFIG_KEYS
    result: dict = {}
    for k, v in cfg.items():
        if k in redacted_keys:
            result[k] = "***"
        elif k == "MA_API_URL" and v:
            result[k] = _mask_text(str(v))
        elif k == "BLUETOOTH_DEVICES" and isinstance(v, list):
            masked_devs: list = [
                {dk: (_mask_text(str(dv)) if dk == "mac" else dv) for dk, dv in d.items()} if isinstance(d, dict) else d
                for d in v
            ]
            result[k] = masked_devs
        else:
            result[k] = v
    return result


def _collect_last_run_summary() -> dict | None:
    """Return a dict describing the previous run, or None on first boot.

    Reads the rotated breadcrumbs (``boot.prev.json`` + ``exit.prev.json``)
    via :class:`BreadcrumbStore`.  Best-effort — any failure returns
    ``None`` so the diagnostics bundle keeps generating.
    """
    try:
        from sendspin_bridge.config import CONFIG_FILE
        from sendspin_bridge.services.lifecycle.exit_breadcrumb import BreadcrumbStore

        store = BreadcrumbStore(Path(CONFIG_FILE).parent)
        prev = store.read_previous()
        if prev is None:
            return None
        return prev.to_dict()
    except Exception:
        return None


def _collect_recent_logs(n: int = 100) -> list[str]:
    """Read recent log lines from journalctl, HA Supervisor, or docker logs."""
    try:
        if os.path.exists("/etc/systemd/system/sendspin-client.service"):
            r = subprocess.run(
                ["journalctl", "-u", "sendspin-client", "-n", str(n), "--no-pager", "--output=short-iso"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.stdout.splitlines() or r.stderr.splitlines()
        if os.path.exists("/data/options.json"):
            import urllib.request as _ur

            token = os.environ.get("SUPERVISOR_TOKEN", "")
            if token:
                req = _ur.Request(
                    "http://supervisor/addons/self/logs",
                    headers={"Authorization": f"Bearer {token}", "Accept": "text/plain"},
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                return text.splitlines()[-n:]
            return []
        # In-process ring buffer — works inside containers without a
        # bind-mounted docker socket, which is the typical case.
        try:
            from sendspin_bridge.bridge.client import _ring_log_handler

            ring_lines = list(_ring_log_handler.records)[-n:]
            if ring_lines:
                return ring_lines
        except Exception:
            pass
        # Last-resort docker CLI for hosts that mount /var/run/docker.sock.
        r = subprocess.run(
            ["docker", "logs", "--tail", str(n), "sendspin-client"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout + r.stderr).splitlines()
    except Exception:
        logger.debug("Could not collect logs for bug report", exc_info=True)
        return []


def _collect_bt_device_info() -> list[dict]:
    """Run ``bluetoothctl info`` for every configured BT device."""
    results: list[dict] = []
    try:
        cfg = load_config()
    except Exception:
        return results
    devices = cfg.get("BLUETOOTH_DEVICES", [])
    if not isinstance(devices, list):
        return results
    for dev in devices:
        mac = dev.get("mac", "") if isinstance(dev, dict) else ""
        if not mac:
            continue
        entry: dict = {"mac": mac, "name": dev.get("name", "?")}
        try:
            r = subprocess.run(
                ["bluetoothctl"],
                input=f"info {mac}\n",
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [_ANSI_RE_STATUS.sub("", ln).strip() for ln in r.stdout.splitlines() if ln.strip()]
            for ln in lines:
                if ":" not in ln:
                    continue
                key, _, val = ln.partition(":")
                k = key.strip().lower().replace(" ", "_")
                if k in ("paired", "bonded", "trusted", "blocked", "connected", "class", "icon"):
                    entry[k] = val.strip()
        except Exception:
            logger.exception("Failed to get BT info for %s", mac)
            entry["error"] = "Failed to retrieve device info"
        results.append(entry)
    return results


def _bugreport_log_message(line: str) -> str:
    text = (line or "").strip()
    parts = text.split(" - ", 3)
    if len(parts) == 4:
        return parts[3].strip()
    return text


def _build_bugreport_suggested_description(masked: dict) -> str:
    """Generate a short editable issue description from masked diagnostics."""
    diag = masked.get("diagnostics", {})
    devices = diag.get("devices", [])
    subprocs = masked.get("subprocesses", [])
    ma_info = diag.get("ma_integration", {})
    recovery = diag.get("recovery_assistant", {})
    recent_issue_logs = masked.get("recent_issue_logs", [])
    recovery_timeline = recovery.get("timeline") or {}

    issues: list[str] = []

    def add_issue(text: str) -> None:
        text = (text or "").strip()
        if not text or text in issues:
            return
        issues.append(text)

    recent_messages: list[str] = []
    for line in recent_issue_logs[-2:]:
        message = _bugreport_log_message(line)
        if message and message not in recent_messages:
            recent_messages.append(message)
    if recent_messages:
        add_issue(f"Recent logs show: {'; '.join(recent_messages)}.")

    if devices:
        bt_total = len(devices)
        bt_connected = sum(1 for device in devices if device.get("connected"))
        if bt_connected < bt_total:
            add_issue(f"Bluetooth health is degraded: {bt_connected}/{bt_total} configured devices are connected.")
        errored_devices = [
            device.get("name") or device.get("mac") or "Unknown device"
            for device in devices
            if device.get("last_error")
        ]
        if errored_devices:
            add_issue(f"Devices reporting recent errors: {', '.join(errored_devices[:3])}.")

    if subprocs:
        alive_count = sum(1 for proc in subprocs if proc.get("alive"))
        if alive_count < len(subprocs):
            add_issue(f"Bridge subprocess health is degraded: {alive_count}/{len(subprocs)} device daemons are alive.")
        reconnecting = [proc.get("name") or "Unknown device" for proc in subprocs if proc.get("reconnecting")]
        if reconnecting:
            add_issue(f"Devices currently reconnecting: {', '.join(reconnecting[:3])}.")

    if not diag.get("dbus_available", True):
        add_issue("D-Bus is unavailable, so Bluetooth control may not be working correctly.")

    bluetooth_daemon = str(diag.get("bluetooth_daemon") or "").strip().lower()
    if bluetooth_daemon and bluetooth_daemon not in {"active", "unknown"}:
        add_issue(f"The bluetooth daemon reports status `{bluetooth_daemon}`.")

    if ma_info.get("configured") and not ma_info.get("connected"):
        add_issue("Music Assistant is configured but not currently connected.")

    recovery_issues = recovery.get("issues") or recovery.get("issue_groups") or []
    if recovery_issues:
        first_issue = recovery_issues[0] if isinstance(recovery_issues[0], dict) else {}
        title = first_issue.get("title") or first_issue.get("headline") or first_issue.get("summary")
        if title:
            add_issue(f"Recovery guidance highlights: {title}.")
    elif isinstance(recovery.get("summary"), dict):
        summary_headline = recovery["summary"].get("headline")
        if summary_headline:
            add_issue(f"Recovery guidance highlights: {summary_headline}.")

    timeline_excerpt = build_recovery_timeline_excerpt(recovery_timeline)
    if timeline_excerpt:
        add_issue(f"Recovery timeline shows: {timeline_excerpt}.")

    if not issues:
        issues.append("No obvious failures were extracted from the attached diagnostics yet.")

    lines = [
        "Auto-generated from the attached diagnostics. Please review and edit before submitting.",
        "",
        "### Diagnostics summary",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    lines.extend(
        [
            "",
            "### What I was doing",
            "",
            "### What I expected",
            "",
            "### What happened",
            "",
        ]
    )
    return "\n".join(lines)


@status_bp.route("/api/bugreport")
def api_bugreport():
    """Assemble a bug report: short summary for URL + full file for download."""
    try:
        # Collect all diagnostic data
        diag_resp = api_diagnostics()
        diag = diag_resp.get_json() if hasattr(diag_resp, "get_json") else {}

        env = _collect_environment()
        subprocs = _collect_subprocess_info()
        config_info = _sanitized_config()

        log_lines = _collect_recent_logs(100)
        bt_device_info = _collect_bt_device_info()
        last_run_summary = _collect_last_run_summary()

        # Detect runtime
        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        uptime_str = str(get_bridge_uptime())

        issue_summary = summarize_issue_logs(log_lines, max_lines=3)

        # Build structured report
        report = {
            "version": get_runtime_version(),
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "environment": env,
            "diagnostics": diag,
            "subprocesses": subprocs,
            "bt_device_info": bt_device_info,
            "sendspin_bridge.config": config_info,
            "recent_issue_logs": issue_summary["issue_lines"],
            "last_run": last_run_summary,
            "logs": log_lines,
        }

        # Mask all MAC/IP in the report
        masked = _mask_obj(report)

        # --- Short markdown (for URL ?body=, fits ~4 KB) ---
        env = masked["environment"]
        diag = masked.get("diagnostics", {})
        devices = diag.get("devices", [])
        subprocs = masked["subprocesses"]

        bt_total = len(devices)
        bt_conn = sum(1 for d in devices if d.get("connected"))
        ma_info = diag.get("ma_integration", {})
        ma_status = "connected" if ma_info.get("connected") else "disconnected"
        sinks = diag.get("sinks", [])
        sink_inputs = diag.get("sink_inputs", [])
        alive_count = sum(1 for sp in subprocs if sp.get("alive"))

        recent_issue_logs = masked.get("recent_issue_logs", [])

        ma_ver = ma_info.get("version") or "?"
        ma_label = f"connected (v{ma_ver})" if ma_info.get("connected") and ma_ver != "?" else ma_status

        short = [
            "## Bug Report",
            "",
            f"**Version:** {masked['version']} (built {masked['build_date']})",
            f"**Runtime:** {masked['runtime']}  |  **Uptime:** {masked['uptime']}",
            f"**Platform:** {env.get('platform', '?')}  |  **Arch:** {env.get('arch', '?')}",
            f"**BlueZ:** {env.get('bluez', '?')}  |  **Audio:** {env.get('audio_server', '?')}",
            f"**Python:** {env.get('python', '?').split()[0]}  |  **RSS:** {env.get('process_rss_mb', '?')} MB",
            f"**Deps:** sendspin {env.get('sendspin', '?')}  |  "
            f"aiosendspin {env.get('aiosendspin', '?')}  |  "
            f"av {env.get('av', '?')}",
            "",
            f"**BT:** {bt_conn}/{bt_total} connected  |  "
            f"**MA:** {ma_label}  |  "
            f"**Sinks:** {len(sinks)}  |  "
            f"**Streams:** {len(sink_inputs)}",
            f"**D-Bus:** {'✅' if diag.get('dbus_available') else '❌'}  |  "
            f"**bluetoothd:** {diag.get('bluetooth_daemon', '?')}  |  "
            f"**Subprocesses:** {alive_count}/{len(subprocs)} alive",
        ]
        if recent_issue_logs:
            short.append("")
            short.append("**Recent issue logs:**")
            short.append("```")
            short.extend(recent_issue_logs)
            short.append("```")
        short.append("")
        short.append("> 📎 **Full diagnostic report attached as file below**")

        markdown_short = "\n".join(short)

        # --- Full plain-text report (for downloadable file) ---
        text_full = _build_full_text_report(masked, title="BUG REPORT — FULL DIAGNOSTICS")
        suggested_description = _build_bugreport_suggested_description(masked)

        return jsonify(
            {
                "markdown_short": markdown_short,
                "text_full": text_full,
                "suggested_description": suggested_description,
                "report": masked,
            }
        )
    except Exception:
        logger.exception("Bug report assembly failed")
        return jsonify({"error": "Internal error"}), 500


def _build_full_text_report(
    masked: dict,
    *,
    title: str = "DIAGNOSTICS REPORT",
) -> str:
    """Build the full plain-text diagnostics report from masked data."""
    sep = "=" * 60
    full: list[str] = [
        sep,
        f"  {title}",
        sep,
        "",
        f"Version:  {masked.get('version', '?')} (built {masked.get('build_date', '?')})",
        f"Runtime:  {masked.get('runtime', '?')}  |  Uptime: {masked.get('uptime', '?')}",
        "",
    ]

    env = masked.get("environment", {})
    diag = masked.get("diagnostics", {})
    devices = diag.get("devices", [])
    subprocs = masked.get("subprocesses", [])
    ma_info = diag.get("ma_integration", {})
    sinks = diag.get("sinks", [])
    cards = diag.get("cards", [])
    assistant = diag.get("onboarding_assistant", {})
    recovery = diag.get("recovery_assistant", {})
    guidance = diag.get("operator_guidance", {})
    recovery_timeline = recovery.get("timeline") or {}
    last_run = masked.get("last_run") or {}

    # Last run summary — surfaces ungraceful exits from the previous run
    # via boot.json/exit.json breadcrumbs (see services.lifecycle.exit_breadcrumb).
    if last_run:
        full.append("--- LAST RUN SUMMARY ---")
        full.append(f"  {'Exit kind:':<20s} {last_run.get('exit_kind', '?')}")
        if last_run.get("bridge_version"):
            full.append(f"  {'Prev version:':<20s} {last_run['bridge_version']}")
        if last_run.get("started_at"):
            full.append(f"  {'Started at:':<20s} {last_run['started_at']}")
        if last_run.get("last_phase"):
            phase_status = last_run.get("last_phase_status") or "?"
            full.append(f"  {'Last phase:':<20s} {last_run['last_phase']} ({phase_status})")
        if last_run.get("last_message"):
            full.append(f"  {'Last message:':<20s} {last_run['last_message']}")
        if last_run.get("exit_code") is not None or last_run.get("exit_signal") is not None:
            full.append(
                f"  {'Exit code/signal:':<20s} "
                f"code={last_run.get('exit_code', '?')} signal={last_run.get('exit_signal', '?')}"
            )
        if last_run.get("exit_recorded_at"):
            full.append(f"  {'Exit recorded at:':<20s} {last_run['exit_recorded_at']}")
        notes = last_run.get("notes") or []
        for note in notes:
            full.append(f"  {'Note:':<20s} {note}")
        full.append("")

    # Environment
    if env:
        full.append("--- ENVIRONMENT ---")
        for k, v in env.items():
            full.append(f"  {k + ':':<20s} {v}")
        full.append("")

    # Devices
    if devices:
        full.append("--- DEVICES ---")
        full.append(f"  {'Name':<24s} {'MAC':<20s} {'BT':<6s} {'Sink':<36s} {'Enabled'}")
        for d in devices:
            bt = "Yes" if d.get("connected") else "No"
            sink = d.get("sink") or "—"
            enabled = "Yes" if d.get("enabled") else "No"
            full.append(f"  {d.get('name', '?'):<24s} {d.get('mac', '?'):<20s} {bt:<6s} {sink:<36s} {enabled}")
        full.append("")

    # Subprocesses
    if subprocs:
        full.append("--- SUBPROCESSES ---")
        full.append(
            f"  {'Name':<24s} {'PID':<8s} {'Alive':<8s} {'Running':<10s} {'Recon':<8s} {'Zombie':<8s} Last Error"
        )
        for sp in subprocs:
            pid = str(sp.get("pid") or "—")
            alive = "Yes" if sp.get("alive") else "No"
            running = "Yes" if sp.get("running") else "No"
            recon = str(sp.get("reconnect_attempt", 0) or "—")
            zombie = str(sp.get("zombie_restarts", 0))
            err = sp.get("last_error") or "—"
            full.append(
                f"  {sp.get('name', '?'):<24s} {pid:<8s} {alive:<8s} {running:<10s} {recon:<8s} {zombie:<8s} {err}"
            )
        full.append("")

    # MA integration
    if ma_info.get("configured"):
        full.append("--- MUSIC ASSISTANT ---")
        full.append(f"  URL:        {ma_info.get('url', '?')}")
        full.append(f"  Version:    {ma_info.get('version') or '?'}")
        full.append(f"  Connected:  {'Yes' if ma_info.get('connected') else 'No'}")
        groups = ma_info.get("syncgroups", [])
        for g in groups:
            full.append(f"  Group: {g.get('name', '?')}")
            np = g.get("now_playing", {})
            if np:
                full.append(
                    f"    Now playing: {np.get('artist', '?')} — {np.get('title', '?')} ({np.get('sendspin_bridge.bridge.state', '?')})"
                )
            for m in g.get("members", []):
                avail = "OK" if m.get("available") else "FAIL"
                vol = f" vol={m.get('volume')}" if m.get("volume") is not None else ""
                full.append(f"    {m.get('name', '?')}: {m.get('sendspin_bridge.bridge.state', '?')} [{avail}]{vol}")
        full.append("")

    if assistant:
        full.append("--- ONBOARDING ASSISTANT ---")
        for check in assistant.get("checks", []):
            status = str(check.get("status", "?")).upper()
            full.append(f"  [{status}] {check.get('key', '?')}: {check.get('summary', '')}")
        next_steps = assistant.get("next_steps", [])
        if next_steps:
            full.append("  Next steps:")
            for step in next_steps:
                full.append(f"    - {step}")
        full.append("")

    if recovery:
        full.append("--- RECOVERY ASSISTANT ---")
        summary = recovery.get("summary", {})
        full.append(
            "  "
            f"{summary.get('headline', 'Recovery summary')}: "
            f"{summary.get('summary', 'No recovery details available.')}"
        )
        for issue in recovery.get("issues", []):
            severity = str(issue.get("severity", "?")).upper()
            full.append(f"  [{severity}] {issue.get('title', '?')}: {issue.get('summary', '')}")
        for trace in recovery.get("traces", []):
            full.append(f"  Trace: {trace.get('label', '?')} — {trace.get('summary', '')}")
        latency = recovery.get("latency_assistant", {})
        if latency:
            full.append(f"  Latency: {latency.get('summary', '')}")
        full.append("")

    if recovery_timeline:
        full.append("--- RECOVERY TIMELINE ---")
        timeline_text = build_recovery_timeline_text(recovery_timeline, max_entries=8)
        for line in timeline_text.splitlines():
            full.append(f"  {line}" if line else "")
        full.append("")

    if guidance:
        full.append("--- OPERATOR GUIDANCE ---")
        full.append(f"  Mode: {guidance.get('mode', '?')}")
        banner = guidance.get("banner", {})
        if banner:
            full.append(f"  Banner: {banner.get('headline', '')} — {banner.get('summary', '')}")
        header = guidance.get("header_status", {})
        if header:
            full.append(f"  Header: {header.get('label', '')} — {header.get('summary', '')}")
        for issue in guidance.get("issue_groups", []):
            full.append(
                f"  [{str(issue.get('severity', '?')).upper()}] {issue.get('title', '?')}: {issue.get('summary', '')}"
            )
        full.append("")

    # Adapters
    adapters = diag.get("adapters", [])
    if adapters:
        full.append("--- BT ADAPTERS ---")
        for a in adapters:
            dflt = " (default)" if a.get("default") else ""
            full.append(f"  {a.get('id', '?')}  {a.get('mac', '?')}{dflt}")
        full.append("")

    # BT device info (bluetoothctl info per device)
    bt_devs = masked.get("bt_device_info", [])
    if bt_devs:
        full.append("--- BT DEVICE INFO (bluetoothctl) ---")
        for bd in bt_devs:
            full.append(f"  [{bd.get('name', '?')}]  MAC: {bd.get('mac', '?')}")
            for fld in ("paired", "trusted", "connected", "bonded", "blocked", "class", "icon"):
                if fld in bd:
                    full.append(f"    {fld:<12s}: {bd[fld]}")
            if bd.get("error"):
                full.append(f"    error: {bd['error']}")
        full.append("")

    # PA sinks
    if sinks:
        full.append("--- PA SINKS ---")
        for s in sinks:
            full.append(f"  {s}")
        full.append("")

    # PA cards (helpful for diagnosing BT profile issues — card present but no sink
    # usually means wrong active profile, e.g. headset_head_unit instead of a2dp_sink)
    if isinstance(cards, list) and cards:
        full.append("--- PA CARDS ---")
        for c in cards:
            name = c.get("name", "?")
            active = c.get("active_profile") or "—"
            profiles = ",".join(c.get("profiles", []) or []) or "—"
            full.append(f"  {name}")
            full.append(f"    active_profile: {active}")
            full.append(f"    profiles:       {profiles}")
        full.append("")

    # Service status
    full.append("--- SERVICE STATUS ---")
    full.append(f"  D-Bus:       {'OK' if diag.get('dbus_available') else 'FAIL'}")
    full.append(f"  bluetoothd:  {diag.get('bluetooth_daemon', '?')}")
    full.append(f"  PulseAudio:  {diag.get('pulseaudio', '?')}")
    full.append("")

    # Raw diagnostics JSON
    full.append(sep)
    full.append("  RAW DIAGNOSTICS JSON")
    full.append(sep)
    full.append(json.dumps(diag, indent=2, default=str))
    full.append("")

    # Config
    config = masked.get("sendspin_bridge.config")
    if config:
        full.append(sep)
        full.append("  CONFIG (sanitized)")
        full.append(sep)
        full.append(json.dumps(config, indent=2, default=str))
        full.append("")

    issue_logs = masked.get("recent_issue_logs", [])
    if issue_logs:
        full.append(sep)
        full.append("  RECENT ISSUE LOGS")
        full.append(sep)
        for line in issue_logs:
            full.append(str(line))
        full.append("")

    # Logs
    logs = masked.get("logs", [])
    if logs:
        full.append(sep)
        full.append(f"  RECENT LOGS (last {len(logs)} lines)")
        full.append(sep)
        for line in logs:
            full.append(str(line))

    return "\n".join(full)


@status_bp.route("/api/diagnostics/download")
def api_diagnostics_download():
    """Download full diagnostics as a plain-text file."""
    try:
        diag_resp = api_diagnostics()
        diag = diag_resp.get_json() if hasattr(diag_resp, "get_json") else {}

        config_info = _sanitized_config()
        log_lines = _collect_recent_logs(100)
        last_run_summary = _collect_last_run_summary()

        uptime_str = str(get_bridge_uptime())

        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        issue_summary = summarize_issue_logs(log_lines, max_lines=3)

        report = {
            "version": get_runtime_version(),
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "environment": diag.get("environment", {}),
            "diagnostics": diag,
            "subprocesses": diag.get("subprocesses", []),
            "sendspin_bridge.config": config_info,
            "recent_issue_logs": issue_summary["issue_lines"],
            "last_run": last_run_summary,
            "logs": log_lines,
        }

        masked = _mask_obj(report)
        text = _build_full_text_report(masked, title="DIAGNOSTICS REPORT")

        ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="diagnostics-{ts}.txt"'},
        )
    except Exception:
        logger.exception("Diagnostics download failed")
        return jsonify({"error": "Internal error"}), 500


@status_bp.route("/api/health")
def api_health():
    """Lightweight health check — no auth required, no sensitive data."""
    return jsonify({"ok": True})


@status_bp.route("/api/onboarding/assistant")
def api_onboarding_assistant():
    """Return actionable setup guidance derived from current runtime health."""
    return jsonify(_build_onboarding_assistant_payload())


@status_bp.route("/api/recovery/assistant")
def api_recovery_assistant():
    """Return recovery, trace, and latency guidance derived from runtime health."""
    return jsonify(_build_recovery_assistant_payload())


@status_bp.route("/api/recovery/timeline")
def api_recovery_timeline():
    """Return the structured chronological recovery timeline."""
    recovery = _build_recovery_assistant_payload()
    return jsonify(recovery.get("timeline") or {"summary": {"entry_count": 0}, "entries": []})


@status_bp.route("/api/recovery/timeline/download")
def api_recovery_timeline_download():
    """Download the current recovery timeline as CSV."""
    recovery = _build_recovery_assistant_payload()
    timeline = recovery.get("timeline") or {"entries": []}
    payload = build_recovery_timeline_csv(timeline)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    headers = {
        "Content-Disposition": f'attachment; filename="sendspin-recovery-timeline-{timestamp}.csv"',
        "Content-Type": "text/csv; charset=utf-8",
    }
    return Response(payload, headers=headers)


@status_bp.route("/api/operator/guidance")
def api_operator_guidance():
    """Return the unified operator guidance surface used by the dashboard header and banners."""
    return jsonify(_build_operator_guidance_payload())


@status_bp.route("/api/checks/rerun", methods=["POST"])
def api_rerun_safe_check():
    """Rerun one safe, non-destructive operator check."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    check_key = str(payload.get("check_key") or "").strip()
    if not check_key:
        return jsonify({"error": "check_key is required"}), 400
    device_names = payload.get("device_names")
    if device_names is not None and not isinstance(device_names, list):
        return jsonify({"error": "device_names must be an array"}), 400
    result = run_safe_check(check_key, device_names=device_names, config=load_config())
    http_status = 400 if result.get("summary") == "Unknown safe check requested." else 200
    return jsonify(result), http_status


@status_bp.route("/api/latency/recommendations")
def api_latency_recommendations():
    """Return the current latency assistant payload."""
    recovery = _build_recovery_assistant_payload()
    return jsonify(recovery.get("latency_assistant") or {})


@status_bp.route("/api/latency/apply", methods=["POST"])
def api_latency_apply():
    """Persist a recommended Pulse latency value."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    raw_value = payload.get("pulse_latency_msec")
    try:
        pulse_latency = int(raw_value)
    except (TypeError, ValueError):
        return jsonify({"error": "pulse_latency_msec must be an integer"}), 400
    if pulse_latency < 1 or pulse_latency > 5000:
        return jsonify({"error": "pulse_latency_msec must be between 1 and 5000"}), 400

    def _mutate(cfg: dict[str, Any]) -> None:
        cfg["PULSE_LATENCY_MSEC"] = pulse_latency

    update_config(_mutate)
    latency = _build_recovery_assistant_payload(config=load_config()).get("latency_assistant") or {}
    return jsonify(
        {
            "success": True,
            "pulse_latency_msec": pulse_latency,
            "restart_required": True,
            "summary": f"Saved Pulse latency {pulse_latency} ms. Restart the bridge to apply the new buffer.",
            "latency_assistant": latency,
        }
    )


@status_bp.route("/api/preflight")
def api_preflight():
    """Setup verification endpoint — no auth required, no sensitive data.

    Returns platform, audio, bluetooth, and D-Bus status for
    quick troubleshooting without exposing device details.
    """

    payload = _collect_preflight_status()
    payload["ok"] = True
    return jsonify(payload)


@status_bp.route("/api/bugreport/proxy-available")
def api_bugreport_proxy_available():
    """Check if the GitHub issue creation proxy is available."""
    from sendspin_bridge.services.diagnostics.github_issue_proxy import get_issue_proxy

    proxy = get_issue_proxy()
    return jsonify({"available": proxy.available})


@status_bp.route("/api/bugreport/submit", methods=["POST"])
def api_bugreport_submit():
    """Create a GitHub issue via the App proxy (for users without GitHub accounts)."""
    from sendspin_bridge.services.diagnostics.github_issue_proxy import get_issue_proxy

    proxy = get_issue_proxy()
    if not proxy.available:
        return jsonify({"success": False, "error": "Issue proxy not configured"}), 503

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    email = (data.get("email") or "").strip()
    diagnostics_text = (data.get("diagnostics_text") or "").strip()

    # Validation
    if not title or len(title) < 5:
        return jsonify({"success": False, "error": "Title must be at least 5 characters"}), 400
    if len(title) > 200:
        return jsonify({"success": False, "error": "Title must be less than 200 characters"}), 400
    if not description or len(description) < 10:
        return (
            jsonify({"success": False, "error": "Description must be at least 10 characters"}),
            400,
        )
    if len(description) > 5000:
        return (
            jsonify({"success": False, "error": "Description must be less than 5000 characters"}),
            400,
        )
    if not email or "@" not in email:
        return jsonify({"success": False, "error": "A valid email address is required"}), 400

    # Rate limit
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    rate_error = proxy.check_rate_limit(client_ip)
    if rate_error:
        return jsonify({"success": False, "error": rate_error}), 429

    # Build issue body
    body_parts = [
        "_Submitted via Sendspin bridge web UI (no GitHub account)._\n",
        f"**Contact:** {email}\n",
    ]

    body_parts.append(f"## Description\n\n{description}\n")

    if diagnostics_text:
        # Truncate diagnostics to fit GitHub's 65536 char limit
        max_diag = 60000 - len("\n".join(body_parts))
        if len(diagnostics_text) > max_diag:
            diagnostics_text = diagnostics_text[:max_diag] + "\n\n... (truncated)"
        body_parts.append(
            f"## Diagnostics\n\n<details><summary>Click to expand</summary>\n\n"
            f"```\n{diagnostics_text}\n```\n\n</details>\n"
        )

    body = "\n".join(body_parts)

    try:
        result = proxy.create_issue(
            title=title,
            body=body,
            labels=["submitted-via-bridge"],
        )
        return jsonify(
            {
                "success": True,
                "issue_url": result["html_url"],
                "issue_number": result["number"],
            }
        )
    except Exception:
        logger.exception("Failed to create GitHub issue via proxy")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Failed to create issue. Please try the Copy option instead.",
                }
            ),
            502,
        )
