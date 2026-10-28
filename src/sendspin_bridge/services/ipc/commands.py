"""The parent↔daemon command set.

The commands used to be twelve strings parsed by an ``elif`` chain with no
``else``: an unknown command was a silent no-op, and every branch re-derived
its own coercion and clamping from raw payload keys, so the same range check
appeared in the daemon, in the config layer and in the JSON schema.

Here each command is a type.  ``decode_command`` is the one place a payload
becomes one, so the clamps travel with the value rather than with the branch
that happened to read it, and a command nobody implements is an error the
daemon can report instead of a message that vanishes.

The wire shape is unchanged — ``{"cmd": ..., **payload}`` — because parent and
daemon are versioned separately and an older peer must still understand what
this one sends.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.ipc.ipc_protocol import build_command_envelope

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirBatch

__all__ = [
    "AIR_COMMAND_NAMES",
    "AIR_ENVELOPE_VERSION",
    "AirCommand",
    "AirEnvelope",
    "AirFold",
    "AirSource",
    "Command",
    "CommandError",
    "InvalidCommand",
    "OpenPairingWindow",
    "Pause",
    "Play",
    "Reconnect",
    "SetAirCalibration",
    "SetLogLevel",
    "SetMinBufferMs",
    "SetMute",
    "SetRequiredLeadTimeMs",
    "SetStandby",
    "SetStaticDelayMs",
    "SetVolume",
    "Stop",
    "Transport",
    "UnknownCommand",
    "air_envelope_from_batch",
    "decode_command",
    "encode_command",
]

#: Ranges the daemon will accept.  They live with the command rather than
#: with the branch that reads it, so the parent, the daemon and the config
#: schema cannot drift apart.
VOLUME_RANGE = (0, 100)
STATIC_DELAY_RANGE_MS = (0.0, 5000.0)
BUFFER_RANGE_MS = (0.0, 30000.0)

VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class CommandError(Exception):
    """The message could not be turned into a command."""


class UnknownCommand(CommandError):
    """No command by that name — the peer speaks a verb this build lacks."""


class InvalidCommand(CommandError):
    """The command is known but the payload cannot be honoured."""


@dataclass(frozen=True)
class Stop:
    """Shut the daemon down."""


@dataclass(frozen=True)
class Pause:
    """Pause the group."""


@dataclass(frozen=True)
class Play:
    """Resume the group."""


@dataclass(frozen=True)
class SetVolume:
    value: int


@dataclass(frozen=True)
class SetMute:
    muted: bool


@dataclass(frozen=True)
class Reconnect:
    #: Pause before reconnecting, giving the server time to drop the old
    #: player before a new hello arrives.
    delay_s: float = 0.0


@dataclass(frozen=True)
class SetLogLevel:
    level: str


@dataclass(frozen=True)
class SetStaticDelayMs:
    value: float


@dataclass(frozen=True)
class SetRequiredLeadTimeMs:
    value: float


@dataclass(frozen=True)
class SetMinBufferMs:
    value: float


@dataclass(frozen=True)
class Transport:
    action: str
    value: Any = None


@dataclass(frozen=True)
class SetStandby:
    #: The sink to redirect to; ``None`` restores the device's own sink.
    sink: str | None = None


@dataclass(frozen=True)
class OpenPairingWindow:
    """Admit one Sendspin pairing attempt."""


# ── Air sensor (parent → daemon), design "IPC" ───────────────────────────────
#
# Additive verbs with their own payload schema version ``v`` inside the usual
# protocol_version envelope.  Every field arrives from another process and is
# validated here; an error message names the field, never echoes its value.

AIR_ENVELOPE_VERSION = 1

AIR_RATES = frozenset({16000, 32000, 44100, 48000})
AIR_SUBBANDS = frozenset({4, 8})
AIR_BLOCKS = frozenset({4, 8, 12, 16})
AIR_TIMESTAMPS = frozenset({"kernel", "recv"})
AIR_STATES = frozenset({"streaming", "no_traffic", "unsupported_codec", "unavailable"})
AIR_FRAME_US_RANGE = (500.0, 20000.0)
AIR_COMPLETE_LAG_US_RANGE = (0.0, 200000.0)
AIR_T0_WINDOW_US = 10e6
AIR_MAX_CELLS = 256
AIR_MAX_ENERGY = 255
AIR_MAX_COUNTER = 2**32  # epoch and seq
AIR_MAX_MEDIA_US = 1e13  # ~115 days of media time in one epoch
AIR_U0_SLACK_US = 1.0
AIR_REASON_MAX = 80
AIR_CODEC_MAX = 32
AIR_MAX_HCI_INDEX = 0xFFFF
AIR_MAX_ACL_HANDLE = 0x0FFF
AIR_MAX_DROPS = 2**63
AIR_CALIBRATION_RANGE_MS = (0.0, 2000.0)
AIR_FOLD_MAX_SPAN_US = 30e6
AIR_FOLD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")

AIR_COMMAND_NAMES = frozenset({"air_envelope", "air_source", "set_air_calibration", "air_fold"})


@dataclass(frozen=True)
class AirEnvelope:
    """One batch of SBC energy cells of the speaker's link (``e``: 255 = missing frame)."""

    epoch: int
    seq: int
    u0_us: float
    t0_us: float
    frame_us: float
    timestamp: str
    rate: int
    subbands: int
    blocks: int
    bands: tuple[int, ...]
    complete_lag_us: float | None
    anchor_reset: bool
    e: tuple[int, ...]


