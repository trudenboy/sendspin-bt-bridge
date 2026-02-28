#!/usr/bin/env python3
"""
Web Interface for Sendspin Client
Provides configuration and monitoring UI
"""

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request
from flask_cors import CORS
from waitress import serve

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Version information
VERSION = "1.0.0"
BUILD_DATE = "2026-01-01"

# Configuration file path
CONFIG_DIR = Path(os.getenv('CONFIG_DIR', '/config'))
CONFIG_FILE = CONFIG_DIR / 'config.json'

# Default configuration
DEFAULT_CONFIG = {
    'SENDSPIN_NAME': 'Sendspin-Player',
    'SENDSPIN_SERVER': 'auto',
    'BLUETOOTH_MAC': '',
    'BLUETOOTH_DEVICES': [],
    'TZ': 'Australia/Melbourne',
}

# Global clients list will be set by main
_clients: list = []


def set_client(client):
    """Set a single client reference (backward compat)"""
    global _clients
    _clients = [client]
    logger.info(f"Client reference set in web interface: {client}")


def set_clients(clients):
    """Set multiple client references"""
    global _clients
    _clients = clients if clients else []
    logger.info(f"Client references set in web interface: {len(_clients)} client(s)")


def _detect_runtime() -> str:
    """Detect whether running under systemd (LXC) or Docker"""
    if os.path.exists('/etc/systemd/system/sendspin-client.service'):
        return 'systemd'
    if os.path.exists('/run/systemd/system/sendspin-client.service'):
        return 'systemd'
    return 'docker'


def get_client_status_for(client):
    """Get status dict for a specific client"""
    try:
        if client is None:
            return {
                'connected': False, 'server_connected': False,
                'bluetooth_connected': False, 'bluetooth_available': False,
                'playing': False, 'error': 'Client not running',
                'version': VERSION, 'build_date': BUILD_DATE, 'bluetooth_mac': None,
            }

        if not hasattr(client, 'status'):
            return {
                'connected': False, 'server_connected': False,
                'bluetooth_connected': False, 'bluetooth_available': False,
                'playing': False, 'error': 'Client initializing',
                'version': VERSION, 'build_date': BUILD_DATE, 'bluetooth_mac': None,
            }

        status = client.status.copy()

        if 'uptime_start' in status:
            uptime = datetime.now() - status['uptime_start']
            status['uptime'] = str(timedelta(seconds=int(uptime.total_seconds())))
            del status['uptime_start']

        status['version'] = VERSION
        status['build_date'] = BUILD_DATE
        status['connected'] = client.process.poll() is None if client.process else False
        status['player_name'] = getattr(client, 'player_name', None)

        bt_mgr = getattr(client, 'bt_manager', None)
        status['bluetooth_mac'] = bt_mgr.mac_address if bt_mgr else None

        logger.debug(f"Status retrieved: {status}")
        return status

    except Exception as e:
        logger.error(f"Error getting client status: {e}", exc_info=True)
        return {
            'connected': False, 'server_connected': False,
            'bluetooth_connected': False, 'bluetooth_available': False,
            'playing': False, 'error': str(e),
            'version': VERSION, 'build_date': BUILD_DATE, 'bluetooth_mac': None,
        }


def get_client_status():
    """Get status from the first client (backward compat)"""
    if not _clients:
        return {
            'connected': False, 'server_connected': False,
            'bluetooth_connected': False, 'bluetooth_available': False,
            'playing': False, 'error': 'No clients',
            'version': VERSION, 'build_date': BUILD_DATE, 'bluetooth_mac': None,
        }
    return get_client_status_for(_clients[0])


