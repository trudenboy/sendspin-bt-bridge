"""Single chokepoint for HA-originated commands.

Both the MQTT subscriber (``services/ha_mqtt_publisher.py``) and the REST
command surface used by the custom_component (``routes/api_status.py`` /
``routes/api_bt.py``) call into this module so command validation and
dispatch live in one place.

Validation honours the ``EntitySpec`` catalog: option lists are checked
against ``SELECT.options``, numbers clamped to ``min_value`` / ``max_value``,
unknown commands rejected.  This keeps each transport thin — they only
parse the wire format, then hand off here.
"""

from __future__ import annotations

import logging
from typing import Any

from services import bt_commands
from services.bt_commands import CommandResult
from services.ha_entity_model import (
    BRIDGE_ENTITIES,
    DEVICE_ENTITIES,
    EntityKind,
    EntitySpec,
    bridge_command_specs,
    device_command_specs,
)

logger = logging.getLogger(__name__)


def _err(error: str, *, code: int = 400, **details: Any) -> CommandResult:
    return CommandResult(success=False, error=error, code=code, details=details)


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------


def _validate_select_value(spec: EntitySpec, value: Any) -> tuple[Any, str | None]:
    text = "" if value is None else str(value).strip()
    if text not in spec.options:
        return None, (f"Invalid option {text!r} for {spec.object_id}; valid: {', '.join(spec.options)}")
    return text, None


