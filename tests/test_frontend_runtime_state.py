from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS_PATH = REPO_ROOT / "static" / "app.js"

_DERIVE_ZERO_DEVICE_RUNTIME_STATE_SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.env.APP_JS_PATH, 'utf8');

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) {
    throw new Error(`Function not found: ${name}`);
  }
  const openBrace = source.indexOf('{', start);
  if (openBrace === -1) {
    throw new Error(`Opening brace not found for: ${name}`);
  }
  let depth = 0;
  let end = openBrace;
  for (; end < source.length; end += 1) {
    const ch = source[end];
    if (ch === '{') {
      depth += 1;
    } else if (ch === '}') {
      depth -= 1;
      if (depth === 0) {
        end += 1;
        break;
      }
    }
  }
  return source.slice(start, end);
}

const bootstrap = [
  extractFunction('_backendServiceToneClass'),
  extractFunction('_isZeroClientStatusError'),
  extractFunction('_deriveZeroDeviceRuntimeState'),
].join('\n\n');

vm.runInThisContext(bootstrap);
const status = JSON.parse(process.env.STATUS_JSON);
const devices = JSON.parse(process.env.DEVICES_JSON);
const result = _deriveZeroDeviceRuntimeState(status, devices);
process.stdout.write(JSON.stringify(result));
"""


def _derive_zero_device_runtime_state(status: dict[str, object], devices: list[dict[str, object]]) -> object:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is required for frontend runtime regression tests")
    env = os.environ.copy()
    env.update(
        {
            "APP_JS_PATH": str(APP_JS_PATH),
            "STATUS_JSON": json.dumps(status),
            "DEVICES_JSON": json.dumps(devices),
        }
    )
    completed = subprocess.run(
        [node, "-e", _DERIVE_ZERO_DEVICE_RUNTIME_STATE_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_ready_startup_does_not_restore_bridge_state_for_empty_runtime() -> None:
    result = _derive_zero_device_runtime_state(
        {
            "startup_progress": {
                "status": "ready",
                "message": "Startup complete.",
            },
            "operator_guidance": {
                "header_status": {
                    "tone": "neutral",
                    "label": "Waiting for setup",
                    "summary": "Configure your first speaker to start playback.",
                }
            },
        },
        [],
    )

    assert result is None


def test_running_startup_keeps_zero_device_runtime_locked() -> None:
    result = _derive_zero_device_runtime_state(
        {
            "startup_progress": {
                "status": "running",
                "message": "Starting web interface",
            },
            "operator_guidance": {
                "header_status": {
                    "tone": "info",
                    "label": "Startup 60%",
                    "summary": "Starting web interface",
                }
            },
        },
        [],
    )

    assert result == {
        "kind": "starting",
        "tone": "info",
        "label": "Startup 60%",
        "title": "Startup 60%",
        "summary": "Starting web interface",
        "action": {"key": "refresh_diagnostics", "label": "Retry now"},
    }
