"""Grammars for bluetoothctl command output: list / show / info / devices / scan.

These parsers live inside the transport module because their correctness
depends on which command ran under which adapter scope.  Domain policy
(audio-capability classification, pair-failure wording) stays outside as
pure functions over the value types defined here.

Stdlib only — see ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._lines import BluezLine, LineKind, strip_ansi
from ._process import Outcome

__all__ = [
    "INFO_FIELDS",
    "AdapterInfo",
    "AdapterRef",
    "DeviceEntry",
    "DeviceInfo",
    "ScanTranscript",
    "parse_adapter_list",
    "parse_device_info",
    "parse_devices",
    "parse_scan",
    "parse_show",
    "parse_version",
    "summarize_connect_output",
]

# The public keys of the /api/bt/info payload (rendered by the web UI modal).
INFO_FIELDS = frozenset({"name", "alias", "paired", "bonded", "trusted", "blocked", "connected", "class", "icon"})

_LIST_CTRL_RE = re.compile(r"^Controller\s+([0-9A-Fa-f:]{17})\s*(.*)$")
_INFO_HEADER_RE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\b(.*)$")
_DEV_PAT = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_MAC_SHAPED_NAME_RE = re.compile(r"^[0-9A-Fa-f]{2}[-:]")

# Scan-stream grammars (previously routes/api_bt.py module constants).
_NEW_DEV_PAT = re.compile(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_CHG_NAME_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)")
_CHG_RSSI_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:")
# bluetoothctl emits two RSSI formats — modern decimal (``RSSI: -43``) and
# legacy parenthesised hex (``RSSI: 0xffffffd5 (-43)``).  The parenthesised
# decimal takes priority; otherwise the trailing signed integer is the value.
_CHG_RSSI_VALUE_PAT = re.compile(
    r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:\s*"
    r"(?:0x[0-9A-Fa-f]+\s*\((-?\d+)\)|(-?\d+))"
)
_INFO_RSSI_PAT = re.compile(
    r"^\s*RSSI:\s*(?:0x[0-9A-Fa-f]+\s*\((-?\d+)\)|(-?\d+))",
    re.MULTILINE,
)
_SHOW_CTRL_PAT = re.compile(r"^Controller\s+([0-9A-Fa-f:]{17})")
_SHOW_DEV_PAT = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})")
# A controller that refuses discovery answers ``scan bredr`` with a D-Bus
# error and then emits nothing at all — indistinguishable from an empty
# room unless the refusal itself is captured.
_DISCOVERY_FAIL_PAT = re.compile(r"^Failed to start discovery:\s*(.+)$", re.IGNORECASE)

# Caps the connect-output excerpt so a chatty transcript can't flood a
# single log line.
_EXCERPT_MAX_LEN = 200


@dataclass(frozen=True, slots=True)
class AdapterRef:
    """One controller row from ``bluetoothctl list``."""

    mac: str
    name: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """Parsed ``show`` output for one controller."""

    mac: str = ""
    name: str = ""
    alias: str = ""
    powered: bool = False
    present: bool = False
    no_default: bool = False
    outcome: Outcome = Outcome.OK


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Parsed ``info <MAC>`` output.

    ``raw`` reproduces the /api/bt/info payload exactly: ANSI-stripped,
    whitespace-stripped, non-empty *source* lines built from **stdout
    only** — merging stderr would leak transport noise into the UI modal.
    ``fields`` carries only the public :data:`INFO_FIELDS` keys.

    Tri-state contract: the boolean properties are ``bool`` only when the
    device is present and an explicit field parsed; transport failures
    (``TIMEOUT``/``UNAVAILABLE``) and ``present=False`` all yield ``None``
    so callers never escalate to re-pair on a transient timeout.
    """

    mac: str
    present: bool
    fields: dict[str, str]
    raw: tuple[str, ...]
    outcome: Outcome = Outcome.OK
    # ``Device <MAC> not available`` — the controller answered and knows
    # nothing about this device.  Distinct from ``present=False`` alone,
    # which also covers output that carried no info block at all.
    not_available: bool = False

    def _field_bool(self, key: str) -> bool | None:
        if self.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE) or not self.present:
            return None
        value = self.fields.get(key)
        if value is None:
            return None
        return value.lower() == "yes"

    @property
    def paired(self) -> bool | None:
        return self._field_bool("paired")

    @property
    def bonded(self) -> bool | None:
        return self._field_bool("bonded")

    @property
    def trusted(self) -> bool | None:
        return self._field_bool("trusted")

    @property
    def blocked(self) -> bool | None:
        return self._field_bool("blocked")

    @property
    def connected(self) -> bool | None:
        return self._field_bool("connected")

    @property
    def name(self) -> str:
        return self.fields.get("name", "")

    @property
    def alias(self) -> str:
        return self.fields.get("alias", "")

    @property
    def device_class(self) -> str:
        return self.fields.get("class", "")

    @property
    def icon(self) -> str:
        return self.fields.get("icon", "")

    @property
    def text(self) -> str:
        """The raw lines rejoined — input for domain classifiers."""
        return "\n".join(self.raw)

    @property
    def rssi(self) -> int | None:
        """The ``RSSI: <dB>`` value, tolerating both bluetoothctl formats."""
        match = _INFO_RSSI_PAT.search(self.text)
        if not match:
            return None
        value = match.group(1) or match.group(2)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True, slots=True)
