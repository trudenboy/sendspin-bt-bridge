"""Status, diagnostics, guidance and bug-report use cases."""

from __future__ import annotations

import json
import logging
import os
import platform as _platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sendspin_bridge.application.errors import UseCaseError, config_write_error
from sendspin_bridge.bluetooth.bluez import Outcome, get_bluez
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
from sendspin_bridge.security.redaction import redact
from sendspin_bridge.services.audio.pulse import get_audio_server_snapshot, get_server_name, list_cards, list_sinks
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.diagnostics.bugreport_classifier import classify_likely_causes
from sendspin_bridge.services.diagnostics.event_hooks import get_event_hook_registry
from sendspin_bridge.services.diagnostics.log_analysis import summarize_issue_logs
from sendspin_bridge.services.diagnostics.onboarding_assistant import build_onboarding_assistant_snapshot
from sendspin_bridge.services.diagnostics.operator_check_runner import run_safe_check
from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot
from sendspin_bridge.services.diagnostics.preflight_status import add_host_change_listener
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
from sendspin_bridge.services.diagnostics.status_derivation import StatusDerivation
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION
from sendspin_bridge.services.lifecycle.bridge_runtime_state import (
    get_bridge_uptime,
    get_bridge_uptime_seconds,
    get_bridge_uptime_text,
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

VERSION = _CONFIG_VERSION


def latency_history(player_id: str) -> dict:
    """The current process' bounded timing history for one player."""
    clients = get_device_registry_snapshot().active_clients
    client = next((item for item in clients if str(getattr(item, "player_id", "")) == player_id), None)
    if client is None:
        raise UseCaseError(404, "unknown_device", f"Unknown device: {player_id}")
    getter = getattr(client, "timing_history_snapshot", None)
    samples = getter() if callable(getter) else []
    return {"player_id": player_id, "retention": "memory", "samples": samples}


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
    """Measure the host now — two bluetoothctl calls and the rest.

    Called directly where somebody is looking precisely because something may
    have just changed: diagnostics, the setup verification endpoint, the
    operator checks.  The status path reads a sample of it instead — see
    ``_sampled_preflight_status``.
    """
    # One PulseAudio session for both answers: each session is a new client.
    snapshot: list[tuple[str, list[dict]]] = []

    def _audio() -> tuple[str, list[dict]]:
        if not snapshot:
            snapshot.append(get_audio_server_snapshot())
        return snapshot[0]

    return _shared_collect_preflight_status(
        get_server_name_fn=lambda: _audio()[0],
        list_sinks_fn=lambda: _audio()[1],
        runtime_version_fn=get_runtime_version,
        machine_fn=_platform.machine,
        exists_fn=os.path.exists,
        open_fn=open,
    )


#: The probe is the expensive half of a status build (~56 ms of ~62 ms,
#: measured): three bluetoothctl runs and a PulseAudio session.  The host it
#: describes changes on the order of minutes, and the moments it does — a
#: speaker connecting or leaving, a saved config — invalidate the sample.  At
#: the old 2 s cadence an open dashboard made it most of the bridge's idle work.
PREFLIGHT_PROBE_INTERVAL_S = 30.0

_preflight_probe: StatusDerivation[dict] = StatusDerivation(
    # Late-bound on purpose: the measurement is one function, and everything
    # that reaches for it — including tests — reaches for the same one.
    lambda: _collect_preflight_status(),
    min_interval_s=PREFLIGHT_PROBE_INTERVAL_S,
    label="preflight probe",
)
add_host_change_listener(_preflight_probe.invalidate)


def invalidate_preflight_probe() -> None:
    """Make the next status build re-measure the host.

    For the moments an operator acts on the host itself — a saved config, an
    adapter powered back on — and expects the next screen to be true.
    """
    _preflight_probe.invalidate()


def reset_preflight_probe() -> None:
    """Drop the sampled probe entirely (tests, and a fresh runtime)."""
    _preflight_probe.reset()


def _sampled_preflight_status() -> dict:
    """The host's state as of the last measurement, taken at a bounded rate.

    A status tick is driven by runtime state changing, which happens far
    faster than anything this describes.
    """
    return _preflight_probe.current()


def _collection_error_payload(exc: Exception) -> dict[str, str]:
    """Return a structured diagnostics error payload."""
    return _shared_collection_error_payload(exc)


def _collection_status_payload(status: str, *, count: int | None = None, error: dict[str, str] | None = None) -> dict:
    """Build a compact diagnostics collection status payload."""
    return _shared_collection_status_payload(status, count=count, error=error)


def _collect_bluetooth_daemon_status() -> str:
    if get_bluez().list_adapters():
        return "active"
    try:
        r2 = subprocess.run(
            ["systemctl", "is-active", "bluetooth"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        # Non-systemd hosts (LXC, alpine, WSL) — same contract as the
        # preflight ``_default_daemon_state``: never false-flag, never crash.
        return "unknown"
    return r2.stdout.strip() or "inactive"


def _collect_adapter_diagnostics() -> list[dict]:
    return [
        {
            "id": f"hci{i}",
            "mac": ref.mac,
            "default": ref.is_default,
        }
        for i, ref in enumerate(get_bluez().list_adapters())
    ]


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
    try:
        devices = query_audio_devices()
    except Exception:
        return []
    return [{"index": d.index, "name": d.name, "is_default": d.is_default} for d in devices if d.output_channels > 0]


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
        preflight = _sampled_preflight_status()
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
    # A caller that names no state model would otherwise take the branch that
    # rebuilds its answers from the raw extras and disagrees with `/api/status`
    # about the same device.  Build the state here rather than asking every
    # recovery, timeline and latency route to remember.
    if bridge_state is None:
        bridge_state = build_bridge_state_model(
            config=config,
            preflight=preflight,
            devices=devices,
            ma_connected=is_ma_connected(),
            runtime_mode=build_mock_runtime_snapshot().mode,
            startup_progress=startup_progress,
        )
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
    preflight = _sampled_preflight_status()
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


def status_payload() -> dict:
    """The full legacy-shaped status (first device flattened onto the top level).

    Kept for the HA compat router; API v1 serves ``bridge`` and ``devices`` instead.
    """
    return _build_status_payload()


def groups_summary() -> list[dict]:
    """Music Assistant player groups with their members; solo players are their own group."""
    return _build_groups_summary(get_device_registry_snapshot().active_clients)


def startup_progress() -> dict:
    return build_startup_progress_snapshot().to_dict()


def runtime_info() -> dict:
    """Runtime mode and mock-runtime explainability metadata."""
    return build_mock_runtime_snapshot().to_dict()


def bridge_telemetry() -> dict:
    """Resource telemetry and runtime-scoped hook activity."""
    return _build_bridge_telemetry_payload()


def hooks_snapshot() -> dict:
    """Registered runtime webhooks and recent delivery results."""
    return get_event_hook_registry().snapshot()


def register_hook(
    *, url: str, categories: list[str] | None, event_types: list[str] | None, timeout_sec: float = 5.0
) -> dict:
    """Register a runtime-scoped webhook for bridge or device events."""
    try:
        return get_event_hook_registry().register(
            url=url, categories=categories, event_types=event_types, timeout_sec=timeout_sec
        )
    except ValueError as exc:
        raise UseCaseError(400, "invalid_hook", str(exc)) from exc


def unregister_hook(hook_id: str) -> None:
    if not get_event_hook_registry().unregister(hook_id):
        raise UseCaseError(404, "unknown_hook", "Hook not found")


def diagnostics(*, auth_enabled: bool = False) -> dict:
    """Structured health diagnostics. Never raises: a failed collection is reported in the payload."""
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
            "auth_enabled": auth_enabled,
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
            diag["sendspin_connection"] = _collect_sendspin_connection_info(config=load_config())
            _record_success("sendspin_connection")
        except Exception as exc:
            diag["sendspin_connection"] = _record_failure(
                "sendspin_connection",
                exc,
                fallback={"error": "Failed to collect Sendspin connection info"},
                log_message="Failed to collect Sendspin connection info for diagnostics",
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
        # The normalised device state, built once and shared with everything
        # below.  `/api/status` has always passed it; this endpoint did not,
        # so the same speaker produced different issues, traces and timeline
        # entries depending on which page the operator opened — and the
        # bundle attached to a bug report was the one built the other way.
        diagnostics_config = load_config()
        diagnostics_devices = [device for _client, device in snapshot_pairs]
        # Measured, not guessed: the bundle has no "preflight" key, so this
        # used to hand the state model an empty one.  An empty preflight reads
        # as "D-Bus unavailable", and the recovery card in the bundle then
        # reported a runtime that cannot reach D-Bus on a host that was
        # paired, connected and playing.
        # A bug report is taken because something looks wrong, so this one
        # measures the host rather than reading the sampled value.
        diagnostics_preflight = _collect_preflight_status()
        try:
            diagnostics_state = build_bridge_state_model(
                config=diagnostics_config,
                devices=diagnostics_devices,
                runtime_mode=diag["runtime_info"].get("mode", "unknown"),
                ma_connected=is_ma_connected(),
                preflight=diagnostics_preflight,
            )
        except Exception:
            logger.warning("Could not build the state model for diagnostics", exc_info=True)
            diagnostics_state = None

        try:
            onboarding_assistant = _build_onboarding_assistant_payload(
                preflight=diagnostics_preflight,
                config=diagnostics_config,
                devices=diagnostics_devices,
                runtime_mode=diag["runtime_info"].get("mode", "unknown"),
                ma_connected=is_ma_connected(),
                bridge_state=diagnostics_state,
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
                preflight=diagnostics_preflight,
                config=diagnostics_config,
                devices=diagnostics_devices,
                onboarding_assistant=onboarding_assistant,
                startup_progress=diag["startup_progress"],
                bridge_state=diagnostics_state,
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
        # ``_collect_last_run_summary`` handles its own errors and returns
        # ``None`` on failure — no extra guard needed here.
        diag["last_run"] = _collect_last_run_summary()

        diag["status"] = "degraded" if failed_collections else "ok"
        diag["failed_collections"] = failed_collections
        diag["collections_status"] = collections_status
        return diag
    except Exception:
        logger.exception("Diagnostics collection failed")
        return {
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


# ---------------------------------------------------------------------------
# (Logs endpoint lives in api_config.py — reads journalctl / supervisor / docker)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/bugreport — assembled bug report with masked sensitive data
# ---------------------------------------------------------------------------


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
    """Mask addresses and credentials in arbitrary text.

    Addresses are masked because a bug report should not carry someone's
    network layout; credentials because the log ring this draws from carries
    whatever the code logged, and a token that reached a log line would
    otherwise travel intact into an uploaded bundle.
    """
    text = redact(text)
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
    env["bluez"] = get_bluez().version() or "unknown"

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
            "restart_delay": getattr(client, "restart_delay", 1.0),
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


def _collect_sendspin_connection_info(config: dict | None = None) -> dict:
    """Gather everything needed to fill the diagnostics report's SENDSPIN CONNECTION block.

    Added in the issue #291 follow-up so the daemon-exit-at-10s class of bug
    surfaces in the diagnostics bundle directly — resolved target URL, last
    reachability probe, recent spawn cycles with timing/code/unexpected
    flags, and the recurring-interval fingerprint if it tripped.
    """
    from sendspin_bridge.config import load_config
    from sendspin_bridge.services.diagnostics.operator_check_runner import run_safe_check
    from sendspin_bridge.services.diagnostics.sendspin_port_probe import DEFAULT_PORT
    from sendspin_bridge.services.infrastructure.config_validation import resolve_sendspin_url

    cfg = config if config is not None else load_config()
    server = str(cfg.get("SENDSPIN_SERVER") or "").strip()
    try:
        port = int(cfg.get("SENDSPIN_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    auto_mode = (not server) or server.lower() in ("auto", "discover")

    devices: list[dict] = []
    for client in get_device_registry_snapshot().active_clients:
        recent: list[dict] = []
        try:
            recent_fn = getattr(client, "recent_spawn_records", None)
            if callable(recent_fn):
                recent = recent_fn(5)
        except Exception:
            recent = []
        status = getattr(client, "status", None)
        recurring = status.get("daemon_recurring_lifetime_s") if status else None
        devices.append(
            {
                "player_name": getattr(client, "player_name", "?"),
                "server_connected": bool(status.get("server_connected")) if status else False,
                "connected_server_url": (status.get("connected_server_url") if status else None) or None,
                "daemon_recurring_lifetime_s": recurring,
                "recent_spawns": recent,
            }
        )

    # The reachability probe makes an outbound TCP connect — run it best-effort
    # so a hung probe never breaks diagnostic bundle generation.
    try:
        reachability = run_safe_check("sendspin_connection", config=cfg)
    except Exception as exc:
        reachability = {"status": "error", "summary": f"Reachability probe failed: {exc}"}

    return {
        "server": server or None,
        "port": port,
        "auto_discovery": auto_mode,
        "resolved_url": resolve_sendspin_url(server, port),
        "reachability": reachability,
        "devices": devices,
    }


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
        info = get_bluez().device_info(mac)
        if info.outcome is not Outcome.OK:
            logger.warning("Failed to get BT info for %s: outcome=%s", mac, info.outcome.value)
            entry["error"] = "Failed to retrieve device info"
        else:
            for k in ("paired", "bonded", "trusted", "blocked", "connected", "class", "icon"):
                if k in info.fields:
                    entry[k] = info.fields[k]
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


def bug_report(*, auth_enabled: bool = False) -> dict:
    """Assemble a bug report: short summary for a URL, full text for a file, likely causes."""
    try:
        diag = diagnostics(auth_enabled=auth_enabled)

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
            "config": config_info,
            "recent_issue_logs": issue_summary["issue_lines"],
            "last_run": last_run_summary,
            "logs": log_lines,
        }

        # Mask all MAC/IP in the report
        masked: dict[str, Any] = _mask_obj(report)  # type: ignore[assignment]

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

        # v2.70.0-rc.2 (#262) — pre-submit classifier. The UI renders these
        # likely_causes above the form so operators self-serve before
        # submitting a ticket. Empty list when no rule matches — the form
        # is then shown unguarded.
        likely_causes = classify_likely_causes(
            recovery_snapshot=diag.get("recovery_assistant", {}),
            log_summary=issue_summary,
            diagnostics=diag,
        )

        return {
            "markdown_short": markdown_short,
            "text_full": text_full,
            "suggested_description": suggested_description,
            "likely_causes": likely_causes,
            "report": masked,
        }
    except Exception as exc:
        logger.exception("Bug report assembly failed")
        raise UseCaseError(500, "bug_report_failed", "Bug report assembly failed") from exc


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

    # Sendspin connection (issue #291 follow-up) — resolved target URL,
    # reachability probe, recent daemon-spawn lifetimes per device.
    sendspin_conn = diag.get("sendspin_connection") or {}
    if sendspin_conn and "error" not in sendspin_conn:
        full.append("--- SENDSPIN CONNECTION ---")
        server_label = sendspin_conn.get("server") or "auto"
        full.append(f"  {'SENDSPIN_SERVER:':<22s} {server_label}")
        full.append(f"  {'SENDSPIN_PORT:':<22s} {sendspin_conn.get('port', '?')}")
        if sendspin_conn.get("auto_discovery"):
            full.append(f"  {'Auto-discovery:':<22s} yes (target resolved at spawn time via mDNS)")
        if sendspin_conn.get("resolved_url"):
            full.append(f"  {'Resolved URL:':<22s} {sendspin_conn['resolved_url']}")
        reachability = sendspin_conn.get("reachability") or {}
        if reachability:
            full.append(
                f"  {'Reachability:':<22s} "
                f"{str(reachability.get('status', '?')).upper()} — {reachability.get('summary', '')}"
            )
        for dev in sendspin_conn.get("devices", []):
            name = dev.get("player_name", "?")
            full.append(f"  [{name}]")
            full.append(f"    server_connected:  {'Yes' if dev.get('server_connected') else 'No'}")
            if dev.get("connected_server_url"):
                full.append(f"    connected_url:     {dev['connected_server_url']}")
            recurring = dev.get("daemon_recurring_lifetime_s")
            if isinstance(recurring, (int, float)):
                full.append(
                    f"    pattern:           daemon dies every ~{float(recurring):.1f}s "
                    f"(connection timeout fingerprint)"
                )
            spawns = dev.get("recent_spawns") or []
            if spawns:
                full.append("    recent spawns (oldest first):")
                for record in spawns:
                    pid_s = record.get("pid", "?")
                    spawn_at = (record.get("spawn_at") or "?").replace("T", " ").split("+")[0]
                    exit_at_raw = record.get("exit_at")
                    if exit_at_raw:
                        exit_at = exit_at_raw.replace("T", " ").split("+")[0]
                        lifetime = record.get("lifetime_s")
                        lifetime_s = f"{float(lifetime):.1f}s" if isinstance(lifetime, (int, float)) else "?"
                        code = record.get("exit_code")
                        sig = record.get("signal")
                        kind = "unexpected" if record.get("unexpected", True) else "expected"
                        full.append(
                            f"      PID {pid_s}: {spawn_at} → {exit_at}  "
                            f"({lifetime_s}, code={code}, signal={sig}, {kind})"
                        )
                    else:
                        full.append(f"      PID {pid_s}: {spawn_at} → alive")
                # Stderr tail from the most recent record so operators see the
                # daemon's last words even when no IPC error envelope arrived.
                latest_tail = list((spawns[-1].get("stderr_tail") or [])[-5:])
                if latest_tail:
                    full.append("    recent stderr tail:")
                    for line in latest_tail:
                        full.append(f"      {line}")
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

    # Logs before the raw JSON and the config: a report cut to the issue
    # size limit loses its tail, and the logs are what a report most needs.
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
        full.append("")

    # Raw diagnostics JSON
    full.append(sep)
    full.append("  RAW DIAGNOSTICS JSON")
    full.append(sep)
    full.append(json.dumps(diag, indent=2, default=str))
    full.append("")

    # Config
    config = masked.get("config")
    if config:
        full.append(sep)
        full.append("  CONFIG (sanitized)")
        full.append(sep)
        full.append(json.dumps(config, indent=2, default=str))
        full.append("")

    return "\n".join(full)


def _runtime_kind() -> str:
    if os.path.exists("/data/options.json"):
        return "ha_addon"
    if os.path.exists("/.dockerenv"):
        return "docker"
    if os.path.exists("/etc/systemd/system/sendspin-client.service"):
        return "systemd"
    return "unknown"


def diagnostics_text_report(*, auth_enabled: bool) -> tuple[str, str]:
    """The full plain-text diagnostics report and its download file name."""
    diag = diagnostics(auth_enabled=auth_enabled)
    log_lines = _collect_recent_logs(100)
    issue_summary = summarize_issue_logs(log_lines, max_lines=3)
    report = {
        "version": get_runtime_version(),
        "build_date": BUILD_DATE,
        "runtime": _runtime_kind(),
        "uptime": str(get_bridge_uptime()),
        "environment": diag.get("environment", {}),
        "diagnostics": diag,
        "subprocesses": diag.get("subprocesses", []),
        "config": _sanitized_config(),
        "recent_issue_logs": issue_summary["issue_lines"],
        "last_run": _collect_last_run_summary(),
        "logs": log_lines,
    }
    text = _build_full_text_report(_mask_obj(report), title="DIAGNOSTICS REPORT")  # type: ignore[arg-type]
    ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return text, f"diagnostics-{ts}.txt"


def onboarding_assistant() -> dict:
    """Actionable setup guidance derived from current runtime health."""
    return _build_onboarding_assistant_payload()


def recovery_assistant() -> dict:
    """Recovery, trace and latency guidance derived from runtime health."""
    return _build_recovery_assistant_payload()


def recovery_timeline() -> dict:
    """The structured chronological recovery timeline."""
    return _build_recovery_assistant_payload().get("timeline") or {"summary": {"entry_count": 0}, "entries": []}


def recovery_timeline_csv() -> tuple[str, str]:
    """The recovery timeline as CSV, and its download file name."""
    timeline = _build_recovery_assistant_payload().get("timeline") or {"entries": []}
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return build_recovery_timeline_csv(timeline), f"sendspin-recovery-timeline-{timestamp}.csv"


def operator_guidance() -> dict:
    """The unified operator guidance shown in the dashboard header and banners."""
    return _build_operator_guidance_payload()


def rerun_check(check_key: str, device_names: list[str] | None = None) -> dict:
    """Rerun one safe, non-destructive operator check."""
    check_key = (check_key or "").strip()
    if not check_key:
        raise UseCaseError(400, "check_key_required", "check_key is required")
    result = run_safe_check(check_key, device_names=device_names, config=load_config())
    if result.get("summary") == "Unknown safe check requested.":
        raise UseCaseError(404, "unknown_check", f"Unknown safe check: {check_key}")
    return result


def latency_recommendations() -> dict:
    """The latency assistant: recommended PulseAudio latency and why."""
    return _build_recovery_assistant_payload().get("latency_assistant") or {}


def apply_latency_recommendation(pulse_latency_msec: int) -> dict:
    """Persist a PulseAudio latency; it takes effect after a restart."""
    if pulse_latency_msec < 1 or pulse_latency_msec > 5000:
        raise UseCaseError(400, "out_of_range", "pulse_latency_msec must be between 1 and 5000")

    def _mutate(cfg: dict[str, Any]) -> None:
        cfg["PULSE_LATENCY_MSEC"] = pulse_latency_msec

    try:
        update_config(_mutate)
    except OSError as exc:
        raise config_write_error(exc, context="Cannot save latency") from exc
    latency = _build_recovery_assistant_payload(config=load_config()).get("latency_assistant") or {}
    return {
        "pulse_latency_msec": pulse_latency_msec,
        "restart_required": True,
        "summary": f"Saved Pulse latency {pulse_latency_msec} ms. Restart the bridge to apply the new buffer.",
        "latency_assistant": latency,
    }


def preflight() -> dict:
    """Setup verification — measured now, because the operator just changed something."""
    payload = _collect_preflight_status()
    payload["ok"] = True
    return payload


def bug_report_proxy_available() -> bool:
    from sendspin_bridge.services.diagnostics.github_issue_proxy import get_issue_proxy

    return bool(get_issue_proxy().available)


def submit_bug_report(*, title: str, description: str, email: str, diagnostics_text: str, client_ip: str) -> dict:
    """Open a GitHub issue through the App proxy (no GitHub account needed)."""
    from sendspin_bridge.services.diagnostics.github_issue_proxy import get_issue_proxy

    proxy = get_issue_proxy()
    if not proxy.available:
        raise UseCaseError(503, "proxy_unavailable", "Issue proxy not configured")
    title = (title or "").strip()
    description = (description or "").strip()
    email = (email or "").strip()
    diagnostics_text = (diagnostics_text or "").strip()
    if len(title) < 5 or len(title) > 200:
        raise UseCaseError(400, "invalid_title", "Title must be 5 to 200 characters")
    if len(description) < 10 or len(description) > 5000:
        raise UseCaseError(400, "invalid_description", "Description must be 10 to 5000 characters")
    if "@" not in email:
        raise UseCaseError(400, "invalid_email", "A valid email address is required")
    rate_error = proxy.check_rate_limit(client_ip)
    if rate_error:
        raise UseCaseError(429, "rate_limited", rate_error)

    body_parts = [
        "_Submitted via Sendspin bridge web UI (no GitHub account)._\n",
        f"**Contact:** {email}\n",
        f"## Description\n\n{description}\n",
    ]
    if diagnostics_text:
        # GitHub caps an issue body at 65 536 characters.
        max_diag = 60000 - len("\n".join(body_parts))
        if len(diagnostics_text) > max_diag:
            diagnostics_text = diagnostics_text[:max_diag] + "\n\n... (truncated)"
        body_parts.append(
            f"## Diagnostics\n\n<details><summary>Click to expand</summary>\n\n"
            f"```\n{diagnostics_text}\n```\n\n</details>\n"
        )
    try:
        result = proxy.create_issue(title=title, body="\n".join(body_parts), labels=["submitted-via-bridge"])
    except Exception as exc:
        logger.exception("Failed to create GitHub issue via proxy")
        raise UseCaseError(
            502, "issue_creation_failed", "Failed to create issue. Please try the Copy option instead."
        ) from exc
    return {"issue_url": result["html_url"], "issue_number": result["number"]}
