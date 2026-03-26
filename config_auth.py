"""Password hashing utilities (PBKDF2-SHA256).

Hash format history:
  Legacy   — ``salt_hex:hash_hex``  (260 000 iterations, implicit)
  v1       — ``v1:iterations:salt_hex:hash_hex``  (explicit iteration count)

``check_password`` accepts both formats so existing hashes keep working.
New hashes are always created in v1 format.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256
_LEGACY_ITERS = 260_000


def hash_password(plain: str) -> str:
    """Return a versioned PBKDF2-SHA256 hash (``v1:iters:salt_hex:hash_hex``)."""
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERS)
    return f"v1:{_PBKDF2_ITERS}:{salt.hex()}:{h.hex()}"


def check_password(plain: str, stored: str) -> bool:
    """Verify *plain* against *stored* hash (v1 or legacy format)."""
    try:
        if stored.startswith("v1:"):
            _, iters_str, salt_hex, h_hex = stored.split(":", 3)
            iters = int(iters_str)
        else:
            # Legacy format: salt_hex:hash_hex
            salt_hex, h_hex = stored.split(":", 1)
            iters = _LEGACY_ITERS
        salt = bytes.fromhex(salt_hex)
        h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, iters)
        return hmac.compare_digest(h.hex(), h_hex)
    except (ValueError, TypeError):
        return False
