"""The one pairing composite: :class:`PairSession`.

Pairing used to exist as three near-identical bluetoothctl flows — the
monitor loop's re-pair after a lost bond, the manual pair of a new device,
and reset-and-reconnect — each carrying one behaviour the other two
lacked.  This module is their union, driven by
:meth:`~sendspin_bridge.bluetooth.bluez.BluezControl.session`:

* ``pair`` fires the moment the target advertises itself, with the
  discovery deadline as a fallback for speakers that never appear in the
  event stream (issue #168);
* SSP confirmations and legacy PIN prompts are answered on the session's
  own reply channel;
* the popular-PIN ladder advances only while the device keeps rejecting
  the PIN — other failures stop it, because retrying them costs ~20 s
  each and changes nothing;
* every attempt is cancellable, so disabling a device mid-flight stops
  pairing instead of finishing it;
* ``trust`` runs only after a confirmed pair, ``connect`` optionally
  follows inside the same session, and failures come back fingerprinted
  for the operator-facing recovery guidance.

Timing and I/O go through the transport's injected clock, so tests drive
the whole composite without real waits.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sendspin_bridge.bluetooth.bluez import Adapter, Deadline, LineKind, parse_device_info
from sendspin_bridge.services.bluetooth import (
    classify_pair_failure,
    describe_pair_failure,
    is_pin_rejection,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.bluetooth.bluez import BluezControl, BluezSession

logger = logging.getLogger(__name__)

__all__ = ["PairOptions", "PairOutcome", "PairSession", "PairTimings"]

# How long a single read slice may block before the timers are re-checked.
# Matches the historical ``selectors.select(timeout=0.5)`` cadence.
_POLL_SLICE_S = 0.5

_SUCCESS_MARKERS = ("pairing successful", "already paired")
_OUTPUT_SUCCESS_MARKERS = (*_SUCCESS_MARKERS, "paired: yes")
_CONNECTED_MARKERS = ("connection successful", "connected: yes")
_CONFIRM_MARKERS = ("confirm passkey", "request confirmation")
_PIN_PROMPT_MARKERS = ("enter pin code", "enter passkey")


@dataclass(frozen=True, slots=True)
class PairTimings:
    """The waits a pair attempt is allowed to spend, in seconds."""

    scan_window_s: float = 12.0
    pair_wait_s: float = 15.0
    post_trust_settle_s: float = 0.0
    pre_cleanup_settle_s: float = 1.0
    drain_s: float = 3.0


@dataclass(frozen=True, slots=True)
class PairOptions:
    """What kind of pairing to run.  Defaults mirror the manual pair flow."""

    pins: tuple[str, ...] = ("0000",)
    capability: str = "DisplayYesNo"
    allow_hfp: bool = False
    connect_after_trust: bool = False
    timings: PairTimings = PairTimings()


@dataclass(frozen=True, slots=True)
class PairOutcome:
    """Everything one pair run produced."""

    success: bool
    connected: bool = False
    cancelled: bool = False
    pin_attempted: bool = False
    pin_rejected: bool = False
    pin_used: str = ""
    tried_pins: tuple[str, ...] = ()
    failure_kind: str | None = None
    reason: str = ""
    output: str = ""
    agent_telemetry: dict[str, Any] | None = field(default=None)


def _default_agent_factory(*, capability: str, pin: str, allow_hfp: bool, target_mac: str):
    """Build the native ``org.bluez.Agent1`` implementation.

    Imported lazily: this module must stay importable on hosts without
    dbus-fast, where the caller falls back to bluetoothctl's own agent.
    """
    from sendspin_bridge.services.bluetooth.pairing_agent import PairingAgent

    return PairingAgent(capability=capability, pin=pin, allow_hfp=allow_hfp, target_mac=target_mac)


class PairSession:
    """One pairing composite over :class:`BluezControl`.

    ``cancel`` is polled throughout the attempt (the monitor loop uses it
    to abandon a re-pair for a device that got disabled).  ``on_before_pair``
    runs immediately before the ``pair`` line goes out — the hook the
    Samsung Class-of-Device override needs, because ``power on`` can reset
    the controller's CoD.  ``attempt_context`` wraps each individual
    attempt (peer quiesce on single-adapter multi-speaker hosts).
    """

    def __init__(
        self,
        bluez: BluezControl,
        *,
        adapter: Adapter = Adapter.DEFAULT,
        mac: str,
        options: PairOptions | None = None,
        cancel: Callable[[], bool] | None = None,
        agent_factory: Callable[..., Any] | None = None,
        on_before_pair: Callable[[], None] | None = None,
        attempt_context: Callable[[], Any] | None = None,
        label: str = "",
    ) -> None:
        self._bluez = bluez
        self._adapter = adapter
        self._mac = mac
        self._options = options or PairOptions()
        self._cancel = cancel
        self._agent_factory = agent_factory if agent_factory is not None else _default_agent_factory
        self._on_before_pair = on_before_pair
        self._attempt_context = attempt_context
        self._label = label or mac

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(self) -> PairOutcome:
        """Walk the PIN ladder until pairing succeeds or stops being about PINs."""
        pins = self._options.pins or ("0000",)
        tried: list[str] = []
        outcome = PairOutcome(success=False, reason="no pair attempt ran")
        for pin in pins:
            tried.append(pin)
            with self._attempt_scope():
                outcome = self.attempt(pin)
            outcome = _with_tried(outcome, tuple(tried))
            if outcome.success or outcome.cancelled or not outcome.pin_rejected:
                return outcome
            logger.warning(
                "Pair %s: PIN %s rejected — retrying with next candidate",
                self._label,
                pin,
            )
        logger.warning(
            "Pair %s: device rejected every popular PIN (%s) — it likely needs a custom PIN, "
            "which the bridge cannot enter automatically. Last reason: %s",
            self._label,
            ", ".join(tried),
            outcome.reason or "no explicit bluetoothctl reason captured",
        )
        return outcome

    def attempt(self, pin: str = "0000") -> PairOutcome:
        """Run a single pair attempt with one PIN candidate."""
        if self._cancelled():
            logger.info("Pair %s: cancelled before starting", self._label)
            return PairOutcome(success=False, cancelled=True, pin_used=pin, reason="cancelled")

        self._clear_stale_agent_and_bond()

        agent: Any | None = None
        try:
            agent = self._enter_agent(pin)
            return self._pair_in_session(pin, agent)
        finally:
            if agent is not None:
                try:
                    agent.__exit__(None, None, None)
                except Exception as exc:
                    logger.debug("Pair %s: agent cleanup failed: %s", self._label, exc)

    # ------------------------------------------------------------------
    # Attempt internals
    # ------------------------------------------------------------------

    def _pair_in_session(self, pin: str, agent: Any | None) -> PairOutcome:
        mac = self._mac
        timings = self._options.timings
        with self._bluez.session(self._adapter, cancel=self._cancel) as session:
            if session.aborted:
                logger.info("Pair %s: cancelled after session spawn", self._label)
                return PairOutcome(success=False, cancelled=True, pin_used=pin, reason="cancelled")

            session.send(*self._opening_commands(agent))

            state = _AttemptState(pin=pin)
            cancelled = self._read_until_settled(session, state)
            if cancelled:
                return PairOutcome(success=False, cancelled=True, pin_used=pin, reason="cancelled")

            # Safety net: the read loop can end before the deadline (the
            # process died, output hit EOF) without ever sending ``pair``.
            if not state.pair_sent and session.alive:
                self._send_pair(session)
                state.pair_sent = True

            if state.paired_ok:
                session.send(f"trust {mac}")
                if self._options.connect_after_trust:
                    session.send(f"connect {mac}")
            session.send(f"info {mac}", "scan off", "quit")
            if state.paired_ok and timings.post_trust_settle_s > 0:
                # Consume output while settling: the connect result and the
                # final ``info`` block arrive during this window.
                session.wait(timings.post_trust_settle_s, drain=True)
            session.drain(timeout=timings.drain_s)
            output = _transcript_text(session)

        return self._classify(output, state, agent)

    def _opening_commands(self, agent: Any | None) -> tuple[str, ...]:
        commands = ["power on"]
        if agent is None:
            # No native agent — bluetoothctl's own agent authenticates.
            # ``agent NoInputNoOutput`` forces Just-Works SSP for speakers
            # that cancel a passkey exchange (issue #168).
            agent_cmd = "agent NoInputNoOutput" if self._options.capability == "NoInputNoOutput" else "agent on"
            commands.extend([agent_cmd, "default-agent"])
        # ``scan bredr`` (not ``scan on``) keeps discovery on the classic
        # transport: A2DP sinks only speak BR/EDR, and excluding LE-only
        # advertisers keeps the target from being delayed behind them.
        commands.append("scan bredr")
        return tuple(commands)

    def _read_until_settled(self, session: BluezSession, state: _AttemptState) -> bool:
        """Drive the pair dialogue.  Returns ``True`` when cancelled."""
        start = self._bluez.now()
        timings = self._options.timings
        scan_deadline = start + timings.scan_window_s
        full_deadline = scan_deadline + timings.pair_wait_s
        while True:
            if self._cancelled():
                return True
            now = self._bluez.now()
            if not state.pair_sent and now >= scan_deadline:
                # The device never advertised itself; try anyway — it may
                # already be in the BlueZ cache from an earlier scan.
                self._send_pair(session)
                state.pair_sent = True
                continue
            if now >= full_deadline or not session.alive:
                return False
            phase_end = full_deadline if state.pair_sent else scan_deadline
            slice_end = min(phase_end, now + _POLL_SLICE_S)
            for line in session.lines(deadline=slice_end):
                if self._handle_line(session, line, state):
                    return False
            if state.paired_ok:
                return False

    def _handle_line(self, session: BluezSession, line, state: _AttemptState) -> bool:
        """Answer one output line.  Returns ``True`` when the dialogue is done."""
        text = line.text.strip()
        lowered = text.lower()
        if not state.pair_sent and line.kind is LineKind.EVENT_NEW and (line.event_mac or "") == self._mac.upper():
            logger.debug("Pair %s: target advertised itself — firing pair now", self._label)
            self._send_pair(session)
            state.pair_sent = True
            return False
        if any(marker in lowered for marker in _CONFIRM_MARKERS):
            logger.info("Pair %s: SSP confirmation prompt — auto-confirming: %s", self._label, text)
            session.reply("yes")
        elif any(marker in lowered for marker in _PIN_PROMPT_MARKERS):
            # Legacy BT 2.x sinks ask for a numeric PIN; the ladder decides
            # which candidate this attempt offers.
            logger.info("Pair %s: legacy PIN prompt — entering %s: %s", self._label, state.pin, text)
            session.reply(state.pin)
            state.pin_attempted = True
        if any(marker in lowered for marker in _SUCCESS_MARKERS):
            state.paired_ok = True
            return True
        return False

    def _send_pair(self, session: BluezSession) -> None:
        if self._on_before_pair is not None:
            try:
                self._on_before_pair()
            except Exception as exc:
                logger.debug("Pair %s: pre-pair hook failed: %s", self._label, exc)
        session.send(f"pair {self._mac}")

    def _classify(self, output: str, state: _AttemptState, agent: Any | None) -> PairOutcome:
        lowered = output.lower()
        success = state.paired_ok or any(marker in lowered for marker in _OUTPUT_SUCCESS_MARKERS)
        connected = any(marker in lowered for marker in _CONNECTED_MARKERS)
        bond_reason = self._bond_missing_on_adapter(output) if success else ""
        if bond_reason:
            success = False
        telemetry = self._read_telemetry(agent)
        logger.info(
            "Pair %s: success=%s connected=%s pin=%s agent=%s",
            self._label,
            success,
            connected,
            state.pin if state.pin_attempted else "not asked",
            telemetry,
        )
        if success:
            return PairOutcome(
                success=True,
                connected=connected,
                pin_attempted=state.pin_attempted,
                pin_used=state.pin,
                output=output,
                agent_telemetry=telemetry,
            )
        reason = (
            bond_reason
            or describe_pair_failure(output, pin_attempted=state.pin_attempted, pin_used=state.pin)
            or "no explicit bluetoothctl reason captured"
        )
        # Read the rejection off the raw output rather than the rendered
        # reason, so rewording the human-readable text can't break retries.
        pin_rejected = state.pin_attempted and is_pin_rejection(output)
        logger.warning("Pair %s failed: %s", self._label, reason)
        # Full output, not a tail: the agent/SSP dialogue at the start of the
        # session is the part bug reports need.
        logger.debug("Pair %s output (pin=%s): %s", self._label, state.pin, output)
        return PairOutcome(
            success=False,
            connected=connected,
            pin_attempted=state.pin_attempted,
            pin_rejected=pin_rejected,
            pin_used=state.pin,
            failure_kind=classify_pair_failure(output, agent_telemetry=telemetry),
            reason=reason,
            output=output,
            agent_telemetry=telemetry,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _bond_missing_on_adapter(self, output: str) -> str:
        """Reason the claimed pair did not land on the requested controller.

        BlueZ bonds are per-controller.  When the device is already bonded
        on another local adapter the SSP exchange never fires and the
        session prints ``Pairing successful`` regardless, so a two-adapter
        host reports success while every later connect on the requested
        adapter bounces.  The trailing ``info`` ran under this session's
        ``select``, which makes it the requested adapter's own view.

        Only meaningful when an adapter was actually selected — under
        ``Adapter.DEFAULT`` the ``info`` reflects whichever controller
        BlueZ chose and cannot falsify the pair.  Returns an empty string
        when the bond is confirmed or unverifiable.
        """
        if self._adapter.is_default or not self._adapter.ident:
            return ""
        # The raw field, not ``DeviceInfo.paired``: the property is
        # tri-state and returns None when the info block carries no
        # ``Device <mac>`` header — exactly the unbonded case.
        verify = parse_device_info(output, self._mac)
        # ``Paired: no`` and ``Device <MAC> not available`` are the same
        # answer from this controller's point of view: the bond is not
        # here.  Output with no info block at all is not an answer and
        # must not turn a confirmed pair into a failure.
        if verify.fields.get("paired", "").lower() != "no" and not verify.not_available:
            return ""
        logger.warning(
            "Pair %s: session claimed success but the bond is not on %s (paired=%s present=%s)",
            self._label,
            self._adapter.ident,
            verify.fields.get("paired"),
            verify.present,
        )
        return f"device is not Paired on adapter {self._adapter.ident}; remove it from the other adapter first"

    def _clear_stale_agent_and_bond(self) -> None:
        """Drop a lingering agent object and any stale bond for the target.

        Without ``agent off`` the next ``agent on`` answers ``Failed to
        register agent object`` and pairing proceeds with no authentication
        agent, surfacing as ``org.bluez.Error.ConnectionAttemptFailed``
        (issue #162).
        """
        try:
            self._bluez.run(
                ["agent off", f"remove {self._mac}"],
                adapter=self._adapter,
                tier=Deadline.MUTATE,
            )
        except Exception as exc:  # defensive: the transport reports via Outcome
            logger.debug("Pair %s: pre-pair cleanup failed (non-fatal): %s", self._label, exc)
        settle = self._options.timings.pre_cleanup_settle_s
        if settle > 0:
            self._bluez.sleep(settle)

    def _enter_agent(self, pin: str) -> Any | None:
        try:
            agent = self._agent_factory(
                capability=self._options.capability,
                pin=pin,
                allow_hfp=self._options.allow_hfp,
                target_mac=self._mac,
            )
        except Exception as exc:
            logger.warning(
                "Pair %s: native agent unavailable (%s) — falling back to the bluetoothctl agent",
                self._label,
                exc,
            )
            return None
        if agent is None:
            return None
        try:
            entered = agent.__enter__()
        except Exception as exc:
            logger.warning(
                "Pair %s: native agent could not register (%s) — falling back to the bluetoothctl agent",
                self._label,
                exc,
            )
            return None
        logger.info("Pair %s: native agent active (cap=%s)", self._label, self._options.capability)
        return entered

    def _read_telemetry(self, agent: Any | None) -> dict[str, Any] | None:
        if agent is None:
            return None
        try:
            return agent.telemetry
        except Exception as exc:
            logger.debug("Pair %s: agent telemetry read failed: %s", self._label, exc)
            return None

    def _attempt_scope(self):
        if self._attempt_context is None:
            return contextlib.nullcontext()
        try:
            return self._attempt_context()
        except Exception as exc:
            logger.debug("Pair %s: attempt context unavailable: %s", self._label, exc)
            return contextlib.nullcontext()

    def _cancelled(self) -> bool:
        if self._cancel is None:
            return False
        try:
            return bool(self._cancel())
        except Exception as exc:
            logger.debug("Pair %s: cancel check failed: %s", self._label, exc)
            return False


@dataclass
class _AttemptState:
    """Mutable bookkeeping for one attempt's dialogue."""

    pin: str
    pair_sent: bool = False
    paired_ok: bool = False
    pin_attempted: bool = False


def _with_tried(outcome: PairOutcome, tried: tuple[str, ...]) -> PairOutcome:
    return PairOutcome(
        success=outcome.success,
        connected=outcome.connected,
        cancelled=outcome.cancelled,
        pin_attempted=outcome.pin_attempted,
        pin_rejected=outcome.pin_rejected,
        pin_used=outcome.pin_used,
        tried_pins=tried,
        failure_kind=outcome.failure_kind,
        reason=outcome.reason,
        output=outcome.output,
        agent_telemetry=outcome.agent_telemetry,
    )


def _transcript_text(session: BluezSession) -> str:
    return "\n".join(line.raw.rstrip("\n") for line in session.transcript())
