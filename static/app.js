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

// HA Ingress theme injection listener
// HA sends setTheme postMessage when theme changes (Ingress mode)
window.addEventListener('message', function(e) {
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type !== 'setTheme') return;
    // Only accept theme messages from same origin or parent (HA Ingress)
    if (e.origin !== window.location.origin && e.source !== window.parent) return;
    var theme = e.data.theme || {};
    var root = document.documentElement;
    Object.keys(theme).forEach(function(key) {
        if (key) root.style.setProperty('--' + key, theme[key]);
    });
});

// ---- State ----
var autoRefreshLogs = false;
var autoRefreshInterval = null;
var allLogs = [];
var currentLogLevel = 'all';
var btAdapters = [];
var btManualAdapters = [];
var lastDevices = [];
var lastGroups = [];

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

// ---- Auth helper ----

function _handleUnauthorized() {
    var loginUrl = (API_BASE || '') + '/login?next=' + encodeURIComponent(window.location.pathname);
    window.location.href = loginUrl;
}

// ---- Status ----

async function updateStatus() {
    try {
        var resp = await fetch(API_BASE + '/api/status');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var status = await resp.json();

        var info = [];
        if (status.hostname)   info.push(status.hostname);
        if (status.ip_address) info.push(status.ip_address);
        if (status.uptime)     info.push('up ' + status.uptime);
        var sysEl = document.getElementById('system-info');
        if (sysEl) sysEl.textContent = info.join(' \u00b7 ');

        var devices = status.devices || [status];
        var sorted = devices.slice().sort(function(a, b) {
            var score = function(d) { return d.playing ? 2 : (d.bluetooth_connected ? 1 : 0); };
            var gka = a.group_id || ('_' + a.player_name);
            var gkb = b.group_id || ('_' + b.player_name);
            // best score of any member in the same group (keeps active groups first)
            var groupScore = function(gk) {
                var best = 0;
                devices.forEach(function(d) {
                    if ((d.group_id || ('_' + d.player_name)) === gk) best = Math.max(best, score(d));
                });
                return best;
            };
            var gsa = groupScore(gka), gsb = groupScore(gkb);
            if (gsa !== gsb) return gsb - gsa;          // active groups first
            if (gka !== gkb) return gka < gkb ? -1 : 1; // same-score groups together
            return score(b) - score(a);                 // within group: playing first
        });
        // Reset index-keyed state if device list changes (avoids stale mappings)
        if (lastDevices.length !== sorted.length ||
            !lastDevices.every(function(d, idx) { return d.player_name === sorted[idx].player_name; })) {
            _groupSelected = {};
            lastReanchorCount = {};
            reanchorShownAt = {};
            lastReanchorAt = {};
        }
        lastDevices = sorted;
        lastGroups = status.groups || [];
        var grid = document.getElementById('status-grid');

        sorted.forEach(function(dev, i) {
            var card = document.getElementById('device-card-' + i);
            if (!card) {
                card = buildDeviceCard(i);
                grid.appendChild(card);
            }
            populateDeviceCard(i, dev);
        });

        // Remove stale cards
        Array.from(grid.querySelectorAll('.device-card'))
            .slice(sorted.length)
            .forEach(function(c) { c.remove(); });

        _updateGroupPanel();
        updateHealthIndicator(sorted);

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
        var elapsedSec = Math.min(snap.elapsed + (now - snap.t) / 1000, snap.duration);
        var pct = Math.min(100, (elapsedSec / snap.duration) * 100);
        var fill = document.getElementById('dprog-fill-' + idx);
        var timeEl = document.getElementById('dprog-time-' + idx);
        if (fill) fill.style.width = pct + '%';
        if (timeEl) timeEl.textContent = fmtSec(elapsedSec) + ' / ' + fmtSec(snap.duration);
    });
    // Sendspin-native per-device progress (ms)
    Object.keys(_progSnapshots).forEach(function(idx) {
        var snap = _progSnapshots[idx];
        if (!snap) return;
        var pos = Math.min(snap.pos + (now - snap.t), snap.dur);
        var fill = document.getElementById('dprog-fill-' + idx);
        var time = document.getElementById('dprog-time-' + idx);
        if (fill) fill.style.width = Math.min(100, (pos / snap.dur) * 100) + '%';
        if (time) time.textContent = fmtMs(pos) + ' / ' + fmtMs(snap.dur);
    });
}, 1000);

function buildDeviceCard(i) {
    var card = document.createElement('div');
    card.className = 'device-card';
    card.id = 'device-card-' + i;
    card.innerHTML =
        '<div class="device-card-identity">' +
          '<div style="display:flex;align-items:center;gap:6px;">' +
            '<input type="checkbox" class="device-select-cb" id="dsel-' + i + '" checked' +
              ' onchange="onDeviceSelect(' + i + ', this.checked)">' +
            '<div class="device-card-title" id="dname-' + i + '">Device ' + (i+1) + '</div>' +
            '<span class="released-badge" id="dreleased-badge-' + i + '" style="display:none;" title="BT management disabled — click Reclaim to resume">Released</span>' +
            '<div class="eq-bars" id="deq-' + i + '">' +
              '<div class="eq-bar"></div><div class="eq-bar"></div>' +
              '<div class="eq-bar"></div><div class="eq-bar"></div>' +
            '</div>' +
            '<span class="battery-badge" id="dbattery-' + i + '" style="display:none"></span>' +
          '</div>' +
          '<div class="group-badge" id="dgroup-' + i + '" style="display:none"></div>' +
          '<div class="device-mac identity-detail" id="dmac-' + i + '"></div>' +
          '<div class="ts-sub identity-detail" id="durl-' + i + '"></div>' +
        '</div>' +
        '<div class="device-rows">' +
          // Connection column (BT + MA server merged)
          '<div class="conn-col">' +
            '<div class="status-label">Connection</div>' +
            '<div class="conn-row">' +
              '<div class="conn-row-main">' +
                '<span class="conn-tag">BT</span>' +
                '<span class="status-indicator" id="dbt-ind-' + i + '"></span>' +
                '<span id="dbt-txt-' + i + '">-</span>' +
                '<span class="conn-detail" id="dbt-adapter-' + i + '"></span>' +
              '</div>' +
              '<div class="conn-hover-sub" id="dbt-mac-' + i + '"></div>' +
            '</div>' +
            '<div class="conn-row">' +
              '<div class="conn-row-main">' +
                '<span class="conn-tag">MA</span>' +
                '<span class="status-indicator" id="dsrv-ind-' + i + '"></span>' +
                '<span id="dsrv-txt-' + i + '">-</span>' +
                '<span class="ma-api-badge" id="dma-api-' + i + '" style="display:none" title="MA API integration active">api</span>' +
              '</div>' +
              '<div class="conn-hover-sub" id="dsrv-uri-' + i + '"></div>' +
            '</div>' +
          '</div>' +
          // Playback column (with inline track)
          '<div class="playback-col">' +
            '<div class="status-label">Playback</div>' +
            '<div class="status-value" id="dma-secondary-' + i + '">' +
              '<span class="status-indicator" id="dplay-ind-' + i + '"></span>' +
              '<span id="dplay-' + i + '">-</span>' +
              '<button type="button" class="card-icon-btn transport-btn" id="dma-prev-' + i + '" ' +
                'onclick="maQueueCmd(\'previous\', undefined, ' + i + ')" title="Previous" style="display:none;">&#9664;&#9664;</button>' +
              '<button type="button" class="card-icon-btn transport-btn" id="dbtn-pause-' + i + '" ' +
                'onclick="onDevicePause(' + i + ')" title="Pause/Unpause">&#9646;&#9646;</button>' +
              '<button type="button" class="card-icon-btn transport-btn" id="dma-next-' + i + '" ' +
                'onclick="maQueueCmd(\'next\', undefined, ' + i + ')" title="Next" style="display:none;">&#9654;&#9654;</button>' +
              '<button type="button" class="card-icon-btn transport-btn ma-hover-btn" id="dma-shuffle-' + i + '" ' +
                'onclick="maQueueCmd(\'shuffle\', undefined, ' + i + ')" title="Shuffle">&#8644;</button>' +
              '<button type="button" class="card-icon-btn transport-btn ma-hover-btn" id="dma-repeat-' + i + '" ' +
                'onclick="maCycleRepeat(' + i + ')" title="Repeat">&#8635;</button>' +
            '</div>' +
            '<div class="track-art-row">' +
              '<img id="dart-' + i + '" class="album-art" src="" alt="">' +
              '<div id="dtrack-' + i + '" class="device-track-inline"></div>' +
            '</div>' +
            '<div class="track-progress-wrap" id="dprog-wrap-' + i + '" style="display:none;">' +
              '<div class="track-progress-bar"><div class="track-progress-fill" id="dprog-fill-' + i + '"></div></div>' +
              '<div class="track-progress-time" id="dprog-time-' + i + '"></div>' +
            '</div>' +
          '</div>' +
          // Volume column
          '<div class="volume-col">' +
            '<div class="status-label">Volume</div>' +
            '<div class="volume-row">' +
              '<input type="range" min="0" max="100" value="100" ' +
                'class="volume-slider" id="vslider-' + i + '" ' +
                'oninput="onVolumeInput(' + i + ', this.value)">' +
              '<span class="volume-pct" id="dvol-' + i + '">100%</span>' +
              '<button type="button" id="dmute-' + i + '" ' +
                'class="card-icon-btn" ' +
                'title="Mute/Unmute">&#128264;</button>' +
            '</div>' +
            '<div class="ts" id="daudiofmt-' + i + '"></div>' +
            '<div class="dsink-value ts-sub" id="dsink-' + i + '"></div>' +
          '</div>' +
          // Sync column
          '<div>' +
            '<div class="status-label">Sync</div>' +
            '<div class="status-value" id="dsync-' + i + '">&#8212;</div>' +
            '<div class="ts" id="dsync-detail-' + i + '"></div>' +
            '<div class="ts sync-hover" id="ddelay-' + i + '"></div>' +
          '</div>' +
        '</div>' +
        '<div class="device-card-actions">' +
          '<button type="button" class="btn-bt-action btn-bt-reconnect" id="dbtn-reconnect-' + i + '"' +
            ' onclick="btReconnect(' + i + ')">&#128260; Reconnect</button>' +
          '<button type="button" class="btn-bt-action btn-bt-pair" id="dbtn-pair-' + i + '"' +
            ' onclick="btPair(' + i + ')" title="Put the device into pairing mode first">&#128279; Re-pair</button>' +
          '<button type="button" class="btn-bt-action btn-bt-release" id="dbtn-release-' + i + '"' +
            ' onclick="btToggleManagement(' + i + ')">&#128274; Release</button>' +
          '<span class="bt-action-status" id="dbt-action-status-' + i + '"></span>' +
        '</div>';
    return card;
}

