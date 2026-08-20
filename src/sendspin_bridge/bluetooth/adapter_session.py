"""The adapter session — one owner for every mutating BlueZ verb.

BlueZ has a single discovery / agent state machine per host, so two
operations driving a controller at once corrupt each other (one's ``scan
off`` cancels the other's discovery window).  The bridge used to guard that
with a free-standing pair of lock functions plus a convention that callers
also remember to hop off the event loop; five call sites remembered and
three did not.

Here the guard is the object.  :class:`AdapterHandle` hands out an exclusive
:class:`Lease`, and the mutating verbs live only on the :class:`AdapterSession`
a lease carries — "forgot to take the lock" stops being expressible.
:attr:`Lease.async_session` exposes the same verbs as coroutines that run in
the Bluetooth executor, so a coroutine cannot accidentally block the shared
event loop on a 10-second ``bluetoothctl`` deadline.

Two further properties this module owns:

* **Link state is tri-state.**  A timed-out or unavailable transport answers
  :attr:`LinkState.UNKNOWN`, never ``DISCONNECTED`` — reporting a transport
  failure as a confirmed disconnect used to tear down the speaker's MPRIS
  player and feed the reconnect-churn counter.
* **Adapter identity has one ladder.**  ``hciN`` is resolved through the
  kernel's own map (:meth:`BluezControl.hci_map`), lazily and repeatedly, so
  a bridge that started before ``bluetoothd`` is not stuck without a D-Bus
  path for the rest of the process's life.

The lease is released by *token*, not by thread: the HTTP layer acquires it
on a request thread and hands it to a worker thread that releases it when the
operation completes.  A stale release therefore cannot free a lease somebody
else is holding.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import TYPE_CHECKING, Any

from sendspin_bridge.bluetooth.bluez import Adapter, Outcome, get_bluez

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.bluetooth.bluez import BluezControl, BluezResult, PowerResult, ScanTranscript

logger = logging.getLogger(__name__)

__all__ = [
    "AdapterHandle",
    "AdapterSession",
    "AsyncAdapterSession",
    "Lease",
    "LinkState",
    "bt_executor",
    "force_release_lease",
]


# Blocking BlueZ work runs here; the pool is process-wide because the
# underlying controller is.
_bt_executor = ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 4), thread_name_prefix="bt-blocking")


def bt_executor() -> ThreadPoolExecutor:
    """The shared executor for blocking Bluetooth work."""
    return _bt_executor


class LinkState(Enum):
    """What BlueZ can currently say about a device's ACL link."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    #: BlueZ could not answer — a timeout, a dead transport, a parse gap.
    #: Callers must leave the last known state alone rather than treating
    #: this as a disconnect.
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# The process-wide exclusive lease
# ---------------------------------------------------------------------------

#: The single lock every controller operation contends for.  Every lease in
#: the process is a token handed out by this lock.
_bt_operation_lock = threading.Lock()
_lease_state_lock = threading.Lock()
_active_token: int | None = None
_active_reason: str = ""
_token_seq = itertools.count(1)


def _acquire_lease(reason: str, timeout: float | None) -> int | None:
    """Take the process-wide lease; return its token, or ``None``."""
    global _active_token, _active_reason
    if timeout is None:
        acquired = _bt_operation_lock.acquire(blocking=False)
    else:
        acquired = _bt_operation_lock.acquire(timeout=timeout)
    if not acquired:
        return None
    token = next(_token_seq)
    with _lease_state_lock:
        _active_token = token
        _active_reason = reason
    return token


def _release_lease(token: int) -> bool:
    """Release the lease held under *token*; ``False`` when it is stale."""
    global _active_token, _active_reason
    with _lease_state_lock:
        if _active_token != token:
            return False
        _active_token = None
        _active_reason = ""
    try:
        _bt_operation_lock.release()
    except RuntimeError:
        # A test fixture (or a legacy caller) already freed the raw lock.
        logger.debug("Bluetooth lease lock was already free at release time")
    return True


def _current_holder() -> str | None:
    with _lease_state_lock:
        return _active_reason if _active_token is not None else None


def force_release_lease() -> None:
    """Drop whatever lease is active, if any.

    A blunt instrument: nothing in the bridge's normal flow needs it, since
    every lease is released by its holder.  It exists for test fixtures that
    must guarantee a clean adapter between cases.
    """
    global _active_token, _active_reason
    with _lease_state_lock:
        _active_token = None
        _active_reason = ""
    try:
        _bt_operation_lock.release()
    except RuntimeError:
        pass


