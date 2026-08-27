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