function populateDeviceCard(i, dev) {
    var name = dev.player_name || ('Device ' + (i + 1));
    var nameEl = document.getElementById('dname-' + i);
    if (nameEl) nameEl.textContent = name;

    // Released badge next to name
    var releasedBadge = document.getElementById('dreleased-badge-' + i);
    if (releasedBadge) {
        var isReleased = dev.bt_management_enabled === false;
        releasedBadge.style.display = isReleased ? '' : 'none';
    }

    // Battery badge (only shown when device reports battery level)
    var batteryEl = document.getElementById('dbattery-' + i);
    if (batteryEl) {
        if (dev.battery_level != null) {
            var bl = dev.battery_level;
            var batColor = bl <= 15 ? '#ef4444' : bl <= 25 ? '#f59e0b' : '#22c55e';
            var batW = Math.max(2, Math.round(bl / 100 * 12));
            batteryEl.innerHTML =
                '<svg width="20" height="11" viewBox="0 0 20 11" style="vertical-align:-1px">' +
                '<rect x="0.5" y="0.5" width="16" height="10" rx="1.5" fill="none" stroke="' + batColor + '" stroke-width="1"/>' +
                '<rect x="17" y="3" width="2" height="5" rx="0.5" fill="' + batColor + '"/>' +
                '<rect x="2" y="2" width="' + batW + '" height="7" rx="1" fill="' + batColor + '"/>' +
                '</svg> ' + bl + '%';
            batteryEl.title = 'Battery: ' + bl + '%';
            batteryEl.style.color = batColor;
            batteryEl.style.display = '';
        } else {
            batteryEl.style.display = 'none';
        }
    }

    var mac = dev.bluetooth_mac || '';
    document.getElementById('dmac-' + i).textContent = mac ? 'MAC: ' + mac : '';

    // Card activity classes
    var card = document.getElementById('device-card-' + i);
    if (card) {
        var isActive = dev.bluetooth_connected || dev.playing;
        card.classList.toggle('inactive', !isActive);
        card.classList.toggle('playing', !!dev.playing);
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

    var groupBadge = document.getElementById('dgroup-' + i);
    if (groupBadge) {
        var groupLabel = dev.group_name || dev.group_id || '';
        var groupDisplay = groupLabel ? groupLabel.split('-').pop() : '';

        // Find matching group entry to get external (cross-bridge) members.
        // Match by player_name membership (not group_id) because Sendspin
        // assigns unique UUIDs per session — merged groups use one arbitrary id.
        var devName = dev.player_name || '';
        var grp = dev.group_id
            ? (lastGroups || []).find(function(g) {
                return g.members && g.members.some(function(m) { return m.player_name === devName; });
            })
            : null;
        var extCount = grp ? (grp.external_count || 0) : 0;
        var extSuffix = extCount > 0 ? '  +' + extCount : '';
        groupBadge.textContent = groupDisplay ? '\uD83D\uDD17 ' + groupDisplay + extSuffix : '';

        // Rich tooltip: local members with status + external members
        var tipLines = [groupLabel];
        if (grp && grp.members && grp.members.length > 0) {
            tipLines.push('───');
            grp.members.forEach(function(m) {
                var icon;
                if (m.playing) icon = '▶';
                else if (!m.server_connected) icon = '✕';
                else if (!m.bluetooth_connected) icon = '⚡';
                else icon = '✓';
                tipLines.push(icon + ' ' + (m.player_name || '?'));
            });
            (grp.external_members || []).forEach(function(m) {
                var icon = m.available === false ? '⊘' : '\uD83C\uDF10';
                tipLines.push(icon + ' ' + m.name);
            });
        }
        groupBadge.title = tipLines.join('\n');

        // Solo = no other bridge device shares this group_id and no external members
        var groupPeers = dev.group_id
            ? (lastDevices || []).filter(function(d) { return d !== dev && d.group_id === dev.group_id; }).length
            : 0;
        var isSolo = !groupPeers && !extCount;
        groupBadge.classList.toggle('hover-only', isSolo);
        groupBadge.style.display = groupDisplay ? '' : 'none';
    }

    var btAdapterEl = document.getElementById('dbt-adapter-' + i);
    if (btAdapterEl) {
        btAdapterEl.textContent = dev.bluetooth_adapter_hci || '';
        if (dev.bluetooth_adapter) btAdapterEl.title = (dev.bluetooth_adapter_hci ? dev.bluetooth_adapter_hci + ' ' : '') + dev.bluetooth_adapter;
    }

    var urlEl = document.getElementById('durl-' + i);
    if (urlEl) {
        if (dev.ip_address && dev.listen_port) {
            urlEl.textContent = 'ws://' + dev.ip_address + ':' + dev.listen_port + '/sendspin';
        } else {
            urlEl.textContent = '';
        }
    }

    // Bluetooth
    var btInd   = document.getElementById('dbt-ind-' + i);
    var btTxt   = document.getElementById('dbt-txt-' + i);
    var btMacEl = document.getElementById('dbt-mac-' + i);
    if (dev.bt_management_enabled === false) {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Released';
    } else if (dev.bluetooth_connected) {
        btInd.className = 'status-indicator active';
        btTxt.textContent = 'Connected';
    } else if (dev.reconnecting) {
        btInd.className = 'status-indicator reconnecting';
        btTxt.textContent = 'Reconnecting\u2026' +
            (dev.reconnect_attempt ? ' (' + dev.reconnect_attempt + ')' : '');
    } else if (dev.bluetooth_available) {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Disconnected';
    } else {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Not Available';
    }
    if (btMacEl) btMacEl.textContent = dev.bluetooth_mac || '';

    // Server
    var srvInd = document.getElementById('dsrv-ind-' + i);
    var srvTxt = document.getElementById('dsrv-txt-' + i);
    var maApiBadge = document.getElementById('dma-api-' + i);
    if (dev.server_connected) {
        srvInd.className = 'status-indicator active';
        srvTxt.textContent = 'Connected';
    } else {
        srvInd.className = 'status-indicator inactive';
        srvTxt.textContent = dev.error || 'Disconnected';
    }
    if (maApiBadge) maApiBadge.style.display = (dev.ma_now_playing && dev.ma_now_playing.connected) ? '' : 'none';
    var srvUri = document.getElementById('dsrv-uri-' + i);
    if (srvUri) {
        var srvLabel = '';
        var h = dev.server_host || '';
        var p = dev.server_port || 9000;
        if (dev.connected_server_url) {
            var m = dev.connected_server_url.match(/^wss?:\/\/([^\/]+)/);
            if (m) srvLabel = m[1];
        }
        if (!srvLabel) {
            if (h && !['auto','discover',''].includes(h.toLowerCase())) {
                srvLabel = h + ':' + p;
            } else {
                srvLabel = 'auto:' + p;
            }
        }
        srvUri.textContent = srvLabel;
    }

    // Playback
    var playInd   = document.getElementById('dplay-ind-' + i);
    var playTxt   = document.getElementById('dplay-' + i);
    var fmtEl = document.getElementById('daudiofmt-' + i);

    // Color indicator: red=no sink (BT not ready), green=playing+streaming,
    // red=playing but no audio (stale), yellow=stopped
    if (!dev.has_sink && dev.bluetooth_mac) {
        if (playInd) playInd.className = 'status-indicator inactive';
        if (playTxt) playTxt.textContent = 'No Sink';
    } else if (dev.playing && dev.audio_streaming) {
        if (playInd) playInd.className = 'status-indicator active';
        if (playTxt) playTxt.textContent = '\u25b6 Playing';
    } else if (dev.playing && !dev.audio_streaming) {
        if (playInd) playInd.className = 'status-indicator inactive';
        if (playTxt) playTxt.textContent = '\u25b6 No Audio';
    } else {
        if (playInd) playInd.className = 'status-indicator warning';
        if (playTxt) playTxt.textContent = '\u23f8 Stopped';
    }

    // Track progress bar (per-device MA data takes priority over Sendspin)
    var progWrap = document.getElementById('dprog-wrap-' + i);
    var progFill = document.getElementById('dprog-fill-' + i);
    var progTime = document.getElementById('dprog-time-' + i);
    var ma = dev.ma_now_playing || {};
    var maActive = !!(ma.connected);
    var deviceMaActive = maActive && !!dev.has_sink;
    var maHasProg = deviceMaActive && ma.state === 'playing' && ma.duration > 0 && ma.elapsed != null;
    if (maHasProg) {
        _maProgSnapshots[i] = {
            elapsed: ma.elapsed,
            duration: ma.duration,
            t: ma.elapsed_updated_at
                ? (Date.now() - (Date.now() / 1000 - ma.elapsed_updated_at) * 1000)
                : Date.now(),
        };
        if (progWrap) progWrap.style.display = '';
        delete _progSnapshots[i];
    } else {
        delete _maProgSnapshots[i];
        var hasProg = dev.playing && dev.track_duration_ms > 0 && dev.track_progress_ms != null;
        if (progWrap) progWrap.style.display = hasProg ? '' : 'none';
        if (hasProg) {
            _progSnapshots[i] = {pos: dev.track_progress_ms, dur: dev.track_duration_ms, t: Date.now()};
            if (progFill) progFill.style.width = Math.min(100, (dev.track_progress_ms / dev.track_duration_ms) * 100) + '%';
            if (progTime) progTime.textContent = fmtMs(dev.track_progress_ms) + ' / ' + fmtMs(dev.track_duration_ms);
        } else {
            delete _progSnapshots[i];
        }
    }

    // Audio format (strip codec prefix)
    if (fmtEl) {
        var fmt = dev.audio_format || '';
        if (fmt) { var sp = fmt.indexOf(' '); fmt = sp !== -1 ? fmt.slice(sp + 1) : ''; }
        fmtEl.textContent = fmt;
    }

    // Sync pause button state from poll (don't override if user just clicked)
    var pauseBtn = document.getElementById('dbtn-pause-' + i);
    if (pauseBtn && !pauseBtn.classList.contains('pending')) {
        if (dev.playing) {
            pauseBtn.innerHTML = '&#9646;&#9646;';
            pauseBtn.classList.remove('paused');
            pauseBtn.title = 'Pause';
        } else {
            pauseBtn.innerHTML = '&#9654;';
            pauseBtn.classList.add('paused');
            pauseBtn.title = 'Unpause';
        }
        var groupSize = dev.group_id
            ? (lastDevices || []).filter(function(d) { return d.group_id === dev.group_id; }).length
            : 0;
        pauseBtn.style.display = (!dev.has_sink && dev.bluetooth_mac) ? 'none' : '';
    }

    var trackEl = document.getElementById('dtrack-' + i);
    var artEl   = document.getElementById('dart-' + i);
    if (trackEl) {
        var maArtist = deviceMaActive ? (ma.artist || '') : '';
        var maTrack  = deviceMaActive ? (ma.track  || '') : '';
        var showArtist = maArtist || dev.current_artist;
        var showTrack  = maTrack  || dev.current_track;
        // Persist track on pause — clear only when both fields are empty
        if (showArtist || showTrack) {
            var fullText = showArtist && showTrack
                ? showArtist + ' \u2014 ' + showTrack
                : (showArtist || showTrack || '');
            trackEl.textContent = _firstOfSlash(showArtist) && _firstOfSlash(showTrack)
                ? _firstOfSlash(showArtist) + ' \u2014 ' + _firstOfSlash(showTrack)
                : _firstOfSlash(showArtist || showTrack || '');
            var tipAlbum = deviceMaActive && ma.album ? ' \u00b7 ' + ma.album : '';
            trackEl.title = fullText + tipAlbum;
            trackEl.style.color = dev.playing
                ? 'var(--primary-text-color)' : 'var(--secondary-text-color)';
        } else {
            trackEl.textContent = '';
            trackEl.title = '';
        }
    }
    // Album art
    if (artEl) {
        var imgUrl = deviceMaActive ? (ma.image_url || '') : '';
        if (artEl.src !== imgUrl) artEl.src = imgUrl;
    }

    // MA transport buttons (prev/next flanking pause) + hover secondary controls
    var prevBtn      = document.getElementById('dma-prev-' + i);
    var nextBtn      = document.getElementById('dma-next-' + i);
    var maShuffleBtn = document.getElementById('dma-shuffle-' + i);
    var maRepeatBtn  = document.getElementById('dma-repeat-' + i);
    if (prevBtn) prevBtn.style.display = deviceMaActive ? '' : 'none';
    if (nextBtn) nextBtn.style.display = deviceMaActive ? '' : 'none';
    if (maShuffleBtn) maShuffleBtn.classList.toggle('ma-ready', deviceMaActive);
    if (maRepeatBtn) maRepeatBtn.classList.toggle('ma-ready', deviceMaActive);
    if (deviceMaActive) {
        if (maShuffleBtn) maShuffleBtn.classList.toggle('active', !!ma.shuffle);
        if (maRepeatBtn) {
            var rm = ma.repeat || 'off';
            maRepeatBtn.title = 'Repeat: ' + rm + ' (click to cycle)';
            maRepeatBtn.classList.toggle('active', rm !== 'off');
        }
    }
    // Delay badge — hover-only in sync column
    var delayEl = document.getElementById('ddelay-' + i);
    if (delayEl) {
        var delay = dev.static_delay_ms;
        if (dev.playing && delay !== undefined && delay !== null && delay !== 0) {
            delayEl.textContent = 'delay: ' + (delay > 0 ? '+' : '') + delay + 'ms';
            delayEl.style.color = Math.abs(delay) > 1000 ? '#f59e0b' : 'var(--secondary-text-color)';
            delayEl.classList.add('has-delay');
        } else {
            delayEl.textContent = '';
            delayEl.classList.remove('has-delay');
        }
    }

    // Sync
    // NOTE: dev.reanchoring is NOT used for display — the backend flag can get stuck True
    // because sendspin logs "re-anchoring" AFTER the stream-restart callback fires, so the
    // bridge_daemon's on_stream_event("start") guard runs before the flag is ever set.
    // Instead we track reanchor_count changes: when count increases a timed warning fires.
    var syncEl = document.getElementById('dsync-' + i);
    var syncDetail = document.getElementById('dsync-detail-' + i);
    if (syncEl) {
        var currCount = dev.reanchor_count || 0;
        var currAt = dev.last_reanchor_at || '';
        if (!dev.playing) {
            syncEl.textContent = '\u2014';
            syncEl.style.color = '#9ca3af';
            if (syncDetail) syncDetail.textContent = '';
            delete reanchorShownAt[i];
            lastReanchorCount[i] = currCount;
            lastReanchorAt[i] = currAt;
        } else {
            // Detect a new re-anchor event: count increased OR last_reanchor_at changed
            // (count alone can reset to 0 on stream restart, causing missed detections)
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
                syncEl.innerHTML = '<span style="color:#f59e0b;">&#9888; Re-anchoring</span>';
                if (syncDetail) syncDetail.textContent = dev.last_sync_error_ms != null
                    ? 'Error: ' + dev.last_sync_error_ms.toFixed(1) + ' ms' : '';
            } else {
                delete reanchorShownAt[i];
                syncEl.innerHTML = '<span style="color:#10b981;">&#10003; In sync</span>';
                if (syncDetail) {
                    var rc = dev.reanchor_count || 0;
                    syncDetail.innerHTML = rc
                        ? '<span title="Number of re-synchronisations in this stream session">Re-anchors: ' + rc + '</span>'
                        : '';
                    syncDetail.style.color = rc > 100 ? 'var(--error-color)' : rc > 10 ? '#f59e0b' : '';
                }
            }
        }
    }

    // Volume — only update if user isn't actively adjusting this slider
    var hasSink = dev.has_sink !== false;  // true when audio sink is configured
    if (dev.volume !== undefined && !volPending[i]) {
        var slider = document.getElementById('vslider-' + i);
        var volEl  = document.getElementById('dvol-' + i);
        if (slider) {
            slider.value = dev.volume;
            slider.disabled = !hasSink;
            slider.style.opacity = hasSink ? '' : '0.35';
            slider.title = hasSink ? '' : 'Audio sink not configured';
        }
        if (volEl) volEl.textContent = dev.volume + '%';
    }

    // Sink name — small hint under volume slider
    var sinkEl = document.getElementById('dsink-' + i);
    if (sinkEl) {
        if (dev.sink_name) {
            sinkEl.textContent = dev.sink_name;
            sinkEl.style.color = '';
        } else if (dev.bluetooth_mac && !hasSink) {
            sinkEl.textContent = '\u26a0 No audio sink';
            sinkEl.style.color = '#f59e0b';
        } else {
            sinkEl.textContent = '';
        }
    }

    // Equalizer — animated green when streaming, frozen red when playing without audio
    var eqEl = document.getElementById('deq-' + i);
    if (eqEl) {
        var isStreaming = !!dev.playing && !!dev.audio_streaming;
        var isStale = !!dev.playing && !dev.audio_streaming;
        eqEl.classList.toggle('active', isStreaming);
        eqEl.classList.toggle('stale', isStale);
    }

    // Mute button — attach handler once, update icon on every poll
    var muteBtn = document.getElementById('dmute-' + i);
    if (muteBtn) {
        muteBtn.textContent = dev.muted ? '\uD83D\uDD07' : '\uD83D\uDD08';
        muteBtn.title = dev.muted ? 'Unmute' : (hasSink ? 'Mute' : 'Audio sink not configured');
        muteBtn.classList.toggle('muted', !!dev.muted);
        muteBtn.disabled = !hasSink;
        muteBtn.style.opacity = hasSink ? '' : '0.35';
        if (!muteBtn._handlerSet) {
            muteBtn._handlerSet = true;
            muteBtn.addEventListener('click', function() {
                var dev = lastDevices && lastDevices[i]; if (!dev) return;
                fetch(API_BASE + '/api/mute', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ player_name: dev.player_name || null }),
                }).then(function(r) { return r.json(); }).then(function(d) {
                    if (d.success && lastDevices[i]) lastDevices[i].muted = d.muted;
                    var btn = document.getElementById('dmute-' + i);
                    if (btn) {
                        btn.textContent = d.muted ? '\uD83D\uDD07' : '\uD83D\uDD08';
                        btn.title = d.muted ? 'Unmute' : 'Mute';
                        btn.classList.toggle('muted', !!d.muted);
                    }
                }).catch(function(e) { console.error('Mute failed:', e); });
            });
        }
    }

    // Release/Reclaim button state
    var relBtn = document.getElementById('dbtn-release-' + i);
    if (relBtn) {
        var mgmtEnabled = dev.bt_management_enabled !== false;
        if (mgmtEnabled) {
            relBtn.textContent = '🔓 Release';
            relBtn.className = 'btn-bt-action btn-bt-release';
            relBtn.title = 'Stop BT management for this device (it will stop auto-reconnecting)';
        } else {
            relBtn.textContent = '🔒 Reclaim';
            relBtn.className = 'btn-bt-action btn-bt-reclaim';
            relBtn.title = 'Resume BT management and auto-reconnect';
        }
        // Disable Reconnect/Re-pair while released
        var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
        var pairBtn = document.getElementById('dbtn-pair-' + i);
        if (reconnBtn) reconnBtn.disabled = !mgmtEnabled;
        if (pairBtn) pairBtn.disabled = !mgmtEnabled;
    }
}

