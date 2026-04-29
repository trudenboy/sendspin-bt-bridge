from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_JS_PATH = REPO_ROOT / "src" / "sendspin_bridge" / "web" / "static" / "app.js"

_COMMON_JS = r"""
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync(process.env.APP_JS_PATH, 'utf8');

function extractFunction(name) {
  const markers = [`async function ${name}(`, `function ${name}(`];
  let start = -1;
  for (const marker of markers) {
    start = source.indexOf(marker);
    if (start !== -1) {
      break;
    }
  }
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
"""

_OPEN_CLOSE_SCRIPT = (
    _COMMON_JS
    + r"""
const bootstrap = [
  extractFunction('_getBtScanOverlay'),
  extractFunction('_getBtScanDialog'),
  extractFunction('_isBtScanModalVisible'),
  extractFunction('_getFocusableElementsWithin'),
  extractFunction('_getBtScanModalTrapTarget'),
  extractFunction('_focusBtScanModalTarget'),
  extractFunction('_restoreBtScanModalFocus'),
  extractFunction('_getBtScanLauncherState'),
  extractFunction('_applyBtScanCooldownUi'),
  extractFunction('_showBtScanBackgroundNotice'),
  extractFunction('closeBtScanModal'),
  extractFunction('openBtScanModal'),
].join('\n\n');

vm.runInThisContext(bootstrap);

function makeEl(id) {
  return {
    id,
    hidden: false,
    disabled: false,
    innerHTML: '',
    focusCount: 0,
    focus() {
      this.focusCount += 1;
      document.activeElement = this;
    },
    getAttribute() {
      return null;
    },
  };
}

let startCalls = 0;
const listeners = {};
const trigger = makeEl('scan-btn');
const closeBtn = makeEl('close-btn');
const rescanBtn = makeEl('scan-rescan-btn');
const overlay = {
  hidden: true,
  onclick: null,
  querySelector(selector) {
    return selector === '.bt-scan-modal' ? dialog : null;
  },
};
const dialog = {
  querySelector(selector) {
    return selector === '.bt-scan-modal-close' ? closeBtn : null;
  },
  querySelectorAll() {
    return [closeBtn, rescanBtn];
  },
};
const body = {
  contains(node) {
    return [trigger, closeBtn, rescanBtn].includes(node);
  },
};

global.document = {
  activeElement: trigger,
  body,
  getElementById(id) {
    return {
      'bt-scan-modal-overlay': overlay,
      'scan-btn': trigger,
    }[id] || null;
  },
  addEventListener(name, fn) {
    listeners[name] = fn;
  },
  removeEventListener(name, fn) {
    if (listeners[name] === fn) {
      delete listeners[name];
    }
  },
};

global._scanCooldownRemaining = 0;
global._btScanModalKeydownHandler = null;
global._btScanModalState = {
  adapter: '',
  audioOnly: true,
  activeJobId: '',
  isRunning: false,
  isVisible: false,
  expectedDuration: 15,
  startedAtMs: 0,
  progressTimer: null,
  lastDevices: [],
  lastStats: null,
  lastError: '',
  lastFocusedElement: null,
  requestToken: 0,
  backgroundNoticeShown: false,
};
global._buttonLabelWithIconHtml = (icon, label) => `${icon}:${label}`;
global._syncBtScanControls = () => {};
global._openConfigPanel = () => {};
global._renderBtScanAdapterOptions = () => {};
global._renderBtScanProgress = () => {};
global._renderBtScanOutcome = () => {};
global._hasDetectedAdapter = () => true;
global._goToAdapters = () => {
  throw new Error('unexpected adapter redirect');
};
global._applyExperimentalVisibility = () => {};
global.showToast = () => {};
global.startBtScan = () => {
  startCalls += 1;
  return false;
};

openBtScanModal({autoStart: false});
const afterOpen = {
  overlayHidden: overlay.hidden,
  focusMovedToClose: closeBtn.focusCount,
  openerTracked: _btScanModalState.lastFocusedElement === trigger,
  keydownAttached: Boolean(listeners.keydown),
  startCalls,
};
closeBtScanModal();
process.stdout.write(JSON.stringify({
  afterOpen,
  afterClose: {
    overlayHidden: overlay.hidden,
    openerFocusCount: trigger.focusCount,
    keydownAttached: Boolean(listeners.keydown),
  },
}));
"""
)

_LAUNCHER_AND_TRAP_SCRIPT = (
    _COMMON_JS
    + r"""
const bootstrap = [
  extractFunction('_getBtScanLauncherState'),
  extractFunction('_getBtScanModalTrapTarget'),
].join('\n\n');

vm.runInThisContext(bootstrap);

const first = {id: 'first'};
const middle = {id: 'middle'};
const last = {id: 'last'};

process.stdout.write(JSON.stringify({
  launcherRunningHidden: _getBtScanLauncherState(true, false, 0),
  launcherCooldown: _getBtScanLauncherState(false, false, 8),
  trapForwardWraps: _getBtScanModalTrapTarget([first, middle, last], last, false) === first,
  trapBackwardWraps: _getBtScanModalTrapTarget([first, middle, last], first, true) === last,
  trapMiddleSkips: _getBtScanModalTrapTarget([first, middle, last], middle, false),
}));
"""
)