@dataclass(frozen=True)
class AirSource:
    """The parent's view of the speaker's air source."""

    state: str
    reason: str | None
    codec: str | None
    adapter: str | None
    index: int | None
    handle: int | None
    in_step: bool
    monitor_drops: int | None
    ipc_dropped: int


@dataclass(frozen=True)
class SetAirCalibration:
    #: Air-to-sound latency for the current codec and adapter; ``None`` = uncalibrated.
    latency_ms: float | None


@dataclass(frozen=True)
class AirFold:
    """Fold the written envelope over the click cycle between two RAW times."""

    id: str
    from_us: float
    to_us: float


AirCommand = AirEnvelope | AirSource | SetAirCalibration | AirFold


Command = (
    Stop
    | Pause
    | Play
    | SetVolume
    | SetMute
    | Reconnect
    | SetLogLevel
    | SetStaticDelayMs
    | SetRequiredLeadTimeMs
    | SetMinBufferMs
    | Transport
    | SetStandby
    | OpenPairingWindow
    | AirEnvelope
    | AirSource
    | SetAirCalibration
    | AirFold
)


def _clamped_number(payload: dict, key: str, name: str, bounds: tuple[float, float]) -> float:
    if key not in payload or payload[key] is None:
        raise InvalidCommand(f"{name} requires {key!r}")
    try:
        value = float(payload[key])
    except (TypeError, ValueError) as exc:
        raise InvalidCommand(f"{name} got a non-numeric {key!r}: {payload[key]!r}") from exc
    low, high = bounds
    return max(low, min(high, value))


def _raw_now_us() -> float:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) / 1000


def _field(message: dict, name: str, key: str) -> Any:
    if key not in message:
        raise InvalidCommand(f"{name} requires {key!r}")
    return message[key]


def _air_int(message: dict, name: str, key: str, low: int, high: int, *, nullable: bool = False) -> Any:
    value = _field(message, name, key)
    if value is None and nullable:
        return None
    if type(value) is not int or not low <= value <= high:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return value


def _air_float(
    message: dict, name: str, key: str, low: float = -math.inf, high: float = math.inf, *, nullable: bool = False
) -> Any:
    value = _field(message, name, key)
    if value is None and nullable:
        return None
    if type(value) not in (int, float):
        raise InvalidCommand(f"{name} got a bad {key!r}")
    try:
        number = float(value)  # a JSON integer can be too large for a float
    except OverflowError:
        raise InvalidCommand(f"{name} got a bad {key!r}") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return number


def _air_bool(message: dict, name: str, key: str) -> bool:
    value = _field(message, name, key)
    if type(value) is not bool:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return value