// ---- Volume slider ----

function onVolumeInput(i, val) {
    var volEl = document.getElementById('dvol-' + i);
    if (volEl) volEl.textContent = val + '%';

    // Mark pending so status poll doesn't overwrite while user drags
    volPending[i] = true;
    var slider = document.getElementById('vslider-' + i);
    if (slider && !slider.disabled) { slider.style.opacity = '0.55'; }
    clearTimeout(volTimers[i]);
    volTimers[i] = setTimeout(function() {
        sendVolume(i, parseInt(val, 10));
    }, 300);
}

async function sendVolume(deviceIndex, vol) {
    var dev = lastDevices[deviceIndex] || {};
    try {
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

// ---- Logs ----

function escHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getLogClass(line) {
    if (!line || typeof line !== 'string') return '';
    var u = line.toUpperCase();
    if (u.indexOf('ERROR') !== -1 || u.indexOf('CRITICAL') !== -1) return 'log-error';
    if (u.indexOf('WARNING') !== -1 || u.indexOf('WARN') !== -1)   return 'log-warning';
    if (u.indexOf(' INFO ') !== -1  || u.indexOf(' INFO\t') !== -1) return 'log-info';
    if (u.indexOf('DEBUG') !== -1) return 'log-debug';
    return '';
}

function renderLogs() {
    var filtered = allLogs;
    if (currentLogLevel === 'error') {
        filtered = allLogs.filter(function(l) {
            var u = l.toUpperCase();
            return u.indexOf('ERROR') !== -1 || u.indexOf('CRITICAL') !== -1;
        });
    } else if (currentLogLevel === 'warning') {
        filtered = allLogs.filter(function(l) {
            var u = l.toUpperCase();
            return u.indexOf('WARNING') !== -1 || u.indexOf('WARN') !== -1;
        });
    } else if (currentLogLevel === 'info') {
        filtered = allLogs.filter(function(l) {
            var u = l.toUpperCase();
            return u.indexOf(' INFO ') !== -1 || u.indexOf(' INFO\t') !== -1;
        });
    }
    var container = document.getElementById('logs');
    container.innerHTML = filtered.map(function(line) {
        return '<div class="log-line ' + getLogClass(line) + '">' + escHtml(line) + '</div>';
    }).join('');
    container.scrollTop = container.scrollHeight;
}

function setLogLevel(level) {
    currentLogLevel = level;
    document.querySelectorAll('.filter-btn').forEach(function(b) { b.classList.remove('active'); });
    document.getElementById('filter-' + level).classList.add('active');
    renderLogs();
}

async function refreshLogs() {
    try {
        var resp = await fetch(API_BASE + '/api/logs?lines=150');
        var data = await resp.json();
        allLogs = data.logs || [];
        renderLogs();
    } catch (err) {
        console.error('Error refreshing logs:', err);
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
    if (total < 2) {
        document.getElementById('group-controls').style.display = 'none';
        return;
    }
    document.getElementById('group-controls').style.display = 'flex';
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
        if (groupPct) groupPct.textContent = avg + '%';
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
    if (pct) pct.textContent = val + '%';
    // Mark slider as user-controlled so auto-sync doesn't override it
    var slider = document.getElementById('group-vol-slider');
    if (slider) slider._userTouched = true;
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
    var btn = document.getElementById('group-mute-btn');
    var currentlyMuted = btn && btn.classList.contains('muted');
    var muteVal = !currentlyMuted;   // Toggle: muted→unmute, not muted→mute
    fetch(API_BASE + '/api/mute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mute: muteVal, player_names: names})
    }).then(function(r) { return r.json(); }).then(function() {
        if (btn) {
            btn.textContent = muteVal ? '🔇 Unmute All' : '🔈 Mute All';
            btn.className = 'btn-group-mute' + (muteVal ? ' muted' : '');
        }
    });
}

function onPauseAll() {
    var btn = document.getElementById('group-pause-btn');
    var isPaused = btn && btn.classList.contains('paused');
    var action = isPaused ? 'play' : 'pause';
    var names = _getSelectedNames();
    var total = lastDevices ? lastDevices.length : 0;

    var afterPause = function() {
        if (btn) {
            if (action === 'pause') {
                btn.textContent = '\u25b6 Unpause All';
                btn.classList.add('paused');
            } else {
                btn.textContent = '\u23f8\u23f8 Pause All';
                btn.classList.remove('paused');
            }
        }
    };

    if (names.length === total) {
        // All players — use bulk MPRIS pause
        fetch(API_BASE + '/api/pause_all', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: action})
        }).then(function(r) { return r.json(); }).then(afterPause);
    } else {
        // Filtered selection — call individual pause per player
        var calls = names.map(function(name) {
            return fetch(API_BASE + '/api/pause', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: action, player_name: name})
            });
        });
        Promise.all(calls).then(afterPause);
    }
}

