"""Regression coverage for manual Bluetooth device registration."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS = (
    REPO_ROOT
    / "src"
    / "sendspin_bridge"
    / "web"
    / "static"
    / "app.js"
)


def test_add_device_action_does_not_forward_event_objects():
    """The manual add action must create an empty device row."""
    source = APP_JS.read_text(encoding="utf-8")

    match = re.search(
        r"^\s*'add-bt-device-row':\s*(.+),\s*$",
        source,
        re.MULTILINE,
    )

    assert match is not None

    action = match.group(1)

    script = """
const assert = require('node:assert/strict');

let received = null;

function addBtDeviceRow(...args) {
    received = args;
}

const action = ACTION_EXPRESSION;

const element = {
    toString: () => '[object HTMLButtonElement]'
};

const event = {
    toString: () => '[object PointerEvent]'
};

action(element, event, null);

assert.deepEqual(
    received,
    [],
    'Manual device creation must not forward delegated event arguments'
);
""".replace("ACTION_EXPRESSION", action)

    node = shutil.which("node")
    assert node is not None, (
        "node is required for frontend runtime regression tests"
    )

    completed = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, (
        completed.stdout + completed.stderr
    )
