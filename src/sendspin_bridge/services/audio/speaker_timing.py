"""One per-speaker owner for timing commands, provenance and persistence.

The daemon command is the output adapter; the config id is the persistence identity. HTTP,
Home Assistant and the one-shot initializer use change(), while config reconfiguration uses
apply_runtime() after its own atomic config write. Server observations never echo a command.
"""

from __future__ import annotations

import asyncio
import functools
import math
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.bridge.exceptions import IPCError
from sendspin_bridge.services.ipc.commands import SetMinBufferMs, SetRequiredLeadTimeMs, SetStaticDelayMs

if TYPE_CHECKING:
    from collections.abc import Callable

FIELDS = frozenset({"static_delay_ms", "required_lead_time_ms", "min_buffer_ms"})
_COMMANDS = {
    "static_delay_ms": SetStaticDelayMs,
    "required_lead_time_ms": SetRequiredLeadTimeMs,
    "min_buffer_ms": SetMinBufferMs,
}


def validate_timing(field: str, value: object) -> float:
    maximum = 5000 if field == "static_delay_ms" else 30000
    if field not in FIELDS or isinstance(value, bool):
        raise ValueError("Unknown timing field or non-numeric value")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Timing value must be numeric") from exc
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError(f"{field} must be between 0 and {maximum}")
    return number


def refuse_external_timing(player_ids: set[str]) -> None:
    from sendspin_bridge.sync_tuning.session_store import timing_owner_for

    if owner := timing_owner_for(player_ids):
        raise UseCaseError(
            409,
            "session_active",
            "Finish the group's tuning session before changing its timing",
            extra={"session_id": owner},
        )


def recommendation_context(host: Any) -> dict[str, Any]:
    """The facts that make an absolute report inappropriate; no host probes or network calls."""
    from sendspin_bridge.services.music_assistant.ma_runtime_state import (
        get_ma_group_for_player_id,
        get_ma_player_id,
        is_ma_connected,
    )

    player_id = str(getattr(host, "player_id", "") or "")
    ma_id = str(getattr(host, "ma_player_id", "") or get_ma_player_id(player_id) or player_id)
    group = get_ma_group_for_player_id(ma_id)
    if group is None and player_id != ma_id:
        group = get_ma_group_for_player_id(player_id)
    resolve = getattr(host, "_resolve_air_target", None)
    target = resolve() if callable(resolve) else (getattr(host, "air_calibration_ms", None), None, None, None, None)
    return {
        "current_delay_ms": host.status.get("static_delay_ms", getattr(host, "static_delay_ms", 0)),
        "current_source": host.status.get("static_delay_source"),
        "air_target_active": target[0] is not None or target[2] is not None,
        "grouped": group is not None,
        "group_membership_known": bool(get_ma_player_id(player_id)) and is_ma_connected(),
    }