function onDevicePause(i) {
    var dev = lastDevices && lastDevices[i];
    var btn = document.getElementById('dbtn-pause-' + i);
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
                btn.innerHTML = '&#9654;';
                btn.classList.add('paused');
                btn.title = 'Unpause';
            } else {
                btn.innerHTML = '&#9646;&#9646;';
                btn.classList.remove('paused');
                btn.title = 'Pause';
            }
        }
    });
}

// ---- BT Actions (reconnect / pair) ----

async function maQueueCmd(action, value, devIdx) {
    var body = {action: action};
    if (value !== undefined) body.value = value;
    if (devIdx != null && lastDevices && lastDevices[devIdx]) {
        var ma = lastDevices[devIdx].ma_now_playing || {};
        if (ma.syncgroup_id) body.syncgroup_id = ma.syncgroup_id;
        if (action === 'shuffle' && value === undefined) body.value = !ma.shuffle;
    }
    try {
        await fetch(API_BASE + '/api/ma/queue/cmd', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
    } catch (err) { console.warn('MA queue cmd failed:', err); }
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

async function btPair(i) {
    if (!confirm('Put the device into pairing mode first, then click OK.\nThis will interrupt playback for ~25 seconds.')) return;
    var dev = lastDevices && lastDevices[i];
    var playerName = dev ? dev.player_name : null;
    var btn = document.getElementById('dbtn-pair-' + i);
    var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
    var status = document.getElementById('dbt-action-status-' + i);
    if (btn) btn.disabled = true;
    if (reconnBtn) reconnBtn.disabled = true;
    if (status) status.textContent = '&#8635; Pairing\u2026 (~25s, put device in pairing mode)';
    try {
        var resp = await fetch(API_BASE + '/api/bt/pair', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({player_name: playerName})
        });
        var d = await resp.json();
        if (status) status.textContent = d.success ? '\u2713 ' + (d.message || 'Started') : '\u2717 ' + (d.error || 'Failed');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    setTimeout(function() {
        if (btn) btn.disabled = false;
        if (reconnBtn) reconnBtn.disabled = false;
        if (status) status.textContent = '';
    }, 30000);
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
                btn.textContent = newEnabled ? '\uD83D\uDD13 Release' : '\uD83D\uDD12 Reclaim';
                btn.className = 'btn-bt-action ' + (newEnabled ? 'btn-bt-release' : 'btn-bt-reclaim');
            }
            // Disable Reconnect/Re-pair while released
            var reconnBtn = document.getElementById('dbtn-reconnect-' + i);
            var pairBtn = document.getElementById('dbtn-pair-' + i);
            if (reconnBtn) reconnBtn.disabled = newEnabled ? false : true;
            if (pairBtn) pairBtn.disabled = newEnabled ? false : true;
        }
        if (status) status.textContent = d.success ? '\u2713 ' + d.message : '\u2717 ' + (d.error || 'Failed');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    if (btn) btn.disabled = false;
    setTimeout(function() { if (status) status.textContent = ''; }, 4000);
}

