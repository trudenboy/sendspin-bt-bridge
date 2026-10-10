"""A bridge speaker, as API v1 shows it.

Built from the runtime's ``DeviceSnapshot`` (the flat status dict the legacy
API served), regrouped by concern. ``from_status`` is the only place that knows
the flat field names.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from sendspin_bridge.application.models.base import ResponseModel

IdleMode = Literal["default", "power_save", "auto_disconnect", "keep_alive"]


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class AdapterRef(ResponseModel):
    mac: str | None = None
    hci: str = ""
    name: str | None = None


class PairFailure(ResponseModel):
    kind: str
    adapter_mac: str | None = None
    at: str | None = None


class AdapterRecovery(ResponseModel):
    stage: str = "idle"
    last_at: str | None = None
    adapter: str | None = None
    result: str | None = None
    failure_reason: str | None = None


class DeviceBluetooth(ResponseModel):
    mac: str | None = None
    adapter: AdapterRef
    available: bool = False
    connected: bool = False
    connected_at: str | None = None
    paired: bool | None = None
    management_enabled: bool = True
    released_by: str | None = Field(default=None, description="'user' or 'auto' when released.")
    reconnecting: bool = False
    reconnect_attempt: int = 0
    reconnect_attempts_remaining: int | None = None
    standby: bool = False
    standby_since: str | None = None
    waking: bool = False
    power_save: bool = False
    rssi_dbm: int | None = None
    battery_level: int | None = None
    codec_id: int | None = None
    codec_name: str | None = None
    transport_state: str | None = None
    reported_delay_ms: float | None = None
    delay_reporting_supported: bool = False
    never_paired: bool = False
    never_paired_since: str | None = None
    pair_failure: PairFailure | None = None
    adapter_recovery: AdapterRecovery


class DeviceAudio(ResponseModel):
    sink_name: str | None = None
    has_sink: bool = False
    volume: int = 100
    muted: bool = False
    sink_muted: bool = False
    format: str | None = None
    streaming: bool = False
    buffering: bool = False
    idle_mode: IdleMode | str = "default"
    static_delay_ms: float = 0.0
    static_delay_source: str = "default"
    static_delay_calibrated_at: str | None = None
    static_delay_codec: str | None = None


class LatencySuggestion(ResponseModel):
    suggested_static_delay_ms: int | None = None
    source: str = "unavailable"
    confidence: str = "none"
    explanation: str = ""
    revision: str | None = None
    double_count_risk: bool = False


class DeviceTiming(ResponseModel):
    available: bool = False
    sampled_at: str | None = None
    backend_output_latency_ms: float | None = None
    buffered_audio_ms: float | None = None
    playback_position_us: int | None = None
    sync_error_ms: float | None = None
    clock_synchronized: bool = False
    clock_offset_ms: float | None = None
    clock_uncertainty_ms: float | None = None
    required_lead_time_ms: int = 250
    min_buffer_ms: int = 250
    reanchoring: bool = False
    reanchor_count: int = 0
    reanchor_count_session: int = 0
    reanchor_count_5m: int | None = None
    reanchor_count_30m: int | None = None
    last_sync_error_ms: float | None = None
    last_reanchor_at: float | None = None
    calibration_metronome_active: bool = False
    latency_suggestion: LatencySuggestion


class Track(ResponseModel):
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    artwork_url: str | None = None
    year: int | None = None
    number: int | None = None
    progress_ms: int | None = None
    duration_ms: int | None = None


class DeviceGroupRef(ResponseModel):
    id: str | None = None
    name: str | None = None
    volume: int | None = None
    muted: bool | None = None


class DevicePlayback(ResponseModel):
    playing: bool = False
    connected: bool = False
    server_connected: bool = False
    server_connected_at: str | None = None
    server_url: str | None = None
    track: Track
    playback_speed: int | None = None
    shuffle: bool | None = None
    repeat_mode: str | None = None
    supported_commands: list[str] = Field(default_factory=list)
    group: DeviceGroupRef


class DeviceSendspin(ResponseModel):
    client_id: str | None = Field(default=None, description="Sendspin client id (Music Assistant player id).")
    listen_port: int | None = None
    active_listen_port: int | None = None
    port_collision: bool = False
    server_host: str | None = None
    server_port: int | None = None
    pairing_state: str = "disabled"
    pairing_window_open: bool = False
    pairing_pin: str | None = None
    reloading: bool = False
    stopping: bool = False
    daemon_recurring_lifetime_s: float | None = None


class DeviceMusicAssistant(ResponseModel):
    syncgroup_id: str | None = None
    reconnecting: bool = False
    now_playing: dict[str, Any] | None = None


class DeviceRoom(ResponseModel):
    id: str | None = None
    name: str | None = None
    source: str | None = None
    confidence: str | None = None


class DeviceHealth(ResponseModel):
    state: str = "unknown"
    severity: str = "info"
    summary: str = ""
    reasons: list[str] = Field(default_factory=list)
    last_event_at: str | None = None


class Device(ResponseModel):
    id: str = Field(description="Stable player id (UUID derived from the speaker's MAC).")
    name: str | None = None
    enabled: bool = True
    state_changed_at: str | None = None
    uptime: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    health: DeviceHealth
    room: DeviceRoom
    bluetooth: DeviceBluetooth
    audio: DeviceAudio
    timing: DeviceTiming
    playback: DevicePlayback
    sendspin: DeviceSendspin
    music_assistant: DeviceMusicAssistant
    capabilities: dict[str, Any] = Field(default_factory=dict, description="Per-action availability and remediation.")
    transfer_readiness: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def from_status(cls, d: dict[str, Any]) -> Device:
        g = d.get
        pair_failure = None
        if g("pair_failure_kind"):
            pair_failure = PairFailure(
                kind=str(g("pair_failure_kind")), adapter_mac=g("pair_failure_adapter_mac"), at=g("pair_failure_at")
            )
        health = g("health_summary") or {}
        return cls(
            id=str(g("player_id") or ""),
            name=g("player_name"),
            enabled=bool(g("enabled", True)),
            state_changed_at=g("state_changed_at"),
            uptime=g("uptime"),
            last_error=g("last_error"),
            last_error_at=g("last_error_at"),
            health=DeviceHealth(**{k: v for k, v in health.items() if k in DeviceHealth.model_fields}),
            room=DeviceRoom(
                id=g("room_id"), name=g("room_name"), source=g("room_source"), confidence=g("room_confidence")
            ),
            bluetooth=DeviceBluetooth(
                mac=g("bluetooth_mac"),
                adapter=AdapterRef(
                    mac=g("bluetooth_adapter"), hci=g("bluetooth_adapter_hci") or "", name=g("bluetooth_adapter_name")
                ),
                available=bool(g("bluetooth_available")),
                connected=bool(g("bluetooth_connected")),
                connected_at=g("bluetooth_connected_at"),
                paired=g("bluetooth_paired"),
                management_enabled=bool(g("bt_management_enabled", True)),
                released_by=g("bt_released_by"),
                reconnecting=bool(g("reconnecting")),
                reconnect_attempt=int(g("reconnect_attempt") or 0),
                reconnect_attempts_remaining=g("reconnect_attempts_remaining"),
                standby=bool(g("bt_standby")),
                standby_since=g("bt_standby_since"),
                waking=bool(g("bt_waking")),
                power_save=bool(g("bt_power_save")),
                rssi_dbm=g("rssi_dbm"),
                battery_level=g("battery_level"),
                codec_id=g("bt_codec_id"),
                codec_name=g("bt_codec_name"),
                transport_state=g("bt_transport_state"),
                reported_delay_ms=g("bt_reported_delay_ms"),
                delay_reporting_supported=bool(g("bt_delay_reporting_supported")),
                never_paired=bool(g("never_paired")),
                never_paired_since=g("never_paired_since"),
                pair_failure=pair_failure,
                adapter_recovery=AdapterRecovery(
                    stage=g("adapter_recovery_stage") or "idle",
                    last_at=g("adapter_recovery_last_at"),
                    adapter=g("adapter_recovery_adapter"),
                    result=g("adapter_recovery_result"),
                    failure_reason=g("adapter_recovery_failure_reason"),
                ),
            ),
            audio=DeviceAudio(
                sink_name=g("sink_name"),
                has_sink=bool(g("has_sink")),
                volume=_int_or(g("volume"), 100),
                muted=bool(g("muted")),
                sink_muted=bool(g("sink_muted")),
                format=g("audio_format"),
                streaming=bool(g("audio_streaming")),
                buffering=bool(g("buffering")),
                idle_mode=g("idle_mode") or "default",
                static_delay_ms=float(g("static_delay_ms") or 0.0),
                static_delay_source=g("static_delay_source") or "default",
                static_delay_calibrated_at=g("static_delay_calibrated_at"),
                static_delay_codec=g("static_delay_codec"),
            ),
            timing=DeviceTiming(
                available=bool(g("timing_metrics_available")),
                sampled_at=g("timing_sampled_at"),
                backend_output_latency_ms=g("backend_output_latency_ms"),
                buffered_audio_ms=g("buffered_audio_ms"),
                playback_position_us=g("playback_position_us"),
                sync_error_ms=g("playback_sync_error_ms"),
                clock_synchronized=bool(g("clock_synchronized")),
                clock_offset_ms=g("clock_offset_ms"),
                clock_uncertainty_ms=g("clock_uncertainty_ms"),
                required_lead_time_ms=int(g("required_lead_time_ms") or 250),
                min_buffer_ms=int(g("min_buffer_ms") or 250),
                reanchoring=bool(g("reanchoring")),
                reanchor_count=int(g("reanchor_count") or 0),
                reanchor_count_session=int(g("reanchor_count_session") or 0),
                reanchor_count_5m=g("reanchor_count_5m"),
                reanchor_count_30m=g("reanchor_count_30m"),
                last_sync_error_ms=g("last_sync_error_ms"),
                last_reanchor_at=g("last_reanchor_at"),
                calibration_metronome_active=bool(g("calibration_metronome_active")),
                latency_suggestion=LatencySuggestion(
                    suggested_static_delay_ms=g("suggested_static_delay_ms"),
                    source=g("latency_suggestion_source") or "unavailable",
                    confidence=g("latency_suggestion_confidence") or "none",
                    explanation=g("latency_suggestion_explanation") or "",
                    revision=g("latency_suggestion_revision"),
                    double_count_risk=bool(g("latency_double_count_risk")),
                ),
            ),
            playback=DevicePlayback(
                playing=bool(g("playing")),
                connected=bool(g("connected")),
                server_connected=bool(g("server_connected")),
                server_connected_at=g("server_connected_at"),
                server_url=g("connected_server_url") or None,
                track=Track(
                    title=g("current_track"),
                    artist=g("current_artist"),
                    album=g("current_album"),
                    album_artist=g("current_album_artist"),
                    artwork_url=g("artwork_url"),
                    year=g("track_year"),
                    number=g("track_number"),
                    progress_ms=g("track_progress_ms"),
                    duration_ms=g("track_duration_ms"),
                ),
                playback_speed=g("playback_speed"),
                shuffle=g("shuffle"),
                repeat_mode=g("repeat_mode"),
                supported_commands=[str(c) for c in (g("supported_commands") or [])],
                group=DeviceGroupRef(
                    # The MA sync group names the group (Sendspin 1.0's group id is per
                    # session), matching the id /api/v1/groups and the group commands use.
                    id=g("ma_syncgroup_id") or g("group_id"),
                    name=g("group_name"),
                    volume=g("group_volume"),
                    muted=g("group_muted"),
                ),
            ),
            sendspin=DeviceSendspin(
                client_id=g("sendspin_client_id"),
                listen_port=g("listen_port"),
                active_listen_port=g("active_listen_port"),
                port_collision=bool(g("port_collision")),
                server_host=g("server_host"),
                server_port=g("server_port"),
                pairing_state=g("pairing_state") or "disabled",
                pairing_window_open=bool(g("pairing_window_open")),
                pairing_pin=g("pairing_pin"),
                reloading=bool(g("reloading")),
                stopping=bool(g("stopping")),
                daemon_recurring_lifetime_s=g("daemon_recurring_lifetime_s"),
            ),
            music_assistant=DeviceMusicAssistant(
                syncgroup_id=g("ma_syncgroup_id"),
                reconnecting=bool(g("ma_reconnecting")),
                now_playing=g("ma_now_playing"),
            ),
            capabilities=g("capabilities") or {},
            transfer_readiness=g("transfer_readiness"),
            recent_events=list(g("recent_events") or []),
        )


class DisabledDevice(ResponseModel):
    """A configured speaker the bridge is not running (``enabled: false``)."""

    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    id: str | None = Field(default=None, description="The id /devices/{id} commands take (enable, remove).")
    mac: str | None = None
    player_name: str | None = None
