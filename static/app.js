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
var volTimers = {};
var volPending = {}; // deviceIndex -> true if user recently touched slider

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
        if (status.hostname)   info.push(escHtml(status.hostname));
        if (status.ip_address) info.push(escHtml(status.ip_address));
        if (status.uptime)     info.push('up ' + escHtml(status.uptime));
        var sysEl = document.getElementById('system-info');
        if (sysEl) sysEl.innerHTML = info.join(' &middot; ');

        var devices = status.devices || [status];
        // Reset group selection if device list changes (avoids stale index mapping)
        if (lastDevices.length !== devices.length ||
            !lastDevices.every(function(d, idx) { return d.player_name === devices[idx].player_name; })) {
            _groupSelected = {};
        }
        lastDevices = devices;
        var grid = document.getElementById('status-grid');

        devices.forEach(function(dev, i) {
            var card = document.getElementById('device-card-' + i);
            if (!card) {
                card = buildDeviceCard(i);
                grid.appendChild(card);
            }
            populateDeviceCard(i, dev);
        });

        // Remove stale cards
        Array.from(grid.querySelectorAll('.device-card'))
            .slice(devices.length)
            .forEach(function(c) { c.remove(); });

        _updateGroupPanel();

    } catch (err) {
        console.error('Status update failed:', err);
    }
}

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
          '</div>' +
          '<div class="device-mac" id="dmac-' + i + '"></div>' +
          '<div class="ts-sub" id="durl-' + i + '"></div>' +
        '</div>' +
        '<div class="device-rows">' +
          '<div>' +
            '<div class="status-label">Bluetooth</div>' +
            '<div class="status-value">' +
              '<span class="status-indicator" id="dbt-ind-' + i + '"></span>' +
              '<span id="dbt-txt-' + i + '">-</span>' +
            '</div>' +
            '<div class="ts" id="dbt-since-' + i + '"></div>' +
            '<div class="ts-sub" id="dbt-adapter-' + i + '"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Server</div>' +
            '<div class="status-value">' +
              '<span class="status-indicator" id="dsrv-ind-' + i + '"></span>' +
              '<span id="dsrv-txt-' + i + '">-</span>' +
            '</div>' +
            '<div class="ts" id="dsrv-since-' + i + '"></div>' +
            '<div class="ts-sub" id="dsrv-uri-' + i + '"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Playback</div>' +
            '<div class="status-value">' +
              '<span class="status-indicator" id="dplay-ind-' + i + '"></span>' +
              '<span id="dplay-' + i + '">-</span>' +
              '<button type="button" id="dbtn-pause-' + i + '" ' +
                'class="card-icon-btn" ' +
                'onclick="onDevicePause(' + i + ')" title="Pause/Unpause">&#9646;&#9646;</button>' +
            '</div>' +
            '<div class="ts" id="dplay-since-' + i + '"></div>' +
            '<div class="ts-sub" id="daudiofmt-' + i + '"></div>' +
          '</div>' +
          '<div>' +
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
          '</div>' +
          '<div>' +
            '<div class="status-label">Sync</div>' +
            '<div class="status-value" id="dsync-' + i + '">&#8212;</div>' +
            '<div class="ts" id="dsync-detail-' + i + '"></div>' +
            '<div class="ts" id="ddelay-' + i + '" style="display:none;color:#f59e0b;"></div>' +
          '</div>' +
        '</div>' +
        '<div class="device-card-actions">' +
          '<div style="grid-column:1/4;display:flex;gap:6px;align-items:center;">' +
            '<button type="button" class="btn-bt-action btn-bt-reconnect" id="dbtn-reconnect-' + i + '"' +
              ' onclick="btReconnect(' + i + ')">&#128260; Reconnect</button>' +
            '<button type="button" class="btn-bt-action btn-bt-pair" id="dbtn-pair-' + i + '"' +
              ' onclick="btPair(' + i + ')" title="Put the device into pairing mode first">&#128279; Re-pair</button>' +
            '<button type="button" class="btn-bt-action btn-bt-release" id="dbtn-release-' + i + '"' +
              ' onclick="btToggleManagement(' + i + ')">🔓 Release</button>' +
            '<span class="bt-action-status" id="dbt-action-status-' + i + '"></span>' +
          '</div>' +
          '<div id="dtrack-' + i + '" class="device-track-info" style="color:#94a3b8;font-style:italic;font-size:13px;"></div>' +
        '</div>';
    return card;
}

