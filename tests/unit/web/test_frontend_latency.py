from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"

_FRONTEND_LATENCY_SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.env.APP_JS_PATH, 'utf8');

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`Function not found: ${name}`);
  const openBrace = source.indexOf('{', start);
  let depth = 0;
  let end = openBrace;
  for (; end < source.length; end += 1) {
    const ch = source[end];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) {
        end += 1;
        break;
      }
    }
  }
  return source.slice(start, end);
}

const mode = process.env.MODE;
if (mode === 'latency') {
  vm.runInThisContext(extractFunction('_getLatencyUiState'));
  const dev = JSON.parse(process.env.DEVICE_JSON);
  const peers = JSON.parse(process.env.PEER_DELAYS_JSON);
  process.stdout.write(JSON.stringify(_getLatencyUiState(dev, peers)));
} else if (mode === 'stream-recovery') {
  vm.runInThisContext(extractFunction('_recoverBackendUiAfterStatusStreamOpen'));
  global._statusHasEverSucceeded = process.env.STATUS_SUCCEEDED === 'true';
  global.lastDevices = JSON.parse(process.env.DEVICES_JSON);
  const calls = [];
  global._applyBackendServiceState = state => calls.push(['state', state]);
  global._syncRestartBanner = (status, state) => calls.push(['banner', status, state]);
  const recovered = _recoverBackendUiAfterStatusStreamOpen();
  process.stdout.write(JSON.stringify({recovered, calls}));
} else if (mode === 'calibration-peers') {
  vm.runInThisContext(extractFunction('_getMicrophoneCalibrationPeers'));
  const devices = JSON.parse(process.env.DEVICES_JSON);
  const targetIndex = Number(process.env.TARGET_INDEX);
  process.stdout.write(JSON.stringify(_getMicrophoneCalibrationPeers(devices, targetIndex)));
} else {
  throw new Error(`Unsupported mode: ${mode}`);
}
"""


def _run_node(mode: str, **values: object) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is required for frontend latency regression tests")
    env = os.environ.copy()
    env.update({"APP_JS_PATH": str(APP_JS_PATH), "MODE": mode})
    for key, value in values.items():
        env[key] = json.dumps(value) if not isinstance(value, str) else value
    completed = subprocess.run(
        [node, "-e", _FRONTEND_LATENCY_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast("dict[str, Any]", json.loads(completed.stdout))


def test_latency_ui_without_bluez_delay_still_renders_numeric_suggestion() -> None:
    state = _run_node(
        "latency",
        DEVICE_JSON={
            "static_delay_ms": 300,
            "bt_reported_delay_ms": None,
            "suggested_static_delay_ms": 125,
            "latency_suggestion_explanation": "SBC fallback",
            "latency_suggestion_confidence": "low",
        },
        PEER_DELAYS_JSON=[],
    )

    assert state["label"] == "Delay 300 ms"
    assert state["chipVisible"] is False
    assert state["chipText"] == ""
    assert state["applyVisible"] is True
    assert state["applyText"] == "Apply 125 ms"


def test_latency_ui_renders_bluez_report_and_hides_applied_suggestion() -> None:
    state = _run_node(
        "latency",
        DEVICE_JSON={
            "static_delay_ms": 170,
            "bt_reported_delay_ms": 170,
            "suggested_static_delay_ms": 170,
            "latency_suggestion_explanation": "AVDTP report",
            "latency_suggestion_confidence": "medium",
        },
        PEER_DELAYS_JSON=[],
    )

    assert state["chipVisible"] is True
    assert state["chipText"] == "BT delay 170.0 ms"
    assert state["applyVisible"] is False
    assert state["applyText"] == "Apply 170 ms"


def test_status_stream_open_clears_stale_backend_warning() -> None:
    result = _run_node(
        "stream-recovery",
        STATUS_SUCCEEDED="true",
        DEVICES_JSON=[{"player_name": "Speaker"}],
    )

    assert result == {
        "recovered": True,
        "calls": [["state", None], ["banner", None, None]],
    }


def test_status_stream_open_keeps_connecting_state_before_first_snapshot() -> None:
    result = _run_node(
        "stream-recovery",
        STATUS_SUCCEEDED="false",
        DEVICES_JSON=[],
    )

    assert result == {"recovered": False, "calls": []}


def test_microphone_calibration_accepts_enabled_peer_from_different_ma_group() -> None:
    peers = _run_node(
        "calibration-peers",
        DEVICES_JSON=[
            {
                "player_id": "eneby",
                "enabled": True,
                "connected": True,
                "bluetooth_connected": True,
                "group_id": "living-room",
            },
            {
                "player_id": "wh",
                "enabled": True,
                "connected": True,
                "bluetooth_connected": True,
                "group_id": "headphones",
            },
        ],
        TARGET_INDEX="0",
    )

    assert [peer["player_id"] for peer in peers] == ["wh"]


def test_microphone_calibration_rejects_disconnected_peer() -> None:
    peers = _run_node(
        "calibration-peers",
        DEVICES_JSON=[
            {"player_id": "eneby", "enabled": True, "bluetooth_connected": True},
            {"player_id": "wh", "enabled": True, "bluetooth_connected": False},
        ],
        TARGET_INDEX="0",
    )

    assert peers == []
