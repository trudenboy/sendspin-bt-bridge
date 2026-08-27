"""A bridge loop for the Bluetooth tests.

Work that crosses to the bridge loop — the device module's blocking reads,
the controller's verbs over D-Bus — hands itself to it because production
always has one: the manager is driven from Waitress workers and D-Bus
callbacks, not from the loop. Without a loop it answers "don't know", which
is right in production and useless in a test that wants an answer.

Autouse here on purpose — elsewhere, "there is no bridge loop" is a state
some tests deliberately arrange, and those ask for ``bridge_loop`` by name.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _bridge_loop(bridge_loop):
    yield bridge_loop
