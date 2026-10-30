"""The bridge configuration (config.json, schema version 6) as Pydantic models.

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

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from sendspin_bridge.config.players import Player


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
    advertise_host: str | None = ""
    """
    Address announced to Home Assistant instead of the detected one (reverse proxy, NAT). Empty = detect.
    """
    advertise_port: int | None = Field(0, ge=0, le=65535)
    """
    Port announced to Home Assistant instead of the web port. 0 = the web port.
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


class AdapterArea(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    area_id: str
    """
    Home Assistant area ID.
    """
    area_name: str | None = None
    """
    The area's name when it was chosen.
    """


class AUTHTOKEN(BaseModel):
    model_config = ConfigDict(extra="allow", use_attribute_docstrings=True)

    id: str | None = None
    label: str | None = None
    token_hash: str | None = None
    created: str | None = None
    last_used: str | None = None


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
    CONFIG_SCHEMA_VERSION: Literal[6] | None = None
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
    SENDSPIN_OWN_SERVER_ONLY: bool | None = True
    """
    Accept only this bridge's own Music Assistant. When another Music Assistant on the network dials a speaker first, the bridge turns it away so the speaker stays available to the right one. Has no effect until the bridge knows its Music Assistant (a server address or the Music Assistant API URL).
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
    players: list[Player] = Field(default_factory=list)
    """
    Configured players. Each has a stable id and a backend: a Bluetooth speaker
    (bluetooth_a2dp) or a local audio output (pulse_sink).
    """
    BLUETOOTH_ADAPTERS: list[BluetoothAdapter] | None = Field([], validate_default=True)
    """
    List of Bluetooth adapter configurations.
    """
    HA_AREA_NAME_ASSIST_ENABLED: bool | None = False
    """
    Enable automatic room name assist from Home Assistant areas.
    """
    HA_ADAPTER_AREA_MAP: dict[str, AdapterArea] | None = {}
    """
    Manual mapping of adapter MAC → Home Assistant area.
    """
    TZ: str | None = "Australia/Melbourne"
    """
    Timezone (IANA format).
    """
    PULSE_LATENCY_MSEC: int | None = Field(600, ge=0, le=5000)
    """
    PulseAudio latency in milliseconds.
    """
    PLAYOUT_SYNC: Literal["off", "measure", "loop"] | None = "off"
    """
    Bluetooth playout sync engine: off keeps the plain output, measure only measures the playout error, loop also corrects it.
    """
    EXPERIMENTAL_AIR_SENSOR: StrictBool | None = False
    """
    Experimental: measure when audio leaves over the air (SBC, needs PLAYOUT_SYNC=measure or loop and CAP_NET_RAW). Measures in measure mode; corrects placement in loop mode.
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
    MA_ADDON_SLUG: str | None = ""
    """
    HA add-on mode: slug of a Music Assistant add-on from a custom repository the bridge may sign in through (official and local add-ons need no entry).
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
    COMPENSATE_OUTPUT_LATENCY: bool | None = True
    """
    Take the PulseAudio buffer's latency out of the playback schedule, so audio leaves the audio server at its scheduled time whatever PULSE_LATENCY_MSEC is. Off restores the timing before 3.1.0-beta.7, where speakers played behind by that latency. The SENDSPIN_COMPENSATE_OUTPUT_LATENCY environment variable overrides it.
    """
    PLAYOUT_DIAG: bool | None = False
    """
    Playback clock diagnostics: let the audio sink provide its own clock so the log's Clock diag line also reports the sink's audio clock rate. Playback timing is unchanged. The SENDSPIN_PLAYOUT_DIAG environment variable overrides it.
    """
    PULSE_SINK_RESET_ON_START: bool | None = False
    """
    Suspend and resume each local audio output once before its player first starts, to wake an output that came up stuck.
    """
    DUPLICATE_DEVICE_CHECK: bool | None = True
    """
    Check MA API for duplicate device registrations across bridges.
    """
    TRUSTED_PROXIES: list[str] | None = []
    """
    Additional trusted proxy IP addresses for X-Forwarded-For.
    """
    SENDSPIN_SOURCE: bool | None = False
    """
    Offer audio a phone streams into the bridge over Bluetooth as a Sendspin source other players can play (#563). Needs PipeWire for the capture. Off by default.
    """
    SENDSPIN_SOURCE_PORT: int | None = 8940
    """
    The port the source's Sendspin server listens on. It must differ from the players' own listen ports (they start at 8928).
    """
    EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE: bool | None = True
    """
    Ask for the speaker's A2DP sink profile first when the sink is missing after connect, and disconnect→reconnect once if that does not help. A speaker that is also an audio source (an Amazon Echo, bluez/bluez#1898) otherwise lands on the a2dp_source profile and exposes no sink. On by default since 3.1.0-beta.25; may cause extra churn on some devices, turn it off for those.
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
    SHOW_TECHNICAL_DETAILS: bool | None = False
    """
    Web UI default for showing technical chips and rows (signal Δ dB, re-anchor counts, Playout, server ID, sync diagnostics, advanced settings). Each browser can override it from the preferences menu. Off for a new install, on for an install that already had speakers when this setting first appeared.
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
