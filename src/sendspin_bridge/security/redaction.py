"""Keeping credentials out of text an operator is going to send us.

The diagnostics bundle masks MAC and IPv4 addresses, which is what a bug
report needs — but it knows nothing about credentials, and the log ring it
draws from carries whatever the code logged.  Three branches in `ma_auth.py`
log a whole Music Assistant response at WARNING, and those are exactly the
branches taken when a token arrives in a shape the code did not anticipate.

Redaction here is deliberately shape-based rather than value-based: the
bridge cannot enumerate every secret it might be handed, but it can
recognise the containers they travel in — a bearer header, a field whose
name says credential, a JWT-looking run of characters.
"""

from __future__ import annotations

import re

__all__ = ["REDACTED", "redact"]

REDACTED = "***REDACTED***"

#: Field names that carry a credential whatever their value looks like.
_SECRET_KEYS = (
    "token",
    "access_token",
    "refresh_token",
    "api_token",
    "id_token",
    "session_token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "authorization",
)

_KEY_ALTERNATION = "|".join(sorted(_SECRET_KEYS, key=len, reverse=True))

#: ``"access_token": "…"`` and ``'access_token': '…'`` — JSON and the repr of
#: a dict, which is what a ``%s`` of a response produces.
#: Each quoting style is matched on its own so a value may contain the
#: other quote — `{"token": "ab'cd"}` is ordinary JSON, and a single pattern
#: that banned both characters simply failed to match and let it through.
_QUOTED_FIELD_RE = re.compile(
    rf"""(?P<prefix>['"](?:{_KEY_ALTERNATION})['"]\s*:\s*)"""
    rf"""(?:"(?P<dq>(?:[^"\\]|\\.)*)"|'(?P<sq>(?:[^'\\]|\\.)*)')""",
    re.IGNORECASE,
)

#: ``access_token=…`` in a query string or a form body.
_ASSIGNED_FIELD_RE = re.compile(
    rf"(?P<prefix>\b(?:{_KEY_ALTERNATION})\s*=\s*)(?P<value>[^\s&;'\"]+)",
    re.IGNORECASE,
)

#: ``Authorization: Bearer …`` and bare ``Bearer …``.
_BEARER_RE = re.compile(r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._\-+/=]{8,})", re.IGNORECASE)

#: A JWT loose in running text — three base64url segments joined by dots.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")


def _replace_value(match: re.Match) -> str:
    if match.group("value") == REDACTED:
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED}"


def _replace_quoted(match: re.Match) -> str:
    quote = '"' if match.group("dq") is not None else "'"
    value = match.group("dq") if quote == '"' else match.group("sq")
    if value == REDACTED:
        return match.group(0)
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


def redact(text: str | None) -> str:
    """Replace anything credential-shaped in *text* with a marker.

    The surrounding structure is left intact so a redacted line still tells
    the operator what happened — the point is to keep the bug report useful,
    not to erase it.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = _QUOTED_FIELD_RE.sub(_replace_quoted, text)
    text = _ASSIGNED_FIELD_RE.sub(_replace_value, text)
    text = _BEARER_RE.sub(_replace_value, text)
    return _JWT_RE.sub(REDACTED, text)
