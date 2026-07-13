"""Long-lived API bearer tokens used by the HA custom_component.

Tokens are minted at ``POST /api/auth/tokens``; the plain value is
returned **once** and never persisted.

The plaintext has the shape ``"{token_id}.{secret}"`` — the id is a
non-secret handle that lets lookup resolve the exact record in O(1)
instead of scanning every stored hash.  Because the ``secret`` is 256
bits of CSPRNG output, a single SHA-256 is sufficient (a stretching KDF
buys nothing against a random secret and previously turned every bearer
request into a 600k-iteration PBKDF2 scan — a CPU-exhaustion DoS vector).

Storage shape (under ``config["AUTH_TOKENS"]``)::

    [
        {
            "id": "abc123...",        # short random hex, == plaintext prefix
            "label": "ha-custom-component",
            "token_hash": "v2:sha256_hex",
            "created": "2026-04-28T12:00:00+00:00",
            "last_used": "2026-04-28T12:34:56+00:00" | null
        },
        ...
    ]

Legacy ``v1:`` (PBKDF2) hashes are **invalidated** on upgrade: they never
verify and are pruned on the next write, so deployed integrations re-pair
once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sendspin_bridge.config import CONFIG_FILE, config_lock, load_config

logger = logging.getLogger(__name__)

UTC = timezone.utc

_TOKEN_BYTES = 32  # 256 bits of secret
# Only rewrite ``last_used`` when the recorded value is older than this, so
# a polling HA integration doesn't rewrite config.json on every request.
_LAST_USED_THROTTLE_S = 600


# ---------------------------------------------------------------------------
# Token primitives
# ---------------------------------------------------------------------------


def mint_token() -> tuple[str, str]:
    """Generate a fresh ``(plain_token, token_id)`` pair.

    ``plain_token`` is ``"{token_id}.{secret}"``; the caller stores it in
    HA.  The embedded id lets :func:`find_matching_token` resolve the record
    directly, and the UI uses it to list / revoke without touching the hash.
    """
    token_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(_TOKEN_BYTES)
    return f"{token_id}.{secret}", token_id


def hash_token(plain: str) -> str:
    """Return ``v2:sha256_hex`` for the given plaintext token."""
    digest = hashlib.sha256(plain.encode()).hexdigest()
    return f"v2:{digest}"


def _token_id_of(plain: str) -> str:
    """Extract the non-secret id prefix from a plaintext token."""
    return plain.split(".", 1)[0]


def verify_token(plain: str, stored: str) -> bool:
    """Constant-time verify against a ``v2:`` hash.

    Legacy ``v1:`` hashes always return False (invalidated on upgrade).
    """
    try:
        if not stored.startswith("v2:"):
            return False
        expected = stored[3:]
        candidate = hashlib.sha256(plain.encode()).hexdigest()
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Storage helpers (config.json round-trips)
# ---------------------------------------------------------------------------


@dataclass
class TokenRecord:
    id: str
    label: str
    created: str
    last_used: str | None = None
    token_hash: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        """Public representation — ``token_hash`` and plaintext NEVER leak."""
        return {
            "id": self.id,
            "label": self.label,
            "created": self.created,
            "last_used": self.last_used,
        }


def _is_current_format(tok: Any) -> bool:
    """True for a well-formed v2 record; False for legacy/invalid entries."""
    return isinstance(tok, dict) and str(tok.get("token_hash") or "").startswith("v2:")


def _load_tokens() -> list[dict[str, Any]]:
    config = load_config()
    return list(config.get("AUTH_TOKENS") or [])


def _save_tokens(tokens: list[dict[str, Any]]) -> None:
    """Persist ``AUTH_TOKENS`` under ``config_lock`` without disturbing other keys."""
    with config_lock:
        try:
            with open(CONFIG_FILE) as fh:
                config = json.load(fh)
        except FileNotFoundError:
            config = dict(load_config())
        config["AUTH_TOKENS"] = list(tokens)
        tmp_path = f"{CONFIG_FILE}.tmp"
        with open(tmp_path, "w") as fh:
            json.dump(config, fh, indent=2)
        os.replace(tmp_path, CONFIG_FILE)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def issue_token(label: str) -> tuple[str, TokenRecord]:
    """Mint a new token and persist its hash.

    Returns ``(plain_token, record)``.  The plaintext is the caller's only
    chance to see this token — the bridge keeps only the hash.
    """
    label = (label or "unnamed").strip()[:64]
    plain, token_id = mint_token()
    record = TokenRecord(
        id=token_id,
        label=label,
        created=_now_iso(),
        last_used=None,
        token_hash=hash_token(plain),
    )
    # Prune any invalidated legacy (v1) records on this write.
    tokens = [t for t in _load_tokens() if _is_current_format(t)]
    tokens.append(
        {
            "id": record.id,
            "label": record.label,
            "created": record.created,
            "last_used": record.last_used,
            "token_hash": record.token_hash,
        }
    )
    _save_tokens(tokens)
    return plain, record


def list_tokens() -> list[TokenRecord]:
    """Public list (no plaintext, no hashes)."""
    out: list[TokenRecord] = []
    for tok in _load_tokens():
        if not _is_current_format(tok):
            continue  # legacy/invalidated tokens are not shown
        out.append(
            TokenRecord(
                id=str(tok.get("id") or ""),
                label=str(tok.get("label") or ""),
                created=str(tok.get("created") or ""),
                last_used=tok.get("last_used"),
                token_hash="",  # never expose
            )
        )
    return out


def revoke_token(token_id: str) -> bool:
    """Delete a token by ID.  Returns True iff one was removed."""
    target = (token_id or "").strip()
    if not target:
        return False
    tokens = _load_tokens()
    new_tokens = [t for t in tokens if isinstance(t, dict) and str(t.get("id") or "") != target]
    if len(new_tokens) == len(tokens):
        return False
    _save_tokens(new_tokens)
    return True


def find_matching_token(plain: str) -> TokenRecord | None:
    """Resolve a presented plaintext token against stored hashes.

    Returns the matching record (with the hash redacted from the public
    representation, but ``id`` populated) or ``None`` if no match.  Bumps
    ``last_used`` as a side effect on a match.
    """
    candidate = (plain or "").strip()
    if not candidate:
        return None

    # O(1): the plaintext carries its own id, so resolve the single record
    # directly and verify only that one hash — no scan over every token.
    token_id = _token_id_of(candidate)
    tokens = _load_tokens()
    matched_index: int | None = None
    matched_record: TokenRecord | None = None
    for idx, tok in enumerate(tokens):
        if not _is_current_format(tok):
            continue
        if str(tok.get("id") or "") != token_id:
            continue
        if verify_token(candidate, str(tok.get("token_hash") or "")):
            matched_index = idx
            matched_record = TokenRecord(
                id=str(tok.get("id") or ""),
                label=str(tok.get("label") or ""),
                created=str(tok.get("created") or ""),
                last_used=tok.get("last_used"),
                token_hash="",
            )
        break

    if matched_record is None or matched_index is None:
        return None

    # Update last_used, but throttled: a polling integration must not rewrite
    # config.json on every request (flash/SD wear on Pi/HAOS targets).
    if _last_used_is_stale(tokens[matched_index].get("last_used")):
        try:
            tokens[matched_index]["last_used"] = _now_iso()
            _save_tokens(tokens)
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to persist last_used for token %s: %s", matched_record.id, exc)

    return matched_record


def _last_used_is_stale(last_used: Any) -> bool:
    """True if ``last_used`` is absent or older than the throttle window."""
    if not last_used:
        return True
    try:
        prev = datetime.fromisoformat(str(last_used))
    except (ValueError, TypeError):
        return True
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=UTC)
    return (datetime.now(tz=UTC) - prev).total_seconds() >= _LAST_USED_THROTTLE_S


def extract_bearer(headers) -> str | None:
    """Pull a bearer token out of an ``Authorization`` header dict-like."""
    auth = headers.get("Authorization") if hasattr(headers, "get") else None
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


__all__ = [
    "TokenRecord",
    "extract_bearer",
    "find_matching_token",
    "hash_token",
    "issue_token",
    "list_tokens",
    "revoke_token",
    "verify_token",
]
