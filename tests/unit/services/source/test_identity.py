"""The source client's identity and pairing state survive a restart and stay private."""

from __future__ import annotations

import json
import os

import pytest

from sendspin_bridge.services.source.identity import (
    IDENTITY_FILE,
    PAIRING_FILE,
    load_or_create_identity,
    open_pairing_store,
)


def _mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def test_a_new_identity_is_private(tmp_path):
    state_dir = tmp_path / "state"
    identity = load_or_create_identity(state_dir)

    assert (state_dir / IDENTITY_FILE).is_file()
    assert _mode(state_dir / IDENTITY_FILE) == 0o600
    assert identity.private_bytes


def test_the_identity_is_reused_across_starts(tmp_path):
    first = load_or_create_identity(tmp_path / "state")
    second = load_or_create_identity(tmp_path / "state")

    assert first.peer_id == second.peer_id
    assert first.private_bytes == second.private_bytes


def test_a_corrupt_identity_is_refused_with_a_next_step(tmp_path):
    (tmp_path / IDENTITY_FILE).write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupt"):
        load_or_create_identity(tmp_path)


def test_the_identity_file_holds_no_temporary_after_write(tmp_path):
    load_or_create_identity(tmp_path / "state")

    names = sorted(p.name for p in (tmp_path / "state").iterdir())
    assert names == [IDENTITY_FILE]
    payload = json.loads((tmp_path / "state" / IDENTITY_FILE).read_text(encoding="utf-8"))
    assert "private_key_b64u" in payload


async def test_the_pairing_store_is_created_next_to_the_identity(tmp_path):
    state_dir = tmp_path / "state"
    await open_pairing_store(state_dir)

    assert (state_dir / PAIRING_FILE).exists()