function toggleAutoRefresh() {
    autoRefreshLogs = !autoRefreshLogs;
    var btn = document.getElementById('auto-refresh-btn');
    if (autoRefreshLogs) {
        btn.textContent = 'Auto-Refresh: On';
        btn.classList.add('auto-on');
        autoRefreshInterval = setInterval(refreshLogs, 2000);
        refreshLogs();
    } else {
        btn.textContent = 'Auto-Refresh: Off';
        btn.classList.remove('auto-on');
        clearInterval(autoRefreshInterval);
    }
}

// ---- BT Device Table ----

async function loadBtAdapters() {
    try {
        var resp = await fetch(API_BASE + '/api/bt/adapters');
        var data = await resp.json();
        btAdapters = data.adapters || [];
    } catch (_) { btAdapters = []; }
    // Merge manual entries not already in detected list
    btManualAdapters.forEach(function(m) {
        if (!btAdapters.find(function(a) { return a.id === m.id; }))
            btAdapters.push(Object.assign({}, m, {manual: true}));
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
            row.innerHTML =
                '<span>' + escHtml(a.id) + '</span>' +
                '<span class="mono">' + escHtml(a.mac) + '</span>' +
                '<span>' + escHtml(a.name || '') + '</span>' +
                '<span class="dot ' + (a.powered ? 'green' : 'grey') + '">\u25cf</span>' +
                '<span></span>';
            el.appendChild(row);
        }
    });
}

function buildManualRow(id, mac, name) {
    var row = document.createElement('div');
    row.className = 'adapter-row manual';
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

function syncManualAdapters() {
    btManualAdapters = [];
    document.querySelectorAll('#adapters-table .adapter-row.manual').forEach(function(row) {
        var id  = row.querySelector('.adp-id').value.trim();
        var mac = row.querySelector('.adp-mac').value.trim();
        var name = row.querySelector('.adp-name').value.trim();
        if (id || mac) btManualAdapters.push({id: id, mac: mac, name: name});
    });
    // Re-merge into btAdapters (keep detected, replace manual section)
    btAdapters = btAdapters.filter(function(a) { return !a.manual; });
    btManualAdapters.forEach(function(m) {
        if (!btAdapters.find(function(a) { return a.id === m.id; }))
            btAdapters.push(Object.assign({}, m, {manual: true}));
    });
    rebuildAdapterDropdowns();
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
        var label = a.id + (a.mac ? ' \u2014 ' + a.mac : '');
        opts += '<option value="' + a.id + '"' +
            (selected === a.id ? ' selected' : '') + '>' + label + '</option>';
    });
    return opts;
}

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort, enabled, preferredFormat, keepaliveSilence, keepaliveInterval) {
    var tbody = document.getElementById('bt-devices-table');
    var wrap = document.createElement('div');
    wrap.className = 'bt-device-wrap';
    if (enabled === false) wrap.dataset.enabled = 'false';

    var row = document.createElement('div');
    row.className = 'bt-device-row';
    var delayVal = (delay !== undefined && delay !== null && delay !== '') ? delay : 0;
    var portVal  = (listenPort !== undefined && listenPort !== null && listenPort !== '') ? listenPort : '';
    var fmtVal   = (preferredFormat !== undefined && preferredFormat !== null) ? preferredFormat : 'flac:44100:16:2';
    // keepalive: 0 = disabled; ≥30 = enabled. Migrate old checkbox+interval format.
    var kaVal;
    if (keepaliveSilence) {
        kaVal = (keepaliveInterval !== undefined && keepaliveInterval !== null && keepaliveInterval !== '') ? parseInt(keepaliveInterval, 10) || 30 : 30;
        if (kaVal < 30) kaVal = 30;
    } else {
        kaVal = (keepaliveInterval !== undefined && keepaliveInterval !== null && keepaliveInterval !== '') ? parseInt(keepaliveInterval, 10) : 0;
        // If migrated from a config that only has keepalive_interval without keepalive_silence, treat >0 as enabled
        if (kaVal > 0 && kaVal < 30) kaVal = 30;
    }
    row.innerHTML =
        '<input type="text" placeholder="Player Name" class="bt-name" value="' +
            escHtmlAttr(name || '') + '">' +
        '<input type="text" placeholder="AA:BB:CC:DD:EE:FF" class="bt-mac" value="' +
            escHtmlAttr(mac || '') + '">' +
        '<select class="bt-adapter">' + btAdapterOptions(adapter || '') + '</select>' +
        '<input type="text" class="bt-listen-host" placeholder="auto" title="IP address this player advertises/listens on. Leave blank to auto-detect." value="' +
            escHtmlAttr(listenHost || '') + '">' +
        '<input type="number" class="bt-listen-port" placeholder="8928" title="Port this player listens on (default: 8928, 8929 for 2nd\u2026)" value="' +
            escHtmlAttr(String(portVal)) + '" min="1024" max="65535">' +
        '<input type="number" class="bt-delay" title="Static delay (ms). Negative = compensate for output latency. Typical BT A2DP: -500" value="' +
            escHtmlAttr(String(delayVal)) + '" step="50">' +
        '<input type="text" class="bt-preferred-format" placeholder="flac:44100:16:2" title="Preferred audio format: codec:samplerate:bitdepth:channels. Default flac:44100:16:2 matches SBC A2DP (no resampling)." value="' +
            escHtmlAttr(fmtVal) + '">' +
        '<input type="number" class="bt-keepalive-interval" min="0" placeholder="0" ' +
            'title="Keep-alive silence interval in seconds. 0 = disabled. Minimum 30 when enabled." value="' +
            escHtmlAttr(String(kaVal)) + '">' +
        '<button type="button" class="btn-remove-dev">\u00d7</button>';

    row.querySelector('.btn-remove-dev').addEventListener('click', function() {
        wrap.remove();
    });
    row.querySelector('.bt-mac').addEventListener('input', function() {
        var v = this.value.trim();
        var valid = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(v);
        this.classList.toggle('invalid', v !== '' && !valid);
    });

    wrap.appendChild(row);
    tbody.appendChild(wrap);
}

function collectBtDevices() {
    var devices = [];
    document.querySelectorAll('#bt-devices-table .bt-device-wrap').forEach(function(wrap) {
        var row    = wrap.querySelector('.bt-device-row');
        var name       = row.querySelector('.bt-name').value.trim();
        var mac        = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var adapter    = row.querySelector('.bt-adapter').value;
        var listenHost = (row.querySelector('.bt-listen-host') || {}).value || '';
        var portEl     = row.querySelector('.bt-listen-port');
        var listenPort = portEl && portEl.value.trim() ? parseInt(portEl.value, 10) : null;
        var delayEl    = row.querySelector('.bt-delay');
        var delay      = delayEl ? parseFloat(delayEl.value) : 0;
        if (isNaN(delay)) delay = 0;
        var fmtEl      = row.querySelector('.bt-preferred-format');
        var preferredFormat = fmtEl ? fmtEl.value.trim() : 'flac:44100:16:2';
        var kaIntEl    = row.querySelector('.bt-keepalive-interval');
        var kaVal      = kaIntEl ? parseInt(kaIntEl.value, 10) : 0;
        if (isNaN(kaVal) || kaVal < 0) kaVal = 0;
        if (kaVal > 0 && kaVal < 30) kaVal = 30;  // enforce minimum
        var dev = { mac: mac, adapter: adapter, player_name: name, static_delay_ms: delay, preferred_format: preferredFormat || 'flac:44100:16:2' };
        if (listenHost) dev.listen_host = listenHost;
        if (listenPort) dev.listen_port = listenPort;
        dev.keepalive_interval = kaVal;
        if (kaVal > 0) {
            dev.keepalive_silence = true;
        }
        // Preserve enabled flag: live status takes precedence, then config-loaded value from dataset
        var livedev = lastDevices && lastDevices.find(function(d) {
            return d.player_name === name || d.bluetooth_mac === mac;
        });
        if (livedev) {
            if (livedev.bt_management_enabled === false) dev.enabled = false;
        } else if (wrap.dataset.enabled === 'false') {
            dev.enabled = false;
        }
        if (mac) devices.push(dev);
    });
    return devices;
}