class Lease:
    """Exclusive right to drive one controller, released by token."""

    def __init__(self, handle: AdapterHandle, reason: str, token: int):
        self._handle = handle
        self._token = token
        self.reason = reason
        self._session = AdapterSession(handle, self)
        self._async_session = AsyncAdapterSession(self._session)
        self._released = False

    @property
    def active(self) -> bool:
        return not self._released

    @property
    def session(self) -> AdapterSession:
        """The blocking verbs. Valid only while the lease is held."""
        return self._session

    @property
    def async_session(self) -> AsyncAdapterSession:
        """The same verbs as coroutines, dispatched to the BT executor."""
        return self._async_session

    def release(self) -> None:
        """Release the lease. A stale release is logged, never obeyed."""
        if self._released:
            logger.error(
                "Bluetooth lease for %r released twice — ignoring the second release "
                "so it cannot free another operation's lease",
                self.reason,
            )
            return
        self._released = True
        if not _release_lease(self._token):
            logger.error(
                "Bluetooth lease for %r was no longer the active lease at release time",
                self.reason,
            )

    def __enter__(self) -> Lease:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Handle + sessions
# ---------------------------------------------------------------------------


class AdapterHandle:
    """One controller, addressed the way the kernel names it.

    *adapter* is the configured value: empty for "the default controller",
    an ``hciN`` name, or a controller MAC.  *link_probe* is the optional
    fast path (production injects the D-Bus read) — it may answer ``None``
    to mean "cannot say", in which case the transport decides.
    """

    def __init__(
        self,
        *,
        adapter: str = "",
        bluez: BluezControl | None = None,
        link_probe: Callable[[str], LinkState | None] | None = None,
    ):
        self._adapter = (adapter or "").strip()
        self._bluez = bluez
        self._link_probe = link_probe
        self._hci_name = ""

    # -- identity ------------------------------------------------------

    @property
    def bluez(self) -> BluezControl:
        return self._bluez if self._bluez is not None else get_bluez()

    @property
    def adapter(self) -> str:
        """The configured adapter identity (``""`` = default controller)."""
        return self._adapter

    @property
    def scope(self) -> Adapter:
        """The configured adapter as a transport scope directive."""
        if not self._adapter:
            return Adapter.DEFAULT
        return Adapter.select(self._adapter)

    @property
    def adapter_mac(self) -> str:
        """The controller's MAC, resolved from an ``hciN`` identity if needed."""
        if not self._adapter:
            return ""
        if not self._adapter.startswith("hci"):
            return self._adapter.upper()
        for mac, hci in self.bluez.hci_map().items():
            if hci == self._adapter:
                stripped = mac.replace(":", "").upper()
                return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))
        return ""

    @property
    def hci_name(self) -> str:
        """The kernel's ``hciN`` name for this controller, or ``""``.

        Resolution is retried until it succeeds: a bridge that started
        before ``bluetoothd`` used to cache the empty answer forever and
        run without a D-Bus fast path for the rest of the process.
        """
        if self._hci_name:
            return self._hci_name
        if self._adapter.startswith("hci"):
            self._hci_name = self._adapter
            return self._hci_name
        if not self._adapter:
            return ""
        wanted = self._adapter.replace(":", "").upper()
        for mac, hci in self.bluez.hci_map().items():
            if mac.replace(":", "").upper() == wanted:
                self._hci_name = hci
                return hci
        return ""

    def pin_hci(self, hci_name: str) -> None:
        """Pin the controller's ``hciN`` name, skipping resolution."""
        self._hci_name = (hci_name or "").strip()

    def dbus_device_path(self, mac: str) -> str | None:
        """``/org/bluez/hciN/dev_AA_BB_...`` once the controller resolves."""
        hci = self.hci_name
        if not hci:
            return None
        return f"/org/bluez/{hci}/dev_{mac.upper().replace(':', '_')}"

    # -- reads (no lease: a read cannot corrupt another operation) ------

    def link_state(self, mac: str) -> LinkState:
        """Tri-state ACL link state for *mac* on this controller.

        The injected fast probe answers first when it can; a transport that
        times out, is unavailable or cannot be parsed yields ``UNKNOWN``,
        never ``DISCONNECTED``.
        """
        if self._link_probe is not None:
            try:
                probed = self._link_probe(mac)
            except Exception as exc:  # a broken fast path must not decide the state
                logger.debug("Link-state fast probe failed for %s: %s", mac, exc)
                probed = None
            if probed is not None:
                return probed
        try:
            info = self.bluez.device_info(mac, self.scope)
        except Exception as exc:
            logger.warning("Link-state read failed for %s: %s", mac, exc)
            return LinkState.UNKNOWN
        if info.outcome is not Outcome.OK:
            return LinkState.UNKNOWN
        if not info.present:
            # BlueZ has no device object: it is certainly not connected.
            return LinkState.DISCONNECTED
        if info.connected is None:
            return LinkState.UNKNOWN
        return LinkState.CONNECTED if info.connected else LinkState.DISCONNECTED

    def device_info(self, mac: str):
        """Raw ``info <MAC>`` for callers that need more than link state."""
        return self.bluez.device_info(mac, self.scope)

    # -- leasing -------------------------------------------------------

    def try_lease(self, reason: str) -> Lease | None:
        """Take the lease without waiting; ``None`` when someone else holds it."""
        token = _acquire_lease(reason, None)
        if token is None:
            logger.debug("Bluetooth lease for %r refused; held by %r", reason, _current_holder())
            return None
        return Lease(self, reason, token)

    def lease(self, reason: str, timeout: float | None = None) -> Lease:
        """Take the lease, waiting up to *timeout* seconds.

        Raises :class:`TimeoutError` when the wait expires — callers that
        would rather skip the operation use :meth:`try_lease`.
        """
        token = _acquire_lease(reason, timeout if timeout is not None else -1)
        if token is None:
            raise TimeoutError(f"Bluetooth adapter is busy with {_current_holder()!r}; {reason!r} timed out")
        return Lease(self, reason, token)

    @staticmethod
    def current_holder() -> str | None:
        """The reason string of the live lease, or ``None``."""
        return _current_holder()