def _air_str_choice(message: dict, name: str, key: str, choices: frozenset[str]) -> str:
    value = _field(message, name, key)
    if type(value) is not str or value not in choices:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return value


def _air_int_choice(message: dict, name: str, key: str, choices: frozenset[int]) -> int:
    value = _field(message, name, key)
    if type(value) is not int or value not in choices:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return value


def _air_text(message: dict, name: str, key: str, max_len: int) -> str | None:
    value = _field(message, name, key)
    if value is None:
        return None
    if type(value) is not str or len(value) > max_len or not value.isprintable():
        raise InvalidCommand(f"{name} got a bad {key!r}")
    return value


def _air_int_list(message: dict, name: str, key: str, low: int, high: int, max_len: int) -> tuple[int, ...]:
    value = _field(message, name, key)
    if type(value) is not list or not 1 <= len(value) <= max_len:
        raise InvalidCommand(f"{name} got a bad {key!r}")
    for item in value:
        if type(item) is not int or not low <= item <= high:
            raise InvalidCommand(f"{name} got a bad {key!r}")
    return tuple(value)


def _air_version(message: dict, name: str) -> None:
    value = _field(message, name, "v")
    if type(value) is not int or value != AIR_ENVELOPE_VERSION:
        raise InvalidCommand(f"{name} got an unsupported 'v'")


def _decode_air_envelope(message: dict, now_raw_us: Callable[[], float] | None) -> AirEnvelope:
    name = "air_envelope"
    _air_version(message, name)
    epoch = _air_int(message, name, "epoch", 0, AIR_MAX_COUNTER)
    seq = _air_int(message, name, "seq", 0, AIR_MAX_COUNTER)
    frame_us = _air_float(message, name, "frame_us", *AIR_FRAME_US_RANGE)
    u0_us = _air_float(message, name, "u0_us", 0.0, AIR_MAX_MEDIA_US)
    # Skips only add media time: the first frame's media time is never behind its index.
    if u0_us < seq * frame_us - AIR_U0_SLACK_US:
        raise InvalidCommand(f"{name} got a bad 'u0_us'")
    t0_us = _air_float(message, name, "t0_us")
    now = (now_raw_us or _raw_now_us)()
    if abs(t0_us - now) > AIR_T0_WINDOW_US:
        raise InvalidCommand(f"{name} got a bad 't0_us'")
    timestamp = _air_str_choice(message, name, "timestamp", AIR_TIMESTAMPS)
    rate = _air_int_choice(message, name, "rate", AIR_RATES)
    subbands = _air_int_choice(message, name, "subbands", AIR_SUBBANDS)
    blocks = _air_int_choice(message, name, "blocks", AIR_BLOCKS)
    bands = _air_int_list(message, name, "bands", 0, subbands - 1, subbands)
    if len(set(bands)) != len(bands):
        raise InvalidCommand(f"{name} got a bad 'bands'")
    complete_lag_us = _air_float(message, name, "complete_lag_us", *AIR_COMPLETE_LAG_US_RANGE, nullable=True)
    anchor_reset = _air_bool(message, name, "anchor_reset")
    e = _air_int_list(message, name, "e", 0, AIR_MAX_ENERGY, AIR_MAX_CELLS)
    return AirEnvelope(
        epoch=epoch,
        seq=seq,
        u0_us=u0_us,
        t0_us=t0_us,
        frame_us=frame_us,
        timestamp=timestamp,
        rate=rate,
        subbands=subbands,
        blocks=blocks,
        bands=bands,
        complete_lag_us=complete_lag_us,
        anchor_reset=anchor_reset,
        e=e,
    )