function populateDeviceCard(i, dev) {
    var name = dev.player_name || ('Device ' + (i + 1));
    document.getElementById('dname-' + i).textContent = name;

    var mac = dev.bluetooth_mac || '';
    document.getElementById('dmac-' + i).textContent = mac ? 'MAC: ' + mac : '';

    var btAdapterEl = document.getElementById('dbt-adapter-' + i);
    if (btAdapterEl) {
        var parts = [];
        if (dev.bluetooth_adapter_hci) parts.push(dev.bluetooth_adapter_hci);
        if (dev.bluetooth_adapter) parts.push(dev.bluetooth_adapter);
        btAdapterEl.textContent = parts.join(' ');
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
    var btSince = document.getElementById('dbt-since-' + i);
    if (dev.bluetooth_connected) {
        btInd.className = 'status-indicator active';
        btTxt.textContent = 'Connected';
    } else if (dev.reconnecting) {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Reconnecting\u2026' +
            (dev.reconnect_attempt ? ' (' + dev.reconnect_attempt + ')' : '');
    } else if (dev.bluetooth_available) {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Disconnected';
    } else {
        btInd.className = 'status-indicator inactive';
        btTxt.textContent = 'Not Available';
    }
    if (btSince) btSince.textContent = dev.bluetooth_connected_at
        ? 'Since: ' + new Date(dev.bluetooth_connected_at).toLocaleString() : '';

    // Server
    var srvInd   = document.getElementById('dsrv-ind-' + i);
    var srvTxt   = document.getElementById('dsrv-txt-' + i);
    var srvSince = document.getElementById('dsrv-since-' + i);
    if (dev.server_connected) {
        srvInd.className = 'status-indicator active';
        srvTxt.textContent = 'Connected';
    } else {
        srvInd.className = 'status-indicator inactive';
        srvTxt.textContent = dev.error || 'Disconnected';
    }
    if (srvSince) srvSince.textContent = dev.server_connected_at
        ? 'Since: ' + new Date(dev.server_connected_at).toLocaleString() : '';
    var srvUri = document.getElementById('dsrv-uri-' + i);
    if (srvUri) {
        var srvLabel = '';
        if (dev.server_connected) {
            var h = dev.server_host || '';
            if (h && !['auto','discover',''].includes(h.toLowerCase())) {
                srvLabel = h + ':' + (dev.server_port || 9000);
            } else if (dev.connected_server_url) {
                var m = dev.connected_server_url.match(/^wss?:\/\/([^\/]+)/);
                if (m) srvLabel = m[1];
            }
        }
        srvUri.textContent = srvLabel;
    }

    // Playback
    var playInd   = document.getElementById('dplay-ind-' + i);
    var playTxt   = document.getElementById('dplay-' + i);
    var playSince = document.getElementById('dplay-since-' + i);
    var fmtEl     = document.getElementById('daudiofmt-' + i);

    // Color indicator: red=no sink (BT not ready), green=playing, yellow=stopped
    if (!dev.has_sink && dev.bluetooth_mac) {
        if (playInd) playInd.className = 'status-indicator inactive';
        if (playTxt) playTxt.textContent = 'No Sink';
    } else if (dev.playing) {
        if (playInd) playInd.className = 'status-indicator active';
        if (playTxt) playTxt.textContent = '\u25b6 Playing';
    } else {
        if (playInd) playInd.className = 'status-indicator warning';
        if (playTxt) playTxt.textContent = '\u23f8 Stopped';
    }

    // Since: above audioformat
    if (playSince) playSince.textContent = dev.state_changed_at
        ? 'Since: ' + new Date(dev.state_changed_at).toLocaleString() : '';

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
    }

    var trackEl = document.getElementById('dtrack-' + i);
    if (trackEl) {
        if (dev.playing && (dev.current_artist || dev.current_track)) {
            trackEl.textContent = dev.current_artist && dev.current_track
                ? dev.current_artist + ' \u2014 ' + dev.current_track
                : (dev.current_artist || dev.current_track || '');
        } else {
            trackEl.textContent = '';
        }
    }
    // Delay badge
    var delayEl = document.getElementById('ddelay-' + i);
    if (delayEl) {
        var delay = dev.static_delay_ms;
        if (delay !== undefined && delay !== null && delay !== 0) {
            delayEl.textContent = 'delay: ' + (delay > 0 ? '+' : '') + delay + 'ms';
            delayEl.style.display = '';
        } else {
            delayEl.style.display = 'none';
        }
    }

    // Sync
    var syncEl = document.getElementById('dsync-' + i);
    var syncDetail = document.getElementById('dsync-detail-' + i);
    if (syncEl) {
        if (!dev.playing) {
            syncEl.textContent = '\u2014';
            syncEl.style.color = '#9ca3af';
            if (syncDetail) syncDetail.textContent = '';
        } else if (dev.reanchoring) {
            syncEl.innerHTML = '<span style="color:#f59e0b;">&#9888; Re-anchoring</span>';
            if (syncDetail) syncDetail.textContent = dev.last_sync_error_ms
                ? 'Error: ' + dev.last_sync_error_ms.toFixed(1) + ' ms' : '';
        } else {
            syncEl.innerHTML = '<span style="color:#10b981;">&#10003; In sync</span>';
            if (syncDetail) syncDetail.textContent = dev.reanchor_count
                ? 'Re-anchors: ' + dev.reanchor_count : '';
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

    // Mute button — attach handler once, update icon on every poll
    var muteBtn = document.getElementById('dmute-' + i);
    if (muteBtn) {
        muteBtn.textContent = dev.muted ? '🔇' : '🔈';
        muteBtn.title = dev.muted ? 'Unmute' : (hasSink ? 'Mute' : 'Audio sink not configured');
        muteBtn.style.background = dev.muted ? '#fee2e2' : 'white';
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
                        btn.textContent = d.muted ? '🔇' : '🔈';
                        btn.title = d.muted ? 'Unmute' : 'Mute';
                        btn.style.background = d.muted ? '#fee2e2' : 'white';
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
        } else {
            relBtn.textContent = '🔒 Reclaim';
            relBtn.className = 'btn-bt-action btn-bt-reclaim';
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
    }
}

// ---- Logs ----

function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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

function _getSelectedNames() {
    var names = [];
    if (lastDevices) {
        lastDevices.forEach(function(dev, i) {
            if (_groupSelected[i] !== false) names.push(dev.player_name);
        });
    }
    return names;
}

function _updateGroupPanel() {
    var total = lastDevices ? lastDevices.length : 0;
    if (total < 2) {
        document.getElementById('group-controls').style.display = 'none';
        return;
    }
    document.getElementById('group-controls').style.display = 'flex';
    var sel = _getSelectedNames().length;
    var info = document.getElementById('group-select-info');
    if (info) info.textContent = sel === total ? 'All ' + total + ' players' : sel + ' of ' + total + ' selected';
    var allCb = document.getElementById('group-select-all');
    if (allCb) {
        allCb.checked = sel === total;
        allCb.indeterminate = sel > 0 && sel < total;
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
function onGroupVolumeInput(val) {
    var pct = document.getElementById('group-vol-pct');
    if (pct) pct.textContent = val + '%';
    clearTimeout(_groupVolTimer);
    _groupVolTimer = setTimeout(function() {
        var names = _getSelectedNames();
        if (!names.length) return;
        fetch(API_BASE + '/api/volume', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({volume: parseInt(val, 10), player_names: names})
        });
    }, 250);
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
    fetch(API_BASE + '/api/pause_all', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: action})
    }).then(function(r) { return r.json(); }).then(function() {
        if (btn) {
            if (action === 'pause') {
                btn.textContent = '\u25b6 Unpause All';
                btn.classList.add('paused');
            } else {
                btn.textContent = '\u23f8\u23f8 Pause All';
                btn.classList.remove('paused');
            }
        }
    });
}

