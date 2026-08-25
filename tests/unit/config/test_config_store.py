"""One owner for reading, migrating and writing the config.

The read held the lock while the migration and the write that followed it did
not, four separate implementations of the atomic write had grown up around
it, and the route layer opened the file directly ten times over.

The store also closes a data-loss path.  A file whose JSON fails to parse is
backed up before the bridge falls back to defaults; a file that parses into
something that is not an object — `[1, 2, 3]`, `"text"`, `null` — took a
different branch that logged a warning and took no backup, so the operator's
config was replaced by defaults on the next write with no way back.
"""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.config.store import ConfigStore


@pytest.fixture()
def store(tmp_path):
    return ConfigStore(tmp_path / "config.json")


def _written(store) -> dict:
    return json.loads(store.path.read_text())


# ── reading ──────────────────────────────────────────────────────────────


def test_a_missing_file_reads_as_defaults(store):
    config = store.load()

    assert config["SENDSPIN_PORT"] == 8927
    assert store.path.exists() is False, "reading must not create the file"


def test_a_stored_value_wins_over_the_default(store):
    store.path.write_text(json.dumps({"BRIDGE_NAME": "Kitchen"}))

    assert store.load()["BRIDGE_NAME"] == "Kitchen"


def test_keys_absent_from_the_file_still_come_back(store):
    store.path.write_text(json.dumps({"BRIDGE_NAME": "Kitchen"}))

    assert store.load()["SENDSPIN_PORT"] == 8927


# ── the corruption paths ─────────────────────────────────────────────────


def test_unparseable_json_is_backed_up_before_falling_back(store):
    store.path.write_text('{"BRIDGE_NAME": "Kitch')

    config = store.load()

    assert config["SENDSPIN_PORT"] == 8927
    backups = list(store.path.parent.glob("config.json.corrupt-*"))
    assert backups, "the operator's file was replaced with no way back"
    assert backups[0].read_text() == '{"BRIDGE_NAME": "Kitch'


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"just a string"', "null", "42"])
def test_json_that_is_not_an_object_is_backed_up_too(store, payload):
    """This branch used to warn and move on, losing the file silently."""
    store.path.write_text(payload)

    config = store.load()

    assert config["SENDSPIN_PORT"] == 8927
    backups = list(store.path.parent.glob("config.json.corrupt-*"))
    assert backups, f"{payload} was discarded without a backup"
    assert backups[0].read_text() == payload


def test_an_unreadable_file_is_reported_rather_than_silently_defaulted(store, monkeypatch):
    """A permissions problem is not the same as a corrupt file."""
    store.path.write_text(json.dumps({"BRIDGE_NAME": "Kitchen"}))

    def _boom(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(store.path), "read_text", _boom)

    with pytest.raises(OSError):
        store.load()


# ── writing ──────────────────────────────────────────────────────────────


def test_a_mutation_is_persisted(store):
    store.mutate(lambda cfg: cfg.__setitem__("BRIDGE_NAME", "Study"))

    assert _written(store)["BRIDGE_NAME"] == "Study"


def test_the_file_is_replaced_atomically(store):
    store.mutate(lambda cfg: cfg.__setitem__("BRIDGE_NAME", "Study"))

    leftovers = [p.name for p in store.path.parent.iterdir() if p.name != "config.json"]
    assert leftovers == [], f"a temporary file survived: {leftovers}"


def test_a_failed_write_leaves_the_previous_file_intact(store, monkeypatch):
    store.mutate(lambda cfg: cfg.__setitem__("BRIDGE_NAME", "Study"))
    before = store.path.read_text()

    import os

    monkeypatch.setattr(os, "replace", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError):
        store.mutate(lambda cfg: cfg.__setitem__("BRIDGE_NAME", "Lost"))

    assert store.path.read_text() == before


# ── replace, as one operation ────────────────────────────────────────────


def test_replace_carries_the_named_keys_forward(store):
    store.path.write_text(json.dumps({"AUTH_PASSWORD_HASH": "v1:600000:aa", "BRIDGE_NAME": "Old"}))

    store.replace({"BRIDGE_NAME": "New"}, preserve=["AUTH_PASSWORD_HASH"])

    written = _written(store)
    assert written["BRIDGE_NAME"] == "New"
    assert written["AUTH_PASSWORD_HASH"] == "v1:600000:aa"


def test_replace_lets_the_caller_overwrite_a_preserved_key(store):
    store.path.write_text(json.dumps({"AUTH_PASSWORD_HASH": "old"}))

    store.replace({"AUTH_PASSWORD_HASH": "new"}, preserve=["AUTH_PASSWORD_HASH"])

    assert _written(store)["AUTH_PASSWORD_HASH"] == "new"


def test_replace_refuses_rather_than_erasing_what_it_cannot_read(store):
    """The whole reason `preserve` exists: an unreadable file must not win."""
    store.path.write_text('{"AUTH_PASSWORD_HASH": "v1:600')
    before = store.path.read_text()

    with pytest.raises(ValueError):
        store.replace({"BRIDGE_NAME": "New"}, preserve=["AUTH_PASSWORD_HASH"], backup_corrupt=False)

    assert store.path.read_text() == before


def test_replace_returns_the_previous_contents_for_diffing(store):
    store.path.write_text(json.dumps({"BRIDGE_NAME": "Old"}))

    previous = store.replace({"BRIDGE_NAME": "New"}, preserve=[])

    assert previous["BRIDGE_NAME"] == "Old"
