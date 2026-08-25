"""When a speaker should be let go, and when it should be woken.

A device can be configured to release its Bluetooth link (``auto_disconnect``),
suspend its PulseAudio sink (``power_save``), hold the link open with
inaudible bursts (``keep_alive``), or do nothing (``default``).  Deciding
which of those applies is arithmetic over a handful of flags — is audio
flowing, is the server connected, is the speaker already in standby — but the
branches were spread across ``_update_status``, two sink callbacks and three
timer starters, each reading ``self.status`` and firing side effects inline.

So the decisions could only be reached through a live client: the tests for
them built one via ``SendspinClient.__new__`` and wired six private
attributes by hand before they could ask a single question.

Here the decisions are the whole module and come back as data.  Arming a
timer, suspending a sink, entering standby — all of that stays with the
client, which is the thing that owns the timers and the subprocess.

There are two authorities for "audio stopped": PulseAudio sink events, which
are fastest where they are reliable, and the daemon's own flags.  Both feed
this machine, and overlapping decisions are harmless because arming a timer
cancels the previous one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ArmIdleTimer",
    "ArmPowerSaveTimer",
    "ArmSinkMuteWatchdog",
    "CancelIdleTimer",
    "CancelPowerSaveTimer",
    "CancelSinkMuteWatchdog",
    "ExitPowerSave",
    "IdleAction",
    "IdleMachine",
    "PlaybackState",
]

#: Modes that can release or suspend the speaker.  ``default`` and
#: ``keep_alive`` never do, so the machine has nothing to say about them.
RELEASING_MODES = frozenset({"auto_disconnect", "power_save"})


@dataclass(frozen=True)
class PlaybackState:
    """The flags every idle decision is made from."""

    playing: bool = False
    audio_streaming: bool = False
    server_connected: bool = False
    bt_standby: bool = False
    bt_power_save: bool = False
    sink_muted: bool = False
    muted: bool = False

    @property
    def audio_active(self) -> bool:
        """True while anything is coming out of the speaker."""
        return self.playing or self.audio_streaming

    @classmethod
    def from_status(cls, status) -> PlaybackState:
        """Read the flags out of a device status (or any mapping)."""
        return cls(
            playing=bool(status.get("playing", False)),
            audio_streaming=bool(status.get("audio_streaming", False)),
            server_connected=bool(status.get("server_connected", False)),
            bt_standby=bool(status.get("bt_standby", False)),
            bt_power_save=bool(status.get("bt_power_save", False)),
            sink_muted=bool(status.get("sink_muted", False)),
            muted=bool(status.get("muted", False)),
        )


@dataclass(frozen=True)
class CancelIdleTimer:
    """Audio is flowing — the speaker is not idle after all."""


@dataclass(frozen=True)
class CancelPowerSaveTimer:
    """Same, for the sink-suspend timer."""


@dataclass(frozen=True)
class ArmIdleTimer:
    """Release the Bluetooth link if nothing happens for *delay_s*."""

    delay_s: float


@dataclass(frozen=True)
class ArmPowerSaveTimer:
    """Suspend the PulseAudio sink if nothing happens for *delay_s*."""

    delay_s: float


@dataclass(frozen=True)
class ExitPowerSave:
    """Resume a sink that was suspended."""


@dataclass(frozen=True)
class ArmSinkMuteWatchdog:
    """Watch a sink that is muted without us having asked for it."""


@dataclass(frozen=True)
class CancelSinkMuteWatchdog:
    """Stop watching."""


IdleAction = (
    CancelIdleTimer
    | CancelPowerSaveTimer
    | ArmIdleTimer
    | ArmPowerSaveTimer
    | ExitPowerSave
    | ArmSinkMuteWatchdog
    | CancelSinkMuteWatchdog
)


class IdleMachine:
    """Decides what an idle speaker deserves. Executes nothing."""

    def __init__(
        self,
        *,
        mode: str,
        idle_disconnect_minutes: int,
        power_save_delay_minutes: int,
        sink_monitor_active: Callable[[], bool],
    ):
        self.mode = mode
        self.idle_disconnect_minutes = idle_disconnect_minutes
        self.power_save_delay_minutes = power_save_delay_minutes
        self._sink_monitor_active = sink_monitor_active

    # -- the delays ----------------------------------------------------

    @property
    def idle_delay_s(self) -> float:
        return self.idle_disconnect_minutes * 60

    @property
    def power_save_delay_s(self) -> float:
        return self.power_save_delay_minutes * 60

    def _arm_for_mode(self) -> list[IdleAction]:
        """The timer this mode arms when the speaker goes quiet."""
        if self.mode == "auto_disconnect":
            return [ArmIdleTimer(delay_s=self.idle_delay_s)]
        if self.mode == "power_save":
            return [ArmPowerSaveTimer(delay_s=self.power_save_delay_s)]
        return []

    # -- the daemon's own flags ----------------------------------------

    def on_playback_change(self, *, previous: PlaybackState, current: PlaybackState) -> list[IdleAction]:
        """Audio started or stopped, according to the daemon."""
        if self.mode not in RELEASING_MODES:
            return []
        if previous.audio_active == current.audio_active:
            return []

        if current.audio_active:
            actions: list[IdleAction] = [CancelIdleTimer()]
            if self.mode == "power_save":
                actions.append(CancelPowerSaveTimer())
                if current.bt_power_save:
                    actions.append(ExitPowerSave())
            return actions

        if current.bt_standby:
            return []
        return self._arm_for_mode()

    def on_server_connected(self, current: PlaybackState) -> list[IdleAction]:
        """The server connected and nothing is playing.

        A fallback for hosts where PulseAudio events are unreliable: when the
        sink monitor is running it has already made this call at registration
        time, and a second opinion would only duplicate it.
        """
        if self.mode not in RELEASING_MODES:
            return []
        if self._sink_monitor_active():
            return []
        if not current.server_connected or current.audio_active or current.bt_standby:
            return []
        return self._arm_for_mode()

    # -- PulseAudio sink events ----------------------------------------

    def on_sink_active(self, current: PlaybackState) -> list[IdleAction]:
        """The sink entered ``running`` — audio is flowing."""
        if self.mode == "auto_disconnect":
            return [CancelIdleTimer()]
        if self.mode == "power_save":
            actions: list[IdleAction] = [CancelPowerSaveTimer()]
            if current.bt_power_save:
                actions.append(ExitPowerSave())
            return actions
        return []

    def on_sink_idle(self, current: PlaybackState) -> list[IdleAction]:
        """The sink left ``running`` — the speaker has gone quiet."""
        if self.mode not in RELEASING_MODES or current.bt_standby:
            return []
        return self._arm_for_mode()

    # -- the sink-mute watchdog ----------------------------------------

    def on_sink_mute_change(self, current: PlaybackState) -> list[IdleAction]:
        """A sink muted without us asking is a sink drifting out from under us."""
        if current.sink_muted and not current.muted:
            return [ArmSinkMuteWatchdog()]
        return [CancelSinkMuteWatchdog()]
