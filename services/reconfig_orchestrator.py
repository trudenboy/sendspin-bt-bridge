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
    ) -> None:
        self._loop = loop
        self._snapshot = snapshot

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
            # v1 scope: brand-new devices require a bridge restart because we
            # need to wire up BT managers, MA listeners, etc.  Surface this to
            # the UI so the user knows to click "restart bridge".
            summary.restart_required.append(action.to_summary())
        elif kind is ActionKind.GLOBAL_BROADCAST:
            self._apply_global_broadcast(action, clients_by_mac, summary)
        elif kind is ActionKind.GLOBAL_RESTART:
            self._apply_global_restart(action, clients_by_mac, summary)
        elif kind is ActionKind.RESTART_REQUIRED:
            summary.restart_required.append(action.to_summary())

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
        future = asyncio.run_coroutine_threadsafe(
            client.apply_hot_config(dict(action.payload)),
            self._loop,
        )
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
            "keepalive_interval": getattr(client, "keepalive_interval", 30),
            "idle_disconnect_minutes": getattr(client, "idle_disconnect_minutes", 0),
            "power_save_delay_minutes": getattr(client, "power_save_delay_minutes", 1),
        }


__all__ = ["ReconfigOrchestrator", "ReconfigSummary"]
