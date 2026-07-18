from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"
STYLE_CSS_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "style.css"
INDEX_HTML_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "templates" / "index.html"

_FRONTEND_LATENCY_SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.env.APP_JS_PATH, 'utf8');

function extractFunction(name) {
  const marker = `function ${name}(`;
  const asyncMarker = `async ${marker}`;
  const asyncStart = source.indexOf(asyncMarker);
  const start = asyncStart === -1 ? source.indexOf(marker) : asyncStart;
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
} else if (mode === 'config-latency-markup') {
  vm.runInThisContext(extractFunction('_uiIconSvg'));
  vm.runInThisContext(extractFunction('_renderConfigLatencyControlsHtml'));
  process.stdout.write(JSON.stringify({
    html: _renderConfigLatencyControlsHtml(Number(process.env.DELAY_MS)),
  }));
} else if (mode === 'latency-icons') {
  vm.runInThisContext(extractFunction('_uiIconSvg'));
  process.stdout.write(JSON.stringify({
    metronome: _uiIconSvg('metronome', 'latency-action-icon'),
    microphone: _uiIconSvg('microphone', 'latency-action-icon'),
  }));
} else if (mode === 'metronome-toggle') {
  vm.runInThisContext(extractFunction('playDeviceCalibrationTone'));
  global.API_BASE = '';
  global.currentViewMode = 'grid';
  global.lastDevices = [{player_id: 'speaker-1', player_name: 'Speaker', calibration_metronome_active: false}];
  global._calibrationTonePending = {};
  const requests = [];
  let refreshes = 0;
  const toasts = [];
  global.fetch = async (url, options) => {
    const payload = JSON.parse(options.body);
    requests.push({url, payload});
    return {ok: true, json: async () => ({success: true, active: payload.action === 'start'})};
  };
  global.refreshBtDeviceRowsRuntime = () => { refreshes += 1; };
  global.showToast = (message, level) => toasts.push({message, level});
  (async () => {
    await playDeviceCalibrationTone(0);
    await playDeviceCalibrationTone(0);
    process.stdout.write(JSON.stringify({requests, refreshes, toasts, device: lastDevices[0]}));
  })();
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
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return cast("dict[str, Any]", json.loads(completed.stdout))


def test_latency_ui_does_not_expose_codec_recommendation() -> None:
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
    assert "suggestedDelay" not in state
    assert "applyVisible" not in state
    assert "applyText" not in state


def test_latency_ui_renders_bluez_report_without_apply_action() -> None:
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
    assert "suggestedDelay" not in state
    assert "applyVisible" not in state
    assert "applyText" not in state


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


def test_configuration_delay_controls_use_compact_stepper() -> None:
    html = _run_node("config-latency-markup", DELAY_MS="240")["html"]

    assert 'class="bt-latency-controls"' in html
    assert 'class="bt-delay"' in html
    assert 'type="hidden"' in html
    assert 'data-action="config-latency-nudge"' in html
    assert 'data-arg="-1"' in html
    assert 'data-arg="1"' in html
    assert 'class="bt-latency-value"' in html
    assert ">240 ms<" in html
    assert 'data-action="toggle-config-latency-step"' in html
    assert ">±10<" in html
    assert 'class="action-btn latency-test-clicks"' in html
    assert 'class="action-btn latency-mic-compare"' in html
    assert html.count('class="latency-action-icon"') == 2
    assert html.index('data-arg="-1"') < html.index('class="bt-latency-value"')
    assert html.index('class="bt-latency-value"') < html.index('data-arg="1"')
    assert html.index("toggle-config-latency-step") < html.index("latency-test-clicks")


def test_latency_action_icons_are_inline_accessibility_safe_svgs() -> None:
    icons = _run_node("latency-icons")

    assert icons["metronome"].startswith('<svg class="latency-action-icon"')
    assert icons["microphone"].startswith('<svg class="latency-action-icon"')
    assert 'aria-hidden="true"' in icons["metronome"]
    assert 'aria-hidden="true"' in icons["microphone"]
    assert icons["metronome"] != icons["microphone"]


def test_latency_controls_live_only_in_configuration_device_actions() -> None:
    source = APP_JS_PATH.read_text(encoding="utf-8")
    template = INDEX_HTML_PATH.read_text(encoding="utf-8")

    assert "_renderLatencyTuneHtml(i, 'd')" not in source
    assert "_renderLatencyTuneHtml(i, 'l')" not in source
    assert "_renderConfigLatencyControlsHtml(delayVal)" in source
    actions_pos = source.index("_renderConfigLatencyControlsHtml(delayVal)")
    bluetooth_actions_pos = source.index("Bluetooth actions", actions_pos)
    assert actions_pos < bluetooth_actions_pos
    assert "<span>Delay</span>" not in template
    assert "<span>Live</span><span>Actions</span>" in template


def test_configuration_latency_controls_stay_on_one_row() -> None:
    css = STYLE_CSS_PATH.read_text(encoding="utf-8")

    assert ".bt-latency-controls" in css
    assert ".bt-latency-stepper" in css
    assert "flex-wrap: nowrap" in css


def test_test_clicks_toggles_continuous_metronome() -> None:
    result = _run_node("metronome-toggle")

    assert result["requests"] == [
        {
            "url": "/api/calibration/metronome",
            "payload": {"player_id": "speaker-1", "action": "start"},
        },
        {
            "url": "/api/calibration/metronome",
            "payload": {"player_id": "speaker-1", "action": "stop"},
        },
    ]
    assert result["refreshes"] == 2
    assert result["device"]["calibration_metronome_active"] is False


def test_microphone_calibration_is_not_experimental_ui() -> None:
    template = INDEX_HTML_PATH.read_text(encoding="utf-8")
    source = APP_JS_PATH.read_text(encoding="utf-8")

    assert "latency-calibration-beta" not in template
    assert "ENABLE_LATENCY_CALIBRATION_BETA" not in template
    assert "latency-calibration-beta" not in source
    assert "ENABLE_LATENCY_CALIBRATION_BETA" not in source
