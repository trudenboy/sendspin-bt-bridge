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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping

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

# R-12: the air-to-sound latency is stored per (codec, SBC rate, adapter MAC), at most 8 per player.
AIR_LATENCY_MAX_ENTRIES: Final = 8


class AirLatencyEntry(BaseModel):
    """One air-to-sound calibration of a speaker (the sync wizard's ``L_as``). Strict: no string numbers."""

    model_config = ConfigDict(extra="forbid", strict=True)

    codec: Literal["sbc"]
    rate: Literal[16000, 32000, 44100, 48000]
    adapter: str = Field(pattern=r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
    latency_ms: float = Field(ge=0, le=2000)
    spread_ms: float | None = Field(None, ge=0, le=500)
    measured_at: str
    reference: str = Field("", max_length=80)
    source: Literal["sync_wizard", "manual"] = "sync_wizard"

    def key(self) -> tuple[str, int, str]:
        return (self.codec, self.rate, self.adapter)


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
    # Air sensor calibrations, one per (codec, SBC rate, adapter); applied live.
    air_to_sound_latency: list[AirLatencyEntry] | None = Field(None, max_length=AIR_LATENCY_MAX_ENTRIES)
    backend: Backend

    @field_validator("keepalive_interval")
    @classmethod
    def _keepalive_interval_range(cls, value: int | None) -> int | None:
        if value is not None and value != 0 and not 30 <= value <= 3600:
            raise ValueError("keepalive_interval must be 0 (off) or between 30 and 3600")
        return value

    @field_validator("air_to_sound_latency")
    @classmethod
    def _unique_air_latency_keys(cls, value: list[AirLatencyEntry] | None) -> list[AirLatencyEntry] | None:
        if value is not None and len({entry.key() for entry in value}) != len(value):
            raise ValueError("air_to_sound_latency has two entries for the same codec, rate and adapter")
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


def _measured_at(entry: Mapping[str, Any]) -> datetime:
    """When an entry was measured (for eviction); an unreadable time counts as oldest."""
    try:
        when = datetime.fromisoformat(str(entry.get("measured_at")).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _air_key(entry: Mapping[str, Any]) -> tuple[str, int, str]:
    return (str(entry["codec"]), int(entry["rate"]), str(entry["adapter"]))


def _cap_air_latency(entries: list[dict[str, Any]], max_entries: int) -> list[dict[str, Any]]:
    """*entries* without the oldest measurements beyond *max_entries* (order kept)."""
    if len(entries) <= max_entries:
        return entries
    newest = sorted(range(len(entries)), key=lambda i: _measured_at(entries[i]), reverse=True)[:max_entries]
    keep = set(newest)
    return [entry for i, entry in enumerate(entries) if i in keep]


def valid_air_latency_entries(raw: object, *, max_entries: int = AIR_LATENCY_MAX_ENTRIES) -> list[dict[str, Any]]:
    """The valid entries of a stored ``air_to_sound_latency`` value, as dicts.

    Invalid entries are dropped one by one, a repeated key keeps its newest
    measurement, and only the newest *max_entries* measurements stay.
    """
    if not isinstance(raw, list):
        return []
    by_key: dict[tuple[str, int, str], dict[str, Any]] = {}
    for item in raw:
        try:
            entry = AirLatencyEntry.model_validate(item)
        except ValidationError:
            continue
        dumped = entry.model_dump(mode="json", exclude_unset=True)
        previous = by_key.get(entry.key())
        if previous is None or _measured_at(dumped) >= _measured_at(previous):
            by_key[entry.key()] = dumped
    return _cap_air_latency(list(by_key.values()), max_entries)


def resolve_air_latency(
    player_cfg: Mapping[str, Any], codec: str | None, rate: int | None, adapter: str | None
) -> float | None:
    """The stored air-to-sound latency for the speaker's current codec, SBC rate and adapter, or None."""
    if not isinstance(codec, str) or not isinstance(rate, int) or isinstance(rate, bool):
        return None
    wanted = (codec.strip().lower(), rate, normalize_mac(adapter))
    if not wanted[2]:
        return None
    for entry in valid_air_latency_entries(player_cfg.get("air_to_sound_latency")):
        if _air_key(entry) == wanted:
            return float(entry["latency_ms"])
    return None


def upsert_air_latency(
    entries: list[dict[str, Any]], entry: dict[str, Any], *, max_entries: int = AIR_LATENCY_MAX_ENTRIES
) -> list[dict[str, Any]]:
    """*entries* with *entry* saved: it replaces the one with its key; the oldest measurements beyond *max_entries* go.

    Raises ``pydantic.ValidationError`` when *entry* is not a valid calibration.
    """
    new = AirLatencyEntry.model_validate(entry)
    kept = [e for e in valid_air_latency_entries(entries, max_entries=max_entries) if _air_key(e) != new.key()]
    return _cap_air_latency([*kept, new.model_dump(mode="json", exclude_unset=True)], max_entries)


def forget_air_latency(
    entries: object, codec: str | None = None, rate: int | None = None, adapter: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    """*entries* without the one for (codec, rate, adapter), or without all of them when *codec* is None.

    Returns the remaining valid entries and how many were removed.
    """
    valid = valid_air_latency_entries(entries)
    if codec is None:
        return [], len(valid)
    wanted = (codec.strip().lower(), rate, normalize_mac(adapter))
    remaining = [e for e in valid if _air_key(e) != wanted]
    return remaining, len(valid) - len(remaining)


def supported_actions_for(backend_type: str) -> list[str]:
    """What a player of this backend can do (``Device.supported_actions``)."""
    return list(SUPPORTED_ACTIONS.get(backend_type, ()))
