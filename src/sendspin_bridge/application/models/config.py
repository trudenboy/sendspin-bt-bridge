"""The bridge configuration (config.json, schema version 5) as Pydantic models.

Generated once from config/schema.json and maintained here from now on:
these models are the source, the JSON Schema is exported from them.
Unknown keys are kept (extra="allow") so a newer config survives a
round-trip through an older client.
"""

# Enum fields keep their JSON Schema defaults as plain strings; Pydantic
# validates them into the enum, mypy cannot see that.
# mypy: disable-error-code="assignment"

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class LASTVOLUMES(RootModel[int]):
    root: int = Field(..., ge=0, le=100)


class LOGLEVEL(StrEnum):
    """
    Root logger level.
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class UPDATECHANNEL(StrEnum):
    """
    Software update channel.
    """

    stable = "stable"
    rc = "rc"
    beta = "beta"


class Mode(StrEnum):
    """
    Transport selector — pick exactly one. ``both`` was removed in v2.65.0-rc.3 because it caused duplicate HA entities; legacy stored values are normalised to ``mqtt`` on load.
    """

    off = "off"
    mqtt = "mqtt"
    rest = "rest"


class Mqtt(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    broker: str | None = None
    """
    Broker host or 'auto' (Supervisor MQTT add-on auto-detect).
    """
    port: int | None = Field(1883, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    discovery_prefix: str | None = "homeassistant"
    tls: bool | None = False
    client_id: str | None = None
    """
    Optional MQTT client_id; empty = auto-derived from bridge_id.
    """


class Rest(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    advertise_mdns: bool | None = True
    """
    Publish _sendspin-bridge._tcp.local. so HA's Zeroconf discovery offers our integration.
    """
    supervisor_pair: bool | None = True
    """
    On HAOS, allow the custom_component to obtain a long-lived bearer token via the SUPERVISOR_TOKEN round-trip pairing endpoint.
    """


class HAINTEGRATION(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    """
    Home Assistant integration: MQTT discovery (transport B) and/or mDNS-advertised REST surface for the custom_component (transport A1). Bridge-only entities — never duplicates Music Assistant's media_player.<name>.
    """

    enabled: bool | None = False
    mode: Mode | None = "off"
    """
    Transport selector — pick exactly one. ``both`` was removed in v2.65.0-rc.3 because it caused duplicate HA entities; legacy stored values are normalised to ``mqtt`` on load.
    """
    mqtt: Mqtt | None = None
    rest: Rest | None = None


class AUTHTOKEN(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    id: str | None = None
    label: str | None = None
    token_hash: str | None = None
    created: str | None = None
    last_used: str | None = None


class StaticDelaySource(StrEnum):
    """
    Provenance of the per-device static delay. auto_pending is an internal one-shot initialization marker.
    """

    auto_pending = "auto_pending"
    legacy = "legacy"
    manual = "manual"
    music_assistant = "music_assistant"
    bluez_report = "bluez_report"
    bluez_delay_report = "bluez_delay_report"
    codec_fallback = "codec_fallback"
    manual_calibration = "manual_calibration"
    microphone_calibration = "microphone_calibration"


class VolumeController(StrEnum):
    """
    Volume control backend: 'pa' (PulseAudio) or 'sendspin'.
    """

    pa = "pa"
    sendspin = "sendspin"


class IdleMode(StrEnum):
    """
    Per-device idle behavior: 'default' (no action), 'power_save' (suspend PA sink to release A2DP), 'auto_disconnect' (full BT disconnect after timeout), 'keep_alive' (periodic infrasound bursts).
    """

    default = "default"
    power_save = "power_save"
    auto_disconnect = "auto_disconnect"
    keep_alive = "keep_alive"


class KeepAliveMethod(StrEnum):
    """
    Payload for keepalive bursts: 'infrasound' (default — 2 Hz subsonic stereo), 'silence' (zero PCM, same length, for speakers that misbehave on the 2 Hz tone), 'none' (skip — let speaker time out naturally).
    """

    infrasound = "infrasound"
    silence = "silence"
    none = "none"


class BluetoothDevice(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)
    mac: str = Field(..., pattern="^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    """
    Bluetooth MAC address (XX:XX:XX:XX:XX:XX).
    """
    player_name: str | None = None
    """
    Display name for the speaker. Falls back to MAC if empty.
    """
    adapter: str | None = ""
    """
    Bluetooth adapter to use (e.g. 'hci0'). Empty = default adapter.
    """
    listen_port: int | None = Field(None, ge=1024, le=65535)
    """
    Override Sendspin listener port for this device.
    """
    static_delay_ms: int | None = Field(None, ge=0, le=5000)
    """
    Per-device static audio delay in milliseconds (0-5000). Added on top of DAC-anchored sync.
    """
    static_delay_source: StaticDelaySource | None = None
    """
    Provenance of the per-device static delay. auto_pending is an internal one-shot initialization marker.
    """
    static_delay_calibrated_at: str | None = None
    """
    ISO-8601 timestamp of the last confirmed calibration.
    """
    static_delay_codec: str | None = None
    """
    Codec active when the delay was calibrated.
    """
    required_lead_time_ms: int | None = Field(250, ge=0, le=30000)
    """
    Sendspin startup lead time advertised by this device.
    """
    min_buffer_ms: int | None = Field(250, ge=0, le=30000)
    """
    Sendspin minimum ongoing buffer advertised by this device.
    """
    enabled: bool | None = True
    """
    Whether this device is active.
    """
    volume_controller: VolumeController | None = "pa"
    """
    Volume control backend: 'pa' (PulseAudio) or 'sendspin'.
    """
    idle_disconnect_minutes: int | None = Field(0, ge=0)
    """
    Minutes of idle before BT standby disconnect. 0 = disabled. Legacy field — prefer idle_mode.
    """
    keepalive_enabled: bool | None = False
    """
    Send periodic silence to prevent BT speaker auto-sleep. Legacy field — prefer idle_mode.
    """
    keepalive_interval: int | None = Field(30, ge=30)
    """
    Seconds between keepalive infrasound bursts.
    """
    idle_mode: IdleMode | None = "default"
    """
    Per-device idle behavior: 'default' (no action), 'power_save' (suspend PA sink to release A2DP), 'auto_disconnect' (full BT disconnect after timeout), 'keep_alive' (periodic infrasound bursts).
    """
    power_save_delay_minutes: int | None = Field(1, ge=0, le=60)
    """
    Minutes after sink idle before suspending PA sink in power_save mode.
    """
    keep_alive_method: KeepAliveMethod | None = "infrasound"
    """
    Payload for keepalive bursts: 'infrasound' (default — 2 Hz subsonic stereo), 'silence' (zero PCM, same length, for speakers that misbehave on the 2 Hz tone), 'none' (skip — let speaker time out naturally).
    """
    room_id: str | None = None
    """
    Home Assistant area/room ID for this device.
    """
    room_name: str | None = None
    """
    Human-readable room name.
    """


class BluetoothAdapter(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)
    mac: str | None = Field(None, pattern="^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    """
    Adapter MAC address.
    """
    name: str | None = None
    """
    Friendly name for the adapter.
    """
    hci: str | None = None
    """
    HCI device name (e.g. 'hci0').
    """
    device_class: str | None = Field("", pattern="^(0x[0-9a-fA-F]{6}|)$")
    """
    Override the local Bluetooth Class of Device for this adapter (six-hex form, e.g. '0x00010c' for Computer/Laptop). Empty string leaves the kernel/bluetoothd default in place. Some peers — notably Samsung Q-series soundbars (bluez/bluez#1025) — reject incoming BR/EDR connections whose initiator CoD they don't recognise; setting this to '0x00010c' is the documented workaround.
    """


class BridgeConfig(BaseModel):
    """
    Machine-readable schema for /config/config.json used by the Sendspin BT Bridge.
    """

    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)
    CONFIG_SCHEMA_VERSION: Literal[5] | None = None
    """
    Schema version; managed by the application — do not change.
    """
    SENDSPIN_SERVER: str | None = "auto"
    """
    Music Assistant server hostname/IP. 'auto' uses mDNS discovery.
    """
    SENDSPIN_PORT: int | None = Field(9000, ge=1, le=65535)
    """
    Music Assistant WebSocket port.
    """
    SENDSPIN_PAIRING: bool | None = False
    """
    Require Music Assistant to pair with a PIN before playback. Off by default so speakers play as soon as Music Assistant finds them. Transport encryption still runs; this only gates pairing.
    """
    WEB_PORT: int | None = Field(None, ge=1, le=65535)
    """
    Web UI port. null = use WEB_PORT env var or 8080.
    """
    BASE_LISTEN_PORT: int | None = Field(None, ge=1024, le=65535)
    """
    Override per-device Sendspin listener base port. null = auto-assign.
    """
    BRIDGE_NAME: str | None = ""
    """
    Bridge instance name. Empty or 'auto' resolves to hostname.
    """
    BLUETOOTH_DEVICES: list[BluetoothDevice] | None = Field([], validate_default=True)
    """
    List of configured Bluetooth speaker devices.
    """
    BLUETOOTH_ADAPTERS: list[BluetoothAdapter] | None = Field([], validate_default=True)
    """
    List of Bluetooth adapter configurations.
    """
    HA_AREA_NAME_ASSIST_ENABLED: bool | None = False
    """
    Enable automatic room name assist from Home Assistant areas.
    """
    HA_ADAPTER_AREA_MAP: dict[str, str] | None = {}
    """
    Manual mapping of adapter MAC → HA area name.
    """
    TZ: str | None = "Australia/Melbourne"
    """
    Timezone (IANA format).
    """
    LAST_VOLUMES: dict[str, LASTVOLUMES] | None = Field({}, validate_default=True)
    """
    Runtime state: persisted per-device volumes keyed by MAC.
    """
    LAST_SINKS: dict[str, str] | None = {}
    """
    Runtime state: persisted per-device PulseAudio sink names keyed by MAC.
    """
    PULSE_LATENCY_MSEC: int | None = Field(600, ge=0, le=5000)
    """
    PulseAudio latency in milliseconds.
    """
    PREFER_SBC_CODEC: bool | None = False
    """
    Prefer SBC codec over AAC/aptX for Bluetooth audio.
    """
    BT_CHECK_INTERVAL: int | None = Field(10, ge=1, le=300)
    """
    Bluetooth connection check interval in seconds.
    """
    BT_MAX_RECONNECT_FAILS: int | None = Field(0, ge=0)
    """
    Max consecutive reconnect failures before giving up. 0 = unlimited.
    """
    BT_CHURN_THRESHOLD: int | None = Field(0, ge=0)
    """
    Number of rapid connect/disconnect cycles triggering churn isolation. 0 = disabled.
    """
    BT_CHURN_WINDOW: float | None = Field(300.0, ge=0.0)
    """
    Window in seconds for churn threshold counting.
    """
    AUTH_ENABLED: bool | None = False
    """
    Enable web UI password authentication.
    """
    SESSION_TIMEOUT_HOURS: int | None = Field(24, ge=1)
    """
    Authenticated session lifetime in hours.
    """
    BRUTE_FORCE_PROTECTION: bool | None = True
    """
    Enable brute-force login protection.
    """
    BRUTE_FORCE_MAX_ATTEMPTS: int | None = Field(5, ge=1)
    """
    Max failed login attempts before lockout.
    """
    BRUTE_FORCE_WINDOW_MINUTES: int | None = Field(1, ge=1)
    """
    Time window for counting failed login attempts.
    """
    BRUTE_FORCE_LOCKOUT_MINUTES: int | None = Field(5, ge=1)
    """
    Lockout duration after exceeding max attempts.
    """
    STARTUP_BANNER_GRACE_SECONDS: int | None = Field(5, ge=0)
    """
    Grace period before showing startup guidance banners.
    """
    RECOVERY_BANNER_GRACE_SECONDS: int | None = Field(15, ge=0)
    """
    Grace period before showing recovery guidance banners.
    """
    AUTH_PASSWORD_HASH: str | None = ""
    """
    PBKDF2-SHA256 password hash. Managed by the application — do not edit manually.
    """
    SECRET_KEY: str | None = ""
    """
    Flask session secret key. Auto-generated on first startup — do not edit.
    """
    LOG_LEVEL: LOGLEVEL | None = "INFO"
    """
    Root logger level.
    """
    MA_API_URL: str | None = ""
    """
    Music Assistant REST API URL.
    """
    MA_API_TOKEN: str | None = ""
    """
    Music Assistant API token.
    """
    MA_AUTH_PROVIDER: str | None = ""
    """
    MA authentication provider identifier.
    """
    MA_USERNAME: str | None = ""
    """
    Music Assistant username.
    """
    MA_TOKEN_INSTANCE_HOSTNAME: str | None = ""
    """
    Hostname of the MA instance the token was issued for.
    """
    MA_TOKEN_LABEL: str | None = ""
    """
    Label for the MA API token.
    """
    MA_ACCESS_TOKEN: str | None = ""
    """
    MA OAuth access token. Managed by the application.
    """
    MA_REFRESH_TOKEN: str | None = ""
    """
    MA OAuth refresh token. Managed by the application.
    """
    MA_AUTO_SILENT_AUTH: bool | None = True
    """
    Automatically attempt silent MA re-authentication.
    """
    MA_WEBSOCKET_MONITOR: bool | None = True
    """
    Enable persistent WebSocket connection to MA for real-time updates.
    """
    VOLUME_VIA_MA: bool | None = True
    """
    Route volume commands through Music Assistant API.
    """
    MUTE_VIA_MA: bool | None = True
    """
    Route mute commands through Music Assistant API.
    """
    SMOOTH_RESTART: bool | None = True
    """
    Enable smooth daemon restart (reconnect IPC instead of full restart).
    """
    UPDATE_CHANNEL: UPDATECHANNEL | None = "stable"
    """
    Software update channel.
    """
    AUTO_UPDATE: bool | None = False
    """
    Automatically apply available updates.
    """
    CHECK_UPDATES: bool | None = True
    """
    Periodically check for new versions.
    """
    DISABLE_PA_RESCUE_STREAMS: bool | None = False
    """
    Disable PulseAudio module-rescue-streams correction on BT reconnect.
    """
    DUPLICATE_DEVICE_CHECK: bool | None = True
    """
    Check MA API for duplicate device registrations across bridges.
    """
    TRUSTED_PROXIES: list[str] | None = []
    """
    Additional trusted proxy IP addresses for X-Forwarded-For.
    """
    EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE: bool | None = False
    """
    Experimental: last-resort A2DP sink recovery via disconnect→reconnect when sink is missing after connect. Workaround for bluez/bluez#1922. May cause extra churn on some devices.
    """
    EXPERIMENTAL_PA_MODULE_RELOAD: bool | None = False
    """
    Experimental: reload PulseAudio module-bluez5-discover as a last resort when bluez_card.* fails to appear. Disrupts all active BT audio on the bridge.
    """
    EXPERIMENTAL_ADAPTER_AUTO_RECOVERY: bool | None = False
    """
    Experimental: after BT_MAX_RECONNECT_FAILS consecutive failed reconnects, power-cycle the adapter through Bluetooth MGMT/HCI and optionally issue a USB reset if the power cycle fails. Disruptive to every device on that controller. Requires Linux Bluetooth capabilities; USB escalation additionally requires /dev/bus/usb access.
    """
    RSSI_BADGE: bool | None = True
    """
    Poll live RSSI for connected BR/EDR speakers via the kernel mgmt socket (opcode 0x0031) every 5 s and surface it in the device card / scan results as a coloured signal-strength chip. BR/EDR returns delta from the controller's Golden Receive Power Range (0 = good, negative = weak), LE returns absolute dBm. Adds one mgmt round-trip per connected device per tick, gated by the shared bt_operation_lock. Legacy key ``EXPERIMENTAL_RSSI_BADGE`` is migrated automatically.
    """
    HA_INTEGRATION: HAINTEGRATION | None = Field(
        {
            "enabled": False,
            "mode": "off",
            "mqtt": {
                "broker": "auto",
                "port": 1883,
                "username": "",
                "password": "",
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
        validate_default=True,
    )
    """
    Home Assistant integration: MQTT discovery (transport B) and/or mDNS-advertised REST surface for the custom_component (transport A1). Bridge-only entities — never duplicates Music Assistant's media_player.<name>.
    """
    AUTH_TOKENS: list[AUTHTOKEN] | None = Field([], validate_default=True)
    """
    Long-lived API bearer tokens used by the HA custom_component. Plaintext tokens are returned only at issuance — the persisted form stores PBKDF2-SHA256 hashes.
    """
