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

function applyThemeMode(isDark) {
    document.documentElement.classList.toggle('theme-dark', !!isDark);
}

applyThemeMode(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);

// HA Ingress theme injection listener
// HA sends setTheme postMessage when theme changes (Ingress mode)
window.addEventListener('message', function(e) {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type !== 'setTheme') return;
    if (e.origin !== window.location.origin && e.source !== window.parent) return;
    var theme = e.data.theme || {};
    var root = document.documentElement;
    Object.keys(theme).forEach(function(key) {
        if (key) root.style.setProperty('--' + key, theme[key]);
    });
    var mode = e.data.mode || e.data.themeMode || '';
    if (mode === 'dark' || mode === 'light') {
        applyThemeMode(mode === 'dark');
        return;
    }
    var bg = theme['primary-background-color'] || theme['card-background-color'];
    if (bg) applyThemeMode(_isDarkThemeColor(bg));
});

// ---- State ----
var autoRefreshLogs = false;
var autoRefreshInterval = null;
var allLogs = [];
var recentLogIssueState = { hasMeta: false, hasIssues: false, level: '', count: 0 };
var currentLogLevel = 'all';
var btAdapters = [];
var btManualAdapters = [];
var lastDevices = [];
var lastGroups = [];
var lastMaWebUrl = '';
var VIEW_MODE_STORAGE_KEY = 'sendspin-ui:view-mode';
var _viewModeStorageScope = 'default';
var _runtimeMode = 'production';
var _demoScreenshotDefaultsApplied = false;
var userPreferredViewMode = _loadSavedViewMode();
var currentViewMode = userPreferredViewMode || 'list';
var listSortState = {column: 'status', direction: 'desc'};
var expandedListRowKey = null;
var _muteDebounce = {};  // player_name → timestamp of last user mute action
var _btnLocks = {};      // btnId → expiry timestamp
var _deviceSettingsHighlightTimer = null;
var _adapterSettingsHighlightTimer = null;

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
var _maProgSnapshots = {};  // deviceIndex -> {elapsed, duration, t} for MA progress interpolation

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
    if (maHasProg) {
        var maSnapshot = idx != null
            ? _buildMergedMaProgressSnapshot(idx, ma, now)
            : {
                elapsed: Math.max(0, Math.min(Number(ma.elapsed) || 0, Number(ma.duration) || 0)),
                duration: Math.max(0, Number(ma.duration) || 0),
                t: now,
                key: _getMaProgressTrackKey(ma),
            };
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

    var nativeHasProg = dev.playing && dev.track_duration_ms > 0 && dev.track_progress_ms != null;
    if (nativeHasProg) {
        var nativeDuration = Math.max(0, Number(dev.track_duration_ms) || 0);
        var nativeProgress = Math.max(0, Math.min(Number(dev.track_progress_ms) || 0, nativeDuration));
        if (idx != null) {
            _progSnapshots[idx] = {pos: nativeProgress, dur: nativeDuration, t: now};
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
    if (statusVal === 'reconnecting') return !!dev.reconnecting;
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

function getDeviceSinkLabel(dev) {
    var sinkName = getDeviceSinkName(dev);
    if (sinkName) return sinkName;
    if (dev && dev.bt_management_enabled === false) return 'Released';
    if (dev && dev.bluetooth_connected) return 'Waiting for sink';
    return 'Not attached';
}

function getDeviceStatusKey(dev) {
    if (dev && dev.bt_management_enabled === false) return 'released';
    return getUnifiedDeviceStatusMeta(dev).key;
}

function getDeviceStatusLabel(dev) {
    if (dev && dev.bt_management_enabled === false) return getDeviceReleaseMeta(dev).label;
    return getUnifiedDeviceStatusMeta(dev).label;
}

function getDeviceStatusClass(dev) {
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
}

function _buttonLabelWithIconHtml(kind, label) {
    return _uiIconSvg(kind, 'btn-icon-svg') + '<span>' + escHtml(label) + '</span>';
}

function _getBtBadgeStateMeta(dev, adapterInfo) {
    var info = adapterInfo || _getAdapterDisplayInfo(dev);
    if (info.empty) return _buildBadgeStateMeta('neutral', false, 'No Bluetooth adapter assigned');
    if (dev && dev.bt_management_enabled === false && dev.bt_released_by === 'auto') {
        return _buildBadgeStateMeta('warning', false, 'Bluetooth management auto-disabled');
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
    if (dev && dev.server_connected) {
        return _buildBadgeStateMeta('success', false, maConnected ? 'Music Assistant connected' : 'Bridge service connected');
    }
    return _buildBadgeStateMeta('error', false, 'Music Assistant unavailable');
}

function _getGroupBadgeStateMeta(dev, groupMeta) {
    var meta = groupMeta || _getGroupBadgeMeta(dev);
    if (!meta || meta.isEmpty) return _buildBadgeStateMeta('neutral', false, 'No Music Assistant group');
    return _buildBadgeStateMeta('info', false, 'Music Assistant group assigned');
}

function getDeviceReleaseMeta(dev) {
    var isReleased = !!(dev && dev.bt_management_enabled === false);
    var isAuto = !!(isReleased && dev.bt_released_by === 'auto');
    var stateMeta = _buildBadgeStateMeta(isAuto ? 'warning' : 'neutral', false, isAuto
        ? 'Bluetooth management auto-disabled'
        : 'Bluetooth management released');
    return {
        visible: isReleased,
        isAuto: isAuto,
        label: isAuto ? 'Auto-disabled' : 'Released',
        summary: isAuto ? 'Auto-disabled after connection issues' : 'Ready to reclaim',
        title: isAuto
            ? 'Auto-disabled due to connection issues — click Reclaim to retry'
            : 'BT management disabled — click Reclaim to resume',
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

    if (safeDev.reconnecting) {
        key = 'reconnecting';
        label = 'Reconnecting';
        tone = 'warning';
        summary = 'Trying to reconnect';
        pulse = true;
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
                    : key === 'disconnected'
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

document.addEventListener('click', function() {
    _closeArtworkPreviews();
});

document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') _closeArtworkPreviews();
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

function _setViewModeStorageScope(runtimeMode) {
    var nextScope = runtimeMode === 'demo' ? 'demo' : 'default';
    if (_viewModeStorageScope === nextScope) return;
    _viewModeStorageScope = nextScope;
    userPreferredViewMode = _loadSavedViewMode();
    currentViewMode = userPreferredViewMode || 'list';
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
    if (logsSection) logsSection.open = true;

    var pairedBox = document.getElementById('paired-box');
    var pairedList = document.getElementById('paired-list');
    if (pairedBox && pairedList && !pairedBox.hidden) {
        pairedList.hidden = false;
        var pairedArrow = pairedBox.querySelector('.paired-arrow');
        if (pairedArrow) pairedArrow.classList.add('expanded');
    }
}

function _getAutomaticViewMode(deviceCount) {
    return 'list';
}

function _resolveViewMode(deviceCount) {
    return userPreferredViewMode || _getAutomaticViewMode(deviceCount);
}

function _applyViewModeButtons(mode) {
    var gridBtn = document.getElementById('view-grid-btn');
    var listBtn = document.getElementById('view-list-btn');
    if (gridBtn) {
        gridBtn.classList.toggle('active', mode !== 'list');
        gridBtn.setAttribute('aria-pressed', mode !== 'list' ? 'true' : 'false');
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

function _settingsIconHtml() {
    return '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19.14 12.94c.04-.31.06-.62.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.03 7.03 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.58.23-1.13.54-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.7 8.84a.5.5 0 0 0 .12.64l2.03 1.58c-.04.31-.06.62-.06.94s.02.63.06.94L2.82 14.52a.5.5 0 0 0-.12.64l1.92 3.32c.13.23.4.32.64.22l2.39-.96c.5.4 1.05.72 1.63.94l.36 2.54c.04.24.25.42.5.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.58-.23 1.13-.54 1.63-.94l2.39.96c.24.1.51.01.64-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58ZM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7Z"/></svg>';
}

function _findBtConfigWrap(dev) {
    var targetMac = _normalizeDeviceMac(dev && (dev.bluetooth_mac || dev.mac));
    var targetName = _normalizeDeviceName(dev && dev.player_name);
    var wraps = document.querySelectorAll('#bt-devices-table .bt-device-wrap');
    for (var i = 0; i < wraps.length; i++) {
        var wrap = wraps[i];
        if (targetMac && wrap.dataset.deviceMac === targetMac) return wrap;
        if (targetName && wrap.dataset.deviceName === targetName) return wrap;
    }
    return null;
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

function openDeviceSettings(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
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

function _getMaGroupSettingsUrl(dev) {
    var groupId = '';
    if (dev && dev.ma_syncgroup_id) groupId = String(dev.ma_syncgroup_id);
    else if (dev && dev.group_id && String(dev.group_id).indexOf('syncgroup_') === 0) groupId = String(dev.group_id);
    var maWebUrl = lastMaWebUrl ? String(lastMaWebUrl).replace(/\/+$/, '') : '';
    if (!groupId || !maWebUrl) return '';
    return maWebUrl + '/#/settings/editplayer/' + encodeURIComponent(groupId);
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
    var popup = window.open(url, '_blank', 'noopener,noreferrer');
    if (popup) {
        try { popup.opener = null; } catch (_) {}
        return true;
    }
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

function _getListCollapsedBadgesHtml(dev, i) {
    var badges = [];
    var releaseMeta = getDeviceReleaseMeta(dev);
    if (releaseMeta.visible) {
        badges.push(_renderReleaseBadgeHtml(releaseMeta, 'chip list-inline-badge'));
    }
    var serviceState = _getServiceBadgeStateMeta(dev);
    var maConnected = !!((dev.ma_now_playing || {}).connected);
    badges.push(
        '<span class="chip meta-badge meta-badge-service service-chip-badge ma-service-badge list-inline-badge ' + serviceState.toneClass + '"' +
            ' title="' + escHtmlAttr(serviceState.summary || 'Music Assistant service') + '">' +
            _renderBadgeIndicatorHtml('ma', serviceState) +
            (maConnected ? '<span class="ma-chip-tag">API</span>' : '') +
        '</span>'
    );

    var syncMeta = _getSyncStatusMeta(dev, i);
    if (syncMeta.visible) {
        badges.push(
            '<span class="chip meta-badge meta-badge-status list-inline-badge list-sync-chip ' + syncMeta.toneClass + '"' +
                ' title="' + escHtmlAttr(syncMeta.title || 'Synchronization status') + '">' +
                _renderBadgeIndicatorHtml(syncMeta.indicatorKind || 'chain', syncMeta) +
                '<span class="meta-badge-label">' + escHtml(syncMeta.text) + '</span>' +
            '</span>'
            );
    }
    if (syncMeta.visible && syncMeta.detailText) {
        badges.push(
            '<span class="chip meta-badge meta-badge-status list-inline-badge list-sync-detail-chip ' + syncMeta.detailToneClass + '"' +
                ' title="' + escHtmlAttr(syncMeta.detailTitle || 'Sync details') + '">' +
                _getSyncDetailBadgeInnerHtml(syncMeta) +
            '</span>'
        );
    }

    var batteryMeta = _getBatteryBadgeMeta(dev.battery_level);
    if (batteryMeta.visible) {
        badges.push(
            '<span class="chip meta-badge meta-badge-status list-inline-badge list-battery-chip ' + batteryMeta.toneClass + '" title="' + escHtmlAttr(batteryMeta.title) + '">' +
                batteryMeta.html +
            '</span>'
        );
    }

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
            if (dev.reconnecting) return 2;
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

function _getEqualizerStateClass(dev) {
    if (!!dev.playing && !!dev.audio_streaming) return ' active';
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
    var album = _firstOfSlash(deviceMaActive ? (ma.album || '') : '');
    return {
        ma: ma,
        deviceMaActive: deviceMaActive,
        track: track,
        artist: artist,
        album: album,
        artUrl: deviceMaActive ? (ma.image_url || '') : '',
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
    var hasSink = deviceHasSink(safeDev);
    var hasSendspin = !!safeDev.server_connected;
    var hasMaApi = !!ma.connected;
    var queueUnavailableTitle = !hasSendspin ? 'Sendspin not connected' : 'Music Assistant API not connected';
    var pendingSummary = _getPendingMaSummary(maMeta);
    var queueActionPending = _isQueueTransportActionPending(maMeta);
    var shufflePending = _hasPendingMaAction(maMeta, 'shuffle');
    var repeatPending = _hasPendingMaAction(maMeta, 'repeat');
    return {
        hasSink: hasSink,
        canTransport: hasSendspin,
        hasQueueControls: !!(hasSendspin && hasMaApi),
        isPlaying: !!safeDev.playing,
        shuffle: !!ma.shuffle,
        repeat: ma.repeat || 'off',
        transportUnavailableTitle: hasSendspin ? '' : 'Sendspin not connected',
        queueUnavailableTitle: queueUnavailableTitle,
        muteUnavailableTitle: hasSink ? '' : 'Audio sink not configured',
        pendingSummary: pendingSummary,
        queueActionPending: queueActionPending,
        shufflePending: shufflePending,
        repeatPending: repeatPending,
        shuffleTitle: _buildQueueActionTitle(
            ma.shuffle ? 'Shuffle on — click to disable' : 'Shuffle off — click to enable',
            queueActionPending,
            !!(hasSendspin && hasMaApi) ? '' : queueUnavailableTitle,
            pendingSummary
        ),
        repeatTitle: _buildQueueActionTitle(
            'Repeat: ' + (ma.repeat || 'off') + ' — click to cycle',
            queueActionPending,
            !!(hasSendspin && hasMaApi) ? '' : queueUnavailableTitle,
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
            onclick: kind === 'repeat' ? 'maCycleRepeat(' + i + ')' : 'maQueueCmd(\'' + kind + '\', undefined, ' + i + ')',
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
            onclick: 'maQueueCmd(\'' + (kind === 'prev' ? 'previous' : 'next') + '\', undefined, ' + i + ')',
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
        return '<div class="list-view-shell"><div class="list-empty-state">No devices match the current filters.</div></div>';
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
        var canTransport = transportState.canTransport;
        var canMute = transportState.hasSink;
        var hasQueueNeighbors = !!(mediaState.ma || {}).connected;
        var pauseTitle = canTransport ? (dev.playing ? 'Pause' : 'Play') : transportState.transportUnavailableTitle;
        var muteTitle = canMute
            ? (effectiveMuted ? 'Unmute' : 'Mute')
            : transportState.muteUnavailableTitle;
        var trackTitleEq = dev.playing && trackLabel !== 'Nothing playing'
            ? _getEqualizerHtml(dev, 'list-track-eq')
            : '';
        var playerNameEq = !expanded && dev.playing
            ? _getEqualizerHtml(dev, 'list-name-eq')
            : '';
        var rowPauseBtnId = 'drow-pause-' + i;
        var rowMuteBtnId = 'drow-mute-' + i;
        var releaseActionClass = mgmtEnabled ? 'warn' : 'success';
        var detailTransport = '<div class="list-player-transport" onclick="event.stopPropagation()">' +
            _renderPlaybackTransportButtonsHtml(i, transportState, {
                buttonBaseClass: 'icon-btn list-player-transport-btn',
                primaryButtonClass: 'icon-btn list-player-transport-btn is-primary',
                modeButtonClass: 'icon-btn list-player-transport-btn is-mode',
                modeFirst: true,
                prevTitle: 'Previous track',
                nextTitle: 'Next track',
            }) +
        '</div>';
        var detailActions = '<div class="list-detail-actions" onclick="event.stopPropagation()">' +
            '<button type="button" class="list-action-btn accent" id="dbtn-reconnect-' + i + '" onclick="btReconnect(' + i + ')"' + (mgmtEnabled ? '' : ' disabled') + '>' + _actionButtonInnerHtml('reconnect', 'Reconnect') + '</button>' +
            '<button type="button" class="list-action-btn ' + releaseActionClass + '" id="dbtn-release-' + i + '" onclick="btToggleManagement(' + i + ')">' + _actionButtonInnerHtml('release', mgmtEnabled ? 'Release' : 'Reclaim') + '</button>' +
            '<button type="button" class="list-action-btn danger" onclick="confirmDisableDevice(' + i + ')">' + _actionButtonInnerHtml('disable', 'Disable') + '</button>' +
        '</div>';
        var routeSummary = _getListRoutingSummary(dev);
        var detailFooter = '<div class="list-detail-footer">' +
            (routeSummary ? '<div class="list-route-summary">' + escHtml(routeSummary) + '</div>' : '<div class="list-route-summary-spacer"></div>') +
        '</div>';
        var detailCurrentCopy = _renderNowPlayingTextHtml(mediaState, {
            containerClass: 'list-detail-current-copy is-rail',
            preTitleHtml: '<div class="list-now-playing-row">' +
                ((dev.playing && trackLabel !== 'Nothing playing')
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
        var detailPlaybackRail = '<div class="list-detail-playback-rail' + (hasQueueNeighbors ? '' : ' is-solo') + '">' +
            detailMediaLane +
        '</div>';
        var quickActions = '<div class="list-actions" onclick="event.stopPropagation()">' +
            '<button type="button" class="icon-btn list-inline-btn' + (dev.playing ? '' : ' paused') + '" id="' + rowPauseBtnId + '" onclick="event.stopPropagation();onDevicePause(' + i + ', \'' + rowPauseBtnId + '\')" title="' + escHtmlAttr(pauseTitle) + '"' + (canTransport ? '' : ' disabled') + '>' + _playPauseIconHtml(dev.playing) + '</button>' +
            '<button type="button" class="icon-btn list-inline-btn' + (effectiveMuted ? ' muted' : '') + '" id="' + rowMuteBtnId + '" onclick="event.stopPropagation();onMuteClick(' + i + ', \'' + rowMuteBtnId + '\')" title="' + escHtmlAttr(muteTitle) + '"' + (canMute ? '' : ' disabled') + '>' + _muteIconHtml(effectiveMuted) + '</button>' +
            '<button type="button" class="icon-btn list-inline-btn list-settings-btn" onclick="event.stopPropagation();openDeviceSettings(' + i + ')" title="Device settings">' + _settingsIconHtml() + '</button>' +
            '<span class="list-row-affordance' + (expanded ? ' expanded' : '') + '" aria-hidden="true">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>' +
            '</span>' +
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
        return '<div class="list-row ' + statusMeta.cardStateClass + ' ' + (expanded ? 'expanded' : '') + '">' +
            '<div class="list-row-main" onclick="toggleListRow(\'' + escHtmlAttr(key) + '\')">' +
                '<div class="list-select-cell"><input type="checkbox" id="dsel-' + i + '" ' + (_groupSelected[i] !== false ? 'checked' : '') + ' onclick="event.stopPropagation()" onchange="onDeviceSelect(' + i + ', this.checked)"></div>' +
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
                    '<input type="range" min="0" max="100" value="' + (dev.volume || 0) + '" id="vslider-' + i + '" oninput="onVolumeInput(' + i + ', this.value)">' +
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
        grid.innerHTML = '<div class="list-view-shell"><div class="list-empty-state">No devices match the current filters.</div></div>';
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
            badge.className = 'runtime-badge';
            badge.textContent = status.runtime.toUpperCase();
            sysEl.appendChild(badge);
        }
        if (info.length) {
            sysEl.appendChild(document.createTextNode(' ' + info.join(' · ')));
        }
    }

    _showUpdateBadge(status.update_available);
    var resolvedMaWebUrl = status.ma_web_url || lastMaWebUrl || '';
    if (resolvedMaWebUrl) lastMaWebUrl = resolvedMaWebUrl;

    var userLink = document.getElementById('header-user-link');
    if (userLink) {
        var method = userLink.dataset.authMethod || '';
        if (status.ma_connected && resolvedMaWebUrl) {
            userLink.href = resolvedMaWebUrl + '/#/settings/profile';
        } else if (method === 'ha' || method === 'ha_via_ma') {
            if (resolvedMaWebUrl) {
                var u = new URL(resolvedMaWebUrl);
                userLink.href = u.protocol + '//' + u.hostname + ':8123/profile';
            } else {
                userLink.href = '/profile';
            }
        }
    }

    var devices = status.devices || (status.error ? [] : [status]);
    var grid = document.getElementById('status-grid');
    var emptyEl = document.getElementById('no-devices-hint');
    if (devices.length === 0) {
        if (grid) {
            grid.classList.remove('list-view');
            grid.innerHTML = '<div id="no-devices-hint" class="no-devices-hint">' + _buildEmptyStateHTML() + '</div>';
        } else if (emptyEl) {
            emptyEl.innerHTML = _buildEmptyStateHTML();
        }
        _updateGroupPanel();
        updateHealthIndicator([]);
        return;
    }
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
    updateHealthIndicator(sorted);
}

async function updateStatus() {
    try {
        var resp = await fetch(API_BASE + '/api/status');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        renderStatusPayload(await resp.json());
    } catch (err) {
        console.error('Status update failed:', err);
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
        var pos = Math.min(snap.pos + (now - snap.t), snap.dur);
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
          '<span class="card-badge meta-badge meta-badge-status is-neutral" id="dreleased-badge-' + i + '" style="display:none"></span>' +
          '<button type="button" class="card-badge group-badge meta-badge meta-badge-link group-badge-unified meta-badge-interactive" id="dgroup-' + i + '" style="display:none"></button>' +
        '</div>' +
        '<div class="card-chips">' +
          '<button type="button" class="chip meta-badge meta-badge-link meta-badge-interactive adapter-link-badge is-neutral" id="dchip-bt-' + i + '" title="Open Bluetooth adapter settings"></button>' +
          '<span class="chip meta-badge meta-badge-service service-chip-badge ma-service-badge is-neutral" id="dchip-ma-' + i + '">' +
            '<span class="meta-badge-indicator" id="dsrv-ind-' + i + '">' + _maIconSvg('meta-badge-indicator-icon') + '</span>' +
            ' <span class="ma-chip-tag" id="dma-api-' + i + '" style="display:none">API</span>' +
          '</span>' +
          _renderDeviceStatusBadgeHtml(null, 'chip', 'meta-badge-label', 'dplay-chip-' + i) +
          '<span class="chip meta-badge meta-badge-status sync-chip is-success" id="dsync-' + i + '" title="Synchronization healthy">' +
            '<span class="meta-badge-indicator" id="dsync-ind-' + i + '">' + _checkIconSvg('meta-badge-indicator-icon') + '</span><span class="meta-badge-label" id="dsync-text-' + i + '">Sync</span>' +
          '</span>' +
          '<span class="chip meta-badge meta-badge-status sync-detail-chip is-neutral" id="dsync-detail-' + i + '" style="display:none"></span>' +
          '<span class="chip meta-badge meta-badge-status battery-chip" id="dbattery-' + i + '" style="display:none"></span>' +
        '</div>' +
        '<div class="card-controls">' +
          _renderPlaybackTransportButtonsHtml(i, placeholderTransport, {
              buttonBaseClass: 'icon-btn',
              primaryButtonClass: 'icon-btn',
              modeButtonClass: 'icon-btn',
              renderPrevNextWhenInactive: true,
              renderModeButtonsWhenInactive: true,
              disableWhenInactive: true,
              modeFirst: false,
          }) +
          '<div class="vol-wrap">' +
            '<input type="range" min="0" max="100" value="100" id="vslider-' + i + '" oninput="onVolumeInput(' + i + ', this.value)">' +
            '<span class="vol-pct" id="dvol-' + i + '">100</span>' +
          '</div>' +
          '<button type="button" class="icon-btn" id="dmute-' + i + '" title="Mute/Unmute"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg></button>' +
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
            '<button type="button" class="action-btn warn" id="dbtn-release-' + i + '" onclick="btToggleManagement(' + i + ')">' + _actionButtonInnerHtml('release', 'Release') + '</button>' +
            '<button type="button" class="action-btn danger" id="dbtn-disable-' + i + '" onclick="confirmDisableDevice(' + i + ')">' + _actionButtonInnerHtml('disable', 'Disable') + '</button>' +
            '<button type="button" class="icon-btn device-settings-btn card-corner-settings-btn" onclick="openDeviceSettings(' + i + ')" title="Device settings">' + _settingsIconHtml() + '</button>' +
          '</div>' +
        '</div>';
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
        var releaseRenderData = _getReleaseBadgeRenderData(releaseMeta, 'card-badge');
        releasedBadge.style.display = releaseRenderData ? '' : 'none';
        if (releaseRenderData) {
            releasedBadge.innerHTML = releaseRenderData.innerHtml;
            releasedBadge.className = releaseRenderData.className;
            releasedBadge.title = releaseRenderData.title;
        }
    }

    var batteryEl = document.getElementById('dbattery-' + i);
    if (batteryEl) {
        var batteryMeta = _getBatteryBadgeMeta(dev.battery_level);
        if (batteryMeta.visible) {
            batteryEl.className = 'chip meta-badge meta-badge-status battery-chip ' + batteryMeta.toneClass;
            batteryEl.innerHTML = batteryMeta.html;
            batteryEl.title = batteryMeta.title;
            batteryEl.style.display = '';
        } else {
            batteryEl.className = 'chip meta-badge meta-badge-status battery-chip';
            batteryEl.innerHTML = '';
            batteryEl.title = '';
            batteryEl.style.display = 'none';
        }
    }

    var card = document.getElementById('device-card-' + i);
    if (card) {
        var isActive = dev.bluetooth_connected || dev.playing;
        card.classList.toggle('inactive', !isActive);
        card.classList.remove('is-success', 'is-warning', 'is-error', 'is-neutral');
        card.classList.toggle('playing', statusMeta.key === 'playing');
        card.classList.add(statusMeta.cardStateClass);
        var selCb = document.getElementById('dsel-' + i);
        if (selCb) {
            if (!isActive && selCb.checked) {
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
        var groupRenderData = _getGroupBadgeRenderData(dev, i, 'card-badge group-badge');
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
        btChipEl.disabled = adapterRenderData.disabled;
        btChipEl.title = adapterRenderData.title;
        btChipEl.onclick = adapterRenderData.disabled ? null : function() { openDeviceAdapterSettings(i); };
    }

    var srvInd = document.getElementById('dsrv-ind-' + i);
    var maChip = document.getElementById('dchip-ma-' + i);
    var maApiBadge = document.getElementById('dma-api-' + i);
    var serviceStateMeta = _getServiceBadgeStateMeta(dev);
    if (maChip) maChip.className = 'chip meta-badge meta-badge-service service-chip-badge ma-service-badge ' + serviceStateMeta.toneClass;
    if (srvInd) {
        srvInd.className = _getBadgeIndicatorClassName(serviceStateMeta, '', 'ma');
        srvInd.innerHTML = _getBadgeIndicatorInnerHtml('ma', serviceStateMeta);
    }
    if (maApiBadge) maApiBadge.style.display = (dev.ma_now_playing && dev.ma_now_playing.connected) ? '' : 'none';

    var playChip = document.getElementById('dplay-chip-' + i);
    var fmtEl = document.getElementById('daudiofmt-' + i);
    if (playChip) {
        var statusRenderData = _getStatusBadgeRenderData(dev, 'chip', 'meta-badge-label');
        playChip.className = statusRenderData.className;
        playChip.innerHTML = statusRenderData.innerHtml;
        playChip.title = statusRenderData.title;
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
        pauseBtn.disabled = !transportState.canTransport;
        pauseBtn.style.display = '';
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
    var syncInd = document.getElementById('dsync-ind-' + i);
    var syncTxt = document.getElementById('dsync-text-' + i);
    var syncDetail = document.getElementById('dsync-detail-' + i);
    if (syncEl) {
        var syncMeta = _getSyncStatusMeta(dev, i);
        syncEl.className = 'chip meta-badge meta-badge-status sync-chip ' + syncMeta.toneClass;
        syncEl.title = syncMeta.title || 'Synchronization status';
        if (syncTxt) syncTxt.textContent = syncMeta.text;
        if (syncInd) {
            syncInd.className = _getBadgeIndicatorClassName(syncMeta, '', syncMeta.indicatorKind || 'chain');
            syncInd.innerHTML = _getBadgeIndicatorInnerHtml(syncMeta.indicatorKind || 'chain', syncMeta);
        }
        syncEl.style.display = syncMeta.visible ? '' : 'none';
        if (syncDetail) {
            syncDetail.innerHTML = _getSyncDetailBadgeInnerHtml(syncMeta);
            syncDetail.className = 'chip meta-badge meta-badge-status sync-detail-chip ' + syncMeta.detailToneClass;
            syncDetail.title = syncMeta.detailTitle || 'Sync details';
            syncDetail.style.display = (syncMeta.visible && syncMeta.detailText) ? '' : 'none';
        }
    }

    var hasSink = deviceHasSink(dev);
    if (dev.volume !== undefined && !volPending[i]) {
        var slider = document.getElementById('vslider-' + i);
        var volEl = document.getElementById('dvol-' + i);
        if (slider) {
            slider.value = dev.volume;
            slider.disabled = !transportState.hasSink;
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
        muteBtn.disabled = !transportState.hasSink;
        muteBtn.style.opacity = hasSink ? '' : '0.35';
    }

    var vslider = document.getElementById('vslider-' + i);
    if (vslider) updateSliderFill(vslider);

    var relBtn = document.getElementById('dbtn-release-' + i);
    if (relBtn) {
        var mgmtEnabled = dev.bt_management_enabled !== false;
        _setReleaseActionButtonState(relBtn, mgmtEnabled);
        var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
        if (reconnBtn) reconnBtn.disabled = !mgmtEnabled;
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
            btn.className = 'icon-btn' + (muteVal ? ' muted' : '');
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
    } catch (err) {
        console.warn('MA queue cmd failed:', err);
        showToast('Music Assistant command failed: ' + (err && err.message ? err.message : 'Unknown error'), 'error');
    }
    finally { if (btnId) _unlockBtn(btnId); }
}

function maCycleRepeat(devIdx) {
    var ma = (devIdx != null && lastDevices && lastDevices[devIdx] && lastDevices[devIdx].ma_now_playing) || {};
    var rm = ma.repeat || 'off';
    var next = rm === 'off' ? 'all' : rm === 'all' ? 'one' : 'off';
    maQueueCmd('repeat', next, devIdx);
}

async function btReconnect(i) {
    var dev = lastDevices && lastDevices[i];
    var playerName = dev ? dev.player_name : null;
    var btn = document.getElementById('dbtn-reconnect-' + i);
    var pairBtn = document.getElementById('dbtn-pair-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
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
        if (status) status.textContent = msg;
        showToast(msg, d.success ? 'success' : 'error');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
        showToast('\u2717 Reconnect error', 'error');
    }
    setTimeout(function() {
        if (btn) btn.disabled = false;
        if (pairBtn) pairBtn.disabled = false;
        if (status) status.textContent = '';
    }, 8000);
}

async function btToggleManagement(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
    var playerName = dev.player_name || null;
    var newEnabled = dev.bt_management_enabled === false;  // toggle
    var btn = document.getElementById('dbtn-release-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
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
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    if (btn) btn.disabled = false;
    setTimeout(function() { if (status) status.textContent = ''; }, 4000);
}

// ---- Device enabled toggle (used by config checkbox and dashboard Disable button) ----

async function toggleDeviceEnabled(playerName, enabled) {
    try {
        var resp = await fetch(API_BASE + '/api/device/enabled', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName, enabled: enabled})
        });
        var d = await resp.json();
        if (d.success) {
            showToast(d.message, 'success');
        } else {
            showToast(d.error || 'Failed', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function confirmDisableDevice(i) {
    var dev = lastDevices && lastDevices[i];
    if (!dev) return;
    var name = dev.player_name || 'Device ' + (i + 1);
    if (!confirm('Disable "' + name + '"?\n\nThe device will be skipped on next bridge restart.\nYou can re-enable it from the config page.')) return;
    var btn = document.getElementById('dbtn-disable-' + i);
    if (btn) btn.disabled = true;
    toggleDeviceEnabled(name, false);
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

async function loadBtAdapters() {
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
}

// ---- Adapter panel ----

function escHtmlAttr(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

function renderAdaptersTable() {
    var el = document.getElementById('adapters-table');
    if (!el) return;
    el.innerHTML = '';
    btAdapters.forEach(function(a) {
        if (a.manual) {
            el.appendChild(buildManualRow(a.id, a.mac, a.name));
        } else {
            var row = document.createElement('div');
            row.className = 'adapter-row detected';
            row.dataset.adapterId = a.id || '';
            row.dataset.adapterMac = a.mac || '';
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
                '</span>';
            row.querySelector('.adp-name').addEventListener('blur', syncManualAdapters);
            row.querySelector('.btn-adp-reboot').addEventListener('click', function() { rebootAdapter(a.mac); });
            el.appendChild(row);
        }
    });
}

function buildManualRow(id, mac, name) {
    var row = document.createElement('div');
    row.className = 'adapter-row manual';
    row.dataset.adapterId = id || '';
    row.dataset.adapterMac = mac || '';
    row.innerHTML =
        '<input type="text" class="adp-id" placeholder="hci2" value="' + escHtmlAttr(id) + '">' +
        '<input type="text" class="adp-mac mono" placeholder="AA:BB:CC:DD:EE:FF" value="' + escHtmlAttr(mac) + '">' +
        '<input type="text" class="adp-name" placeholder="Display name" value="' + escHtmlAttr(name) + '">' +
        '<span class="dot grey">\u25cf</span>' +
        '<button type="button" class="btn-remove-adapter">\u00d7</button>';
    ['adp-id', 'adp-mac', 'adp-name'].forEach(function(cls) {
        row.querySelector('.' + cls).addEventListener('blur', syncManualAdapters);
    });
    row.querySelector('.btn-remove-adapter').addEventListener('click', function() {
        row.remove();
        syncManualAdapters();
    });
    return row;
}

function addManualAdapterRow(id, mac, name) {
    var el = document.getElementById('adapters-table');
    if (!el) return;
    el.appendChild(buildManualRow(id || '', mac || '', name || ''));
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

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort, enabled, preferredFormat, keepaliveInterval) {
    var tbody = document.getElementById('bt-devices-table');
    var wrap = document.createElement('div');
    wrap.className = 'bt-device-wrap';
    var isEnabled = enabled !== false;

    var row = document.createElement('div');
    row.className = 'bt-device-row';
    var delayVal = (delay !== undefined && delay !== null && delay !== '') ? delay : 0;
    var portVal  = (listenPort !== undefined && listenPort !== null && listenPort !== '') ? listenPort : '';
    var fmtVal   = (preferredFormat !== undefined && preferredFormat !== null) ? preferredFormat : 'flac:44100:16:2';
    var kaVal = (keepaliveInterval !== undefined && keepaliveInterval !== null && keepaliveInterval !== '') ? parseInt(keepaliveInterval, 10) : 0;
    if (kaVal > 0 && kaVal < 30) kaVal = 30;
    row.innerHTML =
        '<div class="bt-enabled-cell"><label class="bt-switch" title="Enable or disable device">' +
            '<input type="checkbox" class="bt-enabled"' + (isEnabled ? ' checked' : '') + '>' +
            '<span class="bt-switch-track"></span>' +
        '</label></div>' +
        '<div class="bt-name-field">' +
            '<button type="button" class="bt-expand-btn" title="Show advanced settings" aria-label="Show advanced settings" aria-expanded="false">' +
                '<span class="bt-expand-btn-label">Details</span>' +
                '<span class="bt-expand-btn-icon" aria-hidden="true">▾</span>' +
            '</button>' +
            '<input type="text" placeholder="Player Name" class="bt-name" value="' +
                escHtmlAttr(name || '') + '">' +
        '</div>' +
        '<input type="text" placeholder="AA:BB:CC:DD:EE:FF" class="bt-mac" value="' +
            escHtmlAttr(mac || '') + '">' +
        '<select class="bt-adapter">' + btAdapterOptions(adapter || '') + '</select>' +
        '<input type="number" class="bt-listen-port" placeholder="8928" min="1024" max="65535" value="' +
            escHtmlAttr(String(portVal)) + '">' +
        '<input type="number" class="bt-delay" title="Static delay. Negative = compensate latency" placeholder="0" value="' +
            escHtmlAttr(String(delayVal)) + '" step="50">' +
        '<div class="bt-runtime" aria-live="polite"></div>' +
        '<button type="button" class="btn-remove-dev" title="Remove device" aria-label="Remove device">' +
            _trashIconSvg() +
        '</button>';

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
            escHtmlAttr(String(kaVal)) + '"></div>';

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
        _setConfigDirty(true);
    });
    enabledCb.addEventListener('change', function() {
        syncBtRowState();
        _setConfigDirty(true);
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
    _setConfigDirty(true);
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
        if (isNaN(kaVal) || kaVal < 0) kaVal = 0;
        if (kaVal > 0 && kaVal < 30) kaVal = 30;
        var dev = { mac: mac, adapter: adapter, player_name: name, static_delay_ms: delay, preferred_format: preferredFormat || 'flac:44100:16:2' };
        if (listenHost) dev.listen_host = listenHost;
        if (listenPort) dev.listen_port = listenPort;
        dev.keepalive_interval = kaVal;
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
        if (!runtime) {
            runtimeEl.innerHTML =
                '<span class="bt-live-badge muted">Not seen</span>';
            return;
        }
        runtimeEl.innerHTML =
            '<span class="bt-live-badge ' + getDeviceStatusClass(runtime) + '">' + escHtml(getDeviceStatusLabel(runtime)) + '</span>';
    });
}

function populateBtDeviceRows(devices) {
    document.getElementById('bt-devices-table').innerHTML = '';
    devices.forEach(function(d) {
        addBtDeviceRow(d.player_name || '', d.mac || '', d.adapter || '',
                       d.static_delay_ms, d.listen_host, d.listen_port, d.enabled,
                       d.preferred_format, d.keepalive_interval);
    });
    refreshBtDeviceRowsRuntime();
}

function _hasDetectedAdapter() {
    return btAdapters.some(function(a) { return !a.manual; });
}

function _buildEmptyStateHTML() {
    if (!_hasDetectedAdapter()) {
        return '<div class="no-devices-icon">' + _uiIconSvg('plug', 'ui-icon-svg') + '</div>' +
            '<div class="no-devices-text">No Bluetooth adapter detected</div>' +
            '<a href="#" class="no-devices-link" onclick="_goToAdapters(); return false;">' +
                _uiIconSvg('plus', 'no-devices-link-icon') + '<span>Add adapter</span>' +
            '</a>';
    }
    return '<div class="no-devices-icon">' + _uiIconSvg('bt', 'ui-icon-svg') + '</div>' +
        '<div class="no-devices-text">No Bluetooth devices configured</div>' +
        '<a href="#" class="no-devices-link" onclick="_goToDevicesAndScan(); return false;">' +
            _uiIconSvg('search', 'no-devices-link-icon') + '<span>Scan for devices</span>' +
        '</a>';
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

function _goToDevicesAndScan() {
    if (!_hasDetectedAdapter()) {
        _goToAdapters();
        return;
    }
    _openConfigPanel('devices', 'config-devices-discovery-card', 'start');
    setTimeout(function() {
        var scanBtn = document.getElementById('scan-btn');
        if (scanBtn) scanBtn.focus({preventScroll: true});
        startBtScan();
    }, 180);
}

function _refreshEmptyState() {
    var el = document.getElementById('no-devices-hint');
    if (el) el.innerHTML = _buildEmptyStateHTML();
}

function openConfigAndAddDevice() {
    if (!_hasDetectedAdapter()) {
        _goToAdapters();
    } else {
        _goToDevicesAndScan();
    }
}

// ---- BT Scan ----

async function startBtScan() {
    var btn     = document.getElementById('scan-btn');
    var status  = document.getElementById('scan-status');
    var box     = document.getElementById('scan-results-box');
    var listDiv = document.getElementById('scan-results-list');

    btn.disabled = true;
    status.innerHTML = '<span class="scan-spinner"></span> Scanning\u2026 (~15s)';
    box.hidden = true;

    try {
        var resp = await fetch(API_BASE + '/api/bt/scan', { method: 'POST' });
        var data = await resp.json();

        if (resp.status === 429 && data.retry_after) {
            _startScanCooldown(btn, data.retry_after);
            status.textContent = '';
            return;
        }

        var jobId = data.job_id;
        if (!jobId) { throw new Error(data.error || 'No job_id returned'); }

        // Poll for result every 2 s
        var devices = null;
        for (var attempt = 0; attempt < 30; attempt++) {
            await new Promise(function(resolve) { setTimeout(resolve, 2000); });
            var pollResp = await fetch(API_BASE + '/api/bt/scan/result/' + jobId);
            var pollData = await pollResp.json();
            if (pollData.status === 'done') {
                if (pollData.error) { throw new Error(pollData.error); }
                devices = pollData.devices || [];
                break;
            }
        }
        if (devices === null) { throw new Error('Scan timed out'); }

        if (devices.length === 0) {
            status.innerHTML = '<strong>No devices found.</strong>' +
                '<div style="margin-top:6px;font-size:12px;color:var(--text-secondary,#888);line-height:1.5">' +
                '\u2022 Make sure your speaker is in <b>pairing mode</b> (usually hold the Bluetooth button for 3\u20135 s)<br>' +
                '\u2022 Move the device closer to the Bluetooth adapter<br>' +
                '\u2022 Some devices need to be <b>unpaired</b> from other sources (phone, laptop) first<br>' +
                '\u2022 Try scanning again \u2014 some speakers advertise intermittently</div>';
        } else {
            status.textContent = 'Found ' + devices.length + ' device(s)';
            listDiv.innerHTML = devices.map(function(d, i) {
                return '<div class="scan-result-item" data-scan-idx="' + i + '">' +
                    '<span class="scan-result-actions">' +
                    '<button type="button" class="scan-action-btn scan-action-btn--primary scan-add-btn" title="Add to config without pairing now">Add</button>' +
                    '<button type="button" class="scan-action-btn scan-action-btn--pair scan-pair-btn" data-pair-idx="' + i + '" title="Pair, trust, and add to config">Add & Pair</button>' +
                    '</span>' +
                    '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                    '<span class="scan-result-name">' + escHtml(d.name) + '</span>' +
                    '</div>';
            }).join('');
            // "Add" button
            listDiv.querySelectorAll('[data-scan-idx]').forEach(function(row) {
                row.querySelector('.scan-add-btn').addEventListener('click', function(e) {
                    e.stopPropagation();
                    var d = devices[parseInt(row.dataset.scanIdx)];
                    addFromScan(d.mac, d.name, d.adapter);
                });
            });
            // "Add & Pair" button
            listDiv.querySelectorAll('.scan-pair-btn').forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    var d = devices[parseInt(this.dataset.pairIdx)];
                    pairAndAdd(d.mac, d.name, d.adapter, this);
                });
            });
            box.hidden = false;
        }
        _startScanCooldown(btn, 30);
    } catch (err) {
        status.textContent = 'Scan failed: ' + err.message;
        btn.disabled = false;
    }
}

var _scanCooldownTimer = null;
function _startScanCooldown(btn, seconds) {
    if (_scanCooldownTimer) clearInterval(_scanCooldownTimer);
    var remaining = seconds;
    btn.disabled = true;
    btn.innerHTML = _buttonLabelWithIconHtml('search', 'Scan (' + remaining + 's)');
    _scanCooldownTimer = setInterval(function() {
        remaining--;
        if (remaining <= 0) {
            clearInterval(_scanCooldownTimer);
            _scanCooldownTimer = null;
            btn.disabled = false;
            btn.innerHTML = _buttonLabelWithIconHtml('search', 'Scan');
        } else {
            btn.innerHTML = _buttonLabelWithIconHtml('search', 'Scan (' + remaining + 's)');
        }
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
        var resp = await fetch(API_BASE + '/api/bt/pair_new', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({mac: mac, adapter: adapter || autoAdapter()})
        });
        var data = await resp.json();
        if (!data.job_id) throw new Error(data.error || 'No job_id');

        // Poll for result
        var result = null;
        for (var i = 0; i < 30; i++) {
            await new Promise(function(r) { setTimeout(r, 2000); });
            var pr = await fetch(API_BASE + '/api/bt/pair_new/result/' + data.job_id);
            var pd = await pr.json();
            if (pd.status === 'done') { result = pd; break; }
        }
        if (!result) throw new Error('Pairing timed out');
        if (result.error) throw new Error(result.error);
        if (result.success) {
            _setScanActionState(btnEl, 'success', '\u2713 Paired');
            addFromScan(mac, name, adapter);
        } else {
            _setScanActionState(btnEl, 'error', '\u2717 Failed');
            setTimeout(function() { _setScanActionState(btnEl, '', 'Add & Pair'); }, 3000);
        }
    } catch (err) {
        _setScanActionState(btnEl, 'error', 'Error');
        setTimeout(function() { _setScanActionState(btnEl, '', 'Add & Pair'); }, 3000);
        alert('Pair failed: ' + err.message);
    }
}

function autoAdapter() {
    return (btAdapters.length === 1) ? btAdapters[0].id : '';
}

function addFromScan(mac, name, adapter) {
    addBtDeviceRow(name, mac, adapter || autoAdapter());
    document.getElementById('scan-results-box').hidden = true;
    document.getElementById('scan-status').textContent = '';
}

function addFromPaired(mac, name) {
    addBtDeviceRow(name, mac, autoAdapter());
    document.getElementById('paired-box').hidden = true;
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

async function loadPairedDevices() {
    try {
        var showAll = document.getElementById('paired-show-all');
        var showAllChecked = showAll && showAll.checked;
        var qs = showAllChecked ? '?filter=0' : '';
        var resp = await fetch(API_BASE + '/api/bt/paired' + qs);
        var data = await resp.json();
        var devices = data.devices || [];
        var allCount = data.total_count || devices.length;
        var box = document.getElementById('paired-box');
        var listDiv = document.getElementById('paired-list');
        if (devices.length === 0 && !showAllChecked) { box.hidden = true; return; }
        box.hidden = false;

        // Update title with count hint
        var titleEl = box.querySelector('.paired-box-copy');
        if (titleEl) {
            var countHint = '';
            if (!showAllChecked && allCount > devices.length) {
                countHint = ' (' + devices.length + ' audio · ' + allCount + ' total)';
            } else {
                countHint = ' (' + devices.length + ')';
            }
            titleEl.textContent = 'Already paired \u2014 click to add:' + countHint;
        }

        // Auto-collapse list when more than 5 devices, except in demo mode where
        // the screenshot stand keeps the import drawer open by default.
        var arrow = box.querySelector('.paired-arrow');
        if (_runtimeMode === 'demo') {
            listDiv.hidden = false;
            if (arrow) arrow.classList.add('expanded');
        } else if (devices.length > 5) {
            listDiv.hidden = true;
            if (arrow) arrow.classList.remove('expanded');
        } else {
            listDiv.hidden = false;
            if (arrow) arrow.classList.add('expanded');
        }

        listDiv.innerHTML = devices.map(function(d, idx) {
            // Replace raw RSSI-only strings with a friendlier label
            var displayName = /^RSSI:/i.test(d.name) ? 'Unknown device' : d.name;
            var btInfoIcon = _bluetoothIconSvg('scan-action-icon');
            return '<div class="scan-result-item" data-paired-idx="' + idx + '">' +
                '<span class="scan-result-actions">' +
                '<button type="button" class="scan-action-btn scan-action-btn--primary paired-add-btn">Add</button>' +
                '</span>' +
                '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                '<span class="scan-result-name">' + escHtml(displayName) + '</span>' +
                '<span class="paired-actions" onclick="event.stopPropagation()">' +
                '<button type="button" class="scan-action-btn paired-info-btn" title="Show Bluetooth device info">' + btInfoIcon + '<span>Info</span></button>' +
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
        });
        _applyDemoScreenshotDefaults();
    } catch (_) {}
}

function togglePairedList(node) {
    var container = node && node.closest ? node.closest('.paired-box') : null;
    if (!container) return;
    var list = container.querySelector('#paired-list');
    var arrow = container.querySelector('.paired-arrow');
    if (!list) return;
    list.hidden = !list.hidden;
    if (arrow) arrow.classList.toggle('expanded', !list.hidden);
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

async function saveConfig() {
    var formData = new FormData(document.getElementById('config-form'));
    var config = Object.fromEntries(formData);

    // Collect BT devices from table rows (overrides anything from formData)
    config.BLUETOOTH_DEVICES = collectBtDevices();
    syncManualAdapters();
    // Checkbox → bool (FormData only includes it when checked, with value "on")
    config.PREFER_SBC_CODEC = !!(document.getElementById('prefer-sbc-codec') || {}).checked;
    config.AUTH_ENABLED = !!(document.getElementById('auth-enabled') || {}).checked;
    config.BRUTE_FORCE_PROTECTION = !!(document.getElementById('brute-force-protection') || {}).checked;
    config.MA_WEBSOCKET_MONITOR = !!(document.getElementById('ma-websocket-monitor') || {}).checked;
    config.VOLUME_VIA_MA = !!(document.getElementById('volume-via-ma') || {}).checked;
    config.MUTE_VIA_MA = !!(document.getElementById('mute-via-ma') || {}).checked;
    config.SMOOTH_RESTART = !!(document.getElementById('smooth-restart') || {}).checked;
    config.UPDATE_CHANNEL = (((document.getElementById('update-channel') || {}).value) || 'stable').toLowerCase();
    config.AUTO_UPDATE = !!(document.getElementById('auto-update') || {}).checked;
    config.CHECK_UPDATES = !!(document.getElementById('check-updates') || {}).checked;
    // Log level lives outside the config form (in Logs section)
    var logSel = document.getElementById('log-level-select');
    if (logSel) config.LOG_LEVEL = logSel.value;
    function readOptionalNumberField(name) {
        var input = document.querySelector('[name="' + name + '"]');
        if (!input) return null;
        var raw = (input.value || '').trim();
        if (!raw) return null;
        var value = parseInt(raw, 10);
        return Number.isFinite(value) ? value : raw;
    }
    // Cast numeric BT settings to integers
    config.BT_CHECK_INTERVAL = parseInt(config.BT_CHECK_INTERVAL, 10) || 10;
    config.BT_MAX_RECONNECT_FAILS = parseInt(config.BT_MAX_RECONNECT_FAILS, 10) || 0;
    config.WEB_PORT = readOptionalNumberField('WEB_PORT');
    config.BASE_LISTEN_PORT = readOptionalNumberField('BASE_LISTEN_PORT');
    config.SESSION_TIMEOUT_HOURS = parseInt(((document.querySelector('[name="SESSION_TIMEOUT_HOURS"]') || {}).value), 10) || 24;
    config.BRUTE_FORCE_MAX_ATTEMPTS = parseInt(((document.querySelector('[name="BRUTE_FORCE_MAX_ATTEMPTS"]') || {}).value), 10) || 5;
    config.BRUTE_FORCE_WINDOW_MINUTES = parseInt(((document.querySelector('[name="BRUTE_FORCE_WINDOW_MINUTES"]') || {}).value), 10) || 1;
    config.BRUTE_FORCE_LOCKOUT_MINUTES = parseInt(((document.querySelector('[name="BRUTE_FORCE_LOCKOUT_MINUTES"]') || {}).value), 10) || 5;
    // Pass current group slider value so backend can init volume for new devices
    var groupSlider = document.getElementById('group-vol-slider');
    config._new_device_default_volume = groupSlider ? parseInt(groupSlider.value, 10) : 100;
    // Save all adapters (auto-detected + manual) so native HA Config tab shows them
    config.BLUETOOTH_ADAPTERS = btManualAdapters
        .filter(function(a) { return a.id; })
        .map(function(a) {
            var entry = {id: a.id, mac: a.mac || ''};
            if (a.name) entry.name = a.name;
            return entry;
        });

    // Require password before enabling auth (HA addon uses HA login instead)
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
        return { ok: true };
    } catch (err) {
        console.error('Save config error:', err);
        return { ok: false, error: 'Network error: ' + err.message };
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
        if (data.success && data.servers && data.servers.length > 0) {
            var s = data.servers[0];
            if (urlInput) urlInput.value = s.url;
            _setStatusText(msgEl, '\u2714 Found: MA v' + (s.version || '?') + ' at ' + s.url, 'success');
            // Detect HA addon mode — check both bridge flag and MA server flag
            _setMaAddonMode(!!(data.is_addon || s.homeassistant_addon));
        } else {
            _setStatusText(msgEl, '\u2716 No MA server found on network', 'error');
        }
    } catch (err) {
        _setStatusText(msgEl, '\u2716 Discovery error: ' + err.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function _setMaAddonMode(isAddon) {
    var creds = document.getElementById('ma-login-creds');
    var hint = document.getElementById('ma-addon-hint');
    var loginBtn = document.getElementById('ma-login-btn');
    if (isAddon) {
        if (creds) creds.hidden = true;
        if (hint) hint.hidden = false;
        if (loginBtn) loginBtn.hidden = true;
    } else {
        if (creds) creds.hidden = false;
        if (hint) hint.hidden = true;
        if (loginBtn) loginBtn.hidden = false;
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
            loadConfig().then(function() { _setConfigDirty(true); });
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
            await loadConfig();
            _setConfigDirty(true);
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
    if (connected) {
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
    var form = document.getElementById('ma-conn-form');
    var reconf = document.getElementById('ma-reconfigure');
    var apiFields = document.getElementById('ma-api-fields');
    if (form) form.hidden = connected;
    if (reconf) reconf.hidden = !connected;
    if (apiFields) apiFields.hidden = true;
}

function toggleMaForm(show) {
    var form = document.getElementById('ma-conn-form');
    var apiFields = document.getElementById('ma-api-fields');
    if (form) form.hidden = !show;
    if (apiFields) apiFields.hidden = !show;
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
            await loadConfig();
            _setConfigDirty(true);
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
                await loadConfig();
                _setConfigDirty(true);
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
            _setMaStatus(true, data.username || '', data.url || maUrl);
            showToast('\u2714 Connected to Music Assistant', 'success');
            await loadConfig();
            _setConfigDirty(true);
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
    // Auto-discover MA server and detect addon mode for UI
    await maDiscover();
    // No auto silent auth — user clicks "Sign in with HA" button explicitly
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
            _setConfigDirty(false);
            showToast('\u2713 Configuration saved \u2014 restart to apply', 'success');
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
function _syncConfigFooterActions() {
    var cancelBtn = document.getElementById('config-cancel-btn');
    if (cancelBtn) cancelBtn.disabled = !_configDirty || _configLoading;
}
function _setConfigDirty(dirty) {
    if (_configLoading) return;
    _configDirty = dirty;
    var summary = document.querySelector('.config-section summary');
    if (summary) {
        var dot = summary.querySelector('.config-dirty-dot');
        if (dirty) {
            if (!dot) {
                dot = document.createElement('span');
                dot.className = 'config-dirty-dot';
                dot.title = 'Unsaved changes';
                summary.appendChild(dot);
            }
        } else {
            if (dot) dot.remove();
        }
    }
    var footer = document.getElementById('config-footer');
    if (footer) footer.classList.toggle('is-dirty', dirty);
    _syncConfigFooterActions();
}
// Watch config form for any change
document.getElementById('config-form').addEventListener('input', function() { _setConfigDirty(true); });
document.getElementById('config-form').addEventListener('change', function() { _setConfigDirty(true); });
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
        _setConfigDirty(false);
        showToast('Unsaved changes discarded', 'info');
    } catch (err) {
        console.error('Cancel config changes error:', err);
        showToast('Failed to restore saved configuration: ' + err.message, 'error');
    } finally {
        actionBtns.forEach(function(btn) { btn.disabled = false; });
        _syncConfigFooterActions();
    }
}

async function loadConfig() {
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
         'BRUTE_FORCE_LOCKOUT_MINUTES'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key] == null ? '' : config[key];
        });
        // Populate checkboxes
        var sbcCheck = document.getElementById('prefer-sbc-codec');
        if (sbcCheck) sbcCheck.checked = !!config.PREFER_SBC_CODEC;
        var authCheck = document.getElementById('auth-enabled');
        if (authCheck) authCheck.checked = !!config.AUTH_ENABLED;
        var authPw = document.getElementById('auth-password-fields');
        if (authPw && authCheck) authPw.hidden = !authCheck.checked;
        window._passwordSet = !!config._password_set;
        _updateAuthMethodsHint();
        var bruteForceCheck = document.getElementById('brute-force-protection');
        if (bruteForceCheck) bruteForceCheck.checked = config.BRUTE_FORCE_PROTECTION !== false;
        _syncSecurityPolicyState();
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
        var autoUpdateCheck = document.getElementById('auto-update');
        if (autoUpdateCheck) autoUpdateCheck.checked = !!config.AUTO_UPDATE;
        var checkUpdatesCheck = document.getElementById('check-updates');
        if (checkUpdatesCheck) checkUpdatesCheck.checked = config.CHECK_UPDATES !== false;
        _syncUpdateChannelState();
        var logLevelSel = document.getElementById('log-level-select');
        if (logLevelSel && config.LOG_LEVEL) logLevelSel.value = config.LOG_LEVEL.toUpperCase();
        _restoreConfigTransientInputs(config);
        updateTzPreview();

        // Restore manual adapters before re-running loadBtAdapters so merging picks them up
        btManualAdapters = config.BLUETOOTH_ADAPTERS || [];
        await loadBtAdapters();
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
        _setConfigDirty(false);
        return config;
    } catch (err) {
        _configLoading = false;
        console.error('Error loading config:', err);
        _syncConfigFooterActions();
        return false;
    }
}

// ---- Restart ----

function _restartDeviceStats(statusData) {
    var devs = statusData.devices || [statusData];
    var total = devs.length;
    var bt = 0, pa = 0, ss = 0;
    var perDevice = [];
    for (var i = 0; i < devs.length; i++) {
        var d = devs[i];
        var dBt = !!d.bluetooth_connected;
        var dPa = deviceHasSink(d);
        var dSs = !!d.server_connected;
        if (dBt) bt++;
        if (dPa) pa++;
        if (dSs) ss++;
        perDevice.push({
            name: d.player_name || d.bluetooth_mac || ('Device ' + (i + 1)),
            bt: dBt, pa: dPa, ss: dSs
        });
    }
    return { total: total, bt: bt, pa: pa, ss: ss, ma: !!statusData.ma_connected, perDevice: perDevice };
}

function _restartProgressHtml(step, totalSteps, message, elapsed) {
    var pct = Math.min(100, Math.round((step / totalSteps) * 100));
    var done = step >= totalSteps;
    var failed = message.indexOf('\u26a0') >= 0 || message.indexOf('\u2717') >= 0;
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
    var banner = document.getElementById('restart-banner');
    var smooth = !!(document.getElementById('smooth-restart') || {}).checked;
    var totalSteps = smooth ? 6 : 5;

    banner.className = 'restart-banner active';
    banner.innerHTML = _restartProgressHtml(0, totalSteps, 'Saving configuration…', 0);

    try {
        var saved = await saveConfig();
        if (!saved || !saved.ok) {
            banner.innerHTML = _restartProgressHtml(0, totalSteps, '\u2717 ' + (saved && saved.error || 'Failed to save configuration'), 0);
            setTimeout(function() { banner.className = 'restart-banner'; }, 3000);
            return;
        }
        _setConfigDirty(false);

        var step = 1;

        if (smooth) {
            // Mute local PA sinks (not MA pause) to avoid audio glitches on shutdown.
            // This only silences THIS bridge's speakers — other players in a sync group keep playing.
            // After restart, _startup_unmute_watcher unmutes each sink once audio stabilizes.
            banner.innerHTML = _restartProgressHtml(step, totalSteps, 'Muting speakers…', 0);
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
            step++;
        }

        banner.innerHTML = _restartProgressHtml(step, totalSteps, 'Stopping service…', 0);
        try {
            await fetch(API_BASE + '/api/restart', { method: 'POST' });
        } catch (_) { /* Service dropped connection — expected */ }

        await new Promise(function(r) { setTimeout(r, 2000); });
        step++;

        // Wait for service to come back
        var serviceUp = false;
        var statusData = null;
        for (var attempt = 1; attempt <= 40; attempt++) {
            banner.innerHTML = _restartProgressHtml(step, totalSteps, 'Starting service…', attempt);
            await new Promise(function(r) { setTimeout(r, 1000); });
            try {
                var resp = await fetch(API_BASE + '/api/status');
                if (resp.ok) {
                    statusData = await resp.json();
                    serviceUp = true;
                    break;
                }
            } catch (_) {}
        }

        if (!serviceUp) {
            banner.innerHTML = _restartProgressHtml(step, totalSteps, '\u26a0\ufe0f Service did not respond within 40s', 0);
            return;
        }
        step++;

        // Wait for devices to initialize
        var stats = _restartDeviceStats(statusData);
        var allReady = stats.total === 0 || (stats.bt >= stats.total && stats.pa >= stats.total &&
                       stats.ss >= stats.total);
        if (!allReady) {
            for (var w = 1; w <= 30; w++) {
                var readyCount = Math.min(stats.bt, stats.pa, stats.ss);
                var msg = 'Connecting devices… ' + readyCount + '/' + stats.total;
                banner.innerHTML = _restartProgressHtml(step, totalSteps, msg, w);
                await new Promise(function(r) { setTimeout(r, 1000); });
                try {
                    var r2 = await fetch(API_BASE + '/api/status');
                    if (r2.ok) {
                        statusData = await r2.json();
                        stats = _restartDeviceStats(statusData);
                        allReady = stats.bt >= stats.total && stats.pa >= stats.total &&
                                   stats.ss >= stats.total;
                        if (allReady) break;
                    }
                } catch (_) {}
            }
        }
        step++;

        // Wait for MA connection
        stats = _restartDeviceStats(statusData);
        if (!stats.ma && stats.total > 0) {
            banner.innerHTML = _restartProgressHtml(step, totalSteps, 'Connecting to Music Assistant…', 0);
            for (var m = 1; m <= 15; m++) {
                await new Promise(function(r) { setTimeout(r, 1000); });
                try {
                    var r3 = await fetch(API_BASE + '/api/status');
                    if (r3.ok) {
                        statusData = await r3.json();
                        stats = _restartDeviceStats(statusData);
                        if (stats.ma) break;
                    }
                } catch (_) {}
                banner.innerHTML = _restartProgressHtml(step, totalSteps, 'Connecting to Music Assistant…', m);
            }
        }

        // Final
        stats = _restartDeviceStats(statusData);
        allReady = (stats.total === 0 || (stats.bt >= stats.total && stats.pa >= stats.total &&
                   stats.ss >= stats.total)) && stats.ma;

        banner.innerHTML = _restartProgressHtml(totalSteps, totalSteps,
            allReady ? 'Restart complete — all systems operational' : '\u26a0\ufe0f Restart complete — some connections pending', 0);
        setTimeout(function() { banner.className = 'restart-banner'; }, allReady ? 4000 : 10000);
        updateStatus();

    } catch (err) {
        banner.innerHTML = _restartProgressHtml(0, totalSteps, '\u26a0\ufe0f Error: ' + err.message, 0);
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

async function loadVersionInfo() {
    try {
        var resp = await fetch(API_BASE + '/api/version');
        var data = await resp.json();
        var el = document.getElementById('version-display');
        if (!el) return;
        var ver = data.version || el.textContent;
        var title = data.built_at || '';
        if (data.git_sha && data.git_sha !== 'unknown') title += ' · ' + data.git_sha;
        el.textContent = 'v' + ver;
        if (title) el.title = title;
        _applyReleaseChannelTextTone(el, _releaseChannelFromVersion(ver));
    } catch (_) { /* Keep static Jinja2-rendered values */ }
}

function _releaseChannelFromVersion(version) {
    var normalized = String(version || '').toLowerCase();
    if (normalized.indexOf('-beta') !== -1) return 'beta';
    if (normalized.indexOf('-rc') !== -1) return 'rc';
    return 'stable';
}

function _applyReleaseChannelTextTone(el, channel) {
    if (!el) return;
    el.classList.remove('channel-rc', 'channel-beta');
    if (channel === 'rc' || channel === 'beta') {
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
        _applyReleaseChannelTextTone(ver, channel);
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
        _applyReleaseChannelTextTone(ver, 'stable');
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
    fetch(API_BASE + '/api/update/check', {method: 'POST'})
        .then(function(r) { return r.json(); })
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

            // Release notes body
            if (info.body) {
                var bodyEl = document.createElement('div');
                bodyEl.className = 'update-modal-body';
                var plain = info.body
                    .replace(/^## .+\n+/, '')
                    .replace(/^### .+$/gm, '')
                    .replace(/\*\*(.+?)\*\*/g, '$1')
                    .replace(/^- /gm, '\u2022 ')
                    .replace(/\n{3,}/g, '\n\n')
                    .trim();
                bodyEl.textContent = plain;
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
                fetch(API_BASE + '/api/update/check', {method: 'POST'})
                    .then(function(r) { return r.json(); })
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
                var instrBtn = document.createElement('button');
                instrBtn.className = 'update-modal-btn primary';
                instrBtn.innerHTML = _UPD_ICON_NOTES + ' Instructions';
                instrBtn.onclick = function() {
                    overlay.remove();
                    showToast(info.instructions || 'Follow the selected update channel instructions.', 'info');
                };
                footer.appendChild(instrBtn);
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
    fetch(API_BASE + '/api/update/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({version: ver, channel: channel || (link && link.dataset.updateChannel) || 'stable'})
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                showToast(data.already_running ? 'Update already in progress…' : 'Update started! Restarting…', 'info');
                setTimeout(function() { location.reload(); }, 8000);
            } else {
                showToast('Update failed: ' + (data.error || 'unknown error'), 'error');
                _showUpdateBadge({version: ver, url: releaseUrl, channel: channel || (link && link.dataset.updateChannel) || 'stable'});
            }
        })
        .catch(function() {
            showToast('Update started…', 'info');
            setTimeout(function() { location.reload(); }, 8000);
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
        contentEl.innerHTML = renderDiagnostics(data);
        contentEl.dataset.loaded = '1';
    } catch (err) {
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
            title: 'Bridge devices',
            value: connectedDevices.length + '/' + activeDevices.length,
            tone: connectedDevices.length === activeDevices.length && activeDevices.length
                ? (degradedDevices.length ? 'warn' : 'ok')
                : (connectedDevices.length ? 'warn' : 'error'),
            hint: playingDevices.length + ' playing now' +
                (degradedDevices.length ? ' · ' + degradedDevices.length + ' issue' + (degradedDevices.length === 1 ? '' : 's') : ''),
        },
        {
            title: 'Audio routing',
            value: routedDevices.length + '/' + activeDevices.length,
            tone: routedDevices.length === activeDevices.length && activeDevices.length
                ? (sinkIssueDevices.length ? 'warn' : 'ok')
                : (routedDevices.length ? 'warn' : 'error'),
            hint: sinks.length + ' sink' + (sinks.length === 1 ? '' : 's') + ' detected' +
                (sinkIssueDevices.length ? ' · ' + sinkIssueDevices.length + ' degraded' : ''),
        },
        {
            title: 'Music Assistant',
            value: ma.connected ? 'Connected' : (ma.configured ? 'Configured' : 'Offline'),
            tone: ma.connected ? (degradedGroups.length ? 'warn' : 'ok') : (ma.configured ? 'warn' : 'error'),
            hint: groups.length + ' sync group' + (groups.length === 1 ? '' : 's') +
                (degradedGroups.length ? ' · ' + degradedGroups.length + ' degraded' : ''),
        },
        {
            title: 'Bluetooth adapters',
            value: (daemonActive ? healthyAdapters.length : 0) + '/' + adapters.length,
            tone: daemonActive && healthyAdapters.length === adapters.length && adapters.length
                ? 'ok'
                : ((daemonActive || daemonState === 'unknown') && healthyAdapters.length ? 'warn' : 'error'),
            hint: 'Daemon ' + daemonState + ' · ' + audioServerLabel,
        },
    ].map(function(card) {
        return '<div class="diag-summary-card ' + card.tone + '">' +
            '<div class="diag-summary-label">' + escHtml(card.title) + '</div>' +
            '<div class="diag-summary-value">' + escHtml(card.value) + '</div>' +
            '<div class="diag-summary-hint">' + escHtml(card.hint) + '</div>' +
        '</div>';
    }).join('');

    var overview = [
        ['Version', d.version || 'Unknown'],
        ['Build date', d.build_date || 'Unknown'],
        ['Uptime', d.uptime || 'Unknown'],
        ['Runtime', d.runtime || 'Unknown'],
        ['Platform', env.platform ? env.platform + (env.arch ? ' (' + env.arch + ')' : '') : 'Unknown'],
        ['Python', env.python ? env.python.split('\n')[0] : 'Unknown'],
        ['BlueZ', env.bluez || 'Unknown'],
        ['D-Bus', d.dbus_available ? 'Available' : 'Missing'],
    ].map(function(item) {
        return '<div class="diag-item"><span class="diag-label">' + escHtml(item[0]) + '</span><span class="diag-value">' + escHtml(item[1]) + '</span></div>';
    }).join('');

    var adapterCards = adapters.length
        ? adapters.map(function(adapter, idx) {
            var tone = adapter.error ? 'err' : (daemonActive ? 'ok' : 'warn');
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(tone) + '<span>' + escHtml(adapter.id || ('hci' + idx)) + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    (adapter.mac ? '<div>' + escHtml(adapter.mac) + '</div>' : '') +
                    (adapter.default ? '<div>Default adapter</div>' : '') +
                    (!adapter.error && !daemonActive ? '<div>Bluetooth daemon: ' + escHtml(daemonState) + '</div>' : '') +
                    (adapter.error ? '<div>' + escHtml(adapter.error) + '</div>' : '') +
                '</div>' +
            '</div>';
        }).join('')
        : '<div class="diag-mini-card"><div class="diag-mini-meta">No Bluetooth adapters detected.</div></div>';

    var deviceCards = devices.length
        ? devices.map(function(dev) {
            var deviceTone = dev.enabled === false ? 'warn' : (dev.connected ? (dev.last_error ? 'warn' : 'ok') : 'err');
            var deviceStatus = dev.playing ? 'Playing' : (dev.connected ? 'Connected' : 'Disconnected');
            if (dev.enabled === false) deviceStatus += ' · Disabled';
            if (dev.last_error) deviceStatus += ' · Attention needed';
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(deviceTone) + '<span>' + escHtml(dev.name || dev.mac || 'Unknown') + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    '<div>' + escHtml(deviceStatus) + '</div>' +
                    (dev.mac ? '<div>' + escHtml(dev.mac) + '</div>' : '') +
                    (dev.sink ? '<div>Sink: <code>' + escHtml(dev.sink) + '</code></div>' : '<div>Sink: not attached</div>') +
                    (dev.last_error ? '<div>' + escHtml(dev.last_error) + '</div>' : '') +
                '</div>' +
            '</div>';
        }).join('')
        : '<div class="diag-mini-card"><div class="diag-mini-meta">No bridge devices configured.</div></div>';

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
        : '<tr><td colspan="3">No Bluetooth sinks detected.</td></tr>';

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
            var nowPlaying = group.now_playing && group.now_playing.title
                ? (group.now_playing.artist ? group.now_playing.artist + ' — ' + group.now_playing.title : group.now_playing.title)
                : 'Nothing playing';
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(groupTone) + '<span>' + escHtml(group.name || group.id || 'Unnamed group') + '</span></div>' +
                '<div class="diag-mini-meta">' +
                    '<div>' + escHtml(groupStatus.join(' · ')) + '</div>' +
                    '<div>' + escHtml(nowPlaying) + '</div>' +
                '</div>' +
            '</div>';
        }).join('')
        : '<div class="diag-mini-card"><div class="diag-mini-meta">No MA sync groups available.</div></div>';

    var subprocessInfo = subprocesses.length
        ? subprocesses.map(function(proc) {
            var parts = [];
            if (proc.pid) parts.push('pid ' + proc.pid);
            if (proc.running) parts.push('running');
            if (proc.zombie_restarts > 0) parts.push('zombies ' + proc.zombie_restarts);
            if (proc.last_error) parts.push(proc.last_error);
            var procTone = !proc.alive ? 'err' : (proc.last_error ? 'warn' : 'ok');
            return '<div class="diag-mini-card">' +
                '<div class="diag-mini-title">' + dot(procTone) + '<span>' + escHtml(proc.name || 'Subprocess') + '</span></div>' +
                '<div class="diag-mini-meta">' + escHtml(parts.join(' · ') || 'No extra details') + '</div>' +
            '</div>';
        }).join('')
        : '<div class="diag-mini-card"><div class="diag-mini-meta">No subprocess telemetry available.</div></div>';

    var advancedOverview = [
        ['Audio server', audioServerLabel],
        ['Bluetooth daemon', daemonState],
        ['Memory (RSS)', env.process_rss_mb != null ? env.process_rss_mb + ' MB' : 'Unknown'],
        ['MA connection', ma.connected ? 'Connected' : (ma.configured ? 'Configured' : 'Offline')],
        ['Sink inputs', sinkInputError ? 'Error' : String(visibleSinkInputs.length)],
        ['PortAudio outputs', portAudioError ? 'Error' : String(visiblePortAudioDevices.length)],
        ['MA URL', ma.url || 'Not configured'],
    ].map(function(item) {
        return '<div class="diag-item"><span class="diag-label">' + escHtml(item[0]) + '</span><span class="diag-value">' + escHtml(item[1]) + '</span></div>';
    }).join('');

    var sinkInputCards = sinkInputError
        ? '<div class="diag-mini-card"><div class="diag-mini-title">' + dot('err') + '<span>Sink input scan failed</span></div><div class="diag-mini-meta">' + escHtml(sinkInputError.error) + '</div></div>'
        : (visibleSinkInputs.length
            ? visibleSinkInputs.map(function(input) {
                var inputTitle = input.application_name || input.media_name || input.media_title || ('Sink input #' + (input.id || '?'));
                var inputTone = input.state && input.state.toUpperCase() === 'RUNNING' ? 'ok' : 'warn';
                return '<div class="diag-mini-card">' +
                    '<div class="diag-mini-title">' + dot(inputTone) + '<span>' + escHtml(inputTitle) + '</span></div>' +
                    '<div class="diag-mini-meta">' +
                        (input.id ? '<div>ID: ' + escHtml(input.id) + '</div>' : '') +
                        (input.sink ? '<div>Sink: <code>' + escHtml(input.sink) + '</code></div>' : '') +
                        (input.state ? '<div>State: ' + escHtml(input.state) + '</div>' : '') +
                        (input.media_name && input.application_name !== input.media_name ? '<div>Media: ' + escHtml(input.media_name) + '</div>' : '') +
                    '</div>' +
                '</div>';
            }).join('')
            : '<div class="diag-mini-card"><div class="diag-mini-meta">No active sink inputs.</div></div>');

    var portAudioCards = portAudioError
        ? '<div class="diag-mini-card"><div class="diag-mini-title">' + dot('err') + '<span>PortAudio probe failed</span></div><div class="diag-mini-meta">' + escHtml(portAudioError.error) + '</div></div>'
        : (visiblePortAudioDevices.length
            ? visiblePortAudioDevices.map(function(device) {
                return '<div class="diag-mini-card">' +
                    '<div class="diag-mini-title">' + dot(device.is_default ? 'ok' : 'warn') + '<span>' + escHtml(device.name || 'Audio output') + '</span></div>' +
                    '<div class="diag-mini-meta">' +
                        (device.index != null ? '<div>Index: ' + escHtml(String(device.index)) + '</div>' : '') +
                        '<div>' + escHtml(device.is_default ? 'Default output device' : 'Available output device') + '</div>' +
                    '</div>' +
                '</div>';
            }).join('')
            : '<div class="diag-mini-card"><div class="diag-mini-meta">No PortAudio output devices detected.</div></div>');

    return '<div class="diag-panel">' +
        '<div class="diag-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Health summary</div><div class="diag-card-subtitle">Start here for overall bridge, routing, and MA health.</div></div></div>' +
            '<div class="diag-summary-grid">' + summaryCards + '</div>' +
            '<div class="diag-grid diag-runtime-grid">' + overview + '</div>' +
        '</div>' +
        '<div class="diag-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Adapters & routing</div><div class="diag-card-subtitle">Detected controllers and attached PulseAudio / PipeWire outputs.</div></div></div>' +
            '<div class="diag-adapters">' + adapterCards + '</div>' +
            '<div class="sink-table-wrap"><table class="sink-table"><thead><tr><th>Sink</th><th>Status</th><th>Attached device</th></tr></thead><tbody>' + sinkRows + '</tbody></table></div>' +
        '</div>' +
        '<div class="diag-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Bridge devices</div><div class="diag-card-subtitle">Connection state, sink assignment and last-known issues.</div></div></div>' +
            '<div class="diag-devices">' + deviceCards + '</div>' +
        '</div>' +
        '<div class="diag-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Music Assistant groups</div><div class="diag-card-subtitle">' + escHtml(ma.url || 'No MA URL configured') + '</div></div></div>' +
            '<div class="diag-ma-groups">' + groupCards + '</div>' +
        '</div>' +
        '<div class="diag-card">' +
            '<div class="diag-card-header"><div><div class="diag-card-title">Subprocesses & advanced</div><div class="diag-card-subtitle">Per-device bridge daemon telemetry and runtime details.</div></div></div>' +
            '<div class="diag-devices">' + subprocessInfo + '</div>' +
            '<div class="diag-grid diag-runtime-grid">' + advancedOverview + '</div>' +
            '<div class="diag-subsection">' +
                '<div class="diag-subsection-title">Active sink inputs</div>' +
                '<div class="diag-devices diag-subsection-grid">' + sinkInputCards + '</div>' +
            '</div>' +
            '<div class="diag-subsection">' +
                '<div class="diag-subsection-title">PortAudio outputs</div>' +
                '<div class="diag-devices diag-subsection-grid">' + portAudioCards + '</div>' +
            '</div>' +
        '</div>' +
        '<div class="diag-actions">' +
            '<div class="diag-actions-left">' +
                '<button type="button" class="btn btn-sm" onclick="downloadDiagnostics()">' + _buttonLabelWithIconHtml('download', 'Download diagnostics') + '</button>' +
                '<button type="button" class="btn btn-sm" onclick="return _openBugReport(event)">' + _buttonLabelWithIconHtml('report', 'Submit bug report') + '</button>' +
            '</div>' +
            '<div class="diag-actions-right">' +
                '<button type="button" class="btn btn-sm btn-refresh" onclick="reloadDiagnostics()">' + _buttonLabelWithIconHtml('refresh', 'Refresh') + '</button>' +
            '</div>' +
        '</div>' +
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
function updateHealthIndicator(devices) {
    var el = document.getElementById('health-indicator');
    if (!el) return;
    if (!devices || !devices.length) {
        el.innerHTML = '';
        return;
    }
    var active = devices.filter(function(d) {
        return d.bt_management_enabled !== false || d.bt_released_by === 'auto';
    });
    var released = devices.length - active.length;
    var total = active.length;
    var playing = 0, btOk = 0, maOk = 0;
    active.forEach(function(d) {
        if (d.playing) playing++;
        if (d.bluetooth_connected) btOk++;
        if (d.connected) maOk++;
    });
    var parts = [];
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
loadConfig();   // calls loadBtAdapters() internally after restoring btManualAdapters
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
