"""Schema v6 players: models and the conversions to and from the v5 device list.

A player is a Music Assistant player the bridge runs. ``backend`` says what it
plays to: a Bluetooth speaker today, a local PulseAudio/PipeWire sink later.
Volatile per-player state (volume, sink, released) lives in ``PLAYER_STATE``,
keyed by player id, so writing it does not look like a settings change.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
BLUETOOTH: Final = "bluetooth_a2dp"
PULSE_SINK: Final = "pulse_sink"
SUPPORTED_ACTIONS: dict[str, tuple[str, ...]] = {
    BLUETOOTH: (
        "volume",
        "transport",
        "power_save",
        "reconnect",
        "repair",
        "standby",
        "claim",
        "pairing_window",
        "rssi",
        "battery",
    ),
    PULSE_SINK: ("volume", "transport", "power_save"),
}
LEGACY_DEVICE_STATE_KEYS = ("released", "released_by")
# v5 device keys that move into ``backend``.
_LEGACY_BACKEND_KEYS = ("mac", "adapter")


def normalize_mac(raw: object) -> str:
    """Upper-case, trimmed MAC, or "" when *raw* is not a MAC."""
    if not isinstance(raw, str):
        return ""
    mac = raw.strip().upper()
    return mac if _MAC_RE.match(mac) else ""


def player_id_for_mac(mac: str) -> str:
    """The player id Music Assistant knows a Bluetooth speaker by."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, mac.strip().lower()))


class BluetoothBackend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bluetooth_a2dp"] = BLUETOOTH
    mac: str
    adapter: str = ""

    @field_validator("mac", mode="before")
    @classmethod
    def _mac(cls, value: object) -> str:
        mac = normalize_mac(value)
        if not mac:
            raise ValueError(f"invalid MAC address {value!r}")
        return mac


class SinkMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial: str | None = None
    bus_path: str | None = None
    vendor_id: str | None = None
    product_id: str | None = None
    alsa_long_card_name: str | None = None


class PulseSinkBackend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pulse_sink"] = PULSE_SINK
    sink: str = Field(min_length=1)
    match: SinkMatch = Field(default_factory=SinkMatch)


Backend = Annotated[BluetoothBackend | PulseSinkBackend, Field(discriminator="type")]


class Player(BaseModel):
    # Extra keys survive (legacy keep-alive fields, future additions).
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    name: str = ""
    enabled: bool = True
    listen_port: int | None = Field(None, ge=1024, le=65535)
    listen_host: str | None = None
    preferred_format: str | None = None
    static_delay_ms: int | None = Field(None, ge=0, le=5000)
    static_delay_source: str | None = None
    static_delay_calibrated_at: str | None = None
    static_delay_codec: str | None = None
    required_lead_time_ms: int | None = Field(None, ge=0, le=30000)
    min_buffer_ms: int | None = Field(None, ge=0, le=30000)
    volume_controller: Literal["pa", "sendspin"] | None = None
    idle_mode: Literal["default", "power_save", "auto_disconnect", "keep_alive"] | None = None
    power_save_delay_minutes: int | None = Field(1, ge=0, le=60)
    idle_disconnect_minutes: int | None = Field(0, ge=0)
    # 0 switches keep-alive off; otherwise 30-3600 s. The schema advertises the
    # smallest value the UI lets a user enter (30); the validator also accepts 0.
    keepalive_interval: Annotated[int, Field(json_schema_extra={"minimum": 30})] | None = 30
    keep_alive_method: Literal["infrasound", "silence", "none"] | None = None
    room_id: str | None = None
    room_name: str | None = None
    # false = this speaker runs the way it did before playout sync (legacy player,
    # not held warm, no automatic offset); null / true follow PLAYOUT_SYNC.
    playout_sync_enabled: StrictBool | None = None
    backend: Backend

    @field_validator("keepalive_interval")
    @classmethod
    def _keepalive_interval_range(cls, value: int | None) -> int | None:
        if value is not None and value != 0 and not 30 <= value <= 3600:
            raise ValueError("keepalive_interval must be 0 (off) or between 30 and 3600")
        return value

    @model_validator(mode="after")
    def _backend_options(self) -> Player:
        if isinstance(self.backend, PulseSinkBackend):
            if self.idle_mode == "auto_disconnect":
                raise ValueError("idle_mode 'auto_disconnect' is only for bluetooth_a2dp players")
            if self.keep_alive_method == "infrasound":
                raise ValueError("keep_alive_method 'infrasound' is only for bluetooth_a2dp players")
        return self


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    volume: int | None = Field(None, ge=0, le=100)
    sink: str | None = None
    released: bool | None = None
    released_by: str | None = None


