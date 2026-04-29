from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"

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
  extractFunction('getDeviceSinkName'),
  extractFunction('_buildFinalizingStartupSummary'),
  extractFunction('_isZeroClientStatusError'),
  extractFunction('_deriveZeroDeviceRuntimeState'),
].join('\n\n');

vm.runInThisContext(bootstrap);
const status = JSON.parse(process.env.STATUS_JSON);
const devices = JSON.parse(process.env.DEVICES_JSON);
const result = _deriveZeroDeviceRuntimeState(status, devices);
process.stdout.write(JSON.stringify(result));
"""

_DERIVE_UPDATE_RUNTIME_STATE_SCRIPT = r"""
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
  extractFunction('getDeviceSinkName'),
  extractFunction('_buildFinalizingStartupSummary'),
  extractFunction('_normalizeBridgeVersion'),
  extractFunction('_bridgeVersionReleaseLine'),
  extractFunction('_updateMonitorElapsedSeconds'),
  extractFunction('_deriveUpdateRuntimeState'),
].join('\n\n');

vm.runInThisContext(bootstrap);
global._updateMonitor = JSON.parse(process.env.UPDATE_MONITOR_JSON);
let refreshedVersion = null;
global._refreshPageAfterUpdate = function(version) {
  refreshedVersion = version;
};
const status = JSON.parse(process.env.STATUS_JSON);
const options = JSON.parse(process.env.OPTIONS_JSON);
const result = _deriveUpdateRuntimeState(status, options);
process.stdout.write(JSON.stringify({result, monitor: global._updateMonitor, refreshedVersion}));
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


def _derive_update_runtime_state(
    update_monitor: dict[str, object],
    status: dict[str, object],
    options: dict[str, object] | None = None,
) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is required for frontend runtime regression tests")
    env = os.environ.copy()
    env.update(
        {
            "APP_JS_PATH": str(APP_JS_PATH),
            "UPDATE_MONITOR_JSON": json.dumps(update_monitor),
            "STATUS_JSON": json.dumps(status),
            "OPTIONS_JSON": json.dumps(options or {}),
        }
    )
    completed = subprocess.run(
        [node, "-e", _DERIVE_UPDATE_RUNTIME_STATE_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast("dict[str, Any]", json.loads(completed.stdout))


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


def test_finalizing_startup_uses_device_restore_summary() -> None:
    result = _derive_zero_device_runtime_state(
        {
            "startup_progress": {
                "status": "ready",
                "message": "Startup complete",
            },
            "operator_guidance": {
                "header_status": {
                    "tone": "info",
                    "label": "Startup 90%",
                    "summary": "Finalizing Startup",
                }
            },
        },
        [
            {
                "player_name": "Living Room",
                "enabled": True,
                "bt_management_enabled": True,
                "bluetooth_connected": True,
                "has_sink": True,
                "server_connected": True,
                "reconnecting": False,
            },
            {
                "player_name": "Kitchen",
                "enabled": True,
                "bt_management_enabled": True,
                "bluetooth_connected": True,
                "has_sink": False,
                "server_connected": False,
                "reconnecting": False,
            },
            {
                "player_name": "Patio",
                "enabled": True,
                "bt_management_enabled": True,
                "bluetooth_connected": False,
                "has_sink": False,
                "server_connected": False,
                "reconnecting": True,
            },
        ],
    )

    assert result == {
        "kind": "starting",
        "tone": "info",
        "label": "Startup 90%",
        "title": "Startup 90%",
        "summary": "1/3 speakers ready · 1 reconnecting · 1 waiting for sink",
        "action": {"key": "refresh_diagnostics", "label": "Retry now"},
    }


def test_update_monitor_treats_rc_target_as_complete_when_backend_reports_release_line() -> None:
    payload = _derive_update_runtime_state(
        {
            "startedAt": 0,
            "targetVersion": "2.42.4-rc.1",
            "targetReleaseLine": "2.42.4",
            "initialVersion": "2.42.3",
            "initialReleaseLine": "2.42.3",
            "channel": "rc",
            "alreadyRunning": False,
            "sawBackendUnavailable": True,
            "sawRestartTransition": True,
        },
        {
            "version": "2.42.4",
            "startup_progress": {"status": "ready", "message": "Startup complete."},
            "operator_guidance": {"header_status": {"label": "Healthy", "summary": "Bridge ready."}},
        },
    )

    result = cast("dict[str, Any]", payload["result"])
    monitor = cast("dict[str, Any]", payload["monitor"])
    assert payload["refreshedVersion"] == "2.42.4"
    assert result == {
        "kind": "updating",
        "tone": "info",
        "label": "Update complete",
        "title": "Update complete",
        "summary": "Refreshing the page to load the updated UI…",
        "action": {"key": "refresh_diagnostics", "label": "Retry now"},
        "elapsedSeconds": result["elapsedSeconds"],
    }
    assert monitor["refreshing"] is True


def test_update_monitor_does_not_false_complete_when_initial_release_line_already_matches() -> None:
    payload = _derive_update_runtime_state(
        {
            "startedAt": 0,
            "targetVersion": "2.42.4-rc.1",
            "targetReleaseLine": "2.42.4",
            "initialVersion": "2.42.4",
            "initialReleaseLine": "2.42.4",
            "channel": "rc",
            "alreadyRunning": False,
            "sawBackendUnavailable": False,
            "sawRestartTransition": False,
        },
        {
            "version": "2.42.4",
            "startup_progress": {"status": "ready", "message": "Startup complete."},
            "operator_guidance": {"header_status": {"label": "Healthy", "summary": "Bridge ready."}},
        },
    )

    result = cast("dict[str, Any]", payload["result"])
    assert payload["refreshedVersion"] is None
    assert result["label"] == "Updating…"
    assert result["title"] == "Update in progress"
    assert result["summary"] == "Preparing update to v2.42.4-rc.1. Waiting for the bridge service to restart."


def test_update_monitor_finalizing_startup_uses_device_restore_summary() -> None:
    payload = _derive_update_runtime_state(
        {
            "startedAt": 0,
            "targetVersion": "2.42.4",
            "targetReleaseLine": "2.42.4",
            "initialVersion": "2.42.3",
            "initialReleaseLine": "2.42.3",
            "channel": "stable",
            "alreadyRunning": False,
            "sawBackendUnavailable": True,
            "sawRestartTransition": True,
        },
        {
            "version": "2.42.3",
            "startup_progress": {"status": "ready", "message": "Startup complete"},
            "operator_guidance": {"header_status": {"label": "Startup 90%", "summary": "Finalizing Startup"}},
            "devices": [
                {
                    "enabled": True,
                    "bt_management_enabled": True,
                    "bluetooth_connected": True,
                    "has_sink": True,
                    "server_connected": True,
                    "reconnecting": False,
                },
                {
                    "enabled": True,
                    "bt_management_enabled": True,
                    "bluetooth_connected": False,
                    "has_sink": False,
                    "server_connected": False,
                    "reconnecting": True,
                },
                {
                    "enabled": True,
                    "bt_management_enabled": True,
                    "bluetooth_connected": True,
                    "has_sink": True,
                    "server_connected": False,
                    "reconnecting": False,
                },
            ],
        },
    )

    result = cast("dict[str, Any]", payload["result"])
    assert result["label"] == "Startup 90%"
    assert result["summary"] == "1/3 speakers ready · 1 reconnecting · 1 waiting for Sendspin"