function populateBtDeviceRows(devices) {
    document.getElementById('bt-devices-table').innerHTML = '';
    devices.forEach(function(d) {
        addBtDeviceRow(d.player_name || '', d.mac || '', d.adapter || '',
                       d.static_delay_ms, d.listen_host, d.listen_port, d.enabled,
                       d.preferred_format, d.keepalive_silence, d.keepalive_interval);
    });
}

// ---- BT Scan ----

async function startBtScan() {
    var btn     = document.getElementById('scan-btn');
    var status  = document.getElementById('scan-status');
    var box     = document.getElementById('scan-results-box');
    var listDiv = document.getElementById('scan-results-list');

    btn.disabled = true;
    status.innerHTML = '<span class="scan-spinner"></span> Scanning\u2026 (~10s)';
    box.style.display = 'none';

    try {
        var resp = await fetch(API_BASE + '/api/bt/scan', { method: 'POST' });
        var data = await resp.json();
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
            status.textContent = 'No devices found.';
        } else {
            status.textContent = 'Found ' + devices.length + ' device(s)';
            listDiv.innerHTML = devices.map(function(d, i) {
                return '<div class="scan-result-item">' +
                    '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                    '<span>' + escHtml(d.name) + '</span>' +
                    '<button type="button" data-scan-idx="' + i + '" style="margin-left:auto;padding:3px 10px;' +
                        'background:var(--primary-color);color:white;border:none;border-radius:4px;' +
                        'cursor:pointer;font-size:12px;">Add</button>' +
                    '</div>';
            }).join('');
            listDiv.querySelectorAll('[data-scan-idx]').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var d = devices[parseInt(this.dataset.scanIdx)];
                    addFromScan(d.mac, d.name, d.adapter);
                });
            });
            box.style.display = 'block';
        }
    } catch (err) {
        status.textContent = 'Scan failed: ' + err.message;
    } finally {
        btn.disabled = false;
    }
}

function autoAdapter() {
    return (btAdapters.length === 1) ? btAdapters[0].id : '';
}

function addFromScan(mac, name, adapter) {
    addBtDeviceRow(name, mac, adapter || autoAdapter());
    document.getElementById('scan-results-box').style.display = 'none';
    document.getElementById('scan-status').textContent = '';
}

function addFromPaired(mac, name) {
    addBtDeviceRow(name, mac, autoAdapter());
    document.getElementById('paired-box').style.display = 'none';
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
        if (devices.length === 0 && !showAllChecked) { box.style.display = 'none'; return; }
        box.style.display = 'block';

        // Update title with count hint
        var titleEl = box.querySelector('.paired-box-title span');
        if (titleEl) {
            var countHint = '';
            if (!showAllChecked && allCount > devices.length) {
                countHint = ' (' + devices.length + ' audio · ' + allCount + ' total)';
            } else {
                countHint = ' (' + devices.length + ')';
            }
            titleEl.textContent = 'Already paired \u2014 click to add:' + countHint;
        }

        listDiv.innerHTML = devices.map(function(d, idx) {
            // Replace raw RSSI-only strings with a friendlier label
            var displayName = /^RSSI:/i.test(d.name) ? 'Unknown device' : d.name;
            return '<div class="scan-result-item">' +
                '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                '<span>' + escHtml(displayName) + '</span>' +
                '<button type="button" data-paired-idx="' + idx + '" style="margin-left:auto;padding:3px 10px;' +
                    'background:var(--primary-color);color:white;border:none;border-radius:4px;' +
                    'cursor:pointer;font-size:12px;">Add</button>' +
                '</div>';
        }).join('');
        listDiv.querySelectorAll('[data-paired-idx]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var d = devices[parseInt(this.dataset.pairedIdx)];
                addFromPaired(d.mac, d.name);
            });
        });
    } catch (_) {}
}

// ---- Config ----

async function saveConfig() {
    var formData = new FormData(document.getElementById('config-form'));
    var config = Object.fromEntries(formData);

    // Collect BT devices from table rows (overrides anything from formData)
    config.BLUETOOTH_DEVICES = collectBtDevices();
    // Checkbox → bool (FormData only includes it when checked, with value "on")
    config.PREFER_SBC_CODEC = !!(document.getElementById('prefer-sbc-codec') || {}).checked;
    config.AUTH_ENABLED = !!(document.getElementById('auth-enabled') || {}).checked;
    config.VOLUME_VIA_MA = !!(document.getElementById('volume-via-ma') || {}).checked;
    // Cast numeric BT settings to integers
    config.BT_CHECK_INTERVAL = parseInt(config.BT_CHECK_INTERVAL, 10) || 10;
    config.BT_MAX_RECONNECT_FAILS = parseInt(config.BT_MAX_RECONNECT_FAILS, 10) || 0;
    // Pass current group slider value so backend can init volume for new devices
    var groupSlider = document.getElementById('group-vol-slider');
    config._new_device_default_volume = groupSlider ? parseInt(groupSlider.value, 10) : 100;
    // Save all adapters (auto-detected + manual) so native HA Config tab shows them
    config.BLUETOOTH_ADAPTERS = btAdapters.filter(function(a) { return a.id; });
    // Keep single BLUETOOTH_MAC for backward compat if exactly one device
    if (config.BLUETOOTH_DEVICES.length === 1) {
        config.BLUETOOTH_MAC = config.BLUETOOTH_DEVICES[0].mac;
    } else {
        config.BLUETOOTH_MAC = '';
    }

    try {
        var resp = await fetch(API_BASE + '/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        });
        return resp.ok;
    } catch (err) {
        console.error('Save config error:', err);
        return false;
    }
}

// ---- Change password (standalone mode) ----

async function setPassword() {
    var pw  = (document.getElementById('new-password') || {}).value || '';
    var pw2 = (document.getElementById('new-password-confirm') || {}).value || '';
    if (!pw) { alert('Please enter a password.'); return; }
    if (pw.length < 8) { alert('Password must be at least 8 characters.'); return; }
    if (pw !== pw2) { alert('Passwords do not match.'); return; }
    try {
        var resp = await fetch(API_BASE + '/api/set-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw }),
        });
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (resp.ok) {
            alert('Password set successfully.');
            document.getElementById('new-password').value = '';
            document.getElementById('new-password-confirm').value = '';
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
        }
    } catch (err) {
        alert('Error setting password: ' + err.message);
    }
}

// ---- Music Assistant discover & login ----

