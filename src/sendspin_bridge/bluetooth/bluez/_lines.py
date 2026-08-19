"""bluetoothctl output hygiene: ANSI strip, prompt grammars, line classification.

One classification serves both historical consumers:

* the connect-summary **blacklist** (formerly
  ``bluetooth.manager._summarize_bluetoothctl_connect_output``) drops
  prompts, async ``[CHG]/[NEW]/[DEL]`` discovery notifications and the
  agent banner, keeping everything else as content;
* the paired-list **whitelist** (formerly
  ``web.routes.api_bt._parse_paired_stdout``) keeps only CONTENT lines
  whose text starts with ``Device ``.

Stdlib only — see ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

__all__ = ["BluezLine", "LineKind", "classify_line", "classify_lines", "strip_ansi"]


class LineKind(Enum):
    PROMPT = auto()
    EVENT_NEW = auto()
    EVENT_CHG = auto()
    EVENT_DEL = auto()
    BANNER = auto()
    CONTENT = auto()
    EMPTY = auto()


# All three historical copies of this regex covered SGR colour codes only
# (``\x1b[…m``).  bluetoothctl also redraws its readline prompt with
# cursor-movement escapes (``\x1b[C``), which then leaked into the device
# info modal, so the full CSI grammar is stripped here.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# ``[name]> `` prompt-echo prefix (the grammar the paired-list whitelist
# strips).  Anchored so bracketed async notifications (``[CHG] `` etc.)
# survive the strip and keep failing downstream ``Device `` checks.
_PROMPT_GT_RE = re.compile(r"^\[[^\]]+\]>\s*")

# Any interactive prompt token, wherever it lands on the line:
# ``[bluetooth]#``, ``[bluetoothctl]>``, and the device-named prompts
# bluetoothctl switches to once a device connects (``[ENEBY Portable]#``).
# With stdin piped, bluetoothctl glues the startup banner, the prompt and
# whatever it emits next onto one physical line, so the prompt is a
# separator inside the line rather than a shape the whole line has.
# Only the ``#`` grammar behaves this way; under the ``]>`` grammar the
# prompt is a prefix on real output lines and is stripped, not dropped.
_PROMPT_TOKEN_RE = re.compile(r"\[[^\[\]]+\]#\s*")

# bluetoothctl echoes the input line after its prompt when stdin is piped.
# The echo is input, not output, so it must not become the connect
# summary's last-line fallback.  Recognised by its verb, because the
# prompt name alone can't distinguish an echo from a real emission that
# happened to land on the prompt line.
_ECHOED_VERBS = frozenset(
    {
        "agent",
        "connect",
        "default-agent",
        "devices",
        "disconnect",
        "discoverable",
        "info",
        "list",
        "menu",
        "pair",
        "pairable",
        "power",
        "quit",
        "remove",
        "scan",
        "select",
        "show",
        "trust",
        "version",
    }
)

_EVENT_RE = re.compile(r"^\[(NEW|CHG|DEL)\]\s+Device\s+([0-9A-Fa-f:]{17})", re.IGNORECASE)
_EVENT_KINDS = {
    "NEW": LineKind.EVENT_NEW,
    "CHG": LineKind.EVENT_CHG,
    "DEL": LineKind.EVENT_DEL,
}

# Transport noise, not command output.  ``agent registered`` is printed on
# stderr-adjacent startup even when the real error lands elsewhere; the
# bluetoothd handshake banner shares the first prompt line in piped mode.
_BANNER_CASEFOLD = frozenset({"agent registered"})


def strip_ansi(text: str) -> str:
    """Remove ANSI colour codes from bluetoothctl output."""
    return _ANSI_RE.sub("", text)


@dataclass(frozen=True, slots=True)
class BluezLine:
    """One classified line of bluetoothctl output.

    ``raw`` is the untouched source line; ``text`` is ANSI-stripped with
    the ``[name]> `` echo prefix removed and surrounding whitespace
    stripped; ``kind`` is the classification; ``event_mac`` is set for
    ``EVENT_*`` kinds only.
    """

    raw: str
    text: str
    kind: LineKind
    event_mac: str | None = None


def classify_line(raw: str) -> BluezLine:
    """Classify a single raw bluetoothctl output line."""
    stripped = strip_ansi(raw).strip()

    if not stripped:
        return BluezLine(raw=raw, text="", kind=LineKind.EMPTY)

    # Everything up to and including the last prompt token is prompt
    # redraw — banner text, cursor repositioning, the echoed input line.
    # What follows on the same line is the real emission and is classified
    # on its own merits; nothing left means the line was only a prompt.
    prompts = list(_PROMPT_TOKEN_RE.finditer(stripped))
    if prompts:
        remainder = stripped[prompts[-1].end() :].strip()
        is_echo = (
            remainder.casefold() not in _BANNER_CASEFOLD and remainder.split(maxsplit=1)[0].casefold() in _ECHOED_VERBS
            if remainder
            else False
        )
        if not remainder or is_echo:
            return BluezLine(raw=raw, text="", kind=LineKind.PROMPT)
        stripped = remainder

    text = _PROMPT_GT_RE.sub("", stripped).strip()
    if not text:
        # A bare ``[bluetoothctl]>`` prompt: the whole line was the prefix.
        return BluezLine(raw=raw, text="", kind=LineKind.PROMPT)

    event = _EVENT_RE.match(text)
    if event:
        return BluezLine(
            raw=raw,
            text=text,
            kind=_EVENT_KINDS[event.group(1).upper()],
            event_mac=event.group(2).upper(),
        )

    if text.casefold() in _BANNER_CASEFOLD:
        return BluezLine(raw=raw, text=text, kind=LineKind.BANNER)

    return BluezLine(raw=raw, text=text, kind=LineKind.CONTENT)


def classify_lines(output: str) -> tuple[BluezLine, ...]:
    """Classify every line of a bluetoothctl output blob."""
    return tuple(classify_line(raw) for raw in output.splitlines())