function onDevicePause(i) {
    var dev = lastDevices && lastDevices[i];
    var btn = document.getElementById('dbtn-pause-' + i);
    var isPaused = btn && btn.classList.contains('paused');
    var action = isPaused ? 'play' : 'pause';
    fetch(API_BASE + '/api/pause', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: action, player_name: dev ? dev.player_name : null})
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
        if (status) status.textContent = d.success ? '\u2713 ' + (d.message || 'Started') : '\u2717 ' + (d.error || 'Failed');
    } catch (e) {
        if (status) status.textContent = '\u2717 Error';
    }
    setTimeout(function() {
        if (btn) btn.disabled = false;
        if (pairBtn) pairBtn.disabled = false;
        if (status) status.textContent = '';
    }, 8000);
}

async function btPair(i) {
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
        if (d.success) lastDevices[i].bt_management_enabled = newEnabled;
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
        btn.style.background = '#10b981';
        autoRefreshInterval = setInterval(refreshLogs, 2000);
        refreshLogs();
    } else {
        btn.textContent = 'Auto-Refresh: Off';
        btn.style.background = 'var(--primary-color)';
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

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort, enabled) {
    var tbody = document.getElementById('bt-devices-table');
    var row = document.createElement('div');
    row.className = 'bt-device-row';
    if (enabled === false) row.dataset.enabled = 'false';
    var delayVal = (delay !== undefined && delay !== null && delay !== '') ? delay : 0;
    var portVal  = (listenPort !== undefined && listenPort !== null && listenPort !== '') ? listenPort : '';
    row.innerHTML =
        '<input type="text" placeholder="Player Name" class="bt-name" value="' +
            escHtmlAttr(name || '') + '">' +
        '<input type="text" placeholder="AA:BB:CC:DD:EE:FF" class="bt-mac" value="' +
            escHtmlAttr(mac || '') + '">' +
        '<select class="bt-adapter">' + btAdapterOptions(adapter || '') + '</select>' +
        '<input type="text" class="bt-listen-host" placeholder="auto" title="IP address this player advertises/listens on. Leave blank to auto-detect." value="' +
            escHtmlAttr(listenHost || '') + '">' +
        '<input type="number" class="bt-listen-port" placeholder="8928" title="Port this player listens on (default: 8928, 8929 for 2nd…)" value="' +
            escHtmlAttr(String(portVal)) + '" min="1024" max="65535">' +
        '<input type="number" class="bt-delay" title="Static delay (ms). Negative = compensate for output latency. Typical BT A2DP: -500" value="' +
            escHtmlAttr(String(delayVal)) + '" step="50">' +
        '<button type="button" class="btn-remove-dev">\u00d7</button>';

    row.querySelector('.btn-remove-dev').addEventListener('click', function() {
        this.closest('.bt-device-row').remove();
    });
    row.querySelector('.bt-mac').addEventListener('input', function() {
        var v = this.value.trim();
        var valid = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/.test(v);
        this.classList.toggle('invalid', v !== '' && !valid);
    });
    tbody.appendChild(row);
}