class DeviceEntry:
    """One ``(mac, name)`` row from ``devices [filter]``.

    Iterable as a 2-tuple so legacy ``for mac, name in pairs`` unpacking
    keeps working during the migration.
    """

    mac: str
    name: str

    def __iter__(self):
        yield self.mac
        yield self.name


@dataclass(frozen=True, slots=True)
class ScanTranscript:
    """Structured result of a scan composite: classified lines + attribution."""

    lines: tuple[BluezLine, ...] = ()
    seen_macs: frozenset[str] = frozenset()
    names: dict[str, str] = field(default_factory=dict)
    device_adapter: dict[str, str] = field(default_factory=dict)
    active_macs: frozenset[str] = frozenset()
    rssi_by_mac: dict[str, int] = field(default_factory=dict)
    discovery_errors: tuple[str, ...] = ()


def parse_version(stdout: str) -> str:
    """``bluetoothctl --version`` → e.g. ``"bluetoothctl: 5.72"``."""
    return strip_ansi(stdout).strip()


def parse_adapter_list(stdout: str) -> list[AdapterRef]:
    """Parse ``bluetoothctl list`` output into controller rows."""
    adapters: list[AdapterRef] = []
    for raw in stdout.splitlines():
        clean = strip_ansi(raw).strip()
        match = _LIST_CTRL_RE.match(clean)
        if not match:
            continue
        rest = match.group(2).strip()
        is_default = "[default]" in rest.lower()
        name = re.sub(r"\[default\]", "", rest, flags=re.IGNORECASE).strip()
        adapters.append(AdapterRef(mac=match.group(1).upper(), name=name, is_default=is_default))
    return adapters


def parse_show(stdout: str, *, outcome: Outcome = Outcome.OK) -> AdapterInfo:
    """Parse ``show [<MAC>]`` output for one controller."""
    info = AdapterInfo(outcome=outcome)
    if "no default controller" in stdout.lower():
        return AdapterInfo(no_default=True, outcome=outcome)
    mac = ""
    alias = ""
    name = ""
    powered = False
    for raw in stdout.splitlines():
        line = strip_ansi(raw).strip()
        if not mac:
            match = _SHOW_CTRL_PAT.match(line)
            if match:
                mac = match.group(1).upper()
            continue
        # Property lines are tab-indented under the Controller block; async
        # events (``[CHG] Controller ... Pairable: yes``) never match because
        # they don't start with the bare property token after trimming.
        if line.startswith("Alias:"):
            value = line.split("Alias:", 1)[1].strip()
            if value and not alias:
                alias = value
        elif line.startswith("Name:"):
            value = line.split("Name:", 1)[1].strip()
            if value and not name:
                name = value
        elif line.startswith("Powered:"):
            powered = "yes" in line.split("Powered:", 1)[1].lower()
    if not mac:
        return info
    return AdapterInfo(mac=mac, name=name, alias=alias, powered=powered, present=True, outcome=outcome)


def parse_device_info(stdout: str, mac: str, *, outcome: Outcome = Outcome.OK) -> DeviceInfo:
    """Parse ``info <MAC>`` output into a :class:`DeviceInfo`.

    ``raw`` is what the /api/bt/info modal renders verbatim, so it carries
    CONTENT lines only — the prompt-echo glue, the daemon banner and
    ``Agent registered``-style banners are transport noise, not payload.
    """
    from ._lines import classify_lines

    raw_lines = tuple(line.text for line in classify_lines(stdout) if line.kind is LineKind.CONTENT)
    fields: dict[str, str] = {}
    present = False
    for line in raw_lines:
        if ":" in line:
            key, _, val = line.partition(":")
            normalised = key.strip().lower().replace(" ", "_")
            if normalised in INFO_FIELDS:
                fields[normalised] = val.strip()
        header = _INFO_HEADER_RE.match(line)
        if header and header.group(1).upper() == mac.upper():
            if "not available" in header.group(2).lower():
                return DeviceInfo(
                    mac=mac,
                    present=False,
                    fields={},
                    raw=raw_lines,
                    outcome=outcome,
                    not_available=True,
                )
            present = True
    return DeviceInfo(mac=mac, present=present, fields=fields, raw=raw_lines, outcome=outcome)