def legacy_device_to_player(device: dict[str, Any], *, existing_id: str | None = None) -> dict[str, Any]:
    """Turn one v5 ``BLUETOOTH_DEVICES`` entry into a v6 player dict.

    Keys move as they are; ``player_name`` becomes ``name``, ``mac`` and
    ``adapter`` go into ``backend``, ``released``/``released_by`` are left out
    (they are state, see ``PLAYER_STATE``).
    """
    mac = normalize_mac(device.get("mac"))
    if not mac:
        raise ValueError(f"Bluetooth device without a valid MAC: {device.get('mac')!r}")
    player: dict[str, Any] = {"id": existing_id or player_id_for_mac(mac)}
    player["name"] = str(device.get("player_name") or "").strip() or mac
    for key, value in device.items():
        if key in ("player_name", "id", *_LEGACY_BACKEND_KEYS, *LEGACY_DEVICE_STATE_KEYS):
            continue
        player[key] = value
    player["backend"] = {"type": BLUETOOTH, "mac": mac, "adapter": str(device.get("adapter") or "").strip()}
    return player


def player_to_device_entry(player: dict[str, Any]) -> dict[str, Any] | None:
    """The v5-shaped view of a Bluetooth player (plus ``id``), or None."""
    backend = player.get("backend") if isinstance(player, dict) else None
    if not isinstance(backend, dict) or backend.get("type") != BLUETOOTH:
        return None
    mac = normalize_mac(backend.get("mac"))
    if not mac:
        return None
    entry: dict[str, Any] = {"mac": mac, "adapter": str(backend.get("adapter") or "")}
    entry["player_name"] = player.get("name") or mac
    for key, value in player.items():
        if key in ("name", "backend", "id"):
            continue
        entry[key] = value
    entry["id"] = str(player.get("id") or player_id_for_mac(mac))
    return entry


def device_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Every Bluetooth player as a v5 device dict, ``released`` merged in from state."""
    players = config.get("players") if isinstance(config, dict) else None
    if not isinstance(players, list):
        return []
    raw_state = config.get("PLAYER_STATE")
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    entries: list[dict[str, Any]] = []
    for player in players:
        entry = player_to_device_entry(player) if isinstance(player, dict) else None
        if entry is None:
            continue
        raw_entry_state = state.get(entry["id"])
        player_state_entry: dict[str, Any] = raw_entry_state if isinstance(raw_entry_state, dict) else {}
        for key in LEGACY_DEVICE_STATE_KEYS:
            if player_state_entry.get(key) is not None:
                entry[key] = player_state_entry[key]
        entries.append(entry)
    return entries


def get_players(config: dict[str, Any]) -> list[Player]:
    """Validated players; an invalid entry is skipped with a warning."""
    raw = config.get("players") if isinstance(config, dict) else None
    if not isinstance(raw, list):
        return []
    players: list[Player] = []
    for entry in raw:
        try:
            players.append(Player.model_validate(entry))
        except ValidationError as exc:
            logger.warning("Skipping invalid player %r: %s", entry, exc.errors(include_url=False))
    return players


def find_player_by_mac(config: dict[str, Any], mac: str) -> dict[str, Any] | None:
    """The live Bluetooth player dict for *mac* inside ``config["players"]``."""
    wanted = normalize_mac(mac)
    if not wanted:
        return None
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if not isinstance(backend, dict) or backend.get("type") != BLUETOOTH:
            continue
        if normalize_mac(backend.get("mac")) == wanted:
            return player
    return None


def find_player_by_id(config: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    """The live player dict with *player_id* inside ``config["players"]``."""
    for player in config.get("players") or []:
        if isinstance(player, dict) and player.get("id") == player_id:
            return player
    return None


def player_state(config: dict[str, Any], player_id: str) -> dict[str, Any]:
    """The live ``PLAYER_STATE[player_id]`` dict, created when missing."""
    state = config.get("PLAYER_STATE")
    if not isinstance(state, dict):
        state = config["PLAYER_STATE"] = {}
    entry = state.get(player_id)
    if not isinstance(entry, dict):
        entry = state[player_id] = {}
    return entry


def supported_actions_for(backend_type: str) -> list[str]:
    """What a player of this backend can do (``Device.supported_actions``)."""
    return list(SUPPORTED_ACTIONS.get(backend_type, ()))