function collectBtDevices() {
    var devices = [];
    document.querySelectorAll('#bt-devices-table .bt-device-row').forEach(function(row) {
        var name       = row.querySelector('.bt-name').value.trim();
        var mac        = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var adapter    = row.querySelector('.bt-adapter').value;
        var listenHost = (row.querySelector('.bt-listen-host') || {}).value || '';
        var portEl     = row.querySelector('.bt-listen-port');
        var listenPort = portEl && portEl.value.trim() ? parseInt(portEl.value, 10) : null;
        var delayEl    = row.querySelector('.bt-delay');
        var delay      = delayEl ? parseFloat(delayEl.value) : 0;
        if (isNaN(delay)) delay = 0;
        var dev = { mac: mac, adapter: adapter, player_name: name, static_delay_ms: delay };
        if (listenHost) dev.listen_host = listenHost;
        if (listenPort) dev.listen_port = listenPort;
        // Preserve enabled flag: live status takes precedence, then config-loaded value from dataset
        var livedev = lastDevices && lastDevices.find(function(d) {
            return d.player_name === name || d.bluetooth_mac === mac;
        });
        if (livedev) {
            if (livedev.bt_management_enabled === false) dev.enabled = false;
        } else if (row.dataset.enabled === 'false') {
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
                       d.static_delay_ms, d.listen_host, d.listen_port, d.enabled);
    });
}