def _decode_air_source(message: dict) -> AirSource:
    name = "air_source"
    _air_version(message, name)
    state = _air_str_choice(message, name, "state", AIR_STATES)
    reason = _air_text(message, name, "reason", AIR_REASON_MAX)
    codec = _air_text(message, name, "codec", AIR_CODEC_MAX)
    adapter = _field(message, name, "adapter")
    if adapter is not None and (type(adapter) is not str or not _MAC_RE.fullmatch(adapter)):
        raise InvalidCommand(f"{name} got a bad 'adapter'")
    return AirSource(
        state=state,
        reason=reason,
        codec=codec,
        adapter=adapter,
        index=_air_int(message, name, "index", 0, AIR_MAX_HCI_INDEX, nullable=True),
        handle=_air_int(message, name, "handle", 0, AIR_MAX_ACL_HANDLE, nullable=True),
        in_step=_air_bool(message, name, "in_step"),
        monitor_drops=_air_int(message, name, "monitor_drops", 0, AIR_MAX_DROPS, nullable=True),
        ipc_dropped=_air_int(message, name, "ipc_dropped", 0, AIR_MAX_DROPS),
    )


def _decode_set_air_calibration(message: dict) -> SetAirCalibration:
    _air_version(message, "set_air_calibration")
    latency = _air_float(message, "set_air_calibration", "latency_ms", nullable=True)
    if latency is None:
        return SetAirCalibration(latency_ms=None)
    low, high = AIR_CALIBRATION_RANGE_MS
    return SetAirCalibration(latency_ms=max(low, min(high, latency)))


def _decode_air_fold(message: dict) -> AirFold:
    name = "air_fold"
    _air_version(message, name)
    fold_id = _field(message, name, "id")
    if type(fold_id) is not str or not AIR_FOLD_ID_RE.fullmatch(fold_id):
        raise InvalidCommand(f"{name} got a bad 'id'")
    from_us = _air_float(message, name, "from_us", 0.0)
    to_us = _air_float(message, name, "to_us", 0.0)
    if not 0.0 < to_us - from_us <= AIR_FOLD_MAX_SPAN_US:
        raise InvalidCommand(f"{name} got a bad span")
    return AirFold(id=fold_id, from_us=from_us, to_us=to_us)


def air_envelope_from_batch(batch: AirBatch) -> AirEnvelope:
    """The wire command for one tracker batch."""
    return AirEnvelope(
        epoch=batch.epoch,
        seq=batch.seq,
        u0_us=float(batch.u0_us),
        t0_us=float(batch.t0_us),
        frame_us=float(batch.frame_us),
        timestamp=batch.timestamp,
        rate=batch.rate,
        subbands=batch.subbands,
        blocks=batch.blocks,
        bands=tuple(batch.bands),
        complete_lag_us=None if batch.complete_lag_us is None else float(batch.complete_lag_us),
        anchor_reset=batch.anchor_reset,
        e=tuple(batch.e),
    )


def decode_command(message: Any, *, now_raw_us: Callable[[], float] | None = None) -> Command:
    """Turn one IPC message into a command, or say why it cannot be one.

    ``now_raw_us`` (``CLOCK_MONOTONIC_RAW`` µs by default) is read only for
    ``air_envelope``, whose ``t0_us`` must lie within ±10 s of now.
    """
    if not isinstance(message, dict):
        raise InvalidCommand(f"command message must be an object, got {type(message).__name__}")

    name = str(message.get("cmd") or "").strip()
    if not name:
        raise UnknownCommand("command message carries no 'cmd'")

    if name == "stop":
        return Stop()
    if name == "pause":
        return Pause()
    if name == "play":
        return Play()
    if name == "set_volume":
        return SetVolume(value=int(_clamped_number(message, "value", name, VOLUME_RANGE)))
    if name == "set_mute":
        if "muted" not in message:
            raise InvalidCommand("set_mute requires 'muted'")
        return SetMute(muted=bool(message["muted"]))
    if name == "reconnect":
        raw = message.get("delay", 0)
        try:
            delay = float(raw if raw is not None else 0)
        except (TypeError, ValueError) as exc:
            raise InvalidCommand(f"reconnect got a non-numeric 'delay': {raw!r}") from exc
        return Reconnect(delay_s=max(0.0, delay))
    if name == "set_log_level":
        level = str(message.get("level", "")).upper()
        if level not in VALID_LOG_LEVELS:
            raise InvalidCommand(f"set_log_level got an unknown level: {message.get('level')!r}")
        return SetLogLevel(level=level)
    if name == "set_static_delay_ms":
        return SetStaticDelayMs(value=_clamped_number(message, "value", name, STATIC_DELAY_RANGE_MS))
    if name == "set_required_lead_time_ms":
        return SetRequiredLeadTimeMs(value=_clamped_number(message, "value", name, BUFFER_RANGE_MS))
    if name == "set_min_buffer_ms":
        return SetMinBufferMs(value=_clamped_number(message, "value", name, BUFFER_RANGE_MS))
    if name == "transport":
        action = str(message.get("action") or "").strip()
        if not action:
            raise InvalidCommand("transport requires 'action'")
        return Transport(action=action, value=message.get("value"))
    if name == "set_standby":
        sink = message.get("sink")
        return SetStandby(sink=str(sink) if sink else None)
    if name == "open_pairing_window":
        return OpenPairingWindow()
    if name == "air_envelope":
        return _decode_air_envelope(message, now_raw_us)
    if name == "air_source":
        return _decode_air_source(message)
    if name == "set_air_calibration":
        return _decode_set_air_calibration(message)
    if name == "air_fold":
        return _decode_air_fold(message)

    raise UnknownCommand(f"unknown command: {name}")


