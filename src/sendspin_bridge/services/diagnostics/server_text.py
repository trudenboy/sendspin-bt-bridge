"""Stable codes for the texts the bridge writes for people.

The bridge still sends its English wording (``title``, ``summary``, ``label``,
``message``) so scripts and old clients keep working, but every such text now
also carries a stable ``code`` and ``params``.  The web UI looks the code up in
its own language files and falls back to the English text for a code it does
not know.  Decision record: UI/UX overhaul Q2; the table of what is covered
lives in ``docs/ux-codes.md``.

Rules:

* A ``code`` names one condition.  Its wording can change; the code cannot.
* ``params`` hold the values the sentence interpolates (names, counts, ports),
  never prose.  Where a sentence has a cause that is itself prose, the cause is
  a code in the ``cause`` area, passed as ``<name>_code`` / ``<name>_codes``.
* ``summary_variant`` picks a different summary text for the same title (the
  summary key is ``summary_<variant>``).
* ``SERVER_TEXT`` below is the list of everything the UI must translate; the
  UI's language files are checked against it (``ui/tests/fixtures``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# area -> code -> leaves (the keys under ``server.<area>.<code>`` in the UI files)
SERVER_TEXT: dict[str, dict[str, tuple[str, ...]]] = {}


def _area(name: str, codes: dict[str, tuple[str, ...]]) -> None:
    SERVER_TEXT[name] = codes


_TS = ("title", "summary")
_TSE = ("title", "summary", "summary_error")  # a device card; the error variant shows the speaker's own error
_L = ("label",)

# --------------------------------------------------------------------- issues
# Guidance issue groups (the banner) and recovery issues (Diagnostics) share one
# area: the same condition has the same words in both.
_area(
    "issue",
    {
        # Host and setup (guidance groups; check-derived summaries are variants)
        "runtime_access": ("title", "summary", "summary_all_disabled"),
        "bluetooth_unavailable": (
            "title",
            "summary",
            "summary_daemon_down",
            "summary_all_disabled",
        ),
        "audio_unavailable": (
            "title",
            "summary",
            "summary_socket_refused",
            "summary_socket_unreachable",
            "summary_socket_missing",
            "summary_no_server_env",
            "summary_no_server",
            "summary_all_disabled",
        ),
        "pa_socket_refused": _TS,
        "pa_socket_missing": _TS,
        "config_migration_failed": _TS,
        "config_dir_not_writable": _TS,
        # Per speaker
        "missing_sink": _TSE,
        "transport_down": _TSE,
        "sendspin_url_malformed": _TS,
        "sendspin_daemon_loop_timeout": _TS,
        "never_paired": _TSE,
        "repair_required": _TSE,
        "disconnected": _TSE,
        "auto_released": _TSE,
        "auto_disabled_never_paired": _TSE,
        "samsung_cod_filter": ("title", "summary", "summary_no_adapter"),
        "sink_system_muted": _TSE,
        "sendspin_port_unreachable": ("title", "summary", "summary_error_only"),
        "needs_attention": (
            "title",
            "summary",
            "summary_error",
            "summary_stopping",
            "summary_reconnecting",
            "summary_resyncing",
            "summary_foreign_server",
            "summary_daemon_disconnected",
            "summary_sink_muted",
            "summary_playback_without_audio",
        ),
        "duplicate_device": _TS,
        "bt_sink_suspend_in_gaps": _TS,
        # Local audio outputs
        "output_missing": _TS,
        "output_muted": _TS,
        "output_volume_zero": _TS,
        "output_port_unavailable": _TS,
        # Experimental air sensor
        "air_sensor_unavailable": _TS,
        "air_sensor_codec": _TS,
        "air_sensor_codec_unsupported": _TS,
        "air_correction_inactive": _TS,
        # Music Assistant and latency (guidance groups)
        "ma_auth": (
            "title",
            "summary",
            "summary_no_url",
            "summary_no_credentials",
            "summary_not_connected",
        ),
        "latency": (
            "title",
            "summary",
            "summary_multi_no_delays",
            "summary_high_latency",
            "summary_repeated_resyncs",
        ),
        # Setup checklist steps (recovery issues); the summary variant is the check's wording
        "setup_step": _TS,
        "setup_runtime_access": ("title", "summary_no_dbus"),
        "setup_bluetooth": (
            "title",
            "summary_daemon_down",
            "summary_no_controller",
            "summary_no_paired",
            "summary_no_paired_disabled",
        ),
        "setup_bluetooth_pair": ("title", "summary_no_paired", "summary_no_paired_disabled"),
        "setup_audio": (
            "title",
            "summary_socket_refused",
            "summary_socket_unreachable",
            "summary_socket_missing",
            "summary_no_server_env",
            "summary_no_server",
            "summary_no_sinks",
        ),
        "setup_bridge_control": ("title", "summary_all_disabled", "summary_all_released_auto", "summary_all_released"),
        "setup_sink_verification": (
            "title",
            "summary_none_configured",
            "summary_missing_sink",
            "summary_none_connected_mixed",
            "summary_none_connected_bluetooth",
            "summary_none_connected_outputs",
        ),
        "setup_ma_auth": ("title", "summary_no_url", "summary_no_credentials", "summary_not_connected"),
        "setup_latency": ("title", "summary_multi_no_delays", "summary_high_latency"),
    },
)

# -------------------------------------------------------------- shared words
_area(
    "shared",
    {
        # Appended to a speaker's summary while a reconnect is in flight.
        "attempt": ("with_limit", "open"),
        # "A, B, and N more" for a long list of names.
        "list": ("more",),
        # Appended to an error that was cleared later.
        "recovered": ("text",),
    },
)

# ---------------------------------------------------------------------- causes
# Prose that explains *why* inside an issue (passed as ``<name>_code(s)``).
_area(
    "cause",
    {
        code: ("text",)
        for code in (
            # air sensor cannot run
            "no_cap_net_raw",
            "unsupported_platform",
            "monitor_error",
            "cpu_budget",
            "unsupported_input",
            "playout_fallback",
            "internal_error",
            # air correction not running
            "air_not_effective",
            "air_unavailable",
            "air_not_locked",
            # sink suspend
            "observed_suspend",
            "module_suspend_on_idle",
            "wireplumber_suspend_timeout",
            "unknown_suspend",
            # missing local output
            "sink_in_use",
            "sink_ambiguous",
            "sink_unplugged",
        )
    },
)

# --------------------------------------------------------------------- actions
# The code of an action is its key unless one key carries several labels.
_ACTION_CODES: dict[tuple[str, str], str] = {
    ("open_diagnostics", "Open diagnostics"): "open_diagnostics",
    ("open_diagnostics", "Open runtime diagnostics"): "open_runtime_diagnostics",
    ("open_diagnostics", "Open audio diagnostics"): "open_audio_diagnostics",
    ("open_diagnostics", "Open sink diagnostics"): "open_output_diagnostics",
    ("open_diagnostics", "Review diagnostics"): "review_diagnostics",
    ("open_bluetooth_settings", "Open adapter settings"): "open_adapter_settings",
    ("open_bluetooth_settings", "Open Bluetooth settings"): "open_bluetooth_settings",
    ("open_bt_settings", "Open Bluetooth settings"): "open_bluetooth_settings",
    ("open_devices_settings", "Open device settings"): "open_devices_settings",
    ("open_devices_settings", "Review buffer settings"): "review_buffer_settings",
    ("open_devices_settings", "Review latency settings"): "review_latency_settings",
    ("open_devices_settings", "Tune device delays"): "tune_latency_compensation",
    ("open_devices_settings", "Open player settings"): "open_player_settings",
    ("open_latency_settings", "Review latency settings"): "review_latency_settings",
    ("open_latency_settings", "Review Pulse latency"): "review_pulse_latency",
    ("open_latency_settings", "Prefer SBC codec"): "prefer_sbc_codec",
    ("open_ma_settings", "Open Music Assistant settings"): "open_ma_settings",
    ("open_config", "Open Music Assistant settings"): "open_ma_settings",
    ("open_config", "Edit Music Assistant server"): "edit_ma_server",
    ("open_config", "Open config"): "open_config",
    ("scan_devices", "Scan for speakers"): "scan_devices",
    ("scan_devices", "Open device scan"): "scan_devices",
    ("retry_ma_discovery", "Retry MA discovery"): "retry_ma_discovery",
    ("retry_ma_discovery", "Discover Music Assistant"): "retry_ma_discovery",
    ("refresh_diagnostics", "Rerun checks"): "rerun_checks",
    ("rerun_safe_check", "Rerun preflight"): "rerun_preflight",
    ("rerun_safe_check", "Recheck Bluetooth sinks"): "recheck_outputs",
    ("rerun_safe_check", "Recheck sinks"): "recheck_outputs",
    ("rerun_safe_check", "Revalidate MA link"): "revalidate_ma_link",
    ("reconnect_device", "Reconnect speaker"): "reconnect_device",
    ("reconnect_devices", "Reconnect {n} devices"): "reconnect_device",
    ("pair_device", "Start pairing"): "start_pairing",
    ("pair_device", "Re-pair speaker"): "repair_speaker",
    ("enable_device", "Re-enable"): "enable_device",
    ("enable_devices", "Re-enable {n} devices"): "enable_device",
    ("toggle_bt_management", "Release Bluetooth"): "release_bluetooth",
    ("toggle_bt_management", "Reclaim Bluetooth"): "reclaim_bluetooth",
    ("toggle_bt_management_devices", "Reclaim {n} devices"): "reclaim_bluetooth",
    ("unmute_sink", "Unmute speaker"): "unmute_speaker",
    ("test_sendspin_connection", "Test Music Assistant connection"): "test_ma_connection",
    ("apply_latency_recommended", "Apply {n} ms latency"): "apply_latency_recommended",
}
_area("action", {code: _L for code in sorted(set(_ACTION_CODES.values()))})

_DIGITS = re.compile(r"\d+")


def action_code(key: str, label: str) -> str:
    """The stable code of an action: its key, or the entry for this key and label."""
    normalized = _DIGITS.sub("{n}", label)
    return _ACTION_CODES.get((key, normalized)) or _ACTION_CODES.get((key, label)) or key


def action_fields(key: str, label: str, device_names: Iterable[str] = (), value: Any = None) -> dict[str, Any]:
    """``code`` and ``params`` of an action, for its dict."""
    names = [name for name in device_names if name]
    params: dict[str, Any] = {"count": len(names) or 1}
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        params["value"] = value
    return {"code": action_code(key, label), "params": params}


# ---------------------------------------------------------------------- header
_area(
    "header",
    {
        "startup": _L,
        "startup_finalizing": _L,
        "no_playable_speaker": _L,
        "all_disabled": _L,
        "first_run": _L,
        "add_first_speaker": _L,
        "issues_attention": _L,
        "all_handed_over": _L,
        "setup_progress": _L,
        "devices_ready": _L,
        "waiting_for_setup": _L,
    },
)

# ---------------------------------------------------------------------- events
# One entry per device event type and per bridge startup phase.  ``title`` is the
# short line, ``summary`` the sentence.
_EVENT_CODES = (
    "bluetooth-connected",
    "bluetooth-disconnected",
    "bluetooth-reconnected",
    "bluetooth-reconnect-failed",
    "daemon-connected",
    "daemon-disconnected",
    "playback-started",
    "playback-stopped",
    "audio-streaming",
    "audio-stream-stalled",
    "reconnecting",
    "reanchoring",
    "runtime-error",
    "runtime-error-cleared",
    "bt-management-disabled",
    "bt-management-reclaimed",
    "ma-monitor-stale",
    "bluetooth-standby-entered",
    "bluetooth-standby-exited",
    "sink-muted",
    "sink-unmuted",
    "sink-rematched",
    "sink-lost",
    "sink-restored",
    "ma-server-refused",
)
_STARTUP_PHASES = (
    "idle",
    "config",
    "runtime",
    "devices",
    "web",
    "integrations",
    "ready",
    "shutdown",
    "failed",
)
_HEALTH_CODES = (
    "released",
    "error",
    "stopping",
    "reconnecting",
    "resyncing",
    "output_missing",
    "bluetooth_disconnected",
    "foreign_server",
    "ma_reconnecting",
    "daemon_disconnected",
    "sink_muted",
    "playback_without_audio",
    "idle",
)
_area(
    "event",
    {
        **{code: _TS for code in _EVENT_CODES},
        **{f"startup-{phase}": _TS for phase in _STARTUP_PHASES},
        **{f"health-{code}": _TS for code in _HEALTH_CODES},
    },
)
_area("source", {"bridge_startup": ("name",)})

# --------------------------------------------------------------- blocked reasons
_area(
    "blocked",
    {
        code: ("message",)
        for code in (
            "released",
            "not_paired",
            "reconnecting",
            "ma_reconnecting",
            "stopping",
            "daemon_disconnected",
            "no_sink",
            "ma_disconnected",
            "ma_queue_unknown",
        )
    },
)

# ------------------------------------------------------------------ API problems
_area("problem", {})  # filled by ``PROBLEM_CODES`` below

#: Codes whose ``detail`` is a fixed sentence (no values in it).  The UI shows
#: its own wording for these; any other code shows the server's ``detail``.
PROBLEM_CODES: tuple[str, ...] = (
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "method_not_allowed",
    "conflict",
    "payload_too_large",
    "invalid_request",
    "too_many_requests",
    "internal_error",
    "upstream_error",
    "unavailable",
    "rate_limited",
    "session_expired",
    "invalid_credentials",
    "login_failed",
    "invalid_mac",
    "unknown_device",
    "not_connected",
    "no_bluetooth",
    "bluetooth_busy",
    "scan_in_progress",
    "pairing_failed",
    "pairing_disabled",
    "already_in_standby",
    "not_in_standby",
    "ma_not_configured",
    "ma_unavailable",
    "ma_unreachable",
    "ma_token_invalid",
    "job_in_progress",
    "unknown_job",
    "unknown_check",
    "not_ready",
    "timeout",
    "network_error",
    "http_error",
)
SERVER_TEXT["problem"] = {code: ("detail",) for code in PROBLEM_CODES}


def registry_keys() -> list[str]:
    """Every ``server.*`` key the UI files must define, sorted."""
    keys = [
        f"server.{area}.{code}.{leaf}"
        for area, codes in SERVER_TEXT.items()
        for code, leaves in codes.items()
        for leaf in leaves
    ]
    return sorted(keys)


def has_code(area: str, code: str) -> bool:
    return code in SERVER_TEXT.get(area, {})


def has_text(area: str, code: str, leaf: str) -> bool:
    return leaf in SERVER_TEXT.get(area, {}).get(code, ())


# ---------------------------------------------------------------------- builders
_SCALARS = (str, int, float, bool)


def _clean(value: Any) -> Any:
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if isinstance(item, _SCALARS)]
    return None


def coded(
    code: str,
    params: dict[str, Any] | None = None,
    *,
    variant: str | None = None,
) -> dict[str, Any]:
    """The ``code`` / ``params`` (and ``summary_variant``) fields of one text."""
    clean = {key: _clean(val) for key, val in (params or {}).items() if val is not None}
    payload: dict[str, Any] = {"code": code, "params": {k: v for k, v in clean.items() if v is not None}}
    if variant:
        payload["summary_variant"] = variant
    return payload


def device_params(names: Iterable[str], **extra: Any) -> dict[str, Any]:
    """The params every per-speaker text shares: how many, who, and the list."""
    listed = [name for name in names if name]
    params: dict[str, Any] = {
        "count": max(1, len(listed)),
        "device": listed[0] if listed else "",
        "device_names": listed,
    }
    params.update(extra)
    return params


def event_fields(event: dict[str, Any]) -> dict[str, Any]:
    """``code`` and ``params`` of a device event, read from the event the bridge already stores.

    The code is the event type; the values are its details, plus the error text
    for a runtime error (its message *is* the error).
    """
    event_type = str(event.get("event_type") or "event")
    params = dict(event.get("details") or {})
    if event_type == "runtime-error":
        params["error"] = str(event.get("message") or "")
    if event.get("recovered"):
        params["recovered"] = True
    return coded(event_type, params)
