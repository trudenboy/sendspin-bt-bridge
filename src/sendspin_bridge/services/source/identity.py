"""The source client's persisted identity and Music Assistant pairing state.

The identity is the Sendspin client's private key; the pairing store holds the PSK the
Music Assistant server issued after the operator entered the 8-digit code.  Both must
survive a restart, and neither may be world-readable.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["IDENTITY_FILE", "PAIRING_FILE", "load_or_create_identity", "open_pairing_store"]

logger = logging.getLogger(__name__)

IDENTITY_FILE = "sendspin_source_identity.json"
PAIRING_FILE = "sendspin_source_pairing.json"


def _ensure_state_dir(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)


def _atomic_write_private_json(path: Path, payload: dict[str, str]) -> None:
    """Write ``payload`` atomically, owner-readable only (0600 from creation), durably."""
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with contextlib.suppress(OSError):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(json.dumps(payload) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_create_identity(state_dir: Path) -> Any:
    """Load the persisted Sendspin identity, creating one on first start."""
    from aiosendspin.noise.keys import Identity, b64url_decode, b64url_encode

    _ensure_state_dir(state_dir)
    path = state_dir / IDENTITY_FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Identity.from_private_bytes(b64url_decode(data["private_key_b64u"]))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise RuntimeError(
                f"{path} is corrupt ({exc}); move or delete it to generate a new identity "
                "(the source must then be paired with Music Assistant again)"
            ) from exc
    identity = Identity.generate()
    _atomic_write_private_json(path, {"private_key_b64u": b64url_encode(identity.private_bytes)})
    return identity


async def open_pairing_store(state_dir: Path) -> Any:
    """Open (creating if absent) the client pairing store next to the identity."""
    from aiosendspin.noise.trust_store import FileClientPairingStore

    _ensure_state_dir(state_dir)
    return await FileClientPairingStore.open(state_dir / PAIRING_FILE)
