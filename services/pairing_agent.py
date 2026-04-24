"""Native BlueZ authentication agent for pairing flows.

Replaces the bluetoothctl ``agent on`` + stdin-``yes`` race that loses to
BlueZ's internal agent timeout on some speakers (issue #168, Synergy 65 S).
A ``PairingAgent`` exports ``org.bluez.Agent1`` on the system bus for the
duration of a pair attempt, auto-confirms SSP passkey prompts (Numeric
Comparison via ``DisplayYesNo`` capability) and supplies a legacy PIN when
asked.

Usage::

    with PairingAgent(capability="DisplayYesNo", pin="0000") as agent:
        ...run bluetoothctl pair without agent on...
    # agent unregistered cleanly here
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

AGENT_PATH = "/io/sendspin/bridge/PairingAgent"
_BLUEZ_BUS = "org.bluez"
_AGENT_MANAGER_PATH = "/org/bluez"
_AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"

_VALID_CAPABILITIES = {
    "DisplayOnly",
    "DisplayYesNo",
    "KeyboardOnly",
    "NoInputNoOutput",
    "KeyboardDisplay",
}

# Service UUIDs we will authorize when the peer asks us to host one of them.
# Strictly audio-centric (A2DP source/sink + AVRCP controller/target + classic
# HSP/HFP) plus universally-advertised accessory services (GATT, GAP, DIS,
# Battery) so multi-profile peers don't spuriously fail authorization.
#
# Over-broad auto-accept (current behaviour before this scope) lets a peer
# bond as, for example, a HID keyboard alongside the requested audio profile
# — undesirable both for compatibility (some DSPs prefer HFP over A2DP when
# both are offered and accepted) and for security (unexpected service binds).
_AUTHORIZED_SERVICE_UUIDS: frozenset[str] = frozenset(
    {
        # Audio profiles
        "0000110a-0000-1000-8000-00805f9b34fb",  # A2DP Source
        "0000110b-0000-1000-8000-00805f9b34fb",  # A2DP Sink
        "0000110c-0000-1000-8000-00805f9b34fb",  # AVRCP Target
        "0000110d-0000-1000-8000-00805f9b34fb",  # Advanced Audio Distribution (generic)
        "0000110e-0000-1000-8000-00805f9b34fb",  # AVRCP Controller
        "0000110f-0000-1000-8000-00805f9b34fb",  # AVRCP (legacy)
        "00001108-0000-1000-8000-00805f9b34fb",  # Headset (HSP)
        "00001112-0000-1000-8000-00805f9b34fb",  # Headset AG
        "0000111e-0000-1000-8000-00805f9b34fb",  # Hands-Free (HFP)
        "0000111f-0000-1000-8000-00805f9b34fb",  # Hands-Free AG
        # Universally-advertised accessory services (safe to authorize;
        # authorize-all-or-reject-all behaviour on these leaks nothing an
        # already-bonded peer couldn't read anyway).
        "00001800-0000-1000-8000-00805f9b34fb",  # Generic Access
        "00001801-0000-1000-8000-00805f9b34fb",  # Generic Attribute
        "0000180a-0000-1000-8000-00805f9b34fb",  # Device Information
        "0000180f-0000-1000-8000-00805f9b34fb",  # Battery Service
    }
)


def _normalize_service_uuid(raw: str) -> str:
    """Normalize a Bluetooth service UUID to the 128-bit lowercase form.

    BlueZ may deliver UUIDs as 16-bit shorts (``"110B"``), 32-bit
    (``"0000110B"``), or full 128-bit with or without dashes.  Collapse
    all variants to ``"0000xxxx-0000-1000-8000-00805f9b34fb"`` lowercase
    so the allowlist check is unambiguous.
    """
    value = (raw or "").strip().lower()
    if not value:
        return ""
    # Remove the odd "0x" prefix some tools emit.
    if value.startswith("0x"):
        value = value[2:]
    compact = value.replace("-", "")
    if len(compact) == 4:
        compact = f"0000{compact}" + "00001000800000805f9b34fb"
    elif len(compact) == 8:
        compact = compact + "00001000800000805f9b34fb"
    if len(compact) != 32:
        return value  # not recognizable; let caller reject on its own
    return f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:32]}"


def _build_agent_iface(pin: str):
    """Build the org.bluez.Agent1 service interface object.

    Constructed lazily so ``import services.pairing_agent`` stays cheap
    (and importable) on systems without ``dbus_fast``.
    """
    from dbus_fast import DBusError  # type: ignore
    from dbus_fast.service import ServiceInterface, method  # type: ignore

    class _Agent(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")
            self.pin: str = pin
            self.pin_attempted: bool = False
            self.cancelled: bool = False
            self.last_passkey: int | None = None
            # Structured invocation log for #3 telemetry. Each entry is the
            # Agent1 method name BlueZ called on us, in order. Combined with
            # capability + last_passkey + authorized/rejected services, it
            # gives support enough context to triage a pair-time failure
            # without needing a DEBUG log.
            self.method_calls: list[str] = []
            self.authorized_services: list[str] = []
            self.rejected_services: list[str] = []

        @method()
        def Release(self):  # type: ignore[no-untyped-def]
            logger.debug("BlueZ released pairing agent")
            self.method_calls.append("Release")

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.RequestPinCode(%s) → '%s'", device, self.pin)
            self.pin_attempted = True
            self.method_calls.append("RequestPinCode")
            return self.pin

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            # Mirrors the legacy bluetoothctl flow which handles both
            # "enter pin code" (RequestPinCode) and "enter passkey"
            # (RequestPasskey) by supplying the configured PIN.  BlueZ
            # requires a 0..999999 numeric passkey here; if the PIN is
            # non-numeric (e.g. a 4-digit string with leading zeros like
            # "0000" is fine, but an alphanumeric override wouldn't be)
            # fall back to 0 and log so the operator can see the bad
            # configuration.
            self.pin_attempted = True
            self.method_calls.append("RequestPasskey")
            try:
                passkey = int(self.pin)
            except (TypeError, ValueError):
                logger.warning(
                    "Agent.RequestPasskey(%s): configured pin %r is not numeric — returning 0",
                    device,
                    self.pin,
                )
                return 0
            if not 0 <= passkey <= 999999:
                logger.warning(
                    "Agent.RequestPasskey(%s): configured pin %r out of range 0-999999 — returning 0",
                    device,
                    self.pin,
                )
                return 0
            logger.info("Agent.RequestPasskey(%s) → %06d", device, passkey)
            return passkey

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.DisplayPasskey(%s): %06d (entered=%d)", device, passkey, entered)
            self.last_passkey = int(passkey)
            self.method_calls.append("DisplayPasskey")

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.DisplayPinCode(%s): %s", device, pincode)
            self.method_calls.append("DisplayPinCode")

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            self.last_passkey = int(passkey)
            self.method_calls.append("RequestConfirmation")
            logger.info(
                "Agent.RequestConfirmation(%s): auto-confirming passkey %06d",
                device,
                passkey,
            )

        @method()
        def RequestAuthorization(self, device: "o"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.RequestAuthorization(%s): auto-authorize", device)
            self.method_calls.append("RequestAuthorization")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            self.method_calls.append("AuthorizeService")
            normalized = _normalize_service_uuid(uuid)
            if normalized in _AUTHORIZED_SERVICE_UUIDS:
                logger.info("Agent.AuthorizeService(%s, %s): authorized", device, uuid)
                self.authorized_services.append(normalized)
                return
            logger.warning(
                "Agent.AuthorizeService(%s, %s): rejected — not in the audio allow-list",
                device,
                uuid,
            )
            self.rejected_services.append(normalized or (uuid or ""))
            raise DBusError(
                "org.bluez.Error.Rejected",
                f"service {uuid} not in sendspin-bridge audio allow-list",
            )

        @method()
        def Cancel(self):  # type: ignore[no-untyped-def]
            logger.warning("Agent.Cancel — peer cancelled pairing")
            self.cancelled = True
            self.method_calls.append("Cancel")

    return _Agent()


class PairingAgent:
    """Context manager that hosts a BlueZ auth agent for a single pair."""

    def __init__(self, *, capability: str = "DisplayYesNo", pin: str = "0000") -> None:
        if capability not in _VALID_CAPABILITIES:
            raise ValueError(f"Invalid agent capability: {capability}")
        self._capability = capability
        self._pin = pin
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Typed as Any so mypy doesn't require an eager dbus_fast import at
        # module level (the import is deliberately lazy inside _register).
        self._bus: Any = None
        self._iface: Any = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    @property
    def capability(self) -> str:
        return self._capability

    @property
    def was_cancelled(self) -> bool:
        return bool(self._iface and self._iface.cancelled)

    @property
    def pin_attempted(self) -> bool:
        return bool(self._iface and self._iface.pin_attempted)

    @property
    def telemetry(self) -> dict[str, Any]:
        """Snapshot of what BlueZ asked us during this pair attempt.

        Callers should read this right before the ``with`` block exits
        (or inside ``finally``). Stable contract for surfacing in bug
        reports: always returns the same keys so downstream UI / log
        parsers can rely on them.
        """
        if self._iface is None:
            return {
                "capability": self._capability,
                "method_calls": [],
                "last_passkey": None,
                "pin_attempted": False,
                "peer_cancelled": False,
                "authorized_services": [],
                "rejected_services": [],
            }
        return {
            "capability": self._capability,
            "method_calls": list(self._iface.method_calls),
            "last_passkey": self._iface.last_passkey,
            "pin_attempted": self._iface.pin_attempted,
            "peer_cancelled": self._iface.cancelled,
            "authorized_services": list(self._iface.authorized_services),
            "rejected_services": list(self._iface.rejected_services),
        }

    def __enter__(self) -> PairingAgent:
        self._thread = threading.Thread(target=self._thread_main, name="pair-agent", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            self._force_stop()
            raise RuntimeError("PairingAgent did not register within 5s")
        if self._start_error is not None:
            err = self._start_error
            self._force_stop()
            raise RuntimeError(f"PairingAgent start failed: {err}") from err
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._force_stop()

    def _force_stop(self) -> None:
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _thread_main(self) -> None:
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._register())
            except BaseException as exc:
                # _register may raise AFTER MessageBus.connect() succeeded —
                # e.g. AgentManager1.RegisterAgent refuses because another
                # agent holds the default. We still need to unwind the bus
                # connection and close the loop so we don't leak a SystemBus
                # socket / event loop for every failed pair attempt.
                self._start_error = exc
                self._ready.set()
                return
            self._ready.set()
            loop.run_forever()
        finally:
            if loop is not None:
                try:
                    if self._bus is not None:
                        loop.run_until_complete(self._unregister())
                except Exception as exc:
                    logger.debug("PairingAgent unregister failed (non-fatal): %s", exc)
                try:
                    loop.close()
                except Exception as exc:  # pragma: no cover — best-effort
                    logger.debug("PairingAgent loop close failed: %s", exc)
            self._loop = None

    async def _register(self) -> None:
        from dbus_fast import BusType  # type: ignore
        from dbus_fast.aio import MessageBus  # type: ignore

        self._iface = _build_agent_iface(self._pin)
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus.export(AGENT_PATH, self._iface)
        introspect = await self._bus.introspect(_BLUEZ_BUS, _AGENT_MANAGER_PATH)
        proxy = self._bus.get_proxy_object(_BLUEZ_BUS, _AGENT_MANAGER_PATH, introspect)
        mgr = proxy.get_interface(_AGENT_MANAGER_IFACE)
        await mgr.call_register_agent(AGENT_PATH, self._capability)
        await mgr.call_request_default_agent(AGENT_PATH)
        logger.info(
            "PairingAgent registered (path=%s capability=%s)",
            AGENT_PATH,
            self._capability,
        )

    async def _unregister(self) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            introspect = await bus.introspect(_BLUEZ_BUS, _AGENT_MANAGER_PATH)
            mgr = bus.get_proxy_object(_BLUEZ_BUS, _AGENT_MANAGER_PATH, introspect).get_interface(_AGENT_MANAGER_IFACE)
            await mgr.call_unregister_agent(AGENT_PATH)
        finally:
            bus.disconnect()
            try:
                await bus.wait_for_disconnect()
            except Exception:
                pass
        self._bus = None
