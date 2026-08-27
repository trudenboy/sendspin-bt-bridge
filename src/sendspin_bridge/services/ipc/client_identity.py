from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def load_or_create_identity(path: Path):
    """Load a persisted X25519 identity, or generate and store one."""
    from aiosendspin.noise.keys import Identity

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return Identity.from_private_bytes(path.read_bytes())
    identity = Identity.generate()
    path.write_bytes(identity.private_bytes)
    path.chmod(0o600)
    return identity
