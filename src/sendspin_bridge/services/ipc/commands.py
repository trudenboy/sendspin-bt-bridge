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

from dataclasses import dataclass
from typing import Any

from sendspin_bridge.services.ipc.ipc_protocol import build_command_envelope

__all__ = [
    "Command",
    "CommandError",
    "InvalidCommand",
    "Pause",
    "Play",
    "Reconnect",
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


def decode_command(message: Any) -> Command:
    """Turn one IPC message into a command, or say why it cannot be one."""
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

    raise InvalidCommand(f"cannot encode {type(command).__name__}")