def parse_devices(stdout: str) -> list[DeviceEntry]:
    """Parse ``devices [filter]`` output — the paired-list whitelist.

    Only CONTENT lines starting with ``Device `` count; async discovery
    notifications and prompt echoes are excluded by line classification.
    MAC-shaped names (``AA:BB:…`` / ``AA-BB-…``) normalise to ``""`` so
    downstream filters can treat them as unnamed.
    """
    from ._lines import classify_lines

    entries: list[DeviceEntry] = []
    for line in classify_lines(stdout):
        if line.kind is not LineKind.CONTENT or not line.text.startswith("Device "):
            continue
        match = _DEV_PAT.match(line.text)
        if not match:
            continue
        mac = match.group(1).upper()
        name = match.group(2).strip()
        if _MAC_SHAPED_NAME_RE.match(name):
            name = ""
        entries.append(DeviceEntry(mac=mac, name=name))
    return entries


def parse_scan(lines: tuple[BluezLine, ...] | list[BluezLine]) -> ScanTranscript:
    """Parse a scan composite's classified lines.

    ``[NEW]``/``[CHG]`` stream events feed ``seen_macs``/``names``/
    ``active_macs``/``rssi_by_mac``; the per-adapter ``show`` + ``devices``
    enumeration blocks feed ``device_adapter`` (each device is attributed
    to the controller whose ``show`` marker most recently appeared).
    """
    seen: set[str] = set()
    names: dict[str, str] = {}
    device_adapter: dict[str, str] = {}
    active_macs: set[str] = set()
    rssi_by_mac: dict[str, int] = {}
    discovery_errors: list[str] = []
    current_show_adapter = ""
    for line in lines:
        clean = line.text
        if not clean:
            continue
        refused = _DISCOVERY_FAIL_PAT.match(clean)
        if refused:
            reason = refused.group(1).strip()
            if reason not in discovery_errors:
                discovery_errors.append(reason)
            continue
        if not clean.startswith("["):
            ctrl = _SHOW_CTRL_PAT.match(clean)
            if ctrl:
                current_show_adapter = ctrl.group(1).upper()
                continue
            if current_show_adapter:
                dev = _SHOW_DEV_PAT.match(clean)
                if dev:
                    device_adapter.setdefault(dev.group(1).upper(), current_show_adapter)
                    continue
        new = _NEW_DEV_PAT.search(clean)
        if new:
            mac = new.group(1).upper()
            name = new.group(2).strip()
            seen.add(mac)
            if name and not _MAC_SHAPED_NAME_RE.match(name):
                names[mac] = name
            continue
        chg_name = _CHG_NAME_PAT.search(clean)
        if chg_name:
            names[chg_name.group(1).upper()] = chg_name.group(2).strip()
            continue
        chg_rssi = _CHG_RSSI_PAT.search(clean)
        if chg_rssi:
            mac = chg_rssi.group(1).upper()
            active_macs.add(mac)
            value_match = _CHG_RSSI_VALUE_PAT.search(clean)
            if value_match:
                value = value_match.group(2) or value_match.group(3)
                try:
                    rssi_by_mac[mac] = int(value)
                except (TypeError, ValueError):
                    pass
    return ScanTranscript(
        lines=tuple(lines),
        seen_macs=frozenset(seen),
        names=names,
        device_adapter=device_adapter,
        active_macs=frozenset(active_macs),
        rssi_by_mac=rssi_by_mac,
        discovery_errors=tuple(discovery_errors),
    )


def summarize_connect_output(lines: tuple[BluezLine, ...] | list[BluezLine]) -> str:
    """Reduce connect-output lines to one diagnostic line.

    Prefers a line containing the BlueZ error fingerprint (``Failed to
    connect: …``); otherwise falls back to the last CONTENT line.  Returns
    an empty string when the lines carry no usable signal — the caller
    then falls back to the bare warning.
    """
    cleaned = [line.text for line in lines if line.kind is LineKind.CONTENT and line.text]
    if not cleaned:
        return ""
    for text in cleaned:
        if "failed to connect" in text.lower():
            return text[:_EXCERPT_MAX_LEN]
    return cleaned[-1][:_EXCERPT_MAX_LEN]