async function maDiscover() {
    var btn = document.getElementById('ma-discover-btn');
    var urlInput = document.getElementById('ma-login-url');
    var msgEl = document.getElementById('ma-login-msg');
    if (btn) btn.disabled = true;
    if (msgEl) { msgEl.textContent = 'Scanning network...'; msgEl.style.color = 'var(--secondary-text-color)'; }
    try {
        var resp = await fetch(API_BASE + '/api/ma/discover');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var data = await resp.json().catch(function() { return {}; });
        if (data.success && data.servers && data.servers.length > 0) {
            var s = data.servers[0];
            if (urlInput) urlInput.value = s.url;
            if (msgEl) {
                msgEl.textContent = '\u2714 Found: MA v' + (s.version || '?') + ' at ' + s.url;
                msgEl.style.color = 'var(--success-color, green)';
            }
            // Detect HA addon mode — hide login form, show token hint
            if (s.homeassistant_addon) {
                _setMaAddonMode(true);
            }
        } else {
            if (msgEl) {
                msgEl.textContent = '\u2716 No MA server found on network';
                msgEl.style.color = 'var(--error-color, red)';
            }
        }
    } catch (err) {
        if (msgEl) { msgEl.textContent = '\u2716 Discovery error: ' + err.message; msgEl.style.color = 'var(--error-color, red)'; }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function _setMaAddonMode(isAddon) {
    var creds = document.getElementById('ma-login-creds');
    var hint = document.getElementById('ma-addon-hint');
    var loginBtn = document.getElementById('ma-login-btn');
    if (isAddon) {
        if (creds) creds.style.display = 'none';
        if (hint) hint.style.display = 'block';
        if (loginBtn) loginBtn.style.display = 'none';
    } else {
        if (creds) creds.style.display = 'flex';
        if (hint) hint.style.display = 'none';
        if (loginBtn) loginBtn.style.display = '';
    }
}

function maHaAuthPopup() {
    var maUrl = (document.getElementById('ma-login-url').value || '').trim();
    var msgEl = document.getElementById('ma-ha-login-msg');
    if (!maUrl) {
        if (msgEl) { msgEl.textContent = 'Discover MA server first'; msgEl.style.color = 'var(--error-color, red)'; }
        return;
    }
    var w = 400, h = 520;
    var left = (screen.width - w) / 2, top = (screen.height - h) / 2;
    var popup = window.open(
        API_BASE + '/api/ma/ha-auth-page?ma_url=' + encodeURIComponent(maUrl),
        'ha_auth', 'width=' + w + ',height=' + h + ',left=' + left + ',top=' + top
    );
    if (!popup) {
        if (msgEl) { msgEl.textContent = 'Popup blocked — allow popups for this site'; msgEl.style.color = 'var(--error-color, red)'; }
        return;
    }
    function onMessage(ev) {
        if (ev.data && ev.data.type === 'ma-ha-auth-done' && ev.data.success) {
            window.removeEventListener('message', onMessage);
            _setMaStatus(true, ev.data.username, ev.data.url);
            var urlField = document.querySelector('input[name="MA_API_URL"]');
            if (urlField) urlField.value = ev.data.url;
            if (msgEl) { msgEl.textContent = '\u2714 ' + (ev.data.message || 'Connected'); msgEl.style.color = 'var(--success-color, green)'; }
            showToast('\u2714 Connected to Music Assistant via HA', 'success');
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
        if (msgEl) { msgEl.textContent = 'Enter MA username and password'; msgEl.style.color = 'var(--error-color, red)'; }
        return;
    }
    if (btn) btn.disabled = true;
    if (msgEl) { msgEl.textContent = 'Connecting...'; msgEl.style.color = 'var(--secondary-text-color)'; }
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
            if (msgEl) {
                msgEl.textContent = '\u2714 ' + data.message;
                msgEl.style.color = 'var(--success-color, green)';
            }
            showToast('\u2714 Connected to Music Assistant', 'success');
        } else {
            if (msgEl) {
                msgEl.textContent = '\u2716 ' + (data.error || 'Login failed');
                msgEl.style.color = 'var(--error-color, red)';
            }
        }
    } catch (err) {
        if (msgEl) { msgEl.textContent = '\u2716 Error: ' + err.message; msgEl.style.color = 'var(--error-color, red)'; }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function _setMaStatus(connected, username, url) {
    var icon = document.getElementById('ma-status-icon');
    var text = document.getElementById('ma-status-text');
    if (connected) {
        if (icon) icon.textContent = '\u2705';
        if (text) text.innerHTML = 'Connected' + (username ? ' as <b>' + escHtml(username) + '</b>' : '') + (url ? ' \u2014 ' + escHtml(url) : '');
    } else {
        if (icon) icon.textContent = '\u26aa';
        if (text) text.textContent = 'Not connected';
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
    var msgEl = document.getElementById('ma-login-msg');
    if (msgEl) { msgEl.textContent = 'Connecting via Home Assistant...'; msgEl.style.color = 'var(--secondary-text-color)'; }
    try {
        var resp = await fetch(API_BASE + '/api/ma/ha-silent-auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ha_token: haToken, ma_url: maUrl }),
        });
        var data = await resp.json().catch(function() { return {}; });
        if (data.success) {
            _setMaStatus(true, data.username || '', data.url || maUrl);
            showToast('\u2714 Auto-connected to Music Assistant', 'success');
            if (msgEl) { msgEl.textContent = '\u2714 Connected automatically'; msgEl.style.color = 'var(--success-color, green)'; }
            return true;
        }
    } catch (e) {
        console.warn('Silent MA auth failed:', e);
    }
    return false;
}

async function _maAutoConnect() {
    // 1. Auto-discover MA server
    await maDiscover();
    // 2. In Ingress mode with addon detected, try silent auth
    if (!_isIngress()) return;
    var hint = document.getElementById('ma-addon-hint');
    if (!hint || hint.style.display !== 'block') return;
    var maUrl = (document.getElementById('ma-login-url').value || '').trim();
    if (!maUrl) return;
    await _maSilentAuth(maUrl);
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
            if (msg) { msg.textContent = '\u2713 Applied'; setTimeout(function() { if (msg) msg.textContent = ''; }, 3000); }
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
        }
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

document.getElementById('config-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    try {
        var ok = await saveConfig();
        if (ok) {
            _setConfigDirty(false);
            showToast('\u2713 Configuration saved \u2014 restart to apply', 'success');
        } else {
            showToast('\u2717 Failed to save configuration', 'error');
        }
    } catch (err) {
        showToast('\u2717 Error: ' + err.message, 'error');
    }
});

// ---- Config dirty-state tracking ----
var _configDirty = false;
function _setConfigDirty(dirty) {
    _configDirty = dirty;
    var summary = document.querySelector('.config-section summary');
    if (!summary) return;
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
// Watch config form for any change
document.getElementById('config-form').addEventListener('input', function() { _setConfigDirty(true); _updateAdvancedCounter(); });
document.getElementById('config-form').addEventListener('change', function() { _setConfigDirty(true); _updateAdvancedCounter(); });
window.addEventListener('beforeunload', function(e) {
    if (_configDirty) {
        e.preventDefault();
        e.returnValue = '';
    }
});

async function loadConfig() {
    try {
        var resp = await fetch(API_BASE + '/api/config');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var config = await resp.json();

        // Populate simple fields
        ['SENDSPIN_SERVER', 'SENDSPIN_PORT', 'BRIDGE_NAME', 'TZ', 'PULSE_LATENCY_MSEC',
         'BT_CHECK_INTERVAL', 'BT_MAX_RECONNECT_FAILS', 'MA_API_URL', 'MA_API_TOKEN'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key];
        });
        // Populate checkboxes
        var sbcCheck = document.getElementById('prefer-sbc-codec');
        if (sbcCheck) sbcCheck.checked = !!config.PREFER_SBC_CODEC;
        var authCheck = document.getElementById('auth-enabled');
        if (authCheck) authCheck.checked = !!config.AUTH_ENABLED;
        var volMaCheck = document.getElementById('volume-via-ma');
        if (volMaCheck) volMaCheck.checked = config.VOLUME_VIA_MA !== false;
        var logLevelSel = document.getElementById('log-level-select');
        if (logLevelSel && config.LOG_LEVEL) logLevelSel.value = config.LOG_LEVEL.toUpperCase();
        updateTzPreview();

        // Restore manual adapters before re-running loadBtAdapters so merging picks them up
        btManualAdapters = config.BLUETOOTH_ADAPTERS || [];
        await loadBtAdapters();
        loadPairedDevices();

        // Populate BT device table
        var devices = config.BLUETOOTH_DEVICES;
        if (devices && Array.isArray(devices) && devices.length > 0) {
            populateBtDeviceRows(devices);
        } else if (config.BLUETOOTH_MAC) {
            // Migrate single BLUETOOTH_MAC to table
            addBtDeviceRow('', config.BLUETOOTH_MAC, '');
        }

        // Show count of non-default advanced fields
        _updateAdvancedCounter();

        // Update MA connection status
        if (config.MA_API_TOKEN) {
            _setMaStatus(true, config.MA_USERNAME || '', config.MA_API_URL || '');
        } else {
            // Auto-discover MA and attempt silent auth in Ingress mode
            _maAutoConnect();
        }
    } catch (err) {
        console.error('Error loading config:', err);
    }
}

// ---- Restart ----