// ---- BT Scan ----

async function startBtScan() {
    var btn     = document.getElementById('scan-btn');
    var status  = document.getElementById('scan-status');
    var box     = document.getElementById('scan-results-box');
    var listDiv = document.getElementById('scan-results-list');

    btn.disabled = true;
    status.textContent = '🔄 Scanning\u2026 (~10s)';
    box.style.display = 'none';

    try {
        var resp = await fetch(API_BASE + '/api/bt/scan', { method: 'POST' });
        var data = await resp.json();
        var devices = data.devices || [];

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
        var qs = (showAll && showAll.checked) ? '?filter=0' : '';
        var resp = await fetch(API_BASE + '/api/bt/paired' + qs);
        var data = await resp.json();
        var devices = data.devices || [];
        var box = document.getElementById('paired-box');
        var listDiv = document.getElementById('paired-list');
        if (devices.length === 0) { box.style.display = 'none'; return; }
        box.style.display = 'block';
        listDiv.innerHTML = devices.map(function(d, i) {
            return '<div class="scan-result-item">' +
                '<span class="scan-result-mac">' + escHtml(d.mac) + '</span>' +
                '<span>' + escHtml(d.name) + '</span>' +
                '<button type="button" data-paired-idx="' + i + '" style="margin-left:auto;padding:3px 10px;' +
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
        box.style.display = 'block';
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

document.getElementById('config-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    try {
        var ok = await saveConfig();
        if (ok) {
            alert('Configuration saved! Use \u201cSave & Restart\u201d or restart the service for changes to take effect.');
        } else {
            alert('Failed to save configuration.');
        }
    } catch (err) {
        alert('Error saving configuration: ' + err.message);
    }
});

async function loadConfig() {
    try {
        var resp = await fetch(API_BASE + '/api/config');
        if (resp.status === 401) { _handleUnauthorized(); return; }
        var config = await resp.json();

        // Populate simple fields
        ['SENDSPIN_SERVER', 'SENDSPIN_PORT', 'BRIDGE_NAME', 'TZ', 'PULSE_LATENCY_MSEC',
         'BT_CHECK_INTERVAL', 'BT_MAX_RECONNECT_FAILS'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key];
        });
        // Populate checkboxes
        var sbcCheck = document.getElementById('prefer-sbc-codec');
        if (sbcCheck) sbcCheck.checked = !!config.PREFER_SBC_CODEC;
        var authCheck = document.getElementById('auth-enabled');
        if (authCheck) authCheck.checked = !!config.AUTH_ENABLED;
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
setInterval(updateTzPreview, 1000);

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
        (sinks.length > 0 ? escHtml(sinks.join(', ')) : 'None') +
        '</td></tr>';

    (d.devices || []).forEach(function(dev) {
        rows += '<tr><td>' + escHtml(dev.name || dev.mac || 'Unknown') + '</td><td>' +
            dot(dev.connected) + (dev.connected ? 'Connected' : 'Disconnected') +
            (dev.sink ? ' <span style="color:#6b7280;font-family:monospace;font-size:11px;">' +
                escHtml(dev.sink) + '</span>' : '') +
            (dev.last_error
                ? '<br><span style="color:#ef4444;font-size:11px;">' +
                  escHtml(dev.last_error) + '</span>' : '') +
            '</td></tr>';
    });

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

// ---- Init ----
loadConfig();   // calls loadBtAdapters() internally after restoring btManualAdapters
updateStatus();
setInterval(updateStatus, 2000);
refreshLogs();
loadVersionInfo();
