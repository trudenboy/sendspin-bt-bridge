from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def load_or_create_identity(path: Path):
    """Load a persisted X25519 identity, or generate and store one."""
    from aiosendspin.noise.keys import Identity

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            identity = Identity.from_private_bytes(path.read_bytes())
        except (OSError, ValueError):
            backup = path.with_name(f"{path.name}.corrupt")
            suffix = 1
            while backup.exists():
                backup = path.with_name(f"{path.name}.corrupt.{suffix}")
                suffix += 1
            path.chmod(0o600)
            os.replace(path, backup)
        else:
            path.chmod(0o600)
            return identity
    identity = Identity.generate()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = path.with_name(os.path.basename(temporary_name))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(identity.private_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return identity