class AdapterSession:
    """The mutating verbs — reachable only through a live :class:`Lease`."""

    def __init__(self, handle: AdapterHandle, lease: Lease):
        self._handle = handle
        self._lease = lease

    def _check(self) -> None:
        if not self._lease.active:
            raise RuntimeError("Bluetooth adapter session used after its lease was released")

    # -- reads ---------------------------------------------------------

    def link_state(self, mac: str) -> LinkState:
        """Tri-state ACL link state (same read as :meth:`AdapterHandle.link_state`)."""
        self._check()
        return self._handle.link_state(mac)

    def device_info(self, mac: str):
        """Raw ``info <MAC>`` for callers that need more than link state."""
        self._check()
        return self._handle.device_info(mac)

    # -- mutations -----------------------------------------------------

    def connect(self, mac: str) -> BluezResult:
        self._check()
        return self._handle.bluez.connect(mac, self._handle.scope)

    def disconnect(self, mac: str) -> BluezResult:
        self._check()
        return self._handle.bluez.disconnect(mac, self._handle.scope)

    def trust(self, mac: str) -> BluezResult:
        self._check()
        return self._handle.bluez.trust(mac, self._handle.scope)

    def remove(self, mac: str):
        self._check()
        return self._handle.bluez.remove(mac, self._handle.scope)

    def power(self, on: bool) -> PowerResult:
        self._check()
        return self._handle.bluez.power(on, self._handle.scope)

    def scan(self, *, window_s: float = 15.0) -> ScanTranscript:
        self._check()
        adapters = [self._handle.adapter] if self._handle.adapter else None
        return self._handle.bluez.scan(adapters, window_s=window_s)

    def pair(self, mac: str, options: Any = None, **kwargs) -> Any:
        """Run the pairing choreography against this controller."""
        self._check()
        from sendspin_bridge.bluetooth.pairing import PairSession

        return PairSession(
            self._handle.bluez,
            adapter=self._handle.scope,
            mac=mac,
            options=options,
            **kwargs,
        ).run()


class AsyncAdapterSession:
    """The blocking verbs, dispatched to the Bluetooth executor."""

    def __init__(self, session: AdapterSession):
        self._session = session

    async def _call(self, fn, *args, **kwargs):
        import asyncio
        import functools

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_bt_executor, functools.partial(fn, *args, **kwargs))

    async def link_state(self, mac: str) -> LinkState:
        return await self._call(self._session.link_state, mac)

    async def device_info(self, mac: str):
        return await self._call(self._session.device_info, mac)

    async def connect(self, mac: str) -> BluezResult:
        return await self._call(self._session.connect, mac)

    async def disconnect(self, mac: str) -> BluezResult:
        return await self._call(self._session.disconnect, mac)

    async def trust(self, mac: str) -> BluezResult:
        return await self._call(self._session.trust, mac)

    async def remove(self, mac: str):
        return await self._call(self._session.remove, mac)

    async def power(self, on: bool) -> PowerResult:
        return await self._call(self._session.power, on)

    async def scan(self, *, window_s: float = 15.0) -> ScanTranscript:
        return await self._call(self._session.scan, window_s=window_s)

    async def pair(self, mac: str, options: Any = None, **kwargs) -> Any:
        return await self._call(self._session.pair, mac, options, **kwargs)