def encode_command(command: Command) -> dict:
    """Turn a command back into the wire message the daemon reads."""
    if isinstance(command, Stop):
        return build_command_envelope("stop")
    if isinstance(command, Pause):
        return build_command_envelope("pause")
    if isinstance(command, Play):
        return build_command_envelope("play")
    if isinstance(command, SetVolume):
        return build_command_envelope("set_volume", value=command.value)
    if isinstance(command, SetMute):
        return build_command_envelope("set_mute", muted=command.muted)
    if isinstance(command, Reconnect):
        return build_command_envelope("reconnect", delay=command.delay_s)
    if isinstance(command, SetLogLevel):
        return build_command_envelope("set_log_level", level=command.level)
    if isinstance(command, SetStaticDelayMs):
        return build_command_envelope("set_static_delay_ms", value=command.value)
    if isinstance(command, SetRequiredLeadTimeMs):
        return build_command_envelope("set_required_lead_time_ms", value=command.value)
    if isinstance(command, SetMinBufferMs):
        return build_command_envelope("set_min_buffer_ms", value=command.value)
    if isinstance(command, Transport):
        return build_command_envelope("transport", action=command.action, value=command.value)
    if isinstance(command, SetStandby):
        return build_command_envelope("set_standby", sink=command.sink)
    if isinstance(command, OpenPairingWindow):
        return build_command_envelope("open_pairing_window")
    if isinstance(command, AirEnvelope):
        return build_command_envelope(
            "air_envelope",
            v=AIR_ENVELOPE_VERSION,
            epoch=command.epoch,
            seq=command.seq,
            u0_us=command.u0_us,
            t0_us=command.t0_us,
            frame_us=command.frame_us,
            timestamp=command.timestamp,
            rate=command.rate,
            subbands=command.subbands,
            blocks=command.blocks,
            bands=list(command.bands),
            complete_lag_us=command.complete_lag_us,
            anchor_reset=command.anchor_reset,
            e=list(command.e),
        )
    if isinstance(command, AirSource):
        return build_command_envelope(
            "air_source",
            v=AIR_ENVELOPE_VERSION,
            state=command.state,
            reason=command.reason,
            codec=command.codec,
            adapter=command.adapter,
            index=command.index,
            handle=command.handle,
            in_step=command.in_step,
            monitor_drops=command.monitor_drops,
            ipc_dropped=command.ipc_dropped,
        )
    if isinstance(command, SetAirCalibration):
        return build_command_envelope("set_air_calibration", v=AIR_ENVELOPE_VERSION, latency_ms=command.latency_ms)
    if isinstance(command, AirFold):
        return build_command_envelope(
            "air_fold", v=AIR_ENVELOPE_VERSION, id=command.id, from_us=command.from_us, to_us=command.to_us
        )

    raise InvalidCommand(f"cannot encode {type(command).__name__}")
