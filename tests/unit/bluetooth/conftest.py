"""A bridge loop for the Bluetooth tests.

The device module's blocking facade hands its work to the bridge loop, because
production always has one: the manager is driven from Waitress workers and
D-Bus callbacks, not from the loop. Without a loop it answers "don't know",
which is right in production and useless in a test that wants an answer.

Scoped to this directory on purpose — elsewhere, "there is no bridge loop" is
a state some tests deliberately arrange.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def _bridge_loop_thread():
    """One background event loop for the whole session.

    The blocking facade on the Bluetooth device module hands its work to the
    bridge loop, as production always has one. Without a loop it answers
    "don't know", which is right in production and useless in a test that
    wants an answer.
    """
    import asyncio
    import threading

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="test-bridge-loop")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


@pytest.fixture(autouse=True)
def _bridge_loop(_bridge_loop_thread):
    """Register the session loop as the bridge loop, and put it back after."""
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as runtime

    previous = runtime.get_main_loop()
    runtime.set_main_loop(_bridge_loop_thread)
    try:
        yield _bridge_loop_thread
    finally:
        runtime.set_main_loop(previous)
