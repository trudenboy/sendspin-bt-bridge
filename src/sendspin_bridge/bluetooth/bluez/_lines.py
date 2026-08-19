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


# All three historical copies of this regex were byte-identical.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ``[name]> `` prompt-echo prefix (the grammar the paired-list whitelist
# strips).  Anchored so bracketed async notifications (``[CHG] `` etc.)
# survive the strip and keep failing downstream ``Device `` checks.
_PROMPT_GT_RE = re.compile(r"^\[[^\]]+\]>\s*")

# Well-known interactive prompts of either grammar (``[bluetooth]#``,
# ``[bluetoothctl]>``).  Lines led by one of these are prompt output or
# prompt-echoed input — never command output.
_KNOWN_PROMPT_RE = re.compile(r"^\[(?:bluetooth|bluetoothctl)\][#>]", re.IGNORECASE)

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
    text = _PROMPT_GT_RE.sub("", strip_ansi(raw)).strip()

    if not stripped:
        return BluezLine(raw=raw, text="", kind=LineKind.EMPTY)

    # Prompt detection runs on the *unstripped-prefix* form so both
    # grammars are recognised: a line led by ``[bluetooth]#`` /
    # ``[bluetoothctl]>`` (prompt + optional echoed command), and any
    # line *ending* in ``]#`` / ``]>`` (bare prompts of any name,
    # including device-named ones, plus the piped-mode startup line
    # where the bluetoothd banner is glued to the first prompt).
    if _KNOWN_PROMPT_RE.match(stripped) or stripped.endswith(("]#", "]>")):
        return BluezLine(raw=raw, text=text if not stripped.endswith(("]#", "]>")) else "", kind=LineKind.PROMPT)

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
