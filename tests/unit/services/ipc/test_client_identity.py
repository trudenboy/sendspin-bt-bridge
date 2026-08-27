import os
from pathlib import Path

import pytest

from sendspin_bridge.services.ipc.client_identity import load_or_create_identity


def test_load_or_create_identity_round_trip(tmp_path: Path):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"
    first = load_or_create_identity(path)
    second = load_or_create_identity(path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert first.private_bytes == second.private_bytes
    assert first.peer_id == second.peer_id


def test_load_repairs_permissions_without_rotating_identity(tmp_path: Path):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"
    first = load_or_create_identity(path)
    path.chmod(0o644)

    loaded = load_or_create_identity(path)

    assert loaded.peer_id == first.peer_id
    assert path.stat().st_mode & 0o777 == 0o600


def test_corrupt_identity_is_backed_up_before_replacement(tmp_path: Path):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"
    path.write_bytes(b"not-an-x25519-private-key")

    identity = load_or_create_identity(path)

    assert len(identity.private_bytes) == 32
    assert path.read_bytes() == identity.private_bytes
    backups = list(tmp_path.glob("id.key.corrupt*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"not-an-x25519-private-key"
    assert backups[0].stat().st_mode & 0o777 == 0o600


def test_identity_creation_uses_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)

    load_or_create_identity(path)

    assert replacements
    assert replacements[-1][1] == path
    assert not replacements[-1][0].exists()


def test_identity_creation_survives_filesystem_without_fchmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"

    def unsupported_fchmod(_fd, _mode):
        raise OSError("chmod is not supported")

    monkeypatch.setattr(os, "fchmod", unsupported_fchmod)

    identity = load_or_create_identity(path)

    assert path.read_bytes() == identity.private_bytes


def test_existing_identity_survives_filesystem_without_chmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("aiosendspin.noise.keys")
    path = tmp_path / "id.key"
    original = load_or_create_identity(path)

    def unsupported_chmod(self, _mode):
        if self == path:
            raise OSError("chmod is not supported")

    monkeypatch.setattr(Path, "chmod", unsupported_chmod)

    assert load_or_create_identity(path).private_bytes == original.private_bytes