def get_version_info():
    return {'VERSION': VERSION, 'BUILD_DATE': BUILD_DATE}


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sendspin Client</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                         'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }

        /* Header */
        .header {
            background: white; border-radius: 10px; padding: 20px;
            margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            display: flex; justify-content: space-between; align-items: center;
        }
        .header h1 { color: #667eea; font-size: 28px; }
        .version-info { text-align: right; font-size: 12px; color: #666; }

        /* Restart banner */
        .restart-banner {
            border-radius: 8px; padding: 12px 20px; margin-bottom: 20px;
            font-size: 15px; font-weight: 500; text-align: center; display: none;
        }
        .restart-banner.restarting { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
        .restart-banner.online     { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
        .restart-banner.warning    { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }

        /* Status grid */
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .status-card, .device-card {
            background: white; border-radius: 10px; padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .device-card-title {
            font-size: 16px; font-weight: 700; color: #667eea; margin-bottom: 2px;
        }
        .device-mac {
            font-size: 11px; color: #9ca3af; font-family: 'Courier New', monospace;
            margin-bottom: 14px;
        }
        .device-rows { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .device-rows .status-label { font-size: 12px; color: #666; margin-bottom: 3px; }
        .device-rows .status-value { font-size: 14px; font-weight: 600; color: #333; }

        /* Status indicators */
        .status-indicator {
            display: inline-block; width: 10px; height: 10px;
            border-radius: 50%; margin-right: 6px; flex-shrink: 0;
        }
        .status-indicator.active   { background: #10b981; box-shadow: 0 0 8px #10b981; }
        .status-indicator.inactive { background: #ef4444; }

        /* Volume slider */
        .volume-row { display: flex; align-items: center; gap: 6px; }
        .volume-slider { flex: 1; height: 4px; accent-color: #667eea; cursor: pointer; }
        .volume-pct { min-width: 36px; text-align: right; font-size: 13px; color: #555; }

        /* Config section */
        .config-section {
            background: white; border-radius: 10px; padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px;
        }
        .config-section h2 { color: #667eea; margin-bottom: 15px; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #333; font-weight: 500; }
        .form-group input {
            width: 100%; padding: 10px; border: 2px solid #e5e7eb;
            border-radius: 5px; font-size: 14px;
        }
        .form-group input:focus { outline: none; border-color: #667eea; }
        .form-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 15px; }

        /* BT device table */
        .bt-header {
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 30px;
            gap: 8px; margin-bottom: 4px; padding: 0 2px;
            font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase;
        }
        .bt-device-row {
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 30px;
            gap: 8px; align-items: center; margin-bottom: 8px;
        }
        .bt-device-row input, .bt-device-row select {
            padding: 7px 10px; border: 2px solid #e5e7eb;
            border-radius: 5px; font-size: 13px; background: white; width: 100%;
        }
        .bt-device-row input:focus, .bt-device-row select:focus {
            outline: none; border-color: #667eea;
        }
        .bt-device-row input.invalid { border-color: #ef4444; background: #fef2f2; }
        .btn-remove-dev {
            background: #fee2e2; color: #ef4444; border: none; border-radius: 5px;
            width: 30px; height: 34px; cursor: pointer; font-size: 18px; line-height: 1;
        }
        .btn-remove-dev:hover { background: #fecaca; }
        .bt-toolbar { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
        .scan-badge {
            font-size: 13px; color: #6b7280;
            display: flex; align-items: center; gap: 4px;
        }
        .scan-results-box {
            margin-top: 10px; border: 1px solid #e5e7eb; border-radius: 6px;
            padding: 10px; background: #f9fafb; display: none;
        }
        .scan-results-title { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
        .scan-result-item {
            display: flex; align-items: center; gap: 10px;
            padding: 5px 4px; border-bottom: 1px solid #f3f4f6; font-size: 13px;
        }
        .scan-result-item:last-child { border-bottom: none; }
        .scan-result-mac { font-family: monospace; color: #9ca3af; font-size: 12px; }

        /* Buttons */
        .btn {
            background: #667eea; color: white; padding: 10px 20px;
            border: none; border-radius: 5px; font-size: 15px;
            cursor: pointer; transition: background 0.2s;
        }
        .btn:hover { background: #5568d3; }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-sm { padding: 7px 14px; font-size: 13px; }
        .btn-restart { background: #f59e0b; }
        .btn-restart:hover { background: #d97706; }
        .btn-refresh { background: #10b981; }
        .btn-refresh:hover { background: #059669; }
        .btn-scan { background: #8b5cf6; }
        .btn-scan:hover { background: #7c3aed; }

        /* Logs section */
        .logs-section {
            background: white; border-radius: 10px; padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .logs-section h2 { color: #667eea; margin-bottom: 15px; }
        .logs-toolbar {
            display: flex; align-items: center; gap: 10px;
            flex-wrap: wrap; margin-bottom: 12px;
        }
        .logs-container {
            background: #1e293b; color: #e2e8f0; padding: 15px; border-radius: 5px;
            font-family: 'Courier New', monospace; font-size: 12px;
            max-height: 400px; overflow-y: auto;
        }
        .log-line { margin-bottom: 3px; line-height: 1.4; word-break: break-all; }
        .log-error   { color: #f87171; }
        .log-warning { color: #fbbf24; }
        .log-info    { color: #e2e8f0; }
        .log-debug   { color: #6b7280; }
        .log-filter { display: inline-flex; gap: 4px; }
        .filter-btn {
            background: #374151; color: #9ca3af; padding: 4px 12px;
            border: none; border-radius: 4px; cursor: pointer; font-size: 13px;
            transition: background 0.2s;
        }
        .filter-btn:hover { background: #4b5563; color: white; }
        .filter-btn.active { background: #667eea; color: white; }
        .ts { font-size: 11px; color: #9ca3af; margin-top: 3px; }

        /* Diagnostics section */
        .diag-section {
            background: white; border-radius: 10px; padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px;
        }
        .diag-section summary {
            color: #667eea; font-size: 20px; font-weight: 700;
            cursor: pointer; list-style: none; user-select: none;
        }
        .diag-section summary::-webkit-details-marker { display: none; }
        .diag-section summary::before {
            content: '\25B6';
            display: inline-block; font-size: 13px; margin-right: 8px;
            transition: transform 0.2s;
        }
        .diag-section[open] summary::before { transform: rotate(90deg); }
        .diag-table { width: 100%; border-collapse: collapse; margin-top: 14px; }
        .diag-table th {
            font-size: 11px; font-weight: 600; color: #6b7280;
            text-transform: uppercase; padding: 4px 8px; text-align: left;
        }
        .diag-table td { padding: 7px 8px; border-top: 1px solid #f3f4f6; font-size: 13px; }
        .diag-dot {
            display: inline-block; width: 9px; height: 9px;
            border-radius: 50%; margin-right: 6px; vertical-align: middle;
        }
        .diag-dot.ok   { background: #10b981; box-shadow: 0 0 6px #10b981; }
        .diag-dot.err  { background: #ef4444; }
        .diag-dot.warn { background: #f59e0b; }

        /* Timezone preview */
        .tz-preview { font-size: 12px; color: #6b7280; white-space: nowrap; }
    </style>
</head>
<body>
<div class="container">

    <!-- Header -->
    <div class="header">
        <h1>&#127925; Sendspin Client</h1>
        <div class="version-info" id="version-display">
            <div>{{ VERSION }}</div>
            <div>{{ BUILD_DATE }}</div>
        </div>
    </div>

    <!-- Restart banner -->
    <div id="restart-banner" class="restart-banner"></div>

    <!-- Status grid: system info + device cards (populated by JS) -->
    <div class="status-grid" id="status-grid">
        <div class="status-card">
            <div class="status-label" style="font-size:14px;color:#666;margin-bottom:8px;">System</div>
            <div id="container-info" style="font-size:13px;color:#333;line-height:1.7;">Loading&#8230;</div>
        </div>
    </div>

    <!-- Configuration -->
    <div class="config-section">
        <h2>&#9881;&#65039; Configuration</h2>
        <form id="config-form">
            <div class="form-group">
                <label>Player Name</label>
                <input type="text" name="SENDSPIN_NAME" required>
            </div>
            <div class="form-group">
                <label>Server (use &#8216;auto&#8217; for mDNS discovery)</label>
                <input type="text" name="SENDSPIN_SERVER" required>
            </div>

            <div class="form-group">
                <label>Timezone</label>
                <div style="display:flex;gap:10px;align-items:center;">
                    <input type="text" name="TZ" placeholder="Australia/Melbourne" style="flex:1;"
                        oninput="updateTzPreview()">
                    <span id="tz-preview" class="tz-preview"></span>
                </div>
            </div>

            <!-- BT devices table (replaces raw JSON textarea + single MAC input) -->
            <div class="form-group">
                <label>Bluetooth Devices</label>
                <div class="bt-header">
                    <span>Player Name</span><span>MAC Address</span>
                    <span>Adapter</span><span></span>
                </div>
                <div id="bt-devices-table"></div>
                <div class="bt-toolbar">
                    <button type="button" id="add-dev-btn" onclick="addBtDeviceRow()" class="btn btn-sm">+ Add Device</button>
                    <button type="button" onclick="startBtScan()" class="btn btn-sm btn-scan" id="scan-btn">
                        &#128269; Scan
                    </button>
                    <span id="scan-status" class="scan-badge"></span>
                </div>
                <div id="scan-results-box" class="scan-results-box">
                    <div class="scan-results-title">Discovered devices &#8212; click to add:</div>
                    <div id="scan-results-list"></div>
                </div>
            </div>

            <div class="form-actions">
                <button type="submit" class="btn">Save Configuration</button>
                <button type="button" onclick="saveAndRestart()" class="btn btn-restart">
                    Save &amp; Restart
                </button>
            </div>
        </form>
    </div>

    <!-- Diagnostics (collapsible) -->
    <details class="diag-section" id="diag-details" ontoggle="onDiagToggle(this)">
        <summary>Diagnostics</summary>
        <div id="diag-content"></div>
    </details>

    <!-- Logs -->
    <div class="logs-section">
        <h2>&#128203; Logs</h2>
        <div class="logs-toolbar">
            <button onclick="refreshLogs()" class="btn btn-refresh">Refresh Logs</button>
            <button onclick="toggleAutoRefresh()" class="btn" id="auto-refresh-btn">Auto-Refresh: Off</button>
            <div class="log-filter">
                <button onclick="setLogLevel('all')"     id="filter-all"     class="filter-btn active">All</button>
                <button onclick="setLogLevel('error')"   id="filter-error"   class="filter-btn">Error</button>
                <button onclick="setLogLevel('warning')" id="filter-warning" class="filter-btn">Warning</button>
                <button onclick="setLogLevel('info')"    id="filter-info"    class="filter-btn">Info</button>
            </div>
        </div>
        <div class="logs-container" id="logs"></div>
    </div>

</div>

<script>
// ---- State ----
var autoRefreshLogs = false;
var autoRefreshInterval = null;
var allLogs = [];
var currentLogLevel = 'all';
var btAdapters = [];
var lastDevices = [];
var volTimers = {};
var volPending = {}; // deviceIndex -> true if user recently touched slider

// ---- Status ----

async function updateStatus() {
    try {
        var resp = await fetch('/api/status');
        var status = await resp.json();

        var info = [];
        if (status.hostname)   info.push('Host: ' + status.hostname);
        if (status.ip_address) info.push('IP: ' + status.ip_address);
        if (status.uptime)     info.push('Uptime: ' + status.uptime);
        document.getElementById('container-info').innerHTML =
            info.length ? info.join('<br>') : '&mdash;';

        var devices = status.devices || [status];
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

    } catch (err) {
        console.error('Status update failed:', err);
    }
}

function buildDeviceCard(i) {
    var card = document.createElement('div');
    card.className = 'device-card';
    card.id = 'device-card-' + i;
    card.innerHTML =
        '<div class="device-card-title" id="dname-' + i + '">Device ' + (i+1) + '</div>' +
        '<div class="device-mac" id="dmac-' + i + '"></div>' +
        '<div class="device-rows">' +
          '<div>' +
            '<div class="status-label">Bluetooth</div>' +
            '<div class="status-value">' +
              '<span class="status-indicator" id="dbt-ind-' + i + '"></span>' +
              '<span id="dbt-txt-' + i + '">-</span>' +
            '</div>' +
            '<div class="ts" id="dbt-since-' + i + '"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Server</div>' +
            '<div class="status-value">' +
              '<span class="status-indicator" id="dsrv-ind-' + i + '"></span>' +
              '<span id="dsrv-txt-' + i + '">-</span>' +
            '</div>' +
            '<div class="ts" id="dsrv-since-' + i + '"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Playback</div>' +
            '<div class="status-value" id="dplay-' + i + '">-</div>' +
            '<div class="ts" id="dtrack-' + i + '"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Volume</div>' +
            '<div class="volume-row">' +
              '<input type="range" min="0" max="100" value="100" ' +
                'class="volume-slider" id="vslider-' + i + '" ' +
                'oninput="onVolumeInput(' + i + ', this.value)">' +
              '<span class="volume-pct" id="dvol-' + i + '">100%</span>' +
            '</div>' +
          '</div>' +
        '</div>';
    return card;
}

function populateDeviceCard(i, dev) {
    var name = dev.player_name || ('Device ' + (i + 1));
    document.getElementById('dname-' + i).textContent = name;

    var mac = dev.bluetooth_mac || '';
    document.getElementById('dmac-' + i).textContent = mac ? 'MAC: ' + mac : '';

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

    // Playback
    document.getElementById('dplay-' + i).textContent =
        dev.playing ? '\u25b6\ufe0f Playing' : '\u23f8\ufe0f Stopped';
    var trackEl = document.getElementById('dtrack-' + i);
    if (trackEl) trackEl.textContent = dev.current_track || '';

    // Volume — only update if user isn't actively adjusting this slider
    if (dev.volume !== undefined && !volPending[i]) {
        var slider = document.getElementById('vslider-' + i);
        var volEl  = document.getElementById('dvol-' + i);
        if (slider) slider.value = dev.volume;
        if (volEl)  volEl.textContent = dev.volume + '%';
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
        delete volPending[i];
        sendVolume(i, parseInt(val, 10));
    }, 300);
}

async function sendVolume(deviceIndex, vol) {
    var dev = lastDevices[deviceIndex] || {};
    try {
        await fetch('/api/volume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ volume: vol, player_name: dev.player_name || null }),
        });
    } catch (err) {
        console.error('Volume set failed:', err);
    }
}

// ---- Logs ----

function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function getLogClass(line) {
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
        var resp = await fetch('/api/logs?lines=150');
        var data = await resp.json();
        allLogs = data.logs || [];
        renderLogs();
    } catch (err) {
        console.error('Error refreshing logs:', err);
    }
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
        btn.style.background = '#667eea';
        clearInterval(autoRefreshInterval);
    }
}

// ---- BT Device Table ----

async function loadBtAdapters() {
    try {
        var resp = await fetch('/api/bt/adapters');
        var data = await resp.json();
        btAdapters = data.adapters || [];
    } catch (_) { btAdapters = []; }
}

function btAdapterOptions(selected) {
    var opts = '<option value="">default</option>';
    btAdapters.forEach(function(a) {
        var label = a.id + (a.mac ? ' \u2014 ' + a.mac : '');
        opts += '<option value="' + a.id + '"' +
            (selected === a.id ? ' selected' : '') + '>' + label + '</option>';
    });
    return opts;
}

function addBtDeviceRow(name, mac, adapter) {
    var tbody = document.getElementById('bt-devices-table');
    var row = document.createElement('div');
    row.className = 'bt-device-row';
    row.innerHTML =
        '<input type="text" placeholder="Player Name" class="bt-name" value="' +
            escHtml(name || '') + '">' +
        '<input type="text" placeholder="AA:BB:CC:DD:EE:FF" class="bt-mac" value="' +
            escHtml(mac || '') + '">' +
        '<select class="bt-adapter">' + btAdapterOptions(adapter || '') + '</select>' +
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
        var name    = row.querySelector('.bt-name').value.trim();
        var mac     = row.querySelector('.bt-mac').value.trim().toUpperCase();
        var adapter = row.querySelector('.bt-adapter').value;
        if (mac) devices.push({ mac: mac, adapter: adapter, player_name: name });
    });
    return devices;
}

function populateBtDeviceRows(devices) {
    document.getElementById('bt-devices-table').innerHTML = '';
    devices.forEach(function(d) {
        addBtDeviceRow(d.player_name || '', d.mac || '', d.adapter || '');
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
        var resp = await fetch('/api/bt/scan', { method: 'POST' });
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
                        'background:#667eea;color:white;border:none;border-radius:4px;' +
                        'cursor:pointer;font-size:12px;">Add</button>' +
                    '</div>';
            }).join('');
            listDiv.querySelectorAll('[data-scan-idx]').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    var d = devices[parseInt(this.dataset.scanIdx)];
                    addFromScan(d.mac, d.name);
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

function addFromScan(mac, name) {
    addBtDeviceRow(name, mac, '');
    document.getElementById('scan-results-box').style.display = 'none';
    document.getElementById('scan-status').textContent = '';
}

// ---- Config ----

async function saveConfig() {
    var formData = new FormData(document.getElementById('config-form'));
    var config = Object.fromEntries(formData);

    // Collect BT devices from table rows (overrides anything from formData)
    config.BLUETOOTH_DEVICES = collectBtDevices();
    // Keep single BLUETOOTH_MAC for backward compat if exactly one device
    if (config.BLUETOOTH_DEVICES.length === 1) {
        config.BLUETOOTH_MAC = config.BLUETOOTH_DEVICES[0].mac;
    } else {
        config.BLUETOOTH_MAC = '';
    }

    try {
        var resp = await fetch('/api/config', {
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
        var resp = await fetch('/api/config');
        var config = await resp.json();

        // Populate simple fields
        ['SENDSPIN_NAME', 'SENDSPIN_SERVER', 'TZ'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key];
        });
        updateTzPreview();

        // Populate BT device table
        var devices = config.BLUETOOTH_DEVICES;
        if (devices && Array.isArray(devices) && devices.length > 0) {
            populateBtDeviceRows(devices);
        } else if (config.BLUETOOTH_MAC) {
            // Migrate single BLUETOOTH_MAC to table
            addBtDeviceRow(config.SENDSPIN_NAME || '', config.BLUETOOTH_MAC, '');
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
            await fetch('/api/restart', { method: 'POST' });
        } catch (_) { /* Service dropped connection — expected */ }

        await new Promise(function(r) { setTimeout(r, 2500); });

        for (var attempt = 1; attempt <= 30; attempt++) {
            banner.textContent = '🔄 Restarting\u2026 (' + attempt + 's)';
            await new Promise(function(r) { setTimeout(r, 1000); });
            try {
                var resp = await fetch('/api/status');
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

// ---- Version ----

async function loadVersionInfo() {
    try {
        var resp = await fetch('/api/version');
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
        var resp = await fetch('/api/diagnostics');
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
          'style="font-size:12px;background:none;border:none;color:#667eea;cursor:pointer;">' +
          '&#8635; Refresh</button></div>';
}

function reloadDiagnostics() {
    var content = document.getElementById('diag-content');
    delete content.dataset.loaded;
    loadDiagnostics(content);
}

// ---- Init ----
loadBtAdapters().then(function() {
    loadConfig();   // populate adapter dropdowns after adapters are loaded
});
updateStatus();
setInterval(updateStatus, 2000);
refreshLogs();
loadVersionInfo();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Render the main page"""
    return render_template_string(HTML_TEMPLATE, **get_version_info())


@app.route('/api/restart', methods=['POST'])
def api_restart():
    """Restart the service (systemd or Docker)"""
    runtime = _detect_runtime()
    try:
        if runtime == 'systemd':
            def _do_systemd():
                time.sleep(0.5)
                subprocess.run(
                    ['systemctl', 'restart', 'sendspin-client'],
                    capture_output=True, timeout=10
                )
            threading.Thread(target=_do_systemd, daemon=True).start()
        else:
            def _do_docker():
                time.sleep(0.5)
                try:
                    os.kill(1, signal.SIGTERM)
                except ProcessLookupError:
                    os.kill(os.getpid(), signal.SIGTERM)
            threading.Thread(target=_do_docker, daemon=True).start()

        return jsonify({'success': True, 'runtime': runtime})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/volume', methods=['POST'])
def set_volume():
    """Set player volume"""
    try:
        data = request.get_json()
        volume = max(0, min(100, int(data.get('volume', 100))))
        player_name = data.get('player_name')

        client = None
        if player_name:
            for c in _clients:
                if getattr(c, 'player_name', None) == player_name:
                    client = c
                    break
        if client is None and _clients:
            client = _clients[0]

        if client and client.bluetooth_sink_name:
            result = subprocess.run(
                ['pactl', 'set-sink-volume', client.bluetooth_sink_name, f'{volume}%'],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                client.status['volume'] = volume
                return jsonify({'success': True, 'volume': volume})
            else:
                return jsonify({'success': False, 'error': 'Failed to set volume'}), 500
        else:
            return jsonify({'success': False, 'error': 'Client not available'}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/status')
def api_status():
    """API endpoint for status"""
    if not _clients:
        return jsonify({'error': 'No clients'})
    if len(_clients) == 1:
        return jsonify(get_client_status_for(_clients[0]))
    first = get_client_status_for(_clients[0])
    result = {**first, 'devices': [get_client_status_for(c) for c in _clients]}
    return jsonify(result)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """API endpoint for configuration"""
    if request.method == 'GET':
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()
        return jsonify(config)

    elif request.method == 'POST':
        config = request.get_json()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return jsonify({'success': True})


@app.route('/api/logs')
def api_logs():
    """Return real service logs (journalctl or docker logs)"""
    lines = request.args.get('lines', 150, type=int)
    try:
        runtime = _detect_runtime()
        if runtime == 'systemd':
            result = subprocess.run(
                ['journalctl', '-u', 'sendspin-client',
                 '-n', str(lines), '--no-pager', '--output=short-iso'],
                capture_output=True, text=True, timeout=10
            )
            log_lines = result.stdout.splitlines()
            if not log_lines and result.stderr:
                log_lines = result.stderr.splitlines()
        else:
            result = subprocess.run(
                ['docker', 'logs', '--tail', str(lines), 'sendspin-client'],
                capture_output=True, text=True, timeout=10
            )
            log_lines = (result.stdout + result.stderr).splitlines()

        if not log_lines:
            log_lines = ['(No logs available)']

        return jsonify({'logs': log_lines, 'runtime': runtime})
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return jsonify({'logs': [f'Error reading logs: {e}']})


@app.route('/api/bt/adapters')
def api_bt_adapters():
    """List available Bluetooth adapters"""
    try:
        result = subprocess.run(
            ['bash', '-c', 'bluetoothctl list 2>/dev/null'],
            capture_output=True, text=True, timeout=5
        )
        adapters = []
        for i, line in enumerate(result.stdout.splitlines()):
            if 'Controller' not in line:
                continue
            parts = line.split()
            mac = next(
                (p for p in parts if len(p) == 17 and p.count(':') == 5), None
            )
            if mac:
                mac_idx = parts.index(mac)
                name_parts = [p for p in parts[mac_idx + 1:]
                              if p not in ('[default]', 'default')]
                name = ' '.join(name_parts).strip() or f'hci{i}'
                adapters.append({'id': f'hci{i}', 'mac': mac, 'name': name})
        return jsonify({'adapters': adapters})
    except Exception as e:
        return jsonify({'adapters': [], 'error': str(e)})


@app.route('/api/bt/scan', methods=['POST'])
def api_bt_scan():
    """Scan for nearby Bluetooth devices (~10 second scan)"""
    try:
        # Keep stdin open with sleep so bluetoothctl doesn't exit immediately
        # after processing commands — it exits on stdin EOF before discoveries arrive.
        result = subprocess.run(
            ['bash', '-c',
             '( printf "power on\\nagent on\\nscan on\\n"; sleep 10; printf "scan off\\n" )'
             ' | timeout 13 bluetoothctl 2>&1'],
            capture_output=True, text=True, timeout=16
        )
        devices = []
        seen: set = set()
        ansi_re = re.compile(r'\x1b\[[0-9;]*m')
        pattern = re.compile(r'\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)')
        for line in result.stdout.splitlines():
            clean = ansi_re.sub('', line)
            m = pattern.search(clean)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                if mac and mac not in seen:
                    seen.add(mac)
                    devices.append({'mac': mac, 'name': name or mac})
        return jsonify({'devices': devices})
    except Exception as e:
        logger.error(f"BT scan failed: {e}")
        return jsonify({'devices': [], 'error': str(e)})


@app.route('/api/diagnostics')
def api_diagnostics():
    """Return structured health diagnostics"""
    try:
        diag: dict = {}

        # Bluetooth daemon
        try:
            r = subprocess.run(
                ['systemctl', 'is-active', 'bluetooth'],
                capture_output=True, text=True, timeout=3
            )
            diag['bluetooth_daemon'] = r.stdout.strip() or 'unknown'
        except Exception:
            diag['bluetooth_daemon'] = 'unknown'

        # D-Bus socket
        dbus_env = os.environ.get('DBUS_SYSTEM_BUS_ADDRESS', '')
        dbus_path = dbus_env.replace('unix:path=', '') if dbus_env else '/run/dbus/system_bus_socket'
        diag['dbus_available'] = os.path.exists(dbus_path)

        # Bluetooth adapters
        try:
            r = subprocess.run(
                ['bash', '-c', 'bluetoothctl list 2>/dev/null'],
                capture_output=True, text=True, timeout=5
            )
            adapters = []
            for i, line in enumerate(r.stdout.splitlines()):
                if 'Controller' not in line:
                    continue
                parts = line.split()
                mac = next((p for p in parts if len(p) == 17 and p.count(':') == 5), '')
                adapters.append({
                    'id': f'hci{i}', 'mac': mac,
                    'default': 'default' in line.lower(),
                })
            diag['adapters'] = adapters
        except Exception as e:
            diag['adapters'] = [{'error': str(e)}]

        # PulseAudio / PipeWire
        try:
            r = subprocess.run(['pactl', 'info'], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                diag['pulseaudio'] = next(
                    (l.split(':', 1)[-1].strip() for l in r.stdout.splitlines()
                     if 'Server Name' in l),
                    'running'
                )
            else:
                diag['pulseaudio'] = 'not available'
        except Exception:
            diag['pulseaudio'] = 'not available'

        # BT audio sinks
        try:
            r = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True, text=True, timeout=3
            )
            diag['sinks'] = [
                l.split()[1] for l in r.stdout.splitlines()
                if 'bluez' in l.lower() and len(l.split()) > 1
            ]
        except Exception:
            diag['sinks'] = []

        # Per-device status (from cached status — fast, no bluetoothctl calls)
        device_diag = []
        for client in _clients:
            bt_mgr = getattr(client, 'bt_manager', None)
            device_diag.append({
                'name': getattr(client, 'player_name', 'Unknown'),
                'mac': bt_mgr.mac_address if bt_mgr else None,
                'connected': client.status.get('bluetooth_connected', False),
                'sink': getattr(client, 'bluetooth_sink_name', None),
                'last_error': client.status.get('last_error'),
            })
        diag['devices'] = device_diag

        return jsonify(diag)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/version')
def api_version():
    """Return git version information"""
    cwd = os.path.dirname(os.path.abspath(__file__))
    try:
        git_sha = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=3, cwd=cwd
        ).stdout.strip()
        git_desc = subprocess.run(
            ['git', 'describe', '--tags', '--always'],
            capture_output=True, text=True, timeout=3, cwd=cwd
        ).stdout.strip()
        git_date = subprocess.run(
            ['git', 'log', '-1', '--format=%ci'],
            capture_output=True, text=True, timeout=3, cwd=cwd
        ).stdout.strip()
        return jsonify({
            'version': git_desc or VERSION,
            'git_sha': git_sha or 'unknown',
            'built_at': (git_date.split(' ')[0] if git_date else BUILD_DATE),
        })
    except Exception:
        return jsonify({'version': VERSION, 'git_sha': 'unknown', 'built_at': BUILD_DATE})


def main():
    """Start the web interface"""
    port = int(os.getenv('WEB_PORT', 8080))
    logger.info(f"Starting web interface on port {port}")
    serve(app, host='0.0.0.0', port=port, threads=4)


if __name__ == '__main__':
    main()