_POLLING_SCRIPT = (
    _COMMON_JS
    + r"""
const bootstrap = [
  extractFunction('_sleep'),
  extractFunction('_fetchJsonOrThrow'),
  extractFunction('_pollBtAsyncJobResult'),
].join('\n\n');

vm.runInThisContext(bootstrap);

global.API_BASE = '';
global._sleep = () => Promise.resolve();

async function run() {
  let fetchCalls = 0;
  global.fetch = async () => {
    fetchCalls += 1;
    if (fetchCalls === 1) {
      return {ok: true, json: async () => ({status: 'running'})};
    }
    return {ok: true, json: async () => ({status: 'done', success: true})};
  };
  const completed = await _pollBtAsyncJobResult('job-1', '/api/bt/scan/result/', {
    delayMs: 0,
    maxAttempts: 5,
  });

  let staleFetchCalls = 0;
  global.fetch = async () => {
    staleFetchCalls += 1;
    return {ok: true, json: async () => ({status: 'done'})};
  };
  const stale = await _pollBtAsyncJobResult('job-2', '/api/bt/scan/result/', {
    delayMs: 0,
    maxAttempts: 5,
    isStale: () => true,
  });

  process.stdout.write(JSON.stringify({
    completed,
    fetchCalls,
    stale,
    staleFetchCalls,
  }));
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
)

_OUTCOME_SCRIPT = (
    _COMMON_JS
    + r"""
const bootstrap = [
  extractFunction('_renderBtScanOutcome'),
].join('\n\n');

vm.runInThisContext(bootstrap);

const status = {innerHTML: ''};
const box = {hidden: false};
let badgeCalls = [];
let renderedDevices = null;

global._btScanModalState = {
  isRunning: false,
  lastError: 'Scan failed hard',
  startedAtMs: 123,
  lastDevices: [],
  lastStats: null,
  audioOnly: true,
};
global._isBtScanModalVisible = () => true;
global._clearBtScanStatusPanels = () => {
  status.innerHTML = '';
  box.hidden = true;
};
global._renderBtScanEmptyStateHtml = () => 'EMPTY';
global._renderBtScanResults = (devices) => {
  renderedDevices = devices;
};
global._renderScanStatusBadgeHtml = (label, tone, hint) => {
  badgeCalls.push({label, tone, hint: hint || null});
  return `BADGE:${label}:${tone}:${hint || ''}`;
};
global.document = {
  getElementById(id) {
    return {
      'scan-results-box': box,
      'scan-status': status,
    }[id] || null;
  },
};

_renderBtScanOutcome();
const errorState = {html: status.innerHTML, boxHidden: box.hidden, badgeCalls: badgeCalls.slice()};

badgeCalls = [];
status.innerHTML = '';
box.hidden = false;
_btScanModalState.lastError = '';
_btScanModalState.lastDevices = [{mac: 'AA:BB', name: 'Speaker', supports_import: true, audio_capable: true}];
_btScanModalState.lastStats = {returned_candidates: 1};
_renderBtScanOutcome();

process.stdout.write(JSON.stringify({
  errorState,
  successState: {
    html: status.innerHTML,
    boxHidden: box.hidden,
    badgeCalls,
    renderedDevices,
  },
}));
"""
)


def _run_frontend_script(script: str) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node is required for frontend regression tests")
    env = os.environ.copy()
    env["APP_JS_PATH"] = str(APP_JS_PATH)
    completed = subprocess.run(
        [node, "-e", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return cast("dict[str, Any]", json.loads(completed.stdout))


def test_scan_modal_open_close_restores_focus_and_does_not_autostart() -> None:
    result = _run_frontend_script(_OPEN_CLOSE_SCRIPT)

    assert result["afterOpen"] == {
        "overlayHidden": False,
        "focusMovedToClose": 1,
        "openerTracked": True,
        "keydownAttached": True,
        "startCalls": 0,
    }
    assert result["afterClose"] == {
        "overlayHidden": True,
        "openerFocusCount": 1,
        "keydownAttached": False,
    }


def test_scan_modal_launcher_and_focus_trap_helpers_cover_running_and_edges() -> None:
    result = _run_frontend_script(_LAUNCHER_AND_TRAP_SCRIPT)

    assert result["launcherRunningHidden"] == {
        "disabled": False,
        "icon": "search",
        "label": "Open active scan",
    }
    assert result["launcherCooldown"] == {
        "disabled": True,
        "icon": "search",
        "label": "Scan nearby (8s)",
    }
    assert result["trapForwardWraps"] is True
    assert result["trapBackwardWraps"] is True
    assert result["trapMiddleSkips"] is None


def test_scan_modal_shared_polling_helper_completes_and_respects_stale_guard() -> None:
    result = _run_frontend_script(_POLLING_SCRIPT)

    assert result == {
        "completed": {"status": "done", "success": True},
        "fetchCalls": 2,
        "stale": None,
        "staleFetchCalls": 0,
    }


def test_scan_modal_outcome_uses_scan_badge_helper_for_error_and_success_states() -> None:
    result = _run_frontend_script(_OUTCOME_SCRIPT)

    assert result["errorState"] == {
        "html": "BADGE:Scan failed:error:Scan failed hard",
        "boxHidden": True,
        "badgeCalls": [{"label": "Scan failed", "tone": "error", "hint": "Scan failed hard"}],
    }
    assert result["successState"] == {
        "html": "BADGE:Found 1 device:success:",
        "boxHidden": False,
        "badgeCalls": [{"label": "Found 1 device", "tone": "success", "hint": None}],
        "renderedDevices": [{"mac": "AA:BB", "name": "Speaker", "supports_import": True, "audio_capable": True}],
    }
