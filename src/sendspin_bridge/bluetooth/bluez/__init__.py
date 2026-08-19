"""BlueZ control transport — the one deep module owning bluetoothctl.

Everything that talks to ``bluetoothctl`` goes through
:class:`BluezControl`: invocation, output hygiene (ANSI, prompt grammars,
async event noise), adapter scoping and timeout tiers.  Parsers whose
correctness depends on which command ran under which adapter live inside;
domain policy (audio-capability classification, pair-failure wording)
stays outside as pure functions over the value types re-exported here.

Hard import rule: this package is stdlib-only so it can sit at the bottom
of the dependency graph (importable from ``services.*``,
``bluetooth.manager`` and ``web.*`` without a cycle).  The rule is
enforced by ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ._adapters import Adapter, enumerate_sysfs_adapters, parse_hciconfig, resolve_select_mac
from ._lines import BluezLine, LineKind, classify_line, classify_lines, strip_ansi
from ._parsers import (
    INFO_FIELDS,
    AdapterInfo,
    AdapterRef,
    DeviceEntry,
    DeviceInfo,
    ScanTranscript,
    parse_adapter_list,
    parse_device_info,
    parse_devices,
    parse_scan,
    parse_show,
    parse_version,
    summarize_connect_output,
)
from ._process import (
    BluezResult,
    Deadline,
    Outcome,
    PowerResult,
    RemoveResult,
    SubprocessSpawner,
    run_bluetoothctl,
)
from ._session import BluezSession

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

__all__ = [
    "INFO_FIELDS",
    "Adapter",
    "AdapterInfo",
    "AdapterRef",
    "BluezControl",
    "BluezLine",
    "BluezResult",
    "BluezSession",
    "Deadline",
    "DeviceEntry",
    "DeviceInfo",
    "LineKind",
    "Outcome",
    "PowerResult",
    "RemoveResult",
    "ScanTranscript",
    "SubprocessSpawner",
    "classify_line",
    "classify_lines",
    "get_bluez",
    "parse_adapter_list",
    "parse_device_info",
    "parse_devices",
    "parse_scan",
    "parse_show",
    "parse_version",
    "set_bluez",
    "strip_ansi",
    "summarize_connect_output",
]

_BLUETOOTHCTL = ("bluetoothctl",)

# How long to wait for BlueZ to apply a power toggle before reporting the
# controller's state as final, and how often to re-read it meanwhile.
_POWER_SETTLE_S = 2.0
_POWER_POLL_S = 0.25


class BluezControl:
    """The bluetoothctl transport.

    Parameters are injection seams; production uses the defaults plus an
    optional ``hci_resolver`` (``bluetooth.manager`` injects the D-Bus
    object-path lookup — the good ``hciN → MAC`` path).  Tests substitute
    ``spawner`` (FakeBluez) and the clock/sleeper pair.
    """

    def __init__(
        self,
        *,
        spawner=None,
        hci_resolver: Callable[[str], str] | None = None,
        sysfs_dir: Path = Path("/sys/class/bluetooth"),
        time_source: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._spawner = spawner if spawner is not None else SubprocessSpawner()
        self._hci_resolver = hci_resolver
        self._sysfs_dir = sysfs_dir
        self._time = time_source
        self._sleep = sleeper

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    def now(self) -> float:
        """The injected monotonic clock — composites must not read their own."""
        return self._time()

    def sleep(self, seconds: float) -> None:
        """The injected sleeper (instant under the test fake)."""
        self._sleep(seconds)

    # ------------------------------------------------------------------
    # Scoping
    # ------------------------------------------------------------------

    def _scope_prefix(self, adapter: Adapter) -> tuple[tuple[str, ...], bool]:
        """Return the ``select <MAC>`` prefix lines + unresolved flag."""
        if adapter.mode == "default":
            return (), False
        if adapter.mode == "addressed":
            raise ValueError("Adapter.addressed applies to adapter verbs (show); it emits no select line")
        mac, unresolved = resolve_select_mac(
            adapter.ident,
            sysfs_dir=self._sysfs_dir,
            hci_resolver=self._hci_resolver,
            list_macs=lambda: [ref.mac for ref in self.list_adapters()],
        )
        if unresolved:
            logger.warning(
                "Adapter %r could not be resolved to a controller MAC — passing it to `bluetoothctl "
                "select` unchanged so a failure surfaces loudly instead of silently hitting the "
                "default controller",
                adapter.ident,
            )
        return (f"select {mac}",), unresolved

    # ------------------------------------------------------------------
    # Generic runner
    # ------------------------------------------------------------------

    def run(
        self,
        script: Iterable[str] | str,
        *,
        adapter: Adapter = Adapter.DEFAULT,
        tier: Deadline = Deadline.QUERY,
        timeout: float | None = None,
    ) -> BluezResult:
        """Run a raw script (composite cleanups) with adapter scoping."""
        if isinstance(script, str):
            lines = tuple(line for line in script.splitlines() if line.strip())
        else:
            lines = tuple(script)
        prefix, unresolved = self._scope_prefix(adapter)
        full_script = prefix + lines
        return run_bluetoothctl(
            self._spawner,
            _BLUETOOTHCTL,
            full_script,
            float(timeout if timeout is not None else tier),
            self._time,
            scope_unresolved=unresolved,
        )

    # ------------------------------------------------------------------
    # One-shot verbs (each = run() + a parser — hold that invariant)
    # ------------------------------------------------------------------

    def version(self, *, timeout: float | None = None) -> str:
        """``bluetoothctl --version`` → e.g. ``"bluetoothctl: 5.72"``; "" on failure."""
        result = run_bluetoothctl(
            self._spawner,
            ("bluetoothctl", "--version"),
            (),
            float(timeout if timeout is not None else Deadline.PROBE),
            self._time,
        )
        if result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
            return ""
        return parse_version(result.stdout)

    def list_adapters(self, *, timeout: float | None = None) -> list[AdapterRef]:
        """``bluetoothctl list`` → controller rows (MAC, name, default flag)."""
        result = run_bluetoothctl(
            self._spawner,
            ("bluetoothctl", "list"),
            (),
            float(timeout if timeout is not None else Deadline.QUERY),
            self._time,
        )
        if result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
            return []
        return parse_adapter_list(result.stdout)

    def hci_map(self) -> dict[str, str]:
        """``{MAC: hciN}`` for every visible controller (colon-stripped keys).

        Sysfs is the authoritative source — ``/sys/class/bluetooth`` is
        what the kernel and BlueZ honour.  When sysfs is not mounted into
        the process (Docker without ``/sys`` passthrough) the fallback is
        ``hciconfig -a`` via the spawner, which reads the same mapping
        through the BlueZ control socket.  Empty dict when both fail.
        """
        mapping = enumerate_sysfs_adapters(self._sysfs_dir)
        if mapping:
            return mapping
        try:
            proc = self._spawner.run(("hciconfig", "-a"), input=None, timeout=3.0)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("hciconfig fallback failed: %s", exc)
            return {}
        return parse_hciconfig(proc.stdout)

    def show(self, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> AdapterInfo:
        """``show`` for one controller; ``Adapter.addressed(mac)`` never selects."""
        if adapter.mode == "addressed":
            result = run_bluetoothctl(
                self._spawner,
                _BLUETOOTHCTL,
                (f"show {adapter.ident}",),
                float(timeout if timeout is not None else Deadline.QUERY),
                self._time,
            )
        else:
            result = self.run(["show"], adapter=adapter, tier=Deadline.QUERY, timeout=timeout)
        return parse_show(result.stdout, outcome=result.outcome)

    def device_info(
        self,
        mac: str,
        adapter: Adapter = Adapter.DEFAULT,
        *,
        timeout: float | None = None,
    ) -> DeviceInfo:
        """``info <MAC>`` with adapter scoping; tri-state fields via :class:`DeviceInfo`."""
        result = self.run([f"info {mac}"], adapter=adapter, tier=Deadline.QUERY, timeout=timeout)
        return parse_device_info(result.stdout, mac.upper(), outcome=result.outcome)

    def list_devices(
        self,
        adapter: Adapter = Adapter.DEFAULT,
        *,
        filter: str = "Paired",
        timeout: float | None = None,
    ) -> list[DeviceEntry]:
        """``devices [filter]`` → ``(mac, name)`` entries; ``[]`` on transport failure."""
        verb = f"devices {filter}".rstrip()
        result = self.run([verb], adapter=adapter, tier=Deadline.QUERY, timeout=timeout)
        if result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
            return []
        return parse_devices(result.stdout)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def power(self, on: bool, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> PowerResult:
        """``power on|off``, reported against the controller's own state.

        bluetoothctl confirms a toggle asynchronously ("Changing power on
        succeeded"), and with stdin piped it often exits before printing
        it — scraping stdout then reports a toggle that plainly happened
        as a failure.  The confirmation line is still honoured when it
        arrives; otherwise a follow-up ``show`` decides, and only when
        that cannot answer either does the assumed state stand.
        """
        result = self.run(
            ["power on" if on else "power off"],
            adapter=adapter,
            tier=Deadline.MUTATE,
            timeout=timeout,
        )
        clean = strip_ansi(result.stdout).lower()
        confirmed = (
            "succeeded" in clean
            or "changing power" in clean
            or (("powered: yes" in clean) if on else ("powered: no" in clean))
        )
        if confirmed:
            return PowerResult(changed=True, powered=on, result=result)
        # BlueZ applies the toggle asynchronously — on the live stand the
        # controller kept reporting the old state for about a second — so
        # the state is re-read until it settles or the window closes.
        deadline = self.now() + _POWER_SETTLE_S
        while True:
            info = self.show(adapter, timeout=timeout)
            if info.outcome is not Outcome.OK or not info.present:
                # No second opinion available: report the command's own verdict.
                return PowerResult(changed=False, powered=on, result=result)
            if info.powered is on:
                return PowerResult(changed=True, powered=on, result=result)
            if self.now() >= deadline:
                return PowerResult(changed=False, powered=info.powered, result=result)
            self.sleep(_POWER_POLL_S)

    def connect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> BluezResult:
        return self.run([f"connect {mac}"], adapter=adapter, tier=Deadline.MUTATE, timeout=timeout)

    def disconnect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> BluezResult:
        return self.run([f"disconnect {mac}"], adapter=adapter, tier=Deadline.MUTATE, timeout=timeout)

    def trust(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> BluezResult:
        return self.run([f"trust {mac}"], adapter=adapter, tier=Deadline.MUTATE, timeout=timeout)

    def remove(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> RemoveResult:
        """``remove <MAC>``; bluetoothctl exits 0 even on failure, so the
        stdout marker is the source of truth."""
        result = self.run([f"remove {mac}"], adapter=adapter, tier=Deadline.MUTATE, timeout=timeout)
        out = result.text.lower()
        not_available = "not available" in out or "failed to remove" in out
        removed = result.outcome is Outcome.OK and not not_available
        return RemoveResult(removed=removed, not_available=not_available, result=result)

    # ------------------------------------------------------------------
    # Composites + sessions
    # ------------------------------------------------------------------

    def scan(self, adapters: Iterable[str] | None = None, *, window_s: float = 15.0) -> ScanTranscript:
        """Scan composite: one session for discovery, then per-adapter
        ``show`` + ``devices`` enumeration so devices are attributed to the
        controller that actually saw them (issue #340)."""
        adapter_macs = tuple(a.strip().upper() for a in (adapters or ()) if a and a.strip())
        # The one place a composite budget is computed: base window plus
        # per-adapter overhead plus drain slack (historically 12 + 2N + 4).
        budget = 12 + 2 * len(adapter_macs) + 4
        lines: list[BluezLine] = []
        with self.session() as session:
            if adapter_macs:
                session.send("agent on", "default-agent")
                for mac in adapter_macs:
                    session.send(f"select {mac}", "power on", "scan bredr")
            else:
                session.send("power on", "agent on", "default-agent", "scan bredr")
            session.wait(window_s, drain=True)
            session.send("scan off")
            session.wait(1.0, drain=True)
            session.send("quit")
            session.drain(timeout=budget)
            lines.extend(session.transcript())
        # Per-adapter device enumeration runs in dedicated short sessions.
        # Inside the long-lived scan session the marker lines interleave with
        # async discovery notifications on piped stdin — the same
        # unreliability that produced the alias swap in #193 — so devices
        # could be attributed to whichever controller happened to be selected
        # when the output flushed (issue #340).
        for mac in adapter_macs:
            result = self.run(["show", "devices"], adapter=Adapter.select(mac), tier=Deadline.MUTATE)
            if result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
                logger.debug("Post-scan device enumeration failed for %s: %s", mac, result.error)
                continue
            lines.extend(result.lines)
        return parse_scan(lines)

    def session(
        self,
        adapter: Adapter = Adapter.DEFAULT,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> BluezSession:
        """Open an interactive session (substrate for the pair composites).

        ``cancel`` is evaluated once right after spawn; on fire the process
        is terminated and the returned session is a no-op.
        """
        prefix, _unresolved = self._scope_prefix(adapter)
        return BluezSession(
            spawner=self._spawner,
            select_lines=prefix,
            cancel=cancel,
            time_source=self._time,
            sleeper=self._sleep,
        )


_default_bluez: BluezControl | None = None


def get_bluez() -> BluezControl:
    """The shared default transport (lazy singleton)."""
    global _default_bluez
    if _default_bluez is None:
        _default_bluez = BluezControl()
    return _default_bluez


def set_bluez(control: BluezControl | None) -> None:
    """Replace the shared transport (tests; pass ``None`` to reset)."""
    global _default_bluez
    _default_bluez = control
