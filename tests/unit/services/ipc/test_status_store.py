"""The daemon's status has one owner, and a group of keys lands at once.

Five writers shared the raw dict — the aiosendspin callbacks, the log
handler, the command reader, the telemetry watcher and the PulseAudio volume
tap.  Under the GIL a single assignment and a single `dict()` copy are each
atomic, so the interesting race is not the copy: it is a *group* of related
keys written one at a time.  `_record_reanchor_status` wrote eight of them
that way, so a status emission landing mid-group published a re-anchor count
that had gone up while `reanchoring` was still False.  Measured on this
interpreter, a reader sees such a group torn hundreds of thousands of times a
second.

`patch()` is the fix: one call, one update, nothing observable in between.
"""

from __future__ import annotations

import threading

from sendspin_bridge.services.ipc.status_store import StatusStore


def test_a_patch_is_visible_in_the_snapshot():
    store = StatusStore({"volume": 50})

    store.patch({"volume": 70, "muted": True})

    assert store.snapshot() == {"volume": 70, "muted": True}


def test_a_snapshot_is_a_private_copy():
    store = StatusStore({"volume": 50})

    snapshot = store.snapshot()
    snapshot["volume"] = 999

    assert store.snapshot()["volume"] == 50


def test_setting_one_key_is_a_patch_of_one():
    store = StatusStore()

    store["volume"] = 30

    assert store.snapshot() == {"volume": 30}
    assert store["volume"] == 30


def test_missing_keys_read_as_a_default():
    store = StatusStore({"volume": 30})

    assert store.get("volume") == 30
    assert store.get("nothing") is None
    assert store.get("nothing", "fallback") == "fallback"


def test_a_group_of_keys_is_never_observed_half_applied():
    """The invariant `patch` exists for.

    Fails if `patch` is implemented as a loop of single-key assignments,
    which is the shape the daemon's re-anchor bookkeeping used to have.
    """
    keys = [f"k{i}" for i in range(8)]
    store = StatusStore(dict.fromkeys(keys, 0))
    stop = threading.Event()
    torn: list[dict] = []

    def _writer():
        flip = 0
        while not stop.is_set():
            flip ^= 1
            store.patch(dict.fromkeys(keys, flip))

    def _reader():
        while not stop.is_set():
            snapshot = store.snapshot()
            if len({snapshot[key] for key in keys}) > 1:
                torn.append(snapshot)

    threads = [threading.Thread(target=_writer), threading.Thread(target=_reader)]
    for thread in threads:
        thread.start()
    threading.Event().wait(0.5)
    stop.set()
    for thread in threads:
        thread.join(timeout=5)

    assert torn == [], f"a snapshot saw half a group: {torn[0]}"


def test_an_empty_patch_changes_nothing():
    store = StatusStore({"volume": 10})

    store.patch({})

    assert store.snapshot() == {"volume": 10}


def test_the_dict_interface_still_works_for_existing_callers():
    """The store stands in for the dict the daemon passes around."""
    store = StatusStore({"volume": 10})

    assert "volume" in store
    assert dict(store) == {"volume": 10}
    assert sorted(store.keys()) == ["volume"]