class SpeakerTiming:
    def __init__(
        self, host: Any, *, persist: Callable[..., None] | None = None, busy: Callable[[], bool] | None = None
    ):
        self.host = host
        self._persist_adapter = persist
        self._busy = busy
        self._lock = asyncio.Lock()
        self._applying = False
        self._pending_server: tuple[float, str] | None = None
        self._server_generation = 0
        self._write_epoch = 0
        self._write_lock = threading.Lock()
        self._epoch_lock = threading.Lock()

    def invalidate_pending_writes(self) -> int:
        """A newer accepted command/config write supersedes delayed observations."""
        with self._epoch_lock:
            self._write_epoch += 1
            return self._write_epoch

    def _current_epoch(self) -> int:
        with self._epoch_lock:
            return self._write_epoch

    def _persist_current(
        self, epoch: int, field: str, value: int, source: str, codec: str | None, *, set_at: str | None = None
    ) -> None:
        from sendspin_bridge.config import config_lock

        with self._write_lock, config_lock:
            if field != "static_delay_ms" or epoch == self._current_epoch():
                self._persist(field, value, source, codec, set_at=set_at)

    def _check(self) -> None:
        if self._busy is not None:
            if self._busy():
                raise UseCaseError(409, "session_active", "A tuning session owns this speaker's timing")
            return
        refuse_external_timing(self._identities())

    def _identities(self) -> set[str]:
        ids = {
            str(getattr(self.host, key, "") or "")
            for key in ("player_id", "config_id", "ma_player_id", "sendspin_client_id")
        }
        return ids - {""}

    def _persist(self, field: str, value: int, source: str, codec: str | None, *, set_at: str | None = None) -> None:
        if self._persist_adapter is not None:
            self._persist_adapter(field, value, source, codec)
            return
        from sendspin_bridge import config

        config_id = getattr(self.host, "config_id", None)
        mac = getattr(getattr(self.host, "bt_manager", None), "mac_address", None)
        if field == "static_delay_ms":
            if config_id:
                config.save_player_static_delay(
                    config_id, value, source=source, codec=codec, set_at=set_at, strict=True
                )
            elif mac:
                config.save_device_static_delay(mac, value, source=source, codec=codec, strict=True)
            else:
                raise ValueError("The speaker has no configuration identity")
        else:
            config.save_device_buffer_setting(mac, field, value, player_id=config_id, strict=True)

    async def _apply(self, field: str, value: float) -> None:
        self._applying = True
        try:
            if self.host.is_running():
                await self.host._send_subprocess_command(_COMMANDS[field](value=value))
            setattr(self.host, field, value if field == "static_delay_ms" else round(value))
            self.host._update_status({field: round(value)})
        finally:
            self._applying = False
            if self._pending_server is not None:
                pending, self._pending_server = self._pending_server, None
                self._observe(*pending)

    async def apply_runtime(self, field: str, value: object) -> None:
        number = validate_timing(field, 0 if value is None else value)
        async with self._lock:
            self._check()
            observed = self._server_generation
            await self._apply(field, number)
            if field == "static_delay_ms" and observed == self._server_generation:
                self.invalidate_pending_writes()

    async def change(self, field: str, value: object, *, source: str = "manual", codec: str | None = None) -> int:
        try:
            number = validate_timing(field, value)
        except ValueError as exc:
            raise UseCaseError(400, "out_of_range", str(exc)) from exc
        async with self._lock:
            return await self._change(field, number, source, codec)

    async def _change(self, field: str, number: float, source: str, codec: str | None) -> int:
        self._check()
        if source == "recommendation":
            from sendspin_bridge.services.audio.latency_recommendation import build_latency_recommendation

            policy = build_latency_recommendation(
                reported_bt_delay_ms=number, codec_name=codec, **recommendation_context(self.host)
            )
            if policy.value_ms is None or self.host.status.get("suggested_static_delay_ms") != round(number):
                raise UseCaseError(409, "stale_recommendation", "This speaker is already tuned; refresh its timing")
        observed = self._server_generation
        await self._apply(field, number)
        if field == "static_delay_ms" and observed != self._server_generation:
            if self.host.status.get(field) != round(number):
                raise UseCaseError(409, "timing_changed", "Music Assistant changed the delay during this operation")
            return round(number)
        if field == "static_delay_ms":
            self.invalidate_pending_writes()
            self.host._update_status(
                {
                    "static_delay_source": source,
                    "static_delay_codec": codec,
                    "static_delay_calibrated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        try:
            await asyncio.to_thread(self._persist_current, self._current_epoch(), field, round(number), source, codec)
        except (OSError, ValueError) as exc:
            raise UseCaseError(
                500,
                "config_write_failed",
                "Timing was applied but could not be saved; retry after checking configuration storage",
            ) from exc
        if field == "static_delay_ms" and observed != self._server_generation:
            if self.host.status.get(field) != round(number):
                raise UseCaseError(409, "timing_changed", "Music Assistant changed the delay during persistence")
            return round(number)
        if field == "static_delay_ms":
            if self.host.status.get("calibration_metronome_active"):
                await self.host.stop_calibration_metronome()
                await self.host.start_calibration_metronome()
        return round(number)

    async def initialize(self, recommendation: Any, codec: str | None) -> bool:
        from sendspin_bridge.services.audio.latency_recommendation import build_latency_recommendation

        async with self._lock:
            if self.host.status.get("static_delay_source") != "auto_pending" or recommendation.value_ms is None:
                return False
            policy = build_latency_recommendation(
                reported_bt_delay_ms=recommendation.value_ms, codec_name=codec, **recommendation_context(self.host)
            )
            if policy.value_ms is None:
                return False
            try:
                await self._change("static_delay_ms", float(recommendation.value_ms), recommendation.source, codec)
            except IPCError:
                return False
            except UseCaseError as exc:
                if exc.code == "session_active":
                    return False
                raise
            return True

    def observe_server(self, value: float, set_at: str | None = None) -> None:
        """Mirror a server value without echoing IPC; a concurrent command settles before the observation."""
        number = validate_timing("static_delay_ms", value)
        self._server_generation += 1
        at = set_at or datetime.now(timezone.utc).isoformat()
        if self._applying:
            self._pending_server = number, at
        else:
            self._observe(number, at)

    def _observe(self, value: float, set_at: str) -> None:
        self.host.static_delay_ms = value
        self.host._update_status(
            {
                "static_delay_ms": round(value),
                "static_delay_source": "music_assistant",
                "static_delay_calibrated_at": set_at,
                "static_delay_codec": None,
            }
        )
        epoch = self.invalidate_pending_writes()
        save = functools.partial(self._persist_observed, set_at=set_at, epoch=epoch)
        owner = str(getattr(self.host, "config_id", None) or getattr(self.host, "player_id", ""))
        self.host._schedule_persist("static_delay", save, owner, round(value))
        from sendspin_bridge.sync_tuning.session_store import invalidate_verification_for

        invalidate_verification_for(self._identities(), lambda: float(self.host.static_delay_ms))

    def _persist_observed(self, _owner: str, value: int, *, set_at: str, epoch: int) -> None:
        self._persist_current(epoch, "static_delay_ms", value, "music_assistant", None, set_at=set_at)


def timing_for(host: Any) -> SpeakerTiming:
    timing = getattr(host, "_speaker_timing", None)
    if timing is None:
        timing = SpeakerTiming(host)
        host._speaker_timing = timing
    return timing


def refuse_config_timing_changes(before: list[dict], after: list[dict]) -> list[SpeakerTiming]:
    """A full config save/import must respect the same active-session fence as dedicated writers."""
    from sendspin_bridge.config import _player_id_from_mac
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
    from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_player_id

    old = {p.get("id"): p for p in before if isinstance(p, dict)}

    def physical_timing(player: dict) -> tuple:
        return (
            player.get("static_delay_ms") or 0,
            player.get("required_lead_time_ms", 250),
            player.get("min_buffer_ms", 250),
            player.get("air_to_sound_latency") or [],
            player.get("air_learned_latency") or [],
        )

    superseded: list[SpeakerTiming] = []
    for player in after:
        previous = old.get(player.get("id"))
        if previous is None or physical_timing(previous) == physical_timing(player):
            continue
        ids = {str(player.get("id") or "")}
        mac = (player.get("backend") or {}).get("mac")
        if mac:
            ids.add(_player_id_from_mac(mac))
        for client in get_device_registry_snapshot().active_clients:
            if getattr(client, "config_id", None) in ids or getattr(client, "player_id", None) in ids:
                ids.update(
                    str(getattr(client, key, "") or "") for key in ("player_id", "ma_player_id", "sendspin_client_id")
                )
                if (previous.get("static_delay_ms") or 0) != (player.get("static_delay_ms") or 0):
                    superseded.append(timing_for(client))
        ids.update(get_ma_player_id(key) for key in tuple(ids))
        refuse_external_timing(ids - {""})
    return superseded
