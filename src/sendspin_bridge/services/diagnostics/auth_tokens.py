"""Long-lived API bearer tokens used by the HA custom_component.

Tokens are minted at ``POST /api/auth/tokens``; the plain value is
returned **once** and never persisted.  The stored form is a
PBKDF2-SHA256 hash sharing the same parameters as the password hash
helper in ``config_auth.py``.

Storage shape (under ``config["AUTH_TOKENS"]``)::

    [
        {
            "id": "abc123...",        # short random hex
            "label": "ha-custom-component",
            "token_hash": "v1:600000:salt_hex:hash_hex",
            "created": "2026-04-28T12:00:00+00:00",
            "last_used": "2026-04-28T12:34:56+00:00" | null
        },
        ...
    ]
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

from config import CONFIG_FILE, config_lock, load_config

logger = logging.getLogger(__name__)

UTC = timezone.utc

_PBKDF2_ITERS = 600_000  # match config_auth.py
_TOKEN_BYTES = 32  # 256 bits → 64 hex chars


# ---------------------------------------------------------------------------
# Token primitives
# ---------------------------------------------------------------------------


def mint_token() -> tuple[str, str]:
    """Generate a fresh ``(plain_token, token_id)`` pair.

    The plain token is what the caller stores in HA; the ID is a short
    handle the bridge UI uses to list / revoke without revealing the hash.
    """
    plain = secrets.token_urlsafe(_TOKEN_BYTES)
    token_id = secrets.token_hex(8)
    return plain, token_id


def hash_token(plain: str) -> str:
    """Return ``v1:iters:salt_hex:hash_hex`` for the given plaintext token.

    Same scheme as ``config_auth.hash_password`` for code consistency.
    """
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERS)
    return f"v1:{_PBKDF2_ITERS}:{salt.hex()}:{h.hex()}"


def verify_token(plain: str, stored: str) -> bool:
    try:
        if not stored.startswith("v1:"):
            return False
        _, iters_str, salt_hex, h_hex = stored.split(":", 3)
        iters = int(iters_str)
        salt = bytes.fromhex(salt_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iters)
        return hmac.compare_digest(candidate.hex(), h_hex)
    except (ValueError, TypeError):
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
    tokens = _load_tokens()
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
        if not isinstance(tok, dict):
            continue
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

    tokens = _load_tokens()
    matched_index: int | None = None
    matched_record: TokenRecord | None = None
    for idx, tok in enumerate(tokens):
        if not isinstance(tok, dict):
            continue
        stored = str(tok.get("token_hash") or "")
        if not stored:
            continue
        if verify_token(candidate, stored):
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

    # Update last_used.  Best-effort; failure to persist is non-fatal.
    try:
        tokens[matched_index]["last_used"] = _now_iso()
        _save_tokens(tokens)
    except Exception as exc:  # pragma: no cover
        logger.debug("Failed to persist last_used for token %s: %s", matched_record.id, exc)

    return matched_record


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
