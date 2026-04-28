"""Dispatch :class:`~services.config_diff.ReconfigAction` lists to running clients.

The orchestrator is invoked from a Flask request thread (``POST /api/config``)
and needs to schedule per-client work on the asyncio event loop that owns each
daemon subprocess.  Hot-apply actions are awaited synchronously (they are
near-instant IPC writes), while warm restarts are dispatched as fire-and-forget
background coroutines so the HTTP response returns quickly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from services.config_diff import ActionKind, ReconfigAction

if TYPE_CHECKING:
    from concurrent.futures import Future

    from services.device_activation import DeviceActivationContext
    from services.device_registry import DeviceRegistrySnapshot

logger = logging.getLogger(__name__)

# How long to wait for hot-apply IPC to flush before falling back to background
# dispatch.  IPC writes finish in <50 ms in practice but we give 500 ms of slack
# so the request handler never hangs on a busy event loop.
_HOT_APPLY_TIMEOUT_S = 0.5


# ---------------------------------------------------------------------------
# Summary models
# ---------------------------------------------------------------------------


@dataclass
class ReconfigSummary:
    hot_applied: list[dict[str, Any]] = field(default_factory=list)
    warm_restarting: list[dict[str, Any]] = field(default_factory=list)
    global_broadcast: list[dict[str, Any]] = field(default_factory=list)
    global_restart: list[dict[str, Any]] = field(default_factory=list)
    restart_required: list[dict[str, Any]] = field(default_factory=list)
    bt_removed: list[dict[str, Any]] = field(default_factory=list)
    started: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[dict[str, Any]] = field(default_factory=list)
    ha_integration_reloaded: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hot": self.hot_applied,
            "warm_restarting": self.warm_restarting,
            "global_broadcast": self.global_broadcast,
            "global_restart": self.global_restart,
            "restart_required": self.restart_required,
            "bt_removed": self.bt_removed,
            "started": self.started,
            "stopped": self.stopped,
            "ha_integration_reloaded": self.ha_integration_reloaded,
            "errors": self.errors,
        }

    @property
    def has_any_change(self) -> bool:
        return any(
            (
                self.hot_applied,
                self.warm_restarting,
                self.global_broadcast,
                self.global_restart,
                self.restart_required,
                self.bt_removed,
                self.started,
                self.stopped,
                self.ha_integration_reloaded,
            )
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class ReconfigOrchestrator:
    """Apply a list of :class:`ReconfigAction` against the running bridge.

    Parameters
    ----------
    loop
        The asyncio event loop that owns the per-client daemon subprocesses.
        Used to schedule coroutines from the calling (Flask) thread.
    snapshot
        Thread-safe snapshot of the device registry captured immediately
        before dispatch.  The orchestrator only touches clients present in
        the snapshot; newly-added devices (``START_CLIENT``) are deferred
        to a bridge restart because wiring up fresh listeners is out of
        scope for the v1 hot-apply path.

    Notes
    -----
    ``BT_REMOVE`` actions are recorded in the summary only.  Actually
    disconnecting / unpairing the BT device is already handled inline by
    the ``/api/config`` handler via :func:`services.bluetooth.bt_remove_device`
    because the handler has the live adapter MAC (from
    ``bt_manager._adapter_select``) that ``bt_remove_device`` needs.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None,
        snapshot: DeviceRegistrySnapshot,
        *,
        activation_context: DeviceActivationContext | None = None,
    ) -> None:
        self._loop = loop
        self._snapshot = snapshot
        # Optional so unit tests that don't exercise START_CLIENT can
        # continue to instantiate the orchestrator with just (loop, snapshot).
        self._activation_context = activation_context

    # -- public API ------------------------------------------------------

    def apply(self, actions: list[ReconfigAction]) -> ReconfigSummary:
        """Execute every action in ``actions`` and return a summary."""
        summary = ReconfigSummary()
        if not actions:
            return summary

        # MAC strings arrive uppercase from config validation but the BT
        # manager may cache them in whatever case the adapter reported,
        # so index case-insensitively.
        clients_by_mac = {(mac or "").upper(): client for mac, client in self._snapshot.client_map_by_mac().items()}

        for action in actions:
            try:
                self._dispatch(action, clients_by_mac, summary)
            except Exception as exc:
                logger.exception("Reconfig action %s for %s failed", action.kind.value, action.label or action.mac)
                summary.errors.append(
                    {
                        "kind": action.kind.value,
                        "mac": action.mac,
                        "label": action.label,
                        "fields": list(action.fields),
                        "error": str(exc),
                    }
                )

        return summary

    # -- dispatch --------------------------------------------------------

    def _dispatch(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        kind = action.kind
        if kind is ActionKind.HOT_APPLY:
            self._apply_hot(action, clients_by_mac, summary)
        elif kind is ActionKind.WARM_RESTART:
            self._apply_warm(action, clients_by_mac, summary)
        elif kind is ActionKind.BT_REMOVE:
            self._apply_bt_remove(action, summary)
        elif kind is ActionKind.STOP_CLIENT:
            self._apply_stop(action, clients_by_mac, summary)
        elif kind is ActionKind.START_CLIENT:
            self._apply_start_client(action, clients_by_mac, summary)
        elif kind is ActionKind.GLOBAL_BROADCAST:
            self._apply_global_broadcast(action, clients_by_mac, summary)
        elif kind is ActionKind.GLOBAL_RESTART:
            self._apply_global_restart(action, clients_by_mac, summary)
        elif kind is ActionKind.RESTART_REQUIRED:
            summary.restart_required.append(action.to_summary())
        elif kind is ActionKind.HA_INTEGRATION_LIFECYCLE:
            self._apply_ha_integration_lifecycle(action, summary)

    # -- individual kinds ------------------------------------------------

    def _apply_hot(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        client = clients_by_mac.get((action.mac or "").upper())
        if client is None or self._loop is None:
            # Device isn't running — values already persisted; nothing to do.
            summary.hot_applied.append(action.to_summary())
            return
        # Build the coroutine up-front so we can close it if scheduling fails
        # (e.g., the asyncio loop is shutting down), preventing the
        # "coroutine was never awaited" RuntimeWarning and the associated leak.
        coro = client.apply_hot_config(dict(action.payload))
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError as exc:
            coro.close()
            logger.warning(
                "Could not schedule hot-apply for %s: %s",
                action.label or action.mac,
                exc,
            )
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"hot-apply schedule failed ({type(exc).__name__})",
                }
            )
            return
        try:
            future.result(timeout=_HOT_APPLY_TIMEOUT_S)
        except Exception as exc:
            # IPC didn't flush within the HTTP-response window.  Report as an
            # error so the UI doesn't falsely claim "Applied live", and attach
            # a completion callback so a late exception is logged rather than
            # swallowed.  The coroutine keeps running in the background, so
            # the change may still land — the UI just won't over-promise.
            logger.warning(
                "Hot-apply for %s did not complete within %.1fs: %s — continuing in background",
                action.label or action.mac,
                _HOT_APPLY_TIMEOUT_S,
                exc,
            )
            late_label = action.label or action.mac

            def _log_late_failure(done_future: Future, _lbl: str | None = late_label) -> None:
                late_exc = done_future.exception()
                if late_exc is not None:
                    logger.warning("Late hot-apply failure for %s: %s", _lbl, late_exc)

            future.add_done_callback(_log_late_failure)
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"hot-apply pending ({type(exc).__name__})",
                }
            )
            return
        summary.hot_applied.append(action.to_summary())

    def _apply_warm(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        client = clients_by_mac.get((action.mac or "").upper())
        if client is None or self._loop is None:
            summary.warm_restarting.append(action.to_summary())
            return
        device_payload = action.payload.get("device") or {}
        self._schedule_background(
            client.warm_restart(dict(device_payload)),
            description=f"warm_restart:{action.label or action.mac}",
        )
        summary.warm_restarting.append(action.to_summary())

    def _apply_bt_remove(self, action: ReconfigAction, summary: ReconfigSummary) -> None:
        # The BT stack cleanup is performed inline by the /api/config handler
        # because it has the live adapter MAC.  Here we only record the action
        # so the UI summary can mention it.
        summary.bt_removed.append(action.to_summary())

    def _apply_stop(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        client = clients_by_mac.get((action.mac or "").upper())
        if client is None:
            summary.stopped.append(action.to_summary())
            return
        # Release BT management so the bt_monitor won't reclaim the adapter
        # and re-spawn the daemon on the next reconnect.  This is synchronous
        # and internally schedules ``stop_sendspin`` on the asyncio loop when
        # one is running, mirroring what the /api/bt/release endpoint does.
        try:
            client.set_bt_management_enabled(False)
        except Exception as exc:
            logger.warning(
                "set_bt_management_enabled(False) failed for %s: %s",
                action.label or action.mac,
                exc,
            )
            # Fall back to stop_sendspin so we at least kill the daemon now;
            # bt_monitor may still re-spawn but that's better than leaving it.
            if self._loop is not None:
                self._schedule_background(
                    client.stop_sendspin(),
                    description=f"stop_sendspin:{action.label or action.mac}",
                )
        summary.stopped.append(action.to_summary())

    def _apply_start_client(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        """Materialize a brand-new SendspinClient for a just-added device.

        Falls back to ``summary.restart_required`` (the legacy behaviour)
        when the activation context or main loop isn't available — typically
        in unit tests that instantiate the orchestrator directly without
        going through :mod:`bridge_orchestrator`.
        """
        context = self._activation_context
        if context is None or self._loop is None:
            summary.restart_required.append(action.to_summary())
            return

        mac_key = (action.mac or "").upper()
        existing_client = clients_by_mac.get(mac_key) if mac_key else None
        if existing_client is not None:
            # Re-enable path: a client whose BT management was released at
            # runtime (``enabled: false`` flipped via UI → STOP_CLIENT) keeps
            # its entry in the registry with ``bt_management_enabled=False``.
            # Flipping ``enabled`` back to ``true`` at save time emits a fresh
            # START_CLIENT — don't build a new client, just reclaim management
            # on the existing one. ``set_bt_management_enabled(True)`` is the
            # same call the ``/api/bt/management`` route uses and it's safe
            # from the Flask request thread.
            bt_management = bool(getattr(existing_client, "bt_management_enabled", True))
            if not bt_management:
                try:
                    existing_client.set_bt_management_enabled(True)
                except Exception as exc:
                    logger.exception("re-enable via START_CLIENT failed for %s", action.label or action.mac)
                    summary.errors.append(
                        {
                            "kind": action.kind.value,
                            "mac": action.mac,
                            "label": action.label,
                            "fields": list(action.fields),
                            "error": f"re-enable failed: {exc}",
                        }
                    )
                    return
                summary.started.append(action.to_summary())
                return
            # Race guard: two POST /api/config calls with the same new MAC.
            # The first one won; second one no-ops loudly instead of
            # duplicating the client and confusing the registry.
            logger.warning(
                "START_CLIENT for %s ignored — a client for this MAC is already active",
                action.label or action.mac,
            )
            return

        device_payload = action.payload.get("device") or {}
        if not isinstance(device_payload, dict):
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": "START_CLIENT payload missing 'device' dict",
                }
            )
            return

        # Read the live registry to compute the device_index fallback.  The
        # actual append happens inside ``mutate_active_clients`` below, which
        # re-reads the list under the registry lock to avoid the
        # read-modify-write race against concurrent ``POST /api/config``
        # request threads (Waitress runs requests in parallel).
        import state as _state
        from services.device_registry import mutate_active_clients

        try:
            existing_clients = list(_state.get_clients_snapshot())
        except Exception as exc:
            logger.exception("START_CLIENT registry read failed for %s", action.label or action.mac)
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"registry read failed: {exc}",
                }
            )
            return

        # Prefer the explicit ``device_index`` the diff computed over
        # ``len(existing_clients)``.  Position in BLUETOOTH_DEVICES is what
        # the startup path uses for the ``base_listen_port + index`` fallback
        # when a device didn't pin its own ``listen_port``; falling back to
        # the live-clients length would assign a different port (and risk
        # collision with a disabled device's implicit port).
        payload_index = action.payload.get("device_index")
        device_index = (
            int(payload_index) if isinstance(payload_index, int) and payload_index >= 0 else len(existing_clients)
        )

        try:
            from services.device_activation import activate_device

            # Fall through to the context's default player name (the
            # startup path captured ``Sendspin-<hostname>`` / ``$SENDSPIN_NAME``
            # / ``client_factory`` override there) — not a hardcoded
            # "Sendspin" — so online-activation names match what a bridge
            # restart would produce, keeping MA/UI identity stable across
            # restarts when ``player_name`` is absent from the config entry.
            resolved_default_name = str(device_payload.get("player_name") or context.default_player_name)
            result = activate_device(
                device_payload,
                index=device_index,
                context=context,
                default_player_name=resolved_default_name,
            )
        except Exception as exc:
            logger.exception("START_CLIENT activation failed for %s", action.label or action.mac)
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"activation failed: {exc}",
                }
            )
            return

        # Atomic append under the registry lock — re-reads the live list
        # inside ``mutate_active_clients`` so a peer ``POST /api/config``
        # request that appended its own client between our snapshot read
        # and our write doesn't get clobbered.
        def _append_new_client(current: list[Any]) -> list[Any]:
            # Race guard: a parallel request may have already added a client
            # for the same MAC. If so, drop our just-built duplicate to avoid
            # two clients fighting for the same adapter.
            if mac_key:
                for existing in current:
                    existing_mac = (getattr(getattr(existing, "bt_manager", None), "mac_address", "") or "").upper()
                    if existing_mac == mac_key:
                        logger.warning(
                            "START_CLIENT for %s lost the append race — peer request already created a client; dropping duplicate",
                            action.label or action.mac,
                        )
                        return current
            return [*current, result.client]

        try:
            updated_snapshot = mutate_active_clients(_append_new_client)
        except Exception as exc:
            logger.exception("START_CLIENT registry update failed for %s", action.label or action.mac)
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"registry update failed: {exc}",
                }
            )
            return

        # If the race-guard inside the mutator dropped our client, abort
        # before scheduling its run() — there's another active client for
        # this MAC already, and double-running ``client.run()`` against
        # the same BT adapter would race the daemon spawn.
        if not any(c is result.client for c in updated_snapshot.active_clients):
            return

        # Kick the main-loop coroutine that spawns the daemon subprocess and
        # starts the BT monitor.  Fire-and-forget with a rollback callback
        # so a startup exception doesn't leave a zombie registry entry.
        coro = result.client.run()
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError as exc:
            coro.close()
            # Loop stopped mid-request — roll our just-appended client back
            # out of the registry.  Atomic remove-by-identity so we don't
            # clobber peer appends made by other concurrent request threads.
            try:

                def _rollback_pop(current: list[Any]) -> list[Any]:
                    return [c for c in current if c is not result.client]

                mutate_active_clients(_rollback_pop)
            except Exception as revert_exc:  # pragma: no cover — best effort
                logger.debug("registry rollback after schedule failure: %s", revert_exc)
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "mac": action.mac,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"run schedule failed ({type(exc).__name__})",
                }
            )
            return

        label = action.label or action.mac or "new client"

        def _on_run_done(done_future: Future, _mac: str = mac_key, _label: str = label) -> None:
            try:
                done_future.result()
            except Exception as run_exc:
                logger.warning("client.run() for %s exited with error: %s", _label, run_exc)
                self._rollback_started_client(_mac)

        future.add_done_callback(_on_run_done)
        # Keep the dispatch-time MAC index consistent with the live
        # registry so a subsequent action for the same MAC within this
        # apply() call (e.g. a second duplicate START_CLIENT, or an
        # immediate HOT_APPLY added alongside the START_CLIENT) sees the
        # fresh client instead of re-running activate_device.
        if mac_key:
            clients_by_mac[mac_key] = result.client
        summary.started.append(action.to_summary())

    def _rollback_started_client(self, mac_key: str) -> None:
        """Remove a client that failed its ``run()`` bootstrap from the registry."""
        try:
            import state as _state
            from services.device_registry import mutate_active_clients
        except Exception:  # pragma: no cover — imports must succeed in practice
            return

        removed: list[bool] = [False]

        def _drop_by_mac(current: list[Any]) -> list[Any]:
            surviving = [
                c
                for c in current
                if (getattr(getattr(c, "bt_manager", None), "mac_address", "") or "").upper() != mac_key
            ]
            if len(surviving) != len(current):
                removed[0] = True
            return surviving

        try:
            mutate_active_clients(_drop_by_mac)
        except Exception as exc:  # pragma: no cover
            logger.debug("rollback mutate_active_clients failed: %s", exc)
            return
        if removed[0]:
            try:
                _state.notify_status_changed()
            except Exception as exc:  # pragma: no cover
                logger.debug("rollback notify_status_changed failed: %s", exc)

    def _apply_global_broadcast(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        payload = dict(action.payload)
        log_level = payload.get("LOG_LEVEL")
        if isinstance(log_level, str) and self._loop is not None:
            level_upper = log_level.upper()
            cmd = {"cmd": "set_log_level", "level": level_upper}
            for client in clients_by_mac.values():
                if not getattr(client, "is_running", None):
                    continue
                if not client.is_running():
                    continue
                self._schedule_background(
                    client._send_subprocess_command(cmd),
                    description=f"set_log_level:{getattr(client, 'player_name', '?')}",
                )
        summary.global_broadcast.append(action.to_summary())

    def _apply_global_restart(
        self,
        action: ReconfigAction,
        clients_by_mac: dict[str, Any],
        summary: ReconfigSummary,
    ) -> None:
        if self._loop is None:
            summary.global_restart.append(action.to_summary())
            return
        for client in clients_by_mac.values():
            if not getattr(client, "is_running", None):
                continue
            if not client.is_running():
                continue
            device = self._build_device_snapshot_from_client(client)
            self._schedule_background(
                client.warm_restart(device),
                description=f"global_restart:{getattr(client, 'player_name', '?')}",
            )
        summary.global_restart.append(action.to_summary())

    def _apply_ha_integration_lifecycle(self, action: ReconfigAction, summary: ReconfigSummary) -> None:
        """Reload the HA integration subsystem (MQTT publisher / mDNS).

        Routes through the dedicated lifecycle holder.  No client touched.
        """
        try:
            from services.ha_integration_lifecycle import get_default_lifecycle
        except Exception as exc:  # pragma: no cover
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"lifecycle import failed ({type(exc).__name__})",
                }
            )
            return
        lifecycle = get_default_lifecycle()
        if lifecycle is None or self._loop is None:
            # Not yet wired (early boot or non-runtime context) — record as
            # a pending reload so the orchestrator caller can re-trigger
            # once startup completes.
            summary.ha_integration_reloaded.append(action.to_summary())
            return
        try:
            asyncio.run_coroutine_threadsafe(lifecycle.reload(), self._loop)
        except RuntimeError as exc:
            summary.errors.append(
                {
                    "kind": action.kind.value,
                    "label": action.label,
                    "fields": list(action.fields),
                    "error": f"lifecycle reload schedule failed ({type(exc).__name__})",
                }
            )
            return
        summary.ha_integration_reloaded.append(action.to_summary())

    # -- helpers ---------------------------------------------------------

    def _schedule_background(self, coro, *, description: str) -> Future | None:
        if self._loop is None:
            coro.close()
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError as exc:
            coro.close()
            logger.warning("Could not schedule %s: %s", description, exc)
            return None

        def _log_completion(done_future: Future) -> None:
            try:
                done_future.result()
            except Exception as exc:
                logger.warning("%s failed: %s", description, exc)

        future.add_done_callback(_log_completion)
        return future

    @staticmethod
    def _build_device_snapshot_from_client(client: Any) -> dict[str, Any]:
        """Reconstruct a minimal device dict from the client's current state.

        Used by ``GLOBAL_RESTART`` where the changed field is something
        global (PULSE_LATENCY_MSEC, SENDSPIN_SERVER, ...) and we just want
        the subprocess to respawn with its *existing* per-device params.
        """
        return {
            "player_name": getattr(client, "player_name", None),
            "listen_port": getattr(client, "listen_port", None),
            "listen_host": getattr(client, "listen_host", None),
            "preferred_format": getattr(client, "preferred_format", None),
            "static_delay_ms": getattr(client, "static_delay_ms", None),
            "idle_mode": getattr(client, "idle_mode", "default"),
            # Include the current keepalive flag so a GLOBAL_RESTART doesn't
            # accidentally flip it off: _apply_warm_restart_fields re-derives
            # keepalive_enabled from (idle_mode, explicit flag) whenever either
            # key is present, and this snapshot always carries idle_mode.
            "keepalive_enabled": getattr(client, "keepalive_enabled", False),
            "keepalive_interval": getattr(client, "keepalive_interval", 30),
            "idle_disconnect_minutes": getattr(client, "idle_disconnect_minutes", 0),
            "power_save_delay_minutes": getattr(client, "power_save_delay_minutes", 1),
        }


__all__ = ["ReconfigOrchestrator", "ReconfigSummary"]