async function saveAndRestart() {
    var banner = document.getElementById('restart-banner');
    banner.style.display = 'block';
    banner.className = 'restart-banner restarting';
    banner.textContent = 'Saving configuration\u2026';

    try {
        var saved = await saveConfig();
        if (!saved) {
            banner.style.display = 'none';
            return;
        }
        _setConfigDirty(false);
        banner.textContent = '🔄 Restarting service\u2026';
        try {
            await fetch(API_BASE + '/api/restart', { method: 'POST' });
        } catch (_) { /* Service dropped connection — expected */ }

        await new Promise(function(r) { setTimeout(r, 2500); });

        for (var attempt = 1; attempt <= 30; attempt++) {
            banner.textContent = '🔄 Restarting\u2026 (' + attempt + 's)';
            await new Promise(function(r) { setTimeout(r, 1000); });
            try {
                var resp = await fetch(API_BASE + '/api/status');
                if (resp.ok) {
                    banner.className = 'restart-banner online';
                    banner.textContent = '\u2713 Service restarted successfully';
                    setTimeout(function() { banner.style.display = 'none'; }, 3000);
                    updateStatus();
                    return;
                }
            } catch (_) { /* Not yet back */ }
        }
        banner.className = 'restart-banner warning';
        banner.textContent = '\u26a0\ufe0f Service may still be restarting \u2014 check logs';

    } catch (err) {
        banner.className = 'restart-banner warning';
        banner.textContent = '\u26a0\ufe0f Error: ' + err.message;
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
        preview.textContent = 'Now: ' + formatted;
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
        var sha = data.git_sha && data.git_sha !== 'unknown'
            ? '<div style="font-family:monospace;">' + escHtml(data.git_sha) + '</div>' : '';
        el.innerHTML =
            '<div>' + escHtml(data.version || '') + '</div>' +
            sha +
            '<div>' + escHtml(data.built_at || '') + '</div>';
    } catch (_) { /* Keep static Jinja2-rendered values */ }
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

function dot(ok) {
    return '<span class="diag-dot ' + (ok ? 'ok' : 'err') + '"></span>';
}

function renderDiagnostics(d) {
    var rows = '';

    rows += '<tr><td>Bluetooth daemon</td><td>' +
        dot(d.bluetooth_daemon === 'active') + escHtml(d.bluetooth_daemon || 'unknown') +
        '</td></tr>';

    rows += '<tr><td>D-Bus socket</td><td>' +
        dot(d.dbus_available) + (d.dbus_available ? 'Available' : 'Not found') +
        '</td></tr>';

    rows += '<tr><td>Audio server</td><td>' +
        dot(d.pulseaudio && d.pulseaudio !== 'not available') +
        escHtml(d.pulseaudio || 'unknown') + '</td></tr>';

    (d.adapters || []).forEach(function(a, i) {
        rows += '<tr><td>Adapter hci' + i + '</td><td>' +
            escHtml(a.mac || '') + (a.default ? ' <span style="color:#6b7280;">(default)</span>' : '') +
            (a.error ? ' <span style="color:#ef4444;">' + escHtml(a.error) + '</span>' : '') +
            '</td></tr>';
    });

    var sinks = d.sinks || [];
    rows += '<tr><td>BT audio sinks</td><td>' +
        dot(sinks.length > 0) +
        (sinks.length > 0
            ? sinks.map(function(s) { return '<code style="display:block;font-size:11px;word-break:break-all;">' + escHtml(s) + '</code>'; }).join('')
            : 'None') +
        '</td></tr>';

    (d.devices || []).forEach(function(dev) {
        var enabledTag = dev.enabled === false
            ? ' <span style="color:#f59e0b;font-size:11px;">Disabled</span>'
            : '';
        rows += '<tr><td>' + escHtml(dev.name || dev.mac || 'Unknown') + '</td><td>' +
            dot(dev.connected) + (dev.connected ? 'Connected' : 'Disconnected') +
            enabledTag +
            (dev.sink ? ' <span style="color:#6b7280;font-family:monospace;font-size:11px;">' +
                escHtml(dev.sink) + '</span>' : '') +
            (dev.last_error
                ? '<br><span style="color:#ef4444;font-size:11px;">' +
                  escHtml(dev.last_error) + '</span>' : '') +
            '</td></tr>';
    });

    // MA integration status
    var ma = d.ma_integration || {};
    if (ma.configured !== undefined) {
        rows += '<tr><td>MA API</td><td>' +
            dot(ma.connected) +
            (ma.connected ? 'Connected' : (ma.configured ? 'Configured, not connected' : 'Not configured')) +
            (ma.url ? ' <span style="color:#6b7280;font-size:11px;">(' + escHtml(ma.url) + ')</span>' : '') +
            '</td></tr>';
        (ma.syncgroups || []).forEach(function(g) {
            var mList = g.members || [];
            var npHtml = '';
            if (g.now_playing && g.now_playing.title) {
                npHtml = ' <span style="color:#6b7280;font-size:11px;">♫ ' +
                    escHtml(g.now_playing.artist ? g.now_playing.artist + ' — ' + g.now_playing.title : g.now_playing.title) +
                    (g.now_playing.state ? ' (' + escHtml(g.now_playing.state) + ')' : '') + '</span>';
            }
            rows += '<tr><td style="padding-left:20px;">↳ ' + escHtml(g.name || g.id) + '</td><td>' +
                '<span style="color:#6b7280;font-size:11px;">' + mList.length + ' member' + (mList.length !== 1 ? 's' : '') + '</span>' +
                npHtml + '</td></tr>';
            mList.forEach(function(m) {
                var icon = m.is_bridge
                    ? (m.enabled === false ? '⊘' : (m.bt_connected ? (m.playing ? '▶' : '✓') : '⚡'))
                    : (m.available ? '🌐' : '⊘');
                var stateText = m.state || '';
                var enabledText = m.is_bridge && m.enabled === false
                    ? ' <span style="color:#f59e0b;font-size:10px;">Disabled</span>' : '';
                var volText = m.volume != null ? '  vol ' + m.volume + '%' : '';
                var sinkText = m.is_bridge && m.sink
                    ? ' <code style="font-size:10px;color:#6b7280;">' + escHtml(m.sink) + '</code>'
                    : '';
                var macText = m.is_bridge && m.bt_mac
                    ? ' <span style="font-size:10px;color:#9ca3af;">' + escHtml(m.bt_mac) + '</span>'
                    : '';
                rows += '<tr><td style="padding-left:40px;font-size:12px;">' +
                    icon + ' ' + escHtml(m.name || m.id) +
                    '</td><td style="font-size:12px;">' +
                    dot(m.available !== false && m.enabled !== false) +
                    escHtml(stateText) + enabledText + volText + macText + sinkText +
                    '</td></tr>';
            });
        });
    }

    return '<table class="diag-table">' +
        '<tr><th>Component</th><th>Status</th></tr>' +
        rows + '</table>' +
        '<div style="text-align:right;margin-top:8px;">' +
          '<button type="button" onclick="reloadDiagnostics()" ' +
          'style="font-size:12px;background:none;border:none;color:var(--primary-color);cursor:pointer;">' +
          '&#8635; Refresh</button></div>';
}

function reloadDiagnostics() {
    var content = document.getElementById('diag-content');
    delete content.dataset.loaded;
    loadDiagnostics(content);
}

// ---- Config advanced toggle ----
function toggleAdvanced() {
    var btn = document.getElementById('adv-toggle');
    var body = document.getElementById('adv-body');
    if (!btn || !body) return;
    var open = body.classList.toggle('open');
    btn.classList.toggle('open', open);
}

function _updateAdvancedCounter() {
    var btn = document.getElementById('adv-toggle');
    if (!btn) return;
    var defaults = { SENDSPIN_SERVER: 'auto', SENDSPIN_PORT: '9000',
        PULSE_LATENCY_MSEC: '200', BT_CHECK_INTERVAL: '10',
        BT_MAX_RECONNECT_FAILS: '0', PREFER_SBC_CODEC: false };
    var count = 0;
    Object.keys(defaults).forEach(function(name) {
        var el = document.querySelector('#adv-body [name="' + name + '"]');
        if (!el) return;
        var val = el.type === 'checkbox' ? el.checked : (el.value || '').trim();
        var def = defaults[name];
        if (el.type === 'checkbox') { if (val !== def) count++; }
        else if (val && val !== String(def)) count++;
    });
    btn.textContent = count > 0 ? 'Advanced settings (' + count + ')' : 'Advanced settings';
}

// ---- Global Health Indicator ----
function updateHealthIndicator(devices) {
    var el = document.getElementById('health-indicator');
    if (!el || !devices || !devices.length) return;
    var playing = devices.filter(function(d) { return d.playing; }).length;
    var disconnected = devices.filter(function(d) { return !d.bluetooth_connected; }).length;
    var total = devices.length;
    var parts = [];
    if (playing > 0) {
        parts.push('<span class="health-dot ok"></span>' + playing + '/' + total + ' playing');
    } else {
        parts.push('<span class="health-dot warn"></span>0/' + total + ' playing');
    }
    if (disconnected > 0) {
        parts.push('<span class="health-dot error"></span>' + disconnected + ' disconnected');
    }
    el.innerHTML = parts.join('<span style="opacity:0.4;padding:0 2px;">·</span>');
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

// ---- Init ----
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
                var status = JSON.parse(e.data);
                var info = [];
                if (status.hostname)   info.push(status.hostname);
                if (status.ip_address) info.push(status.ip_address);
                if (status.uptime)     info.push('up ' + status.uptime);
                var sysEl = document.getElementById('system-info');
                if (sysEl) sysEl.textContent = info.join(' \u00b7 ');
                var devices = status.devices || [status];
                var sorted = devices.slice().sort(function(a, b) {
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
                if (lastDevices.length !== sorted.length ||
                    !lastDevices.every(function(d, idx) { return d.player_name === sorted[idx].player_name; })) {
                    _groupSelected = {};
                    lastReanchorCount = {};
                    reanchorShownAt = {};
                    lastReanchorAt = {};
                }
                lastDevices = sorted;
                lastGroups = status.groups || [];
                var grid = document.getElementById('status-grid');
                sorted.forEach(function(dev, i) {
                    var card = document.getElementById('device-card-' + i);
                    if (!card) { card = buildDeviceCard(i); grid.appendChild(card); }
                    populateDeviceCard(i, dev);
                });
                Array.from(grid.querySelectorAll('.device-card'))
                    .slice(sorted.length).forEach(function(c) { c.remove(); });
                _updateGroupPanel();
                updateHealthIndicator(sorted);
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
loadVersionInfo();
