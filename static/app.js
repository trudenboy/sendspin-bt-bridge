// ---- Ingress-aware API base path ----
// When served via HA Ingress the page URL is something like
//   https://haos/api/hassio_ingress/TOKEN/
// fetch(API_BASE + '/api/...') would hit HA Core instead of this addon.
// We compute the base path from the current URL so relative API
// calls work both under Ingress and at direct http://host:8080/.
var API_BASE = (function() {
    var p = window.location.pathname;
    return p.endsWith('/') ? p.slice(0, -1) : p.split('/').slice(0, -1).join('/');
})();

var THEME_MODE_STORAGE_KEY = 'sendspin-ui:theme-mode';
var GUIDANCE_ONBOARDING_STORAGE_KEY = 'sendspin-ui:show-onboarding-guidance';
var GUIDANCE_RECOVERY_STORAGE_KEY = 'sendspin-ui:show-recovery-guidance';
var EXPERIMENTAL_STORAGE_KEY = 'sendspin-ui:show-experimental';
var _themeManagedVars = [
    'primary-color',
    'dark-primary-color',
    'accent-color',
    'primary-text-color',
    'secondary-text-color',
    'disabled-text-color',
    'primary-background-color',
    'secondary-background-color',
    'card-background-color',
    'divider-color',
    'error-color',
    'success-color',
    'warning-color',
    'info-color',
    'ha-card-box-shadow',
    'code-background-color',
    'code-text-color',
    'app-header-background-color',
    'app-header-text-color',
];
var _systemThemeMedia = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
var userThemeMode = _loadSavedThemeMode() || 'auto';
var _lastDiagnosticsPayload = null;
var _recoveryTimelineViewState = {
    level: 'all',
    sourceType: 'all',
    source: 'all',
    limit: '12',
    advanced: false,
};
var _haAreaAssistEnabled = false;

function _normalizeThemeMode(mode) {
    return mode === 'light' || mode === 'dark' || mode === 'auto' ? mode : null;
}

function _loadSavedThemeMode() {
    try {
        return _normalizeThemeMode(window.localStorage.getItem(THEME_MODE_STORAGE_KEY));
    } catch (_) {
        return null;
    }
}

function _persistThemeMode(mode) {
    try {
        if (mode) {
            window.localStorage.setItem(THEME_MODE_STORAGE_KEY, mode);
        } else {
            window.localStorage.removeItem(THEME_MODE_STORAGE_KEY);
        }
    } catch (_) {
        // Ignore storage failures and fall back to in-memory state.
    }
}

function _clearManagedThemeVars() {
    var root = document.documentElement;
    _themeManagedVars.forEach(function(key) {
        root.style.removeProperty('--' + key);
    });
}

function _parseThemeColorToRgb(color) {
    if (!color || typeof color !== 'string') return null;
    var trimmed = color.trim();
    var hex = trimmed.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (hex) {
        var raw = hex[1];
        if (raw.length === 3) raw = raw.split('').map(function(c) { return c + c; }).join('');
        return {
            r: parseInt(raw.slice(0, 2), 16),
            g: parseInt(raw.slice(2, 4), 16),
            b: parseInt(raw.slice(4, 6), 16),
        };
    }
    var rgb = trimmed.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
    if (rgb) {
        return {r: parseInt(rgb[1], 10), g: parseInt(rgb[2], 10), b: parseInt(rgb[3], 10)};
    }
    return null;
}

function _isDarkThemeColor(color) {
    var rgb = _parseThemeColorToRgb(color);
    if (!rgb) return false;
    var luminance = (0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b) / 255;
    return luminance < 0.5;
}

function _getAutomaticThemeMode() {
    return _systemThemeMedia && _systemThemeMedia.matches ? 'dark' : 'light';
}

function _resolveThemeMode(mode) {
    var normalized = _normalizeThemeMode(mode) || 'auto';
    return normalized === 'auto' ? _getAutomaticThemeMode() : normalized;
}

function _nextThemeMode(mode) {
    if (mode === 'auto') return 'light';
    if (mode === 'light') return 'dark';
    return 'auto';
}

function _describeThemeMode(mode) {
    if (mode === 'dark') return 'Dark';
    if (mode === 'light') return 'Light';
    return 'Auto';
}

function _themeModeIcon(mode) {
    if (mode === 'dark') return 'moon';
    if (mode === 'light') return 'sun';
    return 'theme-auto';
}

function applyThemeMode(mode, resolvedModeOverride) {
    var root = document.documentElement;
    var selected = _normalizeThemeMode(mode) || 'auto';
    var resolved = resolvedModeOverride === 'dark' || resolvedModeOverride === 'light'
        ? resolvedModeOverride
        : _resolveThemeMode(selected);
    root.classList.toggle('theme-dark', resolved === 'dark');
    root.classList.toggle('theme-light', resolved === 'light');
    root.setAttribute('data-theme-mode', selected);
    root.setAttribute('data-theme-resolved-mode', resolved);
}

function _refreshThemeDependentUi() {
    document.querySelectorAll('input[type="range"]').forEach(function(el) {
        updateSliderFill(el);
    });
}

function _syncThemeToggleUi() {
    var btn = document.getElementById('theme-toggle-btn');
    var icon = document.getElementById('theme-toggle-icon');
    var currentMode = _normalizeThemeMode(userThemeMode) || 'auto';
    var nextMode = _nextThemeMode(currentMode);
    if (btn) {
        var actionLabel = 'Theme: ' + _describeThemeMode(currentMode) + '. Click to switch to ' + _describeThemeMode(nextMode) + '.';
        btn.title = actionLabel;
        btn.setAttribute('aria-label', actionLabel);
        btn.setAttribute('data-theme-cycle-mode', currentMode);
    }
    if (icon) _setUiIconSlot(icon, _themeModeIcon(currentMode));
}

function _applyStoredOrAutomaticTheme() {
    _clearManagedThemeVars();
    applyThemeMode(userThemeMode);
    _syncThemeToggleUi();
    _refreshThemeDependentUi();
}

function toggleThemeMode() {
    userThemeMode = _nextThemeMode(_normalizeThemeMode(userThemeMode) || 'auto');
    _persistThemeMode(userThemeMode);
    _clearManagedThemeVars();
    applyThemeMode(userThemeMode);
    _syncThemeToggleUi();
    _refreshThemeDependentUi();
}

_applyStoredOrAutomaticTheme();

function _handleSystemThemeChange(event) {
    if (userThemeMode !== 'auto') return;
    _clearManagedThemeVars();
    applyThemeMode('auto', event.matches ? 'dark' : 'light');
    _syncThemeToggleUi();
    _refreshThemeDependentUi();
}

if (_systemThemeMedia) {
    if (typeof _systemThemeMedia.addEventListener === 'function') {
        _systemThemeMedia.addEventListener('change', _handleSystemThemeChange);
    } else if (typeof _systemThemeMedia.addListener === 'function') {
        _systemThemeMedia.addListener(_handleSystemThemeChange);
    }
}

// HA Ingress theme injection listener
// HA sends setTheme postMessage when theme changes (Ingress mode)
window.addEventListener('message', function(e) {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type !== 'setTheme') return;
    if (e.origin !== window.location.origin && e.source !== window.parent) return;
    if (userThemeMode !== 'auto') return;
    var theme = e.data.theme || {};
    var root = document.documentElement;
    Object.keys(theme).forEach(function(key) {
        if (key) root.style.setProperty('--' + key, theme[key]);
    });
    var mode = e.data.mode || e.data.themeMode || '';
    if (mode === 'dark' || mode === 'light') {
        applyThemeMode('auto', mode);
        _syncThemeToggleUi();
        _refreshThemeDependentUi();
        return;
    }
    var bg = theme['primary-background-color'] || theme['card-background-color'];
    if (bg) {
        applyThemeMode('auto', _isDarkThemeColor(bg) ? 'dark' : 'light');
        _syncThemeToggleUi();
        _refreshThemeDependentUi();
    }
});

// ---- State ----
var autoRefreshLogs = false;
var autoRefreshInterval = null;
var allLogs = [];
var recentLogIssueState = { hasMeta: false, hasIssues: false, level: '', count: 0 };
var currentLogLevel = 'all';
var btAdapters = [];
var btManualAdapters = [];
var _haAreaCatalog = null;
var _haAdapterAreaMap = {};
var lastDevices = [];
var lastGroups = [];
var _lastDisabledDevices = [];
var lastMaUiUrl = '';
var lastMaWebUrl = '';
var _backendServiceState = null;
var _statusHasEverSucceeded = false;
var VIEW_MODE_STORAGE_KEY = 'sendspin-ui:view-mode';
var MOBILE_LIST_VIEW_MAX_WIDTH = 640;
var _viewModeStorageScope = 'default';
var _runtimeMode = 'production';
var _demoScreenshotDefaultsApplied = false;
var _viewModeMediaQuery = null;
var userPreferredViewMode = _loadSavedViewMode();
var currentViewMode = userPreferredViewMode || 'list';
var listSortState = {column: 'status', direction: 'desc'};
var expandedListRowKey = null;
var _muteDebounce = {};  // player_name → timestamp of last user mute action
var _btnLocks = {};      // btnId → expiry timestamp
var _deviceSettingsHighlightTimer = null;
var _adapterSettingsHighlightTimer = null;
var _restartMonitor = null;
var _updateMonitor = null;

function _normalizeExternalUrlBase(url) {
    return url ? String(url).replace(/\/+$/, '') : '';
}

function _normalizeBridgeVersion(version) {
    return String(version || '').trim().replace(/^v/i, '').toLowerCase();
}

function _bridgeVersionReleaseLine(version) {
    return _normalizeBridgeVersion(version).replace(/[-+].*$/, '');
}

function _currentDisplayedBridgeVersion() {
    var el = document.getElementById('version-display');
    return _normalizeBridgeVersion(el ? el.textContent : '');
}

function _getConfiguredMaUiUrl() {
    var body = document.body;
    if (!body || !body.dataset) return '';
    return _normalizeExternalUrlBase(body.dataset.maUiUrl || '');
}

lastMaUiUrl = _getConfiguredMaUiUrl();

// Lock a button during an async operation; SSE polls skip disabled override while locked.
function _lockBtn(btnId) {
    var btn = document.getElementById(btnId);
    if (!btn || _btnLocks[btnId]) return null;
    _btnLocks[btnId] = Date.now() + 8000;  // 8 s safety ceiling
    btn.disabled = true;
    btn.style.opacity = '0.45';
    return btn;
}
function _unlockBtn(btnId) {
    delete _btnLocks[btnId];
    var btn = document.getElementById(btnId);
    if (btn) { btn.disabled = false; btn.style.opacity = ''; }
}
function _isLocked(btnId) {
    var t = _btnLocks[btnId];
    if (!t) return false;
    if (Date.now() > t) { delete _btnLocks[btnId]; return false; }
    return true;
}

// Return first slash-separated segment with "+N" suffix if list has more items.
// E.g. "A/B/C" → "A +2". Single values pass through unchanged.
function _firstOfSlash(str) {
    if (!str) return str;
    var i = str.indexOf('/');
    if (i === -1) return str;
    var count = str.split('/').length - 1;
    return str.substring(0, i).trim() + ' +' + count;
}
var volTimers = {};
var volPending = {}; // deviceIndex -> true if user recently touched slider
var reanchorShownAt = {};   // deviceIndex -> timestamp(ms) when last re-anchor event was detected
var lastReanchorCount = {}; // deviceIndex -> reanchor_count at last render (to detect new events)
var lastReanchorAt = {};    // deviceIndex -> last_reanchor_at string seen (catches count resets on stream restart)
var _progSnapshots = {};    // deviceIndex -> {pos, dur, t} for Sendspin native progress interpolation
var _maProgSnapshots = {};  // deviceIndex -> {elapsed, duration, t, paused} for MA progress interpolation

// ---- Utility ----

function fmtMs(ms) {
    var s = Math.floor(ms / 1000);
    var m = Math.floor(s / 60);
    s = s % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
}

function fmtSec(sec) {
    sec = Math.max(0, Math.floor(sec));
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
}

function _getMaProgressTrackKey(ma) {
    return [
        ma.syncgroup_id || '',
        ma.queue_index != null ? String(ma.queue_index) : '',
        ma.track || '',
        ma.artist || '',
        ma.album || '',
        ma.duration != null ? String(ma.duration) : '',
    ].join('||');
}

function _getMaSnapshotElapsedNow(snapshot, now) {
    if (!snapshot) return 0;
    var duration = Math.max(0, Number(snapshot.duration) || 0);
    var elapsed = Math.max(0, Math.min(Number(snapshot.elapsed) || 0, duration || Number(snapshot.elapsed) || 0));
    if (snapshot.paused) return elapsed;
    var startedAt = Number(snapshot.t) || now;
    return Math.max(0, Math.min(elapsed + Math.max(0, now - startedAt) / 1000, duration || elapsed));
}

function _buildMergedMaProgressSnapshot(idx, ma, now) {
    var duration = Math.max(0, Number(ma.duration) || 0);
    var elapsed = Math.max(0, Math.min(Number(ma.elapsed) || 0, duration));
    var startedAt = ma.elapsed_updated_at != null ? Number(ma.elapsed_updated_at) * 1000 : now;
    if (!Number.isFinite(startedAt)) startedAt = now;
    if (startedAt > now) startedAt = now;

    var incoming = {
        elapsed: elapsed,
        duration: duration,
        t: startedAt,
        key: _getMaProgressTrackKey(ma),
    };
    var existing = _maProgSnapshots[idx];
    if (!existing || existing.key !== incoming.key || existing.duration !== incoming.duration) {
        return incoming;
    }

    var existingElapsedNow = _getMaSnapshotElapsedNow(existing, now);
    var incomingElapsedNow = _getMaSnapshotElapsedNow(incoming, now);
    if (incomingElapsedNow + 0.75 < existingElapsedNow) {
        return {
            elapsed: existingElapsedNow,
            duration: existing.duration,
            t: now,
            key: existing.key,
        };
    }
    return incoming;
}

function _getDevicePlaybackProgressState(dev, idx, nowMs) {
    var now = nowMs != null ? nowMs : Date.now();
    var ma = dev.ma_now_playing || {};
    var deviceMaActive = !!(ma.connected && deviceHasSink(dev));
    var maHasProg = deviceMaActive && ma.state === 'playing' && ma.duration > 0 && ma.elapsed != null;
    var maPausedProg = deviceMaActive && ma.state === 'paused' && ma.duration > 0 && ma.elapsed != null;
    if (maHasProg || maPausedProg) {
        var maSnapshot;
        if (maHasProg) {
            maSnapshot = idx != null
                ? _buildMergedMaProgressSnapshot(idx, ma, now)
                : {
                    elapsed: Math.max(0, Math.min(Number(ma.elapsed) || 0, Number(ma.duration) || 0)),
                    duration: Math.max(0, Number(ma.duration) || 0),
                    t: now,
                    key: _getMaProgressTrackKey(ma),
                };
            maSnapshot.paused = false;
        } else {
            // Paused — freeze at current elapsed position
            var existing = idx != null ? _maProgSnapshots[idx] : null;
            var frozenElapsed = Math.max(0, Math.min(Number(ma.elapsed) || 0, Number(ma.duration) || 0));
            if (existing && existing.key === _getMaProgressTrackKey(ma) && !existing.paused) {
                frozenElapsed = _getMaSnapshotElapsedNow(existing, now);
            }
            maSnapshot = {
                elapsed: frozenElapsed,
                duration: Math.max(0, Number(ma.duration) || 0),
                t: now,
                key: _getMaProgressTrackKey(ma),
                paused: true,
            };
        }
        var maElapsedSec = _getMaSnapshotElapsedNow(maSnapshot, now);
        if (idx != null) {
            _maProgSnapshots[idx] = maSnapshot;
            delete _progSnapshots[idx];
        }
        return {
            visible: true,
            pct: Math.min(100, (maElapsedSec / maSnapshot.duration) * 100),
            text: fmtSec(maElapsedSec) + ' / ' + fmtSec(maSnapshot.duration),
        };
    }

    if (idx != null) delete _maProgSnapshots[idx];

    var nativeHasProg = dev.track_duration_ms > 0 && dev.track_progress_ms != null;
    var nativePaused = Number(dev.playback_speed) === 0;
    if (nativeHasProg && (dev.playing || nativePaused)) {
        var nativeDuration = Math.max(0, Number(dev.track_duration_ms) || 0);
        var nativeProgress = Math.max(0, Math.min(Number(dev.track_progress_ms) || 0, nativeDuration));
        if (idx != null) {
            _progSnapshots[idx] = {pos: nativeProgress, dur: nativeDuration, t: now, paused: nativePaused};
        }
        return {
            visible: true,
            pct: Math.min(100, (nativeProgress / nativeDuration) * 100),
            text: fmtMs(nativeProgress) + ' / ' + fmtMs(nativeDuration),
        };
    }

    if (idx != null) delete _progSnapshots[idx];
    return {visible: false, pct: 0, text: ''};
}

function _applyPlaybackProgressDom(wrapEl, fillEl, timeEl, progress) {
    if (wrapEl) wrapEl.style.display = progress.visible ? '' : 'none';
    if (fillEl) fillEl.style.width = progress.visible ? (progress.pct + '%') : '0%';
    if (timeEl) timeEl.textContent = progress.text || '';
}

function _applyPlaybackProgressForIndex(idx, progress) {
    _applyPlaybackProgressDom(
        document.getElementById('dprog-wrap-' + idx),
        document.getElementById('dprog-fill-' + idx),
        document.getElementById('dprog-time-' + idx),
        progress
    );
    _applyPlaybackProgressDom(
        document.getElementById('dlprog-wrap-' + idx),
        document.getElementById('dlprog-fill-' + idx),
        document.getElementById('dlprog-time-' + idx),
        progress
    );
}

function formatSince(isoString) {
    if (!isoString) return '';
    try {
        var d = new Date(isoString);
        var now = new Date();
        var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        var dDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        var diffDays = Math.round((today - dDay) / 86400000);
        var timeStr = d.toLocaleTimeString('default', {hour: '2-digit', minute: '2-digit', hour12: false});
        if (diffDays === 0) return 'Since: ' + timeStr;
        if (diffDays === 1) return 'Since: yesterday ' + timeStr;
        return 'Since: ' + diffDays + 'd ago ' + timeStr;
    } catch (_) {
        return 'Since: ' + new Date(isoString).toLocaleString();
    }
}

function showToast(msg, type) {
    var container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + (type || 'info');
    toast.textContent = msg;
    container.appendChild(toast);
    requestAnimationFrame(function() {
        requestAnimationFrame(function() { toast.classList.add('show'); });
    });
    setTimeout(function() {
        toast.classList.remove('show');
        setTimeout(function() { toast.remove(); }, 300);
    }, type === 'error' ? 6000 : 3000);
}

function switchConfigTab(tabName) {
    document.querySelectorAll('.config-tab').forEach(function(tab) {
        tab.classList.toggle('active', tab.dataset.configTab === tabName);
    });
    document.querySelectorAll('.config-tab-panel').forEach(function(panel) {
        panel.classList.toggle('active', panel.dataset.configPanel === tabName);
    });
}

function _openConfigPanel(tabName, targetId, block) {
    var configSection = document.querySelector('.config-section');
    if (configSection) configSection.open = true;
    if (tabName) switchConfigTab(tabName);
    var panel = tabName ? document.getElementById('config-panel-' + tabName) : null;
    var target = targetId ? document.getElementById(targetId) : null;
    var scrollTarget = target || panel;
    if (scrollTarget) {
        scrollTarget.scrollIntoView({behavior: 'smooth', block: block || (target ? 'center' : 'start')});
    }
    return {section: configSection, panel: panel, target: target || panel};
}

function _highlightConfigTarget(target) {
    if (!target) return;
    target.classList.add('config-target-highlight');
    setTimeout(function() {
        target.classList.remove('config-target-highlight');
    }, 3000);
}

function openAuthSettings() {
    var opened = _openConfigPanel('security', 'auth-enabled', 'center');
    var target = document.getElementById('auth-enabled');
    var highlightTarget = target && typeof target.closest === 'function'
        ? target.closest('.config-setting-row') || target
        : target || (opened && opened.target);
    _highlightConfigTarget(highlightTarget);
    if (target && typeof target.focus === 'function') {
        target.focus({preventScroll: true});
    }
    return false;
}

function openLatencySettings() {
    var target = document.querySelector('[name="PULSE_LATENCY_MSEC"]');
    var opened = _openConfigPanel('general', target && target.id ? target.id : null, 'center');
    var highlightTarget = target && typeof target.closest === 'function'
        ? target.closest('.config-setting-row') || target
        : target || (opened && opened.target);
    _highlightConfigTarget(highlightTarget);
    if (target && typeof target.focus === 'function') {
        target.focus({preventScroll: true});
        if (typeof target.select === 'function') target.select();
    }
    return false;
}

function initConfigTabs() {
    document.querySelectorAll('.config-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            switchConfigTab(tab.dataset.configTab);
        });
    });
}

// ---- Auth helper ----

function _handleUnauthorized() {
    var loginUrl = (API_BASE || '') + '/login?next=' + encodeURIComponent(window.location.pathname);
    window.location.href = loginUrl;
}

// ---- Status ----

function listRowKey(dev) {
    return dev.player_name || dev.bluetooth_mac || dev.mac || '';
}

function _sortDevicesForStatus(devices) {
    return devices.slice().sort(function(a, b) {
        var score = function(d) { return d.playing ? 2 : (d.bluetooth_connected ? 1 : 0); };
        var gka = a.group_id || ('_' + a.player_name);
        var gkb = b.group_id || ('_' + b.player_name);
        var groupScore = function(gk) {
            var best = 0;
            devices.forEach(function(d) {
                if ((d.group_id || ('_' + d.player_name)) === gk) best = Math.max(best, score(d));
            });
            return best;
        };
        var gsa = groupScore(gka), gsb = groupScore(gkb);
        if (gsa !== gsb) return gsb - gsa;
        if (gka !== gkb) return gka < gkb ? -1 : 1;
        return score(b) - score(a);
    });
}

function deviceMatchesFilters(dev) {
    var adapterVal = document.getElementById('adapter-filter-sel') ? document.getElementById('adapter-filter-sel').value : '';
    var statusVal = document.getElementById('status-filter-sel') ? document.getElementById('status-filter-sel').value : '';
    if (adapterVal && dev.bluetooth_adapter_hci !== adapterVal) return false;
    if (!statusVal) return true;
    if (statusVal === 'playing') return !!dev.playing;
    if (statusVal === 'idle') return !dev.playing && dev.bluetooth_connected && dev.bt_management_enabled !== false;
    if (statusVal === 'stopping') return !!dev.stopping;
    if (statusVal === 'reconnecting') return !!(dev.reconnecting || dev.ma_reconnecting);
    if (statusVal === 'released') return dev.bt_management_enabled === false;
    if (statusVal === 'error') return getDeviceStatusKey(dev) === 'no-sink';
    return true;
}

function getDeviceSinkName(dev) {
    if (!dev) return '';
    return dev.sink_name || dev.bluetooth_sink_name || dev.sink || '';
}

function deviceHasSink(dev) {
    return !!(dev && (dev.has_sink || getDeviceSinkName(dev)));
}

function _getDeviceActionCapability(dev, actionKey) {
    var actions = (((dev || {}).capabilities || {}).actions) || {};
    return actions[actionKey] || null;
}

function _capabilityAvailable(capability, fallback) {
    if (!capability || capability.currently_available === undefined || capability.currently_available === null) {
        return !!fallback;
    }
    return !!capability.currently_available;
}

function _capabilityBlockedReason(capability, fallback) {
    if (capability && capability.blocked_reason) return capability.blocked_reason;
    return fallback || '';
}

function _capabilityRecommendedActionKey(capability) {
    if (capability && capability.recommended_action) return String(capability.recommended_action || '').trim();
    var safeActions = (capability && capability.safe_actions) || [];
    return safeActions.length ? String(safeActions[0] || '').trim() : '';
}

function _capabilityDependencyLabels(dependsOn) {
    var labels = {
        bt_management_enabled: 'Bluetooth management enabled',
        bluetooth_paired: 'Bluetooth pairing',
        reconnect_idle: 'Reconnect to finish',
        device_not_stopping: 'Device stop to finish',
        sendspin_connected: 'Sendspin transport',
        audio_sink: 'Resolved audio sink',
        ma_connected: 'Music Assistant link',
    };
    return (dependsOn || []).map(function(key) {
        var normalized = String(key || '').trim();
        return labels[normalized] || normalized.replace(/_/g, ' ');
    }).filter(function(label) { return !!label; });
}

function _capabilityActionLabel(actionKey, dev) {
    if (actionKey === 'reconnect' || actionKey === 'reconnect_device') return 'Reconnect speaker';
    if (actionKey === 'pair_device') return 'Re-pair speaker';
    if (actionKey === 'toggle_bt_management') {
        return dev && dev.bt_management_enabled === false ? 'Reclaim Bluetooth' : 'Release Bluetooth';
    }
    if (actionKey === 'open_diagnostics') return 'Open diagnostics';
    if (actionKey === 'open_ma_settings') return 'Open Music Assistant settings';
    if (actionKey === 'scan_devices') return 'Scan for speakers';
    if (actionKey === 'retry_ma_discovery') return 'Retry discovery';
    if (actionKey === 'apply_latency_recommended') return 'Apply recommended latency';
    if (actionKey === 'open_devices_settings') return 'Open device settings';
    return 'Open details';
}

function _capabilityActionPayload(actionKey, dev, value) {
    var normalized = String(actionKey || '').trim();
    if (!normalized) return null;
    var deviceNames = dev && dev.player_name ? [String(dev.player_name)] : [];
    if (normalized === 'reconnect') normalized = 'reconnect_device';
    if (normalized === 'download_diagnostics') normalized = 'open_diagnostics';
    var payload = {
        key: normalized,
        label: _capabilityActionLabel(normalized, dev),
    };
    if (deviceNames.length && (
        normalized === 'reconnect_device' ||
        normalized === 'pair_device' ||
        normalized === 'toggle_bt_management'
    )) {
        payload.device_names = deviceNames;
    }
    if (value !== undefined) payload.value = value;
    return payload;
}

function _pushBlockedControlHint(hints, hint) {
    if (!hint || !hint.message) return;
    var marker = [hint.title || '', hint.message || '', (hint.dependsOn || []).join(','), (hint.action || {}).key || ''].join('|');
    if (hints.some(function(item) { return item._marker === marker; })) return;
    hint._marker = marker;
    hints.push(hint);
}

function _normalizeGuidanceActionKey(actionKey) {
    if (actionKey === 'reconnect_devices') return 'reconnect_device';
    if (actionKey === 'toggle_bt_management_devices') return 'toggle_bt_management';
    return actionKey || '';
}

function _guidanceActionAppliesToDevice(issue, deviceName) {
    if (!issue || !deviceName) return false;
    var issueDeviceNames = [];
    if (Array.isArray(issue.device_names)) issueDeviceNames = issue.device_names;
    else if (issue.context && Array.isArray(issue.context.device_names)) issueDeviceNames = issue.context.device_names;
    return issueDeviceNames.indexOf(deviceName) >= 0;
}

function _guidedActionKeysForDevice(dev, guidance) {
    if (!dev || !guidance || !guidance.issue_groups || !guidance.issue_groups.length) return {};
    var deviceName = dev.player_name || '';
    if (!deviceName) return {};
    return guidance.issue_groups.reduce(function(keys, issue) {
        if (!_guidanceActionAppliesToDevice(issue, deviceName)) return keys;
        [issue.primary_action].concat(issue.secondary_actions || []).forEach(function(action) {
            var actionKey = _normalizeGuidanceActionKey(action && action.key);
            if (actionKey) keys[actionKey] = true;
        });
        return keys;
    }, {});
}

function _collectDeviceBlockedControlHints(dev, transportState, guidance) {
    if (!dev || _isDeviceDisabled(dev)) return [];
    var hints = [];
    var reconnectCapability = _getDeviceActionCapability(dev, 'reconnect');
    var toggleManagementCapability = _getDeviceActionCapability(dev, 'toggle_bt_management');
    var playPauseCapability = _getDeviceActionCapability(dev, 'play_pause');
    var volumeCapability = _getDeviceActionCapability(dev, 'volume');
    var queueCapability = _getDeviceActionCapability(dev, 'queue_control');
    var reconnectAvailable = _capabilityAvailable(reconnectCapability, dev.bt_management_enabled !== false);
    var managementAvailable = _capabilityAvailable(toggleManagementCapability, true);
    var transportAvailable = !!(transportState && transportState.canTransport);
    var sinkAvailable = !!(transportState && transportState.hasSink);
    var queueAvailable = !!(transportState && transportState.hasQueueControls);

    if (!reconnectAvailable) {
        _pushBlockedControlHint(hints, {
            title: 'Reconnect unavailable',
            message: _capabilityBlockedReason(reconnectCapability, 'Reconnect is unavailable right now.'),
            dependsOn: ((reconnectCapability || {}).depends_on) || (((reconnectCapability || {}).blocked_reason_detail || {}).depends_on) || [],
            action: _capabilityActionPayload(_capabilityRecommendedActionKey(reconnectCapability), dev),
        });
    }
    if (!managementAvailable) {
        _pushBlockedControlHint(hints, {
            title: 'Bluetooth management unavailable',
            message: _capabilityBlockedReason(toggleManagementCapability, 'Bluetooth management action is unavailable right now.'),
            dependsOn: ((toggleManagementCapability || {}).depends_on) || (((toggleManagementCapability || {}).blocked_reason_detail || {}).depends_on) || [],
            action: _capabilityActionPayload(_capabilityRecommendedActionKey(toggleManagementCapability), dev),
        });
    }
    if (!transportAvailable) {
        _pushBlockedControlHint(hints, {
            title: 'Playback unavailable',
            message: _capabilityBlockedReason(playPauseCapability, (transportState && transportState.transportUnavailableTitle) || 'Playback transport is unavailable right now.'),
            dependsOn: ((playPauseCapability || {}).depends_on) || (((playPauseCapability || {}).blocked_reason_detail || {}).depends_on) || [],
            action: _capabilityActionPayload(_capabilityRecommendedActionKey(playPauseCapability), dev),
        });
    }
    if (!sinkAvailable) {
        _pushBlockedControlHint(hints, {
            title: 'Volume unavailable',
            message: _capabilityBlockedReason(volumeCapability, 'Audio sink is not configured.'),
            dependsOn: ((volumeCapability || {}).depends_on) || (((volumeCapability || {}).blocked_reason_detail || {}).depends_on) || [],
            action: _capabilityActionPayload(_capabilityRecommendedActionKey(volumeCapability), dev),
        });
    }
    if (!queueAvailable && !(transportState && transportState.queueActionPending)) {
        _pushBlockedControlHint(hints, {
            title: 'Queue controls unavailable',
            message: _capabilityBlockedReason(queueCapability, (transportState && transportState.queueUnavailableTitle) || 'Queue controls are unavailable right now.'),
            dependsOn: ((queueCapability || {}).depends_on) || (((queueCapability || {}).blocked_reason_detail || {}).depends_on) || [],
            action: _capabilityActionPayload(_capabilityRecommendedActionKey(queueCapability), dev),
        });
    }
    var guidedActionKeys = _guidedActionKeysForDevice(dev, guidance);
    return hints.filter(function(hint) {
        var actionKey = _normalizeGuidanceActionKey(hint && hint.action && hint.action.key);
        return !actionKey || !guidedActionKeys[actionKey];
    }).slice(0, 2).map(function(hint) {
        delete hint._marker;
        return hint;
    });
}

function _renderBlockedControlHints(hints, options) {
    if (!hints || !hints.length) return '';
    var opts = options || {};
    return '<div class="device-blocked-hints' + (opts.compact ? ' is-compact' : '') + '">' + hints.map(function(hint) {
        var dependencyLabels = _capabilityDependencyLabels(hint.dependsOn || []);
        var actionHtml = hint.action ? '<div class="device-blocked-hint-actions">' + _renderGuidanceActionLink(hint.action) + '</div>' : '';
        return '<div class="device-blocked-hint">' +
            '<div class="device-blocked-hint-title">' + escHtml(hint.title || 'Control unavailable') + '</div>' +
            '<div class="device-blocked-hint-message">' + escHtml(hint.message || '') + '</div>' +
            (dependencyLabels.length
                ? '<div class="device-blocked-hint-deps">Needs: ' + escHtml(dependencyLabels.join(' • ')) + '</div>'
                : '') +
            actionHtml +
        '</div>';
    }).join('') + '</div>';
}

function getDeviceSinkLabel(dev) {
    var sinkName = getDeviceSinkName(dev);
    if (sinkName) return sinkName;
    if (_isDeviceDisabled(dev)) return 'Disabled';
    if (dev && dev.bt_management_enabled === false) return 'Released';
    if (dev && dev.bluetooth_connected) return 'Waiting for sink';
    return 'Not attached';
}

function getDeviceStatusKey(dev) {
    if (_isDeviceDisabled(dev)) return 'disabled';
    if (dev && dev.bt_management_enabled === false) return 'released';
    return getUnifiedDeviceStatusMeta(dev).key;
}

function getDeviceStatusLabel(dev) {
    if (_isDeviceDisabled(dev)) return 'Disabled';
    if (dev && dev.bt_management_enabled === false) return getDeviceReleaseMeta(dev).label;
    return getUnifiedDeviceStatusMeta(dev).label;
}

function getDeviceStatusClass(dev) {
    if (_isDeviceDisabled(dev)) return 'neutral';
    if (dev && dev.bt_management_enabled === false) {
        return dev.bt_released_by === 'auto' ? 'warning' : 'released';
    }
    return getUnifiedDeviceStatusMeta(dev).runtimeClass;
}

function _deviceStatusToneClass(tone) {
    return 'is-' + (tone || 'neutral');
}

function _deviceStatusDotClass(tone, pulse) {
    var dotClass = tone === 'success'
        ? 'green'
        : tone === 'error'
            ? 'red'
            : tone === 'warning'
                ? 'orange'
                : 'grey';
    return pulse ? dotClass + ' pulse' : dotClass;
}

function _buildBadgeStateMeta(tone, pulse, summary) {
    var normalizedTone = tone || 'neutral';
    return {
        tone: normalizedTone,
        toneClass: _deviceStatusToneClass(normalizedTone),
        dotClass: _deviceStatusDotClass(normalizedTone, !!pulse),
        pulse: !!pulse,
        summary: summary || '',
    };
}

function _getStatusIndicatorSymbol(statusMeta) {
    var key = (statusMeta && statusMeta.key) || 'idle';
    if (key === 'playing') return '▶';
    if (key === 'reconnecting' || key === 'buffering') return '⟳';
    if (key === 'stopping') return '⏹';
    if (key === 'stale') return '⚠';
    if (key === 'no-sink') return '⛔';
    if (key === 'disconnected') return '⊘';
    return '⏸';
}

function _getBadgeIndicatorClassName(stateMeta, extraClass, kind) {
    var classes = 'meta-badge-indicator';
    if (kind === 'status' && ((stateMeta && stateMeta.key) || 'idle') !== 'no-sink') classes += ' status-symbol-indicator';
    if (extraClass) classes += ' ' + extraClass;
    if (stateMeta && stateMeta.pulse) classes += ' is-pulse';
    return classes;
}

function _getBadgeIndicatorInnerHtml(kind, stateMeta) {
    if (kind === 'status') {
        if (((stateMeta && stateMeta.key) || 'idle') === 'no-sink') {
            return _noSinkIconSvg('meta-badge-indicator-icon');
        }
        return '<span class="status-symbol-indicator-text">' + escHtml(_getStatusIndicatorSymbol(stateMeta)) + '</span>';
    }
    if (kind === 'battery') return _batteryIconSvg((stateMeta && stateMeta.level) || 0, 'meta-badge-indicator-icon');
    if (kind === 'check') return _checkIconSvg('meta-badge-indicator-icon');
    if (kind === 'release') return _releaseIconSvg('meta-badge-indicator-icon');
    if (kind === 'bt') return _bluetoothIconSvg('meta-badge-indicator-icon');
    if (kind === 'ma') return _maIconSvg('meta-badge-indicator-icon');
    if (kind === 'anchor') return _anchorIconSvg('meta-badge-indicator-icon');
    return _chainIconSvg('meta-badge-indicator-icon');
}

function _renderBadgeIndicatorHtml(kind, stateMeta, extraClass) {
    return '<span class="' + _getBadgeIndicatorClassName(stateMeta, extraClass, kind) + '">' +
        _getBadgeIndicatorInnerHtml(kind, stateMeta) +
    '</span>';
}

function _uiIconSvg(kind, className) {
    var cls = className ? ' class="' + className + '"' : '';
    if (kind === 'settings') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/>' +
            '<path d="M4 12h2"/><path d="M10 12h10"/><circle cx="8" cy="12" r="2"/>' +
            '<path d="M4 18h10"/><path d="M18 18h2"/><circle cx="16" cy="18" r="2"/>' +
        '</svg>';
    }
    if (kind === 'notes') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M7 3h8l4 4v13a1 1 0 0 1-1 1H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/>' +
            '<path d="M15 3v5h5"/><path d="M9 12h6"/><path d="M9 16h6"/>' +
        '</svg>';
    }
    if (kind === 'tag') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="m20 10-8.1 8.1a2 2 0 0 1-2.83 0L3 12.03V4h8.03L20 12.97Z"/><circle cx="8" cy="8" r="1.4" fill="currentColor" stroke="none"/>' +
        '</svg>';
    }
    if (kind === 'speaker') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
            '<path d="M17 2H7c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-5 2c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm0 16c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/>' +
        '</svg>';
    }
    if (kind === 'check') return _checkIconSvg(className);
    if (kind === 'bt') return _bluetoothIconSvg(className);
    if (kind === 'ma') return _maIconSvg(className);
    if (kind === 'lock') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>' +
        '</svg>';
    }
    if (kind === 'search') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2"/>' +
        '</svg>';
    }
    if (kind === 'key') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="7.5" cy="16.5" r="3.5"/><path d="M10.2 13.8 20 4"/><path d="M15 4h5v5"/><path d="m14.6 9.4 2 2"/>' +
        '</svg>';
    }
    if (kind === 'refresh') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M20 5v5h-5"/><path d="M4 19v-5h5"/><path d="M6.9 9A7 7 0 0 1 18 7.5L20 10"/><path d="M17.1 15A7 7 0 0 1 6 16.5L4 14"/>' +
        '</svg>';
    }
    if (kind === 'sun') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5"/><path d="M12 19v2.5"/><path d="m4.93 4.93 1.77 1.77"/><path d="m17.3 17.3 1.77 1.77"/><path d="M2.5 12H5"/><path d="M19 12h2.5"/><path d="m4.93 19.07 1.77-1.77"/><path d="m17.3 6.7 1.77-1.77"/>' +
        '</svg>';
    }
    if (kind === 'moon') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M21 12.4A8.4 8.4 0 1 1 11.6 3a6.9 6.9 0 0 0 9.4 9.4Z"/>' +
        '</svg>';
    }
    if (kind === 'theme-auto') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="8" fill="none"/><path d="M9.2 16.6 12 7.4l2.8 9.2" fill="none"/><path d="M10.15 13.5h3.7" fill="none"/>' +
        '</svg>';
    }
    if (kind === 'download') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 4v11"/><path d="m8 11 4 4 4-4"/><path d="M5 19h14"/>' +
        '</svg>';
    }
    if (kind === 'upload') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 20V9"/><path d="m8 12 4-4 4 4"/><path d="M5 5h14"/>' +
        '</svg>';
    }
    if (kind === 'user') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="8" r="4"/><path d="M5 19c1.9-3 4.3-4.5 7-4.5S17.1 16 19 19"/>' +
        '</svg>';
    }
    if (kind === 'signout') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4"/><path d="M14 8l5 4-5 4"/><path d="M9 12h10"/>' +
        '</svg>';
    }
    if (kind === 'status-neutral') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="7"/>' +
        '</svg>';
    }
    if (kind === 'status-success') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="7"/><path d="m9.2 12.2 1.9 1.9 4-4.1"/>' +
        '</svg>';
    }
    if (kind === 'info') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="7"/><path d="M12 11.5v4"/><circle cx="12" cy="8" r="1" fill="currentColor" stroke="none"/>' +
        '</svg>';
    }
    if (kind === 'chevron-down') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="m6 9 6 6 6-6"/>' +
        '</svg>';
    }
    if (kind === 'chevron-up') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="m6 15 6-6 6 6"/>' +
        '</svg>';
    }
    if (kind === 'report' || kind === 'warning') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 3.5 3.5 19h17L12 3.5Z"></path><path d="M12 9v4.75"></path><circle cx="12" cy="17.1" r="1" fill="currentColor" stroke="none"></circle>' +
        '</svg>';
    }
    if (kind === 'plug') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M9 3v5"/><path d="M15 3v5"/><path d="M8 8h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V8Z"/><path d="M12 14.5V21"/>' +
        '</svg>';
    }
    if (kind === 'plus') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M12 5v14"/><path d="M5 12h14"/>' +
        '</svg>';
    }
    return '';
}

function _hydrateUiIcons(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (root && root.getAttribute && root.getAttribute('data-ui-icon')) {
        root.innerHTML = _uiIconSvg(root.getAttribute('data-ui-icon'), 'ui-icon-svg');
        root.removeAttribute('data-ui-icon');
    }
    scope.querySelectorAll('[data-ui-icon]').forEach(function(el) {
        el.innerHTML = _uiIconSvg(el.getAttribute('data-ui-icon'), 'ui-icon-svg');
        el.removeAttribute('data-ui-icon');
    });
}

function _setUiIconSlot(el, kind) {
    if (!el) return;
    el.innerHTML = _uiIconSvg(kind, 'ui-icon-svg');
    el.removeAttribute('data-ui-icon');
}

function _buttonLabelWithIconHtml(kind, label) {
    return _uiIconSvg(kind, 'btn-icon-svg') + '<span>' + escHtml(label) + '</span>';
}

function _renderEmptyStateHtml(options) {
    var opts = options || {};
    var classes = ['ui-empty-state'];
    if (opts.className) classes.push(opts.className);
    if (opts.center) classes.push('ui-empty-state--center');
    if (opts.compact) classes.push('ui-empty-state--compact');
    if (opts.inline) classes.push('ui-empty-state--inline');
    if (opts.tone) classes.push('is-' + opts.tone);
    return '<div class="' + classes.join(' ') + '">' +
        (opts.icon ? '<div class="ui-empty-state-icon">' + _uiIconSvg(opts.icon, 'ui-icon-svg') + '</div>' : '') +
        (opts.title ? '<div class="ui-empty-state-title">' + escHtml(opts.title) + '</div>' : '') +
        (opts.copyHtml
            ? '<div class="ui-empty-state-copy">' + opts.copyHtml + '</div>'
            : (opts.copy ? '<div class="ui-empty-state-copy">' + escHtml(opts.copy) + '</div>' : '')) +
        (opts.actionsHtml ? '<div class="ui-empty-state-actions">' + opts.actionsHtml + '</div>' : '') +
    '</div>';
}

function _renderDiagEmptyCardHtml(title, copy, options) {
    var opts = options || {};
    return '<div class="diag-mini-card diag-mini-card--empty">' +
        _renderEmptyStateHtml({
            icon: opts.icon || 'info',
            title: title,
            copy: copy,
            compact: true,
            center: true,
            tone: opts.tone || 'neutral',
        }) +
    '</div>';
}

function _getBtBadgeStateMeta(dev, adapterInfo) {
    var info = adapterInfo || _getAdapterDisplayInfo(dev);
    if (info.empty) return _buildBadgeStateMeta('neutral', false, 'No Bluetooth adapter assigned');
    if (_isDeviceDisabled(dev)) {
        return _buildBadgeStateMeta('neutral', false, 'Device is globally disabled');
    }
    if (dev && dev.bt_management_enabled === false && dev.bt_released_by === 'auto') {
        return _buildBadgeStateMeta('warning', false, 'Bluetooth management auto-released');
    }
    if (dev && dev.bt_management_enabled === false) {
        return _buildBadgeStateMeta('neutral', false, 'Bluetooth management released');
    }
    if (dev && dev.reconnecting) return _buildBadgeStateMeta('warning', true, 'Bluetooth reconnecting');
    if (dev && dev.bluetooth_connected) return _buildBadgeStateMeta('success', false, 'Bluetooth connected');
    return _buildBadgeStateMeta('error', false, 'Bluetooth disconnected');
}

function _getServiceBadgeStateMeta(dev) {
    var maConnected = !!((dev && dev.ma_now_playing) || {}).connected;
    if (dev && dev.ma_reconnecting) {
        return _buildBadgeStateMeta('warning', true, 'Refreshing Music Assistant connection');
    }
    if (dev && dev.server_connected) {
        return _buildBadgeStateMeta('success', false, maConnected ? 'Music Assistant connected' : 'Bridge service connected');
    }
    return _buildBadgeStateMeta('error', false, 'Music Assistant unavailable');
}

function _getGroupBadgeStateMeta(dev, groupMeta) {
    var meta = groupMeta || _getGroupBadgeMeta(dev);
    if (!meta || meta.isEmpty) return _buildBadgeStateMeta('neutral', false, 'No Music Assistant group');
    return _buildBadgeStateMeta('neutral', false, 'Music Assistant group assigned');
}

function getDeviceReleaseMeta(dev) {
    if (_isDeviceDisabled(dev)) {
        return {
            visible: false,
            isAuto: false,
            label: 'Disabled',
            summary: 'Device is globally disabled',
            title: 'Device is globally disabled until it is re-enabled in Configuration → Devices',
            stateMeta: _buildBadgeStateMeta('neutral', false, 'Device is globally disabled'),
        };
    }
    var isReleased = !!(dev && dev.bt_management_enabled === false);
    var isAuto = !!(isReleased && dev.bt_released_by === 'auto');
    var stateMeta = _buildBadgeStateMeta(isAuto ? 'warning' : 'neutral', false, isAuto
        ? 'Bluetooth management auto-released'
        : 'Bluetooth management released');
    return {
        visible: isReleased,
        isAuto: isAuto,
        label: isAuto ? 'Auto-released' : 'Released',
        summary: isAuto ? 'Auto-released after connection issues' : 'Ready to reclaim',
        title: isAuto
            ? 'Auto-released due to connection issues — click Reclaim to retry'
            : 'BT management released — click Reclaim to resume',
        tone: isAuto ? 'warning' : 'neutral',
        toneClass: _deviceStatusToneClass(isAuto ? 'warning' : 'neutral'),
        stateMeta: stateMeta,
        indicatorKind: 'release',
    };
}

function _getReleaseBadgeInnerHtml(releaseMeta) {
    if (!releaseMeta || !releaseMeta.visible) return '';
    return _renderBadgeIndicatorHtml(releaseMeta.indicatorKind || 'release', releaseMeta.stateMeta) +
        '<span class="meta-badge-label">' + escHtml(releaseMeta.label) + '</span>';
}

function _getMaSyncMeta(snapshot) {
    return (snapshot && snapshot._sync_meta) || {};
}

function _getPendingMaSummary(meta) {
    var pendingOps = Array.isArray(meta.pending_ops) ? meta.pending_ops : [];
    var op = pendingOps.length ? pendingOps[0] : {};
    var action = op.action || 'command';
    if (action === 'next') return 'Skipping to the next track';
    if (action === 'previous') return 'Returning to the previous track';
    if (action === 'shuffle') return 'Updating shuffle mode';
    if (action === 'repeat') return 'Updating repeat mode';
    if (action === 'seek') return 'Seeking to the new position';
    return 'Waiting for Music Assistant confirmation';
}

function _hasPendingMaAction(meta, action) {
    var pendingOps = Array.isArray(meta.pending_ops) ? meta.pending_ops : [];
    return pendingOps.some(function(op) { return op && op.action === action; });
}

function _isQueueTransportActionPending(meta) {
    var pendingOps = Array.isArray(meta.pending_ops) ? meta.pending_ops : [];
    return pendingOps.some(function(op) {
        var action = op && op.action;
        return action === 'next' || action === 'previous' || action === 'shuffle' || action === 'repeat' || action === 'seek';
    });
}

function _buildQueueActionTitle(baseTitle, pending, queueReadyTitle, pendingSummary) {
    if (queueReadyTitle) return queueReadyTitle;
    if (pending) return baseTitle + ' — ' + (pendingSummary || 'Waiting for Music Assistant confirmation');
    return baseTitle;
}

function _applyNativeTransportUiPrediction(dev, action) {
    if (!dev || !action) return;
    if (!dev.ma_now_playing || typeof dev.ma_now_playing !== 'object') dev.ma_now_playing = {};

    if (action === 'shuffle' || action === 'unshuffle') {
        var shuffleEnabled = action === 'shuffle';
        dev.ma_now_playing.shuffle = shuffleEnabled;
        dev.shuffle = shuffleEnabled;
        return;
    }

    if (action === 'repeat_off' || action === 'repeat_all' || action === 'repeat_one') {
        var repeatMode = action === 'repeat_all' ? 'all' : action === 'repeat_one' ? 'one' : 'off';
        dev.ma_now_playing.repeat = repeatMode;
        dev.repeat_mode = repeatMode;
    }
}

function getUnifiedDeviceStatusMeta(dev) {
    var safeDev = dev || {};
    var ma = safeDev.ma_now_playing || {};
    var maMeta = _getMaSyncMeta(ma);
    var maState = ma.state;
    var maPlaying = maState === 'playing';
    var tone = 'neutral';
    var key = 'ready';
    var label = 'Ready';
    var summary = 'Connected and ready';
    var pulse = false;

    if (safeDev.stopping) {
        key = 'stopping';
        label = 'Stopping';
        tone = 'warning';
        summary = 'Stopping playback service';
        pulse = true;
    } else if (safeDev.reconnecting || safeDev.ma_reconnecting) {
        key = 'reconnecting';
        label = 'Reconnecting';
        tone = 'warning';
        summary = safeDev.ma_reconnecting ? 'Refreshing Music Assistant connection' : 'Trying to reconnect';
        pulse = true;
    } else if (safeDev.bt_standby && safeDev.bt_waking) {
        key = 'waking';
        label = 'Waking';
        tone = 'warning';
        summary = 'Reconnecting Bluetooth after standby';
        pulse = true;
    } else if (safeDev.bt_standby) {
        key = 'standby';
        label = 'Standby';
        tone = 'neutral';
        var sinceTxt = safeDev.bt_standby_since ? ' (' + _formatDuration(new Date(safeDev.bt_standby_since)) + ')' : '';
        summary = 'Idle standby — saves speaker battery' + sinceTxt;
    } else if (!safeDev.bluetooth_connected) {
        key = 'disconnected';
        label = 'Disconnected';
        tone = 'neutral';
        summary = 'Waiting for connection';
    } else if (!deviceHasSink(safeDev) && safeDev.bluetooth_mac) {
        key = 'no-sink';
        label = 'No sink';
        tone = 'error';
        summary = 'Connected, waiting for audio sink';
    } else if (maMeta.pending && safeDev.server_connected && deviceHasSink(safeDev)) {
        key = 'ma-pending';
        label = 'Applying';
        tone = 'warning';
        summary = _getPendingMaSummary(maMeta);
        pulse = true;
    } else if (safeDev.playing && safeDev.audio_streaming) {
        key = 'playing';
        label = 'Playing';
        tone = 'success';
        summary = 'Streaming audio';
    } else if (safeDev.playing) {
        key = 'stale';
        label = 'Stale stream';
        tone = 'warning';
        summary = 'Playback stalled';
    } else if (maPlaying && safeDev.server_connected && deviceHasSink(safeDev)) {
        key = 'buffering';
        label = 'Buffering';
        tone = 'warning';
        summary = 'Starting playback';
        pulse = true;
    }

    var toneClass = _deviceStatusToneClass(tone);
    return {
        key: key,
        label: label,
        summary: summary,
        tone: tone,
        toneClass: toneClass,
        badgeToneClass: toneClass,
        dotClass: _deviceStatusDotClass(tone, pulse),
        listDotClass: toneClass + (pulse ? ' is-pulse' : ''),
        cardStateClass: toneClass,
        iconToneClass: toneClass,
        pulse: pulse,
        runtimeClass: key === 'playing'
            ? 'playing'
            : key === 'ready'
                ? 'connected'
                : key === 'no-sink'
                    ? 'error'
                    : key === 'disconnected' || key === 'standby'
                        ? 'disconnected'
                        : 'warning',
    };
}

function getDeviceDisplayStatusMeta(dev) {
    var runtimeMeta = getUnifiedDeviceStatusMeta(dev);
    var releaseMeta = getDeviceReleaseMeta(dev);
    if (!releaseMeta.visible) return runtimeMeta;
    var tone = releaseMeta.isAuto ? 'warning' : 'neutral';
    var toneClass = _deviceStatusToneClass(tone);
    return {
        key: 'idle',
        label: 'Idle',
        summary: releaseMeta.summary,
        tone: tone,
        toneClass: toneClass,
        badgeToneClass: toneClass,
        dotClass: _deviceStatusDotClass(tone, false),
        listDotClass: toneClass,
        cardStateClass: toneClass,
        iconToneClass: toneClass,
        pulse: false,
        runtimeClass: runtimeMeta.runtimeClass,
    };
}

function _getSafeArtworkUrl(imgUrl) {
    if (!imgUrl || typeof imgUrl !== 'string') return '';
    var trimmed = imgUrl.trim();
    if (!trimmed) return '';
    if (trimmed.indexOf('data:') === 0) return trimmed;
    try {
        var parsed = new URL(trimmed, window.location.href);
        if (parsed.origin === window.location.origin) return parsed.href;
    } catch (_) {
        return '';
    }
    return '';
}

function _closeArtworkPreviews(exceptEl) {
    document.querySelectorAll('.np-art.preview-open').forEach(function(el) {
        if (exceptEl && el === exceptEl) return;
        el.classList.remove('preview-open');
        el.setAttribute('aria-expanded', 'false');
        var layerEl = el.closest('.device-card, .list-row');
        if (layerEl) layerEl.classList.remove('preview-open');
    });
}

function toggleArtworkPreview(event, el) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    if (!el) return;
    var previewEl = el.querySelector('.artwork-preview-popover');
    if (!previewEl || !previewEl.getAttribute('src')) return;
    var shouldOpen = !el.classList.contains('preview-open');
    _closeArtworkPreviews(el);
    el.classList.toggle('preview-open', shouldOpen);
    el.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    var layerEl = el.closest('.device-card, .list-row');
    if (layerEl) layerEl.classList.toggle('preview-open', shouldOpen);
}

function onArtworkPreviewKeydown(event, el) {
    if (!event) return;
    if (event.key === 'Enter' || event.key === ' ') {
        toggleArtworkPreview(event, el);
    } else if (event.key === 'Escape') {
        _closeArtworkPreviews();
    }
}

function _closeActionMenus(eventTarget) {
    var openMenus = document.querySelectorAll('.notice-action-menu[open], .diag-action-menu[open], .bt-device-action-menu[open]');
    openMenus.forEach(function(menu) {
        if (eventTarget && menu.contains(eventTarget)) return;
        menu.open = false;
    });
}

document.addEventListener('click', function(event) {
    _closeArtworkPreviews();
    _closeActionMenus(event && event.target ? event.target : null);
});

document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        _closeArtworkPreviews();
        _closeActionMenus(null);
    }
});

function _renderArtworkThumbHtml(artUrl, thumbClass, previewClass) {
    return '<img class="' + thumbClass + '" src="' + escHtmlAttr(artUrl) + '" alt="">' +
        '<img class="' + previewClass + '" src="' + escHtmlAttr(artUrl) + '" alt="">';
}

function _setAlbumArtState(artEl, placeholderEl, imgUrl, previewEl) {
    if (!artEl) return;
    var normalizedUrl = _getSafeArtworkUrl(imgUrl);
    var failedSrc = artEl.dataset.failedSrc || '';
    var containerEl = artEl.closest('.np-art');

    if (!normalizedUrl || failedSrc === normalizedUrl) {
        artEl.style.display = 'none';
        artEl.removeAttribute('src');
        if (previewEl) previewEl.removeAttribute('src');
        if (containerEl) {
            containerEl.classList.remove('has-artwork-preview', 'preview-open');
            containerEl.setAttribute('aria-expanded', 'false');
            var layerEl = containerEl.closest('.device-card, .list-row');
            if (layerEl) layerEl.classList.remove('preview-open');
        }
        if (placeholderEl) placeholderEl.style.display = '';
        return;
    }

    artEl.onload = function() {
        artEl.dataset.failedSrc = '';
        artEl.style.display = '';
        if (previewEl) previewEl.src = normalizedUrl;
        if (containerEl) containerEl.classList.add('has-artwork-preview');
        if (placeholderEl) placeholderEl.style.display = 'none';
    };
    artEl.onerror = function() {
        artEl.dataset.failedSrc = normalizedUrl;
        artEl.style.display = 'none';
        artEl.removeAttribute('src');
        if (previewEl) previewEl.removeAttribute('src');
        if (containerEl) {
            containerEl.classList.remove('has-artwork-preview', 'preview-open');
            containerEl.setAttribute('aria-expanded', 'false');
            var layerEl = containerEl.closest('.device-card, .list-row');
            if (layerEl) layerEl.classList.remove('preview-open');
        }
        if (placeholderEl) placeholderEl.style.display = '';
    };

    if (artEl.dataset.currentSrc !== normalizedUrl) {
        artEl.dataset.currentSrc = normalizedUrl;
        artEl.src = normalizedUrl;
        if (previewEl) previewEl.removeAttribute('src');
        if (containerEl) {
            containerEl.classList.remove('has-artwork-preview', 'preview-open');
            containerEl.setAttribute('aria-expanded', 'false');
            var layerEl = containerEl.closest('.device-card, .list-row');
            if (layerEl) layerEl.classList.remove('preview-open');
        }
    } else if (artEl.complete && artEl.naturalWidth > 0) {
        artEl.style.display = '';
        if (previewEl) previewEl.src = normalizedUrl;
        if (containerEl) containerEl.classList.add('has-artwork-preview');
        if (placeholderEl) placeholderEl.style.display = 'none';
    }
}

function _findRuntimeDevice(name, mac) {
    var normalizedMac = (mac || '').trim().toUpperCase();
    var normalizedName = (name || '').trim();
    return (lastDevices || []).find(function(dev) {
        var devMac = (dev.bluetooth_mac || dev.mac || '').trim().toUpperCase();
        if (normalizedMac && devMac === normalizedMac) return true;
        return normalizedName && (dev.player_name || '').trim() === normalizedName;
    }) || null;
}

function _normalizeDeviceMac(mac) {
    return String(mac || '').trim().toUpperCase();
}

function _normalizeDeviceName(name) {
    return String(name || '').trim().toLowerCase();
}

function _deviceNamesMatch(configName, runtimeName) {
    var normalizedConfig = _normalizeDeviceName(configName);
    var normalizedRuntime = _normalizeDeviceName(runtimeName);
    if (!normalizedConfig || !normalizedRuntime) return false;
    return normalizedRuntime === normalizedConfig || normalizedRuntime.indexOf(normalizedConfig + ' @ ') === 0;
}

function _getViewModeStorageKey() {
    return _viewModeStorageScope === 'demo' ? VIEW_MODE_STORAGE_KEY + ':demo' : VIEW_MODE_STORAGE_KEY;
}

function _loadSavedViewMode() {
    try {
        var saved = window.localStorage.getItem(_getViewModeStorageKey());
        return saved === 'list' || saved === 'grid' ? saved : null;
    } catch (_) {
        return null;
    }
}

function _persistViewMode(mode) {
    try {
        window.localStorage.setItem(_getViewModeStorageKey(), mode);
    } catch (_) {
        // Ignore storage failures and fall back to in-memory mode.
    }
}

function _getViewModeMediaQuery() {
    if (!window.matchMedia) return null;
    if (!_viewModeMediaQuery) {
        _viewModeMediaQuery = window.matchMedia('(max-width: ' + MOBILE_LIST_VIEW_MAX_WIDTH + 'px)');
    }
    return _viewModeMediaQuery;
}

function _isMobileListViewForced() {
    var media = _getViewModeMediaQuery();
    if (media) return !!media.matches;
    if (typeof window.innerWidth === 'number') return window.innerWidth <= MOBILE_LIST_VIEW_MAX_WIDTH;
    return false;
}

function _setViewModeStorageScope(runtimeMode) {
    var nextScope = runtimeMode === 'demo' ? 'demo' : 'default';
    if (_viewModeStorageScope === nextScope) return;
    _viewModeStorageScope = nextScope;
    userPreferredViewMode = _loadSavedViewMode();
    currentViewMode = _resolveViewMode(lastDevices.length);
    _applyViewModeButtons(currentViewMode);
}

function _applyDemoScreenshotDefaults() {
    if (_runtimeMode !== 'demo' || _demoScreenshotDefaultsApplied) return;
    _demoScreenshotDefaultsApplied = true;

    var configSection = document.querySelector('.config-section');
    if (configSection) configSection.open = true;
    switchConfigTab('devices');

    var diagSection = document.getElementById('diag-details');
    if (diagSection) diagSection.open = false;

    var logsSection = document.querySelector('.logs-section');
    if (logsSection) logsSection.open = false;
}

function _getAutomaticViewMode(deviceCount) {
    return 'list';
}

function _resolveViewMode(deviceCount) {
    if (_isMobileListViewForced()) return 'list';
    return userPreferredViewMode || _getAutomaticViewMode(deviceCount);
}

function _applyViewModeButtons(mode) {
    var gridBtn = document.getElementById('view-grid-btn');
    var listBtn = document.getElementById('view-list-btn');
    var mobileForced = _isMobileListViewForced();
    if (gridBtn) {
        var gridActive = !mobileForced && mode !== 'list';
        gridBtn.disabled = mobileForced;
        gridBtn.classList.toggle('active', gridActive);
        gridBtn.setAttribute('aria-pressed', gridActive ? 'true' : 'false');
        gridBtn.setAttribute('aria-disabled', mobileForced ? 'true' : 'false');
    }
    if (listBtn) {
        listBtn.classList.toggle('active', mode === 'list');
        listBtn.setAttribute('aria-pressed', mode === 'list' ? 'true' : 'false');
    }
}

function _syncViewModeForDeviceCount(deviceCount) {
    currentViewMode = _resolveViewMode(deviceCount);
    _applyViewModeButtons(currentViewMode);
}

function _syncViewModeForViewport() {
    var nextMode = _resolveViewMode(lastDevices.length);
    var changed = nextMode !== currentViewMode;
    currentViewMode = nextMode;
    _applyViewModeButtons(currentViewMode);
    if (changed && lastDevices.length) renderDevicesView();
}

function _settingsIconHtml() {
    return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19.14 12.94c.04-.31.06-.62.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.03 7.03 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.58.23-1.13.54-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.7 8.84a.5.5 0 0 0 .12.64l2.03 1.58c-.04.31-.06.62-.06.94s.02.63.06.94L2.82 14.52a.5.5 0 0 0-.12.64l1.92 3.32c.13.23.4.32.64.22l2.39-.96c.5.4 1.05.72 1.63.94l.36 2.54c.04.24.25.42.5.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.58-.23 1.13-.54 1.63-.94l2.39.96c.24.1.51.01.64-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7Z"/></svg>';
}

function _findBtConfigWrapByIdentity(playerName, mac) {
    var targetMac = _normalizeDeviceMac(mac);
    var targetName = _normalizeDeviceName(playerName);
    var wraps = document.querySelectorAll('#bt-devices-table .bt-device-wrap');
    for (var i = 0; i < wraps.length; i++) {
        var wrap = wraps[i];
        if (targetMac && wrap.dataset.deviceMac === targetMac) return wrap;
        if (!targetName) continue;
        if (wrap.dataset.deviceName === targetName) return wrap;
        if (_deviceNamesMatch(wrap.dataset.deviceName, targetName) || _deviceNamesMatch(targetName, wrap.dataset.deviceName)) {
            return wrap;
        }
    }
    return null;
}

function _findBtConfigWrap(dev) {
    return _findBtConfigWrapByIdentity(dev && dev.player_name, dev && (dev.bluetooth_mac || dev.mac));
}

function _setBtConfigWrapEnabledState(wrap, enabled) {
    if (!wrap) return false;
    var enabledCb = wrap.querySelector('.bt-enabled');
    if (!enabledCb) return false;
    enabledCb.checked = enabled !== false;
    wrap.classList.toggle('disabled', !enabledCb.checked);
    return true;
}

function _highlightBtConfigWrap(wrap) {
    if (!wrap) return;
    document.querySelectorAll('#bt-devices-table .bt-device-wrap.settings-highlight').forEach(function(node) {
        node.classList.remove('settings-highlight');
    });
    if (_deviceSettingsHighlightTimer) {
        clearTimeout(_deviceSettingsHighlightTimer);
        _deviceSettingsHighlightTimer = null;
    }
    wrap.classList.add('settings-highlight');
    wrap.scrollIntoView({behavior: 'smooth', block: 'center'});
    var nameInput = wrap.querySelector('.bt-name');
    if (nameInput) nameInput.focus({preventScroll: true});
    _deviceSettingsHighlightTimer = setTimeout(function() {
        wrap.classList.remove('settings-highlight');
    }, 2600);
}

function _closeBtDeviceActionMenu(node) {
    var menu = node && node.closest ? node.closest('.bt-device-action-menu') : null;
    if (menu) menu.open = false;
}

function _highlightPairedDeviceRowByMac(mac) {
    var targetMac = _normalizeDeviceMac(mac);
    if (!targetMac) return;
    document.querySelectorAll('#paired-list .scan-result-item.paired-device-highlight').forEach(function(node) {
        node.classList.remove('paired-device-highlight');
    });
    var row = document.querySelector('#paired-list .scan-result-item[data-paired-mac="' + targetMac + '"]');
    if (!row) return;
    row.classList.add('paired-device-highlight');
    row.scrollIntoView({behavior: 'smooth', block: 'center'});
    setTimeout(function() {
        row.classList.remove('paired-device-highlight');
    }, 2600);
}

function _afterBluetoothAddToFleet(name, mac) {
    showToast('Added to Device fleet', 'success');
    _openConfigPanel('devices', 'config-panel-devices', 'start');
    setTimeout(function() {
        var wrap = _findBtConfigWrapByIdentity(name, mac);
        if (wrap) {
            _highlightBtConfigWrap(wrap);
            return;
        }
        var panel = document.getElementById('config-panel-devices');
        if (panel) _highlightConfigTarget(panel);
    }, 180);
}

function openDeviceSettings(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev || dev.enabled === false) return;
    _openConfigPanel('devices', 'config-panel-devices', 'start');
    setTimeout(function() {
        var target = _findBtConfigWrap(dev);
        if (!target) {
            showToast('Device row not found in Configuration → Devices', 'error');
            return;
        }
        _highlightBtConfigWrap(target);
    }, 180);
}

function _isDeviceDisabled(dev) {
    return !!(dev && dev.enabled === false);
}

function _mergeDisabledDeviceState(devices, disabledDevices) {
    if (!Array.isArray(devices) || !devices.length || !Array.isArray(disabledDevices) || !disabledDevices.length) {
        return devices;
    }
    var disabledByName = Object.create(null);
    var disabledByMac = Object.create(null);
    disabledDevices.forEach(function(entry) {
        if (!entry || entry.enabled !== false) return;
        var name = entry.player_name ? String(entry.player_name) : '';
        var mac = entry.bluetooth_mac || entry.mac || '';
        if (name) disabledByName[name] = true;
        if (mac) disabledByMac[String(mac).toUpperCase()] = true;
    });
    return devices.map(function(dev) {
        if (!dev) return dev;
        var name = dev.player_name ? String(dev.player_name) : '';
        var mac = dev.bluetooth_mac || dev.mac || '';
        if (disabledByName[name] || (mac && disabledByMac[String(mac).toUpperCase()])) {
            return Object.assign({}, dev, {enabled: false});
        }
        return dev;
    });
}

function _getMaGroupSettingsUrl(dev) {
    var groupId = '';
    if (dev && dev.ma_syncgroup_id) groupId = String(dev.ma_syncgroup_id);
    else if (dev && dev.group_id && String(dev.group_id).indexOf('syncgroup_') === 0) groupId = String(dev.group_id);
    var maUiUrl = _normalizeExternalUrlBase(lastMaUiUrl || _getConfiguredMaUiUrl() || lastMaWebUrl || '');
    if (!groupId || !maUiUrl) return '';
    return maUiUrl + '/#/settings/editplayer/' + encodeURIComponent(groupId);
}

function openDeviceGroupSettings(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev || !dev.group_id) {
        showToast('This device is not part of a Music Assistant group', 'error');
        return;
    }
    var url = _getMaGroupSettingsUrl(dev);
    if (!url) {
        showToast('Music Assistant web URL is not available yet', 'error');
        return;
    }
    _openExternalUrlInNewTab(url);
}

function _openExternalUrlInNewTab(url) {
    if (!url) return false;
    var link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    return true;
}

function _followLinkInNewTab(event, link) {
    if (event) event.preventDefault();
    if (!link || !link.href) return false;
    return _openExternalUrlInNewTab(link.href);
}

function _trashIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7zm3-4h6l1 2h4v2H4V5h4l1-2z"></path></svg>';
}

function _maIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 240 240" fill="currentColor" aria-hidden="true"><path d="M109.394 4.38C115.242-1.46 124.788-1.46 130.606 4.38L229.394 103.27C235.242 109.11 240 120.64 240 128.91V219.02L239.995 219.37C239.789 227.46 233.114 234 225 234H15C6.758 234 0 227.22 0 218.99V128.88C0 120.61 4.788 109.08 10.606 103.24L109.394 4.38ZM36 120C31.582 120 28 123.58 28 128V206H44V128C44 123.58 40.418 120 36 120ZM68 120C63.582 120 60 123.58 60 128V206H76V128C76 123.58 72.418 120 68 120ZM100 120C95.582 120 92 123.58 92 128V206H108V128C108 123.58 104.418 120 100 120ZM158.393 120.43C154.2 119.03 149.671 121.3 148.275 125.49L121.479 206H138.342L163.456 130.54C164.851 126.35 162.584 121.82 158.393 120.43ZM188.708 125.49C187.313 121.3 182.783 119.03 178.591 120.43C174.399 121.82 172.131 126.35 173.526 130.54L198.642 206H215.504L188.708 125.49Z"/></svg>';
}

function _getSyncStatusMeta(dev, i) {
    var currCount = dev.reanchor_count || 0;
    var currAt = dev.last_reanchor_at || '';
    if (!dev.playing) {
        delete reanchorShownAt[i];
        lastReanchorCount[i] = currCount;
        lastReanchorAt[i] = currAt;
        return {
            visible: false,
            text: '',
            toneClass: _deviceStatusToneClass('neutral'),
            dotClass: _deviceStatusDotClass('neutral', false),
            title: 'Synchronization status',
            detailText: '',
            detailToneClass: _deviceStatusToneClass('neutral'),
            detailTitle: '',
            detailIndicatorKind: '',
        };
    }

    if (dev.reanchoring) {
        if (!reanchorShownAt[i]) {
            reanchorShownAt[i] = Date.now();
        }
        lastReanchorCount[i] = currCount;
        lastReanchorAt[i] = currAt;
        var reanchorParts = [];
        if (dev.last_sync_error_ms != null) {
            reanchorParts.push('\u0394' + dev.last_sync_error_ms.toFixed(0) + ' ms');
        }
        if (currCount) {
            reanchorParts.push(currCount + 'x');
        }
        return {
            visible: true,
            text: 'Re-anchoring',
            indicatorKind: 'anchor',
            toneClass: _deviceStatusToneClass('warning'),
            dotClass: _deviceStatusDotClass('warning', true),
            title: 'Re-anchoring stream timing',
            detailText: reanchorParts.join(' · '),
            detailToneClass: _deviceStatusToneClass('warning'),
            detailTitle: reanchorParts.length ? 'Current sync correction' : '',
            detailIndicatorKind: reanchorParts.length ? 'anchor' : '',
        };
    }

    var countIncreased = lastReanchorCount[i] !== undefined && currCount > lastReanchorCount[i];
    var tsChanged = lastReanchorAt[i] !== undefined && currAt && currAt !== lastReanchorAt[i];
    if (countIncreased || tsChanged) {
        reanchorShownAt[i] = Date.now();
    }
    lastReanchorCount[i] = currCount;
    lastReanchorAt[i] = currAt;

    var warningDuration = Math.max(Math.abs(dev.static_delay_ms || 0), 5000);
    var shownAt = reanchorShownAt[i];
    if (shownAt && (Date.now() - shownAt) < warningDuration) {
        return {
            visible: true,
            text: 'Re-anchoring',
            indicatorKind: 'anchor',
            toneClass: _deviceStatusToneClass('warning'),
            dotClass: _deviceStatusDotClass('warning', true),
            title: 'Re-anchoring stream timing',
            detailText: dev.last_sync_error_ms != null ? '\u0394' + dev.last_sync_error_ms.toFixed(0) + ' ms' : '',
            detailToneClass: _deviceStatusToneClass('warning'),
            detailTitle: 'Current sync correction',
            detailIndicatorKind: dev.last_sync_error_ms != null ? 'anchor' : '',
        };
    }

    delete reanchorShownAt[i];
    var detailText = currCount ? String(currCount) : '';
    var detailTone = currCount > 100 ? 'error' : currCount > 10 ? 'warning' : currCount > 0 ? 'success' : 'neutral';
    return {
        visible: true,
        text: 'Sync',
        indicatorKind: 'check',
        toneClass: _deviceStatusToneClass('success'),
        dotClass: _deviceStatusDotClass('success', false),
        title: 'Synchronization healthy',
        detailText: detailText,
        detailToneClass: _deviceStatusToneClass(detailTone),
        detailTitle: detailText ? 'Re-anchor count' : '',
        detailIndicatorKind: detailText ? 'anchor' : '',
    };
}

function _getSyncDetailBadgeInnerHtml(syncMeta) {
    var detailText = syncMeta && syncMeta.detailText ? syncMeta.detailText : '';
    if (!detailText) return '';
    var indicatorKind = syncMeta && syncMeta.detailIndicatorKind;
    if (!indicatorKind) return '<span class="meta-badge-label">' + escHtml(detailText) + '</span>';
    return _renderBadgeIndicatorHtml(indicatorKind, {pulse: false}) +
        '<span class="meta-badge-label">' + escHtml(detailText) + '</span>';
}

function _getBatteryBadgeMeta(level) {
    if (level == null) return {visible: false};
    var bl = Math.max(0, Math.min(100, Math.round(Number(level))));
    var tone = bl <= 15 ? 'error' : bl <= 25 ? 'warning' : 'success';
    var summary = bl <= 15 ? 'Low battery' : bl <= 25 ? 'Battery running low' : 'Battery level normal';
    return {
        visible: true,
        level: bl,
        tone: tone,
        toneClass: _deviceStatusToneClass(tone),
        stateMeta: {
            key: 'battery',
            level: bl,
            pulse: false,
            summary: summary,
        },
        title: 'Battery: ' + bl + '% — ' + summary,
        html: _renderBadgeIndicatorHtml('battery', {key: 'battery', level: bl, pulse: false}),
    };
}

function _joinClassNames(parts) {
    return (parts || []).filter(Boolean).join(' ');
}

function _getStatusBadgeMeta(dev) {
    if (dev) return getDeviceDisplayStatusMeta(dev);
    return {
        key: 'idle',
        label: '-',
        summary: 'Device status',
        badgeToneClass: _deviceStatusToneClass('neutral'),
        pulse: false,
    };
}

function _getStatusBadgeRenderData(dev, className, labelClassName) {
    var meta = _getStatusBadgeMeta(dev);
    return {
        meta: meta,
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', meta.badgeToneClass]),
        title: meta.summary ? 'Device status — ' + meta.summary : 'Device status',
        innerHtml: _renderBadgeIndicatorHtml('status', meta) +
            '<span class="' + escHtmlAttr(labelClassName || 'meta-badge-label') + '">' + escHtml(meta.label) + '</span>',
    };
}

function _renderDeviceStatusBadgeHtml(dev, className, labelClassName, id) {
    var renderData = _getStatusBadgeRenderData(dev, className, labelClassName);
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _getReleaseBadgeRenderData(releaseMeta, className) {
    if (!releaseMeta || !releaseMeta.visible) return null;
    return {
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', releaseMeta.toneClass]),
        title: releaseMeta.title,
        innerHtml: _getReleaseBadgeInnerHtml(releaseMeta),
    };
}

function _renderReleaseBadgeHtml(releaseMeta, className, id) {
    var renderData = _getReleaseBadgeRenderData(releaseMeta, className);
    if (!renderData) return '';
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _getGroupBadgeRenderData(dev, i, className) {
    var meta = _getGroupBadgeMeta(dev);
    var stateMeta = _getGroupBadgeStateMeta(dev, meta);
    return {
        meta: meta,
        className: _joinClassNames([
            className,
            'meta-badge',
            'meta-badge-link',
            'group-badge-unified',
            stateMeta.toneClass,
            meta.clickable ? 'meta-badge-interactive group-link-badge' : '',
            meta.isEmpty ? 'empty' : ''
        ]),
        title: meta.title,
        innerHtml: _renderBadgeIndicatorHtml('chain', stateMeta) +
            '<span class="group-badge-label meta-badge-label">' + escHtml(meta.displayLabel) + '</span>',
        disabled: !meta.clickable,
        ariaLabel: meta.clickable ? 'Open Music Assistant group settings for ' + meta.label : '',
        onclick: meta.clickable ? 'event.stopPropagation();openDeviceGroupSettings(' + i + ')' : '',
    };
}

function _getAdapterBadgeRenderData(dev, i, className) {
    var info = _getAdapterDisplayInfo(dev);
    var stateMeta = _getBtBadgeStateMeta(dev, info);
    return {
        info: info,
        stateMeta: stateMeta,
        className: _joinClassNames([
            className,
            'meta-badge',
            'meta-badge-link',
            'adapter-link-badge',
            stateMeta.toneClass,
            info.empty ? 'empty' : 'meta-badge-interactive'
        ]),
        title: info.empty
            ? (stateMeta.summary || info.title)
            : (stateMeta.summary ? stateMeta.summary + ' · ' : '') + 'Open Bluetooth adapter settings · ' + info.title,
        innerHtml: _renderBadgeIndicatorHtml('bt', stateMeta) +
            '<span class="adapter-badge-label meta-badge-label">' + escHtml(info.label) + '</span>',
        disabled: info.empty,
        onclick: info.empty ? '' : 'event.stopPropagation();openDeviceAdapterSettings(' + i + ')',
    };
}

function _getServiceBadgeRenderData(dev, className) {
    var stateMeta = _getServiceBadgeStateMeta(dev);
    var maConnected = !!((dev && dev.ma_now_playing) || {}).connected;
    return {
        stateMeta: stateMeta,
        className: _joinClassNames([
            className,
            'meta-badge',
            'meta-badge-service',
            'service-chip-badge',
            'ma-service-badge',
            stateMeta.toneClass,
        ]),
        title: stateMeta.summary || 'Music Assistant service',
        innerHtml: _renderBadgeIndicatorHtml('ma', stateMeta) +
            (maConnected ? '<span class="ma-chip-tag">API</span>' : ''),
    };
}

function _renderServiceBadgeHtml(dev, className, id) {
    var renderData = _getServiceBadgeRenderData(dev, className);
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _getSyncBadgeRenderData(dev, i, className, detailClassName) {
    var syncMeta = _getSyncStatusMeta(dev, i);
    return {
        meta: syncMeta,
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', syncMeta.toneClass]),
        title: syncMeta.title || 'Synchronization status',
        innerHtml: _renderBadgeIndicatorHtml(syncMeta.indicatorKind || 'chain', syncMeta) +
            '<span class="meta-badge-label">' + escHtml(syncMeta.text || '') + '</span>',
        visible: !!syncMeta.visible,
        detailClassName: _joinClassNames([detailClassName, 'meta-badge', 'meta-badge-status', syncMeta.detailToneClass]),
        detailTitle: syncMeta.detailTitle || 'Sync details',
        detailInnerHtml: _getSyncDetailBadgeInnerHtml(syncMeta),
        detailVisible: !!syncMeta.visible && !!syncMeta.detailText,
    };
}

function _renderSyncBadgeHtml(dev, i, className, id) {
    var renderData = _getSyncBadgeRenderData(dev, i, className, '');
    if (!renderData.visible) return '';
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _renderSyncDetailBadgeHtml(dev, i, className, id) {
    var renderData = _getSyncBadgeRenderData(dev, i, '', className);
    if (!renderData.detailVisible) return '';
    return '<span class="' + renderData.detailClassName + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.detailTitle) + '">' +
        renderData.detailInnerHtml +
    '</span>';
}

function _getRoomBadgeRenderData(dev, className) {
    var roomName = String((dev && dev.room_name) || '').trim();
    var roomId = String((dev && dev.room_id) || '').trim();
    if (!roomName && !roomId) return null;
    var label = roomName || roomId;
    var source = String((dev && dev.room_source) || '').trim();
    var toneClass = source === 'ha_area' ? _deviceStatusToneClass('info') : _deviceStatusToneClass('neutral');
    return {
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', 'room-chip-badge', toneClass]),
        title: source === 'ha_area'
            ? 'Room mapped from Home Assistant area' + (roomId ? ' · ' + roomId : '')
            : 'Room assignment' + (roomId ? ' · ' + roomId : ''),
        innerHtml: _renderBadgeIndicatorHtml('tag', {key: 'room', level: label, pulse: false}) +
            '<span class="meta-badge-label">' + escHtml(label) + '</span>',
    };
}

function _renderRoomBadgeHtml(dev, className, id) {
    var renderData = _getRoomBadgeRenderData(dev, className);
    if (!renderData) return '';
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _getTransferReadinessBadgeRenderData(dev, className) {
    var readiness = (dev && dev.transfer_readiness) || {};
    if (!readiness || typeof readiness !== 'object') return null;
    var ready = !!readiness.ready;
    var reason = String(readiness.reason || '').trim();
    var label = ready ? 'Transfer ready' : 'Not ready';
    var toneClass = ready ? _deviceStatusToneClass('success') : _deviceStatusToneClass(readiness.severity || 'warning');
    var title = ready
        ? 'Ready for room handoff'
        : 'Room handoff not ready' + (reason ? ' · ' + reason.replace(/_/g, ' ') : '');
    return {
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', 'transfer-chip-badge', toneClass]),
        title: title,
        innerHtml: _renderBadgeIndicatorHtml('status', {key: ready ? 'ready' : 'blocked', level: ready ? 'ok' : reason, pulse: false}) +
            '<span class="meta-badge-label">' + escHtml(label) + '</span>',
    };
}

function _renderTransferReadinessBadgeHtml(dev, className, id) {
    var renderData = _getTransferReadinessBadgeRenderData(dev, className);
    if (!renderData) return '';
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _getBatteryBadgeRenderData(level, className) {
    var batteryMeta = _getBatteryBadgeMeta(level);
    return {
        meta: batteryMeta,
        className: _joinClassNames([className, 'meta-badge', 'meta-badge-status', batteryMeta.toneClass]),
        title: batteryMeta.title || '',
        innerHtml: batteryMeta.html || '',
        visible: !!batteryMeta.visible,
    };
}

function _renderBatteryBadgeHtml(level, className, id) {
    var renderData = _getBatteryBadgeRenderData(level, className);
    if (!renderData.visible) return '';
    return '<span class="' + renderData.className + '"' +
        (id ? ' id="' + id + '"' : '') +
        ' title="' + escHtmlAttr(renderData.title) + '">' +
        renderData.innerHtml +
    '</span>';
}

function _normalizeBadgeToneClass(tone) {
    if (!tone) return _deviceStatusToneClass('neutral');
    return tone.indexOf('is-') === 0 ? tone : _deviceStatusToneClass(tone);
}

function _renderMetaStatusBadgeHtml(options) {
    var opts = options || {};
    var parts = [];
    if (opts.leadingHtml) {
        parts.push(opts.leadingHtml);
    } else if (opts.indicatorKind) {
        parts.push(_renderBadgeIndicatorHtml(opts.indicatorKind, opts.stateMeta || {pulse: !!opts.pulse}));
    }
    if (opts.label) {
        parts.push('<span class="' + escHtmlAttr(opts.labelClassName || 'meta-badge-label') + '">' + escHtml(opts.label) + '</span>');
    }
    if (opts.hint) {
        parts.push('<span class="' + escHtmlAttr(opts.hintClassName || 'scan-status-hint') + '">' + escHtml(opts.hint) + '</span>');
    }
    return '<span class="' + _joinClassNames([
        opts.className,
        'meta-badge',
        'meta-badge-status',
        _normalizeBadgeToneClass(opts.tone),
    ]) + '"' +
        (opts.id ? ' id="' + opts.id + '"' : '') +
        (opts.title ? ' title="' + escHtmlAttr(opts.title) + '"' : '') +
        '>' +
        parts.join('') +
    '</span>';
}

function _renderBtRuntimeBadgeHtml(runtime, className) {
    var meta = runtime ? getDeviceDisplayStatusMeta(runtime) : {
        key: 'not-seen',
        label: 'Not seen',
        summary: 'Device has not been seen in runtime status yet',
        badgeToneClass: _deviceStatusToneClass('neutral'),
        pulse: false,
    };
    return _renderMetaStatusBadgeHtml({
        className: className,
        tone: meta.badgeToneClass,
        title: meta.summary ? 'Runtime status — ' + meta.summary : 'Runtime status',
        indicatorKind: 'status',
        stateMeta: meta,
        label: meta.label,
    });
}

function _renderScanStatusBadgeHtml(label, tone, hint) {
    return _renderMetaStatusBadgeHtml({
        className: 'scan-status-pill',
        tone: tone || 'neutral',
        label: label,
        hint: hint || '',
        title: hint ? label + ' · ' + hint : label,
    });
}

function _getListCollapsedBadgesHtml(dev, i) {
    var badges = [];
    var releaseMeta = getDeviceReleaseMeta(dev);
    if (releaseMeta.visible) {
        badges.push(_renderReleaseBadgeHtml(releaseMeta, 'chip list-inline-badge'));
    }
    badges.push(_renderServiceBadgeHtml(dev, 'chip list-inline-badge'));
    badges.push(_renderSyncBadgeHtml(dev, i, 'chip list-inline-badge list-sync-chip'));
    badges.push(_renderSyncDetailBadgeHtml(dev, i, 'chip list-inline-badge list-sync-detail-chip'));
    badges.push(_renderBatteryBadgeHtml(dev.battery_level, 'chip list-inline-badge list-battery-chip'));

    return badges.length ? '<span class="list-inline-badges">' + badges.join('') + '</span>' : '';
}

function _groupBadgeIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z"/></svg>';
}

function _chainIconSvg(className) {
    return _groupBadgeIconSvg(className);
}

function _anchorIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2a2.5 2.5 0 1 0 1 4.79V9H9v2h4v6.39c-1.64-.23-3.13-1.09-4.1-2.39l-1.6 1.2A7.03 7.03 0 0 0 13 19.43V22h2v-2.57a7.03 7.03 0 0 0 5.7-3.23l-1.6-1.2A5 5 0 0 1 15 17.39V11h4V9h-4V6.79A2.5 2.5 0 0 0 12 2Z"/></svg>';
}

function _checkIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>';
}

function _releaseIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M9 7.5l1.6-1.6a3 3 0 0 1 4.24 0l1.26 1.26a3 3 0 0 1 0 4.24L14.5 13"/>' +
        '<path d="M15 16.5l-1.6 1.6a3 3 0 0 1-4.24 0L7.9 16.84a3 3 0 0 1 0-4.24L9.5 11"/>' +
        '<path d="M4 20L20 4"/>' +
    '</svg>';
}

function _noSinkIconSvg(className) {
    var cls = className ? ' class="' + className + '"' : '';
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M5 10h3l4-3v10l-4-3H5z"/>' +
        '<path d="M4 20L20 4"/>' +
    '</svg>';
}

function _batteryIconSvg(level, className) {
    var cls = className ? ' class="' + className + '"' : '';
    var bl = Math.max(0, Math.min(100, Math.round(Number(level) || 0)));
    var fillWidth = Math.max(0, Math.min(9.5, Math.round((bl / 100) * 10)));
    return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<rect x="3" y="7" width="16" height="10" rx="2"/>' +
        '<rect x="20" y="10" width="1.5" height="4" rx="0.75" fill="currentColor" stroke="none"/>' +
        (fillWidth > 0
            ? '<rect x="5.2" y="9.2" width="' + fillWidth + '" height="5.6" rx="1.1" fill="currentColor" stroke="none"/>'
            : '') +
    '</svg>';
}

function _findGroupSummaryForDevice(dev) {
    if (!dev || !dev.group_id) return null;
    var devName = dev.player_name || '';
    return (lastGroups || []).find(function(group) {
        if (dev.ma_syncgroup_id && group.ma_syncgroup_id === dev.ma_syncgroup_id) return true;
        if (group.group_id && dev.group_id && group.group_id === dev.group_id) return true;
        return group.members && group.members.some(function(member) { return member.player_name === devName; });
    }) || null;
}

function _groupMemberStatusIcon(member) {
    if (!member) return '•';
    if (member.playing) return '▶';
    if (!member.server_connected) return '✕';
    if (!member.bluetooth_connected) return '⚡';
    return '✓';
}

function _getGroupBadgeMeta(dev) {
    var rawLabel = dev && (dev.group_name || dev.group_id) ? String(dev.group_name || dev.group_id) : '';
    if (!rawLabel) {
        return {
            label: 'No group',
            displayLabel: 'No group',
            textLabel: 'No group',
            externalCount: 0,
            clickable: false,
            isEmpty: true,
            title: 'No Music Assistant group',
            iconHtml: '',
            groupUrl: '',
        };
    }

    var grp = _findGroupSummaryForDevice(dev);
    var externalCount = grp ? (grp.external_count || 0) : 0;
    var suffix = externalCount > 0 ? ' +' + externalCount : '';
    var displayLabel = rawLabel + suffix;
    var titleLines = [rawLabel];
    if (grp && grp.members && grp.members.length > 0) {
        titleLines.push('───');
        grp.members.forEach(function(member) {
            titleLines.push(_groupMemberStatusIcon(member) + ' ' + (member.player_name || '?'));
        });
    }
    if (grp && grp.external_members && grp.external_members.length > 0) {
        if (!(grp.members && grp.members.length > 0)) titleLines.push('───');
        grp.external_members.forEach(function(member) {
            var icon = member.available === false ? '⊘' : '[ext]';
            titleLines.push(icon + ' ' + member.name);
        });
    }

    var groupUrl = _getMaGroupSettingsUrl(dev);
    if (groupUrl) {
        titleLines.push('───');
        titleLines.push('Open Music Assistant group settings');
    }

    return {
        label: rawLabel,
        displayLabel: displayLabel,
        textLabel: displayLabel,
        externalCount: externalCount,
        clickable: !!groupUrl,
        isEmpty: false,
        title: titleLines.join('\n'),
        iconHtml: _groupBadgeIconSvg('group-badge-icon'),
        groupUrl: groupUrl,
        summary: grp,
    };
}

function _groupBadgeHtml(dev, i, className) {
    var renderData = _getGroupBadgeRenderData(dev, i, className);
    if (!renderData.meta.clickable) {
        return '<span class="' + renderData.className + '" title="' + escHtmlAttr(renderData.title) + '">' + renderData.innerHtml + '</span>';
    }
    return '<button type="button" class="' + renderData.className + '" title="' +
        escHtmlAttr(renderData.title) + '" aria-label="' + escHtmlAttr(renderData.ariaLabel) + '" onclick="' + renderData.onclick + '">' +
        renderData.innerHtml +
    '</button>';
}

function _findAdapterRecord(adapterId, adapterMac) {
    var normalizedMac = _normalizeDeviceMac(adapterMac);
    for (var i = 0; i < btAdapters.length; i++) {
        var adapter = btAdapters[i];
        if (adapterId && adapter.id === adapterId) return adapter;
        if (normalizedMac && _normalizeDeviceMac(adapter.mac) === normalizedMac) return adapter;
    }
    return null;
}

function _getAdapterDisplayInfo(dev) {
    var adapterId = dev && dev.bluetooth_adapter_hci ? dev.bluetooth_adapter_hci : '';
    var detectedName = dev && dev.bluetooth_adapter ? dev.bluetooth_adapter : '';
    var adapter = _findAdapterRecord(adapterId, '');
    var customName = adapter && adapter.customName ? adapter.customName : '';
    var detectedLabel = adapter && adapter.detectedName ? adapter.detectedName : detectedName;
    var label = customName || detectedLabel || adapterId || 'No adapter';
    var titleParts = [];
    if (customName) titleParts.push(customName);
    if (adapterId && adapterId !== customName) titleParts.push(adapterId);
    if (detectedLabel && detectedLabel !== customName && detectedLabel !== adapterId) {
        titleParts.push('Detected as ' + detectedLabel);
    }
    if (adapter && adapter.mac) titleParts.push(adapter.mac);
    return {
        id: adapterId,
        mac: adapter && adapter.mac ? adapter.mac : '',
        label: label,
        title: titleParts.join(' · ') || label,
        empty: !adapterId && !detectedLabel && !customName,
    };
}

function _adapterBadgeHtml(dev, i, className) {
    var renderData = _getAdapterBadgeRenderData(dev, i, className);
    if (renderData.info.empty) {
        return '<span class="' + renderData.className + '" title="' + escHtmlAttr(renderData.title) + '">' +
            renderData.innerHtml +
        '</span>';
    }
    return '<button type="button" class="' + renderData.className + '" title="' + escHtmlAttr(renderData.title) + '"' +
        ' onclick="' + renderData.onclick + '">' +
        renderData.innerHtml +
    '</button>';
}

function _getListStatusBadgeHtml(dev) {
    return _renderDeviceStatusBadgeHtml(dev, 'chip');
}

function _findAdapterConfigRow(adapterId, adapterMac) {
    var normalizedMac = _normalizeDeviceMac(adapterMac);
    var rows = document.querySelectorAll('#adapters-table .adapter-row');
    for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        if (adapterId && row.dataset.adapterId === adapterId) return row;
        if (normalizedMac && _normalizeDeviceMac(row.dataset.adapterMac) === normalizedMac) return row;
    }
    return null;
}

function _highlightAdapterConfigRow(row) {
    if (!row) return;
    document.querySelectorAll('#adapters-table .adapter-row.settings-highlight').forEach(function(node) {
        node.classList.remove('settings-highlight');
    });
    if (_adapterSettingsHighlightTimer) {
        clearTimeout(_adapterSettingsHighlightTimer);
        _adapterSettingsHighlightTimer = null;
    }
    row.classList.add('settings-highlight');
    row.scrollIntoView({behavior: 'smooth', block: 'center'});
    var nameInput = row.querySelector('.adp-name');
    if (nameInput) nameInput.focus({preventScroll: true});
    _adapterSettingsHighlightTimer = setTimeout(function() {
        row.classList.remove('settings-highlight');
    }, 2600);
}

function openAdapterSettings(adapterId, adapterMac) {
    if (!adapterId && !adapterMac) {
        showToast('No Bluetooth adapter assigned for this device', 'error');
        return;
    }
    _openConfigPanel('bluetooth', 'config-bluetooth-adapters-card', 'start');
    setTimeout(function() {
        var target = _findAdapterConfigRow(adapterId, adapterMac);
        if (!target) {
            showToast('Adapter row not found in Configuration → Bluetooth', 'error');
            return;
        }
        _highlightAdapterConfigRow(target);
    }, 180);
}

function openDeviceAdapterSettings(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
    var info = _getAdapterDisplayInfo(dev);
    openAdapterSettings(info.id, info.mac);
}

function _setActionButtonTone(btn, tone) {
    if (!btn) return;
    var baseClass = btn.classList.contains('list-action-btn') ? 'list-action-btn' : 'action-btn';
    btn.className = baseClass + (tone ? ' ' + tone : '');
}

function _actionButtonIconSvg(kind, className) {
    var cls = className ? ' class="' + className + '"' : '';
    if (kind === 'reconnect') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M21 12a9 9 0 0 1-15.36 6.36"/>' +
            '<path d="M3 12A9 9 0 0 1 18.36 5.64"/>' +
            '<path d="M3 16v-4h4"/>' +
            '<path d="M21 8v4h-4"/>' +
        '</svg>';
    }
    if (kind === 'release') return _releaseIconSvg(className);
    if (kind === 'standby') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z"/>' +
        '</svg>';
    }
    if (kind === 'disable') {
        return '<svg' + cls + ' viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<circle cx="12" cy="12" r="8"/>' +
            '<path d="M8.5 15.5l7-7"/>' +
        '</svg>';
    }
    return '';
}

function _actionButtonInnerHtml(kind, label) {
    return '<span class="action-btn-content">' +
        _actionButtonIconSvg(kind, 'action-btn-icon') +
        '<span class="action-btn-label">' + escHtml(label) + '</span>' +
    '</span>';
}

function _setReleaseActionButtonState(btn, mgmtEnabled) {
    if (!btn) return;
    _setActionButtonTone(btn, mgmtEnabled ? 'warn' : 'success');
    btn.innerHTML = _actionButtonInnerHtml('release', mgmtEnabled ? 'Release' : 'Reclaim');
    btn.title = mgmtEnabled
        ? 'Stop BT management for this device (it will stop auto-reconnecting)'
        : 'Resume BT management and auto-reconnect';
}

function _setStandbyActionButtonState(btn, isStandby) {
    if (!btn) return;
    _setActionButtonTone(btn, isStandby ? 'success' : 'warn');
    btn.innerHTML = _actionButtonInnerHtml(isStandby ? 'reconnect' : 'standby', isStandby ? 'Wake' : 'Standby');
    btn.title = isStandby
        ? 'Wake from standby — reconnect Bluetooth and resume audio'
        : 'Enter standby — disconnect Bluetooth to save speaker battery';
}

function _getVisibleDeviceEntries() {
    var entries = [];
    lastDevices.forEach(function(dev, index) {
        if (deviceMatchesFilters(dev)) entries.push({dev: dev, index: index});
    });
    return entries;
}

function _compareListValues(a, b, column) {
    if (column === 'status') {
        var statusWeight = function(dev) {
            if (dev.playing) return 4;
            if (dev.bluetooth_connected) return 3;
            if (dev.stopping) return 2.5;
            if (dev.reconnecting || dev.ma_reconnecting) return 2;
            if (dev.bt_management_enabled === false) return 1;
            return 0;
        };
        return statusWeight(a) - statusWeight(b);
    }
    if (column === 'adapter') {
        var adapterInfoA = _getAdapterDisplayInfo(a);
        var adapterInfoB = _getAdapterDisplayInfo(b);
        var adapterCompare = String(adapterInfoA.label || '').localeCompare(String(adapterInfoB.label || ''), undefined, {numeric: true, sensitivity: 'base'});
        if (adapterCompare !== 0) return adapterCompare;
        return String(adapterInfoA.id || adapterInfoA.mac || '').localeCompare(String(adapterInfoB.id || adapterInfoB.mac || ''), undefined, {numeric: true, sensitivity: 'base'});
    }
    if (column === 'group') return String(a.group_name || a.group_id || '').localeCompare(String(b.group_name || b.group_id || ''));
    if (column === 'volume') return (a.volume || 0) - (b.volume || 0);
    return String(a.player_name || '').localeCompare(String(b.player_name || ''));
}

function _sortVisibleEntries(entries) {
    if (currentViewMode !== 'list') return entries;
    return entries.slice().sort(function(a, b) {
        var result = _compareListValues(a.dev, b.dev, listSortState.column);
        if (result === 0) result = String(a.dev.player_name || '').localeCompare(String(b.dev.player_name || ''));
        return listSortState.direction === 'asc' ? result : -result;
    });
}

function _playPauseIconHtml(isPlaying) {
    return isPlaying
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
}

function _muteIconHtml(isMuted) {
    return isMuted
        ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>';
}

function _shouldShowEqualizer(dev) {
    var key = getUnifiedDeviceStatusMeta(dev).key;
    return !!(dev && dev.playing) || key === 'buffering';
}

function _getEqualizerStateClass(dev) {
    var key = getUnifiedDeviceStatusMeta(dev).key;
    if (!!dev.playing && !!dev.audio_streaming) return ' active';
    if (key === 'buffering') return ' stale';
    if (!!dev.playing) return ' stale';
    return '';
}

function _getEqualizerHtml(dev, extraClass, id) {
    var className = 'eq-bars' + _getEqualizerStateClass(dev) + (extraClass ? ' ' + extraClass : '');
    return '<div class="' + className + '"' + (id ? ' id="' + id + '"' : '') + (extraClass ? ' data-eq-extra="' + escHtmlAttr(extraClass) + '"' : '') + ' aria-hidden="true">' +
        '<div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div>' +
    '</div>';
}

function _getDeviceNowPlayingState(dev, i) {
    var safeDev = dev || {};
    var ma = safeDev.ma_now_playing || {};
    var deviceMaActive = !!(ma.connected && deviceHasSink(safeDev));
    var artist = _firstOfSlash((deviceMaActive ? (ma.artist || '') : '') || safeDev.current_artist || '');
    var track = _firstOfSlash((deviceMaActive ? (ma.track || '') : '') || safeDev.current_track || '');
    var album = _firstOfSlash((deviceMaActive ? (ma.album || '') : '') || safeDev.current_album || '');
    var artUrl = deviceMaActive ? (ma.image_url || '') : '';
    if (!artUrl && safeDev.artwork_url) artUrl = safeDev.artwork_url;
    return {
        ma: ma,
        deviceMaActive: deviceMaActive,
        track: track,
        artist: artist,
        album: album,
        artUrl: artUrl,
        hasTrack: !!(track || artist),
        titleText: track || 'Nothing playing',
        metaText: [artist, album].filter(Boolean).join(' · '),
        progress: _getDevicePlaybackProgressState(safeDev, i),
    };
}

function _renderNowPlayingArtworkHtml(i, mediaState, options) {
    var opts = options || {};
    var artUrl = _getSafeArtworkUrl((mediaState || {}).artUrl || '');
    var classes = _joinClassNames(['np-art', opts.containerClass, artUrl ? 'has-artwork-preview' : '']);
    var attrs = (artUrl || opts.persistent)
        ? ' role="button" tabindex="0" aria-expanded="false" onclick="toggleArtworkPreview(event, this)" onkeydown="onArtworkPreviewKeydown(event, this)"'
        : '';
    var imageClass = opts.imageClass || 'np-art-image';
    var previewClass = opts.previewClass || 'artwork-preview-popover';
    if (opts.persistent) {
        return '<div class="' + classes + '"' + attrs + '>' +
            '<img class="' + imageClass + '" id="' + escHtmlAttr(opts.imageId) + '" src="' + escHtmlAttr(artUrl) + '" alt=""' + (artUrl ? '' : ' style="display:none"') + '>' +
            '<img class="' + previewClass + '" id="' + escHtmlAttr(opts.previewId) + '" src="' + escHtmlAttr(artUrl) + '" alt="">' +
            '<svg viewBox="0 0 24 24" fill="currentColor" id="' + escHtmlAttr(opts.placeholderId) + '"' + (artUrl ? ' style="display:none"' : '') + '><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>' +
        '</div>';
    }
    if (artUrl) {
        return '<div class="' + classes + '"' + attrs + '>' +
            _renderArtworkThumbHtml(artUrl, imageClass, previewClass) +
        '</div>';
    }
    return '<div class="' + classes + '"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg></div>';
}

function _renderNowPlayingTextHtml(mediaState, options) {
    var opts = options || {};
    var titleTag = opts.titleTag || 'div';
    var metaTag = opts.metaTag || 'div';
    var preTitleHtml = opts.preTitleHtml || '';
    var postMetaHtml = opts.postMetaHtml || '';
    var titleHtml = '<' + titleTag + ' class="' + escHtmlAttr(opts.titleClass || '') + '"' +
        (opts.titleId ? ' id="' + escHtmlAttr(opts.titleId) + '"' : '') + '>' +
        escHtml((mediaState || {}).titleText || 'Nothing playing') +
    '</' + titleTag + '>';
    if (opts.titleEqHtml || opts.titleGroupClass) {
        titleHtml = '<div class="' + escHtmlAttr(opts.titleGroupClass || '') + '">' + titleHtml + (opts.titleEqHtml || '') + '</div>';
    }
    if (opts.titleRowClass) {
        titleHtml = '<div class="' + escHtmlAttr(opts.titleRowClass) + '">' + titleHtml + '</div>';
    }
    var metaText = opts.metaText !== undefined ? opts.metaText : ((mediaState || {}).metaText || '');
    var metaHtml = (metaText || opts.metaId)
        ? '<' + metaTag + ' class="' + escHtmlAttr(opts.metaClass || '') + '"' +
            (opts.metaId ? ' id="' + escHtmlAttr(opts.metaId) + '"' : '') + '>' +
            escHtml(metaText) +
          '</' + metaTag + '>'
        : '';
    var albumHtml = opts.showAlbumLine && (mediaState || {}).album
        ? '<div class="' + escHtmlAttr(opts.albumClass || '') + '">' + escHtml(mediaState.album) + '</div>'
        : '';
    return '<div class="' + escHtmlAttr(opts.containerClass || '') + '">' +
        preTitleHtml + titleHtml + metaHtml + postMetaHtml + albumHtml +
        '</div>';
}

function _renderNowPlayingInfoBadgeHtml(className, options) {
    var opts = options || {};
    return '<span class="' + _joinClassNames([className, 'chip', 'meta-badge', 'meta-badge-status', 'is-info', opts.placeholder ? 'is-placeholder' : '']) +
        '"' + (opts.placeholder ? ' aria-hidden="true"' : ' title="Current track information"') + '>' +
        '<span class="meta-badge-label">Now playing</span>' +
    '</span>';
}

function _getDeviceTransportState(dev, mediaState) {
    var safeDev = dev || {};
    var media = mediaState || _getDeviceNowPlayingState(safeDev, null);
    var ma = media.ma || {};
    var maMeta = _getMaSyncMeta(ma);
    var playPauseCapability = _getDeviceActionCapability(safeDev, 'play_pause');
    var volumeCapability = _getDeviceActionCapability(safeDev, 'volume');
    var muteCapability = _getDeviceActionCapability(safeDev, 'mute');
    var queueCapability = _getDeviceActionCapability(safeDev, 'queue_control');
    var hasSink = _capabilityAvailable(volumeCapability, deviceHasSink(safeDev));
    var canTransport = _capabilityAvailable(playPauseCapability, !!safeDev.server_connected);
    var nativeCmds = Array.isArray(safeDev.supported_commands) ? safeDev.supported_commands : [];
    var hasNativeTransport = nativeCmds.length > 0;
    var hasQueueControls = _capabilityAvailable(queueCapability, !!(safeDev.server_connected && (ma.connected || hasNativeTransport)));
    var queueUnavailableTitle = _capabilityBlockedReason(
        queueCapability,
        !safeDev.server_connected ? 'Sendspin not connected' : 'Music Assistant API not connected'
    );
    var pendingSummary = _getPendingMaSummary(maMeta);
    var queueActionPending = _isQueueTransportActionPending(maMeta);
    var shufflePending = _hasPendingMaAction(maMeta, 'shuffle');
    var repeatPending = _hasPendingMaAction(maMeta, 'repeat');
    // Use native shuffle/repeat as fallback when MA is unavailable
    var shuffleState = ma.shuffle != null ? !!ma.shuffle : (safeDev.shuffle != null ? !!safeDev.shuffle : false);
    var repeatState = ma.repeat || safeDev.repeat_mode || 'off';
    return {
        hasSink: hasSink,
        canTransport: canTransport,
        hasQueueControls: hasQueueControls,
        hasNativeTransport: hasNativeTransport,
        nativeCommands: nativeCmds,
        isPlaying: !!safeDev.playing,
        shuffle: shuffleState,
        repeat: repeatState,
        transportUnavailableTitle: _capabilityBlockedReason(playPauseCapability, canTransport ? '' : 'Sendspin not connected'),
        queueUnavailableTitle: queueUnavailableTitle,
        muteUnavailableTitle: _capabilityBlockedReason(
            muteCapability || volumeCapability,
            hasSink ? '' : 'Audio sink not configured'
        ),
        pendingSummary: pendingSummary,
        queueActionPending: queueActionPending,
        shufflePending: shufflePending,
        repeatPending: repeatPending,
        shuffleTitle: _buildQueueActionTitle(
            shuffleState ? 'Shuffle on — click to disable' : 'Shuffle off — click to enable',
            queueActionPending,
            hasQueueControls ? '' : queueUnavailableTitle,
            pendingSummary
        ),
        repeatTitle: _buildQueueActionTitle(
            'Repeat: ' + repeatState + ' — click to cycle',
            queueActionPending,
            hasQueueControls ? '' : queueUnavailableTitle,
            pendingSummary
        ),
    };
}

function _getMaQueueTargetId(dev) {
    if (!dev) return '';
    if (dev.ma_syncgroup_id) return String(dev.ma_syncgroup_id);
    if (dev.group_id) return String(dev.group_id);
    var ma = dev.ma_now_playing || {};
    if (ma.syncgroup_id) return String(ma.syncgroup_id);
    return '';
}

function _renderTransportButtonHtml(button) {
    return '<button type="button" class="' + button.className + '" id="' + button.id + '" onclick="' + button.onclick + '" title="' +
        escHtmlAttr(button.title) + '"' + (button.disabled ? ' disabled' : '') + (button.hidden ? ' style="display:none"' : '') + '>' +
        button.iconHtml +
    '</button>';
}

function _repeatIconHtml(mode) {
    if (mode === 'one') {
        return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
            '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>' +
            '<path d="M12.2 9.25h-1.35l-1 1.05v1.15l1-1.05h.35v4.35h1.3V9.25z"/>' +
        '</svg>';
    }
    return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>';
}

function _renderPlaybackTransportButtonsHtml(i, transportState, options) {
    var opts = options || {};
    var state = transportState || _getDeviceTransportState({}, null);
    var baseClass = opts.buttonBaseClass || 'icon-btn';
    var primaryClass = opts.primaryButtonClass || baseClass;
    var modeClass = opts.modeButtonClass || baseClass;
    var showPrevNext = state.hasQueueControls || opts.renderPrevNextWhenInactive;
    var showModeButtons = state.hasQueueControls || opts.renderModeButtonsWhenInactive;
    var buttons = [];
    var pushModeButton = function(kind, title, iconHtml, isActive) {
        if (!showModeButtons) return;
        var resolvedTitle = state.hasQueueControls ? title : (state.queueUnavailableTitle || title);
        var modeStateClass = '';
        if (kind === 'repeat' && state.hasQueueControls) {
            modeStateClass = state.repeat === 'one'
                ? 'repeat-one'
                : (state.repeat === 'all' ? 'repeat-all' : '');
        }
        buttons.push(_renderTransportButtonHtml({
            className: _joinClassNames([modeClass, state.hasQueueControls ? 'ma-ready' : '', isActive ? 'active' : '', modeStateClass]),
            id: 'dma-' + kind + '-' + i,
            onclick: kind === 'repeat' ? 'maCycleRepeat(' + i + ')' : 'maQueueCmd(\'' + escHtmlAttr(kind) + '\', undefined, ' + i + ')',
            title: resolvedTitle,
            iconHtml: iconHtml,
            disabled: (!state.hasQueueControls && !!opts.disableWhenInactive) || (!!state.hasQueueControls && !!state.queueActionPending),
            hidden: !state.hasQueueControls && !!opts.hideModeButtonsWhenInactive,
        }));
    };
    var pushPrevNextButton = function(kind, title, iconHtml) {
        if (!showPrevNext) return;
        var resolvedTitle = state.hasQueueControls ? title : (state.queueUnavailableTitle || title);
        buttons.push(_renderTransportButtonHtml({
            className: baseClass,
            id: 'dma-' + kind + '-' + i,
            onclick: 'maQueueCmd(\'' + escHtmlAttr(kind === 'prev' ? 'previous' : 'next') + '\', undefined, ' + i + ')',
            title: _buildQueueActionTitle(resolvedTitle, state.queueActionPending, !state.hasQueueControls ? state.queueUnavailableTitle : '', state.pendingSummary),
            iconHtml: iconHtml,
            disabled: (!state.hasQueueControls && !!opts.disableWhenInactive) || (!!state.hasQueueControls && !!state.queueActionPending),
            hidden: !state.hasQueueControls,
        }));
    };
    if (opts.modeFirst) {
        pushModeButton('shuffle', state.shuffleTitle, '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>', state.hasQueueControls && state.shuffle);
    }
    pushPrevNextButton('prev', opts.prevTitle || 'Previous', '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>');
    buttons.push(_renderTransportButtonHtml({
        className: _joinClassNames([primaryClass, state.isPlaying ? '' : 'paused']),
        id: 'dbtn-pause-' + i,
        onclick: 'onDevicePause(' + i + ')',
        title: state.canTransport
            ? (state.isPlaying ? (opts.pauseTitlePlaying || 'Pause') : (opts.pauseTitlePaused || 'Play'))
            : (state.transportUnavailableTitle || (state.isPlaying ? (opts.pauseTitlePlaying || 'Pause') : (opts.pauseTitlePaused || 'Play'))),
        iconHtml: _playPauseIconHtml(state.isPlaying),
        disabled: !state.canTransport,
        hidden: false,
    }));
    pushPrevNextButton('next', opts.nextTitle || 'Next', '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>');
    pushModeButton('repeat', state.repeatTitle, _repeatIconHtml(state.repeat), state.hasQueueControls && state.repeat !== 'off');
    if (!opts.modeFirst) {
        pushModeButton('shuffle', state.shuffleTitle, '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>', state.hasQueueControls && state.shuffle);
    }
    return buttons.join('');
}

function _getListTrackLabel(dev) {
    return _firstOfSlash(dev.current_track || (dev.ma_now_playing || {}).track || '');
}

function _getListTrackMeta(dev) {
    var artist = _getListTrackArtist(dev);
    var album = _getListTrackAlbum(dev);
    return [artist, album].filter(Boolean).join(' · ');
}

function _getListTrackArtist(dev) {
    var ma = dev.ma_now_playing || {};
    return _firstOfSlash(dev.current_artist || ma.artist || '');
}

function _getListTrackAlbum(dev) {
    var ma = dev.ma_now_playing || {};
    return _firstOfSlash(ma.connected && deviceHasSink(dev) ? (ma.album || '') : '');
}

function _getListRowSummary(dev) {
    var track = _getListTrackLabel(dev);
    var artist = _firstOfSlash(dev.current_artist || (dev.ma_now_playing || {}).artist || '');
    if (track && artist) return track + ' — ' + artist;
    if (track) return track;
    var releaseMeta = getDeviceReleaseMeta(dev);
    if (releaseMeta.visible) return releaseMeta.summary;
    return getDeviceDisplayStatusMeta(dev).summary;
}

function _getListPlaybackProgress(dev) {
    var idx = arguments.length > 1 ? arguments[1] : null;
    return _getDevicePlaybackProgressState(dev, idx);
}

function _getListQueueNeighborMeta(dev, direction) {
    var ma = dev.ma_now_playing || {};
    if (!ma.connected) {
        return {visible: false, empty: true, label: '', track: '', meta: '', modifierClass: ''};
    }
    var prefix = direction === 'prev' ? 'prev' : 'next';
    var track = _firstOfSlash(ma[prefix + '_track'] || '');
    var artist = _firstOfSlash(ma[prefix + '_artist'] || '');
    var album = _firstOfSlash(ma[prefix + '_album'] || '');
    var queueIndex = Number(ma.queue_index);
    if (!Number.isFinite(queueIndex)) queueIndex = 0;
    var queueTotal = Number(ma.queue_total);
    if (!Number.isFinite(queueTotal)) queueTotal = 0;
    var fallbackTrack = '';
    if (!track) {
        if (direction === 'prev' && queueIndex <= 0) {
            fallbackTrack = 'Queue start';
        } else if (direction === 'next' && queueTotal > 0 && queueIndex >= queueTotal - 1) {
            fallbackTrack = 'Queue end';
        }
    }
    return {
        visible: true,
        empty: !track,
        label: direction === 'prev' ? 'Previous' : 'Next',
        track: track || fallbackTrack,
        artist: artist,
        album: album,
        modifierClass: direction === 'prev' ? 'is-prev' : 'is-next',
    };
}

function _getListQueueNeighborHtml(dev, direction) {
    var meta = _getListQueueNeighborMeta(dev, direction);
    if (!meta.visible) return '';
    return '<div class="list-queue-neighbor ' + meta.modifierClass + (meta.empty ? ' is-empty' : '') + '">' +
        '<div class="list-queue-neighbor-label">' + escHtml(meta.label) + '</div>' +
        '<div class="list-queue-neighbor-title">' + escHtml(meta.track) + '</div>' +
        (meta.artist ? '<div class="list-queue-neighbor-artist">' + escHtml(meta.artist) + '</div>' : '') +
        (meta.album ? '<div class="list-queue-neighbor-album">' + escHtml(meta.album) + '</div>' : '') +
    '</div>';
}

function _getListRoutingSummary(dev) {
    var sinkState = dev.bt_management_enabled === false
        ? 'released'
        : deviceHasSink(dev)
            ? 'sink ready'
            : (dev.bluetooth_connected ? 'waiting for sink' : 'no sink');
    return sinkState === 'sink ready' ? '' : sinkState;
}

function _getListArtworkHtml(mediaState) {
    return _renderNowPlayingArtworkHtml(null, mediaState, {
        containerClass: 'list-detail-art',
        imageClass: 'list-detail-art-image',
        previewClass: 'artwork-preview-popover',
    });
}

function buildListView(entries, hiddenCount) {
    if (!entries.length) {
        return '<div class="list-view-shell">' +
            _renderEmptyStateHtml({
                className: 'list-empty-state',
                icon: 'search',
                title: 'No matching devices',
                copy: 'Adjust the current filters to show more players.',
                compact: true,
                center: true,
                inline: true,
            }) +
        '</div>';
    }
    var dirArrow = listSortState.direction === 'asc' ? '&#9652;' : '&#9662;';
    var header = '<div class="list-header">' +
        '<div></div>' +
        '<button type="button" class="list-sort-btn ' + (listSortState.column === 'name' ? 'active' : '') + '" onclick="event.stopPropagation();sortListBy(\'name\')">Name ' + (listSortState.column === 'name' ? dirArrow : '') + '</button>' +
        '<button type="button" class="list-sort-btn list-col-divider-start ' + (listSortState.column === 'status' ? 'active' : '') + '" onclick="event.stopPropagation();sortListBy(\'status\')">Status ' + (listSortState.column === 'status' ? dirArrow : '') + '</button>' +
        '<button type="button" class="list-sort-btn list-col-divider-mid ' + (listSortState.column === 'adapter' ? 'active' : '') + '" onclick="event.stopPropagation();sortListBy(\'adapter\')">Adapter ' + (listSortState.column === 'adapter' ? dirArrow : '') + '</button>' +
        '<button type="button" class="list-sort-btn list-col-divider-mid ' + (listSortState.column === 'group' ? 'active' : '') + '" onclick="event.stopPropagation();sortListBy(\'group\')">Group ' + (listSortState.column === 'group' ? dirArrow : '') + '</button>' +
        '<button type="button" class="list-sort-btn list-col-divider-mid ' + (listSortState.column === 'volume' ? 'active' : '') + '" onclick="event.stopPropagation();sortListBy(\'volume\')">Volume ' + (listSortState.column === 'volume' ? dirArrow : '') + '</button>' +
        '<div class="list-sort-btn list-sort-label list-col-divider-end">Actions</div>' +
    '</div>';

    var rows = entries.map(function(entry) {
        var dev = entry.dev;
        var i = entry.index;
        var key = listRowKey(dev);
        var expanded = expandedListRowKey === key;
        var rowSummary = _getListRowSummary(dev);
        var mediaState = _getDeviceNowPlayingState(dev, i);
        var trackLabel = mediaState.titleText;
        var collapsedBadges = _getListCollapsedBadgesHtml(dev, i);
        var progress = mediaState.progress;
        var hasMediaContext = !!(deviceHasSink(dev) && (trackLabel !== 'Nothing playing' || mediaState.metaText !== rowSummary || progress.visible));
        var statusMeta = getDeviceDisplayStatusMeta(dev);
        var effectiveMuted = !!dev.muted || !!dev.sink_muted;
        var mgmtEnabled = dev.bt_management_enabled !== false;
        var transportState = _getDeviceTransportState(dev, mediaState);
        var reconnectCapability = _getDeviceActionCapability(dev, 'reconnect');
        var reconnectAvailable = _capabilityAvailable(reconnectCapability, mgmtEnabled);
        var reconnectTitle = reconnectAvailable
            ? 'Reconnect Bluetooth and refresh sink routing'
            : _capabilityBlockedReason(reconnectCapability, 'Reconnect unavailable');
        var canTransport = transportState.canTransport;
        var canMute = transportState.hasSink;
        var hasQueueNeighbors = !!(mediaState.ma || {}).connected;
        var pauseTitle = canTransport ? (dev.playing ? 'Pause' : 'Play') : transportState.transportUnavailableTitle;
        var muteTitle = canMute
            ? (effectiveMuted ? 'Unmute' : 'Mute')
            : transportState.muteUnavailableTitle;
        var trackTitleEq = _shouldShowEqualizer(dev) && trackLabel !== 'Nothing playing'
            ? _getEqualizerHtml(dev, 'list-track-eq')
            : '';
        var playerNameEq = !expanded && _shouldShowEqualizer(dev)
            ? _getEqualizerHtml(dev, 'list-name-eq')
            : '';
        var rowPauseBtnId = 'drow-pause-' + i;
        var rowMuteBtnId = 'drow-mute-' + i;
        var cardDisabled = _isDeviceDisabled(dev);
        var showDetailTransport = !!transportState.canTransport;
        var detailTransport = showDetailTransport
            ? '<div class="list-player-transport" onclick="event.stopPropagation()">' +
                _renderPlaybackTransportButtonsHtml(i, transportState, {
                    buttonBaseClass: 'media-btn list-player-transport-btn',
                    primaryButtonClass: 'media-btn list-player-transport-btn is-primary',
                    modeButtonClass: 'media-btn list-player-transport-btn is-mode',
                    modeFirst: true,
                    prevTitle: 'Previous track',
                    nextTitle: 'Next track',
                }) +
              '</div>'
            : '';
        var isStandby = !!dev.bt_standby;
        var standbyActionClass = isStandby ? 'success' : 'warn';
        var standbyLabel = isStandby ? 'Wake' : 'Standby';
        var standbyIcon = isStandby ? 'reconnect' : 'standby';
        var standbyTitle = isStandby
            ? 'Wake from standby — reconnect Bluetooth and resume audio'
            : 'Enter standby — disconnect Bluetooth to save speaker battery';
        var detailActions = '<div class="list-detail-actions" onclick="event.stopPropagation()">' +
            '<button type="button" class="action-btn list-action-btn accent" id="dbtn-reconnect-' + i + '" onclick="btReconnect(' + i + ')" title="' + escHtmlAttr(reconnectTitle) + '"' + (reconnectAvailable && !cardDisabled ? '' : ' disabled') + '>' + _actionButtonInnerHtml('reconnect', 'Reconnect') + '</button>' +
            '<button type="button" class="action-btn list-action-btn ' + standbyActionClass + '" id="dbtn-standby-' + i + '" onclick="btToggleStandby(' + i + ')" title="' + escHtmlAttr(standbyTitle) + '"' + (cardDisabled ? ' disabled' : '') + '>' + _actionButtonInnerHtml(standbyIcon, standbyLabel) + '</button>' +
            '<button type="button" class="action-btn list-action-btn danger" onclick="confirmDisableDevice(' + i + ')"' + (cardDisabled ? ' disabled' : '') + '>' + _actionButtonInnerHtml('disable', 'Disable') + '</button>' +
        '</div>';
        var detailBlockedHints = _renderBlockedControlHints(_collectDeviceBlockedControlHints(dev, transportState, _lastOperatorGuidance), {compact: true});
        var routeSummary = _getListRoutingSummary(dev);
        var detailFooter = '<div class="list-detail-footer">' +
            (routeSummary ? '<div class="list-route-summary">' + escHtml(routeSummary) + '</div>' : '<div class="list-route-summary-spacer"></div>') +
        '</div>';
        var detailCurrentCopy = _renderNowPlayingTextHtml(mediaState, {
            containerClass: 'list-detail-current-copy is-rail',
            preTitleHtml: '<div class="list-now-playing-row">' +
                ((_shouldShowEqualizer(dev) && trackLabel !== 'Nothing playing')
                    ? _renderNowPlayingInfoBadgeHtml('list-now-playing-badge')
                    : _renderNowPlayingInfoBadgeHtml('list-now-playing-badge', { placeholder: true })) +
                '</div>',
            titleRowClass: 'list-track-title-row',
            titleGroupClass: 'list-track-title-group',
            titleClass: 'list-track-title',
            titleEqHtml: trackTitleEq,
            metaClass: 'list-track-meta',
            metaText: mediaState.artist || '',
            showAlbumLine: true,
            albumClass: 'list-detail-album-title',
        });
        var detailProgressHtml = '<div class="list-detail-progress-wrap" id="dlprog-wrap-' + i + '"' + (progress.visible ? '' : ' style="display:none"') + '>' +
            '<div class="list-detail-time" id="dlprog-time-' + i + '">' + escHtml(progress.text) + '</div>' +
            '<div class="np-progress"><div class="np-progress-fill" id="dlprog-fill-' + i + '" style="width:' + progress.pct + '%"></div></div>' +
        '</div>';
        var detailMediaLane = hasQueueNeighbors
            ? '<div class="list-player-media-lane">' +
                _getListQueueNeighborHtml(dev, 'prev') +
                detailTransport +
                _getListQueueNeighborHtml(dev, 'next') +
                detailProgressHtml +
              '</div>'
            : '<div class="list-player-media-lane is-solo">' + detailTransport + detailProgressHtml + '</div>';
        var showPlaybackRail = hasQueueNeighbors || showDetailTransport || progress.visible;
        var detailPlaybackRail = showPlaybackRail
            ? '<div class="list-detail-playback-rail' + (hasQueueNeighbors ? '' : ' is-solo') + '">' +
                detailMediaLane +
              '</div>'
            : '';
        var quickActions = '<div class="list-actions" onclick="event.stopPropagation()">' +
            '<button type="button" class="media-btn list-inline-btn' + (dev.playing ? '' : ' paused') + '" id="' + rowPauseBtnId + '" onclick="event.stopPropagation();onDevicePause(' + i + ', \'' + rowPauseBtnId + '\')" title="' + escHtmlAttr(pauseTitle) + '"' + (canTransport && !cardDisabled ? '' : ' disabled') + (canTransport ? '' : ' style="display:none"') + '>' + _playPauseIconHtml(dev.playing) + '</button>' +
            '<button type="button" class="media-btn list-inline-btn' + (effectiveMuted ? ' muted' : '') + '" id="' + rowMuteBtnId + '" onclick="event.stopPropagation();onMuteClick(' + i + ', \'' + rowMuteBtnId + '\')" title="' + escHtmlAttr(muteTitle) + '"' + (canMute && !cardDisabled ? '' : ' disabled') + '>' + _muteIconHtml(effectiveMuted) + '</button>' +
            '<button type="button" class="icon-btn list-inline-btn list-settings-btn" onclick="event.stopPropagation();openDeviceSettings(' + i + ')" title="Device settings"' + (cardDisabled ? ' disabled' : '') + '>' + _settingsIconHtml() + '</button>' +
        '</div>';
        var nameTitleRow = '<span class="list-name-title-row">' +
            '<span class="list-name-title-group">' +
                '<span class="list-name-title">' + escHtml(dev.player_name || ('Device ' + (i + 1))) + '</span>' +
                playerNameEq +
            '</span>' +
            collapsedBadges +
        '</span>';
        var nameMetaContent = expanded
            ? ''
            : '<span class="list-name-meta">' + escHtml(rowSummary) + '</span>';
        var nameMetaRow = nameMetaContent
            ? '<span class="list-name-meta-row' + (expanded ? ' is-controls' : '') + '">' + nameMetaContent + '</span>'
            : '';
        return '<div class="list-row ' + statusMeta.cardStateClass + ' ' + (expanded ? 'expanded' : '') + (cardDisabled ? ' list-row--disabled' : '') + '">' +
            '<div class="list-row-main" onclick="toggleListRow(\'' + escHtmlAttr(key) + '\')">' +
                '<div class="list-select-cell"><input type="checkbox" id="dsel-' + i + '" ' + (_groupSelected[i] !== false && !cardDisabled ? 'checked' : '') + (cardDisabled ? ' disabled' : '') + ' onclick="event.stopPropagation()" onchange="onDeviceSelect(' + i + ', this.checked)"></div>' +
                '<div class="list-cell-name">' +
                    '<span class="list-name-icon ' + statusMeta.iconToneClass + '">' +
                        _uiIconSvg('speaker') +
                    '</span>' +
                    '<span class="list-name-copy">' +
                        nameTitleRow +
                        nameMetaRow +
                    '</span>' +
                '</div>' +
                '<div class="list-status-cell">' + _renderDeviceStatusBadgeHtml(dev, 'chip') + '</div>' +
                '<div class="list-adapter-cell">' + _adapterBadgeHtml(dev, i, 'chip') + '</div>' +
                '<div class="list-group-cell">' + _groupBadgeHtml(dev, i, 'chip') + '</div>' +
                '<div class="list-vol-wrap" onclick="event.stopPropagation()">' +
                    '<input type="range" min="0" max="100" value="' + (dev.volume || 0) + '" id="vslider-' + i + '" oninput="onVolumeInput(' + i + ', this.value)"' + (cardDisabled ? ' disabled' : '') + '>' +
                    '<span class="vol-pct" id="dvol-' + i + '">' + (dev.volume || 0) + '</span>' +
                '</div>' +
                quickActions +
            '</div>' +
            '<div class="list-row-detail">' +
                '<div class="list-detail-player-shell">' +
                    '<div class="list-detail-body">' +
                        '<div class="list-detail-player-main">' +
                            '<div class="list-detail-art-rail">' +
                                _getListArtworkHtml(mediaState) +
                                detailCurrentCopy +
                            '</div>' +
                            '<div class="list-detail-player-center">' +
                                detailPlaybackRail +
                            '</div>' +
                        '</div>' +
                        detailActions +
                        detailBlockedHints +
                    '</div>' +
                    detailFooter +
                '</div>' +
            '</div>' +
        '</div>';
    }).join('');

    return '<div class="list-view-shell">' + header + rows +
        '<div class="list-footer">Showing ' + entries.length + ' of ' + lastDevices.length +
        (hiddenCount > 0 ? ' (' + hiddenCount + ' hidden by filters)' : '') + '</div></div>';
}

function renderDevicesView() {
    var grid = document.getElementById('status-grid');
    if (!grid) return;
    var entries = _getVisibleDeviceEntries();
    if (!entries.length && lastDevices.length) {
        grid.classList.toggle('list-view', currentViewMode === 'list');
        grid.innerHTML = '<div class="list-view-shell">' +
            _renderEmptyStateHtml({
                className: 'list-empty-state',
                icon: 'search',
                title: 'No matching devices',
                copy: 'Adjust the current filters to show more players.',
                compact: true,
                center: true,
                inline: true,
            }) +
        '</div>';
        return;
    }
    if (currentViewMode === 'list') {
        if (!expandedListRowKey || !entries.some(function(entry) { return listRowKey(entry.dev) === expandedListRowKey; })) {
            expandedListRowKey = entries.length ? listRowKey((entries.find(function(entry) { return entry.dev.playing; }) || entries[0]).dev) : null;
        }
        grid.classList.add('list-view');
        grid.innerHTML = buildListView(_sortVisibleEntries(entries), lastDevices.length - entries.length);
        Array.from(grid.querySelectorAll('input[type="range"]')).forEach(updateSliderFill);
        return;
    }
    grid.classList.remove('list-view');
    grid.innerHTML = '';
    entries.forEach(function(entry) {
        var card = buildDeviceCard(entry.index);
        grid.appendChild(card);
        populateDeviceCard(entry.index, entry.dev);
        var selCb = document.getElementById('dsel-' + entry.index);
        if (selCb) selCb.checked = _groupSelected[entry.index] !== false;
    });
    Array.from(grid.querySelectorAll('input[type="range"]')).forEach(updateSliderFill);
}

function renderStatusPayload(status) {
    _statusHasEverSucceeded = true;
    _lastDisabledDevices = Array.isArray(status && status.disabled_devices) ? status.disabled_devices : [];
    var info = [];
    _runtimeMode = status.runtime_mode || 'production';
    _setViewModeStorageScope(_runtimeMode);
    _applyDemoScreenshotDefaults();
    if (status.hostname) info.push(status.hostname);
    if (status.ip_address) info.push(status.ip_address);
    if (status.uptime) info.push('up ' + status.uptime);
    var sysEl = document.getElementById('system-info');
    if (sysEl) {
        sysEl.innerHTML = '';
        if (status.runtime) {
            var badge = document.createElement('span');
            badge.className = 'runtime-badge meta-badge';
            badge.textContent = status.runtime.toUpperCase();
            sysEl.appendChild(badge);
        }
        if (info.length) {
            sysEl.appendChild(document.createTextNode(' ' + info.join(' · ')));
        }
    }

    _showUpdateBadge(status.update_available);
    _syncVersionDisplayFromStatus(status);
    var resolvedMaUiUrl = _normalizeExternalUrlBase(lastMaUiUrl || _getConfiguredMaUiUrl() || '');
    if (resolvedMaUiUrl) lastMaUiUrl = resolvedMaUiUrl;
    var resolvedMaWebUrl = _normalizeExternalUrlBase(status.ma_web_url || lastMaWebUrl || '');
    if (resolvedMaWebUrl) lastMaWebUrl = resolvedMaWebUrl;

    var userLink = document.getElementById('header-user-link');
    if (userLink) {
        var method = userLink.dataset.authMethod || '';
        var preferredMaProfileUrl = userLink.dataset.maProfileUrl || '';
        if ((method === 'ha' || method === 'ha_via_ma') && preferredMaProfileUrl) {
            userLink.href = preferredMaProfileUrl;
        } else if (status.ma_connected && (resolvedMaUiUrl || resolvedMaWebUrl)) {
            userLink.href = (resolvedMaUiUrl || resolvedMaWebUrl) + '/#/settings/profile';
        } else if (method === 'ha' || method === 'ha_via_ma') {
            if (resolvedMaWebUrl) {
                var u = new URL(resolvedMaWebUrl);
                userLink.href = u.protocol + '//' + u.hostname + ':8123/profile';
            } else {
                userLink.href = '/profile';
            }
        }
    }

    var devices = _mergeDisabledDeviceState(
        status.devices || (status.error ? [] : [status]),
        _lastDisabledDevices
    );
    var runtimeServiceState = _deriveUpdateRuntimeState(status) || _deriveZeroDeviceRuntimeState(status, devices);
    var grid = document.getElementById('status-grid');
    var emptyEl = document.getElementById('no-devices-hint');
    _applyBackendServiceState(runtimeServiceState);
    if (runtimeServiceState) {
        lastDevices = [];
        lastGroups = [];
        _hideOperatorGuidance();
        _renderBackendServicePlaceholder(runtimeServiceState);
        _updateGroupPanel();
        updateHealthIndicator([], status.operator_guidance || null);
        _syncRestartBanner(status, runtimeServiceState);
        return;
    }
    if (devices.length === 0) {
        lastDevices = [];
        if (grid) {
            _applyOperatorGuidance(status.operator_guidance || null);
            _syncEmptyStatePlaceholder(status.operator_guidance || null);
        } else if (emptyEl) {
            _applyOperatorGuidance(status.operator_guidance || null);
            _syncEmptyStatePlaceholder(status.operator_guidance || null);
        }
        _updateGroupPanel();
        updateHealthIndicator([], status.operator_guidance || null);
        _syncRestartBanner(status, null);
        return;
    }
    _applyOperatorGuidance(status.operator_guidance || null);
    if (emptyEl) emptyEl.remove();

    var sorted = _sortDevicesForStatus(devices);
    if (lastDevices.length !== sorted.length ||
        !lastDevices.every(function(d, idx) { return d.player_name === sorted[idx].player_name; })) {
        _groupSelected = {};
        lastReanchorCount = {};
        reanchorShownAt = {};
        lastReanchorAt = {};
    }

    var now = Date.now();
    var prevDevices = lastDevices;
    lastDevices = sorted;
    lastGroups = status.groups || [];
    _syncViewModeForDeviceCount(sorted.length);
    sorted.forEach(function(dev) {
        var pn = dev.player_name || '__default__';
        if (_muteDebounce[pn] && (now - _muteDebounce[pn]) < 2000) {
            var prev = prevDevices.find(function(item) { return item.player_name === pn; });
            if (prev) dev.muted = prev.muted;
        } else {
            delete _muteDebounce[pn];
        }
    });
    sorted.forEach(function(dev, index) {
        var isActive = !!(dev.bluetooth_connected || dev.playing);
        if (!isActive) {
            _groupSelected[index] = false;
        } else if (_groupSelected[index] === undefined) {
            _groupSelected[index] = true;
        }
    });

    _updateAdapterFilter();
    renderDevicesView();
    refreshBtDeviceRowsRuntime();
    _updateGroupPanel();
    updateHealthIndicator(sorted, status.operator_guidance || null);
    _syncRestartBanner(status, null);
}

async function updateStatus() {
    try {
        var resp = await fetch(API_BASE + '/api/status');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        if (!resp.ok) throw new Error('HTTP ' + resp.status + ' ' + (resp.statusText || 'status error'));
        renderStatusPayload(await resp.json());
    } catch (err) {
        console.error('Status update failed:', err);
        var unavailableState = {
            kind: _statusHasEverSucceeded ? 'unavailable' : 'connecting',
            tone: _statusHasEverSucceeded ? 'warning' : 'info',
            label: _statusHasEverSucceeded ? 'Backend unavailable' : 'Connecting…',
            title: _statusHasEverSucceeded ? 'Bridge backend is temporarily unavailable' : 'Connecting to bridge',
            summary: _statusHasEverSucceeded
                ? 'The backend stopped responding. It may still be restarting. Retrying automatically in the background.'
                : 'Waiting for the backend to start. This page will update automatically when the service becomes ready.',
            action: {key: 'refresh_diagnostics', label: 'Retry now'},
        };
        var updateUnavailableState = _deriveUpdateRuntimeState(null, {backendUnavailable: true});
        _applyBackendServiceState(updateUnavailableState || unavailableState);
        _hideOperatorGuidance();
        if (updateUnavailableState || !_statusHasEverSucceeded || !lastDevices.length) {
            _renderBackendServicePlaceholder(updateUnavailableState || unavailableState);
        }
        updateHealthIndicator(lastDevices || [], _lastOperatorGuidance || null);
        _syncRestartBanner(null, updateUnavailableState || unavailableState);
    }
}

// Interpolate progress bars every second between SSE updates
setInterval(function() {
    var now = Date.now();
    // MA progress per device
    Object.keys(_maProgSnapshots).forEach(function(idx) {
        var snap = _maProgSnapshots[idx];
        if (!snap) return;
        var elapsedSec = _getMaSnapshotElapsedNow(snap, now);
        _applyPlaybackProgressForIndex(idx, {
            visible: true,
            pct: Math.min(100, (elapsedSec / snap.duration) * 100),
            text: fmtSec(elapsedSec) + ' / ' + fmtSec(snap.duration),
        });
    });
    // Sendspin-native per-device progress (ms)
    Object.keys(_progSnapshots).forEach(function(idx) {
        var snap = _progSnapshots[idx];
        if (!snap) return;
        var pos = snap.paused ? snap.pos : Math.min(snap.pos + (now - snap.t), snap.dur);
        _applyPlaybackProgressForIndex(idx, {
            visible: true,
            pct: Math.min(100, (pos / snap.dur) * 100),
            text: fmtMs(pos) + ' / ' + fmtMs(snap.dur),
        });
    });
}, 1000);

function buildDeviceCard(i) {
    var card = document.createElement('div');
    card.className = 'device-card';
    card.id = 'device-card-' + i;
    var speakerSvg = _uiIconSvg('speaker');
    var placeholderMedia = _getDeviceNowPlayingState({}, i);
    var placeholderTransport = _getDeviceTransportState({}, placeholderMedia);
    card.innerHTML =
        '<div class="card-head">' +
          '<input type="checkbox" class="device-select-cb" id="dsel-' + i + '" checked' +
            ' onchange="onDeviceSelect(' + i + ', this.checked)">' +
          '<div class="card-icon" id="dcard-icon-' + i + '">' + speakerSvg + '</div>' +
          '<div class="card-name-block">' +
            '<div class="card-name">' +
              '<span class="name-text" id="dname-' + i + '">Device ' + (i+1) + '</span>' +
              _getEqualizerHtml({}, 'list-name-eq card-name-eq', 'deq-' + i) +
            '</div>' +
          '</div>' +
          '<span class="chip meta-badge meta-badge-status is-neutral" id="dreleased-badge-' + i + '" style="display:none"></span>' +
          '<span class="chip meta-badge meta-badge-status is-neutral" id="dstandby-badge-' + i + '" style="display:none"></span>' +
          '<button type="button" class="chip meta-badge meta-badge-link group-badge-unified meta-badge-interactive device-card-group-badge" id="dgroup-' + i + '" style="display:none"></button>' +
        '</div>' +
        '<div class="card-chips">' +
          '<button type="button" class="chip meta-badge meta-badge-link meta-badge-interactive adapter-link-badge is-neutral" id="dchip-bt-' + i + '" title="Open Bluetooth adapter settings"></button>' +
          '<span id="dchip-ma-' + i + '"></span>' +
          '<span id="dplay-chip-' + i + '"></span>' +
          '<span id="droom-chip-' + i + '" style="display:none"></span>' +
          '<span id="dtransfer-chip-' + i + '" style="display:none"></span>' +
          '<span id="dsync-' + i + '" style="display:none"></span>' +
          '<span id="dsync-detail-' + i + '" style="display:none"></span>' +
          '<span id="dbattery-' + i + '" style="display:none"></span>' +
        '</div>' +
        '<div class="card-controls">' +
          _renderPlaybackTransportButtonsHtml(i, placeholderTransport, {
              buttonBaseClass: 'media-btn',
              primaryButtonClass: 'media-btn',
              modeButtonClass: 'media-btn',
              renderPrevNextWhenInactive: true,
              renderModeButtonsWhenInactive: true,
              disableWhenInactive: true,
              modeFirst: false,
          }) +
          '<div class="vol-wrap">' +
            '<input type="range" min="0" max="100" value="100" id="vslider-' + i + '" oninput="onVolumeInput(' + i + ', this.value)">' +
            '<span class="vol-pct" id="dvol-' + i + '">100</span>' +
          '</div>' +
          '<button type="button" class="media-btn" id="dmute-' + i + '" title="Mute/Unmute"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg></button>' +
        '</div>' +
        '<div class="card-np" id="dnp-' + i + '" style="display:none">' +
          _renderNowPlayingArtworkHtml(i, placeholderMedia, {
              persistent: true,
              imageId: 'dart-' + i,
              previewId: 'dart-preview-' + i,
              placeholderId: 'dart-placeholder-' + i,
          }) +
          _renderNowPlayingTextHtml(placeholderMedia, {
              containerClass: 'np-info',
              titleTag: 'span',
              titleClass: 'np-track',
              titleId: 'dtrack-' + i,
              metaTag: 'span',
              metaClass: 'np-meta',
              metaId: 'dtrack-meta-' + i,
              postMetaHtml: '<div class="np-time">' +
                '<div class="np-progress" id="dprog-wrap-' + i + '"><div class="np-progress-fill" id="dprog-fill-' + i + '"></div></div>' +
                '<span id="dprog-time-' + i + '"></span>' +
                '<span class="tooltip-text" id="daudiofmt-' + i + '"></span>' +
              '</div>',
          }) +
        '</div>' +
        '<div class="card-actions-row">' +
          '<span class="bt-action-status" id="dbt-action-status-' + i + '"></span>' +
          '<div class="card-action-buttons">' +
            '<button type="button" class="action-btn accent" id="dbtn-reconnect-' + i + '" onclick="btReconnect(' + i + ')">' + _actionButtonInnerHtml('reconnect', 'Reconnect') + '</button>' +
            '<button type="button" class="action-btn accent" id="dbtn-wake-' + i + '" onclick="wakeDevice(' + i + ')" style="display:none">' + _actionButtonInnerHtml('reconnect', 'Wake') + '</button>' +
            '<button type="button" class="action-btn warn" id="dbtn-standby-' + i + '" onclick="btToggleStandby(' + i + ')">' + _actionButtonInnerHtml('standby', 'Standby') + '</button>' +
            '<button type="button" class="action-btn danger" id="dbtn-disable-' + i + '" onclick="confirmDisableDevice(' + i + ')">' + _actionButtonInnerHtml('disable', 'Disable') + '</button>' +
            '<button type="button" class="icon-btn device-settings-btn card-corner-settings-btn" onclick="openDeviceSettings(' + i + ')" title="Device settings">' + _settingsIconHtml() + '</button>' +
          '</div>' +
        '</div>' +
        '<div class="device-blocked-hints-wrap" id="dblocked-hints-' + i + '" style="display:none"></div>';
    var muteBtn = card.querySelector('#dmute-' + i);
    if (muteBtn) {
        muteBtn.onclick = function() {
            onMuteClick(i);
        };
    }
    return card;
}

function populateDeviceCard(i, dev) {
    var name = dev.player_name || ('Device ' + (i + 1));
    var statusMeta = getDeviceDisplayStatusMeta(dev);
    var releaseMeta = getDeviceReleaseMeta(dev);
    var mediaState = _getDeviceNowPlayingState(dev, i);
    var transportState = _getDeviceTransportState(dev, mediaState);
    var nameEl = document.getElementById('dname-' + i);
    if (nameEl) nameEl.textContent = name;

    var releasedBadge = document.getElementById('dreleased-badge-' + i);
    if (releasedBadge) {
        var releaseRenderData = _getReleaseBadgeRenderData(releaseMeta, 'chip');
        releasedBadge.style.display = releaseRenderData ? '' : 'none';
        if (releaseRenderData) {
            releasedBadge.innerHTML = releaseRenderData.innerHtml;
            releasedBadge.className = releaseRenderData.className;
            releasedBadge.title = releaseRenderData.title;
        }
    }

    var isStandby = !!dev.bt_standby;
    var standbyBadge = document.getElementById('dstandby-badge-' + i);
    if (standbyBadge) {
        standbyBadge.style.display = isStandby ? '' : 'none';
        if (isStandby) {
            var since = dev.bt_standby_since ? _formatDuration(new Date(dev.bt_standby_since)) : '';
            standbyBadge.innerHTML = '\uD83D\uDCA4 Standby' + (since ? ' (' + since + ')' : '');
            standbyBadge.title = 'Speaker in standby to save battery. Click Wake to reconnect.';
        }
    }
    var wakeBtn = document.getElementById('dbtn-wake-' + i);
    if (wakeBtn) wakeBtn.style.display = isStandby ? '' : 'none';

    var batteryEl = document.getElementById('dbattery-' + i);
    if (batteryEl) {
        var batteryRenderData = _getBatteryBadgeRenderData(dev.battery_level, 'chip battery-chip');
        if (batteryRenderData.visible) {
            batteryEl.className = batteryRenderData.className;
            batteryEl.innerHTML = batteryRenderData.innerHtml;
            batteryEl.title = batteryRenderData.title;
            batteryEl.style.display = '';
        } else {
            batteryEl.className = '';
            batteryEl.innerHTML = '';
            batteryEl.title = '';
            batteryEl.style.display = 'none';
        }
    }

    var card = document.getElementById('device-card-' + i);
    if (card) {
        var isActive = dev.bluetooth_connected || dev.playing;
        var isDisabled = _isDeviceDisabled(dev);
        card.classList.toggle('inactive', !isActive);
        card.classList.toggle('device-card--disabled', isDisabled);
        card.classList.remove('is-success', 'is-warning', 'is-error', 'is-neutral');
        card.classList.toggle('playing', statusMeta.key === 'playing');
        card.classList.add(statusMeta.cardStateClass);
        var selCb = document.getElementById('dsel-' + i);
        if (selCb) {
            selCb.disabled = isDisabled;
            if (isDisabled) {
                selCb.checked = false;
                _groupSelected[i] = false;
            } else if (!isActive && selCb.checked) {
                selCb.checked = false;
                _groupSelected[i] = false;
            } else if (isActive && !selCb.checked && _groupSelected[i] !== false) {
                selCb.checked = true;
            }
        }
    }

    var cardIcon = document.getElementById('dcard-icon-' + i);
    if (cardIcon) {
        cardIcon.className = 'card-icon ' + statusMeta.iconToneClass;
    }

    var groupBadge = document.getElementById('dgroup-' + i);
    if (groupBadge) {
        var groupRenderData = _getGroupBadgeRenderData(dev, i, 'chip device-card-group-badge');
        var groupMeta = groupRenderData.meta;
        groupBadge.className = groupRenderData.className;
        groupBadge.innerHTML = groupRenderData.innerHtml;
        groupBadge.title = groupRenderData.title;
        groupBadge.disabled = groupRenderData.disabled;
        groupBadge.onclick = groupMeta.clickable ? function() { openDeviceGroupSettings(i); } : null;
        if (groupRenderData.ariaLabel) {
            groupBadge.setAttribute('aria-label', groupRenderData.ariaLabel);
        } else {
            groupBadge.removeAttribute('aria-label');
        }
        var groupPeers = dev.group_id
            ? (lastDevices || []).filter(function(d) { return d !== dev && d.group_id === dev.group_id; }).length
            : 0;
        var isSolo = !groupPeers && !groupMeta.externalCount;
        groupBadge.classList.toggle('hover-only', isSolo);
        groupBadge.style.display = groupMeta.isEmpty ? 'none' : '';
    }

    var btChipEl = document.getElementById('dchip-bt-' + i);
    if (btChipEl) {
        var adapterRenderData = _getAdapterBadgeRenderData(dev, i, 'chip');
        btChipEl.className = adapterRenderData.className;
        btChipEl.innerHTML = adapterRenderData.innerHtml;
        btChipEl.disabled = adapterRenderData.disabled || _isDeviceDisabled(dev);
        btChipEl.title = adapterRenderData.title;
        btChipEl.onclick = (adapterRenderData.disabled || _isDeviceDisabled(dev)) ? null : function() { openDeviceAdapterSettings(i); };
    }

    var maChip = document.getElementById('dchip-ma-' + i);
    if (maChip) {
        var serviceRenderData = _getServiceBadgeRenderData(dev, 'chip');
        maChip.className = serviceRenderData.className;
        maChip.innerHTML = serviceRenderData.innerHtml;
        maChip.title = serviceRenderData.title;
    }

    var playChip = document.getElementById('dplay-chip-' + i);
    var fmtEl = document.getElementById('daudiofmt-' + i);
    if (playChip) {
        var statusRenderData = _getStatusBadgeRenderData(dev, 'chip', 'meta-badge-label');
        playChip.className = statusRenderData.className;
        playChip.innerHTML = statusRenderData.innerHtml;
        playChip.title = statusRenderData.title;
    }

    var roomChip = document.getElementById('droom-chip-' + i);
    if (roomChip) {
        var roomRenderData = _getRoomBadgeRenderData(dev, 'chip');
        roomChip.style.display = roomRenderData ? '' : 'none';
        roomChip.className = roomRenderData ? roomRenderData.className : '';
        roomChip.innerHTML = roomRenderData ? roomRenderData.innerHtml : '';
        roomChip.title = roomRenderData ? roomRenderData.title : '';
    }

    var transferChip = document.getElementById('dtransfer-chip-' + i);
    if (transferChip) {
        var transferRenderData = _getTransferReadinessBadgeRenderData(dev, 'chip');
        transferChip.style.display = transferRenderData ? '' : 'none';
        transferChip.className = transferRenderData ? transferRenderData.className : '';
        transferChip.innerHTML = transferRenderData ? transferRenderData.innerHtml : '';
        transferChip.title = transferRenderData ? transferRenderData.title : '';
    }

    var progWrap = document.getElementById('dprog-wrap-' + i);
    _applyPlaybackProgressDom(
        progWrap,
        document.getElementById('dprog-fill-' + i),
        document.getElementById('dprog-time-' + i),
        mediaState.progress
    );

    if (fmtEl) {
        var fmt = dev.audio_format || '';
        if (fmt) { var sp = fmt.indexOf(' '); fmt = sp !== -1 ? fmt.slice(sp + 1) : ''; }
        fmtEl.textContent = fmt;
    }

    var pauseBtn = document.getElementById('dbtn-pause-' + i);
    if (pauseBtn && !_isLocked('dbtn-pause-' + i)) {
        if (dev.playing) {
            pauseBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
            pauseBtn.classList.remove('paused');
            pauseBtn.title = transportState.canTransport ? 'Pause' : transportState.transportUnavailableTitle;
        } else {
            pauseBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
            pauseBtn.classList.add('paused');
            pauseBtn.title = transportState.canTransport ? 'Play' : transportState.transportUnavailableTitle;
        }
        pauseBtn.disabled = !transportState.canTransport || _isDeviceDisabled(dev);
        pauseBtn.style.display = transportState.canTransport ? '' : 'none';
        pauseBtn.style.opacity = transportState.canTransport ? '' : '0.35';
    }

    var trackEl = document.getElementById('dtrack-' + i);
    var artEl = document.getElementById('dart-' + i);
    if (trackEl) {
        if (mediaState.hasTrack) {
            trackEl.textContent = mediaState.artist && mediaState.track
                ? mediaState.artist + ' — ' + mediaState.track
                : (mediaState.artist || mediaState.track || '');
            trackEl.title = mediaState.metaText
                ? trackEl.textContent + ' · ' + mediaState.metaText
                : trackEl.textContent;
            trackEl.style.color = dev.playing
                ? 'var(--primary-text-color)' : 'var(--secondary-text-color)';
        } else {
            trackEl.textContent = '';
            trackEl.title = '';
        }
        var npSection = document.getElementById('dnp-' + i);
        if (npSection) {
            npSection.style.display = mediaState.hasTrack ? '' : 'none';
        }
        var metaEl = document.getElementById('dtrack-meta-' + i);
        if (metaEl) metaEl.textContent = mediaState.metaText;
        var artPlaceholder = document.getElementById('dart-placeholder-' + i);
        var artPreviewEl = document.getElementById('dart-preview-' + i);
        if (artEl) {
            _setAlbumArtState(
                artEl,
                artPlaceholder,
                mediaState.artUrl,
                artPreviewEl
            );
        }
    }

    var prevBtn = document.getElementById('dma-prev-' + i);
    var nextBtn = document.getElementById('dma-next-' + i);
    var maShuffleBtn = document.getElementById('dma-shuffle-' + i);
    var maRepeatBtn = document.getElementById('dma-repeat-' + i);
    if (prevBtn) {
        prevBtn.style.display = transportState.hasQueueControls ? '' : 'none';
        prevBtn.disabled = !transportState.hasQueueControls || !!transportState.queueActionPending;
        prevBtn.title = _buildQueueActionTitle('Previous track', transportState.queueActionPending, !transportState.hasQueueControls ? transportState.queueUnavailableTitle : '', transportState.pendingSummary);
    }
    if (nextBtn) {
        nextBtn.style.display = transportState.hasQueueControls ? '' : 'none';
        nextBtn.disabled = !transportState.hasQueueControls || !!transportState.queueActionPending;
        nextBtn.title = _buildQueueActionTitle('Next track', transportState.queueActionPending, !transportState.hasQueueControls ? transportState.queueUnavailableTitle : '', transportState.pendingSummary);
    }
    if (maShuffleBtn) {
        maShuffleBtn.classList.toggle('ma-ready', transportState.hasQueueControls);
        maShuffleBtn.classList.toggle('active', transportState.hasQueueControls && transportState.shuffle);
        maShuffleBtn.disabled = !transportState.hasQueueControls || !!transportState.queueActionPending;
        maShuffleBtn.title = transportState.shuffleTitle;
        maShuffleBtn.style.opacity = transportState.hasQueueControls ? '' : '0.35';
    }
    if (maRepeatBtn) {
        maRepeatBtn.classList.toggle('ma-ready', transportState.hasQueueControls);
        maRepeatBtn.classList.toggle('active', transportState.hasQueueControls && transportState.repeat !== 'off');
        maRepeatBtn.classList.toggle('repeat-all', transportState.hasQueueControls && transportState.repeat === 'all');
        maRepeatBtn.classList.toggle('repeat-one', transportState.hasQueueControls && transportState.repeat === 'one');
        maRepeatBtn.innerHTML = _repeatIconHtml(transportState.repeat);
        maRepeatBtn.title = transportState.repeatTitle;
        maRepeatBtn.disabled = !transportState.hasQueueControls || !!transportState.queueActionPending;
        maRepeatBtn.style.opacity = transportState.hasQueueControls ? '' : '0.35';
    }

    var syncEl = document.getElementById('dsync-' + i);
    var syncDetail = document.getElementById('dsync-detail-' + i);
    if (syncEl) {
        var syncRenderData = _getSyncBadgeRenderData(dev, i, 'chip sync-chip', 'chip sync-detail-chip');
        syncEl.className = syncRenderData.className;
        syncEl.title = syncRenderData.title;
        syncEl.innerHTML = syncRenderData.innerHtml;
        syncEl.style.display = syncRenderData.visible ? '' : 'none';
        if (syncDetail) {
            syncDetail.innerHTML = syncRenderData.detailInnerHtml;
            syncDetail.className = syncRenderData.detailClassName;
            syncDetail.title = syncRenderData.detailTitle;
            syncDetail.style.display = syncRenderData.detailVisible ? '' : 'none';
        }
    }

    var hasSink = deviceHasSink(dev);
    if (dev.volume !== undefined && !volPending[i]) {
        var slider = document.getElementById('vslider-' + i);
        var volEl = document.getElementById('dvol-' + i);
        if (slider) {
            slider.value = dev.volume;
            slider.disabled = !transportState.hasSink || _isDeviceDisabled(dev);
            slider.style.opacity = hasSink ? '' : '0.35';
            slider.title = hasSink ? '' : 'Audio sink not configured';
        }
        if (volEl) volEl.textContent = String(dev.volume);
    }

    var eqEl = document.getElementById('deq-' + i);
    if (eqEl) {
        var eqExtra = eqEl.getAttribute('data-eq-extra') || '';
        eqEl.className = 'eq-bars' + _getEqualizerStateClass(dev) + (eqExtra ? ' ' + eqExtra : '');
    }

    var muteBtn = document.getElementById('dmute-' + i);
    if (muteBtn && !_isLocked('dmute-' + i)) {
        var effectiveMuted = !!dev.muted || !!dev.sink_muted;
        muteBtn.innerHTML = effectiveMuted
            ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>';
        muteBtn.title = hasSink
            ? (effectiveMuted
                ? (dev.sink_muted && !dev.muted ? 'Unmute (PA sink muted)' : 'Unmute')
                : 'Mute')
            : transportState.muteUnavailableTitle;
        muteBtn.classList.toggle('muted', effectiveMuted);
        muteBtn.disabled = !transportState.hasSink || _isDeviceDisabled(dev);
        muteBtn.style.opacity = hasSink ? '' : '0.35';
    }

    var vslider = document.getElementById('vslider-' + i);
    if (vslider) updateSliderFill(vslider);

    var relBtn = document.getElementById('dbtn-release-' + i);
    var standbyBtn = document.getElementById('dbtn-standby-' + i);
    var reconnectCapability = _getDeviceActionCapability(dev, 'reconnect');
    var reconnectAvailable = _capabilityAvailable(reconnectCapability, dev.bt_management_enabled !== false);
    if (standbyBtn) {
        _setStandbyActionButtonState(standbyBtn, !!dev.bt_standby);
        standbyBtn.disabled = _isDeviceDisabled(dev);
    }
    if (relBtn) {
        var toggleManagementCapability = _getDeviceActionCapability(dev, 'toggle_bt_management');
        var mgmtEnabled = dev.bt_management_enabled !== false;
        _setReleaseActionButtonState(relBtn, mgmtEnabled);
        relBtn.disabled = !_capabilityAvailable(toggleManagementCapability, true) || _isDeviceDisabled(dev);
        relBtn.title = relBtn.disabled
            ? _capabilityBlockedReason(toggleManagementCapability, 'BT management action unavailable')
            : relBtn.title;
    }
    {
        var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
        if (reconnBtn) {
            reconnBtn.disabled = !reconnectAvailable || _isDeviceDisabled(dev);
            reconnBtn.title = reconnectAvailable
                ? 'Reconnect Bluetooth and refresh sink routing'
                : _capabilityBlockedReason(reconnectCapability, 'Reconnect unavailable');
        }
    }

    var blockedHintsEl = document.getElementById('dblocked-hints-' + i);
    if (blockedHintsEl) {
        var blockedHintsHtml = _renderBlockedControlHints(_collectDeviceBlockedControlHints(dev, transportState, _lastOperatorGuidance), {compact: true});
        blockedHintsEl.innerHTML = blockedHintsHtml;
        blockedHintsEl.style.display = blockedHintsHtml ? '' : 'none';
    }
}

// ---- Volume slider ----

function onVolumeInput(i, val) {
    var slider = document.getElementById('vslider-' + i);
    if (!slider || slider.disabled) return;
    var volEl = document.getElementById('dvol-' + i);
    if (volEl) volEl.textContent = String(val);

    updateSliderFill(slider);

    // Mark pending so status poll doesn't overwrite while user drags
    volPending[i] = true;
    if (slider && !slider.disabled) { slider.style.opacity = '0.55'; }
    clearTimeout(volTimers[i]);
    volTimers[i] = setTimeout(function() {
        sendVolume(i, parseInt(val, 10));
    }, 300);
}

async function sendVolume(deviceIndex, vol) {
    var dev = lastDevices[deviceIndex] || {};
    try {
        if (!deviceHasSink(dev)) return;
        await fetch(API_BASE + '/api/volume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ volume: vol, player_name: dev.player_name || null }),
        });
    } catch (err) {
        console.error('Volume set failed:', err);
    } finally {
        delete volPending[deviceIndex];
        var slider = document.getElementById('vslider-' + deviceIndex);
        if (slider && !slider.disabled) { slider.style.opacity = ''; }
    }
}

// ---- Mute click handler (used by new card layout) ----
function onMuteClick(i, btnId) {
    var dev = lastDevices && lastDevices[i]; if (!dev) return;
    if (!deviceHasSink(dev)) return;
    var muteBtnId = btnId || 'dmute-' + i;
    if (_isLocked(muteBtnId)) return;
    _lockBtn(muteBtnId);
    var effMuted = !!dev.muted || !!dev.sink_muted;
    var desired = !effMuted;
    dev.muted = desired;
    dev.sink_muted = false;
    var pn = dev.player_name || '__default__';
    _muteDebounce[pn] = Date.now();
    var btn = document.getElementById(muteBtnId);
    if (btn) {
        btn.innerHTML = _muteIconHtml(desired);
        btn.title = desired ? 'Unmute' : 'Mute';
        btn.classList.toggle('muted', desired);
    }
    fetch(API_BASE + '/api/mute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player_name: dev.player_name || null, mute: desired }),
    }).then(function(r) { return r.json(); }).then(function(d) {
        if (d.success && lastDevices[i]) lastDevices[i].muted = d.muted;
    }).catch(function(e) {
        delete _muteDebounce[pn];
        if (lastDevices[i]) lastDevices[i].muted = !desired;
        if (btn) {
            btn.innerHTML = _muteIconHtml(!desired);
            btn.title = !desired ? 'Unmute' : 'Mute';
            btn.classList.toggle('muted', !desired);
        }
        console.error('Mute failed:', e);
    }).finally(function() { _unlockBtn(muteBtnId); });
}

// ---- Logs ----

function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function _formatDuration(sinceDate) {
    if (!sinceDate || isNaN(sinceDate.getTime())) return '';
    var ms = Date.now() - sinceDate.getTime();
    if (ms < 0) return '';
    var min = Math.floor(ms / 60000);
    if (min < 60) return min + 'm';
    var h = Math.floor(min / 60);
    return h + 'h ' + (min % 60) + 'm';
}

function _isErrorLevel(line) {
    if (!line || typeof line !== 'string') return false;
    var u = line.toUpperCase();
    return u.indexOf(' - ERROR - ') !== -1 || u.indexOf(' - CRITICAL - ') !== -1;
}

function _getRecentLogIssueTitle(issueState) {
    if (!issueState || !issueState.hasIssues) return 'Submit a bug report';
    if (issueState.level === 'warning') return 'Recent actionable warnings detected — click to report';
    return 'Recent errors detected — click to report';
}

function getLogClass(line) {
    if (!line || typeof line !== 'string') return '';
    var u = line.toUpperCase();
    if (u.indexOf(' - ERROR - ') !== -1 || u.indexOf(' - CRITICAL - ') !== -1) return 'log-error';
    if (u.indexOf(' - WARNING - ') !== -1) return 'log-warning';
    if (u.indexOf(' - INFO - ') !== -1)    return 'log-info';
    if (u.indexOf(' - DEBUG - ') !== -1)   return 'log-debug';
    return '';
}

function _parseLogLine(line) {
    var match = String(line).match(/^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)(.*?)(?:\s-\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s-\s)(.*)$/);
    if (!match) return null;
    return {
        ts: match[1],
        ctx: (match[2] || '').replace(/^\s*-\s*/, '').trim(),
        level: match[3],
        msg: match[4],
    };
}

function renderLogs() {
    var filtered = allLogs;
    if (currentLogLevel === 'error') {
        filtered = allLogs.filter(function(l) { return _isErrorLevel(l); });
    } else if (currentLogLevel === 'warning') {
        filtered = allLogs.filter(function(l) {
            var u = l.toUpperCase();
            return u.indexOf(' - WARNING - ') !== -1 || _isErrorLevel(l);
        });
    } else if (currentLogLevel === 'info') {
        filtered = allLogs.filter(function(l) {
            var u = l.toUpperCase();
            return u.indexOf(' - INFO - ') !== -1 || u.indexOf(' - WARNING - ') !== -1 || _isErrorLevel(l);
        });
    } else if (currentLogLevel === 'debug') {
        filtered = allLogs.filter(function(l) {
            return l && l.toUpperCase().indexOf(' - DEBUG - ') !== -1;
        });
    }
    var container = document.getElementById('logs');
    if (!container) return;
    container.innerHTML = filtered.map(function(line) {
        var parsed = _parseLogLine(line);
        if (!parsed) {
            return '<div class="log-line ' + getLogClass(line) + '">' + escHtml(line) + '</div>';
        }
        var ctx = parsed.ctx ? '<span class="log-context">' + escHtml(parsed.ctx) + '</span> ' : '';
        return '<div class="log-line ' + getLogClass(line) + '">' +
            '<span class="log-ts">' + escHtml(parsed.ts) + '</span> ' +
            ctx +
            '<span class="log-level">' + escHtml(parsed.level) + '</span> ' +
            '<span class="log-message">' + escHtml(parsed.msg) + '</span>' +
        '</div>';
    }).join('') || '<div class="logs-empty-state">No log lines match the current filter.</div>';
    var autoRefreshToggle = document.getElementById('auto-refresh-toggle');
    if (!autoRefreshToggle || autoRefreshToggle.checked) {
        container.scrollTop = container.scrollHeight;
    }
    // Highlight Report link if recent logs contain errors
    var reportLink = document.getElementById('report-link');
    if (reportLink) {
        var tail = allLogs.slice(-20);
        var fallbackHasErr = tail.some(function(l) { return _isErrorLevel(l); });
        var hasIssue = recentLogIssueState.hasMeta ? recentLogIssueState.hasIssues : fallbackHasErr;
        reportLink.classList.toggle('has-errors', hasIssue);
        reportLink.title = recentLogIssueState.hasMeta
            ? _getRecentLogIssueTitle(recentLogIssueState)
            : (fallbackHasErr ? 'Recent errors detected — click to report' : 'Submit a bug report');
    }
}

function setLogLevel(level) {
    currentLogLevel = level;
    document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
    var btn = document.getElementById('filter-' + level);
    if (btn) btn.classList.add('active');
    var select = document.getElementById('logs-filter-select');
    if (select && select.value !== level) select.value = level;
    renderLogs();
}

async function refreshLogs() {
    try {
        var resp = await fetch(API_BASE + '/api/logs?lines=150');
        var data = await resp.json();
        allLogs = data.logs || [];
        recentLogIssueState = {
            hasMeta: data.has_recent_issues != null,
            hasIssues: !!data.has_recent_issues,
            level: data.recent_issue_level || '',
            count: Number(data.recent_issue_count) || 0
        };
        renderLogs();
    } catch (err) {
        console.error('Error refreshing logs:', err);
    }
}

async function downloadLogs() {
    try {
        var resp = await fetch(API_BASE + '/api/logs/download');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var blob = await resp.blob();
        var cd = resp.headers.get('content-disposition') || '';
        var fname = (cd.match(/filename="?([^"]+)"?/) || [])[1] || 'sendspin-logs.txt';
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        a.click();
        URL.revokeObjectURL(a.href);
    } catch (err) {
        showToast('Download failed: ' + err.message, 'error');
    }
}

// ---- Group Controls ----

var _groupSelected = {};   // index → true/false
var _groupFilter = '';     // current group filter value ('' = all groups)

function _getSelectedNames() {
    var names = [];
    if (lastDevices) {
        lastDevices.forEach(function(dev, i) {
            if (_groupSelected[i] !== false) names.push(dev.player_name);
        });
    }
    return names;
}

function _updateAdapterFilter() {
    var sel = document.getElementById('adapter-filter-sel');
    if (!sel || !lastDevices) return;
    var adapters = [];
    lastDevices.forEach(function(dev) {
        var info = _getAdapterDisplayInfo(dev);
        if (!info.id) return;
        if (!adapters.some(function(entry) { return entry.id === info.id; })) {
            adapters.push({
                id: info.id,
                label: info.label || info.id,
                title: info.title || info.label || info.id
            });
        }
    });
    var cur = sel.value;
    sel.innerHTML = '<option value="">All adapters</option>';
    adapters.sort(function(a, b) {
        return String(a.label || a.id).localeCompare(String(b.label || b.id), undefined, {numeric: true, sensitivity: 'base'});
    }).forEach(function(adapter) {
        var opt = document.createElement('option');
        opt.value = adapter.id;
        opt.textContent = '\u{1F50C} ' + adapter.label;
        opt.title = adapter.title;
        sel.appendChild(opt);
    });
    sel.style.display = adapters.length ? '' : 'none';
    if (cur && adapters.some(function(adapter) { return adapter.id === cur; })) {
        sel.value = cur;
    } else if (cur) {
        sel.value = '';
    }
}

function _updateGroupFilter() {
    var sel = document.getElementById('group-filter-sel');
    if (!sel || !lastDevices) return;
    // Collect unique group names from current devices
    var groups = [];
    lastDevices.forEach(function(dev) {
        var g = dev.group_name || dev.group_id || '';
        if (g && groups.indexOf(g) === -1) groups.push(g);
    });
    // Rebuild options, preserving current selection
    var cur = sel.value;
    sel.innerHTML = '<option value="">All groups</option>';
    groups.sort().forEach(function(g) {
        var opt = document.createElement('option');
        opt.value = g;
        opt.textContent = '\uD83D\uDD17 ' + g.split('-').pop();
        sel.appendChild(opt);
    });
    // Hide/show based on whether any groups exist
    sel.style.display = groups.length ? '' : 'none';
    // Restore selection if group still exists
    if (cur && groups.indexOf(cur) !== -1) {
        sel.value = cur;
    } else if (cur && groups.indexOf(cur) === -1) {
        sel.value = '';
        _groupFilter = '';
    }
}

function onGroupFilterChange(val) {
    _groupFilter = val;
    if (!lastDevices) return;
    lastDevices.forEach(function(dev, i) {
        var g = dev.group_name || dev.group_id || '';
        var inGroup = !val || g === val;
        _groupSelected[i] = inGroup;
        var cb = document.getElementById('dsel-' + i);
        if (cb) cb.checked = inGroup;
    });
    _updateGroupPanel();
}

function _updateGroupPanel() {
    var total = lastDevices ? lastDevices.length : 0;
    var controls = document.getElementById('group-controls');
    var actionBar = document.getElementById('group-action-bar');
    if (!controls) return;
    if (total < 1) {
        controls.style.display = 'none';
        return;
    }
    controls.style.display = 'flex';
    controls.classList.toggle('toolbar-stack--filters-only', total < 2);
    if (actionBar) actionBar.style.display = total < 2 ? 'none' : 'flex';
    _updateAdapterFilter();
    _updateGroupFilter();
    var sel = _getSelectedNames().length;
    var info = document.getElementById('group-select-info');
    if (info) info.textContent = sel === total ? 'All ' + total + ' players' : sel + ' of ' + total + ' selected';
    var allCb = document.getElementById('group-select-all');
    if (allCb) {
        allCb.checked = sel === total;
        allCb.indeterminate = sel > 0 && sel < total;
    }

    // Sync group volume slider to average of active devices (once — don't override while user drags)
    var groupSlider = document.getElementById('group-vol-slider');
    var groupPct = document.getElementById('group-vol-pct');
    if (groupSlider && !groupSlider._userTouched && lastDevices.length > 0) {
        var active = lastDevices.filter(function(d) { return d.bluetooth_connected || d.playing; });
        var src = active.length ? active : lastDevices;
        var avg = Math.round(src.reduce(function(s, d) { return s + (d.volume || 50); }, 0) / src.length);
        groupSlider.value = avg;
        updateSliderFill(groupSlider);
        if (groupPct) groupPct.textContent = String(avg);
    }
}

function onDeviceSelect(i, checked) {
    _groupSelected[i] = checked;
    _updateGroupPanel();
}

function onGroupSelectAll(cb) {
    if (lastDevices) {
        lastDevices.forEach(function(_, i) {
            _groupSelected[i] = cb.checked;
            var dcb = document.getElementById('dsel-' + i);
            if (dcb) dcb.checked = cb.checked;
        });
    }
    _updateGroupPanel();
}

var _groupVolTimer = null;
var _groupVolTouchTimer = null;
function onGroupVolumeInput(val) {
    var pct = document.getElementById('group-vol-pct');
    if (pct) pct.textContent = String(val);
    // Mark slider as user-controlled so auto-sync doesn't override it
    var slider = document.getElementById('group-vol-slider');
    if (slider) {
        slider._userTouched = true;
        updateSliderFill(slider);
    }
    clearTimeout(_groupVolTouchTimer);
    _groupVolTouchTimer = setTimeout(function() {
        if (slider) slider._userTouched = false;
    }, 3000);
    clearTimeout(_groupVolTimer);
    _groupVolTimer = setTimeout(function() {
        var names = _getSelectedNames();
        if (!names.length) return;
        fetch(API_BASE + '/api/volume', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({volume: parseInt(val, 10), player_names: names, group: true})
        });
    }, 300);
}

function onGroupMute() {
    var names = _getSelectedNames();
    if (!names.length) return;
    var btnId = 'group-mute-btn';
    if (_isLocked(btnId)) return;
    _lockBtn(btnId);
    var btn = document.getElementById(btnId);
    var currentlyMuted = btn && btn.classList.contains('muted');
    var muteVal = !currentlyMuted;
    fetch(API_BASE + '/api/mute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mute: muteVal, player_names: names})
    }).then(function(r) { return r.json(); }).then(function() {
        if (btn) {
            btn.innerHTML = muteVal
                ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>'
                : '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>';
            btn.title = muteVal ? 'Unmute All' : 'Mute All';
            btn.className = 'media-btn media-btn--toolbar' + (muteVal ? ' muted' : '');
        }
    }).finally(function() { _unlockBtn(btnId); });
}

function onPauseAll() {
    var btnId = 'group-pause-btn';
    if (_isLocked(btnId)) return;
    _lockBtn(btnId);
    var btn = document.getElementById(btnId);
    var isPaused = btn && btn.classList.contains('paused');
    var action = isPaused ? 'play' : 'pause';
    var names = _getSelectedNames();
    var total = lastDevices ? lastDevices.length : 0;

    var afterPause = function() {
        if (btn) {
            if (action === 'pause') {
                btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
                btn.classList.add('paused');
                btn.title = 'Play All';
            } else {
                btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
                btn.classList.remove('paused');
                btn.title = 'Pause All';
            }
        }
    };

    var done = function() { _unlockBtn(btnId); };

    if (names.length === total) {
        fetch(API_BASE + '/api/pause_all', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: action})
        }).then(function(r) { return r.json(); }).then(afterPause).finally(done);
    } else {
        var calls = names.map(function(name) {
            return fetch(API_BASE + '/api/pause', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: action, player_name: name})
            });
        });
        Promise.all(calls).then(afterPause).finally(done);
    }
}

// ---- Adapter/Status filter and view mode ----
function onAdapterFilterChange(val) {
    filterDeviceCards();
}
function onStatusFilterChange(val) {
    filterDeviceCards();
}
function filterDeviceCards() {
    renderDevicesView();
}

function setViewMode(mode) {
    if (mode !== 'list' && _isMobileListViewForced()) {
        _syncViewModeForViewport();
        return;
    }
    currentViewMode = mode === 'list' ? 'list' : 'grid';
    userPreferredViewMode = currentViewMode;
    _persistViewMode(currentViewMode);
    _applyViewModeButtons(currentViewMode);
    renderDevicesView();
}

function sortListBy(column) {
    if (listSortState.column === column) {
        listSortState.direction = listSortState.direction === 'asc' ? 'desc' : 'asc';
    } else {
        listSortState.column = column;
        listSortState.direction = column === 'name' || column === 'group' || column === 'adapter' ? 'asc' : 'desc';
    }
    renderDevicesView();
}

function toggleListRow(key) {
    var dev = (lastDevices || []).find(function(entry) { return listRowKey(entry) === key; });
    if (_isDeviceDisabled(dev)) return;
    expandedListRowKey = expandedListRowKey === key ? null : key;
    renderDevicesView();
}

function onGroupAction(action) {
    if (action === 'reconnect') {
        lastDevices.forEach(function(dev, idx) {
            if (_groupSelected[idx] !== false && dev.bluetooth_connected === false && dev.bt_management_enabled !== false) {
                btReconnect(idx);
            }
        });
    } else if (action === 'release') {
        lastDevices.forEach(function(dev, idx) {
            if (_groupSelected[idx] !== false && dev.bt_management_enabled !== false) {
                btToggleManagement(idx);
            }
        });
    }
}

function onDevicePause(i, btnId) {
    var dev = lastDevices && lastDevices[i];
    var transportState = _getDeviceTransportState(dev);
    if (!transportState.canTransport) return;
    var pauseBtnId = btnId || 'dbtn-pause-' + i;
    if (_isLocked(pauseBtnId)) return;
    var btn = _lockBtn(pauseBtnId);

    var isPaused = btn && btn.classList.contains('paused');
    var action = isPaused ? 'play' : 'pause';

    // If device has a group_id, pause/play via /api/group/pause (by group_id)
    // so MA propagates the command to all group members correctly.
    // Only use /api/pause for solo players (group_id is null).
    var inGroup = dev && dev.group_id;
    var url = inGroup ? '/api/group/pause' : '/api/pause';
    var body = inGroup
        ? {action: action, group_id: dev.group_id}
        : {action: action, player_name: dev ? dev.player_name : null};

    fetch(API_BASE + url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    }).then(function(r) { return r.json(); }).then(function() {
        if (btn) {
            if (action === 'pause') {
                btn.innerHTML = _playPauseIconHtml(false);
                btn.classList.add('paused');
                btn.title = 'Play';
            } else {
                btn.innerHTML = _playPauseIconHtml(true);
                btn.classList.remove('paused');
                btn.title = 'Pause';
            }
        }
    }).finally(function() { _unlockBtn(pauseBtnId); });
}

// ---- BT Actions (reconnect / pair) ----

async function maQueueCmd(action, value, devIdx) {
    var btnMap = {previous: 'dma-prev-', next: 'dma-next-', shuffle: 'dma-shuffle-', repeat: 'dma-repeat-'};
    var btnId = devIdx != null && btnMap[action] ? btnMap[action] + devIdx : null;
    var dev = devIdx != null && lastDevices && lastDevices[devIdx] ? lastDevices[devIdx] : null;
    var transportState = _getDeviceTransportState(dev);
    if (!transportState.hasQueueControls) return;
    if (btnId && _isLocked(btnId)) return;
    if (btnId) _lockBtn(btnId);

    // Prefer native Sendspin transport when supported
    var nativeAction = action;
    if (action === 'shuffle' && value === undefined) {
        nativeAction = transportState.shuffle ? 'unshuffle' : 'shuffle';
    } else if (action === 'repeat' && typeof value === 'string' && value.indexOf('repeat_') === 0) {
        // maCycleRepeat passes value='repeat_off'/'repeat_all'/'repeat_one' as native action
        nativeAction = value;
        value = undefined;
    }
    if (transportState.hasNativeTransport && transportState.nativeCommands.indexOf(nativeAction) !== -1) {
        try {
            var nativeBody = {action: nativeAction, device_index: devIdx};
            if (value !== undefined) nativeBody.value = value;
            var resp = await fetch(API_BASE + '/api/transport/cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(nativeBody)
            });
            var data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error((data && data.error) || ('HTTP ' + resp.status));
            }
            _applyNativeTransportUiPrediction(dev, nativeAction);
            renderDevicesView();
        } catch (e) {
            showToast('Transport command failed: ' + e.message, 'error');
        } finally {
            if (btnId) _unlockBtn(btnId);
        }
        return;
    }

    var ma = dev && dev.ma_now_playing ? dev.ma_now_playing : {};
    var body = {action: action};
    if (value !== undefined) body.value = value;
    if (dev) {
        var targetId = _getMaQueueTargetId(dev);
        if (targetId) body.syncgroup_id = targetId;
        if (dev.player_id) body.player_id = dev.player_id;
        if (dev.group_id) body.group_id = dev.group_id;
        if (action === 'shuffle' && value === undefined) body.value = !ma.shuffle;
    }

    try {
        var resp = await fetch(API_BASE + '/api/ma/queue/cmd', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        var data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error((data && data.error) || ('HTTP ' + resp.status));
        }
        if (dev && data && data.ma_now_playing) {
            dev.ma_now_playing = data.ma_now_playing;
            renderDevicesView();
        }
        if (data && data.job_id) {
            _pollBackgroundJob(API_BASE + '/api/ma/queue/cmd/result/' + data.job_id, {
                delayMs: 400,
                maxAttempts: 15,
                timeoutMessage: 'Music Assistant command timed out'
            }).then(function(result) {
                if (!result) return;
                if (!result.success) {
                    if (dev && result.ma_now_playing) {
                        dev.ma_now_playing = result.ma_now_playing;
                        renderDevicesView();
                    }
                    showToast('Music Assistant command failed: ' + (result.error || 'Unknown error'), 'error');
                    return;
                }
                if (dev && result.ma_now_playing) {
                    dev.ma_now_playing = result.ma_now_playing;
                    renderDevicesView();
                }
            }).catch(function(err) {
                console.warn('MA queue cmd result poll failed:', err);
            });
        }
    } catch (err) {
        console.warn('MA queue cmd failed:', err);
        showToast('Music Assistant command failed: ' + (err && err.message ? err.message : 'Unknown error'), 'error');
    }
    finally { if (btnId) _unlockBtn(btnId); }
}

function maCycleRepeat(devIdx) {
    var dev = (devIdx != null && lastDevices && lastDevices[devIdx]) || {};
    var transportState = _getDeviceTransportState(dev);
    var rm = transportState.repeat;
    // Native transport uses repeat_off/repeat_all/repeat_one actions
    if (transportState.hasNativeTransport) {
        var nativeAction = rm === 'off' ? 'repeat_all' : rm === 'all' ? 'repeat_one' : 'repeat_off';
        if (transportState.nativeCommands.indexOf(nativeAction) !== -1) {
            maQueueCmd('repeat', nativeAction, devIdx);
            return;
        }
    }
    var next = rm === 'off' ? 'all' : rm === 'all' ? 'one' : 'off';
    maQueueCmd('repeat', next, devIdx);
}

async function btReconnect(i) {
    var dev = lastDevices && lastDevices[i];
    if (_isDeviceDisabled(dev)) return {success: false, message: 'Device is disabled'};
    var playerName = dev ? dev.player_name : null;
    var btn = document.getElementById('dbtn-reconnect-' + i);
    var pairBtn = document.getElementById('dbtn-pair-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
    var result = {success: false, message: 'Reconnect failed'};
    if (btn) btn.disabled = true;
    if (pairBtn) pairBtn.disabled = true;
    if (status) status.textContent = '&#8635; Reconnecting\u2026';
    try {
        var resp = await fetch(API_BASE + '/api/bt/reconnect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName})
        });
        var d = await resp.json();
        var msg = d.success ? '\u2713 ' + (d.message || 'Reconnect started') : '\u2717 ' + (d.error || 'Failed');
        result = {success: !!d.success, message: d.message || d.error || 'Reconnect finished'};
        if (status) status.textContent = msg;
        showToast(msg, d.success ? 'success' : 'error');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
        showToast('\u2717 Reconnect error', 'error');
        result = {success: false, message: e && e.message ? e.message : 'Reconnect error'};
    }
    setTimeout(function() {
        if (btn) btn.disabled = false;
        if (pairBtn) pairBtn.disabled = false;
        if (status) status.textContent = '';
    }, 8000);
    return result;
}

async function btPairConfiguredDevice(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return {success: false, message: 'Device not found'};
    var playerName = dev.player_name || null;
    var displayName = playerName || ('Device ' + (i + 1));
    if (!confirm('Put "' + displayName + '" into pairing mode, then click OK.\n\nThis will re-pair, trust, and reconnect the device (~25 s).')) {
        return {success: false, message: 'Pairing cancelled'};
    }
    var status = document.getElementById('dbt-action-status-' + i);
    var result = {success: false, message: 'Re-pair failed'};
    if (status) status.textContent = '\u21BB Re-pairing\u2026';
    try {
        var resp = await fetch(API_BASE + '/api/bt/pair', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName})
        });
        var data = await resp.json();
        var msg = data.success ? '\u2713 ' + (data.message || 'Pairing started') : '\u2717 ' + (data.error || 'Failed');
        result = {success: !!data.success, message: data.message || data.error || 'Re-pair finished'};
        if (status) status.textContent = msg;
        showToast(msg, data.success ? 'success' : 'error');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
        result = {success: false, message: e && e.message ? e.message : 'Re-pair error'};
        showToast('\u2717 Re-pair error', 'error');
    }
    setTimeout(function() {
        if (status) status.textContent = '';
    }, 8000);
    return result;
}

async function btToggleManagement(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return {success: false, message: 'Device not found'};
    if (_isDeviceDisabled(dev)) return {success: false, message: 'Device is disabled'};
    var playerName = dev.player_name || null;
    var newEnabled = dev.bt_management_enabled === false;  // toggle
    var btn = document.getElementById('dbtn-release-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
    var result = {success: false, message: 'Bluetooth management update failed'};
    if (btn) btn.disabled = true;
    if (status) status.textContent = newEnabled ? '\u21BB Reclaiming\u2026' : '\u21BB Releasing\u2026';
    try {
        var resp = await fetch(API_BASE + '/api/bt/management', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName, enabled: newEnabled})
        });
        var d = await resp.json();
        if (d.success) {
            lastDevices[i].bt_management_enabled = newEnabled;
            // Update button immediately — don't wait for SSE
            if (btn) {
                _setReleaseActionButtonState(btn, newEnabled);
            }
            // Disable Reconnect while released
            var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
            if (reconnBtn) reconnBtn.disabled = newEnabled ? false : true;
        }
        if (status) status.textContent = d.success ? '\u2713 ' + d.message : '\u2717 ' + (d.error || 'Failed');
        result = {success: !!d.success, message: d.message || d.error || 'Bluetooth management updated'};
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
        result = {success: false, message: e && e.message ? e.message : 'Bluetooth management error'};
    }
    if (btn) btn.disabled = false;
    setTimeout(function() { if (status) status.textContent = ''; }, 4000);
    return result;
}

async function wakeDevice(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
    var playerName = dev.player_name || null;
    var btn = document.getElementById('dbtn-wake-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
    if (btn) btn.disabled = true;
    if (status) status.textContent = '\u21BB Waking\u2026';
    try {
        var resp = await fetch(API_BASE + '/api/bt/wake', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName})
        });
        var d = await resp.json();
        if (status) status.textContent = d.success ? '\u2713 ' + d.message : '\u2717 ' + (d.error || 'Failed');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    if (btn) btn.disabled = false;
    setTimeout(function() { if (status) status.textContent = ''; }, 4000);
}

async function btToggleStandby(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
    var isStandby = !!dev.bt_standby;
    var playerName = dev.player_name || null;
    var btn = document.getElementById('dbtn-standby-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
    if (btn) btn.disabled = true;
    if (status) status.textContent = isStandby ? '\u21BB Waking\u2026' : '\u21BB Entering standby\u2026';
    try {
        var endpoint = isStandby ? '/api/bt/wake' : '/api/bt/standby';
        var resp = await fetch(API_BASE + endpoint, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName})
        });
        var d = await resp.json();
        if (status) status.textContent = d.success ? '\u2713 ' + d.message : '\u2717 ' + (d.error || 'Failed');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    if (btn) btn.disabled = false;
    setTimeout(function() { if (status) status.textContent = ''; }, 4000);
}

// ---- Device enabled toggle (used by config checkbox and dashboard Disable button) ----

async function toggleDeviceEnabled(deviceRef, enabled) {
    var dev = deviceRef && typeof deviceRef === 'object' ? deviceRef : null;
    var playerName = dev ? (dev.player_name || null) : deviceRef;
    var wrap = _findBtConfigWrapByIdentity(playerName, dev && (dev.bluetooth_mac || dev.mac));
    var enabledCb = wrap ? wrap.querySelector('.bt-enabled') : null;
    var previousConfigEnabled = enabledCb ? enabledCb.checked : null;
    var previousDeviceEnabled = dev ? dev.enabled !== false : null;
    if (wrap) {
        _setBtConfigWrapEnabledState(wrap, enabled);
        refreshBtDeviceRowsRuntime();
    }
    try {
        var resp = await fetch(API_BASE + '/api/device/enabled', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName, enabled: enabled})
        });
        var d = await resp.json();
        if (d.success) {
            if (dev) dev.enabled = enabled !== false;
            refreshBtDeviceRowsRuntime();
            showToast(d.message, 'success');
        } else {
            if (wrap && previousConfigEnabled !== null) _setBtConfigWrapEnabledState(wrap, previousConfigEnabled);
            if (dev) {
                dev.enabled = previousDeviceEnabled;
                renderDevicesView();
            }
            refreshBtDeviceRowsRuntime();
            showToast(d.error || 'Failed', 'error');
        }
    } catch (e) {
        if (wrap && previousConfigEnabled !== null) _setBtConfigWrapEnabledState(wrap, previousConfigEnabled);
        if (dev) {
            dev.enabled = previousDeviceEnabled;
            renderDevicesView();
        }
        refreshBtDeviceRowsRuntime();
        showToast('Error: ' + e.message, 'error');
    }
}

function confirmDisableDevice(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev || _isDeviceDisabled(dev)) return;
    var name = dev.player_name || 'Device ' + (i + 1);
    if (!confirm('Disable "' + name + '"?\n\nThe device will be skipped on next bridge restart.\nYou can re-enable it from the config page.')) return;
    var btn = document.getElementById('dbtn-disable-' + i);
    if (btn) btn.disabled = true;
    dev.enabled = false;
    _groupSelected[i] = false;
    if (expandedListRowKey === listRowKey(dev)) expandedListRowKey = null;
    renderDevicesView();
    toggleDeviceEnabled(dev, false);
}

function toggleAutoRefresh(forceState) {
    autoRefreshLogs = typeof forceState === 'boolean' ? forceState : !autoRefreshLogs;
    var btn = document.getElementById('auto-refresh-btn');
    var checkbox = document.getElementById('auto-refresh-toggle');
    if (checkbox) checkbox.checked = autoRefreshLogs;
    if (autoRefreshLogs) {
        if (btn) {
            btn.textContent = 'Auto-Refresh: On';
            btn.classList.add('auto-on');
        }
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = setInterval(refreshLogs, 2000);
        refreshLogs();
    } else {
        if (btn) {
            btn.textContent = 'Auto-Refresh: Off';
            btn.classList.remove('auto-on');
        }
        clearInterval(autoRefreshInterval);
    }
}

// ---- BT Device Table ----

async function loadBtAdapters(options) {
    options = options || {};
    try {
        var resp = await fetch(API_BASE + '/api/bt/adapters');
        var data = await resp.json();
        btAdapters = (data.adapters || []).map(function(adapter) {
            return Object.assign({}, adapter, {
                detectedName: adapter.name || '',
                customName: '',
                manual: false,
            });
        });
    } catch (_) { btAdapters = []; }
    // Merge saved adapter overrides and manual entries.
    btManualAdapters.forEach(function(m) {
        var match = btAdapters.find(function(a) {
            return (m.id && a.id === m.id) ||
                (_normalizeDeviceMac(m.mac) && _normalizeDeviceMac(a.mac) === _normalizeDeviceMac(m.mac));
        });
        if (match) {
            match.customName = m.name || '';
            match.name = m.name || match.detectedName || match.id;
        } else {
            btAdapters.push({
                id: m.id || '',
                mac: m.mac || '',
                name: m.name || '',
                customName: m.name || '',
                detectedName: '',
                manual: true,
            });
        }
    });
    renderAdaptersTable();
    rebuildAdapterDropdowns();
    _renderBtScanAdapterOptions();
    _syncBtScanControls();
    if (!options.skipHaAreaRefresh && _isIngress()) {
        await _maybeLoadHaAreaCatalog({silent: true});
    }
}

// ---- Adapter panel ----

function escHtmlAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

function _createEmptyHaAreaCatalog() {
    return {
        loaded: false,
        loading: false,
        available: false,
        error: '',
        areas: [],
        bridgeSuggestions: [],
        adapterMatchesByMac: {},
    };
}

_haAreaCatalog = _createEmptyHaAreaCatalog();

function _normalizeHaAreaId(rawAreaId) {
    return String(rawAreaId || '').trim();
}

function _normalizeHaAdapterAreaMap(rawMap) {
    var normalized = {};
    if (!rawMap || typeof rawMap !== 'object') return normalized;
    Object.keys(rawMap).forEach(function(rawMac) {
        var mac = _normalizeDeviceMac(rawMac);
        var entry = rawMap[rawMac];
        if (!mac || !entry || typeof entry !== 'object') return;
        var areaId = _normalizeHaAreaId(entry.area_id);
        if (!areaId) return;
        var areaName = String(entry.area_name || '').trim();
        normalized[mac] = {area_id: areaId, area_name: areaName};
    });
    return normalized;
}

function _setHaAreaCatalog(rawCatalog) {
    var catalog = _createEmptyHaAreaCatalog();
    var source = rawCatalog || {};
    catalog.loaded = !!source.loaded;
    catalog.loading = !!source.loading;
    catalog.error = String(source.error || '').trim();
    catalog.areas = Array.isArray(source.areas) ? source.areas.filter(function(area) {
        return area && _normalizeHaAreaId(area.area_id) && String(area.name || '').trim();
    }).map(function(area) {
        return {area_id: _normalizeHaAreaId(area.area_id), name: String(area.name || '').trim()};
    }) : [];
    catalog.bridgeSuggestions = Array.isArray(source.bridge_name_suggestions)
        ? source.bridge_name_suggestions.filter(function(suggestion) {
            return suggestion && String(suggestion.value || '').trim();
        }).map(function(suggestion) {
            return {
                area_id: _normalizeHaAreaId(suggestion.area_id),
                label: String(suggestion.label || suggestion.value || '').trim(),
                value: String(suggestion.value || '').trim(),
            };
        })
        : catalog.areas.map(function(area) {
            return {area_id: area.area_id, label: area.name, value: area.name};
        });
    if (!catalog.bridgeSuggestions.length) {
        catalog.bridgeSuggestions = catalog.areas.map(function(area) {
            return {area_id: area.area_id, label: area.name, value: area.name};
        });
    }
    if (Array.isArray(source.adapter_matches)) {
        source.adapter_matches.forEach(function(match) {
            var mac = _normalizeDeviceMac(match && match.adapter_mac);
            if (!mac) return;
            catalog.adapterMatchesByMac[mac] = {
                adapter_id: String(match.adapter_id || '').trim(),
                adapter_mac: mac,
                matched_area_id: _normalizeHaAreaId(match.matched_area_id),
                matched_area_name: String(match.matched_area_name || '').trim(),
                match_source: String(match.match_source || '').trim(),
                match_confidence: String(match.match_confidence || '').trim(),
                matched_device_name: String(match.matched_device_name || '').trim(),
                suggested_name: String(match.suggested_name || match.matched_area_name || '').trim(),
            };
        });
    }
    catalog.available = catalog.areas.length > 0;
    _haAreaCatalog = catalog;
    _renderBridgeNameHaAssist();
    _updateAdaptersHaAssistSummary();
}

function _serializeAdaptersForHaAreaLookup() {
    return btAdapters.map(function(adapter) {
        return {
            id: adapter.id || '',
            mac: _normalizeDeviceMac(adapter.mac),
            name: adapter.customName || adapter.name || adapter.detectedName || '',
        };
    }).filter(function(adapter) {
        return adapter.id || adapter.mac || adapter.name;
    });
}

function _getHaAreaName(areaId) {
    var target = _normalizeHaAreaId(areaId);
    if (!target || !_haAreaCatalog || !Array.isArray(_haAreaCatalog.areas)) return '';
    for (var i = 0; i < _haAreaCatalog.areas.length; i++) {
        if (_normalizeHaAreaId(_haAreaCatalog.areas[i].area_id) === target) return _haAreaCatalog.areas[i].name || '';
    }
    return '';
}

function _getSavedHaAreaMatch(adapterMac) {
    var mac = _normalizeDeviceMac(adapterMac);
    return mac ? (_haAdapterAreaMap[mac] || null) : null;
}

function _getSuggestedHaAreaMatch(adapterMac) {
    var mac = _normalizeDeviceMac(adapterMac);
    return mac ? ((_haAreaCatalog.adapterMatchesByMac || {})[mac] || null) : null;
}

function _buildHaAreaOptionsHtml(selectedAreaId) {
    var target = _normalizeHaAreaId(selectedAreaId);
    var html = '<option value="">No HA area</option>';
    (_haAreaCatalog.areas || []).forEach(function(area) {
        var areaId = _normalizeHaAreaId(area.area_id);
        html += '<option value="' + escHtmlAttr(areaId) + '"' + (areaId === target ? ' selected' : '') + '>' +
            escHtml(area.name || areaId) + '</option>';
    });
    return html;
}

function _isHaAreaAssistEnabled() {
    return !!_haAreaAssistEnabled;
}

function _setHaAreaAssistEnabled(enabled) {
    _haAreaAssistEnabled = !!enabled;
}

function _onHaAreaAssistToggleChange(enabled) {
    _setHaAreaAssistEnabled(enabled);
    _renderBridgeNameHaAssist();
    _updateAdaptersHaAssistSummary();
    renderAdaptersTable();
    if (_haAreaAssistEnabled && _isIngress() && !_haAreaCatalog.loading && !_haAreaCatalog.loaded) {
        _maybeLoadHaAreaCatalog({silent: true});
    }
    _recomputeConfigDirtyState();
    return false;
}

function _renderBridgeNameHaAssist() {
    var container = document.getElementById('bridge-name-ha-assist');
    var bridgeInput = document.querySelector('[name="BRIDGE_NAME"]');
    if (!container || !bridgeInput) return;

    if (!_isIngress() || !_isHaAreaAssistEnabled()) {
        container.hidden = true;
        container.innerHTML = '';
        return;
    }

    if (_haAreaCatalog.loading) {
        container.hidden = false;
        container.className = 'ha-assist ha-assist--muted';
        container.innerHTML = '<span class="ha-assist-copy">Home Assistant areas</span><div class="ha-assist-note">Loading area suggestions…</div>';
        return;
    }

    if (!_haAreaCatalog.available || !_haAreaCatalog.bridgeSuggestions.length) {
        if (!_haAreaCatalog.loaded) {
            container.hidden = true;
            container.innerHTML = '';
            return;
        }
        container.hidden = false;
        container.className = 'ha-assist ha-assist--muted';
        container.innerHTML = '<span class="ha-assist-copy">Home Assistant areas</span><div class="ha-assist-note">' +
            escHtml(_haAreaCatalog.error || 'HA area suggestions are unavailable right now.') + '</div>';
        return;
    }

    var hasBridgeName = !!String(bridgeInput.value || '').trim();
    var optionsHtml = '<option value="">Choose Home Assistant area…</option>';
    _haAreaCatalog.bridgeSuggestions.forEach(function(suggestion) {
        optionsHtml += '<option value="' + escHtmlAttr(suggestion.value) + '">' +
            escHtml(suggestion.label || suggestion.value) + '</option>';
    });
    container.hidden = false;
    container.className = 'ha-assist';
    container.innerHTML =
        '<span class="ha-assist-copy">Home Assistant areas</span>' +
        '<div class="ha-assist-controls">' +
            '<select id="bridge-name-ha-select">' + optionsHtml + '</select>' +
            '<button type="button" class="btn btn-sm btn-secondary" id="bridge-name-ha-apply" disabled>Use area name</button>' +
        '</div>' +
        '<div class="ha-assist-note">' +
            (hasBridgeName
                ? 'Suggestions stay manual — nothing overwrites the current bridge name until you apply it.'
                : 'The bridge name is empty — you can import an HA area name with one click.') +
        '</div>';

    var select = document.getElementById('bridge-name-ha-select');
    var applyBtn = document.getElementById('bridge-name-ha-apply');
    if (!select || !applyBtn) return;
    select.addEventListener('change', function() {
        applyBtn.disabled = !String(select.value || '').trim();
    });
    applyBtn.addEventListener('click', function() {
        var value = String(select.value || '').trim();
        if (!value) return;
        bridgeInput.value = value;
        bridgeInput.dispatchEvent(new Event('input', {bubbles: true}));
        bridgeInput.dispatchEvent(new Event('change', {bubbles: true}));
        _recomputeConfigDirtyState();
        _renderBridgeNameHaAssist();
    });
}

function _updateAdaptersHaAssistSummary() {
    var summary = document.getElementById('adapters-ha-assist-summary');
    if (!summary) return;
    if (!_isIngress() || !_isHaAreaAssistEnabled()) {
        summary.hidden = true;
        summary.innerHTML = '';
        return;
    }
    if (_haAreaCatalog.loading) {
        summary.hidden = false;
        summary.className = 'ha-assist ha-assist--muted';
        summary.innerHTML = 'Loading Home Assistant area suggestions…';
        return;
    }
    if (!_haAreaCatalog.loaded) {
        summary.hidden = true;
        summary.innerHTML = '';
        return;
    }
    if (!_haAreaCatalog.available) {
        summary.hidden = false;
        summary.className = 'ha-assist ha-assist--muted';
        summary.innerHTML = escHtml(_haAreaCatalog.error || 'HA area suggestions are unavailable right now.');
        return;
    }
    var matchCount = Object.keys(_haAreaCatalog.adapterMatchesByMac || {}).length;
    summary.hidden = false;
    summary.className = 'ha-assist';
    summary.innerHTML = 'Loaded ' + _haAreaCatalog.areas.length + ' Home Assistant area' +
        (_haAreaCatalog.areas.length === 1 ? '' : 's') +
        (matchCount ? ' with exact adapter MAC suggestions for ' + matchCount + ' adapter' + (matchCount === 1 ? '' : 's') + '.' : '.');
}

function _buildAdapterHaAssistHtml(adapter) {
    if (!_isHaAreaAssistEnabled()) return '';
    if (!_haAreaCatalog.available || !_haAreaCatalog.areas.length) return '';
    var saved = _getSavedHaAreaMatch(adapter.mac);
    var suggestion = _getSuggestedHaAreaMatch(adapter.mac);
    var selectedAreaId = _normalizeHaAreaId(saved && saved.area_id);
    var selectedAreaName = _getHaAreaName(selectedAreaId) || String((saved && saved.area_name) || '').trim();
    var suggestionAreaId = _normalizeHaAreaId(suggestion && suggestion.matched_area_id);
    var suggestionAreaName = String((suggestion && suggestion.matched_area_name) || '').trim();
    var meta = '';
    if (suggestionAreaId && suggestionAreaName) {
        meta = 'Suggested by HA device registry: ' + suggestionAreaName;
        if (suggestion && suggestion.matched_device_name) meta += ' (' + suggestion.matched_device_name + ')';
    } else if (selectedAreaId && selectedAreaName) {
        meta = 'Saved HA area: ' + selectedAreaName;
    }
    return '<div class="adapter-ha-assist">' +
        '<span class="adapter-ha-assist-copy">Home Assistant area</span>' +
        '<div class="adapter-ha-controls">' +
            '<select class="adp-ha-area">' + _buildHaAreaOptionsHtml(selectedAreaId) + '</select>' +
            '<button type="button" class="btn btn-sm btn-secondary adp-ha-apply-name"' +
                (selectedAreaId ? '' : ' disabled') + '>Use area name</button>' +
            (suggestionAreaId && suggestionAreaId !== selectedAreaId
                ? '<button type="button" class="btn btn-sm btn-ghost adp-ha-use-suggestion" data-area-id="' +
                    escHtmlAttr(suggestionAreaId) + '">Use suggested area</button>'
                : '') +
        '</div>' +
        '<div class="adapter-ha-meta adp-ha-area-meta">' + escHtml(meta) + '</div>' +
    '</div>';
}

function _updateAdapterHaAssistState(row) {
    var select = row.querySelector('.adp-ha-area');
    var applyNameBtn = row.querySelector('.adp-ha-apply-name');
    var meta = row.querySelector('.adp-ha-area-meta');
    if (!select) return;
    var areaId = _normalizeHaAreaId(select.value);
    var areaName = _getHaAreaName(areaId);
    if (applyNameBtn) applyNameBtn.disabled = !areaId || !areaName;
    if (meta) {
        var suggestion = _getSuggestedHaAreaMatch(row.classList.contains('manual')
            ? (((row.querySelector('.adp-mac') || {}).value) || '')
            : (row.dataset.adapterMac || ''));
        if (areaId && areaName) {
            meta.textContent = 'Selected area: ' + areaName;
        } else if (suggestion && suggestion.matched_area_name) {
            meta.textContent = suggestion.matched_device_name
                ? 'Suggested by HA device registry: ' + suggestion.matched_area_name + ' (' + suggestion.matched_device_name + ')'
                : 'Suggested by HA device registry: ' + suggestion.matched_area_name;
        } else {
            meta.textContent = '';
        }
    }
}

function _bindAdapterHaAssist(row) {
    var select = row.querySelector('.adp-ha-area');
    if (!select) return;
    select.addEventListener('change', function() {
        _updateAdapterHaAssistState(row);
        syncManualAdapters();
        _recomputeConfigDirtyState();
    });
    var applyNameBtn = row.querySelector('.adp-ha-apply-name');
    if (applyNameBtn) {
        applyNameBtn.addEventListener('click', function() {
            var areaName = _getHaAreaName(select.value);
            var nameInput = row.querySelector('.adp-name');
            if (!areaName || !nameInput) return;
            nameInput.value = areaName;
            syncManualAdapters();
            _recomputeConfigDirtyState();
        });
    }
    var useSuggestionBtn = row.querySelector('.adp-ha-use-suggestion');
    if (useSuggestionBtn) {
        useSuggestionBtn.addEventListener('click', function() {
            select.value = useSuggestionBtn.dataset.areaId || '';
            select.dispatchEvent(new Event('change', {bubbles: true}));
        });
    }
    _updateAdapterHaAssistState(row);
}

async function _maybeLoadHaAreaCatalog(options) {
    options = options || {};
    if (!_isIngress() || !_isHaAreaAssistEnabled()) {
        _setHaAreaCatalog({loaded: true, error: ''});
        renderAdaptersTable();
        return;
    }
    if (_haAreaCatalog.loading) return;

    _setHaAreaCatalog({loaded: _haAreaCatalog.loaded, loading: true, error: ''});
    renderAdaptersTable();

    var haToken = await _getHaAccessToken();
    if (!haToken) {
        _setHaAreaCatalog({loaded: true, error: 'Home Assistant access token unavailable in this session.'});
        renderAdaptersTable();
        return;
    }

    try {
        var resp = await fetch(API_BASE + '/api/ha/areas', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                ha_token: haToken,
                include_devices: true,
                adapters: _serializeAdaptersForHaAreaLookup(),
            }),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (!resp.ok || !data.success) {
            _setHaAreaCatalog({
                loaded: true,
                error: String(data.error || 'Could not load Home Assistant areas right now.'),
            });
            renderAdaptersTable();
            return;
        }
        _setHaAreaCatalog(Object.assign({}, data, {loaded: true}));
    } catch (err) {
        console.warn('HA area lookup failed:', err);
        _setHaAreaCatalog({loaded: true, error: 'Could not reach Home Assistant area registry right now.'});
    }
    renderAdaptersTable();
}

function renderAdaptersTable() {
    var el = document.getElementById('adapters-table');
    if (!el) return;
    el.innerHTML = '';
    btAdapters.forEach(function(a) {
        if (a.manual) {
            el.appendChild(buildManualRow(a.id, a.mac, a.name, a._configDirtyKey || ''));
        } else {
            var row = document.createElement('div');
            row.className = 'adapter-row detected';
            row.dataset.adapterId = a.id || '';
            row.dataset.adapterMac = a.mac || '';
            row.dataset.configDirtyKey = a._configDirtyKey || _nextConfigDirtyKey('adapter');
            a._configDirtyKey = row.dataset.configDirtyKey;
            var customName = a.customName || '';
            var placeholderName = a.detectedName || a.name || a.id || '';
            row.innerHTML =
                '<span>' + escHtml(a.id) + '</span>' +
                '<span class="mono">' + escHtml(a.mac) + '</span>' +
                '<input type="text" class="adp-name" placeholder="' + escHtmlAttr(placeholderName) + '" value="' + escHtmlAttr(customName) + '"' +
                    ' title="Custom adapter name">' +
                '<span class="dot ' + (a.powered ? 'green' : 'grey') + '" title="' + (a.powered ? 'Powered on' : 'Powered off') + '">\u25cf</span>' +
                '<span class="adapter-power-btns">' +
                  '<button type="button" class="btn-bt-action btn-adp-reboot" title="Reboot adapter" data-adapter="' + escHtmlAttr(a.mac) + '">\u21bb Reboot</button>' +
                '</span>' +
                _buildAdapterHaAssistHtml(a);
            row.querySelector('.adp-name').addEventListener('blur', syncManualAdapters);
            row.querySelector('.btn-adp-reboot').addEventListener('click', function() { rebootAdapter(a.mac); });
            _bindAdapterHaAssist(row);
            el.appendChild(row);
        }
    });
    _updateAdaptersHaAssistSummary();
}

function buildManualRow(id, mac, name, dirtyKey) {
    var row = document.createElement('div');
    row.className = 'adapter-row manual';
    row.dataset.adapterId = id || '';
    row.dataset.adapterMac = mac || '';
    row.dataset.configDirtyKey = dirtyKey || _nextConfigDirtyKey('adapter');
    row.innerHTML =
        '<input type="text" class="adp-id" placeholder="hci2" value="' + escHtmlAttr(id) + '">' +
        '<input type="text" class="adp-mac mono" placeholder="AA:BB:CC:DD:EE:FF" value="' + escHtmlAttr(mac) + '">' +
        '<input type="text" class="adp-name" placeholder="Display name" value="' + escHtmlAttr(name) + '">' +
        '<span class="dot grey">\u25cf</span>' +
        '<button type="button" class="btn-remove-adapter">\u00d7</button>' +
        _buildAdapterHaAssistHtml({id: id || '', mac: mac || ''});
    ['adp-id', 'adp-mac', 'adp-name'].forEach(function(cls) {
        row.querySelector('.' + cls).addEventListener('blur', syncManualAdapters);
    });
    row.querySelector('.btn-remove-adapter').addEventListener('click', function() {
        row.remove();
        syncManualAdapters();
    });
    _bindAdapterHaAssist(row);
    return row;
}

function addManualAdapterRow(id, mac, name, dirtyKey) {
    var el = document.getElementById('adapters-table');
    if (!el) return;
    el.appendChild(buildManualRow(id || '', mac || '', name || '', dirtyKey || ''));
}

function _ensureEmptyManualAdapterRow() {
    var rows = document.querySelectorAll('#adapters-table .adapter-row.manual');
    for (var i = 0; i < rows.length; i++) {
        var row = rows[i];
        var idInput = row.querySelector('.adp-id');
        var macInput = row.querySelector('.adp-mac');
        var nameInput = row.querySelector('.adp-name');
        var hasValue = (idInput && idInput.value.trim()) ||
            (macInput && macInput.value.trim()) ||
            (nameInput && nameInput.value.trim());
        if (!hasValue) return row;
    }
    addManualAdapterRow('', '', '');
    rows = document.querySelectorAll('#adapters-table .adapter-row.manual');
    return rows.length ? rows[rows.length - 1] : null;
}

function rebootAdapter(adapterMac) {
    var btn = document.querySelector('.btn-adp-reboot[data-adapter="' + adapterMac + '"]');
    if (btn) { btn.disabled = true; btn.textContent = '\u21bb Rebooting\u2026'; }
    fetch('/api/bt/adapter/power', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({adapter: adapterMac, power: false})
    })
    .then(function() {
        return new Promise(function(resolve) { setTimeout(resolve, 3000); });
    })
    .then(function() {
        return fetch('/api/bt/adapter/power', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({adapter: adapterMac, power: true})
        });
    })
    .then(function(r) { return r.json(); })
    .then(function() {
        if (btn) { btn.disabled = false; btn.textContent = '\u21bb Reboot'; }
    })
    .catch(function() {
        if (btn) { btn.disabled = false; btn.textContent = '\u21bb Reboot'; }
    });
}

function syncManualAdapters() {
    var savedAdapters = [];
    btAdapters.filter(function(a) { return !a.manual; }).forEach(function(adapter) {
        adapter.customName = '';
        adapter.name = adapter.detectedName || adapter.id || '';
    });
    document.querySelectorAll('#adapters-table .adapter-row').forEach(function(row) {
        var isManual = row.classList.contains('manual');
        var id = isManual ? row.querySelector('.adp-id').value.trim() : (row.dataset.adapterId || '').trim();
        var mac = isManual ? row.querySelector('.adp-mac').value.trim() : (row.dataset.adapterMac || '').trim();
        var name = (row.querySelector('.adp-name') || {}).value ? row.querySelector('.adp-name').value.trim() : '';
        if (isManual) {
            row.dataset.adapterId = id;
            row.dataset.adapterMac = mac;
            if (id || mac) savedAdapters.push({id: id, mac: mac, name: name});
            return;
        }
        var match = _findAdapterRecord(id, mac);
        if (match) {
            match.customName = name;
            match.name = name || match.detectedName || match.id || '';
        }
        if (id && name) savedAdapters.push({id: id, mac: mac, name: name});
    });
    btManualAdapters = savedAdapters;
    btAdapters = btAdapters.filter(function(a) { return !a.manual; });
    btManualAdapters.forEach(function(m) {
        var match = btAdapters.find(function(a) {
            return (m.id && a.id === m.id) ||
                (_normalizeDeviceMac(m.mac) && _normalizeDeviceMac(a.mac) === _normalizeDeviceMac(m.mac));
        });
        if (!match) {
            btAdapters.push({
                id: m.id || '',
                mac: m.mac || '',
                name: m.name || '',
                customName: m.name || '',
                detectedName: '',
                manual: true,
            });
        }
    });
    rebuildAdapterDropdowns();
    renderDevicesView();
}

function _collectPersistedAdaptersFromDom() {
    var savedAdapters = [];
    document.querySelectorAll('#adapters-table .adapter-row').forEach(function(row) {
        var isManual = row.classList.contains('manual');
        var id = isManual ? ((row.querySelector('.adp-id') || {}).value || '').trim() : (row.dataset.adapterId || '').trim();
        var mac = isManual ? ((row.querySelector('.adp-mac') || {}).value || '').trim() : (row.dataset.adapterMac || '').trim();
        var name = ((row.querySelector('.adp-name') || {}).value || '').trim();
        if (isManual) {
            if (id || mac) {
                var manualEntry = {id: id, mac: mac};
                if (name) manualEntry.name = name;
                savedAdapters.push(manualEntry);
            }
            return;
        }
        if (id && name) {
            var detectedEntry = {id: id, mac: mac};
            detectedEntry.name = name;
            savedAdapters.push(detectedEntry);
        }
    });
    return savedAdapters;
}

function _collectHaAdapterAreaMapFromDom() {
    var savedMap = {};
    document.querySelectorAll('#adapters-table .adapter-row').forEach(function(row) {
        var areaSelect = row.querySelector('.adp-ha-area');
        if (!areaSelect) return;
        var areaId = _normalizeHaAreaId(areaSelect.value);
        var mac = row.classList.contains('manual')
            ? _normalizeDeviceMac((((row.querySelector('.adp-mac') || {}).value) || ''))
            : _normalizeDeviceMac(row.dataset.adapterMac || '');
        if (!mac || !areaId) return;
        var areaName = _getHaAreaName(areaId) || String((areaSelect.options[areaSelect.selectedIndex] || {}).text || '').trim();
        savedMap[mac] = {area_id: areaId};
        if (areaName) savedMap[mac].area_name = areaName;
    });
    return savedMap;
}

function rebuildAdapterDropdowns() {
    document.querySelectorAll('#bt-devices-table .bt-adapter').forEach(function(sel) {
        var current = sel.value;
        sel.innerHTML = btAdapterOptions(current);
    });
}

// ---- BT Device Table (adapter dropdown) ----

function btAdapterOptions(selected) {
    var opts = '<option value="">default</option>';
    btAdapters.forEach(function(a) {
        var primary = a.customName || a.name || a.id;
        var label = primary === a.id
            ? a.id + (a.mac ? ' \u2014 ' + a.mac : '')
            : primary + ' \u2014 ' + a.id + (a.mac ? ' \u00b7 ' + a.mac : '');
        opts += '<option value="' + a.id + '"' +
            (selected === a.id ? ' selected' : '') + '>' + label + '</option>';
    });
    return opts;
}

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort, enabled, preferredFormat, keepaliveInterval, roomName, roomId, handoffMode, idleDisconnectMinutes) {
    var tbody = document.getElementById('bt-devices-table');
    var wrap = document.createElement('div');
    wrap.className = 'bt-device-wrap';
    wrap.dataset.configDirtyKey = _nextConfigDirtyKey('bt-device');
    var isEnabled = enabled !== false;

    var row = document.createElement('div');
    row.className = 'bt-device-row';
    var delayVal = (delay !== undefined && delay !== null && delay !== '') ? delay : 0;
    var portVal  = (listenPort !== undefined && listenPort !== null && listenPort !== '') ? listenPort : '';
    var fmtVal   = (preferredFormat !== undefined && preferredFormat !== null) ? preferredFormat : 'flac:44100:16:2';
    var kaVal = (keepaliveInterval !== undefined && keepaliveInterval !== null && keepaliveInterval !== '') ? parseInt(keepaliveInterval, 10) : 0;
    if (kaVal > 0 && kaVal < 30) kaVal = 30;
    var roomNameVal = String(roomName || '').trim();
    var roomIdVal = String(roomId || '').trim();
    var handoffModeVal = String(handoffMode || 'default').trim().toLowerCase();
    if (handoffModeVal !== 'fast_handoff') handoffModeVal = 'default';
    var idleVal = (idleDisconnectMinutes !== undefined && idleDisconnectMinutes !== null && idleDisconnectMinutes !== '') ? parseInt(idleDisconnectMinutes, 10) : 0;
    if (idleVal < 0) idleVal = 0;
    row.innerHTML =
        '<div class="bt-enabled-cell bt-cell" data-label="Enabled"><label class="bt-switch" title="Enable or disable device">' +
            '<input type="checkbox" class="bt-enabled"' + (isEnabled ? ' checked' : '') + '>' +
            '<span class="bt-switch-track"></span>' +
        '</label></div>' +
        '<div class="bt-name-field bt-cell" data-label="Player name">' +
            '<input type="text" placeholder="Player Name" class="bt-name" aria-label="Player name" value="' +
                escHtmlAttr(name || '') + '">' +
            '<button type="button" class="bt-expand-btn" title="Show advanced settings" aria-label="Show advanced settings" aria-expanded="false">' +
                '<span class="bt-expand-btn-label">Details</span>' +
                '<span class="bt-expand-btn-icon" aria-hidden="true">▾</span>' +
            '</button>' +
        '</div>' +
        '<div class="bt-cell bt-cell--mac" data-label="MAC">' +
            '<input type="text" placeholder="AA:BB:CC:DD:EE:FF" class="bt-mac" aria-label="Bluetooth MAC address" value="' +
                escHtmlAttr(mac || '') + '">' +
        '</div>' +
        '<div class="bt-cell bt-cell--adapter" data-label="Adapter">' +
            '<select class="bt-adapter" aria-label="Bluetooth adapter">' + btAdapterOptions(adapter || '') + '</select>' +
        '</div>' +
        '<div class="bt-cell bt-cell--port" data-label="Port">' +
            '<input type="number" class="bt-listen-port" placeholder="8928" aria-label="Listen port" min="1024" max="65535" value="' +
                escHtmlAttr(String(portVal)) + '">' +
        '</div>' +
        '<div class="bt-cell bt-cell--delay" data-label="Delay">' +
            '<input type="number" class="bt-delay" title="Static delay. Negative = compensate latency" aria-label="Static delay in milliseconds" placeholder="-300" value="' +
                escHtmlAttr(String(delayVal)) + '" step="50">' +
        '</div>' +
        '<div class="bt-cell bt-cell--runtime" data-label="Live">' +
            '<div class="bt-runtime" aria-live="polite"></div>' +
        '</div>' +
        '<div class="bt-row-actions bt-cell" data-label="Actions">' +
            '<details class="bt-device-action-menu ui-action-menu">' +
                '<summary class="btn btn-sm btn-secondary bt-device-action-toggle ui-action-menu-toggle">' + _bluetoothIconSvg('scan-action-icon') + '<span>Tools</span></summary>' +
                '<div class="bt-device-action-menu-list ui-action-menu-list">' +
                    '<button type="button" class="btn btn-sm btn-secondary bt-device-action-item ui-action-menu-item bt-device-action-info">Bluetooth info</button>' +
                    '<button type="button" class="btn btn-sm btn-secondary bt-device-action-item ui-action-menu-item bt-device-action-reset">Reset & reconnect</button>' +
                    '<button type="button" class="btn btn-sm btn-secondary bt-device-action-item ui-action-menu-item bt-device-action-open">Open in Bluetooth tab</button>' +
                    '<button type="button" class="btn btn-sm btn-secondary bt-device-action-item ui-action-menu-item bt-device-action-release">Release Bluetooth</button>' +
                '</div>' +
            '</details>' +
            '<button type="button" class="btn-remove-dev" title="Remove device" aria-label="Remove device">' +
                _trashIconSvg() +
            '</button>' +
        '</div>';

    // Detail sub-row with advanced fields
    var detail = document.createElement('div');
    detail.className = 'bt-detail-row';
    detail.style.display = 'none';
    detail.innerHTML =
        '<div><label>Format</label>' +
            '<input type="text" class="bt-preferred-format" placeholder="flac:44100:16:2" title="codec:samplerate:bitdepth:channels" value="' +
            escHtmlAttr(fmtVal) + '"></div>' +
        '<div><label>Listen Address</label>' +
            '<input type="text" class="bt-listen-host" placeholder="auto" title="IP address this player advertises" value="' +
            escHtmlAttr(listenHost || '') + '"></div>' +
        '<div><label>Keep-alive (s)</label>' +
            '<input type="number" class="bt-keepalive-interval" min="0" placeholder="0" ' +
            'title="0 = disabled, min 30 when enabled" value="' +
            escHtmlAttr(String(kaVal)) + '"></div>' +
        '<div><label>Idle standby (min)</label>' +
            '<input type="number" class="bt-idle-disconnect" min="0" placeholder="0" ' +
            'title="Disconnect BT after this many idle minutes. 0 = always connected. Recommended: 30" value="' +
            escHtmlAttr(String(idleVal)) + '"></div>' +
        '<div data-experimental style="display:none"><label>Room name</label>' +
            '<input type="text" class="bt-room-name" placeholder="e.g. Living Room" title="Stable room label for MA/HA/MassDroid interoperability" value="' +
            escHtmlAttr(roomNameVal) + '"></div>' +
        '<div data-experimental style="display:none"><label>Room ID</label>' +
            '<input type="text" class="bt-room-id" placeholder="living-room" title="Stable machine-readable room identifier" value="' +
            escHtmlAttr(roomIdVal) + '"></div>' +
        '<div data-experimental style="display:none"><label>Handoff mode</label>' +
            '<select class="bt-handoff-mode" title="Optimize speaker readiness for room handoff scenarios">' +
                '<option value="default"' + (handoffModeVal === 'default' ? ' selected' : '') + '>Default</option>' +
                '<option value="fast_handoff"' + (handoffModeVal === 'fast_handoff' ? ' selected' : '') + '>Fast handoff</option>' +
            '</select></div>';

    var enabledCb = row.querySelector('.bt-enabled');
    function syncBtRowIdentity() {
        wrap.dataset.deviceMac = _normalizeDeviceMac(row.querySelector('.bt-mac').value);
        wrap.dataset.deviceName = _normalizeDeviceName(row.querySelector('.bt-name').value);
    }
    function syncBtRowState() {
        wrap.classList.toggle('disabled', !enabledCb.checked);
    }

    row.querySelector('.btn-remove-dev').addEventListener('click', function() {
        wrap.remove();
        _recomputeConfigDirtyState();
    });
    row.querySelector('.bt-device-action-info').addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        var rowMac = row.querySelector('.bt-mac').value.trim().toUpperCase();
        if (!rowMac) {
            showToast('Set a device MAC address first', 'error');
            return;
        }
        _closeBtDeviceActionMenu(this);
        showBtDeviceInfo(rowMac);
    });
    row.querySelector('.bt-device-action-reset').addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        var rowMac = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var rowName = row.querySelector('.bt-name').value.trim();
        if (!rowMac) {
            showToast('Set a device MAC address first', 'error');
            return;
        }
        resetAndReconnect(rowMac, rowName, this);
    });
    row.querySelector('.bt-device-action-open').addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        var rowMac = row.querySelector('.bt-mac').value.trim().toUpperCase();
        if (!rowMac) {
            showToast('Set a device MAC address first', 'error');
            return;
        }
        _closeBtDeviceActionMenu(this);
        _openBluetoothInventory({highlightMac: rowMac});
    });
    var releaseBtn = row.querySelector('.bt-device-action-release');
    releaseBtn.addEventListener('click', function(event) {
        event.preventDefault();
        event.stopPropagation();
        var rowMac = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var rowName = row.querySelector('.bt-name').value.trim();
        if (!rowMac && !rowName) {
            showToast('Set a device MAC or name first', 'error');
            return;
        }
        _closeBtDeviceActionMenu(this);
        var idx = (lastDevices || []).findIndex(function(d) {
            var devMac = (d.bluetooth_mac || d.mac || '').trim().toUpperCase();
            if (rowMac && devMac === rowMac) return true;
            return rowName && (d.player_name || '').trim() === rowName;
        });
        if (idx < 0) {
            showToast('Device not found in runtime — is it running?', 'error');
            return;
        }
        btToggleManagement(idx);
    });
    enabledCb.addEventListener('change', function() {
        syncBtRowState();
        _recomputeConfigDirtyState();
    });
    row.querySelector('.bt-mac').addEventListener('input', function() {
        var v = this.value.trim();
        var valid = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(v);
        this.classList.toggle('invalid', v !== '' && !valid);
        syncBtRowIdentity();
        refreshBtDeviceRowsRuntime();
    });
    row.querySelector('.bt-name').addEventListener('input', function() {
        syncBtRowIdentity();
        refreshBtDeviceRowsRuntime();
    });
    // Mutual exclusion: keep-alive and idle standby cannot both be > 0
    var kaInput = detail.querySelector('.bt-keepalive-interval');
    var idleInput = detail.querySelector('.bt-idle-disconnect');
    function _syncKeepaliveIdleExclusion() {
        var ka = parseInt(kaInput.value, 10) || 0;
        var idle = parseInt(idleInput.value, 10) || 0;
        idleInput.disabled = ka > 0;
        kaInput.disabled = idle > 0;
        if (ka > 0) idleInput.title = 'Disabled — keep-alive prevents idle standby';
        else idleInput.title = 'Disconnect BT after this many idle minutes. 0 = always connected. Recommended: 30';
        if (idle > 0) kaInput.title = 'Disabled — idle standby conflicts with keep-alive';
        else kaInput.title = '0 = disabled, min 30 when enabled';
    }
    kaInput.addEventListener('input', _syncKeepaliveIdleExclusion);
    idleInput.addEventListener('input', _syncKeepaliveIdleExclusion);
    _syncKeepaliveIdleExclusion();

    row.querySelector('.bt-expand-btn').addEventListener('click', function() {
        var open = detail.style.display !== 'none';
        detail.style.display = open ? 'none' : 'grid';
        this.classList.toggle('open', !open);
        this.setAttribute('aria-expanded', String(!open));
        this.title = open ? 'Show advanced settings' : 'Hide advanced settings';
        this.setAttribute('aria-label', open ? 'Show advanced settings' : 'Hide advanced settings');
    });

    // Keep devices collapsed by default

    wrap.appendChild(row);
    wrap.appendChild(detail);
    tbody.appendChild(wrap);
    syncBtRowIdentity();
    syncBtRowState();
    refreshBtDeviceRowsRuntime();
    _recomputeConfigDirtyState();
}

function collectBtDevices() {
    var devices = [];
    document.querySelectorAll('#bt-devices-table .bt-device-wrap').forEach(function(wrap) {
        var row    = wrap.querySelector('.bt-device-row');
        var detail = wrap.querySelector('.bt-detail-row');
        var name       = row.querySelector('.bt-name').value.trim();
        var mac        = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var adapter    = row.querySelector('.bt-adapter').value;
        var delayEl    = row.querySelector('.bt-delay');
        var delay      = delayEl ? parseFloat(delayEl.value) : 0;
        if (isNaN(delay)) delay = 0;
        // Advanced fields are in the detail sub-row
        var fmtEl      = detail ? detail.querySelector('.bt-preferred-format') : null;
        var preferredFormat = fmtEl ? fmtEl.value.trim() : 'flac:44100:16:2';
        var listenHost = detail ? (detail.querySelector('.bt-listen-host') || {}).value || '' : '';
        var portEl     = row.querySelector('.bt-listen-port');
        var listenPort = portEl && portEl.value.trim() ? parseInt(portEl.value, 10) : null;
        var kaIntEl    = detail ? detail.querySelector('.bt-keepalive-interval') : null;
        var kaVal      = kaIntEl ? parseInt(kaIntEl.value, 10) : 0;
        var idleEl     = detail ? detail.querySelector('.bt-idle-disconnect') : null;
        var idleVal    = idleEl ? parseInt(idleEl.value, 10) : 0;
        var roomNameEl = detail ? detail.querySelector('.bt-room-name') : null;
        var roomIdEl   = detail ? detail.querySelector('.bt-room-id') : null;
        var handoffEl  = detail ? detail.querySelector('.bt-handoff-mode') : null;
        if (isNaN(kaVal) || kaVal < 0) kaVal = 0;
        if (kaVal > 0 && kaVal < 30) kaVal = 30;
        if (isNaN(idleVal) || idleVal < 0) idleVal = 0;
        var dev = { mac: mac, adapter: adapter, player_name: name, static_delay_ms: delay, preferred_format: preferredFormat || 'flac:44100:16:2' };
        if (listenHost) dev.listen_host = listenHost;
        if (listenPort) dev.listen_port = listenPort;
        dev.keepalive_interval = kaVal;
        if (idleVal > 0) dev.idle_disconnect_minutes = idleVal;
        var roomName = roomNameEl ? String(roomNameEl.value || '').trim() : '';
        var roomId = roomIdEl ? String(roomIdEl.value || '').trim() : '';
        var handoffMode = handoffEl ? String(handoffEl.value || 'default').trim().toLowerCase() : 'default';
        if (roomName) dev.room_name = roomName;
        if (roomId) dev.room_id = roomId;
        if (handoffMode && handoffMode !== 'default') dev.handoff_mode = handoffMode;
        // Read enabled state from checkbox
        var enabledCb = row.querySelector('.bt-enabled');
        if (enabledCb && !enabledCb.checked) dev.enabled = false;
        if (mac) devices.push(dev);
    });
    return devices;
}

function refreshBtDeviceRowsRuntime() {
    document.querySelectorAll('#bt-devices-table .bt-device-row').forEach(function(row) {
        var runtimeEl = row.querySelector('.bt-runtime');
        if (!runtimeEl) return;
        var name = row.querySelector('.bt-name') ? row.querySelector('.bt-name').value.trim() : '';
        var mac = row.querySelector('.bt-mac') ? row.querySelector('.bt-mac').value.trim() : '';
        var runtime = _findRuntimeDevice(name, mac);
        runtimeEl.innerHTML = _renderBtRuntimeBadgeHtml(runtime, 'chip bt-runtime-badge');
        var relBtn = row.querySelector('.bt-device-action-release');
        if (relBtn && runtime) {
            var mgmt = runtime.bt_management_enabled !== false;
            relBtn.textContent = mgmt ? 'Release Bluetooth' : 'Reclaim Bluetooth';
        }
    });
}

function populateBtDeviceRows(devices) {
    document.getElementById('bt-devices-table').innerHTML = '';
    devices.forEach(function(d) {
        addBtDeviceRow(d.player_name || '', d.mac || '', d.adapter || '',
                       d.static_delay_ms, d.listen_host, d.listen_port, d.enabled,
                       d.preferred_format, d.keepalive_interval, d.room_name, d.room_id, d.handoff_mode, d.idle_disconnect_minutes);
    });
    refreshBtDeviceRowsRuntime();
    _applyExperimentalVisibility();
}

function _hasDetectedAdapter() {
    return btAdapters.some(function(a) { return !a.manual; });
}

function _buildEmptyStateHTML(disabledDevices) {
    var disabledCount = Array.isArray(disabledDevices) ? disabledDevices.length : 0;
    if (disabledCount > 0) {
        return _renderEmptyStateHtml({
            icon: 'settings',
            title: 'All Bluetooth devices are disabled',
            copy: 'Re-enable a device in Configuration → Devices to bring it back into the bridge.' +
                (disabledCount > 1 ? ' (' + disabledCount + ' devices disabled)' : ''),
            center: true,
            actionsHtml: '<a href="#" class="no-devices-link" onclick="return _runOnboardingAssistantAction(\'open_devices_settings\')">' +
                _uiIconSvg('settings', 'no-devices-link-icon') + '<span>Open device settings</span>' +
            '</a>',
        });
    }
    if (!_hasDetectedAdapter()) {
        return _renderEmptyStateHtml({
            icon: 'plug',
            title: 'No Bluetooth adapter detected',
            copy: 'Add or map an adapter before scanning for speakers.',
            center: true,
            actionsHtml: '<a href="#" class="no-devices-link" onclick="_goToAdapters(); return false;">' +
                _uiIconSvg('plus', 'no-devices-link-icon') + '<span>Add adapter</span>' +
            '</a>',
        });
    }
    return _renderEmptyStateHtml({
        icon: 'bt',
        title: 'No Bluetooth devices configured',
        copy: 'Scan nearby speakers or add a device manually to start playback.',
        center: true,
        actionsHtml: '<a href="#" class="no-devices-link" onclick="_goToDevicesAndScan(); return false;">' +
            _uiIconSvg('search', 'no-devices-link-icon') + '<span>Scan for devices</span>' +
        '</a>',
    });
}

function _goToAdapters() {
    _openConfigPanel('bluetooth', 'config-bluetooth-adapters-card', 'start');
    setTimeout(function() {
        var row = _ensureEmptyManualAdapterRow();
        if (!row) return;
        row.scrollIntoView({behavior: 'smooth', block: 'center'});
        var firstInput = row.querySelector('.adp-id') || row.querySelector('.adp-mac') || row.querySelector('.adp-name');
        if (firstInput) firstInput.focus({preventScroll: true});
    }, 180);
}

function _openBluetoothInventory(options) {
    var opts = options || {};
    var opened = _openConfigPanel('bluetooth', 'config-bluetooth-paired-card', 'start');
    setTimeout(function() {
        loadPairedDevices({highlightMac: opts.highlightMac});
        var target = (opened && opened.target) || document.getElementById('config-bluetooth-paired-card');
        if (target) _highlightConfigTarget(target);
    }, 180);
    return false;
}

var _btScanModalKeydownHandler = null;
var _btScanModalState = {
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
var _scanCooldownTimer = null;
var _scanCooldownRemaining = 0;

function _sleep(delayMs) {
    return new Promise(function(resolve) { setTimeout(resolve, delayMs); });
}

function _getBtScanOverlay() {
    return document.getElementById('bt-scan-modal-overlay');
}

function _getBtScanDialog() {
    var overlay = _getBtScanOverlay();
    return overlay && typeof overlay.querySelector === 'function' ? overlay.querySelector('.bt-scan-modal') : null;
}

function _isBtScanModalVisible() {
    var overlay = _getBtScanOverlay();
    return !!(overlay && !overlay.hidden);
}

function _getFocusableElementsWithin(container) {
    if (!container || typeof container.querySelectorAll !== 'function') return [];
    return Array.from(
        container.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), ' +
            'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
    ).filter(function(el) {
        return !!(el && typeof el.focus === 'function' && !el.hidden && (!el.getAttribute || el.getAttribute('aria-hidden') !== 'true'));
    });
}

function _getBtScanModalTrapTarget(focusableElements, activeElement, shiftKey) {
    if (!focusableElements || !focusableElements.length) return null;
    var currentIndex = focusableElements.indexOf(activeElement);
    if (currentIndex === -1) {
        return shiftKey ? focusableElements[focusableElements.length - 1] : focusableElements[0];
    }
    if (shiftKey && currentIndex === 0) {
        return focusableElements[focusableElements.length - 1];
    }
    if (!shiftKey && currentIndex === focusableElements.length - 1) {
        return focusableElements[0];
    }
    return null;
}

function _focusBtScanModalTarget() {
    var dialog = _getBtScanDialog();
    if (!dialog) return;
    var closeBtn = dialog.querySelector('.bt-scan-modal-close');
    var focusables = _getFocusableElementsWithin(dialog);
    var target = closeBtn && !closeBtn.disabled ? closeBtn : (focusables[0] || null);
    if (target && typeof target.focus === 'function') {
        target.focus({preventScroll: true});
    }
}

function _restoreBtScanModalFocus() {
    var target = _btScanModalState.lastFocusedElement;
    if (
        !target ||
        typeof target.focus !== 'function' ||
        (document.body && typeof document.body.contains === 'function' && target !== document.body && !document.body.contains(target))
    ) {
        target = document.getElementById('scan-btn');
    }
    if (target && typeof target.focus === 'function') {
        target.focus({preventScroll: true});
    }
}

function _showBtScanBackgroundNotice() {
    if (_btScanModalState.backgroundNoticeShown) return;
    _btScanModalState.backgroundNoticeShown = true;
    showToast('Bluetooth scan continues in the background. Reopen Scan nearby to review progress.', 'info');
}

function _getBtScanLauncherState(isRunning, isVisible, cooldownRemaining) {
    if (isRunning) {
        return {
            disabled: false,
            icon: 'search',
            label: isVisible ? 'Scan in progress...' : 'Open active scan',
        };
    }
    if (cooldownRemaining > 0) {
        return {
            disabled: true,
            icon: 'search',
            label: 'Scan nearby (' + cooldownRemaining + 's)',
        };
    }
    return {
        disabled: false,
        icon: 'search',
        label: 'Scan nearby',
    };
}

async function _fetchJsonOrThrow(url, options, fallbackMessage) {
    var resp = await fetch(url, options);
    var data = await resp.json();
    if (!resp.ok) {
        throw new Error((data && data.error) || fallbackMessage || ('HTTP ' + resp.status));
    }
    return {resp: resp, data: data};
}

async function _pollBtAsyncJobResult(jobId, path, options) {
    var opts = options || {};
    var attempts = opts.maxAttempts || 30;
    var delayMs = opts.delayMs || 2000;
    for (var attempt = 0; attempt < attempts; attempt++) {
        if (opts.isStale && opts.isStale()) return null;
        await _sleep(delayMs);
        if (opts.isStale && opts.isStale()) return null;
        var poll = await _fetchJsonOrThrow(
            API_BASE + path + jobId,
            undefined,
            opts.failureMessage || 'Bluetooth job polling failed'
        );
        if (opts.onProgress) {
            opts.onProgress(poll.data);
        }
        if (poll.data && poll.data.status === 'done') {
            return poll.data;
        }
    }
    throw new Error(opts.timeoutMessage || 'Bluetooth job timed out');
}

function _getBtScanAdapters() {
    return btAdapters.filter(function(adapter) {
        return adapter && !adapter.manual && (adapter.id || adapter.mac);
    });
}

function _getBtScanAdapterValue(adapter) {
    return adapter && (adapter.id || adapter.mac) ? (adapter.id || adapter.mac) : '';
}

function _getBtScanAdapterLabel(adapterValue) {
    if (!adapterValue) return 'All adapters';
    var adapter = _findAdapterRecord(adapterValue, adapterValue);
    if (!adapter) return adapterValue;
    return adapter.customName || adapter.detectedName || adapter.name || adapter.id || adapter.mac || adapterValue;
}

function _estimateBtScanDurationForSelection(adapterValue) {
    var adapterCount = adapterValue ? 1 : _getBtScanAdapters().length;
    return 15 + Math.max(adapterCount - 1, 0) * 2;
}

function _renderBtScanAdapterOptions() {
    var select = document.getElementById('scan-adapter-select');
    if (!select) return;
    var adapters = _getBtScanAdapters();
    var currentValue = _btScanModalState.adapter || '';
    var options = ['<option value="">All adapters</option>'];
    adapters.forEach(function(adapter) {
        var value = _getBtScanAdapterValue(adapter);
        var label = adapter.customName || adapter.detectedName || adapter.name || value;
        var meta = [];
        if (adapter.id && label !== adapter.id) meta.push(adapter.id);
        if (adapter.mac) meta.push(adapter.mac);
        options.push(
            '<option value="' + escHtmlAttr(value) + '">' +
                escHtml(label + (meta.length ? ' · ' + meta.join(' · ') : '')) +
            '</option>'
        );
    });
    select.innerHTML = options.join('');
    var isAvailable = !currentValue || adapters.some(function(adapter) {
        return _getBtScanAdapterValue(adapter) === currentValue;
    });
    _btScanModalState.adapter = isAvailable ? currentValue : '';
    select.value = _btScanModalState.adapter || '';
}

function _setBtScanProgressPill(variant, label) {
    var stateEl = document.getElementById('scan-progress-state');
    if (!stateEl) return;
    var tone = variant === 'is-scanning' ? 'info' : variant ? variant.replace(/^is-/, '') : 'neutral';
    stateEl.outerHTML = _renderMetaStatusBadgeHtml({
        id: 'scan-progress-state',
        className: 'scan-status-pill',
        tone: tone,
        label: label,
        title: label,
    });
}

function _clearBtScanProgressTimer() {
    if (_btScanModalState.progressTimer) {
        clearInterval(_btScanModalState.progressTimer);
        _btScanModalState.progressTimer = null;
    }
}

function _renderBtScanProgress() {
    if (!_isBtScanModalVisible()) return;
    var progress = document.getElementById('scan-progress');
    var detail = document.getElementById('scan-progress-detail');
    var remaining = document.getElementById('scan-progress-remaining');
    var fill = document.getElementById('scan-progress-bar-fill');
    if (!progress || !detail || !remaining || !fill) return;
    if (!_btScanModalState.startedAtMs && !_btScanModalState.isRunning && !_btScanModalState.lastError && !_btScanModalState.lastDevices.length) {
        progress.hidden = true;
        return;
    }

    var ratio = 1;
    if (_btScanModalState.isRunning) {
        var totalMs = Math.max(_btScanModalState.expectedDuration, 1) * 1000;
        var elapsedMs = Math.max(Date.now() - _btScanModalState.startedAtMs, 0);
        ratio = Math.min(elapsedMs / totalMs, 0.96);
        _setBtScanProgressPill('is-scanning', 'Scanning nearby devices');
        detail.textContent = _getBtScanAdapterLabel(_btScanModalState.adapter) + ' · ' +
            (_btScanModalState.audioOnly ? 'Audio devices only' : 'All Bluetooth devices');
        remaining.textContent = Math.max(0, Math.ceil((totalMs - elapsedMs) / 1000)) + ' s';
    } else if (_btScanModalState.lastError) {
        ratio = 1;
        _setBtScanProgressPill('is-error', 'Scan failed');
        detail.textContent = _btScanModalState.lastError;
        remaining.textContent = 'Error';
    } else {
        var foundCount = (_btScanModalState.lastStats && _btScanModalState.lastStats.returned_candidates) || _btScanModalState.lastDevices.length;
        ratio = 1;
        _setBtScanProgressPill('is-success', 'Scan complete');
        detail.textContent = 'Found ' + String(foundCount) + ' ' +
            (_btScanModalState.audioOnly ? 'device' : 'candidate') +
            (foundCount === 1 ? '' : 's');
        remaining.textContent = 'Done';
    }
    fill.style.width = Math.max(0, Math.min(ratio, 1)) * 100 + '%';
    progress.hidden = false;
}

function _startBtScanProgressTimer(expectedDuration, startedAtMs) {
    _clearBtScanProgressTimer();
    _btScanModalState.expectedDuration = expectedDuration || _btScanModalState.expectedDuration || 15;
    _btScanModalState.startedAtMs = startedAtMs || Date.now();
    _renderBtScanProgress();
    _btScanModalState.progressTimer = setInterval(function() {
        if (!_btScanModalState.isRunning) {
            _clearBtScanProgressTimer();
            return;
        }
        _renderBtScanProgress();
    }, 250);
}

function _syncBtScanControls() {
    var select = document.getElementById('scan-adapter-select');
    var audioOnly = document.getElementById('scan-audio-only');
    var rescanBtn = document.getElementById('scan-rescan-btn');
    if (select) {
        select.value = _btScanModalState.adapter || '';
        select.disabled = _btScanModalState.isRunning;
    }
    if (audioOnly) {
        audioOnly.checked = _btScanModalState.audioOnly !== false;
        audioOnly.disabled = _btScanModalState.isRunning;
    }
    if (rescanBtn) {
        rescanBtn.disabled = _btScanModalState.isRunning || _scanCooldownRemaining > 0;
        if (_btScanModalState.isRunning) {
            rescanBtn.innerHTML = _buttonLabelWithIconHtml('refresh', 'Scanning...');
        } else if (_scanCooldownRemaining > 0) {
            rescanBtn.innerHTML = _buttonLabelWithIconHtml('refresh', 'Rescan (' + _scanCooldownRemaining + 's)');
        } else {
            rescanBtn.innerHTML = _buttonLabelWithIconHtml('refresh', 'Rescan');
        }
    }
}

function _onBtScanOptionChange() {
    var select = document.getElementById('scan-adapter-select');
    var audioOnly = document.getElementById('scan-audio-only');
    _btScanModalState.adapter = select ? (select.value || '') : '';
    _btScanModalState.audioOnly = audioOnly ? !!audioOnly.checked : true;
    if (!_btScanModalState.isRunning) _renderBtScanProgress();
}

function _applyBtScanCooldownUi() {
    var btn = document.getElementById('scan-btn');
    if (btn) {
        var launcherState = _getBtScanLauncherState(
            _btScanModalState.isRunning,
            _isBtScanModalVisible(),
            _scanCooldownRemaining
        );
        btn.disabled = !!launcherState.disabled;
        btn.innerHTML = _buttonLabelWithIconHtml(launcherState.icon, launcherState.label);
    }
    _syncBtScanControls();
}

function _clearBtScanStatusPanels() {
    var box = document.getElementById('scan-results-box');
    var listDiv = document.getElementById('scan-results-list');
    var status = document.getElementById('scan-status');
    if (listDiv) listDiv.innerHTML = '';
    if (box) box.hidden = true;
    if (status) status.innerHTML = '';
}

function _renderBtScanEmptyStateHtml() {
    return _renderEmptyStateHtml({
        className: 'scan-status-card is-empty',
        icon: 'search',
        title: _btScanModalState.audioOnly ? 'No audio devices found' : 'No Bluetooth devices found',
        copyHtml: _btScanModalState.audioOnly
            ? '<ul class="ui-empty-state-list">' +
                '<li>Make sure your speaker is in <strong>pairing mode</strong> (usually hold the Bluetooth button for 3-5 s)</li>' +
                '<li>Move the device closer to the Bluetooth adapter</li>' +
                '<li>Some devices need to be <strong>unpaired</strong> from other sources first</li>' +
                '<li>Try scanning again — some speakers advertise intermittently</li>' +
                '<li>If the device still does not appear, try another scan, then reboot the Bluetooth adapter, and finally reboot the host if needed</li>' +
            '</ul>'
            : '<ul class="ui-empty-state-list">' +
                '<li>No nearby Bluetooth devices were reported during this timed scan</li>' +
                '<li>If the device still does not appear, try another scan, then reboot the Bluetooth adapter, and finally reboot the host if needed</li>' +
            '</ul>',
        compact: true,
        inline: true,
    });
}

function _renderBtScanOutcome() {
    if (!_isBtScanModalVisible()) return;
    var box = document.getElementById('scan-results-box');
    var status = document.getElementById('scan-status');
    if (!status) return;
    if (_btScanModalState.isRunning) {
        _clearBtScanStatusPanels();
        return;
    }
    if (_btScanModalState.lastError) {
        if (box) box.hidden = true;
        status.innerHTML = _renderScanStatusBadgeHtml('Scan failed', 'error', _btScanModalState.lastError);
        return;
    }
    if (!_btScanModalState.startedAtMs && !_btScanModalState.lastDevices.length) {
        _clearBtScanStatusPanels();
        return;
    }
    var foundCount = (_btScanModalState.lastStats && _btScanModalState.lastStats.returned_candidates) || _btScanModalState.lastDevices.length;
    if (!_btScanModalState.lastDevices.length) {
        if (box) box.hidden = true;
        status.innerHTML = _renderBtScanEmptyStateHtml();
        return;
    }
    status.innerHTML = _renderScanStatusBadgeHtml(
        'Found ' + String(foundCount) + ' ' + (_btScanModalState.audioOnly ? 'device' : 'candidate') + (foundCount === 1 ? '' : 's'),
        'success'
    );
    _renderBtScanResults(_btScanModalState.lastDevices);
}

function closeBtScanModal() {
    var overlay = _getBtScanOverlay();
    if (overlay) overlay.hidden = true;
    _btScanModalState.isVisible = false;
    if (_btScanModalKeydownHandler) {
        document.removeEventListener('keydown', _btScanModalKeydownHandler);
        _btScanModalKeydownHandler = null;
    }
    if (_btScanModalState.isRunning) {
        _showBtScanBackgroundNotice();
    }
    _applyBtScanCooldownUi();
    _restoreBtScanModalFocus();
    return false;
}

function openBtScanModal(options) {
    if (!_hasDetectedAdapter()) {
        _goToAdapters();
        return false;
    }
    var opts = options || {};
    _openConfigPanel('bluetooth', 'config-bluetooth-paired-card', 'start');
    _btScanModalState.lastFocusedElement = opts.triggerEl && typeof opts.triggerEl.focus === 'function'
        ? opts.triggerEl
        : document.activeElement;
    _renderBtScanAdapterOptions();
    _syncBtScanControls();
    var overlay = _getBtScanOverlay();
    if (!overlay) return false;
    overlay.hidden = false;
    _btScanModalState.isVisible = true;
    _btScanModalState.backgroundNoticeShown = false;
    overlay.onclick = function(event) {
        if (event.target === overlay) closeBtScanModal();
    };
    if (_btScanModalKeydownHandler) {
        document.removeEventListener('keydown', _btScanModalKeydownHandler);
    }
    _btScanModalKeydownHandler = function(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeBtScanModal();
            return;
        }
        if (event.key !== 'Tab') return;
        var dialog = _getBtScanDialog();
        var focusable = _getFocusableElementsWithin(dialog);
        var trapTarget = _getBtScanModalTrapTarget(focusable, document.activeElement, !!event.shiftKey);
        if (!trapTarget) return;
        event.preventDefault();
        trapTarget.focus({preventScroll: true});
    };
    document.addEventListener('keydown', _btScanModalKeydownHandler);
    _renderBtScanProgress();
    _renderBtScanOutcome();
    _applyBtScanCooldownUi();
    _focusBtScanModalTarget();
    if (opts.autoStart !== false && !_btScanModalState.isRunning) startBtScan();
    return false;
}

function _goToBluetoothAndScan(options) {
    var opts = options || {};
    if (!_hasDetectedAdapter()) {
        _goToAdapters();
        return false;
    }
    _openBluetoothInventory(opts);
    setTimeout(function() {
        var scanBtn = document.getElementById('scan-btn');
        if (scanBtn) scanBtn.focus({preventScroll: true});
        openBtScanModal({autoStart: true});
    }, 180);
    return false;
}

function _goToDevicesAndScan(options) {
    return _goToBluetoothAndScan(options);
}

function _isOnboardingCardVisible(guidance) {
    var card = guidance && guidance.onboarding_card ? guidance.onboarding_card : null;
    return _shouldShowOnboardingAssistantBanner(card, {showByDefault: _onboardingShowByDefault(guidance)});
}

function _syncEmptyStatePlaceholder(guidance) {
    var grid = document.getElementById('status-grid');
    if (!grid || _backendServiceState || (lastDevices && lastDevices.length)) return;
    grid.classList.remove('list-view');
    if (_isOnboardingCardVisible(guidance)) {
        grid.innerHTML = '';
        return;
    }
    grid.innerHTML = '<div id="no-devices-hint" class="no-devices-hint">' + _buildEmptyStateHTML(_lastDisabledDevices) + '</div>';
}

function _refreshEmptyState() {
    _syncEmptyStatePlaceholder(_lastOperatorGuidance);
}

function openConfigAndAddDevice(options) {
    if (!_hasDetectedAdapter()) {
        _goToAdapters();
    } else {
        _goToBluetoothAndScan(options);
    }
}

function _renderBtScanResults(devices) {
    var box = document.getElementById('scan-results-box');
    var listDiv = document.getElementById('scan-results-list');
    if (!box || !listDiv) return;
    listDiv.innerHTML = devices.map(function(d, i) {
        var addable = d.supports_import !== false && d.audio_capable !== false;
        var chips = [
            '<span class="scan-result-chip ' + (d.audio_capable === false ? 'is-other' : 'is-audio') + '">' +
                (d.audio_capable === false ? 'Other Bluetooth device' : 'Audio device') +
            '</span>'
        ];
        if (d.adapter) {
            chips.push('<span class="scan-result-chip">' + escHtml(_getBtScanAdapterLabel(d.adapter)) + '</span>');
        }
        if (d.warning) {
            chips.push('<span class="scan-result-chip is-warning" title="' + escHtmlAttr(d.warning) + '">⚠ Another bridge</span>');
        }
        return '<div class="scan-result-item' + (addable ? '' : ' scan-result-item--passive') + '" data-scan-idx="' + i + '">' +
            '<span class="scan-result-actions">' +
                (addable
                    ? '<button type="button" class="scan-action-btn scan-action-btn--primary scan-add-btn" title="Add to config without pairing now">Add to fleet</button>' +
                      '<button type="button" class="scan-action-btn scan-action-btn--pair scan-pair-btn" data-pair-idx="' + i + '" title="Pair, trust, and add to config">Add & pair</button>'
                    : '<span class="scan-result-passive">Audio import unavailable</span>') +
            '</span>' +
            '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
            '<span class="scan-result-summary">' +
                '<span class="scan-result-name">' + escHtml(d.name) + '</span>' +
                '<span class="scan-result-meta">' + chips.join('') + '</span>' +
            '</span>' +
            '</div>';
    }).join('');

    listDiv.querySelectorAll('[data-scan-idx]').forEach(function(row) {
        var scanAddBtn = row.querySelector('.scan-add-btn');
        if (scanAddBtn) {
            scanAddBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                var d = devices[parseInt(row.dataset.scanIdx, 10)];
                if (d.warning && !confirm(d.warning + '\n\nAdd anyway?')) return;
                addFromScan(d.mac, d.name, d.adapter);
            });
        }
    });
    listDiv.querySelectorAll('.scan-pair-btn').forEach(function(scanPairBtn) {
        scanPairBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var d = devices[parseInt(this.dataset.pairIdx, 10)];
            if (d.warning && !confirm(d.warning + '\n\nPair and add anyway?')) return;
            pairAndAdd(d.mac, d.name, d.adapter, this);
        });
    });
    box.hidden = !devices.length;
}

// ---- BT Scan ----

async function startBtScan() {
    if (_btScanModalState.isRunning) return false;
    _onBtScanOptionChange();
    var btn = document.getElementById('scan-btn');
    var requestToken = ++_btScanModalState.requestToken;

    _btScanModalState.activeJobId = '';
    _btScanModalState.isRunning = true;
    _btScanModalState.lastDevices = [];
    _btScanModalState.lastStats = null;
    _btScanModalState.lastError = '';
    _btScanModalState.expectedDuration = _estimateBtScanDurationForSelection(_btScanModalState.adapter);
    _btScanModalState.startedAtMs = Date.now();
    _btScanModalState.backgroundNoticeShown = false;
    _clearBtScanStatusPanels();
    _syncBtScanControls();
    _applyBtScanCooldownUi();
    _startBtScanProgressTimer(_btScanModalState.expectedDuration, _btScanModalState.startedAtMs);

    try {
        var startedScanResp = await fetch(API_BASE + '/api/bt/scan', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                adapter: _btScanModalState.adapter || '',
                audio_only: _btScanModalState.audioOnly !== false,
            })
        });
        var data = await startedScanResp.json();

        if (startedScanResp.status === 429 && data.retry_after) {
            _btScanModalState.isRunning = false;
            _btScanModalState.startedAtMs = 0;
            _btScanModalState.activeJobId = '';
            _clearBtScanProgressTimer();
            _renderBtScanProgress();
            _startScanCooldown(btn, data.retry_after);
            if (_isBtScanModalVisible()) {
                var status = document.getElementById('scan-status');
                if (status) {
                    status.innerHTML = _renderScanStatusBadgeHtml('Scan cooldown active', 'neutral', String(data.retry_after) + 's remaining');
                }
            }
            return false;
        }
        if (!startedScanResp.ok) {
            throw new Error(data.error || 'Bluetooth scan failed');
        }

        _btScanModalState.activeJobId = data.job_id || '';
        _btScanModalState.expectedDuration = data.expected_duration || _btScanModalState.expectedDuration;
        if (data.scan_options) {
            _btScanModalState.adapter = data.scan_options.adapter || '';
            _btScanModalState.audioOnly = data.scan_options.audio_only !== false;
        }
        _btScanModalState.startedAtMs = data.started_at ? data.started_at * 1000 : _btScanModalState.startedAtMs;
        _syncBtScanControls();
        _applyBtScanCooldownUi();
        _startBtScanProgressTimer(_btScanModalState.expectedDuration, _btScanModalState.startedAtMs);

        var result = await _pollBtAsyncJobResult(_btScanModalState.activeJobId, '/api/bt/scan/result/', {
            timeoutMessage: 'Scan timed out',
            failureMessage: 'Scan polling failed',
            isStale: function() {
                return requestToken !== _btScanModalState.requestToken;
            },
            onProgress: function(pollData) {
                if (pollData.scan_options) {
                    _btScanModalState.adapter = pollData.scan_options.adapter || '';
                    _btScanModalState.audioOnly = pollData.scan_options.audio_only !== false;
                }
                if (pollData.expected_duration) _btScanModalState.expectedDuration = pollData.expected_duration;
                if (pollData.started_at) _btScanModalState.startedAtMs = pollData.started_at * 1000;
                _renderBtScanProgress();
            },
        });
        if (!result) return false;
        _btScanModalState.isRunning = false;
        _btScanModalState.activeJobId = '';
        _btScanModalState.lastDevices = result.devices || [];
        _btScanModalState.lastStats = result.stats || null;
        _btScanModalState.lastError = result.error || '';
        if (result.scan_options) {
            _btScanModalState.adapter = result.scan_options.adapter || '';
            _btScanModalState.audioOnly = result.scan_options.audio_only !== false;
        }
        _clearBtScanProgressTimer();
        _renderBtScanProgress();

        if (_btScanModalState.lastError) {
            throw new Error(_btScanModalState.lastError);
        }
        _renderBtScanOutcome();
        _startScanCooldown(btn, 10);
    } catch (err) {
        _btScanModalState.isRunning = false;
        _btScanModalState.activeJobId = '';
        _btScanModalState.lastError = err && err.message ? err.message : 'Unknown error';
        _clearBtScanProgressTimer();
        _renderBtScanProgress();
        _renderBtScanOutcome();
    } finally {
        _syncBtScanControls();
        _applyBtScanCooldownUi();
    }
    return false;
}

function _startScanCooldown(btn, seconds) {
    if (_scanCooldownTimer) clearInterval(_scanCooldownTimer);
    _scanCooldownRemaining = seconds;
    _applyBtScanCooldownUi();
    _scanCooldownTimer = setInterval(function() {
        _scanCooldownRemaining--;
        if (_scanCooldownRemaining <= 0) {
            clearInterval(_scanCooldownTimer);
            _scanCooldownTimer = null;
            _scanCooldownRemaining = 0;
        }
        _applyBtScanCooldownUi();
    }, 1000);
}

function _setScanActionState(btnEl, state, label) {
    if (!btnEl) return;
    btnEl.textContent = label;
    btnEl.classList.remove('is-pairing', 'is-success', 'is-error');
    btnEl.disabled = false;
    if (state === 'pairing') {
        btnEl.classList.add('is-pairing');
        btnEl.disabled = true;
    } else if (state === 'success') {
        btnEl.classList.add('is-success');
    } else if (state === 'error') {
        btnEl.classList.add('is-error');
    }
}

async function pairAndAdd(mac, name, adapter, btnEl) {
    if (!confirm('Put "' + (name || mac) + '" into pairing mode, then click OK.\n\nThis will pair, trust, and add the device (~25 s).')) return;
    _setScanActionState(btnEl, 'pairing', 'Pairing\u2026');
    try {
        var startedPair = await _fetchJsonOrThrow(API_BASE + '/api/bt/pair_new', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mac: mac, adapter: adapter || autoAdapter()})
        }, 'Bluetooth pairing failed');
        var data = startedPair.data;
        if (!data.job_id) throw new Error(data.error || 'No job_id');

        var result = await _pollBtAsyncJobResult(data.job_id, '/api/bt/pair_new/result/', {
            timeoutMessage: 'Pairing timed out',
            failureMessage: 'Pairing status check failed',
        });
        if (!result) throw new Error('Pairing timed out');
        if (result.error) throw new Error(result.error);
        if (result.success) {
            _setScanActionState(btnEl, 'success', '\u2713 Paired');
            addFromScan(mac, name, adapter);
        } else {
            _setScanActionState(btnEl, 'error', '\u2717 Failed');
            setTimeout(function() { _setScanActionState(btnEl, '', 'Add & Pair'); }, 3000);
            showToast('Pair failed for ' + (name || mac) + '.', 'error');
        }
    } catch (err) {
        _setScanActionState(btnEl, 'error', 'Error');
        setTimeout(function() { _setScanActionState(btnEl, '', 'Add & Pair'); }, 3000);
        showToast('Pair failed: ' + (err && err.message ? err.message : 'Unknown error'), 'error');
    }
}

function autoAdapter() {
    return (btAdapters.length === 1) ? btAdapters[0].id : '';
}

function addFromScan(mac, name, adapter) {
    addBtDeviceRow(name, mac, adapter || autoAdapter());
    _applyExperimentalVisibility();
    var box = document.getElementById('scan-results-box');
    var status = document.getElementById('scan-status');
    if (box) box.hidden = true;
    if (status) status.textContent = '';
    closeBtScanModal();
    _afterBluetoothAddToFleet(name, mac);
}

function addFromPaired(mac, name) {
    addBtDeviceRow(name, mac, autoAdapter());
    _applyExperimentalVisibility();
    _afterBluetoothAddToFleet(name, mac);
}

async function removePairedDevice(mac, name, rowEl) {
    if (!confirm('Remove ' + (name || mac) + ' from BT stack?\nThis will unpair the device.')) return;
    try {
        var resp = await fetch(API_BASE + '/api/bt/remove', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mac: mac})
        });
        if (resp.ok) {
            rowEl.style.opacity = '0.3';
            rowEl.style.pointerEvents = 'none';
            setTimeout(function() { loadPairedDevices(); }, 1500);
        }
    } catch (_) {}
}

async function resetAndReconnect(mac, name, btnEl) {
    if (!confirm('Reset & Reconnect "' + (name || mac) + '"?\n\nThis will:\n1. Remove device from BT stack\n2. Power cycle adapter\n3. Re-pair + trust + connect (~30 s)\n\nPut the device in pairing mode, then click OK.')) return;
    var origText = btnEl.textContent;
    _setScanActionState(btnEl, 'pairing', 'Resetting\u2026');
    try {
        var resp = await fetch(API_BASE + '/api/bt/reset_reconnect', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mac: mac})
        });
        var data = await resp.json();
        if (!data.job_id) throw new Error(data.error || 'No job_id');

        _setScanActionState(btnEl, 'pairing', 'Pairing\u2026');

        var result = null;
        for (var i = 0; i < 40; i++) {
            await new Promise(function(r) { setTimeout(r, 2000); });
            var pr = await fetch(API_BASE + '/api/bt/reset_reconnect/result/' + data.job_id);
            var pd = await pr.json();
            if (pd.status === 'done') { result = pd; break; }
        }
        if (!result) throw new Error('Reset timed out');
        if (result.success) {
            _setScanActionState(btnEl, 'success', '\u2713 ' + (result.connected ? 'Connected' : 'Paired'));
        } else {
            _setScanActionState(btnEl, 'error', '\u2717 Failed');
            setTimeout(function() { _setScanActionState(btnEl, '', origText); }, 3000);
        }
    } catch (err) {
        _setScanActionState(btnEl, 'error', 'Error');
        setTimeout(function() { _setScanActionState(btnEl, '', origText); }, 3000);
        alert('Reset failed: ' + err.message);
    }
}

async function showBtDeviceInfo(mac) {
    try {
        var resp = await fetch(API_BASE + '/api/bt/info', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mac: mac})
        });
        var info = await resp.json();
        var lines = [];
        if (info.name) lines.push('Name: ' + info.name);
        if (info.alias) lines.push('Alias: ' + info.alias);
        lines.push('MAC: ' + mac);
        if (info.paired) lines.push('Paired: ' + info.paired);
        if (info.trusted) lines.push('Trusted: ' + info.trusted);
        if (info.connected) lines.push('Connected: ' + info.connected);
        if (info.bonded) lines.push('Bonded: ' + info.bonded);
        if (info.blocked) lines.push('Blocked: ' + info.blocked);
        if (info['class']) lines.push('Class: ' + info['class']);
        if (info.icon) lines.push('Icon: ' + info.icon);
        if (info.error) lines.push('\nError: ' + info.error);
        var text = lines.join('\n') || 'No info available for ' + mac;
        _showBtInfoModal(info.name || mac, text);
    } catch (err) {
        showToast('Failed to get info: ' + err.message, 'error');
    }
}

function _showBtInfoModal(title, text) {
    var overlay = document.createElement('div');
    overlay.className = 'bugreport-overlay';
    overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

    var modal = document.createElement('div');
    modal.className = 'bugreport-modal bt-info-modal';
    modal.style.maxWidth = '440px';

    var header = document.createElement('div');
    header.className = 'bugreport-header bt-info-header';
    header.innerHTML =
        '<span class="bugreport-header-title">\u2139\uFE0F ' + escHtml(title) + '</span>';
    var closeX = document.createElement('button');
    closeX.className = 'bugreport-close';
    closeX.innerHTML = '\u00d7';
    closeX.title = 'Close';
    closeX.onclick = function() { overlay.remove(); };
    header.appendChild(closeX);
    modal.appendChild(header);

    var body = document.createElement('div');
    body.className = 'bugreport-body';
    var pre = document.createElement('pre');
    pre.style.cssText = 'margin:0;white-space:pre-wrap;word-break:break-all;font-size:13px;line-height:1.5';
    pre.textContent = text;
    body.appendChild(pre);
    modal.appendChild(body);

    var footer = document.createElement('div');
    footer.className = 'bugreport-footer';

    var copyBtn = document.createElement('button');
    copyBtn.className = 'bugreport-btn secondary';
    copyBtn.textContent = '\uD83D\uDCCB Copy';
    copyBtn.onclick = function() {
        _copyToClipboard(text).then(function() {
            showToast('Copied to clipboard', 'info');
        }, function() {
            showToast('Could not copy', 'error');
        });
    };
    footer.appendChild(copyBtn);

    var closeBtn = document.createElement('button');
    closeBtn.className = 'bugreport-btn primary bt-info-btn-primary';
    closeBtn.textContent = 'Close';
    closeBtn.onclick = function() { overlay.remove(); };
    footer.appendChild(closeBtn);

    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);
}

async function loadPairedDevices(options) {
    try {
        var opts = options || {};
        var showAll = document.getElementById('paired-show-all');
        var showAllChecked = showAll && showAll.checked;
        var qs = showAllChecked ? '?filter=0' : '';
        var resp = await fetch(API_BASE + '/api/bt/paired' + qs);
        var data = await resp.json();
        var devices = data.devices || [];
        var allCount = data.total_count || devices.length;
        var box = document.getElementById('paired-box');
        var listDiv = document.getElementById('paired-list');
        var countEl = document.getElementById('paired-box-count');
        if (box) box.hidden = false;

        var titleEl = box.querySelector('.paired-box-copy');
        if (titleEl) {
            titleEl.textContent = 'Already paired devices';
        }
        if (countEl) {
            if (!devices.length) {
                countEl.textContent = showAllChecked ? '0 devices' : 'No audio devices';
            } else if (!showAllChecked && allCount > devices.length) {
                countEl.textContent = devices.length + ' audio · ' + allCount + ' total';
            } else {
                countEl.textContent = devices.length + ' device' + (devices.length === 1 ? '' : 's');
            }
        }

        if (!devices.length) {
            listDiv.innerHTML = _renderEmptyStateHtml({
                className: 'list-empty-state',
                icon: 'bt',
                title: 'No paired devices',
                copy: showAllChecked
                    ? 'No paired Bluetooth devices were found in the local stack.'
                    : 'No paired audio devices are available right now. Enable Show all to inspect the full Bluetooth stack.',
                compact: true,
                inline: true,
            });
            _applyDemoScreenshotDefaults();
            return;
        }

        listDiv.innerHTML = devices.map(function(d, idx) {
            var displayName = /^RSSI:/i.test(d.name) ? 'Unknown device' : d.name;
            var btInfoIcon = _bluetoothIconSvg('scan-action-icon');
            return '<div class="scan-result-item paired-result-item" data-paired-idx="' + idx + '" data-paired-mac="' + escHtmlAttr(_normalizeDeviceMac(d.mac)) + '">' +
                '<span class="scan-result-actions">' +
                '<button type="button" class="scan-action-btn scan-action-btn--primary paired-add-btn">Add to fleet</button>' +
                '</span>' +
                '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                '<span class="scan-result-name">' + escHtml(displayName) + '</span>' +
                '<span class="paired-actions" onclick="event.stopPropagation()">' +
                    '<button type="button" class="scan-action-btn paired-info-btn" title="Show Bluetooth device info">' + btInfoIcon + '<span>Info</span></button>' +
                '<button type="button" class="scan-action-btn paired-release-btn" title="Release or reclaim BT management">Release</button>' +
                '<button type="button" class="scan-action-btn scan-action-btn--warning paired-reset-btn" title="Remove, re-pair and connect from scratch">Reset & Reconnect</button>' +
                '<button type="button" class="paired-remove-btn btn-remove-dev" title="Remove from BT stack" aria-label="Remove from BT stack">' + _trashIconSvg() + '</button>' +
                '</span>' +
                '</div>';
        }).join('');
        listDiv.querySelectorAll('[data-paired-idx]').forEach(function(row) {
            var d = devices[parseInt(row.dataset.pairedIdx)];
            row.querySelector('.paired-add-btn').addEventListener('click', function(e) {
                e.stopPropagation();
                addFromPaired(d.mac, d.name);
            });
            row.querySelector('.paired-remove-btn').addEventListener('click', function(e) {
                e.stopPropagation();
                removePairedDevice(d.mac, d.name, row);
            });
            row.querySelector('.paired-reset-btn').addEventListener('click', function(e) {
                e.stopPropagation();
                resetAndReconnect(d.mac, d.name, this);
            });
            row.querySelector('.paired-info-btn').addEventListener('click', function(e) {
                e.stopPropagation();
                showBtDeviceInfo(d.mac);
            });
            var pairedReleaseBtn = row.querySelector('.paired-release-btn');
            if (pairedReleaseBtn) {
                var pMac = (d.mac || '').trim().toUpperCase();
                var pIdx = (lastDevices || []).findIndex(function(dev) {
                    return (dev.bluetooth_mac || dev.mac || '').trim().toUpperCase() === pMac;
                });
                var pDev = pIdx >= 0 ? lastDevices[pIdx] : null;
                var pMgmt = pDev ? pDev.bt_management_enabled !== false : true;
                pairedReleaseBtn.textContent = pMgmt ? 'Release' : 'Reclaim';
                pairedReleaseBtn.title = pMgmt
                    ? 'Stop BT management for this device'
                    : 'Resume BT management and auto-reconnect';
                if (pIdx < 0) pairedReleaseBtn.disabled = true;
                pairedReleaseBtn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    var idx = (lastDevices || []).findIndex(function(dev) {
                        return (dev.bluetooth_mac || dev.mac || '').trim().toUpperCase() === pMac;
                    });
                    if (idx < 0) {
                        showToast('Device not found in runtime — is it running?', 'error');
                        return;
                    }
                    btToggleManagement(idx).then(function() {
                        loadPairedDevices();
                    });
                });
            }
        });
        _applyDemoScreenshotDefaults();
        _highlightPairedDeviceRowByMac(opts.highlightMac);
    } catch (_) {}
}

function togglePairedList(node) {
    return;
}

// ---- Config ----

function _setStatusText(el, text, tone) {
    if (!el) return;
    el.textContent = text || '';
    if (tone) {
        el.dataset.tone = tone;
    } else {
        delete el.dataset.tone;
    }
}

function _bluetoothIconSvg(className) {
    return '<svg class="' + (className || '') + '" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
        '<path d="M14.88 16.29 13 14.41V17.59L14.88 16.29ZM13 6.41V9.59L14.88 8.29 13 6.41ZM17.71 7.71 12 2H11V9.59L6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 11 14.41V22H12L17.71 16.29 13.41 12 17.71 7.71ZM15.29 16.29 13 18.17V14.41L15.29 16.29ZM13 9.59V5.83L15.29 7.71 13 9.59Z"/>' +
        '</svg>';
}

var _lastConfirmedUpdateChannel = 'stable';

function _updateChannelWarningText(channel) {
    if (channel === 'beta') {
        return 'Beta channel tracks preview builds from the beta branch. Expect unfinished features, regressions, and more frequent changes.';
    }
    if (channel === 'rc') {
        return 'RC channel tracks release candidates from main before stable publication. Use it only if you are ready to validate prerelease builds.';
    }
    return '';
}

function _syncUpdateChannelState() {
    var select = document.getElementById('update-channel');
    var warningEl = document.getElementById('update-channel-warning');
    if (!select || !warningEl) return;
    var channel = (select.value || 'stable').toLowerCase();
    var warningText = _updateChannelWarningText(channel);
    warningEl.hidden = !warningText;
    warningEl.textContent = warningText;
}

function _onUpdateChannelChange() {
    var select = document.getElementById('update-channel');
    if (!select) return;
    var nextChannel = (select.value || 'stable').toLowerCase();
    if ((nextChannel === 'rc' || nextChannel === 'beta') && nextChannel !== _lastConfirmedUpdateChannel) {
        var confirmed = window.confirm(
            nextChannel === 'beta'
                ? 'Beta channel is not stable and may contain unfinished features or regressions. Switch to beta?'
                : 'RC channel is not stable yet and may still contain regressions. Switch to release candidates?'
        );
        if (!confirmed) {
            select.value = _lastConfirmedUpdateChannel;
            _syncUpdateChannelState();
            return;
        }
    }
    _lastConfirmedUpdateChannel = nextChannel;
    _syncUpdateChannelState();
}

function readOptionalNumberField(name) {
    var input = document.querySelector('[name="' + name + '"]');
    if (!input) return null;
    var raw = (input.value || '').trim();
    if (!raw) return null;
    var value = parseInt(raw, 10);
    return Number.isFinite(value) ? value : raw;
}

function _buildConfigPayload(options) {
    options = options || {};
    var formData = new FormData(document.getElementById('config-form'));
    var config = Object.fromEntries(formData);

    config.BLUETOOTH_DEVICES = collectBtDevices();
    config.PREFER_SBC_CODEC = !!(document.getElementById('prefer-sbc-codec') || {}).checked;
    config.DISABLE_PA_RESCUE_STREAMS = !!(document.getElementById('disable-pa-rescue-streams') || {}).checked;
    config.AUTH_ENABLED = !!(document.getElementById('auth-enabled') || {}).checked;
    config.BRUTE_FORCE_PROTECTION = !!(document.getElementById('brute-force-protection') || {}).checked;
    config.HA_AREA_NAME_ASSIST_ENABLED = !!(document.getElementById('ha-area-name-assist-enabled') || {}).checked;
    config.MA_AUTO_SILENT_AUTH = !!(document.getElementById('ma-auto-silent-auth') || {}).checked;
    config.MA_WEBSOCKET_MONITOR = !!(document.getElementById('ma-websocket-monitor') || {}).checked;
    config.VOLUME_VIA_MA = !!(document.getElementById('volume-via-ma') || {}).checked;
    config.MUTE_VIA_MA = !!(document.getElementById('mute-via-ma') || {}).checked;
    config.SMOOTH_RESTART = !!(document.getElementById('smooth-restart') || {}).checked;
    config.AUTO_UPDATE = !!(document.getElementById('auto-update') || {}).checked;
    config.CHECK_UPDATES = !!(document.getElementById('check-updates') || {}).checked;

    var updateChannelSelect = document.getElementById('update-channel');
    if (updateChannelSelect) {
        config.UPDATE_CHANNEL = (updateChannelSelect.value || 'stable').toLowerCase();
    }

    if (options.includeExternal !== false) {
        var logSel = document.getElementById('log-level-select');
        if (logSel) config.LOG_LEVEL = logSel.value;
    }

    config.BT_CHECK_INTERVAL = parseInt(config.BT_CHECK_INTERVAL, 10) || 10;
    config.BT_MAX_RECONNECT_FAILS = parseInt(config.BT_MAX_RECONNECT_FAILS, 10) || 0;
    config.WEB_PORT = readOptionalNumberField('WEB_PORT');
    config.BASE_LISTEN_PORT = readOptionalNumberField('BASE_LISTEN_PORT');
    config.SESSION_TIMEOUT_HOURS = parseInt(((document.querySelector('[name="SESSION_TIMEOUT_HOURS"]') || {}).value), 10) || 24;
    config.BRUTE_FORCE_MAX_ATTEMPTS = parseInt(((document.querySelector('[name="BRUTE_FORCE_MAX_ATTEMPTS"]') || {}).value), 10) || 5;
    config.BRUTE_FORCE_WINDOW_MINUTES = parseInt(((document.querySelector('[name="BRUTE_FORCE_WINDOW_MINUTES"]') || {}).value), 10) || 1;
    config.BRUTE_FORCE_LOCKOUT_MINUTES = parseInt(((document.querySelector('[name="BRUTE_FORCE_LOCKOUT_MINUTES"]') || {}).value), 10) || 5;
    config.STARTUP_BANNER_GRACE_SECONDS = parseInt(
        ((document.querySelector('[name="STARTUP_BANNER_GRACE_SECONDS"]') || {}).value),
        10
    );
    if (!Number.isFinite(config.STARTUP_BANNER_GRACE_SECONDS)) {
        config.STARTUP_BANNER_GRACE_SECONDS = 5;
    }
    config.RECOVERY_BANNER_GRACE_SECONDS = parseInt(
        ((document.querySelector('[name="RECOVERY_BANNER_GRACE_SECONDS"]') || {}).value),
        10
    );
    if (!Number.isFinite(config.RECOVERY_BANNER_GRACE_SECONDS)) {
        config.RECOVERY_BANNER_GRACE_SECONDS = 15;
    }

    if (options.includeRuntime !== false) {
        var groupSlider = document.getElementById('group-vol-slider');
        config._new_device_default_volume = groupSlider ? parseInt(groupSlider.value, 10) : 100;
    }

    config.BLUETOOTH_ADAPTERS = _collectPersistedAdaptersFromDom();
    config.HA_ADAPTER_AREA_MAP = config.HA_AREA_NAME_ASSIST_ENABLED
        ? _collectHaAdapterAreaMapFromDom()
        : _normalizeHaAdapterAreaMap(_haAdapterAreaMap);
    return config;
}

async function saveConfig() {
    syncManualAdapters();
    var config = _buildConfigPayload();
    var shouldReloadMaRuntime = _configShouldReloadMaRuntime(config);

    if (config.AUTH_ENABLED && !window._passwordSet) {
        showToast('Set a password before enabling authentication', 'error');
        var fields = document.getElementById('auth-password-fields');
        if (fields) fields.hidden = false;
        var pwInput = document.getElementById('new-password');
        if (pwInput) pwInput.focus();
        return false;
    }

    try {
        var resp = await fetch(API_BASE + '/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        });
        if (!resp.ok) {
            var errData = await resp.json().catch(function() { return {}; });
            return { ok: false, error: errData.error || 'Save failed (HTTP ' + resp.status + ')' };
        }
        if (shouldReloadMaRuntime && config.MA_API_URL && config.MA_API_TOKEN) {
            var maReload = await _reloadMaRuntimeAfterConfigSave();
            return {
                ok: true,
                maReloaded: !!maReload.ok,
                maReloadError: maReload.error || '',
            };
        }
        return { ok: true };
    } catch (err) {
        console.error('Save config error:', err);
        return { ok: false, error: 'Network error: ' + err.message };
    }
}

function _configShouldReloadMaRuntime(config) {
    var clean = (_configCleanSnapshot && _configCleanSnapshot.staticValues) || {};
    return !_configValuesEqual(clean.MA_API_URL, config.MA_API_URL)
        || !_configValuesEqual(clean.MA_API_TOKEN, config.MA_API_TOKEN);
}

async function _reloadMaRuntimeAfterConfigSave() {
    try {
        var resp = await fetch(API_BASE + '/api/ma/reload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (resp.status === 401) { _handleUnauthorized(); return { ok: false, error: 'Unauthorized' }; }
        var data = await resp.json().catch(function() { return {}; });
        if (!resp.ok) {
            return { ok: false, error: data.error || 'Music Assistant reload failed' };
        }
        return { ok: true, jobId: data.job_id, monitorReloaded: !!data.monitor_reloaded };
    } catch (err) {
        console.error('MA runtime reload error:', err);
        return { ok: false, error: 'Music Assistant reload failed: ' + err.message };
    }
}

// ---- Change password (standalone mode) ----

async function setPassword() {
    var pw  = (document.getElementById('new-password') || {}).value || '';
    var pw2 = (document.getElementById('new-password-confirm') || {}).value || '';
    if (!pw) { showToast('Please enter a password', 'error'); return; }
    if (pw.length < 8) { showToast('Password must be at least 8 characters', 'error'); return; }
    if (pw !== pw2) { showToast('Passwords do not match', 'error'); return; }
    try {
        var resp = await fetch(API_BASE + '/api/set-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw }),
        });
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (resp.ok) {
            showToast('Password set successfully', 'success');
            window._passwordSet = true;
            document.getElementById('new-password').value = '';
            document.getElementById('new-password-confirm').value = '';
        } else {
            showToast('Error: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Error setting password: ' + err.message, 'error');
    }
}

// ---- Music Assistant discover & login ----

var _maAutoSilentAuthAttempted = false;
var _maAutoSilentAuthFailed = false;
var _maConnected = false;
var _maReconfigureRequested = false;

async function _pollBackgroundJob(resultUrl, options) {
    options = options || {};
    var delayMs = options.delayMs || 1000;
    var maxAttempts = options.maxAttempts || 20;
    for (var attempt = 0; attempt < maxAttempts; attempt++) {
        await new Promise(function(resolve) { setTimeout(resolve, delayMs); });
        var pollResp = await fetch(resultUrl);
        if (pollResp.status === 401) { _handleUnauthorized(); return null; }
        var pollData = await pollResp.json().catch(function() { return {}; });
        if (pollData.status === 'running') continue;
        return pollData;
    }
    throw new Error(options.timeoutMessage || 'Background job timed out');
}

async function maDiscover() {
    var btn = document.getElementById('ma-discover-btn');
    var urlInput = document.getElementById('ma-login-url');
    var msgEl = document.getElementById('ma-login-msg');
    if (btn) btn.disabled = true;
    _setStatusText(msgEl, 'Scanning network...', 'muted');
    try {
        var resp = await fetch(API_BASE + '/api/ma/discover');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (data && data.job_id) {
            data = await _pollBackgroundJob(API_BASE + '/api/ma/discover/result/' + data.job_id, {
                delayMs: 1000,
                maxAttempts: 20,
                timeoutMessage: 'Discovery timed out'
            });
            if (!data) return null;
        }
        if (data.success === false && data.error) {
            _setStatusText(msgEl, '\u2716 ' + data.error, 'error');
            return data;
        }
        if (data.success && data.servers && data.servers.length > 0) {
            var s = data.servers[0];
            if (urlInput) urlInput.value = s.url;
            var foundMessage = '\u2714 Found: MA v' + (s.version || '?') + ' at ' + s.url;
            if (s.discovery_summary) foundMessage += ' — ' + s.discovery_summary;
            _setStatusText(msgEl, foundMessage, 'success');
            // Detect HA addon mode — check both bridge flag and MA server flag
            _setMaAddonMode(!!(data.is_addon || s.homeassistant_addon));
            return data;
        } else {
            _setStatusText(msgEl, '\u2716 No MA server found on network', 'error');
            return data;
        }
    } catch (err) {
        _setStatusText(msgEl, '\u2716 Discovery error: ' + err.message, 'error');
        return null;
    } finally {
        if (btn) btn.disabled = false;
    }
}

function _setMaAddonMode(isAddon) {
    var creds = document.getElementById('ma-login-creds');
    var hint = document.getElementById('ma-addon-hint');
    var loginBtn = document.getElementById('ma-login-btn');
    var autoSilentAuthRow = document.getElementById('ma-auto-silent-auth-row');
    if (isAddon) {
        if (creds) creds.hidden = true;
        if (hint) hint.hidden = false;
        if (loginBtn) loginBtn.hidden = true;
        if (autoSilentAuthRow) autoSilentAuthRow.hidden = false;
    } else {
        if (creds) creds.hidden = false;
        if (hint) hint.hidden = true;
        if (loginBtn) loginBtn.hidden = false;
        if (autoSilentAuthRow) autoSilentAuthRow.hidden = true;
    }
}

async function maHaConnect() {
    var maUrl = (document.getElementById('ma-login-url').value || '').trim();
    var msgEl = document.getElementById('ma-ha-login-msg');
    if (!maUrl) {
        _setStatusText(msgEl, 'Discover MA server first', 'error');
        return;
    }
    // In Ingress: try silent auth first, fall back to popup
    if (_isIngress()) {
        var btn = document.getElementById('ma-ha-login-btn');
        if (btn) btn.disabled = true;
        _setStatusText(msgEl, 'Connecting via Home Assistant...', 'muted');
        var ok = await _maSilentAuth(maUrl);
        if (btn) btn.disabled = false;
        if (ok) return;
        // Silent failed — fall through to popup
    }
    maHaAuthPopup();
}

function _syncNoticeStack() {
    var stack = document.getElementById('notice-stack');
    if (!stack) return;
    var visible = Array.prototype.some.call(stack.children || [], function(node) {
        return !!node && !node.hidden;
    });
    stack.hidden = !visible;
}

function _backendServiceToneClass(tone) {
    return tone === 'error' || tone === 'warning' ? 'warning' : 'info';
}

function _buildFinalizingStartupSummary(status, devices, fallbackSummary) {
    var startup = status && status.startup_progress ? status.startup_progress : null;
    var startupMessage = startup && startup.message ? String(startup.message) : '';
    var normalizedMessage = startupMessage.trim().toLowerCase();
    var genericMessages = {
        '': true,
        'startup complete': true,
        'demo restart complete': true,
        'finalizing startup': true,
    };
    var relevantDevices = Array.isArray(devices)
        ? devices.filter(function(dev) { return dev && dev.bt_management_enabled !== false && dev.enabled !== false; })
        : [];
    if (!relevantDevices.length) {
        return genericMessages[normalizedMessage] ? (fallbackSummary || 'Finalizing startup') : startupMessage;
    }

    var ready = 0;
    var reconnecting = 0;
    var waitingForSink = 0;
    var waitingForSendspin = 0;
    var waitingForBluetooth = 0;
    relevantDevices.forEach(function(dev) {
        if (dev.reconnecting || dev.ma_reconnecting) {
            reconnecting += 1;
            return;
        }
        if (!dev.bluetooth_connected) {
            waitingForBluetooth += 1;
            return;
        }
        if (!(dev.has_sink || getDeviceSinkName(dev))) {
            waitingForSink += 1;
            return;
        }
        if (!dev.server_connected) {
            waitingForSendspin += 1;
            return;
        }
        ready += 1;
    });

    var total = relevantDevices.length;
    var parts = [String(ready) + '/' + String(total) + ' speakers ready'];
    if (reconnecting) parts.push(String(reconnecting) + ' reconnecting');
    if (waitingForSink) parts.push(String(waitingForSink) + ' waiting for sink');
    if (waitingForSendspin) parts.push(String(waitingForSendspin) + ' waiting for Sendspin');
    if (waitingForBluetooth) parts.push(String(waitingForBluetooth) + ' waiting for Bluetooth');

    if (!genericMessages[normalizedMessage]) {
        parts.unshift(startupMessage);
    }
    return parts.join(' · ');
}

function _backendServiceIcon(kind, tone) {
    if (kind === 'unavailable' || tone === 'error' || tone === 'warning') {
        return _uiIconSvg('warning', 'ui-icon-svg');
    }
    return _uiIconSvg('refresh', 'ui-icon-svg');
}

function _buildBackendServiceStateHtml(state) {
    var tone = (state && state.tone) || 'info';
    var kind = (state && state.kind) || 'connecting';
    var title = (state && state.title) || 'Connecting to bridge';
    var summary = (state && state.summary) || 'Waiting for backend status. This page will refresh automatically.';
    var action = state && state.action ? state.action : {key: 'refresh_diagnostics', label: 'Retry now'};
    var iconAnimationClass = (kind === 'unavailable' || tone === 'warning' || tone === 'error')
        ? ' service-state-icon--pulse'
        : ' service-state-icon--spin';
    var actionHtml = action.key === 'refresh_diagnostics'
        ? '<a href="#" class="no-devices-link" onclick="return _retryBackendStatus()">' +
            _uiIconSvg('refresh', 'no-devices-link-icon') + '<span>' + escHtml(action.label || 'Retry now') + '</span>' +
          '</a>'
        : '<a href="#" class="no-devices-link" onclick="return _runEncodedOperatorGuidanceAction(\'' + _encodeGuidanceAction(action) + '\')">' +
            _uiIconSvg('info', 'no-devices-link-icon') + '<span>' + escHtml(action.label || 'Open diagnostics') + '</span>' +
          '</a>';
    return '<div class="no-devices-icon service-state-icon is-' + escHtml(tone) + iconAnimationClass + '">' +
            _backendServiceIcon(kind, tone) +
        '</div>' +
        '<div class="no-devices-text">' + escHtml(title) + '</div>' +
        '<div class="service-state-copy">' + escHtml(summary) + '</div>' +
        actionHtml;
}

function _renderBackendServicePlaceholder(state) {
    var grid = document.getElementById('status-grid');
    if (!grid) return;
    grid.classList.remove('list-view');
    grid.innerHTML = '<div id="service-state-hint" class="no-devices-hint service-state-hint is-' +
        escHtml((state && state.tone) || 'info') + '">' + _buildBackendServiceStateHtml(state) + '</div>';
}

function _setBackendServiceBanner(state) {
    var banner = document.getElementById('backend-service-banner');
    var titleEl = document.getElementById('backend-service-banner-title');
    var textEl = document.getElementById('backend-service-banner-text');
    var actionsEl = document.getElementById('backend-service-banner-actions');
    if (!banner || !titleEl || !textEl || !actionsEl) return;
    if (!state) {
        banner.hidden = true;
        titleEl.textContent = '';
        textEl.textContent = '';
        actionsEl.innerHTML = '';
        _syncNoticeStack();
        return;
    }
    banner.className = 'notice-card notice-card--' + _backendServiceToneClass(state.tone || 'info');
    titleEl.textContent = state.title || 'Connecting to bridge';
    textEl.textContent = state.summary || 'Waiting for backend status.';
    actionsEl.innerHTML = '<a href="#" class="notice-card-action notice-card-action--primary" onclick="return _retryBackendStatus()">' +
        escHtml(((state.action && state.action.key === 'refresh_diagnostics') ? state.action.label : 'Retry now') || 'Retry now') +
        '</a>';
    banner.hidden = false;
    _syncNoticeStack();
}

function _commitBackendServiceState(state) {
    _backendServiceState = state || null;
    document.body.classList.toggle('backend-ui-locked', !!_backendServiceState);
    _setBackendServiceBanner(_backendServiceState);
}

function _applyBackendServiceState(state) {
    _commitBackendServiceState(state);
}

function _isZeroClientStatusError(errorValue) {
    var normalized = String(errorValue || '').trim().toLowerCase();
    return normalized === 'no clients' || normalized === 'no clients configured' || normalized === 'no clients available';
}

function _updateMonitorElapsedSeconds() {
    if (!_updateMonitor || !_updateMonitor.startedAt) return 0;
    return Math.max(0, Math.round((Date.now() - _updateMonitor.startedAt) / 1000));
}

function _refreshPageAfterUpdate(version) {
    try {
        var url = new URL(window.location.href);
        url.searchParams.set('_ui_refresh', String(Date.now()));
        if (version) url.searchParams.set('_ui_ver', String(version));
        window.location.replace(url.toString());
    } catch (_) {
        window.location.reload();
    }
}

function _clearUpdateMonitor() {
    _updateMonitor = null;
}

function _renderLockedBackendState(state) {
    _applyBackendServiceState(state);
    lastDevices = [];
    lastGroups = [];
    _hideOperatorGuidance();
    _renderBackendServicePlaceholder(state);
    _updateGroupPanel();
    updateHealthIndicator([], _lastOperatorGuidance || null);
    _syncRestartBanner(null, state);
}

function _startUpdateMonitor(version, channel, options) {
    var opts = options || {};
    var targetVersion = _normalizeBridgeVersion(version);
    var initialVersion = _currentDisplayedBridgeVersion();
    _updateMonitor = {
        startedAt: Date.now(),
        targetVersion: targetVersion,
        targetReleaseLine: _bridgeVersionReleaseLine(targetVersion),
        initialVersion: initialVersion,
        initialReleaseLine: _bridgeVersionReleaseLine(initialVersion),
        channel: (channel || 'stable').toLowerCase(),
        alreadyRunning: !!opts.alreadyRunning,
        sawBackendUnavailable: false,
        sawRestartTransition: false,
    };
    _renderLockedBackendState(_deriveUpdateRuntimeState(null));
}

function _deriveUpdateRuntimeState(status, options) {
    if (!_updateMonitor) return null;
    var opts = options || {};
    var monitor = _updateMonitor;
    var startup = status && status.startup_progress ? status.startup_progress : null;
    var startupStatus = startup && startup.status ? String(startup.status) : '';
    var guidance = status && status.operator_guidance ? status.operator_guidance : null;
    var headerStatus = guidance && guidance.header_status ? guidance.header_status : null;
    var headerLabel = headerStatus && headerStatus.label ? String(headerStatus.label) : '';
    var normalizedHeaderLabel = headerLabel.trim().toLowerCase();
    var startupFinalizing = normalizedHeaderLabel === 'finalizing startup' || normalizedHeaderLabel === 'startup 90%';
    var backendUnavailable = !!opts.backendUnavailable;
    if (backendUnavailable) {
        monitor.sawBackendUnavailable = true;
    }
    if (
        backendUnavailable ||
        startupStatus === 'stopping' ||
        startupStatus === 'stopped' ||
        startupStatus === 'running' ||
        startupStatus === 'starting' ||
        startupStatus === 'error' ||
        startupFinalizing
    ) {
        monitor.sawRestartTransition = true;
    }

    var currentVersion = _normalizeBridgeVersion(status && status.version);
    var currentReleaseLine = _bridgeVersionReleaseLine(currentVersion);
    if (
        monitor.targetReleaseLine &&
        currentReleaseLine &&
        currentReleaseLine === monitor.targetReleaseLine &&
        (
            (monitor.initialReleaseLine && monitor.initialReleaseLine !== monitor.targetReleaseLine) ||
            monitor.sawRestartTransition
        ) &&
        !backendUnavailable &&
        startupStatus !== 'stopping' &&
        startupStatus !== 'stopped' &&
        startupStatus !== 'running' &&
        startupStatus !== 'starting' &&
        startupStatus !== 'error' &&
        !startupFinalizing
    ) {
        if (!monitor.refreshing) {
            monitor.refreshing = true;
            _refreshPageAfterUpdate(currentVersion);
        }
        return {
            kind: 'updating',
            tone: 'info',
            label: 'Update complete',
            title: 'Update complete',
            summary: 'Refreshing the page to load the updated UI…',
            action: {key: 'refresh_diagnostics', label: 'Retry now'},
            elapsedSeconds: _updateMonitorElapsedSeconds(),
        };
    }

    var label = 'Updating…';
    var title = 'Update in progress';
    var summary = monitor.alreadyRunning
        ? 'An update is already running. Waiting for the bridge service to restart.'
        : 'Preparing the update and waiting for the bridge service to restart.';

    if (monitor.targetVersion) {
        summary = (monitor.alreadyRunning ? 'Continuing update to ' : 'Preparing update to ') +
            'v' + monitor.targetVersion + '. Waiting for the bridge service to restart.';
    }
    if (backendUnavailable) {
        summary = 'Applying the update. Waiting for the bridge service to stop and come back online.';
    } else if (startupStatus === 'stopping' || startupStatus === 'stopped') {
        summary = 'Applying the update and restarting the bridge service.';
    } else if (startupStatus === 'running' || startupStatus === 'starting') {
        label = headerLabel || ('Startup ' + String(startup && startup.percent ? startup.percent : 0) + '%');
        title = label;
        summary = (startup && startup.message) || (headerStatus && headerStatus.summary) || 'Starting the updated bridge service.';
    } else if (startupFinalizing) {
        label = 'Startup 90%';
        title = 'Startup 90%';
        summary = _buildFinalizingStartupSummary(
            status,
            status && Array.isArray(status.devices) ? status.devices : [],
            (headerStatus && headerStatus.summary) || 'Finalizing startup'
        );
    } else if (headerStatus && headerStatus.summary && monitor.sawRestartTransition) {
        summary = headerStatus.summary;
    }

    return {
        kind: 'updating',
        tone: 'info',
        label: label,
        title: title,
        summary: summary,
        action: {key: 'refresh_diagnostics', label: 'Retry now'},
        elapsedSeconds: _updateMonitorElapsedSeconds(),
    };
}

function _deriveZeroDeviceRuntimeState(status, devices) {
    var guidance = status && status.operator_guidance ? status.operator_guidance : null;
    var headerStatus = guidance && guidance.header_status ? guidance.header_status : null;
    var startup = status && status.startup_progress ? status.startup_progress : null;
    var startupStatus = startup && startup.status ? String(startup.status) : '';
    var startupRunning = startupStatus === 'running' || startupStatus === 'starting';
    var startupRestarting = startupStatus === 'stopping' || startupStatus === 'stopped';
    var headerLabel = headerStatus && headerStatus.label ? String(headerStatus.label) : '';
    var normalizedHeaderLabel = headerLabel.trim().toLowerCase();
    var startupFinalizing = normalizedHeaderLabel === 'finalizing startup' || normalizedHeaderLabel === 'startup 90%';
    if (normalizedHeaderLabel === 'waiting for setup') {
        headerLabel = '';
    }
    if (status && status.error && !_isZeroClientStatusError(status.error)) {
        return {
            kind: 'unavailable',
            tone: 'warning',
            label: 'Backend unavailable',
            title: 'Bridge backend is unavailable',
            summary: String(status.error || 'The backend did not return a usable status payload.'),
            action: {key: 'refresh_diagnostics', label: 'Retry now'},
        };
    }
    var title = startupRestarting
        ? 'Restart in progress'
        : (startupFinalizing ? 'Startup 90%' : (headerLabel || (startupRunning ? 'Bridge is starting' : 'Restoring bridge state')));
    var label = startupRestarting
        ? 'Restart in progress'
        : (startupFinalizing ? 'Startup 90%' : (headerLabel || (startupRunning ? 'Starting bridge' : 'Restoring bridge state')));
    var summary = startupRestarting
        ? 'The bridge is restarting. Waiting for startup to resume.'
        : (startupRunning || startupFinalizing)
        ? (startupFinalizing
            ? _buildFinalizingStartupSummary(status, devices, 'Finalizing startup')
            : ((startup && startup.message) || (headerStatus && headerStatus.summary) || 'Waiting for bridge startup checks to finish.'))
        : ((startup && startup.message) || (headerStatus && headerStatus.summary) || 'Configured bridge devices are still reconnecting after restart.');
    if (startupRunning || startupRestarting || startupFinalizing) {
        return {
            kind: 'starting',
            tone: _backendServiceToneClass((headerStatus && headerStatus.tone) || 'info'),
            label: label,
            title: title,
            summary: summary,
            action: guidance && guidance.banner && guidance.banner.primary_action ? guidance.banner.primary_action : {key: 'refresh_diagnostics', label: 'Retry now'},
        };
    }
    return null;
}

async function _retryBackendStatus() {
    showToast('Retrying bridge status…', 'info');
    updateStatus();
    if (!_configLoading) loadConfig();
    return false;
}

function _setMaIntegrationBanner(message, title) {
    var banner = document.getElementById('ma-integration-banner');
    var titleEl = document.getElementById('ma-integration-banner-title');
    var text = document.getElementById('ma-integration-banner-text');
    if (!banner || !text || !titleEl) return;
    if (_guidanceOwnsMaBanner()) {
        banner.hidden = true;
        titleEl.textContent = '';
        text.textContent = '';
        _syncNoticeStack();
        return;
    }
    if (!message) {
        banner.hidden = true;
        titleEl.textContent = '';
        text.textContent = '';
        _syncNoticeStack();
        return;
    }
    titleEl.textContent = title || 'Music Assistant needs attention';
    text.textContent = message;
    banner.hidden = false;
    _syncNoticeStack();
}

var _lastOperatorGuidance = null;
var _onboardingAssistantExpanded = null;

function _guidancePreferenceKeys(visibilityKeys) {
    return {
        onboarding: (visibilityKeys && visibilityKeys.onboarding) || GUIDANCE_ONBOARDING_STORAGE_KEY,
        recovery: (visibilityKeys && visibilityKeys.recovery) || GUIDANCE_RECOVERY_STORAGE_KEY,
    };
}

function _isGuidanceVisible(preferenceKey) {
    if (!preferenceKey) return true;
    try {
        return window.localStorage.getItem(preferenceKey) !== 'hidden';
    } catch (_) {
        return true;
    }
}

function _setGuidanceVisible(preferenceKey, visible) {
    if (!preferenceKey) return;
    try {
        if (visible) {
            window.localStorage.removeItem(preferenceKey);
        } else {
            window.localStorage.setItem(preferenceKey, 'hidden');
        }
    } catch (_) {
        // Ignore storage failures and keep the current in-memory rendering.
    }
}

function _syncGuidancePreferenceControls(visibilityKeys) {
    var keys = _guidancePreferenceKeys(visibilityKeys);
    var onboardingToggle = document.getElementById('guidance-show-onboarding');
    if (onboardingToggle) onboardingToggle.checked = _isGuidanceVisible(keys.onboarding);
    var recoveryToggle = document.getElementById('guidance-show-recovery');
    if (recoveryToggle) recoveryToggle.checked = _isGuidanceVisible(keys.recovery);
}

function _dismissGuidance(preferenceKey) {
    var keys = _guidancePreferenceKeys(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
    if (preferenceKey === keys.onboarding) _onboardingAssistantExpanded = false;
    _setGuidanceVisible(preferenceKey, false);
    _syncGuidancePreferenceControls(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
    _applyOperatorGuidance(_lastOperatorGuidance);
    updateHealthIndicator(lastDevices || [], _lastOperatorGuidance || null);
    showToast('Guidance hidden. Restore it anytime in Configuration → General.', 'info');
    return false;
}

function _encodeGuidanceAction(action) {
    return encodeURIComponent(JSON.stringify({
        key: String((action && action.key) || ''),
        device_names: (action && action.device_names) || [],
        check_key: String((action && action.check_key) || ''),
        value: action ? action.value : undefined,
    }));
}

function _runEncodedOperatorGuidanceAction(encodedAction) {
    if (!encodedAction) return false;
    try {
        return _runOperatorGuidanceAction(JSON.parse(decodeURIComponent(encodedAction)));
    } catch (err) {
        console.warn('Invalid operator guidance action payload:', err);
        return false;
    }
}

function _renderGuidanceActionLink(action, options) {
    if (!action || !action.key) return '';
    var opts = options || {};
    var classes = ['notice-card-action'];
    if (opts.primary) classes.push('notice-card-action--primary');
    if (opts.menuItem) classes.push('notice-card-action--menu-item', 'ui-action-menu-item');
    return '<a href="#" class="' + classes.join(' ') + '" onclick="return _runEncodedOperatorGuidanceAction(\'' +
        _encodeGuidanceAction(action) +
    '\')">' + escHtml(action.label || 'Open diagnostics') + '</a>';
}

function _renderGuidanceActionMenu(actions, dismissHtml) {
    var visibleActions = (actions || []).filter(function(action) { return action && action.key; });
    if (!visibleActions.length && !dismissHtml) return '';
    if (visibleActions.length <= 1 && !dismissHtml) {
        return visibleActions.length ? _renderGuidanceActionLink(visibleActions[0]) : '';
    }
    var itemsHtml = visibleActions.map(function(action) {
        return _renderGuidanceActionLink(action, {menuItem: true});
    }).join('');
    if (dismissHtml) itemsHtml += dismissHtml;
    return '<details class="notice-action-menu ui-action-menu">' +
        '<summary class="notice-card-action notice-action-menu-toggle ui-action-menu-toggle">More actions</summary>' +
        '<div class="notice-action-menu-list ui-action-menu-list">' + itemsHtml + '</div>' +
    '</details>';
}

function _resetGuidancePreferences() {
    var keys = _guidancePreferenceKeys(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
    _onboardingAssistantExpanded = null;
    _setGuidanceVisible(keys.onboarding, true);
    _setGuidanceVisible(keys.recovery, true);
    _syncGuidancePreferenceControls(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
    _applyOperatorGuidance(_lastOperatorGuidance);
    updateHealthIndicator(lastDevices || [], _lastOperatorGuidance || null);
    showToast('Guidance visibility reset', 'success');
    return false;
}

/* ---------- Experimental features toggle ---------- */

function _isExperimentalEnabled() {
    try {
        return window.localStorage.getItem(EXPERIMENTAL_STORAGE_KEY) === 'enabled';
    } catch (_) {
        return false;
    }
}

function _setExperimentalEnabled(enabled) {
    try {
        if (enabled) {
            window.localStorage.setItem(EXPERIMENTAL_STORAGE_KEY, 'enabled');
        } else {
            window.localStorage.removeItem(EXPERIMENTAL_STORAGE_KEY);
        }
    } catch (_) { /* ignore storage failures */ }
}

function _applyExperimentalVisibility() {
    var show = _isExperimentalEnabled();
    document.querySelectorAll('[data-experimental]').forEach(function(el) {
        el.style.display = show ? '' : 'none';
    });
    var toggle = document.getElementById('guidance-show-experimental');
    if (toggle && toggle.checked !== show) toggle.checked = show;
}

function _openDiagnosticsPanel() {
    var details = document.getElementById('diag-details');
    if (!details) return false;
    details.open = true;
    onDiagToggle(details);
    details.scrollIntoView({behavior: 'smooth', block: 'start'});
    return false;
}

function _hasOnboardingChecklist(card) {
    return !!(card && card.checklist);
}

function _canRenderOnboardingAssistant(card) {
    return _hasOnboardingChecklist(card) && _isGuidanceVisible(card.preference_key);
}

function _shouldShowOnboardingAssistantBanner(card, options) {
    if (!_canRenderOnboardingAssistant(card)) return false;
    return _isOnboardingAssistantExpanded(card, options) || !!card.show_by_default;
}

function _isOnboardingAssistantExpanded(card, options) {
    if (!_canRenderOnboardingAssistant(card)) return false;
    var opts = options || {};
    if (_onboardingAssistantExpanded === null) return !!opts.showByDefault;
    return !!_onboardingAssistantExpanded;
}

function _onboardingShowByDefault(guidance) {
    var card = guidance && guidance.onboarding_card ? guidance.onboarding_card : null;
    return !!(card && card.show_by_default);
}

function _toggleOnboardingAssistant() {
    var guidance = _lastOperatorGuidance;
    var card = guidance && guidance.onboarding_card ? guidance.onboarding_card : null;
    if (!card || !card.checklist) return false;
    var showByDefault = _onboardingShowByDefault(guidance);
    var currentlyExpanded = _isOnboardingAssistantExpanded(card, {showByDefault: showByDefault});
    _onboardingAssistantExpanded = !currentlyExpanded;
    if (_onboardingAssistantExpanded && card.preference_key) _setGuidanceVisible(card.preference_key, true);
    _syncGuidancePreferenceControls(guidance && guidance.visibility_keys);
    _applyOperatorGuidance(guidance);
    updateHealthIndicator(lastDevices || [], guidance || null);
    if (!currentlyExpanded) {
        var banner = document.getElementById('onboarding-assistant-banner');
        if (banner && !banner.hidden) {
            banner.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
    }
    return false;
}

function _onboardingAssistantToggleLabel(expanded) {
    return expanded ? 'Hide checklist' : 'Show checklist';
}

function _onboardingAssistantToggleTitle(expanded) {
    return expanded ? 'Hide the setup checklist.' : 'Show the setup checklist.';
}

function _renderOnboardingAssistantToggle(expanded, options) {
    var opts = options || {};
    var classes = ['notice-card-action', 'onboarding-toggle-action'];
    if (opts.primary) classes.push('notice-card-action--primary');
    return '<button type="button" class="' + classes.join(' ') + '"' +
        ' aria-controls="onboarding-assistant-banner"' +
        ' aria-expanded="' + (expanded ? 'true' : 'false') + '"' +
        ' title="' + escHtmlAttr(_onboardingAssistantToggleTitle(expanded)) + '"' +
        ' onclick="return _toggleOnboardingAssistant()">' +
        escHtml(_onboardingAssistantToggleLabel(expanded)) +
    '</button>';
}

function _renderOnboardingHeaderToggle(expanded) {
    return '<button type="button" class="guidance-health-action"' +
        ' aria-controls="onboarding-assistant-banner"' +
        ' aria-expanded="' + (expanded ? 'true' : 'false') + '"' +
        ' title="' + escHtmlAttr(_onboardingAssistantToggleTitle(expanded)) + '"' +
        ' onclick="return _toggleOnboardingAssistant()">' +
        '<span class="guidance-health-action-label">' + escHtml(_onboardingAssistantToggleLabel(expanded)) + '</span>' +
        '<span class="guidance-health-action-icon" aria-hidden="true">' +
            _uiIconSvg(expanded ? 'chevron-up' : 'chevron-down', 'ui-icon-svg') +
        '</span>' +
    '</button>';
}

function _findDeviceIndexByName(deviceName) {
    if (!deviceName || !lastDevices || !lastDevices.length) return -1;
    for (var i = 0; i < lastDevices.length; i++) {
        var dev = lastDevices[i];
        if (dev && (dev.player_name || '') === deviceName) return i;
    }
    return -1;
}

function _findDeviceIndicesByNames(deviceNames) {
    if (!deviceNames || !deviceNames.length) return [];
    return deviceNames
        .map(function(name) { return _findDeviceIndexByName(name); })
        .filter(function(index, position, values) { return index >= 0 && values.indexOf(index) === position; });
}

function _summarizeGuidanceDeviceNames(deviceNames, limit) {
    var names = Array.isArray(deviceNames) ? deviceNames.filter(Boolean) : [];
    var maxItems = limit || 5;
    var visibleNames = names.slice(0, maxItems);
    var lines = visibleNames.map(function(name) { return '• ' + String(name); });
    if (names.length > visibleNames.length) {
        lines.push('• +' + String(names.length - visibleNames.length) + ' more');
    }
    return lines;
}

function _confirmGuidanceDeviceBatch(actionLabel, deviceNames, summary) {
    var names = Array.isArray(deviceNames) ? deviceNames.filter(Boolean) : [];
    if (names.length <= 1) return true;
    var messageLines = [
        summary || ('Apply ' + String(actionLabel || 'this action') + ' to ' + String(names.length) + ' devices?'),
        '',
    ].concat(_summarizeGuidanceDeviceNames(names, 6));
    return window.confirm(messageLines.join('\n'));
}

function _runOnboardingAssistantAction(actionKey) {
    if (!actionKey) return false;
    if (actionKey === 'open_bluetooth_settings') {
        _goToAdapters();
        return false;
    }
    if (actionKey === 'scan_devices') {
        openConfigAndAddDevice({expandPaired: true});
        return false;
    }
    if (actionKey === 'open_devices_settings') {
        var opened = _openConfigPanel('devices', 'config-panel-devices', 'start');
        _highlightConfigTarget((opened && opened.target) || document.getElementById('config-panel-devices'));
        return false;
    }
    if (actionKey === 'open_latency_settings') {
        return openLatencySettings();
    }
    if (actionKey === 'open_ma_settings') {
        return openMaTokenSettings();
    }
    if (actionKey === 'open_diagnostics') {
        return _openDiagnosticsPanel();
    }
    return false;
}

async function _retryMaDiscoveryFromRecovery() {
    try {
        showToast('Retrying Music Assistant discovery…', 'info');
        var resp = await fetch(API_BASE + '/api/ma/discover');
        if (resp.status === 401) { _handleUnauthorized(); return false; }
        var data = await resp.json();
        if (data && data.job_id) {
            data = await _pollBackgroundJob(API_BASE + '/api/ma/discover/result/' + data.job_id, {
                timeoutMs: 15000,
                intervalMs: 750,
            });
        }
        if (data && data.success === false) {
            showToast('Music Assistant discovery failed: ' + (data.error || 'Unknown error'), 'error');
            return false;
        }
        showToast('Music Assistant discovery finished', 'success');
        updateStatus();
        return false;
    } catch (err) {
        showToast('Music Assistant discovery failed: ' + err.message, 'error');
        return false;
    }
}

async function _runGuidanceDeviceBatch(deviceNames, runner, pendingMessage, successLabel, options) {
    var opts = options || {};
    var indices = _findDeviceIndicesByNames(deviceNames || []);
    if (!indices.length) {
        showToast('Affected devices are no longer present in the current bridge status', 'error');
        return false;
    }
    var resolvedDeviceNames = indices.map(function(index) {
        var dev = lastDevices && lastDevices[index];
        return (dev && dev.player_name) || (deviceNames && deviceNames[index]) || ('Device ' + String(index + 1));
    });
    if (!_confirmGuidanceDeviceBatch(
        opts.actionLabel || successLabel || pendingMessage || 'this bulk action',
        resolvedDeviceNames,
        opts.confirmSummary
    )) {
        return false;
    }
    if (pendingMessage) showToast(pendingMessage, 'info');
    var results = await Promise.all(indices.map(function(index) { return runner(index); }));
    var successCount = results.filter(function(result) { return !!(result && result.success); }).length;
    var failed = results.length - successCount;
    if (failed === 0) {
        showToast(successLabel || 'Bulk action queued', 'success');
    } else if (successCount === 0) {
        showToast('Bulk action failed for all selected devices', 'error');
    } else {
        showToast(successCount + ' succeeded, ' + failed + ' failed', 'warning');
    }
    updateStatus();
    return false;
}

function _guidanceOwnsMaBanner() {
    return !!(
        _lastOperatorGuidance &&
        _lastOperatorGuidance.issue_groups &&
        _lastOperatorGuidance.issue_groups.some(function(issue) { return issue && issue.key === 'ma_auth'; })
    );
}

function _runOperatorGuidanceAction(action) {
    var actionKey = action && action.key ? action.key : '';
    var deviceNames = action && action.device_names ? action.device_names : [];
    if (!actionKey) return false;
    if (
        actionKey === 'open_bluetooth_settings' ||
        actionKey === 'scan_devices' ||
        actionKey === 'open_devices_settings' ||
        actionKey === 'open_latency_settings' ||
        actionKey === 'open_ma_settings' ||
        actionKey === 'open_diagnostics'
    ) {
        return _runOnboardingAssistantAction(actionKey);
    }
    if (actionKey === 'refresh_diagnostics') {
        if (document.getElementById('diag-details') && document.getElementById('diag-details').open) {
            reloadDiagnostics();
        }
        updateStatus();
        showToast('Rerunning bridge checks…', 'info');
        return false;
    }
    if (actionKey === 'rerun_safe_check') {
        _rerunSafeCheck(action && action.check_key ? action.check_key : '', deviceNames, {
            actionLabel: action && action.label ? action.label : 'Rerun safe check',
        });
        return false;
    }
    if (actionKey === 'retry_ma_discovery') {
        _retryMaDiscoveryFromRecovery();
        return false;
    }
    if (actionKey === 'apply_latency_recommended') {
        _applyLatencyPreset(action && action.value != null ? action.value : null);
        return false;
    }
    if (actionKey === 'reconnect_device') {
        var reconnectIndex = _findDeviceIndexByName(deviceNames[0]);
        if (reconnectIndex < 0) {
            showToast('Device not found in current bridge status', 'error');
            return _runOnboardingAssistantAction('open_devices_settings');
        }
        btReconnect(reconnectIndex);
        return false;
    }
    if (actionKey === 'pair_device') {
        var pairIndex = _findDeviceIndexByName(deviceNames[0]);
        if (pairIndex < 0) {
            showToast('Device not found in current bridge status', 'error');
            return _runOnboardingAssistantAction('open_devices_settings');
        }
        btPairConfiguredDevice(pairIndex);
        return false;
    }
    if (actionKey === 'reconnect_devices') {
        _runGuidanceDeviceBatch(
            deviceNames,
            btReconnect,
            'Reconnecting affected devices…',
            'Reconnect queued for ' + deviceNames.length + ' devices',
            {
                actionLabel: action && action.label ? action.label : 'Reconnect affected devices',
                confirmSummary: 'Reconnect these devices now?',
            }
        );
        return false;
    }
    if (actionKey === 'toggle_bt_management') {
        var managementIndex = _findDeviceIndexByName(deviceNames[0]);
        if (managementIndex < 0) {
            showToast('Device not found in current bridge status', 'error');
            return _runOnboardingAssistantAction('open_devices_settings');
        }
        btToggleManagement(managementIndex);
        return false;
    }
    if (actionKey === 'toggle_bt_management_devices') {
        _runGuidanceDeviceBatch(
            deviceNames,
            btToggleManagement,
            'Updating Bluetooth management for affected devices…',
            'Bluetooth management updated for ' + deviceNames.length + ' devices',
            {
                actionLabel: action && action.label ? action.label : 'Update Bluetooth management',
                confirmSummary: 'Apply this Bluetooth management action to these devices?',
            }
        );
        return false;
    }
    return false;
}

async function _rerunSafeCheck(checkKey, deviceNames, options) {
    if (!checkKey) {
        showToast('No safe check was selected', 'error');
        return false;
    }
    var opts = options || {};
    if (!_confirmGuidanceDeviceBatch(
        opts.actionLabel || ('Rerun ' + String(checkKey).replace(/_/g, ' ')),
        deviceNames || [],
        'Rerun this bridge check for these devices?'
    )) {
        return false;
    }
    var payload = {check_key: String(checkKey || '')};
    if (deviceNames && deviceNames.length) payload.device_names = deviceNames;
    showToast('Rerunning ' + String(checkKey).replace(/_/g, ' ') + '…', 'info');
    try {
        var resp = await fetch(API_BASE + '/api/checks/rerun', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (!resp.ok) {
            throw new Error((data && data.error) || (data && data.summary) || ('Request failed: ' + resp.status));
        }
        showToast(
            (data && data.summary) || 'Safe check finished',
            data && data.status === 'error' ? 'error' : (data && data.status === 'warning' ? 'warning' : 'success')
        );
        if (document.getElementById('diag-details') && document.getElementById('diag-details').open) reloadDiagnostics();
        updateStatus();
    } catch (err) {
        showToast(err && err.message ? err.message : 'Safe check failed', 'error');
    }
    return false;
}

async function _applyLatencyPreset(value) {
    if (value == null || value === '') {
        showToast('No latency value was selected', 'error');
        return false;
    }
    showToast('Saving Pulse latency recommendation…', 'info');
    try {
        var resp = await fetch(API_BASE + '/api/latency/apply', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({pulse_latency_msec: Number(value)}),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (!resp.ok) {
            throw new Error((data && data.error) || ('Request failed: ' + resp.status));
        }
        showToast(data.summary || 'Latency setting saved', 'success');
        if (!_configLoading) loadConfig();
        if (document.getElementById('diag-details') && document.getElementById('diag-details').open) reloadDiagnostics();
    } catch (err) {
        showToast(err && err.message ? err.message : 'Could not save latency setting', 'error');
    }
    return false;
}

function _downloadRecoveryTimeline() {
    window.location.href = API_BASE + '/api/recovery/timeline/download';
    return false;
}

function _onboardingStageLabel(step) {
    if (!step) return 'Upcoming';
    if (step.stage === 'complete') return 'Done';
    if (step.stage === 'current') return step.status === 'error' ? 'Blocked' : 'Next';
    return 'Upcoming';
}

function _hasOnboardingStepDetailValue(value) {
    if (value === null || value === undefined) return false;
    if (Array.isArray(value)) return value.length > 0;
    return String(value).trim() !== '';
}

function _onboardingStepDetailLabel(key) {
    var labels = {
        paired_devices: 'Paired devices',
        configured_devices: 'Configured devices',
        connected_devices: 'Connected devices',
        sink_ready_devices: 'Ready sinks',
        missing_sink_devices: 'Missing sinks',
        configured_url: 'Configured URL',
        has_token: 'Has token',
        has_username: 'Has username',
        pulse_latency_msec: 'Pulse latency',
        custom_device_delays: 'Device delays',
        sinks: 'Detected sinks',
        system: 'Audio backend',
    };
    if (labels[key]) return labels[key];
    return String(key || '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, function(chr) { return chr.toUpperCase(); });
}

function _onboardingStepDetailValue(value) {
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    return String(value);
}

function _renderOnboardingStepDetails(details) {
    if (!details || typeof details !== 'object') return '';
    var items = Object.keys(details).filter(function(key) {
        return _hasOnboardingStepDetailValue(details[key]);
    }).map(function(key) {
        var value = details[key];
        return '<div class="onboarding-step-detail">' +
            '<div class="onboarding-step-detail-label">' + escHtml(_onboardingStepDetailLabel(key)) + '</div>' +
            '<div class="onboarding-step-detail-value">' + escHtml(_onboardingStepDetailValue(value)) + '</div>' +
        '</div>';
    });
    if (!items.length) return '';
    return '<div class="onboarding-step-detail-list">' + items.join('') + '</div>';
}

function _getOnboardingStepFacts(details, limit) {
    if (!details || typeof details !== 'object') return [];
    return Object.keys(details).filter(function(key) {
        return _hasOnboardingStepDetailValue(details[key]);
    }).slice(0, limit || 2).map(function(key) {
        return {
            label: _onboardingStepDetailLabel(key),
            value: _onboardingStepDetailValue(details[key]),
        };
    });
}

function _renderOnboardingStepFacts(details, limit) {
    var facts = _getOnboardingStepFacts(details, limit);
    if (!facts.length) return '';
    return '<div class="onboarding-step-facts">' + facts.map(function(fact) {
        return '<span class="onboarding-step-fact">' +
            '<span class="onboarding-step-fact-label">' + escHtml(fact.label) + ':</span>' +
            '<span class="onboarding-step-fact-value">' + escHtml(fact.value) + '</span>' +
        '</span>';
    }).join('') + '</div>';
}

function _renderOnboardingStepGuidance(actions) {
    if (!actions || !actions.length) return '';
    return '<ul class="onboarding-step-guidance">' + actions.map(function(action) {
        return '<li>' + escHtml(action || '') + '</li>';
    }).join('') + '</ul>';
}

function _stepHasOnboardingAction(step) {
    return !!(step && step.recommended_action && step.recommended_action.key);
}

function _hasInteractiveOnboardingContent(step) {
    return !!(
        step &&
        step.stage !== 'complete' &&
        (
            _stepHasOnboardingAction(step) ||
            ((step.actions || []).length > 0) ||
            (step.details && Object.keys(step.details).some(function(key) {
                return _hasOnboardingStepDetailValue(step.details[key]);
            }))
        )
    );
}

function _renderOnboardingStepBody(step) {
    if (!step) return '';
    var detailsHtml = _renderOnboardingStepDetails(step.details || {});
    var guidanceHtml = _renderOnboardingStepGuidance(step.actions || []);
    var actionHtml = _stepHasOnboardingAction(step)
        ? '<div class="onboarding-step-actions">' +
            _renderGuidanceActionLink(step.recommended_action, {primary: true}) +
        '</div>'
        : '';
    return '<div class="onboarding-step-summary">' + escHtml(step.summary || '') + '</div>' +
        detailsHtml +
        guidanceHtml +
        actionHtml;
}

function _renderOnboardingCheckpoints(checkpoints) {
    if (!checkpoints || !checkpoints.length) return '';
    return checkpoints.map(function(checkpoint) {
        return '<div class="onboarding-checkpoint' + (checkpoint.reached ? ' is-reached' : '') + '"' +
            ' title="' + escHtml(checkpoint.summary || '') + '">' +
            '<span class="onboarding-checkpoint-icon">' +
            (checkpoint.reached ? _uiIconSvg('check', 'ui-icon-svg') : _uiIconSvg('status-neutral', 'ui-icon-svg')) +
            '</span>' +
            '<span>' + escHtml(checkpoint.label) + '</span>' +
        '</div>';
    }).join('');
}

function _renderOnboardingSteps(steps) {
    if (!steps || !steps.length) return '';
    return steps.map(function(step, index) {
        var badgeTone = step.status === 'error' ? 'error' : step.status === 'warning' ? 'warning' : 'success';
        var showSummary = step.stage === 'current' && !!step.summary;
        var summaryHtml = showSummary
            ? '<div class="onboarding-step-summary">' + escHtml(step.summary || '') + '</div>'
            : '';
        var factsHtml = step.stage === 'current'
            ? _renderOnboardingStepFacts(step.details || {}, 2)
            : '';
        var noteHtml = step.stage === 'current' && step.actions && step.actions.length
            ? '<div class="onboarding-step-note">' + escHtml(step.actions[0] || '') + '</div>'
            : '';
        var actionHtml = step.stage === 'current' && _stepHasOnboardingAction(step)
            ? '<div class="onboarding-step-actions">' +
                _renderGuidanceActionLink(step.recommended_action, {primary: true}) +
            '</div>'
            : '';
        var indicator = step.stage === 'complete'
            ? '<span class="onboarding-step-indicator-icon">' + _uiIconSvg('check', 'ui-icon-svg') + '</span>'
            : '<span class="onboarding-step-indicator-number">' + escHtml(String(index + 1)) + '</span>';
        var indicatorLabel = step.stage === 'complete' ? 'Completed step' : 'Step ' + String(index + 1);
        return '<div class="onboarding-step is-' + escHtml(step.stage || 'upcoming') + '">' +
            '<div class="onboarding-step-indicator" aria-label="' + escHtmlAttr(indicatorLabel) + '">' + indicator + '</div>' +
            '<div class="onboarding-step-main">' +
                '<div class="onboarding-step-title">' + escHtml(step.title || step.key || 'Step') + '</div>' +
                summaryHtml +
                factsHtml +
                noteHtml +
                actionHtml +
            '</div>' +
            _renderMetaStatusBadgeHtml({
                className: 'onboarding-step-badge',
                tone: badgeTone,
                label: _onboardingStageLabel(step),
                title: step.summary || _onboardingStageLabel(step),
            }) +
        '</div>';
    }).join('');
}

function _renderOnboardingProgressSummary(checklist) {
    if (!checklist) return '';
    var totalSteps = Number(checklist.total_steps || 0);
    var completedSteps = Number(checklist.completed_steps || 0);
    var parts = [];
    if (checklist.journey_title) {
        parts.push('<span class="onboarding-progress-pill onboarding-progress-pill--journey">' + escHtml(checklist.journey_title) + '</span>');
    }
    if (totalSteps > 0) {
        parts.push('<span class="onboarding-progress-pill onboarding-progress-pill--strong">' +
            escHtml(String(completedSteps)) + '/' + escHtml(String(totalSteps)) + ' complete' +
        '</span>');
    }
    if (checklist.current_step_title) {
        parts.push('<span class="onboarding-progress-pill">Next: ' + escHtml(checklist.current_step_title) + '</span>');
    } else if ((checklist.progress_percent || 0) >= 100) {
        parts.push('<span class="onboarding-progress-pill">Setup complete</span>');
    }
    return '<div class="onboarding-progress-pills">' + parts.join('') + '</div>' +
        _renderOnboardingPhases(checklist.phases || []);
}

function _renderOnboardingPhases(phases) {
    if (!phases || !phases.length) return '';
    return '<div class="onboarding-phase-rail">' + phases.map(function(phase, index) {
        return '<div class="onboarding-phase is-' + escHtml(phase.status || 'upcoming') + '">' +
            '<div class="onboarding-phase-marker">' + escHtml(String(index + 1)) + '</div>' +
            '<div class="onboarding-phase-copy">' +
                '<div class="onboarding-phase-title">' + escHtml(phase.title || 'Phase') + '</div>' +
                '<div class="onboarding-phase-summary">' + escHtml(phase.summary || '') + '</div>' +
            '</div>' +
        '</div>';
    }).join('') + '</div>';
}

function _renderCollapsedOnboardingSummary(checklist, fallbackSummary) {
    if (!checklist) return fallbackSummary || '';
    var totalSteps = Number(checklist.total_steps || 0);
    var completedSteps = Number(checklist.completed_steps || 0);
    var parts = [];
    if (checklist.journey_title) {
        parts.push(String(checklist.journey_title));
    }
    if (totalSteps > 0) {
        parts.push(String(completedSteps) + '/' + String(totalSteps) + ' complete');
    } else if ((checklist.progress_percent || 0) >= 100) {
        parts.push('Setup complete');
    }
    if (checklist.current_step_title) {
        parts.push('Next: ' + String(checklist.current_step_title));
    }
    return parts.length ? parts.join(' - ') : (fallbackSummary || 'Review the bridge setup checklist.');
}

function _setOnboardingAssistantBanner(card, options) {
    var banner = document.getElementById('onboarding-assistant-banner');
    var titleEl = document.getElementById('onboarding-assistant-title');
    var textEl = document.getElementById('onboarding-assistant-text');
    var progressEl = document.getElementById('onboarding-assistant-progress');
    var checkpointsEl = document.getElementById('onboarding-assistant-checkpoints');
    var stepsEl = document.getElementById('onboarding-assistant-steps');
    var actionsEl = document.getElementById('onboarding-assistant-actions');
    var opts = options || {};
    if (!banner || !titleEl || !textEl || !progressEl || !checkpointsEl || !stepsEl || !actionsEl) return;
    if (!card || !card.checklist || !_shouldShowOnboardingAssistantBanner(card, opts)) {
        banner.hidden = true;
        banner.className = 'notice-card notice-card--info onboarding-card';
        titleEl.textContent = '';
        textEl.textContent = '';
        progressEl.hidden = true;
        progressEl.innerHTML = '';
        checkpointsEl.hidden = true;
        checkpointsEl.innerHTML = '';
        stepsEl.innerHTML = '';
        actionsEl.innerHTML = '';
        _syncNoticeStack();
        return;
    }

    var checklist = card.checklist || {};
    var isExpanded = _isOnboardingAssistantExpanded(card, opts);
    banner.className = 'notice-card notice-card--info onboarding-card' + (isExpanded ? '' : ' is-collapsed');
    titleEl.textContent = card.headline || checklist.headline || 'Setup checklist';
    textEl.textContent = isExpanded
        ? (card.summary || checklist.summary || 'Review the bridge setup checklist.')
        : _renderCollapsedOnboardingSummary(checklist, card.summary || checklist.summary || '');
    var progressHtml = _renderOnboardingProgressSummary(checklist);
    progressEl.hidden = !isExpanded || !progressHtml;
    progressEl.innerHTML = isExpanded ? progressHtml : '';
    checkpointsEl.hidden = true;
    checkpointsEl.innerHTML = '';
    stepsEl.hidden = !isExpanded;
    stepsEl.innerHTML = isExpanded ? _renderOnboardingSteps(checklist.steps || []) : '';

    var secondaryActions = card.secondary_actions || [];
    var dismissHtml = '';
    if (card.dismissible && card.preference_key) {
        dismissHtml = '<a href="#" class="notice-card-action notice-card-action--menu-item" onclick="return _dismissGuidance(\'' +
            escHtml(card.preference_key) +
        '\')">Don’t show again</a>';
    }
    actionsEl.innerHTML = _renderOnboardingAssistantToggle(isExpanded, {primary: !isExpanded}) +
        _renderGuidanceActionMenu(secondaryActions, dismissHtml);

    banner.hidden = false;
    _syncNoticeStack();
}

function _recoveryNoticeToneClass(tone) {
    if (tone === 'error' || tone === 'err') return 'notice-card--danger';
    if (tone === 'ok') return 'notice-card--info';
    return 'notice-card--warning';
}

function _renderRecoveryIssuePills(issues) {
    if (!issues || !issues.length) return '';
    var visibleIssues = issues.length > 2 ? issues.slice(0, 2) : issues;
    var extraCount = Math.max(0, issues.length - visibleIssues.length);
    var pillsHtml = visibleIssues.map(function(issue) {
        var tone = issue.severity === 'error' ? 'error' : 'warning';
        return _renderMetaStatusBadgeHtml({
            className: 'recovery-issue-pill',
            tone: tone,
            title: issue.title || 'Issue',
            leadingHtml: '<span class="recovery-issue-pill-icon">' +
                (tone === 'error' ? _uiIconSvg('warning', 'ui-icon-svg') : _uiIconSvg('info', 'ui-icon-svg')) +
            '</span>',
            label: issue.title || 'Issue',
        });
    }).join('');
    if (extraCount > 0) {
        pillsHtml += _renderMetaStatusBadgeHtml({
            className: 'recovery-issue-pill recovery-issue-pill-more',
            tone: 'neutral',
            title: extraCount === 1 ? '1 more issue needs attention' : String(extraCount) + ' more issues need attention',
            label: '+' + String(extraCount) + ' more',
        });
    }
    return pillsHtml;
}

function _setRecoveryAssistantBanner(guidance) {
    var banner = document.getElementById('recovery-assistant-banner');
    var titleEl = document.getElementById('recovery-assistant-title');
    var textEl = document.getElementById('recovery-assistant-text');
    var issuesEl = document.getElementById('recovery-assistant-issues');
    var actionsEl = document.getElementById('recovery-assistant-actions');
    if (!banner || !titleEl || !textEl || !issuesEl || !actionsEl) return;
    var notice = guidance && guidance.banner ? guidance.banner : null;
    var issues = guidance && guidance.issue_groups ? guidance.issue_groups : [];
    var showBanner = !!(notice && _isGuidanceVisible(notice.preference_key));
    if (!showBanner) {
        banner.hidden = true;
        titleEl.textContent = '';
        textEl.textContent = '';
        issuesEl.hidden = true;
        issuesEl.innerHTML = '';
        actionsEl.innerHTML = '';
        _syncNoticeStack();
        return;
    }

    banner.className = 'notice-card recovery-card ' + _recoveryNoticeToneClass(notice.tone || 'warning');
    titleEl.textContent = notice.headline || 'Recovery guidance';
    textEl.textContent = notice.summary || 'Review the latest recovery guidance.';
    issuesEl.hidden = !issues.length;
    issuesEl.innerHTML = _renderRecoveryIssuePills(issues);

    var primaryAction = notice.primary_action || {key: 'open_diagnostics', label: 'Open diagnostics'};
    var secondaryActions = (notice.secondary_actions || []).filter(function(action) {
        return !primaryAction || action.key !== primaryAction.key;
    });
    var dismissHtml = '';
    if (notice.dismissible && notice.preference_key) {
        dismissHtml = '<a href="#" class="notice-card-action notice-card-action--menu-item" onclick="return _dismissGuidance(\'' +
            escHtml(notice.preference_key) +
        '\')">Don’t show again</a>';
    }
    var actionsHtml = _renderGuidanceActionLink(primaryAction, {primary: true});
    actionsHtml += _renderGuidanceActionMenu(secondaryActions, dismissHtml);
    actionsEl.innerHTML = actionsHtml;
    banner.hidden = false;
    _syncNoticeStack();
}

function _applyOperatorGuidance(guidance) {
    _lastOperatorGuidance = guidance || null;
    _syncGuidancePreferenceControls(guidance && guidance.visibility_keys);
    _setOnboardingAssistantBanner(
        guidance && guidance.onboarding_card ? guidance.onboarding_card : null,
        {showByDefault: _onboardingShowByDefault(guidance)}
    );
    _setRecoveryAssistantBanner(guidance || null);
    _syncEmptyStatePlaceholder(guidance || null);
}

function _hideOperatorGuidance() {
    _setOnboardingAssistantBanner(null);
    _setRecoveryAssistantBanner(null);
}

function openMaTokenSettings() {
    var opened = _openConfigPanel('ma', 'ma-connect-panel', 'start');
    toggleMaForm(true);
    var highlightTarget = document.getElementById('ma-auth-card') || document.getElementById('ma-conn-form') || (opened && opened.target);
    _highlightConfigTarget(highlightTarget);
    var focusTarget = document.getElementById('ma-ha-login-btn');
    if (!focusTarget || focusTarget.hidden || focusTarget.offsetParent === null) {
        focusTarget = document.getElementById('ma-login-btn');
    }
    if (!focusTarget || focusTarget.hidden || focusTarget.offsetParent === null) {
        focusTarget = document.getElementById('ma-login-url');
    }
    if (!focusTarget || focusTarget.hidden || focusTarget.offsetParent === null) {
        focusTarget = highlightTarget;
    }
    if (focusTarget && typeof focusTarget.focus === 'function') {
        focusTarget.focus({preventScroll: true});
    }
    return false;
}

function maHaAuthPopup() {
    var maUrl = (document.getElementById('ma-login-url').value || '').trim();
    var msgEl = document.getElementById('ma-ha-login-msg');
    if (!maUrl) {
        _setStatusText(msgEl, 'Discover MA server first', 'error');
        return;
    }
    var w = 400, h = 520;
    var left = (screen.width - w) / 2, top = (screen.height - h) / 2;
    var popup = window.open(
        API_BASE + '/api/ma/ha-auth-page?ma_url=' + encodeURIComponent(maUrl),
        'ha_auth', 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top
    );
    if (!popup) {
        _setStatusText(msgEl, 'Popup blocked — allow popups for this site', 'error');
        return;
    }
    function onMessage(ev) {
        if (ev.data && ev.data.type === 'ma-ha-auth-done' && ev.data.success) {
            window.removeEventListener('message', onMessage);
            _setMaStatus(true, ev.data.username, ev.data.url);
            var urlField = document.querySelector('input[name="MA_API_URL"]');
            if (urlField) urlField.value = ev.data.url;
            _setStatusText(msgEl, '\u2714 ' + (ev.data.message || 'Connected'), 'success');
            showToast('\u2714 Connected to Music Assistant via HA', 'success');
            loadConfig({preserveDirtyBaseline: true});
        }
    }
    window.addEventListener('message', onMessage);
}

async function maLogin() {
    var btn = document.getElementById('ma-login-btn');
    var msgEl = document.getElementById('ma-login-msg');
    var url = (document.getElementById('ma-login-url').value || '').trim();
    var user = (document.getElementById('ma-login-user').value || '').trim();
    var pass = document.getElementById('ma-login-pass').value || '';
    if (!user || !pass) {
        _setStatusText(msgEl, 'Enter MA username and password', 'error');
        return;
    }
    if (btn) btn.disabled = true;
    _setStatusText(msgEl, 'Connecting...', 'muted');
    try {
        var resp = await fetch(API_BASE + '/api/ma/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, username: user, password: pass }),
        });
        if (resp.status === 401 && !url) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (data.success) {
            // Clear password field
            document.getElementById('ma-login-pass').value = '';
            // Update status
            _setMaStatus(true, data.username, data.url);
            // Update the hidden MA_API_URL field too
            var urlField = document.querySelector('input[name="MA_API_URL"]');
            if (urlField) urlField.value = data.url;
            _setStatusText(msgEl, '\u2714 ' + data.message, 'success');
            showToast('\u2714 Connected to Music Assistant', 'success');
            // Reload config so hidden MA_API_TOKEN field is up to date
            // (backend already saved the new token to config.json)
            await loadConfig({preserveDirtyBaseline: true});
        } else if (resp.status === 401) {
            // Builtin login failed — try HA OAuth with same credentials
            _setStatusText(msgEl, 'Trying Home Assistant login...', 'muted');
            var ok = await _maHaLoginWithCreds(url, user, pass, msgEl);
            if (!ok && msgEl) {
                _setStatusText(msgEl, '\u2716 ' + (data.error || 'Login failed'), 'error');
            }
        } else {
            _setStatusText(msgEl, '\u2716 ' + (data.error || 'Login failed'), 'error');
        }
    } catch (err) {
        _setStatusText(msgEl, '\u2716 Error: ' + err.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function _setMaStatus(connected, username, url) {
    var bar = document.getElementById('ma-status-bar');
    var icon = document.getElementById('ma-status-icon');
    var text = document.getElementById('ma-status-text');
    _maConnected = !!connected;
    if (!_maConnected) _maReconfigureRequested = false;
    if (connected) {
        _maAutoSilentAuthFailed = false;
        _setMaIntegrationBanner('');
        if (bar) {
            bar.classList.add('panel-status--success');
            bar.classList.remove('panel-status--neutral');
        }
        _setUiIconSlot(icon, 'status-success');
        if (text) text.innerHTML = 'Connected' + (username ? ' as <b>' + escHtml(username) + '</b>' : '') + (url ? ' \u2014 ' + escHtml(url) : '');
    } else {
        if (bar) {
            bar.classList.add('panel-status--neutral');
            bar.classList.remove('panel-status--success');
        }
        _setUiIconSlot(icon, 'status-neutral');
        if (text) text.textContent = 'Not connected';
    }
    var reconf = document.getElementById('ma-reconfigure');
    if (reconf) reconf.hidden = !connected;
    _syncMaAuthCardVisibility();
}

function _syncMaAuthCardVisibility() {
    var formCard = document.getElementById('ma-auth-card');
    var form = document.getElementById('ma-conn-form');
    var apiFields = document.getElementById('ma-api-fields');
    var showAuthCard = !_maConnected || _maReconfigureRequested;
    if (formCard) formCard.hidden = !showAuthCard;
    if (form) form.hidden = !showAuthCard;
    if (apiFields) apiFields.hidden = !showAuthCard;
}

function toggleMaForm(show) {
    _maReconfigureRequested = !!show;
    _syncMaAuthCardVisibility();
    if (show) _detectMaAddonMode();
}

async function _detectMaAddonMode() {
    var urlInput = document.getElementById('ma-login-url');
    var maUrl = urlInput ? (urlInput.value || '').trim() : '';
    if (!maUrl) return;
    try {
        var resp = await fetch(maUrl + '/info', { signal: AbortSignal.timeout(5000) });
        if (!resp.ok) return;
        var info = await resp.json();
        _setMaAddonMode(!!info.homeassistant_addon);
    } catch (_) { /* ignore — not critical */ }
}

async function _maHaLoginWithCreds(maUrl, username, password, msgEl) {
    try {
        var resp = await fetch(API_BASE + '/api/ma/ha-login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step: 'init', ma_url: maUrl, username: username, password: password }),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (!data.success) {
            _setStatusText(msgEl, '\u2716 ' + (data.error || 'HA login failed'), 'error');
            return false;
        }
        if (data.step === 'done') {
            document.getElementById('ma-login-pass').value = '';
            _setMaStatus(true, data.username, data.url);
            _setStatusText(msgEl, '\u2714 ' + (data.message || 'Connected via HA'), 'success');
            showToast('\u2714 Connected to Music Assistant via HA', 'success');
            await loadConfig({preserveDirtyBaseline: true});
            return true;
        }
        if (data.step === 'mfa') {
            var code = prompt('Enter ' + (data.mfa_module_name || 'TOTP') + ' code:');
            if (!code) { _setStatusText(msgEl, 'MFA cancelled', 'error'); return false; }
            var resp2 = await fetch(API_BASE + '/api/ma/ha-login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    step: 'mfa', flow_id: data.flow_id, ha_url: data.ha_url,
                    client_id: data.client_id, state: data.state,
                    code: code, ma_url: maUrl, username: username,
                }),
            });
            var data2 = await resp2.json().catch(function() { return {}; });
            if (data2.success && data2.step === 'done') {
                document.getElementById('ma-login-pass').value = '';
                _setMaStatus(true, data2.username, data2.url);
                _setStatusText(msgEl, '\u2714 ' + (data2.message || 'Connected via HA'), 'success');
                showToast('\u2714 Connected to Music Assistant via HA', 'success');
                await loadConfig({preserveDirtyBaseline: true});
                return true;
            }
            _setStatusText(msgEl, '\u2716 ' + (data2.error || 'MFA failed'), 'error');
            return false;
        }
        return false;
    } catch (err) {
        _setStatusText(msgEl, '\u2716 HA login error: ' + err.message, 'error');
        return false;
    }
}

function _isIngress() {
    return window.location.pathname.indexOf('/api/hassio_ingress/') !== -1;
}

async function _getHaAccessToken() {
    // Read HA tokens from localStorage (available in Ingress — same origin)
    var raw = localStorage.getItem('hassTokens');
    if (!raw) return null;
    try {
        var tokens = JSON.parse(raw);
        if (!tokens || !tokens.access_token) return null;
        // If expired, try to refresh
        if (tokens.expires && Date.now() / 1000 > tokens.expires - 30) {
            var clientId = window.location.protocol + '//' + window.location.host + '/';
            var resp = await fetch('/auth/token', {
                method: 'POST',
                body: new URLSearchParams({
                    grant_type: 'refresh_token',
                    refresh_token: tokens.refresh_token,
                    client_id: clientId,
                }),
            });
            if (!resp.ok) return null;
            var fresh = await resp.json();
            tokens.access_token = fresh.access_token;
            tokens.expires_in = fresh.expires_in;
            tokens.expires = Date.now() / 1000 + fresh.expires_in;
            localStorage.setItem('hassTokens', JSON.stringify(tokens));
        }
        return tokens.access_token;
    } catch (e) {
        return null;
    }
}

async function _maSilentAuth(maUrl) {
    var haToken = await _getHaAccessToken();
    if (!haToken) return false;
    var msgEl = document.getElementById('ma-ha-login-msg');
    _setStatusText(msgEl, 'Authenticating via Ingress…', 'muted');
    try {
        var resp = await fetch(API_BASE + '/api/ma/ha-silent-auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ha_token: haToken, ma_url: maUrl }),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (data.success) {
            _maAutoSilentAuthFailed = false;
            _setMaStatus(true, data.username || '', data.url || maUrl);
            showToast('\u2714 Connected to Music Assistant', 'success');
            await loadConfig({preserveDirtyBaseline: true});
            _setStatusText(msgEl, '\u2714 Connected', 'success');
            return true;
        }
        _setStatusText(msgEl, data.error || 'Silent auth failed', 'error');
    } catch (e) {
        console.warn('Silent MA auth failed:', e);
        _setStatusText(msgEl, 'Connection error', 'error');
    }
    return false;
}

async function _maAutoConnect() {
    var discovery = await maDiscover();
    var integration = discovery && discovery.integration ? discovery.integration : {};
    var foundServer = discovery && discovery.success && Array.isArray(discovery.servers) && discovery.servers.length > 0
        ? discovery.servers[0]
        : null;
    var integrationConnected = !!integration.connected;
    var tokenConfigured = !!integration.token_configured;
    var tokenValid = !!integration.token_valid;
    var autoSilentAuthEnabled = ((document.getElementById('ma-auto-silent-auth') || {}).checked) !== false;
    var addonMode = !!(discovery && (discovery.is_addon || (foundServer && foundServer.homeassistant_addon)));

    if (!foundServer) {
        _setMaIntegrationBanner('');
        return;
    }

    if (tokenConfigured && !tokenValid && !integrationConnected) {
        _setMaStatus(false);
    }

    if (addonMode && _isIngress() && autoSilentAuthEnabled && !tokenValid && !_maAutoSilentAuthAttempted) {
        _maAutoSilentAuthAttempted = true;
        var autoOk = await _maSilentAuth(foundServer.url || (document.getElementById('ma-login-url').value || '').trim());
        _maAutoSilentAuthFailed = !autoOk;
        if (autoOk) {
            _setMaIntegrationBanner('');
            return;
        }
    }

    if (tokenValid || integrationConnected) {
        _setMaIntegrationBanner('');
        return;
    }

    if (tokenConfigured) {
        _setMaIntegrationBanner(
            'Music Assistant was found, but the saved bridge token is invalid. Open Configuration → Music Assistant and get a new long-lived token.',
            'Saved Music Assistant token is no longer valid'
        );
        return;
    }

    if (addonMode && _isIngress() && autoSilentAuthEnabled && _maAutoSilentAuthAttempted && _maAutoSilentAuthFailed) {
        _setMaIntegrationBanner(
            'Music Assistant was found, but automatic Home Assistant sign-in did not complete. Open Configuration → Music Assistant to retry or get a long-lived token.',
            'Automatic Music Assistant sign-in did not complete'
        );
        return;
    }

    _setMaIntegrationBanner(
        'Music Assistant was found, but this bridge is not connected yet. Open Configuration → Music Assistant and get a long-lived token.',
        'Music Assistant was found, but the bridge is not connected'
    );
}

// ---- Apply log level immediately ----

async function applyLogLevel() {
    var sel = document.getElementById('log-level-select');
    var msg = document.getElementById('log-level-msg');
    if (!sel) return;
    var level = sel.value;
    try {
        var resp = await fetch(API_BASE + '/api/settings/log_level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level: level }),
        });
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (resp.ok) {
            _setStatusText(msg, '\u2713 Applied', 'success');
            setTimeout(function() { _setStatusText(msg, '', ''); }, 3000);
        } else {
            showToast('Error: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

document.getElementById('config-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    var saveBtns = document.querySelectorAll('.config-action-btn, .config-section button[type="submit"]');
    saveBtns.forEach(function(b) { b.disabled = true; });
    try {
        var result = await saveConfig();
        if (result && result.ok) {
            _markConfigSnapshotClean();
            if (result.maReloaded) {
                showToast('\u2713 Configuration saved \u2014 Music Assistant reloaded without full restart', 'success');
            } else if (result.maReloadError) {
                showToast('\u2713 Configuration saved \u2014 MA reload failed, restart to apply: ' + result.maReloadError, 'warning');
            } else {
                showToast('\u2713 Configuration saved \u2014 restart to apply', 'success');
            }
        } else {
            showToast('\u2717 ' + (result && result.error || 'Failed to save configuration'), 'error');
        }
    } catch (err) {
        showToast('\u2717 Error: ' + err.message, 'error');
    } finally {
        saveBtns.forEach(function(b) { b.disabled = false; });
        _syncConfigFooterActions();
    }
});

// ---- Config dirty-state tracking ----
var _configDirty = false;
var _configLoading = false;
var _configCleanSnapshot = null;
var _configDirtyKeySeq = 0;
var _configTabOrder = ['general', 'devices', 'bluetooth', 'ma', 'security'];

function _nextConfigDirtyKey(prefix) {
    _configDirtyKeySeq += 1;
    return (prefix || 'config') + '-' + _configDirtyKeySeq;
}

function _ensureConfigDirtyKey(node, prefix) {
    if (!node) return '';
    if (!node.dataset.configDirtyKey) {
        node.dataset.configDirtyKey = _nextConfigDirtyKey(prefix);
    }
    return node.dataset.configDirtyKey;
}

function _syncConfigFooterActions() {
    var cancelBtn = document.getElementById('config-cancel-btn');
    if (cancelBtn) cancelBtn.disabled = !_configDirty || _configLoading;
}

function _configDirtyFieldLabel(count) {
    return count === 1 ? '1 unsaved change' : count + ' unsaved changes';
}

function _configValuesEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}

function _getTrackableStaticConfigControls() {
    var controls = [];
    document.querySelectorAll('#config-form [name]').forEach(function(control) {
        if (control.closest('[data-config-transient="true"]')) return;
        if (control.closest('#bt-devices-table')) return;
        if (control.closest('#adapters-table')) return;
        controls.push(control);
    });
    return controls;
}

function _getConfigControlTab(control) {
    var panel = control && control.closest('[data-config-panel]');
    return (panel && panel.dataset.configPanel) || 'general';
}

function _getConfigFieldSurface(control) {
    if (!control) return null;
    return control.closest('.config-setting-row, .form-group, .config-inline-field, .config-inline-row, .config-subsection, .config-switch, label') || control;
}

function _normalizeBtDeviceDirtyFields(fields) {
    var keepalive = fields.keepalive_interval;
    if (keepalive == null || keepalive === '') keepalive = 0;
    keepalive = parseInt(keepalive, 10);
    if (!Number.isFinite(keepalive) || keepalive < 0) keepalive = 0;
    if (keepalive > 0 && keepalive < 30) keepalive = 30;
    var listenPort = fields.listen_port;
    if (listenPort == null || listenPort === '') {
        listenPort = null;
    } else {
        var listenPortValue = parseInt(listenPort, 10);
        listenPort = Number.isFinite(listenPortValue) ? listenPortValue : String(listenPort).trim();
    }
    var delay = parseFloat(fields.static_delay_ms);
    if (Number.isNaN(delay)) delay = 0;
    var handoffMode = String(fields.handoff_mode || 'default').trim().toLowerCase();
    if (handoffMode !== 'fast_handoff') handoffMode = 'default';
    var idleDisconnect = fields.idle_disconnect_minutes;
    if (idleDisconnect == null || idleDisconnect === '') idleDisconnect = 0;
    idleDisconnect = parseInt(idleDisconnect, 10);
    if (!Number.isFinite(idleDisconnect) || idleDisconnect < 0) idleDisconnect = 0;
    return {
        enabled: fields.enabled !== false,
        player_name: (fields.player_name || '').trim(),
        mac: ((fields.mac || '').trim()).toUpperCase(),
        adapter: (fields.adapter || '').trim(),
        static_delay_ms: delay,
        listen_host: (fields.listen_host || '').trim(),
        listen_port: listenPort,
        preferred_format: (fields.preferred_format || 'flac:44100:16:2').trim() || 'flac:44100:16:2',
        keepalive_interval: keepalive,
        idle_disconnect_minutes: idleDisconnect,
        room_name: (fields.room_name || '').trim(),
        room_id: (fields.room_id || '').trim(),
        handoff_mode: handoffMode,
    };
}

function _defaultBtDeviceDirtyFields() {
    return _normalizeBtDeviceDirtyFields({
        enabled: true,
        player_name: '',
        mac: '',
        adapter: '',
        static_delay_ms: -300,
        listen_host: '',
        listen_port: null,
        preferred_format: 'flac:44100:16:2',
        keepalive_interval: 0,
        room_name: '',
        room_id: '',
        handoff_mode: 'default',
    });
}

function _readBtDeviceDirtyFields(wrap) {
    var row = wrap.querySelector('.bt-device-row');
    var detail = wrap.querySelector('.bt-detail-row');
    return _normalizeBtDeviceDirtyFields({
        enabled: !!((row.querySelector('.bt-enabled') || {}).checked),
        player_name: ((row.querySelector('.bt-name') || {}).value || ''),
        mac: ((row.querySelector('.bt-mac') || {}).value || ''),
        adapter: ((row.querySelector('.bt-adapter') || {}).value || ''),
        static_delay_ms: ((row.querySelector('.bt-delay') || {}).value || 0),
        listen_host: detail ? (((detail.querySelector('.bt-listen-host') || {}).value) || '') : '',
        listen_port: ((row.querySelector('.bt-listen-port') || {}).value || ''),
        preferred_format: detail ? (((detail.querySelector('.bt-preferred-format') || {}).value) || 'flac:44100:16:2') : 'flac:44100:16:2',
        keepalive_interval: detail ? (((detail.querySelector('.bt-keepalive-interval') || {}).value) || 0) : 0,
        idle_disconnect_minutes: detail ? (((detail.querySelector('.bt-idle-disconnect') || {}).value) || 0) : 0,
        room_name: detail ? (((detail.querySelector('.bt-room-name') || {}).value) || '') : '',
        room_id: detail ? (((detail.querySelector('.bt-room-id') || {}).value) || '') : '',
        handoff_mode: detail ? (((detail.querySelector('.bt-handoff-mode') || {}).value) || 'default') : 'default',
    });
}

function _btDeviceShouldPersist(fields) {
    return !!fields.mac;
}

function _getBtDeviceControlByField(wrap, fieldName) {
    var selectors = {
        enabled: '.bt-enabled',
        player_name: '.bt-name',
        mac: '.bt-mac',
        adapter: '.bt-adapter',
        static_delay_ms: '.bt-delay',
        listen_host: '.bt-listen-host',
        listen_port: '.bt-listen-port',
        preferred_format: '.bt-preferred-format',
        keepalive_interval: '.bt-keepalive-interval',
        idle_disconnect_minutes: '.bt-idle-disconnect',
        room_name: '.bt-room-name',
        room_id: '.bt-room-id',
        handoff_mode: '.bt-handoff-mode',
    };
    return wrap.querySelector(selectors[fieldName] || '');
}

function _normalizeAdapterDirtyFields(fields) {
    return {
        _type: fields._type === 'detected' ? 'detected' : 'manual',
        id: (fields.id || '').trim(),
        mac: ((fields.mac || '').trim()).toUpperCase(),
        name: (fields.name || '').trim(),
        area_id: _normalizeHaAreaId(fields.area_id),
    };
}

function _defaultAdapterDirtyFields(fields) {
    var normalized = _normalizeAdapterDirtyFields(fields || {});
    if (normalized._type === 'detected') {
        return {
            _type: 'detected',
            id: normalized.id,
            mac: normalized.mac,
            name: '',
            area_id: '',
        };
    }
    return {
        _type: 'manual',
        id: '',
        mac: '',
        name: '',
        area_id: '',
    };
}

function _readAdapterDirtyFields(row) {
    return _normalizeAdapterDirtyFields({
        _type: row.classList.contains('detected') ? 'detected' : 'manual',
        id: row.classList.contains('manual')
            ? ((((row.querySelector('.adp-id') || {}).value) || ''))
            : (row.dataset.adapterId || ''),
        mac: row.classList.contains('manual')
            ? ((((row.querySelector('.adp-mac') || {}).value) || ''))
            : (row.dataset.adapterMac || ''),
        name: (((row.querySelector('.adp-name') || {}).value) || ''),
        area_id: (((row.querySelector('.adp-ha-area') || {}).value) || ''),
    });
}

function _adapterShouldPersist(fields) {
    if (fields._type === 'detected') return !!(fields.id && (fields.name || fields.area_id));
    return !!(fields.id || fields.mac);
}

function _getAdapterControlByField(row, fieldName) {
    var selectors = {
        id: '.adp-id',
        mac: '.adp-mac',
        name: '.adp-name',
        area_id: '.adp-ha-area',
    };
    return row.querySelector(selectors[fieldName] || '');
}

function _captureConfigDirtySnapshot() {
    var payload = _buildConfigPayload({includeExternal: false, includeRuntime: false});
    var snapshot = {
        staticValues: {},
        btDevicesByKey: {},
        btDeviceKeys: [],
        btAdaptersByKey: {},
        btAdapterKeys: [],
    };

    _getTrackableStaticConfigControls().forEach(function(control) {
        snapshot.staticValues[control.name] = payload[control.name];
    });

    document.querySelectorAll('#bt-devices-table .bt-device-wrap').forEach(function(wrap) {
        var key = _ensureConfigDirtyKey(wrap, 'bt-device');
        var fields = _readBtDeviceDirtyFields(wrap);
        if (!_btDeviceShouldPersist(fields)) return;
        snapshot.btDevicesByKey[key] = fields;
        snapshot.btDeviceKeys.push(key);
    });

    document.querySelectorAll('#adapters-table .adapter-row').forEach(function(row) {
        var key = _ensureConfigDirtyKey(row, 'adapter');
        var fields = _readAdapterDirtyFields(row);
        if (!_adapterShouldPersist(fields)) return;
        snapshot.btAdaptersByKey[key] = fields;
        snapshot.btAdapterKeys.push(key);
    });

    return snapshot;
}

function _clearConfigDirtyClasses() {
    document.querySelectorAll('.config-input-dirty').forEach(function(node) { node.classList.remove('config-input-dirty'); });
    document.querySelectorAll('.config-field-dirty').forEach(function(node) { node.classList.remove('config-field-dirty'); });
    document.querySelectorAll('.config-dirty-row').forEach(function(node) { node.classList.remove('config-dirty-row'); });
    document.querySelectorAll('.config-panel-has-dirty').forEach(function(node) { node.classList.remove('config-panel-has-dirty'); });
}

function _applyConfigDirtyState(state) {
    _configDirty = state.totalCount > 0;
    _clearConfigDirtyClasses();

    state.controls.forEach(function(control) { control.classList.add('config-input-dirty'); });
    state.surfaces.forEach(function(surface) { surface.classList.add('config-field-dirty'); });
    state.rows.forEach(function(row) { row.classList.add('config-dirty-row'); });
    state.panels.forEach(function(panel) { panel.classList.add('config-panel-has-dirty'); });

    var summaryCount = document.getElementById('config-summary-dirty-count');
    if (summaryCount) {
        summaryCount.hidden = !_configDirty;
        summaryCount.textContent = String(state.totalCount);
        summaryCount.title = _configDirty ? _configDirtyFieldLabel(state.totalCount) : '';
    }

    _configTabOrder.forEach(function(tabName) {
        var tabButton = document.querySelector('.config-tab[data-config-tab="' + tabName + '"]');
        if (!tabButton) return;
        var count = state.tabCounts[tabName] || 0;
        tabButton.classList.toggle('has-dirty', count > 0);
        var badge = tabButton.querySelector('.config-tab-dirty-count');
        if (!badge) return;
        badge.hidden = count <= 0;
        badge.textContent = String(count);
        badge.title = count > 0 ? _configDirtyFieldLabel(count) : '';
    });

    var footer = document.getElementById('config-footer');
    if (footer) footer.classList.toggle('is-dirty', _configDirty);

    var banner = document.getElementById('config-dirty-banner');
    if (banner) {
        banner.hidden = !_configDirty;
        _syncNoticeStack();
    }

    var bannerTitle = document.getElementById('config-dirty-banner-title');
    if (bannerTitle) bannerTitle.textContent = _configDirty ? 'Configuration has ' + _configDirtyFieldLabel(state.totalCount) : 'Configuration has unsaved changes';

    var dirtyLabelText = document.getElementById('config-dirty-label-text');
    if (dirtyLabelText) dirtyLabelText.textContent = _configDirty ? _configDirtyFieldLabel(state.totalCount) : 'Unsaved changes';

    _syncConfigFooterActions();
}

function _recomputeConfigDirtyState() {
    if (_configLoading) return;
    if (!_configCleanSnapshot) {
        _configCleanSnapshot = _captureConfigDirtySnapshot();
    }

    var clean = _configCleanSnapshot;
    var payload = _buildConfigPayload({includeExternal: false, includeRuntime: false});
    var state = {
        totalCount: 0,
        tabCounts: {general: 0, devices: 0, bluetooth: 0, ma: 0, security: 0},
        controls: new Set(),
        surfaces: new Set(),
        rows: new Set(),
        panels: new Set(),
    };

    function markDirty(tabName, control, surface, row, panel) {
        var tab = _configTabOrder.indexOf(tabName) >= 0 ? tabName : 'general';
        state.totalCount += 1;
        state.tabCounts[tab] = (state.tabCounts[tab] || 0) + 1;
        if (control) state.controls.add(control);
        if (surface) state.surfaces.add(surface);
        if (row) state.rows.add(row);
        if (panel) state.panels.add(panel);
    }

    _getTrackableStaticConfigControls().forEach(function(control) {
        var currentValue = payload[control.name];
        var cleanValue = Object.prototype.hasOwnProperty.call(clean.staticValues, control.name)
            ? clean.staticValues[control.name]
            : undefined;
        if (_configValuesEqual(currentValue, cleanValue)) return;
        markDirty(
            _getConfigControlTab(control),
            control,
            _getConfigFieldSurface(control),
            null,
            control.closest('[data-config-panel]')
        );
    });

    var seenBtDeviceKeys = new Set();
    document.querySelectorAll('#bt-devices-table .bt-device-wrap').forEach(function(wrap) {
        var key = _ensureConfigDirtyKey(wrap, 'bt-device');
        var currentFields = _readBtDeviceDirtyFields(wrap);
        var cleanFields = clean.btDevicesByKey[key] || null;
        if (!_btDeviceShouldPersist(currentFields) && !cleanFields) return;
        seenBtDeviceKeys.add(key);
        var baseline = cleanFields || _defaultBtDeviceDirtyFields();
        var panel = document.querySelector('[data-config-panel="devices"]');
        Object.keys(baseline).forEach(function(fieldName) {
            if (_configValuesEqual(currentFields[fieldName], baseline[fieldName])) return;
            var control = _getBtDeviceControlByField(wrap, fieldName);
            markDirty('devices', control, _getConfigFieldSurface(control), wrap, panel);
        });
    });

    clean.btDeviceKeys.forEach(function(key) {
        if (seenBtDeviceKeys.has(key)) return;
        var baseline = _defaultBtDeviceDirtyFields();
        var cleanFields = clean.btDevicesByKey[key];
        var panel = document.querySelector('[data-config-panel="devices"]');
        Object.keys(baseline).forEach(function(fieldName) {
            if (_configValuesEqual(cleanFields[fieldName], baseline[fieldName])) return;
            markDirty('devices', null, null, document.getElementById('bt-devices-table'), panel);
        });
    });

    var seenAdapterKeys = new Set();
    document.querySelectorAll('#adapters-table .adapter-row').forEach(function(row) {
        var key = _ensureConfigDirtyKey(row, 'adapter');
        var currentFields = _readAdapterDirtyFields(row);
        var cleanFields = clean.btAdaptersByKey[key] || null;
        if (!_adapterShouldPersist(currentFields) && !cleanFields) return;
        seenAdapterKeys.add(key);
        var baseline = cleanFields || _defaultAdapterDirtyFields(currentFields);
        var panel = document.querySelector('[data-config-panel="bluetooth"]');
        ['id', 'mac', 'name'].forEach(function(fieldName) {
            if (_configValuesEqual(currentFields[fieldName], baseline[fieldName])) return;
            var control = _getAdapterControlByField(row, fieldName);
            markDirty('bluetooth', control, _getConfigFieldSurface(control), row, panel);
        });
    });

    clean.btAdapterKeys.forEach(function(key) {
        if (seenAdapterKeys.has(key)) return;
        var cleanFields = clean.btAdaptersByKey[key];
        var baseline = _defaultAdapterDirtyFields(cleanFields);
        var panel = document.querySelector('[data-config-panel="bluetooth"]');
        ['id', 'mac', 'name'].forEach(function(fieldName) {
            if (_configValuesEqual(cleanFields[fieldName], baseline[fieldName])) return;
            markDirty('bluetooth', null, null, document.getElementById('adapters-table'), panel);
        });
    });

    _applyConfigDirtyState(state);
}

function _markConfigSnapshotClean() {
    _configCleanSnapshot = _captureConfigDirtySnapshot();
    _applyConfigDirtyState({
        totalCount: 0,
        tabCounts: {general: 0, devices: 0, bluetooth: 0, ma: 0, security: 0},
        controls: new Set(),
        surfaces: new Set(),
        rows: new Set(),
        panels: new Set(),
    });
}

function _setConfigDirty(dirty) {
    if (_configLoading) return;
    if (dirty) {
        _recomputeConfigDirtyState();
        return;
    }
    _markConfigSnapshotClean();
}

// Watch config form for any change
document.getElementById('config-form').addEventListener('input', function(event) {
    if (event && event.target && event.target.closest('[data-config-transient="true"]')) return;
    _recomputeConfigDirtyState();
});
document.getElementById('config-form').addEventListener('change', function(event) {
    if (event && event.target && event.target.closest('[data-config-transient="true"]')) return;
    _recomputeConfigDirtyState();
});
window.addEventListener('beforeunload', function(e) {
    if (_configDirty) {
        e.preventDefault();
        e.returnValue = '';
    }
});
// Toggle auth password fields visibility
(function() {
    var authCheck = document.getElementById('auth-enabled');
    if (authCheck) {
        authCheck.addEventListener('change', function() {
            var fields = document.getElementById('auth-password-fields');
            if (fields) fields.hidden = !this.checked;
            _updateAuthMethodsHint();
        });
    }
})();

function _syncSecurityPolicyState() {
    var bruteForceCheck = document.getElementById('brute-force-protection');
    var fieldsWrap = document.getElementById('security-policy-fields');
    var fieldIds = ['brute-force-max-attempts', 'brute-force-window-minutes', 'brute-force-lockout-minutes'];
    if (!bruteForceCheck || !fieldsWrap) return;
    var enabled = !!bruteForceCheck.checked;
    fieldsWrap.classList.toggle('is-disabled', !enabled);
    fieldIds.forEach(function(id) {
        var input = document.getElementById(id);
        if (input) input.disabled = !enabled;
    });
}

(function() {
    var bruteForceCheck = document.getElementById('brute-force-protection');
    if (!bruteForceCheck) return;
    bruteForceCheck.addEventListener('change', _syncSecurityPolicyState);
    _syncSecurityPolicyState();
})();

(function() {
    var updateChannelSelect = document.getElementById('update-channel');
    if (!updateChannelSelect) return;
    updateChannelSelect.addEventListener('change', _onUpdateChannelChange);
    _lastConfirmedUpdateChannel = (updateChannelSelect.value || 'stable').toLowerCase();
    _syncUpdateChannelState();
})();

(function() {
    var onboardingToggle = document.getElementById('guidance-show-onboarding');
    if (onboardingToggle) {
        onboardingToggle.addEventListener('change', function() {
            var keys = _guidancePreferenceKeys(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
            _onboardingAssistantExpanded = !!this.checked;
            _setGuidanceVisible(keys.onboarding, !!this.checked);
            _applyOperatorGuidance(_lastOperatorGuidance);
        });
    }
    var recoveryToggle = document.getElementById('guidance-show-recovery');
    if (recoveryToggle) {
        recoveryToggle.addEventListener('change', function() {
            var keys = _guidancePreferenceKeys(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
            _setGuidanceVisible(keys.recovery, !!this.checked);
            _applyOperatorGuidance(_lastOperatorGuidance);
        });
    }
    var resetBtn = document.getElementById('guidance-reset-dismissed');
    if (resetBtn) {
        resetBtn.addEventListener('click', function(e) {
            e.preventDefault();
            _resetGuidancePreferences();
        });
    }
    _syncGuidancePreferenceControls(null);

    var experimentalToggle = document.getElementById('guidance-show-experimental');
    if (experimentalToggle) {
        experimentalToggle.addEventListener('change', function() {
            _setExperimentalEnabled(!!this.checked);
            _applyExperimentalVisibility();
        });
    }
    _applyExperimentalVisibility();
})();

function _updateAuthMethodsHint() {
    var hint = document.getElementById('auth-methods-hint');
    var text = document.getElementById('auth-methods-text');
    var authCheck = document.getElementById('auth-enabled');
    if (!hint || !text || !authCheck || !authCheck.checked) {
        if (hint) hint.hidden = true;
        return;
    }
    var methods = [];
    var maUrl = (document.querySelector('input[name="MA_API_URL"]') || {}).value;
    var maToken = (document.querySelector('input[name="MA_API_TOKEN"]') || {}).value;
    if (maUrl && maToken) methods.push('Music Assistant credentials');
    methods.push('local password');
    text.textContent = 'Sign-in methods: ' + methods.join(', ');
    hint.hidden = false;
}

function _restoreConfigTransientInputs(config) {
    var maLoginUrl = document.getElementById('ma-login-url');
    if (maLoginUrl) maLoginUrl.value = config.MA_API_URL || '';
    var maLoginUser = document.getElementById('ma-login-user');
    if (maLoginUser) maLoginUser.value = config.MA_USERNAME || '';
    var maLoginPass = document.getElementById('ma-login-pass');
    if (maLoginPass) maLoginPass.value = '';
    var newPassword = document.getElementById('new-password');
    if (newPassword) newPassword.value = '';
    var confirmPassword = document.getElementById('new-password-confirm');
    if (confirmPassword) confirmPassword.value = '';
}

function _clearConfigTransientStatus() {
    ['ma-login-msg', 'ma-ha-login-msg'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) _setStatusText(el, '', '');
    });
}

async function cancelConfigChanges() {
    if (!_configDirty) return;
    var actionBtns = document.querySelectorAll('.config-action-btn, .config-section button[type="submit"]');
    actionBtns.forEach(function(btn) { btn.disabled = true; });
    try {
        var config = await loadConfig();
        if (config === false) {
            showToast('Failed to restore saved configuration', 'error');
            return;
        }
        _clearConfigTransientStatus();
        showToast('Unsaved changes discarded', 'info');
    } catch (err) {
        console.error('Cancel config changes error:', err);
        showToast('Failed to restore saved configuration: ' + err.message, 'error');
    } finally {
        actionBtns.forEach(function(btn) { btn.disabled = false; });
        _syncConfigFooterActions();
    }
}

async function loadConfig(options) {
    options = options || {};
    _configLoading = true;
    _syncConfigFooterActions();
    try {
        var resp = await fetch(API_BASE + '/api/config');
        if (resp.status === 401) {
            _configLoading = false;
            _syncConfigFooterActions();
            _handleUnauthorized();
            return false;
        }
        var config = await resp.json();

        // Populate simple fields
        ['SENDSPIN_SERVER', 'SENDSPIN_PORT', 'WEB_PORT', 'BASE_LISTEN_PORT', 'BRIDGE_NAME', 'TZ', 'PULSE_LATENCY_MSEC',
         'BT_CHECK_INTERVAL', 'BT_MAX_RECONNECT_FAILS', 'MA_API_URL', 'MA_API_TOKEN',
         'SESSION_TIMEOUT_HOURS', 'BRUTE_FORCE_MAX_ATTEMPTS', 'BRUTE_FORCE_WINDOW_MINUTES',
         'BRUTE_FORCE_LOCKOUT_MINUTES', 'STARTUP_BANNER_GRACE_SECONDS', 'RECOVERY_BANNER_GRACE_SECONDS'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key] == null ? '' : config[key];
        });
        // Populate checkboxes
        var sbcCheck = document.getElementById('prefer-sbc-codec');
        if (sbcCheck) sbcCheck.checked = !!config.PREFER_SBC_CODEC;
        var authCheck = document.getElementById('auth-enabled');
        if (authCheck) authCheck.checked = !!config.AUTH_ENABLED;
        var haAreaAssistCheck = document.getElementById('ha-area-name-assist-enabled');
        if (haAreaAssistCheck) {
            haAreaAssistCheck.checked = config.HA_AREA_NAME_ASSIST_ENABLED !== false;
            _setHaAreaAssistEnabled(haAreaAssistCheck.checked);
        }
        var authPw = document.getElementById('auth-password-fields');
        if (authPw && authCheck) authPw.hidden = !authCheck.checked;
        window._passwordSet = !!config._password_set;
        _updateAuthMethodsHint();
        var bruteForceCheck = document.getElementById('brute-force-protection');
        if (bruteForceCheck) bruteForceCheck.checked = config.BRUTE_FORCE_PROTECTION !== false;
        _syncSecurityPolicyState();
        var maAutoSilentAuthCheck = document.getElementById('ma-auto-silent-auth');
        if (maAutoSilentAuthCheck) maAutoSilentAuthCheck.checked = config.MA_AUTO_SILENT_AUTH !== false;
        var maMonitorCheck = document.getElementById('ma-websocket-monitor');
        if (maMonitorCheck) maMonitorCheck.checked = config.MA_WEBSOCKET_MONITOR !== false;
        var volMaCheck = document.getElementById('volume-via-ma');
        if (volMaCheck) volMaCheck.checked = config.VOLUME_VIA_MA !== false;
        var muteMaCheck = document.getElementById('mute-via-ma');
        if (muteMaCheck) muteMaCheck.checked = !!config.MUTE_VIA_MA;
        var smoothRestartCheck = document.getElementById('smooth-restart');
        if (smoothRestartCheck) smoothRestartCheck.checked = !!config.SMOOTH_RESTART;
        var updateChannelSelect = document.getElementById('update-channel');
        if (updateChannelSelect) {
            updateChannelSelect.value = (config.UPDATE_CHANNEL || 'stable').toLowerCase();
            _lastConfirmedUpdateChannel = updateChannelSelect.value;
        }
        var haWebPortIndicator = document.getElementById('ha-web-port-indicator');
        if (haWebPortIndicator && config._effective_web_port !== undefined && config._effective_web_port !== null) {
            haWebPortIndicator.value = config._effective_web_port;
        }
        var autoUpdateCheck = document.getElementById('auto-update');
        if (autoUpdateCheck) autoUpdateCheck.checked = !!config.AUTO_UPDATE;
        var checkUpdatesCheck = document.getElementById('check-updates');
        if (checkUpdatesCheck) checkUpdatesCheck.checked = config.CHECK_UPDATES !== false;
        _syncUpdateChannelState();
        var logLevelSel = document.getElementById('log-level-select');
        if (logLevelSel && config.LOG_LEVEL) logLevelSel.value = config.LOG_LEVEL.toUpperCase();
        _restoreConfigTransientInputs(config);
        _syncGuidancePreferenceControls(_lastOperatorGuidance && _lastOperatorGuidance.visibility_keys);
        updateTzPreview();

        // Restore manual adapters before re-running loadBtAdapters so merging picks them up
        btManualAdapters = config.BLUETOOTH_ADAPTERS || [];
        _haAdapterAreaMap = _normalizeHaAdapterAreaMap(config.HA_ADAPTER_AREA_MAP);
        await loadBtAdapters({skipHaAreaRefresh: true});
        await _maybeLoadHaAreaCatalog();
        _refreshEmptyState();
        loadPairedDevices();

        // Populate BT device table
        var devices = config.BLUETOOTH_DEVICES;
        if (devices && Array.isArray(devices) && devices.length > 0) {
            populateBtDeviceRows(devices);
        }

        // Update MA connection status and detect addon mode
        if (config.MA_API_TOKEN) {
            _setMaStatus(true, config.MA_USERNAME || '', config.MA_API_URL || '');
        } else {
            _setMaStatus(false);
        }
        // Always discover to detect addon mode and set correct UI
        await _maAutoConnect();

        _configLoading = false;
        if (options.preserveDirtyBaseline) {
            _recomputeConfigDirtyState();
        } else {
            _markConfigSnapshotClean();
        }
        return config;
    } catch (err) {
        _configLoading = false;
        console.error('Error loading config:', err);
        _syncConfigFooterActions();
        return false;
    }
}

// ---- Restart ----

function _restartMonitorElapsedSeconds() {
    if (!_restartMonitor || !_restartMonitor.startedAt) return 0;
    return Math.max(0, Math.round((Date.now() - _restartMonitor.startedAt) / 1000));
}

function _restartProgressHtml(step, totalSteps, message, elapsed, options) {
    var opts = options || {};
    var safeTotal = Math.max(1, Number(totalSteps) || 0);
    var pct = Math.max(0, Math.min(100, Math.round((step / safeTotal) * 100)));
    var done = !!opts.done || step >= safeTotal;
    var failed = !!opts.failed || message.indexOf('\u26a0') >= 0 || message.indexOf('\u2717') >= 0;
    var icon;
    if (done && !failed) {
        icon = '<svg class="restart-check" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3.5 8.5 6.5 11.5 12.5 5.5"/></svg>';
    } else if (failed) {
        icon = '<svg class="restart-warn" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><line x1="8" y1="4" x2="8" y2="9"/><line x1="8" y1="11.5" x2="8" y2="11.5" stroke-width="2"/></svg>';
    } else {
        icon = '<div class="restart-spinner"></div>';
    }
    var elapsedHtml = elapsed ? '<span class="restart-elapsed">' + elapsed + 's</span>' : '';
    return '<div class="restart-status">' + icon +
        '<span>' + message + '</span>' + elapsedHtml +
        '</div>' +
        '<div class="restart-progress-bar"><div class="restart-progress-fill" style="width:' + pct + '%"></div></div>';
}

function _setRestartBannerState(state) {
    var banner = document.getElementById('restart-banner');
    if (!banner) return;
    if (!state) {
        banner.className = 'restart-banner';
        banner.innerHTML = '';
        return;
    }
    banner.className = 'restart-banner active';
    banner.innerHTML = _restartProgressHtml(
        Math.max(0, Math.min(100, Number(state.percent) || 0)),
        100,
        state.message || 'Restart in progress…',
        state.elapsedSeconds || 0,
        {done: !!state.done, failed: !!state.failed}
    );
}

function _clearRestartMonitorAfter(delayMs) {
    var monitor = _restartMonitor;
    setTimeout(function() {
        if (_restartMonitor !== monitor) return;
        _restartMonitor = null;
        _setRestartBannerState(null);
    }, delayMs);
}

function _isRestartRuntimeState(state) {
    return !!(state && (state.kind === 'starting' || state.kind === 'restoring' || state.kind === 'unavailable'));
}

function _syncRestartBanner(status, overrideServiceState) {
    var serviceState = overrideServiceState || _backendServiceState;
    var startup = status && status.startup_progress ? status.startup_progress : null;
    var startupStatus = startup && startup.status ? String(startup.status) : '';
    var restartDevices = status && Array.isArray(status.devices) ? status.devices : lastDevices;
    if (!_restartMonitor && _isRestartRuntimeState(serviceState)) {
        _restartMonitor = {startedAt: Date.now(), manual: false, sawRuntimeRestart: true};
    }
    if (!_restartMonitor) return;

    var elapsedSeconds = _restartMonitorElapsedSeconds();
    var startupPercent = startup && typeof startup.percent === 'number' ? startup.percent : 0;
    var sawLiveRestartState =
        startupStatus === 'stopping' ||
        startupStatus === 'running' ||
        startupStatus === 'starting' ||
        startupStatus === 'error' ||
        _isRestartRuntimeState(serviceState);
    if (sawLiveRestartState) {
        _restartMonitor.sawRuntimeRestart = true;
    }

    if (_restartMonitor.manual && !_restartMonitor.sawRuntimeRestart) {
        _setRestartBannerState({
            percent: 10,
            message: 'Restart requested… Waiting for the service to restart.',
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (startupStatus === 'stopping') {
        _setRestartBannerState({
            percent: Math.max(5, startupPercent || 5),
            message: 'Restart in progress…',
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (startupStatus === 'stopped') {
        _setRestartBannerState({
            percent: Math.max(10, startupPercent || 10),
            message: 'Restart in progress…',
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (startupStatus === 'running' || startupStatus === 'starting') {
        _setRestartBannerState({
            percent: Math.max(12, startupPercent || 12),
            message: startup.message || 'Starting service…',
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (startupStatus === 'error') {
        _setRestartBannerState({
            percent: Math.max(20, startupPercent || 20),
            message: startup.message || 'Startup failed',
            elapsedSeconds: elapsedSeconds,
            failed: true,
        });
        return;
    }

    if (serviceState && serviceState.kind === 'unavailable') {
        _setRestartBannerState({
            percent: Math.max(15, startupPercent || 15),
            message: serviceState.summary || 'Waiting for the backend to come back…',
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (serviceState && (serviceState.kind === 'starting' || serviceState.kind === 'restoring')) {
        var finalizingStartup = serviceState.label === 'Startup 90%' || serviceState.title === 'Startup 90%';
        _setRestartBannerState({
            percent: Math.max(finalizingStartup ? 90 : (serviceState.kind === 'restoring' ? 85 : 20), startupPercent || 0),
            message: finalizingStartup
                ? _buildFinalizingStartupSummary(status, restartDevices, serviceState.summary || 'Finalizing startup')
                : (serviceState.summary || serviceState.title || 'Restoring bridge state…'),
            elapsedSeconds: elapsedSeconds,
        });
        return;
    }

    if (_restartMonitor.sawRuntimeRestart && status) {
        if (_restartMonitor.demoConfigRefreshPending) {
            _restartMonitor.demoConfigRefreshPending = false;
            setTimeout(function() {
                if (_runtimeMode === 'demo' && !_configLoading) loadConfig();
            }, 0);
        }
        _setRestartBannerState({
            percent: 100,
            message: 'Restart complete — bridge is ready',
            elapsedSeconds: elapsedSeconds,
            done: true,
        });
        _clearRestartMonitorAfter(4000);
        return;
    }

    _restartMonitor = null;
    _setRestartBannerState(null);
}

async function uploadConfig(input) {
    var file = input.files && input.files[0];
    input.value = '';  // reset so same file can be re-selected
    if (!file) return;
    if (!confirm('Upload ' + file.name + ' and replace the current configuration?\nSensitive keys (passwords, tokens) will be preserved from the current config.\nA restart will be required to apply changes.')) return;
    var form = new FormData();
    form.append('file', file);
    try {
        var resp = await fetch(API_BASE + '/api/config/upload', {method: 'POST', body: form});
        var data = await resp.json();
        if (!resp.ok) {
            alert('Upload failed: ' + (data.error || resp.statusText));
            return;
        }
        alert('Config uploaded successfully. Restart to apply.');
        location.reload();
    } catch (e) {
        alert('Upload error: ' + e.message);
    }
}

async function saveAndRestart() {
    var smooth = !!(document.getElementById('smooth-restart') || {}).checked;
    _restartMonitor = null;
    _setRestartBannerState({percent: 2, message: 'Saving configuration…', elapsedSeconds: 0});

    try {
        var saved = await saveConfig();
        if (!saved || !saved.ok) {
            _setRestartBannerState({
                percent: 0,
                message: '\u2717 ' + ((saved && saved.error) || 'Failed to save configuration'),
                elapsedSeconds: 0,
                failed: true,
            });
            _clearRestartMonitorAfter(3000);
            return;
        }
        _markConfigSnapshotClean();

        if (smooth) {
            _setRestartBannerState({percent: 8, message: 'Muting speakers…', elapsedSeconds: 0});
            var _allDeviceNames = (lastDevices || []).map(function(d) { return d.player_name; });
            if (_allDeviceNames.length > 0) {
                try {
                    await fetch(API_BASE + '/api/mute', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({mute: true, force_local: true, player_names: _allDeviceNames})
                    });
                    await new Promise(function(r) { setTimeout(r, 300); });
                } catch (_) { /* Non-critical — continue with restart */ }
            }
        }

        _restartMonitor = {
            startedAt: Date.now(),
            manual: true,
            sawRuntimeRestart: false,
            demoConfigRefreshPending: _runtimeMode === 'demo',
        };
        _setRestartBannerState({
            percent: smooth ? 12 : 8,
            message: 'Restart requested… Waiting for the service to restart.',
            elapsedSeconds: 0,
        });
        try {
            await fetch(API_BASE + '/api/restart', { method: 'POST' });
        } catch (_) { /* Service dropped connection — expected */ }
        setTimeout(updateStatus, 250);

    } catch (err) {
        _setRestartBannerState({
            percent: 0,
            message: '\u26a0\ufe0f Error: ' + err.message,
            elapsedSeconds: 0,
            failed: true,
        });
    }
}

// ---- Timezone preview ----

function updateTzPreview() {
    var tzInput = document.querySelector('[name="TZ"]');
    var preview = document.getElementById('tz-preview');
    if (!tzInput || !preview) return;
    var tz = tzInput.value.trim();
    if (!tz) { preview.textContent = ''; return; }
    try {
        var now = new Date();
        var formatted = now.toLocaleTimeString('en-AU', {
            timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        });
        preview.textContent = 'Current time: ' + formatted;
        preview.style.color = '#6b7280';
    } catch (_) {
        preview.textContent = 'Invalid timezone';
        preview.style.color = '#ef4444';
    }
}

// Update TZ preview every second while panel is visible
var _tzPreviewInterval = setInterval(updateTzPreview, 1000);

// Populate TZ datalist from browser's IANA timezone database
(function() {
    var dl = document.getElementById('tz-list');
    if (!dl) return;
    var zones = [];
    try {
        zones = Intl.supportedValuesOf('timeZone');
    } catch (_) {
        // Fallback for older browsers — common zones
        zones = [
            'UTC','Africa/Cairo','Africa/Johannesburg','Africa/Lagos','Africa/Nairobi',
            'America/Anchorage','America/Argentina/Buenos_Aires','America/Bogota',
            'America/Chicago','America/Denver','America/Halifax','America/Los_Angeles',
            'America/Mexico_City','America/New_York','America/Phoenix','America/Santiago',
            'America/Sao_Paulo','America/Toronto','America/Vancouver',
            'Asia/Bangkok','Asia/Colombo','Asia/Dubai','Asia/Hong_Kong','Asia/Jakarta',
            'Asia/Jerusalem','Asia/Karachi','Asia/Kolkata','Asia/Kuala_Lumpur',
            'Asia/Seoul','Asia/Shanghai','Asia/Singapore','Asia/Taipei','Asia/Tehran',
            'Asia/Tokyo','Asia/Vladivostok','Asia/Yekaterinburg',
            'Atlantic/Azores','Atlantic/Reykjavik',
            'Australia/Adelaide','Australia/Brisbane','Australia/Darwin',
            'Australia/Melbourne','Australia/Perth','Australia/Sydney',
            'Europe/Amsterdam','Europe/Athens','Europe/Berlin','Europe/Brussels',
            'Europe/Budapest','Europe/Copenhagen','Europe/Dublin','Europe/Helsinki',
            'Europe/Istanbul','Europe/Kyiv','Europe/Lisbon','Europe/London',
            'Europe/Madrid','Europe/Moscow','Europe/Oslo','Europe/Paris',
            'Europe/Prague','Europe/Rome','Europe/Stockholm','Europe/Vienna',
            'Europe/Warsaw','Europe/Zurich',
            'Pacific/Auckland','Pacific/Fiji','Pacific/Honolulu','Pacific/Noumea',
        ];
    }
    var frag = document.createDocumentFragment();
    zones.forEach(function(tz) {
        var opt = document.createElement('option');
        opt.value = tz;
        frag.appendChild(opt);
    });
    dl.appendChild(frag);
}());

// ---- Version ----

function _syncVersionDisplayFromStatus(status) {
    if (!status || !status.version) return;
    var el = document.getElementById('version-display');
    if (!el) return;
    var ver = String(status.version || '').replace(/^v/i, '');
    var title = status.build_date || '';
    el.textContent = 'v' + ver;
    if (title) el.title = title;
    _applyReleaseChannelTextTone(el, _releaseChannelFromVersion(ver));
}

async function loadVersionInfo() {
    try {
        var resp = await fetch(API_BASE + '/api/version');
        var data = await resp.json();
        var el = document.getElementById('version-display');
        if (!el) return;
        var ver = String(data.version || el.textContent || '').replace(/^v/i, '');
        var title = data.built_at || '';
        if (data.git_sha && data.git_sha !== 'unknown') title += ' · ' + data.git_sha;
        el.textContent = 'v' + ver;
        if (title) el.title = title;
        _applyReleaseChannelTextTone(el, _releaseChannelFromVersion(ver));
    } catch (_) { /* Keep static Jinja2-rendered values */ }
}

function _releaseChannelFromVersion(version) {
    var normalized = String(version || '').toLowerCase();
    if (normalized.indexOf('-demo') !== -1) return 'demo';
    if (normalized.indexOf('-beta') !== -1) return 'beta';
    if (normalized.indexOf('-rc') !== -1) return 'rc';
    return 'stable';
}

function _applyReleaseChannelTextTone(el, channel) {
    if (!el) return;
    el.classList.remove('channel-rc', 'channel-beta', 'channel-demo');
    if (channel === 'rc' || channel === 'beta' || channel === 'demo') {
        el.classList.add('channel-' + channel);
    }
}

function _showUpdateBadge(upd) {
    var badge = document.getElementById('update-badge');
    var link = document.getElementById('update-link');
    var ver = document.getElementById('update-version');
    var icon = document.getElementById('update-icon');
    if (!badge || !link) return;
    link.classList.remove('checking');
    if (_updateMonitor) {
        link.classList.add('checking');
        if (ver) ver.textContent = 'updating…';
        _setUiIconSlot(icon, 'refresh');
        link.href = '#';
        link.title = 'Update in progress';
        return;
    }
    if (upd && upd.version) {
        var channel = upd.channel || _releaseChannelFromVersion(upd.version);
        if (ver) ver.textContent = 'v' + upd.version + (channel !== 'stable' ? ' · ' + channel.toUpperCase() : '');
        _setUiIconSlot(icon, 'upload');
        link.href = upd.url || '#';
        link.target = '_blank';
        link.rel = 'noopener';
        link.title = 'Update available on ' + channel.toUpperCase() + ' channel — click to apply';
        link.classList.remove('no-update');
        link.classList.add('has-update');
        if (ver) ver.classList.remove('channel-rc', 'channel-beta', 'channel-demo');
        link.dataset.updateVersion = upd.version;
        link.dataset.updateUrl = upd.url || '';
        link.dataset.updateChannel = channel;
    } else {
        if (ver) ver.textContent = 'up to date';
        _setUiIconSlot(icon, 'refresh');
        link.removeAttribute('target');
        link.removeAttribute('rel');
        link.href = '#';
        link.title = 'Check for updates';
        link.classList.remove('has-update');
        link.classList.add('no-update');
        if (ver) ver.classList.remove('channel-rc', 'channel-beta', 'channel-demo');
        delete link.dataset.updateVersion;
        delete link.dataset.updateUrl;
        delete link.dataset.updateChannel;
    }
}

// ---------------------------------------------------------------------------
// Bug Report Modal
// ---------------------------------------------------------------------------

// SVG icons for bug report modal (inline to avoid extra requests)
var _BR_ICON_BUG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><line x1="8" y1="4" x2="8" y2="9"/><line x1="8" y1="11.5" x2="8" y2="11.5" stroke-width="2"/></svg>';
var _BR_ICON_GITHUB = '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';
var _BR_ICON_COPY = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M3 11V3a1.5 1.5 0 011.5-1.5H11"/></svg>';
var _BR_ICON_INFO = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><line x1="8" y1="7" x2="8" y2="11"/><line x1="8" y1="5" x2="8" y2="5" stroke-width="2"/></svg>';

function _openBugReport(e) {
    e.preventDefault();

    // Overlay
    var overlay = document.createElement('div');
    overlay.className = 'bugreport-overlay';

    // Modal
    var modal = document.createElement('div');
    modal.className = 'bugreport-modal';

    // Header with accent color
    var header = document.createElement('div');
    header.className = 'bugreport-header';
    header.innerHTML =
        '<span class="bugreport-header-icon">' + _BR_ICON_BUG + '</span>' +
        '<span class="bugreport-header-title">Report an Issue</span>';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'bugreport-close';
    closeBtn.innerHTML = '×';
    closeBtn.title = 'Close';
    closeBtn.onclick = function() { overlay.remove(); };
    header.appendChild(closeBtn);
    modal.appendChild(header);

    // Body
    var body = document.createElement('div');
    body.className = 'bugreport-body';

    // Hint (visible immediately)
    var hint = document.createElement('div');
    hint.className = 'bugreport-hint';
    hint.innerHTML =
        '<span class="bugreport-hint-icon">' + _BR_ICON_INFO + '</span>' +
        '<span>A diagnostics file will download and a GitHub issue will open — drag the file into the issue to attach it.</span>';
    body.appendChild(hint);

    // Title field
    var titleField = document.createElement('div');
    titleField.className = 'bugreport-field';
    titleField.innerHTML = '<label>Title</label>';
    var titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.placeholder = 'Brief description of the issue';
    titleField.appendChild(titleInput);
    var titleError = document.createElement('div');
    titleError.className = 'field-error';
    titleError.textContent = 'Please enter a title for the issue';
    titleField.appendChild(titleError);
    body.appendChild(titleField);

    // Description field
    var descField = document.createElement('div');
    descField.className = 'bugreport-field';
    descField.innerHTML = '<label>Description</label>';
    var descInput = document.createElement('textarea');
    descInput.placeholder = 'What happened? What did you expect instead?';
    descField.appendChild(descInput);
    var descError = document.createElement('div');
    descError.className = 'field-error';
    descError.textContent = 'Please describe the issue';
    descField.appendChild(descError);
    body.appendChild(descField);

    // Preview toggle
    var previewToggle = document.createElement('div');
    previewToggle.className = 'bugreport-preview-toggle';
    previewToggle.innerHTML = '<span class="bugreport-preview-arrow" aria-hidden="true"></span><span>Diagnostic data (auto-attached)</span>';
    var previewBox = document.createElement('div');
    previewBox.className = 'bugreport-preview';
    previewBox.style.display = 'none';
    previewBox.textContent = 'Loading diagnostics…';
    previewToggle.onclick = function() {
        var showing = previewBox.style.display !== 'none';
        previewBox.style.display = showing ? 'none' : 'block';
        previewToggle.querySelector('.bugreport-preview-arrow').classList.toggle('expanded', !showing);
    };
    body.appendChild(previewToggle);
    body.appendChild(previewBox);

    modal.appendChild(body);

    // Footer with actions
    var footer = document.createElement('div');
    footer.className = 'bugreport-footer';

    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'bugreport-btn secondary';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.onclick = function() { overlay.remove(); };
    footer.appendChild(cancelBtn);

    var copyBtn = document.createElement('button');
    copyBtn.className = 'bugreport-btn secondary';
    copyBtn.innerHTML = '<span class="bugreport-btn-icon">' + _BR_ICON_COPY + '</span> Copy';
    copyBtn.style.display = 'none';
    footer.appendChild(copyBtn);

    var submitBtn = document.createElement('button');
    submitBtn.className = 'bugreport-btn primary btn-disabled';
    submitBtn.innerHTML = '<span class="bugreport-spinner"></span> Loading…';
    submitBtn._formValid = false;
    footer.appendChild(submitBtn);

    modal.appendChild(footer);

    // Validation
    var dataReady = false;
    function validateForm() {
        var hasTitle = titleInput.value.trim().length > 0;
        var hasDesc = descInput.value.trim().length > 0;
        var ready = dataReady && hasTitle && hasDesc;
        submitBtn.classList.toggle('btn-disabled', !ready);
        submitBtn._formValid = ready;
        if (hasTitle) titleInput.classList.remove('invalid');
        if (hasDesc) descInput.classList.remove('invalid');
    }
    titleInput.addEventListener('input', validateForm);
    descInput.addEventListener('input', validateForm);

    // Assemble and show
    overlay.appendChild(modal);
    overlay.onclick = function(ev) { if (ev.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
    setTimeout(function() { titleInput.focus(); }, 100);

    // Escape key to close
    function onEsc(ev) { if (ev.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', onEsc); } }
    document.addEventListener('keydown', onEsc);

    // Fetch diagnostics
    var reportShort = '';
    var reportFull = '';
    var reportData = null;
    fetch(API_BASE + '/api/bugreport')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            reportShort = data.markdown_short || '';
            reportFull = data.text_full || '';
            reportData = data.report || {};
            var suggestedDescription = (data.suggested_description || '').trim();
            if (suggestedDescription && !descInput.value.trim()) {
                descInput.value = suggestedDescription;
            }
            previewBox.textContent = reportFull || 'No data available';
            submitBtn.innerHTML = '<span class="bugreport-btn-icon">' + _BR_ICON_GITHUB + '</span> Submit to GitHub';
            dataReady = true;
            validateForm();
            copyBtn.style.display = '';

            copyBtn.onclick = function() {
                var fullBody = _buildBugReportBody(titleInput.value, descInput.value, reportFull);
                _copyToClipboard(fullBody).then(function() {
                    showToast('Report copied to clipboard', 'info');
                }).catch(function() {
                    showToast('Could not copy to clipboard', 'error');
                });
            };

            submitBtn.onclick = function() {
                if (!submitBtn._formValid) {
                    var _hasTitle = titleInput.value.trim().length > 0;
                    var _hasDesc = descInput.value.trim().length > 0;
                    if (!_hasTitle) { titleInput.classList.add('invalid'); titleInput.focus(); }
                    if (!_hasDesc) { descInput.classList.add('invalid'); }
                    return;
                }
                var title = titleInput.value.trim() || 'Bug report';
                var desc = descInput.value.trim();
                var fullBody = _buildBugReportBody(title, desc, reportFull);

                _downloadBugReport(fullBody, title);

                var rep = reportData || {};
                var env = rep.environment || {};
                var diag = rep.diagnostics || {};
                var runtime = rep.runtime || '';

                var runtimeMap = {
                    ha_addon: 'Home Assistant Addon',
                    docker: 'Docker Compose',
                    systemd: 'Proxmox LXC'
                };
                var deployment = runtimeMap[runtime] || '';

                var info = [];
                if (env.platform) info.push('OS:       ' + env.platform);
                if (env.kernel) info.push('Kernel:   ' + env.kernel);
                if (env.audio_server) info.push('Audio:    ' + env.audio_server);
                var adapters = diag.adapters || [];
                if (adapters.length) {
                    info.push('BT:       ' + adapters.map(function(a) {
                        return (a.id || '') + ' ' + (a.mac || '');
                    }).join(', ').trim());
                }
                if (env.python) info.push('Python:   ' + env.python.split(' ')[0]);
                if (env.bluez) info.push('BlueZ:    ' + env.bluez);

                var uptime = (rep.uptime || '?').replace(/\.\d+$/, '');
                info.push('Uptime:   ' + uptime);
                info.push('RAM:      ' + (env.process_rss_mb || '?') + ' MB');

                var devices = diag.devices || [];
                var devParts = [String(devices.length || 1)];
                devices.forEach(function(d) {
                    devParts.push('  ' + (d.name || d.mac) + ': ' +
                        (d.connected ? 'connected' : 'disconnected') +
                        ', sink=' + (d.sink || 'none'));
                });
                info.push('Devices:  ' + devParts.join('\n'));

                var ma = diag.ma_integration || {};
                if (ma.configured) {
                    info.push('MA:       ' + (ma.connected ? 'connected' : 'disconnected') +
                        (ma.version ? ' v' + ma.version : '') +
                        ', ' + ((ma.syncgroups || []).length) + ' group(s)');
                }
                var systemInfo = info.join('\n');

                var issueLines = rep.recent_issue_logs || [];
                var recentErrors = issueLines.slice(-3).map(function(l) {
                    var m = l.match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ - .+/);
                    return m ? m[0] : l;
                }).join('\n');

                var versionStr = rep.version || '';
                if (rep.build_date) versionStr += ' (' + rep.build_date + ')';

                var params = [
                    'template=bug_report_auto.yml',
                    'title=' + encodeURIComponent(title),
                    'description=' + encodeURIComponent(desc),
                    'version=' + encodeURIComponent(versionStr),
                    'diagnostics=' + encodeURIComponent('📎 Drag and drop the downloaded diagnostics file here'),
                    'additional=' + encodeURIComponent('Submitted via web UI Report button')
                ];
                if (deployment) params.push('deployment=' + encodeURIComponent(deployment));
                if (systemInfo) params.push('system_info=' + encodeURIComponent(systemInfo));
                if (recentErrors) params.push('recent_errors=' + encodeURIComponent(recentErrors));

                var issueUrl = 'https://github.com/trudenboy/sendspin-bt-bridge/issues/new?' + params.join('&');

                window.open(issueUrl, '_blank');
                showToast('Report downloaded — attach the file to the GitHub issue', 'info');
                overlay.remove();
                document.removeEventListener('keydown', onEsc);
            };
        })
        .catch(function() {
            previewBox.textContent = 'Failed to load diagnostics';
            submitBtn.innerHTML = '<span class="bugreport-btn-icon">' + _BR_ICON_GITHUB + '</span> Submit to GitHub';
            dataReady = true;
            validateForm();
            submitBtn.onclick = function() {
                if (!submitBtn._formValid) {
                    var _hasTitle = titleInput.value.trim().length > 0;
                    var _hasDesc = descInput.value.trim().length > 0;
                    if (!_hasTitle) { titleInput.classList.add('invalid'); titleInput.focus(); }
                    if (!_hasDesc) { descInput.classList.add('invalid'); }
                    return;
                }
                var title = titleInput.value.trim() || 'Bug report';
                var desc = descInput.value.trim();
                var issueUrl = 'https://github.com/trudenboy/sendspin-bt-bridge/issues/new'
                    + '?template=bug_report_auto.yml'
                    + '&title=' + encodeURIComponent(title)
                    + '&description=' + encodeURIComponent(desc + '\n\n_Diagnostics could not be loaded._');
                window.open(issueUrl, '_blank');
                overlay.remove();
                document.removeEventListener('keydown', onEsc);
            };
        });

    return false;
}

function _copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    }
    // Fallback for non-HTTPS contexts
    return new Promise(function(resolve, reject) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy') ? resolve() : reject();
        } catch (e) { reject(e); }
        finally { document.body.removeChild(ta); }
    });
}

function _downloadBugReport(content, title) {
    var blob = new Blob([content], { type: 'text/plain' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    var date = new Date().toISOString().slice(0, 10);
    var slug = (title || 'bugreport').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
    a.href = url;
    a.download = 'bugreport-' + date + '-' + slug + '.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function _buildBugReportBody(title, description, diagnostics) {
    var parts = [];
    parts.push('## Description\n');
    parts.push(description || '_No description provided_');
    parts.push('');
    if (diagnostics) {
        parts.push('---\n');
        parts.push(diagnostics);
    }
    return parts.join('\n');
}

function _onUpdateBadgeClick(e) {
    var link = document.getElementById('update-link');
    if (!link) return true;

    // Update available — show apply dialog
    if (link.classList.contains('has-update')) {
        e.preventDefault();
        var ver = link.dataset.updateVersion || '?';
        var url = link.dataset.updateUrl || '';
        var channel = link.dataset.updateChannel || 'stable';
        _showUpdateDialog(ver, url, channel);
        return false;
    }

    // No update — check for updates
    e.preventDefault();
    if (link) link.classList.add('checking');
    var verEl = document.getElementById('update-version');
    if (verEl) verEl.textContent = 'checking…';
    _runUpdateCheck()
        .then(function(data) {
            if (data.update_available) {
                _showUpdateBadge({version: data.version, url: data.url, channel: data.channel});
                showToast('Update v' + data.version + ' available on ' + (data.channel || 'stable').toUpperCase(), 'info');
            } else {
                _showUpdateBadge(null);
                showToast('Already on the latest ' + (data.channel || 'stable').toUpperCase() + ' version', 'info');
            }
        })
        .catch(function() {
            _showUpdateBadge(null);
            showToast('Update check failed', 'error');
        });
    return false;
}

async function _runUpdateCheck(channel) {
    var req = { method: 'POST' };
    if (channel) {
        req.headers = { 'Content-Type': 'application/json' };
        req.body = JSON.stringify({ channel: channel });
    }
    var resp = await fetch(API_BASE + '/api/update/check', req);
    if (resp.status === 401) { _handleUnauthorized(); return {}; }
    var data = await resp.json().catch(function() { return {}; });
    if (!resp.ok) throw new Error(data.error || 'Update check failed');
    if (!data.job_id) return data;
    var result = await _pollBackgroundJob(API_BASE + '/api/update/check/result/' + data.job_id, {
        delayMs: 1000,
        maxAttempts: 25,
        timeoutMessage: 'Update check timed out'
    });
    if (!result) return {};
    if (result.success === false) throw new Error(result.error || 'Update check failed');
    return result;
}

// SVG icons for update modal
var _UPD_ICON_ARROW = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="13" x2="8" y2="3"/><polyline points="3,7 8,2 13,7"/></svg>';
var _UPD_ICON_REFRESH = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8a5.5 5.5 0 019.77-3.5M13.5 8a5.5 5.5 0 01-9.77 3.5"/><polyline points="12,1 13,4.5 9.5,4.5"/><polyline points="4,11.5 3,15 6.5,15" transform="translate(0,-3)"/></svg>';
var _UPD_ICON_NOTES = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="1" width="12" height="14" rx="1.5"/><line x1="5" y1="5" x2="11" y2="5"/><line x1="5" y1="8" x2="11" y2="8"/><line x1="5" y1="11" x2="9" y2="11"/></svg>';
var _UPD_ICON_HA = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 7.5 8 3l5.5 4.5"/><path d="M4.5 7v6.5h7V7"/><path d="M6.8 13.5V9.8h2.4v3.7"/></svg>';

function _showUpdateDialog(ver, releaseUrl, releaseChannel) {
    // Fetch update info to determine runtime/method
    fetch(API_BASE + '/api/update/info')
        .then(function(r) { return r.json(); })
        .then(function(info) {
            var method = info.update_method || 'manual';
            var channel = info.channel || releaseChannel || 'stable';
            var curVerEl = document.getElementById('version-display');
            var curVer = curVerEl ? curVerEl.textContent : '';

            var overlay = document.createElement('div');
            overlay.className = 'update-modal-overlay';
            var modal = document.createElement('div');
            modal.className = 'update-modal';

            // Header
            var header = document.createElement('div');
            header.className = 'update-modal-header';
            header.innerHTML = _UPD_ICON_ARROW +
                '<span class="update-modal-header-title">Update Available</span>' +
                '<button class="update-modal-header-close" title="Close">\u00d7</button>';
            header.querySelector('.update-modal-header-close').onclick = function() { overlay.remove(); };
            modal.appendChild(header);

            // Version comparison
            var verRow = document.createElement('div');
            verRow.className = 'update-modal-version';
            verRow.innerHTML = '<span>' + curVer + '</span>' +
                '<span class="update-modal-version-arrow">\u2192</span>' +
                '<span class="update-modal-version-new">v' + ver + (channel !== 'stable' ? ' · ' + channel.toUpperCase() : '') + '</span>';
            modal.appendChild(verRow);

            if (info.channel_warning) {
                var warningEl = document.createElement('div');
                warningEl.className = 'config-channel-warning';
                warningEl.textContent = info.channel_warning;
                modal.appendChild(warningEl);
            }

            // Body
            if ((method === 'manual' && (info.instructions || info.command)) || info.body) {
                var bodyEl = document.createElement('div');
                bodyEl.className = 'update-modal-body';
                if (method === 'manual' && (info.instructions || info.command)) {
                    var instructionsEl = document.createElement('div');
                    instructionsEl.className = 'update-modal-instructions';
                    var instructionText = document.createElement('div');
                    instructionText.className = 'update-modal-instructions-copy';
                    instructionText.textContent = info.instructions || 'Pull the new image and redeploy your stack.';
                    instructionsEl.appendChild(instructionText);
                    if (info.command) {
                        var commandRow = document.createElement('div');
                        commandRow.className = 'update-modal-command-row';
                        var commandEl = document.createElement('code');
                        commandEl.className = 'update-modal-command';
                        commandEl.textContent = info.command;
                        commandRow.appendChild(commandEl);
                        var copyBtn = document.createElement('button');
                        copyBtn.type = 'button';
                        copyBtn.className = 'update-modal-copy-btn';
                        copyBtn.textContent = 'Copy command';
                        copyBtn.onclick = function() {
                            var btn = this;
                            _copyToClipboard(info.command).then(function() {
                                btn.textContent = 'Copied';
                                showToast('Update command copied', 'info');
                                setTimeout(function() { btn.textContent = 'Copy command'; }, 1600);
                            }).catch(function() {
                                showToast('Could not copy update command', 'error');
                            });
                        };
                        commandRow.appendChild(copyBtn);
                        instructionsEl.appendChild(commandRow);
                    }
                    bodyEl.appendChild(instructionsEl);
                }
                if (info.body) {
                    var notesEl = document.createElement('div');
                    notesEl.className = 'update-modal-release-notes';
                    var notesTitle = document.createElement('div');
                    notesTitle.className = 'update-modal-section-title';
                    notesTitle.textContent = 'Release notes';
                    notesEl.appendChild(notesTitle);
                    var notesCopy = document.createElement('div');
                    notesCopy.className = 'update-modal-release-copy';
                    var plain = info.body
                        .replace(/^## .+\n+/, '')
                        .replace(/^### .+$/gm, '')
                        .replace(/\*\*(.+?)\*\*/g, '$1')
                        .replace(/^- /gm, '\u2022 ')
                        .replace(/\n{3,}/g, '\n\n')
                        .trim();
                    notesCopy.textContent = plain;
                    notesEl.appendChild(notesCopy);
                    bodyEl.appendChild(notesEl);
                }
                modal.appendChild(bodyEl);
            }

            // Footer
            var footer = document.createElement('div');
            footer.className = 'update-modal-footer';

            // Re-check
            var recheckBtn = document.createElement('button');
            recheckBtn.className = 'update-modal-btn secondary';
            recheckBtn.innerHTML = _UPD_ICON_REFRESH + ' Re-check';
            recheckBtn.onclick = function() {
                overlay.remove();
                var link = document.getElementById('update-link');
                if (link) link.classList.add('checking');
                var verEl = document.getElementById('update-version');
                if (verEl) verEl.textContent = 'checking\u2026';
                _runUpdateCheck(channel)
                    .then(function(data) {
                        if (data.update_available) {
                            _showUpdateBadge({version: data.version, url: data.url, channel: data.channel});
                            showToast('Update v' + data.version + ' available on ' + (data.channel || 'stable').toUpperCase(), 'info');
                        } else {
                            _showUpdateBadge(null);
                            showToast('Already on the latest ' + (data.channel || 'stable').toUpperCase() + ' version', 'info');
                        }
                    })
                    .catch(function() {
                        _showUpdateBadge(null);
                        showToast('Update check failed', 'error');
                    });
            };
            footer.appendChild(recheckBtn);

            // Release Notes
            var notesBtn = document.createElement('a');
            notesBtn.className = 'update-modal-btn secondary';
            notesBtn.href = releaseUrl;
            notesBtn.target = '_blank';
            notesBtn.rel = 'noopener';
            notesBtn.innerHTML = _UPD_ICON_NOTES + ' Release Notes';
            notesBtn.onclick = function() { overlay.remove(); };
            footer.appendChild(notesBtn);

            // Primary action
            if (method === 'one_click') {
                var applyBtn = document.createElement('button');
                applyBtn.className = 'update-modal-btn primary';
                applyBtn.innerHTML = _UPD_ICON_ARROW + ' Update Now';
                applyBtn.onclick = function() {
                    overlay.remove();
                    _applyUpdate(ver, releaseUrl, channel);
                };
                footer.appendChild(applyBtn);
            } else if (method === 'ha_store') {
                var haBtn = document.createElement('a');
                haBtn.className = 'update-modal-btn primary';
                haBtn.href = '/hassio/addon/85b1ecde_sendspin_bt_bridge/info';
                haBtn.target = '_blank';
                haBtn.innerHTML = _UPD_ICON_HA + ' Update in HA';
                haBtn.onclick = function() { overlay.remove(); };
                footer.appendChild(haBtn);
            } else {
                var closeBtn = document.createElement('button');
                closeBtn.className = 'update-modal-btn primary';
                closeBtn.innerHTML = _UPD_ICON_NOTES + ' Got it';
                closeBtn.onclick = function() { overlay.remove(); };
                footer.appendChild(closeBtn);
            }

            modal.appendChild(footer);
            overlay.appendChild(modal);
            overlay.onclick = function(ev) { if (ev.target === overlay) overlay.remove(); };
            document.addEventListener('keydown', function _escUpdate(ev) {
                if (ev.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', _escUpdate); }
            });
            document.body.appendChild(overlay);
        })
        .catch(function() {
            window.open(releaseUrl, '_blank');
        });
}

function _applyUpdate(ver, releaseUrl, channel) {
    var link = document.getElementById('update-link');
    var verEl = document.getElementById('update-version');
    var iconEl = document.getElementById('update-icon');
    if (link) link.classList.add('checking');
    if (verEl) verEl.textContent = 'updating…';
    _setUiIconSlot(iconEl, 'refresh');
    _startUpdateMonitor(ver, channel || (link && link.dataset.updateChannel) || 'stable');
    fetch(API_BASE + '/api/update/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({version: ver, channel: channel || (link && link.dataset.updateChannel) || 'stable'})
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                if (_updateMonitor) _updateMonitor.alreadyRunning = !!data.already_running;
                showToast(data.already_running ? 'Update already in progress…' : 'Update started! Waiting for restart…', 'info');
                setTimeout(updateStatus, 250);
            } else {
                _clearUpdateMonitor();
                showToast('Update failed: ' + (data.error || 'unknown error'), 'error');
                _showUpdateBadge({version: ver, url: releaseUrl, channel: channel || (link && link.dataset.updateChannel) || 'stable'});
                updateStatus();
            }
        })
        .catch(function() {
            showToast('Update started… Waiting for restart…', 'info');
            setTimeout(updateStatus, 250);
        });
}

// ---- Diagnostics ----

function onDiagToggle(el) {
    if (el.open) {
        var content = document.getElementById('diag-content');
        if (!content.dataset.loaded) {
            loadDiagnostics(content);
        }
    }
}

async function loadDiagnostics(contentEl) {
    contentEl.innerHTML = '<span style="color:#6b7280;font-size:13px;">Loading&#8230;</span>';
    try {
        var resp = await fetch(API_BASE + '/api/diagnostics');
        var data = await resp.json();
        _lastDiagnosticsPayload = data;
        contentEl.innerHTML = renderDiagnostics(data);
        contentEl.dataset.loaded = '1';
    } catch (err) {
        _lastDiagnosticsPayload = null;
        contentEl.innerHTML =
            '<span style="color:#ef4444;font-size:13px;">Error: ' + err.message + '</span>';
    }
}

function dot(state) {
    var tone = state;
    if (typeof tone === 'boolean') tone = tone ? 'ok' : 'err';
    if (tone !== 'ok' && tone !== 'warn' && tone !== 'err') tone = 'err';
    return '<span class="diag-dot ' + tone + '"></span>';
}

function _diagRecoveryToneClass(tone) {
    return tone === 'error' ? 'error' : (tone === 'warning' || tone === 'warn' ? 'warn' : 'ok');
}

function _diagRecoveryDotTone(tone) {
    return tone === 'error' ? 'err' : (tone === 'warning' || tone === 'warn' ? 'warn' : 'ok');
}

function _recoveryTimelineEntries(timeline) {
    return timeline && Array.isArray(timeline.entries) ? timeline.entries : [];
}

function _normalizeRecoveryTimelineLimit(limit) {
    return limit === '24' || limit === 'all' ? limit : '12';
}

function _recoveryTimelineSourceOptions(timeline, sourceType) {
    var counts = {};
    _recoveryTimelineEntries(timeline).forEach(function(entry) {
        if (sourceType && sourceType !== 'all' && String(entry.source_type || 'unknown') !== sourceType) return;
        var source = String((entry && entry.source) || '').trim();
        if (!source) return;
        counts[source] = (counts[source] || 0) + 1;
    });
    return Object.keys(counts).sort(function(left, right) {
        if (counts[right] !== counts[left]) return counts[right] - counts[left];
        return left.localeCompare(right);
    }).map(function(source) {
        return {source: source, count: counts[source]};
    });
}

function _normalizedRecoveryTimelineView(timeline) {
    var normalizedSourceType = _recoveryTimelineViewState.sourceType === 'bridge' || _recoveryTimelineViewState.sourceType === 'device'
        ? _recoveryTimelineViewState.sourceType
        : 'all';
    var sourceOptions = _recoveryTimelineSourceOptions(timeline, normalizedSourceType).map(function(item) { return item.source; });
    var view = {
        level: _recoveryTimelineViewState.level === 'error' || _recoveryTimelineViewState.level === 'warning' || _recoveryTimelineViewState.level === 'info'
            ? _recoveryTimelineViewState.level
            : 'all',
        sourceType: normalizedSourceType,
        source: _recoveryTimelineViewState.source || 'all',
        limit: _normalizeRecoveryTimelineLimit(_recoveryTimelineViewState.limit),
        advanced: !!_recoveryTimelineViewState.advanced,
    };
    if (view.source !== 'all' && sourceOptions.indexOf(view.source) < 0) {
        view.source = 'all';
    }
    _recoveryTimelineViewState = view;
    return view;
}

function _recoveryTimelineFilteredView(timeline) {
    var allEntries = _recoveryTimelineEntries(timeline);
    var view = _normalizedRecoveryTimelineView(timeline);
    var matchedEntries = allEntries.filter(function(entry) {
        if (view.level !== 'all' && String(entry.level || 'info') !== view.level) return false;
        if (view.sourceType !== 'all' && String(entry.source_type || 'unknown') !== view.sourceType) return false;
        if (view.source !== 'all' && String(entry.source || '') !== view.source) return false;
        return true;
    });
    var visibleEntries = view.limit === 'all'
        ? matchedEntries
        : matchedEntries.slice(-Number(view.limit));
    return {
        view: view,
        allEntries: allEntries,
        matchedEntries: matchedEntries,
        visibleEntries: visibleEntries,
        sourceOptions: _recoveryTimelineSourceOptions(timeline, view.sourceType),
    };
}

function _recoveryTimelineHasActiveFilters(view) {
    return !!(view && (view.level !== 'all' || view.sourceType !== 'all' || view.source !== 'all' || view.limit !== '12'));
}

function _setRecoveryTimelineViewPatch(patch) {
    _recoveryTimelineViewState = Object.assign({}, _recoveryTimelineViewState, patch || {});
    _refreshRecoveryTimelineView();
    return false;
}

function _setRecoveryTimelineLevel(level) {
    return _setRecoveryTimelineViewPatch({level: level || 'all'});
}

function _setRecoveryTimelineSourceType(sourceType) {
    return _setRecoveryTimelineViewPatch({sourceType: sourceType || 'all', source: 'all'});
}

function _setRecoveryTimelineSource(source) {
    return _setRecoveryTimelineViewPatch({source: source || 'all'});
}

function _setRecoveryTimelineLimit(limit) {
    return _setRecoveryTimelineViewPatch({limit: _normalizeRecoveryTimelineLimit(limit)});
}

function _setRecoveryTimelineAdvanced(open) {
    _recoveryTimelineViewState = Object.assign({}, _recoveryTimelineViewState, {advanced: !!open});
    return false;
}

function _refreshRecoveryTimelineView() {
    var timelineContainer = document.getElementById('diag-recovery-timeline');
    var diagnostics = _lastDiagnosticsPayload;
    if (!timelineContainer || !diagnostics) return;
    var recovery = diagnostics.recovery_assistant || {};
    timelineContainer.outerHTML = _renderRecoveryTimelineCard(recovery.timeline || {});
}

function _renderRecoveryTimelineControlOptions(options, selectedValue) {
    return options.map(function(option) {
        return '<option value="' + escHtmlAttr(option.value) + '"' +
            (option.value === selectedValue ? ' selected' : '') +
            '>' + escHtml(option.label) + '</option>';
    }).join('');
}

function _renderRecoveryTimelineControls(timelineView) {
    var view = timelineView.view;
    var sourceOptions = timelineView.sourceOptions || [];
    var controlsHtml = '<details class="diag-timeline-advanced"' + (view.advanced ? ' open' : '') +
        ' ontoggle="_setRecoveryTimelineAdvanced(this.open)">' +
        '<summary class="diag-timeline-advanced-summary">Advanced timeline view</summary>' +
        '<div class="diag-timeline-controls">' +
            '<label class="diag-timeline-control">' +
                '<span class="diag-timeline-control-label">Severity</span>' +
                '<select onchange="return _setRecoveryTimelineLevel(this.value)">' +
                    _renderRecoveryTimelineControlOptions([
                        {value: 'all', label: 'All levels'},
                        {value: 'error', label: 'Errors only'},
                        {value: 'warning', label: 'Warnings only'},
                        {value: 'info', label: 'Info only'},
                    ], view.level) +
                '</select>' +
            '</label>' +
            '<label class="diag-timeline-control">' +
                '<span class="diag-timeline-control-label">Scope</span>' +
                '<select onchange="return _setRecoveryTimelineSourceType(this.value)">' +
                    _renderRecoveryTimelineControlOptions([
                        {value: 'all', label: 'Bridge + speakers'},
                        {value: 'device', label: 'Speakers only'},
                        {value: 'bridge', label: 'Bridge only'},
                    ], view.sourceType) +
                '</select>' +
            '</label>' +
            '<label class="diag-timeline-control">' +
                '<span class="diag-timeline-control-label">Window</span>' +
                '<select onchange="return _setRecoveryTimelineLimit(this.value)">' +
                    _renderRecoveryTimelineControlOptions([
                        {value: '12', label: 'Latest 12 matches'},
                        {value: '24', label: 'Latest 24 matches'},
                        {value: 'all', label: 'All retained matches'},
                    ], view.limit) +
                '</select>' +
            '</label>';
    if (sourceOptions.length > 1) {
        controlsHtml += '<label class="diag-timeline-control">' +
            '<span class="diag-timeline-control-label">Source</span>' +
            '<select onchange="return _setRecoveryTimelineSource(this.value)">' +
                _renderRecoveryTimelineControlOptions(
                    [{value: 'all', label: 'All sources'}].concat(sourceOptions.map(function(option) {
                        return {
                            value: option.source,
                            label: option.source + ' (' + option.count + ')',
                        };
                    })),
                    view.source
                ) +
            '</select>' +
        '</label>';
    }
    controlsHtml += '</div></details>';
    return controlsHtml;
}

function _renderRecoveryTimelineOverview(timeline, timelineView) {
    var summary = timeline && timeline.summary ? timeline.summary : {};
    var totalEntries = Number(summary.total_entry_count || timelineView.allEntries.length || 0);
    var retainedEntries = Number(summary.visible_entry_count || summary.entry_count || timelineView.allEntries.length || 0);
    var truncatedEntries = Number(summary.truncated_entry_count || Math.max(0, totalEntries - retainedEntries));
    var matchedEntries = timelineView.matchedEntries.length;
    var shownEntries = timelineView.visibleEntries.length;
    var sourcePreview = (timelineView.sourceOptions || []).slice(0, 4).map(function(item) {
        return '<span class="diag-timeline-source-pill">' + escHtml(item.source) + ' · ' + escHtml(String(item.count)) + '</span>';
    }).join('');
    var summaryBits = [
        shownEntries + ' shown',
        matchedEntries + ' match filter',
    ];
    if (!_recoveryTimelineHasActiveFilters(timelineView.view)) {
        summaryBits = [shownEntries + ' shown from retained timeline'];
    }
    if (retainedEntries !== totalEntries) {
        summaryBits.push('latest ' + retainedEntries + ' retained');
    }
    if (truncatedEntries > 0) {
        summaryBits.push(truncatedEntries + ' older hidden');
    }
    return '<div class="diag-timeline-overview">' +
        '<div class="diag-timeline-overview-copy">' + escHtml(summaryBits.join(' · ')) + '</div>' +
        (sourcePreview ? '<div class="diag-timeline-source-list">' + sourcePreview + '</div>' : '') +
    '</div>';
}

function _diagCodePill(value) {
    var text = value == null || value === '' ? '—' : String(value);
    return '<code class="diag-code-pill">' + escHtml(text) + '</code>';
}

function _renderDiagMetaRow(label, value, options) {
    if (value == null || value === '') return '';
    var opts = options || {};
    var rowClasses = ['diag-meta-row'];
    if (opts.stack) rowClasses.push('diag-meta-row--stack');
    return '<div class="' + rowClasses.join(' ') + '">' +
        '<span class="diag-meta-label">' + escHtml(label) + '</span>' +
        '<span class="diag-meta-value">' + (opts.code ? _diagCodePill(String(value)) : escHtml(String(value))) + '</span>' +
    '</div>';
}

function _renderDiagInfoItem(label, value, options) {
    var opts = options || {};
    var itemClasses = ['diag-item'];
    if (opts.stack) itemClasses.push('diag-item--stacked');
    var text = value == null || value === '' ? '—' : String(value);
    var valueHtml = opts.code ? _diagCodePill(text) : escHtml(text);
    return '<div class="' + itemClasses.join(' ') + '">' +
        '<span class="diag-label">' + escHtml(label) + '</span>' +
        '<span class="diag-value">' + valueHtml + '</span>' +
    '</div>';
}

function _renderDiagSummaryCard(card) {
    var classes = ['diag-summary-card', card.tone || 'ok'];
    var attrs = '';
    if (card.target) {
        classes.push('diag-summary-card--link');
        attrs = ' type="button" onclick="return focusDiagnosticsSection(\'' + String(card.target) + '\')"';
    }
    return '<button class="' + classes.join(' ') + '"' + attrs + '>' +
        '<div class="diag-summary-label">' + escHtml(card.title) + '</div>' +
        '<div class="diag-summary-value">' + escHtml(card.value) + '</div>' +
        '<div class="diag-summary-hint">' + escHtml(card.hint) + '</div>' +
    '</button>';
}

function focusDiagnosticsSection(targetId) {
    var target = document.getElementById(targetId);
    if (!target) return false;
    var advancedSection = target.closest('.diag-advanced-section');
    if (advancedSection && !advancedSection.open) advancedSection.open = true;
    window.requestAnimationFrame(function() {
        window.requestAnimationFrame(function() {
            target.classList.remove('diag-target-highlight');
            void target.offsetWidth;
            target.classList.add('diag-target-highlight');
            target.scrollIntoView({behavior: 'smooth', block: 'start'});
            window.setTimeout(function() {
                target.classList.remove('diag-target-highlight');
            }, 1800);
        });
    });
    return false;
}

function _diagDeviceOutcome(dev) {
    if (dev.enabled === false) return 'Unavailable until you re-enable this speaker.';
    if (!dev.connected) return 'Unavailable until Bluetooth reconnects.';
    if (!dev.sink) return 'Connected, but audio cannot route until a sink attaches.';
    if (dev.last_error) return 'Connected, but playback may stay unstable until the issue clears.';
    if (dev.playing) return 'Healthy and playing now.';
    return 'Healthy and ready for playback.';
}

function _diagAdapterOutcome(adapter, daemonActive, daemonState) {
    if (adapter.error) return 'Bluetooth routing on this adapter needs attention before speakers can attach reliably.';
    if (!daemonActive) return 'Adapter is detected, but Bluetooth control is limited until the daemon is ' + daemonState + '.';
    return adapter.default
        ? 'Primary adapter is ready for pairing and audio routing.'
        : 'Adapter is available for pairing and audio routing.';
}

function _diagGroupOutcome(unavailableMembers, bridgeIssues) {
    if (unavailableMembers.length) return 'Group playback is degraded until unavailable members return.';
    if (bridgeIssues.length) return 'Group playback may drift or stall until bridge members recover.';
    return 'Group is ready for synced playback.';
}

function openDiagnosticsDeviceSettings() {
    var opened = _openConfigPanel('devices', 'config-panel-devices', 'start');
    _highlightConfigTarget((opened && opened.target) || document.getElementById('config-panel-devices'));
    return false;
}

function openBluetoothSettings() {
    var opened = _openConfigPanel('bluetooth', 'config-bluetooth-adapters-card', 'start');
    _highlightConfigTarget((opened && opened.target) || document.getElementById('config-bluetooth-adapters-card'));
    return false;
}

function _renderDiagConfigButton(actionKey, buttonLabel) {
    return '<button type="button" class="btn btn-sm btn-secondary diag-config-btn" onclick="return runDiagnosticsConfigJump(\'' +
        String(actionKey) + '\')">' + escHtml(buttonLabel || 'Open settings') + '</button>';
}

function runDiagnosticsConfigJump(actionKey) {
    if (actionKey === 'devices') return openDiagnosticsDeviceSettings();
    if (actionKey === 'bluetooth') return openBluetoothSettings();
    if (actionKey === 'ma') return openMaTokenSettings();
    if (actionKey === 'latency') return openLatencySettings();
    return false;
}

function _renderDiagCopyButton(sectionId, label, buttonLabel) {
    return '<button type="button" class="btn btn-sm btn-secondary diag-copy-btn" onclick="return copyDiagnosticsSection(\'' +
        String(sectionId) + '\', \'' + String(label) + '\')">' + escHtml(buttonLabel || 'Copy') + '</button>';
}

function copyDiagnosticsSection(sectionId, label) {
    var target = document.getElementById(sectionId);
    if (!target) {
        showToast('Could not find diagnostics section to copy', 'error');
        return false;
    }
    var copySource = target.cloneNode(true);
    Array.prototype.forEach.call(copySource.querySelectorAll('.diag-copy-btn'), function(node) {
        node.remove();
    });
    var text = (copySource.innerText || copySource.textContent || '').trim().replace(/\n{3,}/g, '\n\n');
    if (!text) {
        showToast('Nothing to copy from this diagnostics section', 'error');
        return false;
    }
    _copyToClipboard((label ? label + '\n\n' : '') + text).then(function() {
        showToast((label || 'Diagnostics section') + ' copied to clipboard', 'info');
    }).catch(function() {
        showToast('Could not copy diagnostics section', 'error');
    });
    return false;
}

function _renderDiagRawDetails(payload, summaryLabel) {
    if (!payload) return '';
    var rawJson = JSON.stringify(payload, null, 2);
    if (!rawJson) return '';
    return '<details class="diag-raw-details">' +
        '<summary>' + escHtml(summaryLabel || 'Raw details') + '</summary>' +
        '<pre class="diag-raw-pre">' + escHtml(rawJson) + '</pre>' +
    '</details>';
}

function _renderRecoveryActionButton(action, options) {
    if (!action || !action.key) return '';
    var opts = options || {};
    var classes = ['btn', 'btn-sm', 'diag-recovery-action'];
    if (opts.primary) classes.push('diag-recovery-action--primary');
    if (opts.menuItem) classes.push('diag-recovery-action--menu-item', 'ui-action-menu-item');
    return '<button type="button" class="' + classes.join(' ') + '" onclick="return _runEncodedOperatorGuidanceAction(\'' +
        _encodeGuidanceAction({
            key: String(action.key || ''),
            device_names: action.device_name ? [String(action.device_name || '')] : (action.device_names || []),
        }) +
    '\')">' + escHtml(action.label || 'Open diagnostics') + '</button>';
}

function _renderRecoveryActionMenu(actions) {
    var visibleActions = (actions || []).filter(function(action) { return action && action.key; });
    if (!visibleActions.length) return '';
    return '<details class="diag-action-menu ui-action-menu">' +
        '<summary class="btn btn-sm diag-recovery-action diag-action-menu-toggle ui-action-menu-toggle">More actions</summary>' +
        '<div class="diag-action-menu-list ui-action-menu-list">' +
            visibleActions.map(function(action) {
                return _renderRecoveryActionButton(action, {menuItem: true});
            }).join('') +
        '</div>' +
    '</details>';
}

function _renderRecoveryIssueActionRow(issue) {
    var primaryAction = issue.primary_action || issue.recommended_action || null;
    var primaryNames = primaryAction && primaryAction.device_name
        ? [String(primaryAction.device_name || '')]
        : ((primaryAction && primaryAction.device_names) || []);
    var secondaryActions = (issue.secondary_actions || []).filter(function(action) {
        if (!primaryAction) return true;
        var actionNames = action.device_name ? [String(action.device_name || '')] : (action.device_names || []);
        return action.key !== primaryAction.key || JSON.stringify(actionNames) !== JSON.stringify(primaryNames);
    });
    if (!primaryAction && !secondaryActions.length) return '';
    return '<div class="diag-recovery-actions">' +
        (primaryAction ? _renderRecoveryActionButton(primaryAction, {primary: true}) : '') +
        _renderRecoveryActionMenu(secondaryActions) +
    '</div>';
}

function _renderRecoveryIssues(issues) {
    if (!issues || !issues.length) {
        return _renderDiagEmptyCardHtml('Recovery clear', 'Nothing needs recovery right now.', {icon: 'check', tone: 'success'});
    }
    return '<div class="diag-recovery-list">' + issues.map(function(issue) {
        var tone = issue.severity === 'error' ? 'error' : 'warning';
        return '<div class="diag-recovery-item is-' + tone + '">' +
            '<div class="diag-recovery-item-title">' + dot(_diagRecoveryDotTone(issue.severity)) + '<span>' + escHtml(issue.title || 'Issue') + '</span></div>' +
            '<div class="diag-recovery-item-summary">' + escHtml(issue.summary || '') + '</div>' +
            _renderRecoveryIssueActionRow(issue) +
        '</div>';
    }).join('') + '</div>';
}

function _renderRecoveryTraces(traces) {
    if (!traces || !traces.length) {
        return _renderDiagEmptyCardHtml('No recent recovery events', 'No recovery events have been recorded yet.', {icon: 'info'});
    }
    return '<div class="diag-trace-list">' + traces.map(function(trace) {
        var entries = trace.entries || [];
        return '<div class="diag-mini-card">' +
            '<div class="diag-mini-title">' + dot(_diagRecoveryDotTone(trace.tone)) + '<span>' + escHtml(trace.label || 'Recovery event') + '</span></div>' +
            '<div class="diag-mini-meta">' + escHtml(trace.summary || '') + '</div>' +
            (entries.length ? entries.map(function(entry) {
                return '<div class="diag-trace-entry">' +
                    dot(_diagRecoveryDotTone(entry.level === 'error' ? 'error' : (entry.level === 'warning' ? 'warning' : 'ok'))) +
                    '<div class="diag-trace-copy">' +
                        '<div class="diag-trace-label">' + escHtml(entry.label || 'Event') + '</div>' +
                        '<div class="diag-trace-meta">' +
                            escHtml(entry.summary || '') +
                            (entry.at ? ' · ' + escHtml(entry.at) : '') +
                        '</div>' +
                    '</div>' +
                '</div>';
            }).join('') : '') +
        '</div>';
    }).join('') + '</div>';
}

function _renderKnownGoodTestPath(testPath) {
    var steps = testPath && testPath.steps ? testPath.steps : [];
    if (!steps.length) {
        return _renderDiagEmptyCardHtml('Verification path unavailable', 'No recommended verification path is available yet.', {icon: 'info'});
    }
    return '<div class="diag-test-path">' + steps.map(function(step) {
        return '<div class="diag-test-step' + (step.reached ? ' is-reached' : '') + '">' +
            '<div>' + (step.reached ? dot('ok') : dot('warn')) + '</div>' +
            '<div class="diag-test-step-copy">' +
                '<div class="diag-test-step-title">' + escHtml(step.label || 'Step') + '</div>' +
                '<div class="diag-test-step-summary">' + escHtml(step.summary || '') + '</div>' +
            '</div>' +
        '</div>';
    }).join('') + '</div>' +
    (testPath.recommended_action
        ? '<div class="diag-recovery-actions" style="margin-top:12px">' + _renderRecoveryActionButton(testPath.recommended_action) + '</div>'
        : '');
}

function _renderRecoveryTimeline(timeline) {
    var timelineView = _recoveryTimelineFilteredView(timeline);
    var entries = timelineView.visibleEntries;
    if (!timelineView.allEntries.length) {
        return _renderDiagEmptyCardHtml('No recovery timeline yet', 'The bridge has not recorded any recovery timeline entries yet.', {icon: 'info'});
    }
    if (!entries.length) {
        return '<div class="diag-timeline">' +
            _renderRecoveryTimelineOverview(timeline, timelineView) +
            _renderRecoveryTimelineControls(timelineView) +
            '<div class="diag-timeline-empty">No retained timeline entries match the current advanced filters.</div>' +
        '</div>';
    }
    return '<div class="diag-timeline">' +
        _renderRecoveryTimelineOverview(timeline, timelineView) +
        _renderRecoveryTimelineControls(timelineView) +
        entries.map(function(entry) {
        return '<div class="diag-timeline-entry is-' + escHtml(entry.level || 'info') + '">' +
            '<div class="diag-timeline-dot">' + dot(_diagRecoveryDotTone(entry.level === 'error' ? 'error' : (entry.level === 'warning' ? 'warning' : 'ok'))) + '</div>' +
            '<div class="diag-timeline-copy">' +
                '<div class="diag-timeline-title">' + escHtml(entry.source || 'Bridge') + ' · ' + escHtml(entry.label || 'Event') + '</div>' +
                '<div class="diag-timeline-summary">' + escHtml(entry.summary || '') + '</div>' +
                '<div class="diag-timeline-meta">' + escHtml(entry.at || 'Latest known state') + '</div>' +
            '</div>' +
        '</div>';
    }).join('') + '</div>';
}

function _renderRecoveryTimelineCard(recoveryTimeline) {
    var recoveryTimelineTone = ((recoveryTimeline.summary && recoveryTimeline.summary.error_count) || 0) > 0
        ? 'error'
        : ((((recoveryTimeline.summary && recoveryTimeline.summary.warning_count) || 0) > 0) ? 'warning' : 'ok');
    var summary = recoveryTimeline.summary || {};
    var visibleEntries = Number(summary.visible_entry_count || summary.entry_count || 0);
    var totalEntries = Number(summary.total_entry_count || visibleEntries);
    var metaText = visibleEntries === totalEntries
        ? (visibleEntries + ' entries retained')
        : ('Showing the latest ' + visibleEntries + ' of ' + totalEntries + ' entries');
    return '<div class="diag-jump-target" id="diag-recovery-timeline"><div class="diag-subsection-title">Chronological recovery timeline</div><div class="diag-mini-card"><div class="diag-mini-title">' +
        dot(_diagRecoveryDotTone(recoveryTimelineTone)) +
        '<span>Recent bridge and speaker events</span></div><div class="diag-mini-meta">' +
        escHtml(metaText) +
        '</div>' +
        _renderRecoveryTimeline(recoveryTimeline) +
        '<div class="diag-recovery-actions"><button type="button" class="btn btn-sm diag-recovery-action" onclick="return _downloadRecoveryTimeline()">' +
        _buttonLabelWithIconHtml('download', 'Export timeline CSV') +
        '</button></div></div></div>';
}

function _renderLatencyPresetButtons(recoveryLatency) {
    var presets = recoveryLatency && recoveryLatency.presets ? recoveryLatency.presets : [];
    if (!presets.length) return '';
    return '<div class="diag-recovery-actions diag-recovery-actions--wrap">' + presets.map(function(preset) {
        return '<button type="button" class="btn btn-sm diag-recovery-action' +
            (preset.value === recoveryLatency.recommended_pulse_latency_msec ? ' diag-recovery-action--primary' : '') +
            '" onclick="return _applyLatencyPreset(' + JSON.stringify(preset.value) + ')">' +
            escHtml(preset.label || (String(preset.value) + ' ms')) +
        '</button>';
    }).join('') + '</div>';
}

function renderDiagnostics(d) {
    var env = d.environment || {};
    var ma = d.ma_integration || {};
    var devices = d.devices || [];
    var sinks = d.sinks || [];
    var adapters = d.adapters || [];
    var groups = ma.syncgroups || [];
    var sinkInputs = d.sink_inputs || [];
    var portAudioDevices = d.portaudio_devices || [];
    var subprocesses = d.subprocesses || [];
    var recovery = d.recovery_assistant || {};

    var activeDevices = devices.filter(function(dev) { return dev.enabled !== false; });
    var connectedDevices = activeDevices.filter(function(dev) { return !!dev.connected; });
    var routedDevices = activeDevices.filter(function(dev) { return !!dev.sink; });
    var playingDevices = activeDevices.filter(function(dev) { return !!dev.playing; });
    var degradedDevices = activeDevices.filter(function(dev) { return !!dev.last_error; });
    var sinkIssueDevices = activeDevices.filter(function(dev) { return !!dev.sink && !!dev.last_error; });
    var healthyAdapters = adapters.filter(function(adapter) { return !adapter.error; });
    var daemonState = d.bluetooth_daemon || 'unknown';
    var daemonActive = daemonState === 'active';
    var audioServerLabel = env.audio_server || d.pulseaudio || 'Unknown audio server';
    var degradedGroups = groups.filter(function(group) {
        return (group.members || []).some(function(member) {
            if (member.available === false) return true;
            if (!member.is_bridge) return false;
            return member.enabled === false || member.bt_connected === false || member.server_connected === false;
        });
    });
    var sinkInputError = sinkInputs.find(function(input) { return !!(input && input.error); }) || null;
    var portAudioError = portAudioDevices.find(function(device) { return !!(device && device.error); }) || null;
    var visibleSinkInputs = sinkInputs.filter(function(input) { return input && !input.error; });
    var visiblePortAudioDevices = portAudioDevices.filter(function(device) { return device && !device.error; });

    var summaryCards = [
        {
            title: 'Speakers connected',
            target: 'diag-speaker-states',
            value: connectedDevices.length + '/' + activeDevices.length,
            tone: connectedDevices.length === activeDevices.length && activeDevices.length
                ? (degradedDevices.length ? 'warn' : 'ok')
                : (connectedDevices.length ? 'warn' : 'error'),
            hint: playingDevices.length + ' playing now' +
                (degradedDevices.length ? ' · ' + degradedDevices.length + ' issue' + (degradedDevices.length === 1 ? '' : 's') : ''),
        },
        {
            title: 'Audio routing',
            target: 'diag-routing',
            value: routedDevices.length + '/' + activeDevices.length,
            tone: routedDevices.length === activeDevices.length && activeDevices.length
                ? (sinkIssueDevices.length ? 'warn' : 'ok')
                : (routedDevices.length ? 'warn' : 'error'),
            hint: sinks.length + ' sink' + (sinks.length === 1 ? '' : 's') + ' detected' +
                (sinkIssueDevices.length ? ' · ' + sinkIssueDevices.length + ' degraded' : ''),
        },
        {
            title: 'Music Assistant',
            target: 'diag-ma-groups-card',
            value: ma.connected ? 'Connected' : (ma.configured ? 'Configured' : 'Offline'),
            tone: ma.connected ? (degradedGroups.length ? 'warn' : 'ok') : (ma.configured ? 'warn' : 'error'),
            hint: groups.length + ' sync group' + (groups.length === 1 ? '' : 's') +
                (degradedGroups.length ? ' · ' + degradedGroups.length + ' degraded' : ''),
        },
        {
            title: 'Bluetooth adapters',
            target: 'diag-routing',
            value: (daemonActive ? healthyAdapters.length : 0) + '/' + adapters.length,
            tone: daemonActive && healthyAdapters.length === adapters.length && adapters.length
                ? 'ok'
                : ((daemonActive || daemonState === 'unknown') && healthyAdapters.length ? 'warn' : 'error'),
            hint: 'Daemon ' + daemonState + ' · ' + audioServerLabel,
        },
    ].map(_renderDiagSummaryCard).join('');

    var overview = [
        {label: 'Version', value: d.version || 'Unknown', code: true},
        {label: 'Build date', value: d.build_date || 'Unknown'},
        {label: 'Uptime', value: d.uptime || 'Unknown'},
        {label: 'Runtime', value: d.runtime || 'Unknown', code: true},
        {label: 'Platform', value: env.platform ? env.platform + (env.arch ? ' (' + env.arch + ')' : '') : 'Unknown', stack: true},
        {label: 'Python', value: env.python ? env.python.split('\n')[0] : 'Unknown', code: true, stack: true},
        {label: 'BlueZ', value: env.bluez || 'Unknown', code: true},
        {label: 'D-Bus', value: d.dbus_available ? 'Available' : 'Missing'},
    ].map(function(item) {
        return _renderDiagInfoItem(item.label, item.value, item);
    }).join('');

    var adapterCards = adapters.length
        ? adapters.map(function(adapter, idx) {
            var tone = adapter.error ? 'err' : (daemonActive ? 'ok' : 'warn');
            var adapterOutcome = _diagAdapterOutcome(adapter, daemonActive, daemonState);
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(tone) + '<span>' + escHtml(adapter.id || ('hci' + idx)) + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    _renderDiagMetaRow('Availability', adapterOutcome, {stack: true}) +
                    _renderDiagMetaRow('Daemon', daemonState === 'active' ? 'Active' : daemonState, {stack: true}) +
                    (adapter.default ? _renderDiagMetaRow('Role', 'Default adapter') : _renderDiagMetaRow('Role', 'Available adapter')) +
                    _renderDiagMetaRow('MAC', adapter.mac, {code: true, stack: true}) +
                    (adapter.error ? _renderDiagMetaRow('Issue', adapter.error, {stack: true}) : '') +
                '</div>' +
                _renderDiagRawDetails(adapter, 'Raw adapter data') +
            '</div>';
        }).join('')
        : _renderDiagEmptyCardHtml('No Bluetooth adapters', 'No Bluetooth adapters were detected.', {icon: 'bt'});

    var deviceCards = devices.length
        ? devices.map(function(dev) {
            var deviceTone = dev.enabled === false ? 'warn' : (dev.connected ? (dev.last_error ? 'warn' : 'ok') : 'err');
            var deviceStatus = dev.playing ? 'Playing' : (dev.connected ? 'Connected' : 'Disconnected');
            var deviceOutcome = _diagDeviceOutcome(dev);
            var routingOutcome = dev.sink
                ? 'Audio is routed to the attached sink.'
                : (dev.connected ? 'Waiting for sink attachment before playback can route.' : 'No Bluetooth audio route is available yet.');
            if (dev.enabled === false) deviceStatus += ' · Disabled';
            if (dev.last_error) deviceStatus += ' · Attention needed';
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(deviceTone) + '<span>' + escHtml(dev.name || dev.mac || 'Unknown') + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    _renderDiagMetaRow('Availability', deviceOutcome, {stack: true}) +
                    _renderDiagMetaRow('Status', deviceStatus) +
                    _renderDiagMetaRow('Routing', routingOutcome, {stack: true}) +
                    _renderDiagMetaRow('MAC', dev.mac, {code: true, stack: true}) +
                    (dev.sink ? _renderDiagMetaRow('Sink', dev.sink, {code: true, stack: true}) : _renderDiagMetaRow('Sink', 'Not attached')) +
                    (dev.last_error ? _renderDiagMetaRow('Issue', dev.last_error, {stack: true}) : '') +
                '</div>' +
                _renderDiagRawDetails(dev, 'Raw speaker data') +
            '</div>';
        }).join('')
        : _renderDiagEmptyCardHtml('No speakers configured', 'Add a speaker in Configuration → Devices to start monitoring it here.', {icon: 'speaker'});

    var sinkStates = {};
    var sinkOwners = {};
    var sinkStatePriority = {idle: 0, connected: 1, running: 2, error: 3};
    function setSinkState(sinkName, nextState, ownerName) {
        if (!sinkName) return;
        if (!sinkStates[sinkName] || sinkStatePriority[nextState] > sinkStatePriority[sinkStates[sinkName]]) {
            sinkStates[sinkName] = nextState;
        }
        if (ownerName && !sinkOwners[sinkName]) sinkOwners[sinkName] = ownerName;
    }
    devices.forEach(function(dev) {
        if (!dev.sink) return;
        setSinkState(
            dev.sink,
            dev.enabled === false ? 'idle' : (dev.last_error ? 'error' : (dev.playing ? 'running' : (dev.connected ? 'connected' : 'idle'))),
            dev.name || dev.mac || '—'
        );
    });
    groups.forEach(function(group) {
        (group.members || []).forEach(function(member) {
            if (!member.sink) return;
            setSinkState(
                member.sink,
                member.available === false ? 'error' : ((member.playing || member.state === 'playing') ? 'running' : 'connected'),
                member.name || member.id || '—'
            );
        });
    });
    var sinkRows = sinks.length
        ? sinks.map(function(sink) {
            var state = sinkStates[sink] || 'idle';
            return '<tr>' +
                '<td><code>' + escHtml(sink) + '</code></td>' +
                '<td><span class="sink-status ' + state + '">' + escHtml(state.toUpperCase()) + '</span></td>' +
                '<td>' + escHtml(sinkOwners[sink] || '—') + '</td>' +
            '</tr>';
        }).join('')
        : '<tr class="sink-table-empty-row"><td colspan="3">' +
            _renderEmptyStateHtml({
                className: 'sink-table-empty-state',
                icon: 'bt',
                title: 'No Bluetooth sinks',
                copy: 'No Bluetooth sinks were detected for the current runtime.',
                compact: true,
                center: true,
                inline: true,
            }) +
        '</td></tr>';

    var groupCards = groups.length
        ? groups.map(function(group) {
            var members = group.members || [];
            var unavailableMembers = members.filter(function(member) { return member.available === false; });
            var bridgeIssues = members.filter(function(member) {
                return member.is_bridge && member.available !== false &&
                    (member.enabled === false || member.bt_connected === false || member.server_connected === false);
            });
            var groupTone = unavailableMembers.length ? 'err' : (bridgeIssues.length ? 'warn' : 'ok');
            var groupStatus = [
                members.length + ' member' + (members.length === 1 ? '' : 's'),
            ];
            if (unavailableMembers.length) {
                groupStatus.push(unavailableMembers.length + ' unavailable');
            }
            if (bridgeIssues.length) {
                groupStatus.push(bridgeIssues.length + ' bridge issue' + (bridgeIssues.length === 1 ? '' : 's'));
            }
            if (!unavailableMembers.length && !bridgeIssues.length) {
                groupStatus.push('All members healthy');
            }
            var groupOutcome = _diagGroupOutcome(unavailableMembers, bridgeIssues);
            var nowPlaying = group.now_playing && group.now_playing.title
                ? (group.now_playing.artist ? group.now_playing.artist + ' — ' + group.now_playing.title : group.now_playing.title)
                : 'Nothing playing';
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(groupTone) + '<span>' + escHtml(group.name || group.id || 'Unnamed group') + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    _renderDiagMetaRow('Availability', groupOutcome, {stack: true}) +
                    _renderDiagMetaRow('Group health', groupStatus.join(' · '), {stack: true}) +
                    _renderDiagMetaRow('Now playing', nowPlaying, {stack: true}) +
                '</div>' +
                _renderDiagRawDetails(group, 'Raw group data') +
            '</div>';
        }).join('')
        : _renderDiagEmptyCardHtml('No Music Assistant groups', 'No Music Assistant groups are available.', {icon: 'ma'});

    var subprocessInfo = subprocesses.length
        ? subprocesses.map(function(proc) {
            var parts = [];
            if (proc.pid) parts.push('pid ' + proc.pid);
            if (proc.running) parts.push('running');
            if (proc.zombie_restarts > 0) parts.push('zombies ' + proc.zombie_restarts);
            var procTone = !proc.alive ? 'err' : (proc.last_error ? 'warn' : 'ok');
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(procTone) + '<span>' + escHtml(proc.name || 'Subprocess') + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    _renderDiagMetaRow('State', parts.join(' · ') || 'No extra details', {stack: true}) +
                    (proc.last_error ? _renderDiagMetaRow('Issue', proc.last_error, {stack: true}) : '') +
                '</div>' +
                _renderDiagRawDetails(proc, 'Raw process data') +
            '</div>';
        }).join('')
        : _renderDiagEmptyCardHtml('No advanced runtime details', 'No advanced runtime details are available.', {icon: 'info'});

    var advancedOverview = [
        {label: 'Audio server', value: audioServerLabel, code: true, stack: true},
        {label: 'Bluetooth daemon', value: daemonState},
        {label: 'Memory (RSS)', value: env.process_rss_mb != null ? env.process_rss_mb + ' MB' : 'Unknown'},
        {label: 'MA connection', value: ma.connected ? 'Connected' : (ma.configured ? 'Configured' : 'Offline')},
        {label: 'Sink inputs', value: sinkInputError ? 'Error' : String(visibleSinkInputs.length)},
        {label: 'Local audio outputs', value: portAudioError ? 'Error' : String(visiblePortAudioDevices.length)},
        {label: 'MA URL', value: ma.url || 'Not configured', code: true, stack: true},
    ].map(function(item) {
        return _renderDiagInfoItem(item.label, item.value, item);
    }).join('');

    var sinkInputCards = sinkInputError
        ? '<div class="diag-mini-card"><div class="diag-mini-title">' + dot('err') + '<span>Audio stream scan failed</span></div><div class="diag-mini-meta">' + escHtml(sinkInputError.error) + '</div></div>'
        : (visibleSinkInputs.length
            ? visibleSinkInputs.map(function(input) {
                var inputTitle = input.application_name || input.media_name || input.media_title || ('Audio stream #' + (input.id || '?'));
                var inputTone = input.state && input.state.toUpperCase() === 'RUNNING' ? 'ok' : 'warn';
                return '<div class="diag-mini-card">' +
                    '<div class="diag-mini-title">' + dot(inputTone) + '<span>' + escHtml(inputTitle) + '</span></div>' +
                    '<div class="diag-mini-meta">' +
                        _renderDiagMetaRow('ID', input.id, {code: true}) +
                        _renderDiagMetaRow('Sink', input.sink, {code: true, stack: true}) +
                        _renderDiagMetaRow('State', input.state) +
                        (input.media_name && input.application_name !== input.media_name ? _renderDiagMetaRow('Media', input.media_name, {stack: true}) : '') +
                    '</div>' +
                    _renderDiagRawDetails(input, 'Raw stream data') +
                '</div>';
            }).join('')
            : _renderDiagEmptyCardHtml('No current audio streams', 'No current audio streams were detected.', {icon: 'speaker'}));

    var portAudioCards = portAudioError
        ? '<div class="diag-mini-card"><div class="diag-mini-title">' + dot('err') + '<span>Local output scan failed</span></div><div class="diag-mini-meta">' + escHtml(portAudioError.error) + '</div></div>'
        : (visiblePortAudioDevices.length
            ? visiblePortAudioDevices.map(function(device) {
                return '<div class="diag-mini-card">' +
                    '<div class="diag-mini-title">' + dot(device.is_default ? 'ok' : 'warn') + '<span>' + escHtml(device.name || 'Audio output') + '</span></div>' +
                    '<div class="diag-mini-meta">' +
                        _renderDiagMetaRow('Index', device.index, {code: true}) +
                        _renderDiagMetaRow('Role', device.is_default ? 'Default output device' : 'Available output device', {stack: true}) +
                    '</div>' +
                    _renderDiagRawDetails(device, 'Raw output data') +
                '</div>';
            }).join('')
            : _renderDiagEmptyCardHtml('No local audio outputs', 'No local audio outputs were detected.', {icon: 'plug'}));

    var recoverySummary = recovery.summary || {};
    var recoveryLatency = recovery.latency_assistant || {};
    var recoveryTimeline = recovery.timeline || {};
    var recoveryOverviewCards = [
        {
            title: 'Needs attention',
            target: 'diag-recovery-issues',
            value: String(recoverySummary.open_issue_count || 0),
            tone: _diagRecoveryToneClass(recoverySummary.highest_severity || 'ok'),
            hint: recoverySummary.headline || 'Nothing needs recovery right now',
        },
        {
            title: 'Latency review',
            target: 'diag-recovery-latency',
            value: recoveryLatency.recommended_pulse_latency_msec != null
                ? String(recoveryLatency.recommended_pulse_latency_msec) + ' ms'
                : '—',
            tone: _diagRecoveryToneClass(recoveryLatency.tone || 'ok'),
            hint: recoveryLatency.summary || 'No latency guidance available.',
        },
        {
            title: 'Timeline',
            target: 'diag-recovery-timeline',
            value: String((recoveryTimeline.summary && recoveryTimeline.summary.entry_count) || 0),
            tone: ((recoveryTimeline.summary && recoveryTimeline.summary.error_count) || 0) > 0
                ? 'error'
                : ((((recoveryTimeline.summary && recoveryTimeline.summary.warning_count) || 0) > 0) ? 'warn' : 'ok'),
            hint: (recoveryTimeline.summary && recoveryTimeline.summary.latest_at) || 'No timeline entries yet',
        },
    ].map(_renderDiagSummaryCard).join('');
    var recoverySafeActions = (recovery.safe_actions || []).map(_renderRecoveryActionButton).join('');
    var diagnosticsActions = '<div class="diag-actions diag-actions--hero">' +
        '<div class="diag-actions-left">' +
            '<button type="button" class="btn btn-sm btn-secondary" onclick="reloadDiagnostics()">' + _buttonLabelWithIconHtml('refresh', 'Refresh') + '</button>' +
            '<button type="button" class="btn btn-sm btn-secondary" onclick="downloadDiagnostics()">' + _buttonLabelWithIconHtml('download', 'Download diagnostics') + '</button>' +
        '</div>' +
        '<div class="diag-actions-right">' +
            '<button type="button" class="btn btn-sm btn-primary" onclick="return _openBugReport(event)">' + _buttonLabelWithIconHtml('report', 'Submit bug report') + '</button>' +
        '</div>' +
    '</div>';
    var latencyHints = (recoveryLatency.hints || []).map(function(hint) {
        return _renderDiagInfoItem('Hint', hint, {stack: true});
    }).join('');
    var latencyFacts = [
        _renderDiagInfoItem(
            'Current Pulse latency',
            recoveryLatency.current_pulse_latency_msec != null ? (String(recoveryLatency.current_pulse_latency_msec) + ' ms') : 'Unknown',
            {stack: true}
        ),
        _renderDiagInfoItem(
            'Recommended Pulse latency',
            recoveryLatency.recommended_pulse_latency_msec != null ? (String(recoveryLatency.recommended_pulse_latency_msec) + ' ms') : 'Unknown',
            {stack: true}
        ),
        _renderDiagInfoItem('Why', recoveryLatency.recommended_summary || 'No recommendation yet.', {stack: true}),
    ].join('');
    var recoveryLatencyCard = '<div class="diag-jump-target" id="diag-recovery-latency"><div class="diag-subsection-title">Latency review</div><div class="diag-mini-card"><div class="diag-mini-title">' +
        dot(_diagRecoveryDotTone(recoveryLatency.tone || 'ok')) +
        '<span>Latency guidance</span></div><div class="diag-mini-meta">' +
        escHtml(recoveryLatency.summary || 'No latency guidance available.') +
        '</div><div class="diag-grid diag-runtime-grid">' + latencyFacts + latencyHints + '</div>' +
        _renderLatencyPresetButtons(recoveryLatency) +
        '<div class="diag-recovery-actions">' +
        _renderDiagConfigButton('latency', 'Latency settings') +
        (((recoveryLatency.safe_actions || []).length) ? (recoveryLatency.safe_actions || []).map(_renderRecoveryActionButton).join('') : '') +
        '</div></div></div>';
    var recoveryTimelineCard = _renderRecoveryTimelineCard(recoveryTimeline);
    var advancedPreviewBits = [
        subprocesses.length + ' daemon' + (subprocesses.length === 1 ? '' : 's'),
        (sinkInputError ? 'audio stream scan failed' : (visibleSinkInputs.length + ' audio stream' + (visibleSinkInputs.length === 1 ? '' : 's'))),
        (portAudioError ? 'local output scan failed' : (visiblePortAudioDevices.length + ' local output' + (visiblePortAudioDevices.length === 1 ? '' : 's'))),
    ].join(' · ');

    return '<div class="diag-panel">' +
        '<div class="diag-overview-head">' +
            '<div class="diag-overview-title">Overview</div>' +
            '<div class="diag-overview-copy">Start in this first layer for the current issue, bridge health, and speaker readiness.</div>' +
        '</div>' +
        '<div class="diag-card diag-card--primary diag-jump-target" id="diag-recovery-center">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Recovery center</div><div class="diag-card-subtitle">Start here for current blockers, recent recovery signals, and the safest next step.</div></div><div class="diag-card-header-actions">' + _renderDiagCopyButton('diag-recovery-center', 'Recovery center') + '</div></div>' +
            '<div class="diag-summary-grid">' + recoveryOverviewCards + '</div>' +
            '<div class="diag-recovery-summary diag-recovery-summary--hero">' +
                '<div class="diag-recovery-headline">' + escHtml(recoverySummary.headline || 'Nothing needs recovery right now') + '</div>' +
                '<div class="diag-recovery-copy">' + escHtml(recoverySummary.summary || 'Playback and recovery signals look healthy right now.') + '</div>' +
                (recoverySafeActions ? '<div class="diag-recovery-actions">' + recoverySafeActions + '</div>' : '') +
                diagnosticsActions +
            '</div>' +
            '<div class="diag-recovery-grid">' +
                '<div class="diag-jump-target" id="diag-recovery-issues"><div class="diag-subsection-title">Active issues</div>' + _renderRecoveryIssues(recovery.issues || []) + '</div>' +
                '<div><div class="diag-subsection-title">Recent recovery events</div>' + _renderRecoveryTraces(recovery.traces || []) + '</div>' +
            '</div>' +
            '<div class="diag-recovery-grid">' +
                recoveryLatencyCard +
                '<div><div class="diag-subsection-title">Recommended verification path</div>' + _renderKnownGoodTestPath(recovery.known_good_test_path || {}) + '</div>' +
            '</div>' +
            '<div class="diag-recovery-grid">' +
                recoveryTimelineCard +
                '<div></div>' +
            '</div>' +
        '</div>' +
        '<div class="diag-card diag-jump-target" id="diag-health-summary">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Health summary</div><div class="diag-card-subtitle">Fast read on speaker health, routing coverage, and Music Assistant readiness.</div></div><div class="diag-card-header-actions">' + _renderDiagCopyButton('diag-health-summary', 'Health summary') + '</div></div>' +
            '<div class="diag-summary-grid">' + summaryCards + '</div>' +
            '<div class="diag-grid diag-runtime-grid">' + overview + '</div>' +
        '</div>' +
        '<div class="diag-card diag-jump-target" id="diag-speaker-states">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Speaker states</div><div class="diag-card-subtitle">Connection state, sink attachment, and the clearest next hint for each speaker.</div></div><div class="diag-card-header-actions">' + _renderDiagConfigButton('devices', 'Device settings') + _renderDiagCopyButton('diag-speaker-states', 'Speaker states') + '</div></div>' +
            '<div class="diag-devices">' + deviceCards + '</div>' +
        '</div>' +
        '<div class="diag-card diag-jump-target" id="diag-routing">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Adapters & routing</div><div class="diag-card-subtitle">Detected controllers and attached PulseAudio / PipeWire outputs.</div></div><div class="diag-card-header-actions">' + _renderDiagConfigButton('bluetooth', 'Bluetooth settings') + _renderDiagCopyButton('diag-routing', 'Adapters & routing') + '</div></div>' +
            '<div class="diag-adapters">' + adapterCards + '</div>' +
            '<div class="sink-table-wrap"><table class="sink-table"><thead><tr><th>Sink</th><th>Status</th><th>Used by</th></tr></thead><tbody>' + sinkRows + '</tbody></table></div>' +
        '</div>' +
        '<div class="diag-card diag-jump-target" id="diag-ma-groups-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Music Assistant groups</div><div class="diag-card-subtitle">' + escHtml(ma.url || 'No MA URL configured') + '</div></div><div class="diag-card-header-actions">' + _renderDiagConfigButton('ma', 'MA settings') + _renderDiagCopyButton('diag-ma-groups-card', 'Music Assistant groups') + '</div></div>' +
            '<div class="diag-ma-groups">' + groupCards + '</div>' +
        '</div>' +
        '<details class="diag-advanced-section">' +
            '<summary>' +
                '<span class="diag-advanced-summary">' +
                    '<span class="diag-advanced-title">Advanced diagnostics</span>' +
                    '<span class="diag-advanced-copy">' + escHtml(advancedPreviewBits) + '</span>' +
                '</span>' +
            '</summary>' +
            '<div class="diag-advanced-panel">' +
                '<div class="diag-card" id="diag-advanced-runtime">' +
                    '<div class="diag-card-header"><div><div class="diag-card-title">Advanced runtime details</div><div class="diag-card-subtitle">Deep runtime details for bridge daemons, audio streams, and local outputs.</div></div><div class="diag-card-header-actions">' + _renderDiagCopyButton('diag-advanced-runtime', 'Advanced runtime details') + '</div></div>' +
                    '<div class="diag-devices">' + subprocessInfo + '</div>' +
                    '<div class="diag-grid diag-runtime-grid">' + advancedOverview + '</div>' +
                    '<div class="diag-subsection">' +
                        '<div class="diag-subsection-title">Current audio streams</div>' +
                        '<div class="diag-devices diag-subsection-grid">' + sinkInputCards + '</div>' +
                    '</div>' +
                    '<div class="diag-subsection">' +
                        '<div class="diag-subsection-title">Local audio outputs</div>' +
                        '<div class="diag-devices diag-subsection-grid">' + portAudioCards + '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</details>' +
    '</div>';
}

function reloadDiagnostics() {
    var content = document.getElementById('diag-content');
    delete content.dataset.loaded;
    loadDiagnostics(content);
}

async function downloadDiagnostics() {
    try {
        var resp = await fetch(API_BASE + '/api/diagnostics/download');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        var blob = await resp.blob();
        var cd = resp.headers.get('content-disposition') || '';
        var fname = (cd.match(/filename="?([^"]+)"?/) || [])[1] || 'diagnostics.txt';
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = fname;
        a.click();
        URL.revokeObjectURL(a.href);
    } catch (err) {
        showToast('Download failed: ' + err.message, 'error');
    }
}

// ---- Global Health Indicator ----
function _guidanceHeaderToneClass(tone) {
    return tone === 'error'
        ? 'error'
        : tone === 'warning'
            ? 'warning'
            : tone === 'info'
                ? 'info'
                : tone === 'success'
                    ? 'success'
                    : 'neutral';
}

function updateHealthIndicator(devices, guidance) {
    var el = document.getElementById('health-indicator');
    if (!el) return;
    var parts = [];
    if (_backendServiceState && _backendServiceState.label) {
        parts.push(
            '<span class="health-pill meta-badge meta-badge-status guidance-health-pill is-' +
            _guidanceHeaderToneClass(_backendServiceState.tone || 'info') +
            '" title="' + escHtmlAttr(_backendServiceState.summary || '') + '">' +
                '<span class="health-pill-text">' + escHtml(_backendServiceState.label || '') + '</span>' +
            '</span>'
        );
    }
    var headerStatus = !_backendServiceState && guidance && guidance.header_status ? guidance.header_status : null;
    if (headerStatus && headerStatus.label) {
        var headerToneClass = _guidanceHeaderToneClass(headerStatus.tone);
        var onboardingCard = guidance && guidance.onboarding_card ? guidance.onboarding_card : null;
        var togglesOnboarding = _canRenderOnboardingAssistant(onboardingCard);
        var headerTitle = headerStatus.summary || '';
        parts.push(
            '<span class="health-pill meta-badge meta-badge-status guidance-health-pill is-' + headerToneClass +
            '" title="' + escHtmlAttr(headerTitle) + '">' +
                '<span class="health-pill-text">' + escHtml(headerStatus.label || '') + '</span>' +
            '</span>'
        );
        if (togglesOnboarding) {
            var onboardingExpanded = _isOnboardingAssistantExpanded(onboardingCard, {
                showByDefault: _onboardingShowByDefault(guidance),
            });
            parts.push(_renderOnboardingHeaderToggle(onboardingExpanded));
        }
    }
    if (!devices || !devices.length) {
        el.innerHTML = parts.join('');
        return;
    }
    var active = devices.filter(function(d) {
        return d.bt_management_enabled !== false || d.bt_released_by === 'auto';
    });
    var standbyCount = active.filter(function(d) { return !!d.bt_standby; }).length;
    var released = devices.length - active.length;
    var total = active.length;
    var playing = 0, btOk = 0, maOk = 0;
    active.forEach(function(d) {
        if (d.playing) playing++;
        if (d.bluetooth_connected || d.bt_standby) btOk++;
        if (d.connected || d.bt_standby) maOk++;
    });
    if (total > 0) {
        var btClass = btOk === total ? 'ok' : btOk > 0 ? 'warn' : 'error';
        var btTone = btClass === 'ok' ? 'success' : btClass === 'warn' ? 'warning' : 'error';
        var btMeta = _buildBadgeStateMeta(btTone, false, 'Bluetooth availability');
        parts.push('<span class="health-pill meta-badge meta-badge-link is-' + btTone + '">' +
            _renderBadgeIndicatorHtml('bt', btMeta) + '<span class="health-pill-text">' + btOk + '/' + total + '</span></span>');
    }
    if (total > 0) {
        var maClass = maOk === total ? 'ok' : maOk > 0 ? 'warn' : 'error';
        var maTone = maClass === 'ok' ? 'success' : maClass === 'warn' ? 'warning' : 'error';
        var maMeta = _buildBadgeStateMeta(maTone, false, 'Music Assistant availability');
        parts.push('<span class="health-pill meta-badge meta-badge-service is-' + maTone + '">' +
            _renderBadgeIndicatorHtml('ma', maMeta) + '<span class="health-pill-text">' + maOk + '/' + total + '</span></span>');
    }
    if (playing > 0) {
        parts.push('<span class="health-pill meta-badge meta-badge-status is-success">' +
            _renderBadgeIndicatorHtml('status', {key: 'playing'}) + '<span class="health-pill-text">' + playing + '</span></span>');
    }
    if (released > 0) {
        parts.push('<span class="health-pill meta-badge meta-badge-status is-neutral">' +
            _renderBadgeIndicatorHtml('release', _buildBadgeStateMeta('neutral', false, 'Released devices')) +
            '<span class="health-pill-text">' + released + '</span></span>');
    }
    el.innerHTML = parts.join('');
}

// ---- Keyboard shortcuts ----
document.addEventListener('keydown', function(e) {
    // Skip if user is typing in an input/textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === 'r' || e.key === 'R') { updateStatus(); showToast('Refreshed status', 'info'); }
    if (e.key === 'p' || e.key === 'P') { onPauseAll(); }
    if (e.key === 's' || e.key === 'S') {
        document.getElementById('config-form').dispatchEvent(new Event('submit', {cancelable: true}));
    }
});

// ---- Slider fill coloring (MA-style colored track) ----
function updateSliderFill(el) {
    var min = +el.min || 0, max = +el.max || 100, val = +el.value;
    var pct = ((val - min) / (max - min)) * 100;
    var primary = getComputedStyle(document.documentElement).getPropertyValue('--primary-color').trim() || '#03a9f4';
    var track = getComputedStyle(document.documentElement).getPropertyValue('--divider-color').trim() || 'rgba(0,0,0,.12)';
    el.style.setProperty('--slider-fill', 'linear-gradient(to right, ' + primary + ' ' + pct + '%, ' + track + ' ' + pct + '%)');
}

// ---- Init ----
_hydrateUiIcons(document);
initConfigTabs();
_applyBackendServiceState({
    kind: 'connecting',
    tone: 'info',
    label: 'Connecting…',
    title: 'Connecting to bridge',
    summary: 'Waiting for the backend to start. This page will update automatically when the service becomes ready.',
    action: {key: 'refresh_diagnostics', label: 'Retry now'},
});
_renderBackendServicePlaceholder(_backendServiceState);
loadConfig();   // calls loadBtAdapters() internally after restoring btManualAdapters
var _viewModeMedia = _getViewModeMediaQuery();
if (_viewModeMedia) {
    if (typeof _viewModeMedia.addEventListener === 'function') {
        _viewModeMedia.addEventListener('change', _syncViewModeForViewport);
    } else if (typeof _viewModeMedia.addListener === 'function') {
        _viewModeMedia.addListener(_syncViewModeForViewport);
    }
}
window.addEventListener('resize', _syncViewModeForViewport);
_syncViewModeForViewport();
updateStatus();

// Use SSE for real-time status push; fall back to polling if not supported
var _statusInterval = null;
(function _initStatusStream() {
    if (typeof EventSource === 'undefined') {
        _statusInterval = setInterval(updateStatus, 2000);
        return;
    }

    var _sseRetries = 0;
    var _maxRetries = 5;

    function connectSSE() {
        var es = new EventSource(API_BASE + '/api/status/stream');

        es.onopen = function() {
            _sseRetries = 0;
            // SSE connected — stop polling fallback
            if (_statusInterval) { clearInterval(_statusInterval); _statusInterval = null; }
        };

        es.onmessage = function(e) {
            try {
                renderStatusPayload(JSON.parse(e.data));
            } catch (err) { console.error('SSE parse error:', err); }
        };

        es.onerror = function() {
            es.close();
            _sseRetries++;
            if (_sseRetries <= _maxRetries) {
                // Exponential backoff: 1s, 2s, 4s, 8s, 16s
                var delay = Math.min(1000 * Math.pow(2, _sseRetries - 1), 16000);
                console.warn('SSE error, reconnecting in ' + delay + 'ms (attempt ' + _sseRetries + '/' + _maxRetries + ')');
                // Start polling while waiting to reconnect
                if (!_statusInterval) _statusInterval = setInterval(updateStatus, 2000);
                setTimeout(connectSSE, delay);
            } else {
                console.warn('SSE failed after ' + _maxRetries + ' retries, using polling');
                if (!_statusInterval) _statusInterval = setInterval(updateStatus, 2000);
            }
        };

        window.addEventListener('beforeunload', function() { es.close(); });
    }

    connectSSE();
})();

window.addEventListener('beforeunload', function() {
    if (_statusInterval) clearInterval(_statusInterval);
    clearInterval(_tzPreviewInterval);
});
refreshLogs();
toggleAutoRefresh(false);
loadVersionInfo();