def _validate_number_value(spec: EntitySpec, value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, f"Number {spec.object_id} requires a numeric value"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None, f"Value {value!r} is not numeric for {spec.object_id}"
    if spec.min_value is not None and num < spec.min_value:
        return None, f"Value {num} below min {spec.min_value} for {spec.object_id}"
    if spec.max_value is not None and num > spec.max_value:
        return None, f"Value {num} above max {spec.max_value} for {spec.object_id}"
    return num, None


def _coerce_switch_value(value: Any, spec: EntitySpec) -> bool:
    """Map MQTT ``ON``/``OFF`` payloads or JSON booleans to a clean bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {spec.payload_on.lower(), "on", "true", "1", "yes"}:
        return True
    if text in {spec.payload_off.lower(), "off", "false", "0", "no"}:
        return False
    # Fall back to truthy semantics so ``"1.0"`` / ``"True"`` etc. still work.
    return bool(text)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class HaCommandDispatcher:
    """Resolve HA commands → bridge actions.

    Stateless: holds no client references, looks them up on each call so
    config changes (added / removed devices) are picked up automatically.
    Construct one and reuse it across the MQTT publisher + REST routes.
    """

    def __init__(self) -> None:
        self._device_specs: dict[str, EntitySpec] = device_command_specs()
        self._bridge_specs: dict[str, EntitySpec] = bridge_command_specs()
        self._device_specs_by_object_id: dict[str, EntitySpec] = {s.object_id: s for s in DEVICE_ENTITIES}
        self._bridge_specs_by_object_id: dict[str, EntitySpec] = {s.object_id: s for s in BRIDGE_ENTITIES}

    # -- Device commands -------------------------------------------------

    def dispatch_device(self, player_id: str, command: str, value: Any | None = None) -> CommandResult:
        """Run ``command`` against the device identified by ``player_id``."""
        if not player_id:
            return _err("player_id is required")
        if not command:
            return _err("command is required")

        spec = self._device_specs.get(command)
        if spec is None:
            return _err(f"Unknown device command: {command}", code=404)

        client = bt_commands.find_client_by_player_id(player_id)
        # The ``set_enabled`` command is special — it can target a disabled
        # client that's not in the active registry.  Other commands require
        # an active client.
        if client is None and command != "set_enabled":
            return _err(f"Unknown player_id: {player_id}", code=404)

        try:
            return self._run_device(spec, client, player_id, command, value)
        except Exception as exc:
            logger.exception("HA device command %s failed for %s", command, player_id)
            return _err(f"Internal error: {exc}", code=500)

    def _run_device(
        self,
        spec: EntitySpec,
        client: Any,
        player_id: str,
        command: str,
        value: Any,
    ) -> CommandResult:
        # Buttons -----------------------------------------------------------
        if spec.kind is EntityKind.BUTTON:
            handler_name = _BUTTON_HANDLER_NAMES.get(command)
            if handler_name is None:
                return _err(f"No handler for button {command}", code=501)
            return _call_bt_helper(handler_name, client)

        # Switches ----------------------------------------------------------
        if spec.kind is EntityKind.SWITCH:
            target = _coerce_switch_value(value, spec)
            if command == "set_enabled":
                return _call_bt_helper("apply_device_enabled", player_id, target)
            if command == "set_bt_management":
                return _call_bt_helper("command_set_bt_management", client, target)
            return _err(f"No handler for switch {command}", code=501)

        # Selects -----------------------------------------------------------
        if spec.kind is EntityKind.SELECT:
            normalized, err = _validate_select_value(spec, value)
            if err:
                return _err(err)
            key = _SELECT_CONFIG_KEYS.get(command)
            if key is None:
                return _err(f"No config key mapped for {command}", code=501)
            return _call_bt_helper("apply_device_config_change", player_id, key, normalized)

        # Numbers -----------------------------------------------------------
        if spec.kind is EntityKind.NUMBER:
            num, err = _validate_number_value(spec, value)
            if err or num is None:
                return _err(err or "missing number")
            key = _NUMBER_CONFIG_KEYS.get(command)
            if key is None:
                return _err(f"No config key mapped for {command}", code=501)
            # Numbers persist as int when their step is integral — else float.
            value_to_persist: int | float = (
                int(num) if (spec.step or 1) >= 1 and float(num).is_integer() else float(num)
            )
            return _call_bt_helper("apply_device_config_change", player_id, key, value_to_persist)

        return _err(f"Unsupported command kind {spec.kind.value} for {command}", code=501)

    # -- Bridge commands -------------------------------------------------

    def dispatch_bridge(self, command: str, value: Any | None = None) -> CommandResult:
        if not command:
            return _err("command is required")
        spec = self._bridge_specs.get(command)
        if spec is None:
            return _err(f"Unknown bridge command: {command}", code=404)

        try:
            handler = _BRIDGE_HANDLERS.get(command)
            if handler is None:
                return _err(f"No handler for bridge command {command}", code=501)
            return handler(value)
        except Exception as exc:
            logger.exception("HA bridge command %s failed", command)
            return _err(f"Internal error: {exc}", code=500)

    # -- Introspection ----------------------------------------------------

    def known_device_commands(self) -> tuple[str, ...]:
        return tuple(self._device_specs.keys())

    def known_bridge_commands(self) -> tuple[str, ...]:
        return tuple(self._bridge_specs.keys())


# ---------------------------------------------------------------------------
# Handler tables
# ---------------------------------------------------------------------------


# Map button command → bt_commands function NAME.  Resolution is dynamic
# (``getattr(bt_commands, name)``) at call time so monkeypatched test
# doubles take effect — and so a future split of bt_commands across
# modules doesn't break the dispatcher.
_BUTTON_HANDLER_NAMES: dict[str, str] = {
    "reconnect": "command_reconnect",
    "disconnect": "command_disconnect",
    "wake": "command_wake",
    "standby": "command_standby",
    "power_save_toggle": "command_power_save_toggle",
    "claim_audio": "command_claim_audio",
    "reset_reconnect": "command_reset_reconnect",
    # ``pair`` is intentionally absent — pairing requires the speaker in
    # pairing mode and produces a one-shot result; no safe HA-automation
    # surface for it (see services/ha_entity_model.py).
}


def _call_bt_helper(name: str, *args: Any, **kwargs: Any) -> CommandResult:
    fn = getattr(bt_commands, name, None)
    if fn is None:
        return _err(f"bt_commands.{name} not found", code=501)
    return fn(*args, **kwargs)


_SELECT_CONFIG_KEYS = {
    "set_idle_mode": "idle_mode",
    "set_keep_alive_method": "keep_alive_method",
}

_NUMBER_CONFIG_KEYS = {
    "set_static_delay_ms": "static_delay_ms",
    "set_power_save_delay_minutes": "power_save_delay_minutes",
}


def _bridge_restart(_value: Any) -> CommandResult:
    """Restart the bridge process.

    Implementation note: a clean restart needs to flush daemons and let
    Supervisor re-spawn the container.  The web UI delegates to
    ``/api/restart`` (``routes/api.py``) which schedules a deferred
    ``os._exit``.  We replicate that here so HA isn't forced to call the
    REST endpoint with a token round-trip.
    """
    import os
    import threading

    def _shutdown() -> None:
        threading.Event().wait(0.5)
        os._exit(0)

    threading.Thread(target=_shutdown, daemon=True).start()
    return CommandResult(success=True, message="Bridge restart scheduled")


def _bridge_scan(_value: Any) -> CommandResult:
    """Trigger a BT scan.

    Heavy operation — the existing ``/api/bt/scan`` endpoint runs it in a
    background job with TTL-tracked results.  For HA we only ack scheduling;
    operators inspect results in the web UI.
    """
    try:
        # Best-effort: try to kick off a scan via routes/api_bt's job
        # machinery if it exposes one; otherwise fall back to ack-only.
        # The route module is imported by name so mypy doesn't insist
        # on a concrete attribute that may not exist on older builds.
        import importlib

        api_bt_mod = importlib.import_module("routes.api_bt")
        starter = getattr(api_bt_mod, "_start_bt_scan_job", None)
        if callable(starter):
            job_id = starter()
            return CommandResult(success=True, message="Scan started", details={"job_id": job_id})
    except Exception as exc:
        logger.debug("Bridge scan dispatch fell back: %s", exc)
    # Without a job-pipeline available we still want callers to know
    # the command was acknowledged but no job tracking exists.
    return CommandResult(
        success=True,
        message="Scan acknowledged (no job pipeline available)",
        details={"job_id": None},
    )


_BRIDGE_HANDLERS = {
    "restart": _bridge_restart,
    # ``scan`` is intentionally absent from the HA dispatcher — scan
    # results only matter inside the bridge web UI's pair-flow modal,
    # which HA can't open (see services/ha_entity_model.py).  The
    # ``_bridge_scan`` helper above stays in the module so future
    # surfaces (e.g. a programmatic API) can reuse it.
}


# Module-level singleton for convenience.
_default_dispatcher: HaCommandDispatcher | None = None


def get_default_dispatcher() -> HaCommandDispatcher:
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = HaCommandDispatcher()
    return _default_dispatcher


__all__ = [
    "CommandResult",
    "HaCommandDispatcher",
    "get_default_dispatcher",
]
