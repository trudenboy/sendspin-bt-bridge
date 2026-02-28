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
VERSION = "1.2.1"
BUILD_DATE = "2026-02-28"

# Configuration file path
CONFIG_DIR = Path(os.getenv('CONFIG_DIR', '/config'))
CONFIG_FILE = CONFIG_DIR / 'config.json'

# Default configuration
DEFAULT_CONFIG = {

    'SENDSPIN_SERVER': 'auto',
    'BLUETOOTH_MAC': '',
    'BLUETOOTH_DEVICES': [],
    'TZ': 'Australia/Melbourne',
}

# Global clients list will be set by main
_clients: list = []


def set_clients(clients):
    """Set multiple client references"""
    global _clients
    _clients = clients if clients else []
    logger.info(f"Client references set in web interface: {len(_clients)} client(s)")


_runtime_cache: str = ''

def _detect_runtime() -> str:
    """Detect whether running under systemd (LXC) or Docker. Result is cached."""
    global _runtime_cache
    if not _runtime_cache:
        if os.path.exists('/etc/systemd/system/sendspin-client.service'):
            _runtime_cache = 'systemd'
        elif os.path.exists('/run/systemd/system/sendspin-client.service'):
            _runtime_cache = 'systemd'
        else:
            _runtime_cache = 'docker'
    return _runtime_cache


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
        status['listen_port'] = getattr(client, 'listen_port', None)
        status['server_host'] = getattr(client, 'server_host', None)
        status['server_port'] = getattr(client, 'server_port', None)

        bt_mgr = getattr(client, 'bt_manager', None)
        status['bluetooth_mac'] = bt_mgr.mac_address if bt_mgr else None
        status['bluetooth_adapter'] = bt_mgr.adapter if bt_mgr else None
        status['has_sink'] = bool(getattr(client, 'bluetooth_sink_name', None))

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
    <title>Sendspin Bluetooth Bridge</title>
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
            display: flex; flex-direction: column;
            gap: 12px; margin-bottom: 20px;
        }
        .device-card {
            background: white; border-radius: 10px; padding: 16px 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            display: flex; align-items: center; gap: 0; flex-wrap: wrap;
        }
        .device-card-actions {
            width: 100%; display: flex; align-items: center; gap: 8px;
            padding-top: 10px; margin-top: 10px; border-top: 1px solid #f3f4f6;
            flex-wrap: wrap;
        }
        .btn-bt-action {
            padding: 4px 12px; font-size: 12px; font-weight: 600; border: none;
            border-radius: 5px; cursor: pointer; color: white; transition: opacity 0.15s;
        }
        .btn-bt-action:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-bt-reconnect { background: #667eea; }
        .btn-bt-reconnect:hover:not(:disabled) { background: #5a67d8; }
        .btn-bt-pair { background: #f59e0b; }
        .btn-bt-pair:hover:not(:disabled) { background: #d97706; }
        .bt-action-status { font-size: 12px; color: #6b7280; }
        /* Group controls */
        .group-controls {
            background: white; border-radius: 10px; padding: 12px 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.08); margin-bottom: 14px;
            display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
        }
        .group-controls-label {
            font-size: 12px; font-weight: 700; color: #667eea;
            text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap;
        }
        .group-select-info { font-size: 12px; color: #6b7280; white-space: nowrap; }
        .group-vol-row { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 180px; }
        .group-vol-slider { flex: 1; height: 4px; accent-color: #667eea; cursor: pointer; }
        .group-vol-pct { min-width: 36px; font-size: 13px; color: #555; text-align: right; }
        .btn-group-mute {
            padding: 4px 12px; font-size: 12px; font-weight: 600; border: 1px solid #d1d5db;
            border-radius: 5px; background: white; cursor: pointer;
        }
        .btn-group-mute.muted { background: #fee2e2; border-color: #fca5a5; }
        .device-select-cb { width: 15px; height: 15px; accent-color: #667eea; cursor: pointer; }
        .device-card-identity {
            min-width: 180px; max-width: 220px; padding-right: 20px;
            border-right: 1px solid #e5e7eb; margin-right: 0; flex-shrink: 0;
        }
        .device-card-title {
            font-size: 15px; font-weight: 700; color: #667eea; margin-bottom: 2px;
        }
        .device-mac {
            font-size: 11px; color: #9ca3af; font-family: 'Courier New', monospace;
        }
        .device-rows {
            display: flex; flex: 1; gap: 0;
        }
        .device-rows > div {
            flex: 1; padding: 0 16px; border-right: 1px solid #e5e7eb;
        }
        .device-rows > div:last-child { border-right: none; }
        .device-rows .status-label { font-size: 11px; color: #9ca3af; margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
        .device-rows .status-value { font-size: 13px; font-weight: 600; color: #333; }

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
        .config-section summary, .logs-section summary {
            color: #667eea; font-size: 20px; font-weight: 700;
            cursor: pointer; list-style: none; user-select: none; margin-bottom: 0;
        }
        .config-section summary::-webkit-details-marker,
        .logs-section summary::-webkit-details-marker { display: none; }
        .config-section summary::before, .logs-section summary::before {
            content: '\\25B6';
            display: inline-block; font-size: 13px; margin-right: 8px;
            transition: transform 0.2s;
        }
        .config-section[open] summary::before,
        .logs-section[open] summary::before { transform: rotate(90deg); }
        .config-section[open] summary, .logs-section[open] summary { margin-bottom: 15px; }
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
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 9rem 4.5rem 5.5rem 30px;
            gap: 8px; margin-bottom: 4px; padding: 0 2px;
            font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase;
        }
        .bt-device-row {
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 9rem 4.5rem 5.5rem 30px;
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

        /* Adapters panel */
        .adapters-card {
            border: 1px solid #e5e7eb; border-radius: 8px;
            margin-bottom: 18px; overflow: hidden;
        }
        .adapters-card-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 10px 14px; background: #f9fafb;
            border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #374151;
            font-size: 13px;
        }
        .adapters-card-header div { display: flex; gap: 8px; }
        .adapter-row {
            display: grid;
            grid-template-columns: 5rem 1fr 1.4fr 2.5rem 2rem;
            gap: 8px; align-items: center;
            padding: 7px 14px; border-bottom: 1px solid #f3f4f6; font-size: 13px;
        }
        .adapter-row:last-child { border-bottom: none; }
        .adapter-row.detected { color: #374151; }
        .adapter-row input {
            padding: 5px 8px; border: 1px solid #e5e7eb;
            border-radius: 4px; font-size: 13px; width: 100%;
        }
        .adapter-row input:focus { outline: none; border-color: #667eea; }
        .dot { font-size: 16px; text-align: center; }
        .dot.green { color: #10b981; }
        .dot.grey  { color: #9ca3af; }
        .mono { font-family: 'Courier New', monospace; font-size: 12px; color: #6b7280; }
        .btn-remove-adapter {
            background: #fee2e2; color: #ef4444; border: none; border-radius: 4px;
            width: 24px; height: 24px; cursor: pointer; font-size: 14px; line-height: 1;
        }
        .btn-remove-adapter:hover { background: #fecaca; }

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
            content: '\\25B6';
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
        <h1>&#127925; Sendspin Bluetooth Bridge</h1>
        <div style="text-align:right;">
            <div class="version-info" id="version-display">
                <div>{{ VERSION }}</div>
                <div>{{ BUILD_DATE }}</div>
            </div>
            <div id="system-info" style="font-size:12px;color:#9ca3af;margin-top:4px;line-height:1.6;"></div>
        </div>
    </div>

    <!-- Restart banner -->
    <div id="restart-banner" class="restart-banner"></div>

    <!-- Group Controls -->
    <div class="group-controls" id="group-controls" style="display:none;">
        <span class="group-controls-label">&#127922; Group</span>
        <span class="group-select-info" id="group-select-info">All players</span>
        <label style="display:flex;align-items:center;gap:5px;font-size:12px;color:#6b7280;cursor:pointer;">
            <input type="checkbox" id="group-select-all" checked onchange="onGroupSelectAll(this)">
            All
        </label>
        <div class="group-vol-row">
            <span style="font-size:11px;color:#9ca3af;font-weight:600;text-transform:uppercase;">Vol</span>
            <input type="range" min="0" max="100" value="50" class="group-vol-slider" id="group-vol-slider"
                oninput="onGroupVolumeInput(this.value)">
            <span class="group-vol-pct" id="group-vol-pct">50%</span>
        </div>
        <button type="button" class="btn-group-mute" id="group-mute-btn"
            onclick="onGroupMute()">&#128264; Mute All</button>
    </div>

    <!-- Status grid: device cards (populated by JS) -->
    <div class="status-grid" id="status-grid"></div>

    <!-- Configuration -->
    <details class="config-section" open>
        <summary>&#9881;&#65039; Configuration</summary>
        <form id="config-form">
            <div class="form-group">
                <label>Music Assistant server &mdash; IP/hostname, or <code>auto</code> to discover via mDNS</label>
                <input type="text" name="SENDSPIN_SERVER" placeholder="auto" required>
            </div>

            <div class="form-group">
                <label>Timezone</label>
                <div style="display:flex;gap:10px;align-items:center;">
                    <input type="text" name="TZ" id="tz-input" placeholder="Australia/Melbourne" style="flex:1;"
                        list="tz-list" autocomplete="off" oninput="updateTzPreview()">
                    <datalist id="tz-list"></datalist>
                    <span id="tz-preview" class="tz-preview"></span>
                </div>
            </div>

            <!-- Bluetooth Adapters panel -->
            <div class="form-group">
                <label>Bluetooth Adapters</label>
                <div class="adapters-card">
                    <div class="adapters-card-header">
                        <span>Detected &amp; manual adapters</span>
                        <div>
                            <button type="button" class="btn btn-sm btn-refresh" onclick="loadBtAdapters()">&#x21BA; Refresh</button>
                            <button type="button" class="btn btn-sm" onclick="addManualAdapterRow('','','')">+ Add</button>
                        </div>
                    </div>
                    <div id="adapters-table"></div>
                </div>
            </div>

            <!-- BT devices table (replaces raw JSON textarea + single MAC input) -->
            <div class="form-group">
                <label>Bluetooth Devices</label>
                <div class="bt-header">
                    <span>Player Name</span><span>MAC Address</span>
                    <span>Adapter</span><span>Listen Address</span><span>Port</span><span>Delay ms</span><span></span>
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
    </details>

    <!-- Diagnostics (collapsible) -->
    <details class="diag-section" id="diag-details" ontoggle="onDiagToggle(this)">
        <summary>Diagnostics</summary>
        <div id="diag-content"></div>
    </details>

    <!-- Logs -->
    <details class="logs-section">
        <summary>&#128203; Logs</summary>
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
    </details>

</div>

<script>
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

// ---- Status ----

async function updateStatus() {
    try {
        var resp = await fetch('/api/status');
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
          '<div id="dadapter-' + i + '" style="font-size:10px;color:#94a3b8;margin-top:1px;"></div>' +
          '<div id="durl-' + i + '" style="font-size:10px;color:#c4b5fd;margin-top:2px;word-break:break-all;"></div>' +
        '</div>' +
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
            '<div class="ts" id="daudiofmt-' + i + '" style="color:#8b5cf6;"></div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Volume</div>' +
            '<div class="volume-row">' +
              '<input type="range" min="0" max="100" value="100" ' +
                'class="volume-slider" id="vslider-' + i + '" ' +
                'oninput="onVolumeInput(' + i + ', this.value)">' +
              '<span class="volume-pct" id="dvol-' + i + '">100%</span>' +
              '<button type="button" id="dmute-' + i + '" ' +
                'style="margin-left:6px;padding:2px 7px;border:1px solid #d1d5db;border-radius:4px;' +
                'background:white;cursor:pointer;font-size:12px;" ' +
                'title="Mute/Unmute">&#128264;</button>' +
            '</div>' +
          '</div>' +
          '<div>' +
            '<div class="status-label">Sync</div>' +
            '<div class="status-value" id="dsync-' + i + '">&#8212;</div>' +
            '<div class="ts" id="dsync-detail-' + i + '"></div>' +
          '</div>' +
        '</div>' +
        '<div class="device-card-actions">' +
          '<button type="button" class="btn-bt-action btn-bt-reconnect" id="dbtn-reconnect-' + i + '"' +
            ' onclick="btReconnect(' + i + ')">&#128260; Reconnect</button>' +
          '<button type="button" class="btn-bt-action btn-bt-pair" id="dbtn-pair-' + i + '"' +
            ' onclick="btPair(' + i + ')" title="Put the device into pairing mode first">&#128279; Re-pair</button>' +
          '<span class="bt-action-status" id="dbt-action-status-' + i + '"></span>' +
        '</div>';
    return card;
}

function populateDeviceCard(i, dev) {
    var name = dev.player_name || ('Device ' + (i + 1));
    document.getElementById('dname-' + i).textContent = name;

    var mac = dev.bluetooth_mac || '';
    document.getElementById('dmac-' + i).textContent = mac ? 'MAC: ' + mac : '';

    var adapterEl = document.getElementById('dadapter-' + i);
    if (adapterEl) adapterEl.textContent = dev.bluetooth_adapter ? dev.bluetooth_adapter : '';

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

    // Playback
    document.getElementById('dplay-' + i).textContent =
        dev.playing ? '\u25b6\ufe0f Playing' : '\u23f8\ufe0f Stopped';
    var trackEl = document.getElementById('dtrack-' + i);
    if (trackEl) trackEl.textContent = dev.current_track || '';

    // Audio format
    var fmtEl = document.getElementById('daudiofmt-' + i);
    if (fmtEl) fmtEl.textContent = dev.audio_format ? 'Transport: ' + dev.audio_format : '';

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
                fetch('/api/mute', {
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
        await fetch('/api/volume', {
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
        var resp = await fetch('/api/logs?lines=150');
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
        fetch('/api/volume', {
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
    // Determine: if any selected player is unmuted → mute all; else unmute all
    var anyUnmuted = false;
    if (lastDevices) {
        lastDevices.forEach(function(dev, i) {
            if (_groupSelected[i] !== false && !dev.muted) anyUnmuted = true;
        });
    }
    var muteVal = anyUnmuted;
    fetch('/api/mute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mute: muteVal, player_names: names})
    }).then(function(r) { return r.json(); }).then(function(d) {
        if (btn) {
            btn.textContent = muteVal ? '🔇 Unmute All' : '🔈 Mute All';
            btn.className = 'btn-group-mute' + (muteVal ? ' muted' : '');
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
        var resp = await fetch('/api/bt/reconnect', {
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
        var resp = await fetch('/api/bt/pair', {
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

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort) {
    var tbody = document.getElementById('bt-devices-table');
    var row = document.createElement('div');
    row.className = 'bt-device-row';
    var delayVal = (delay !== undefined && delay !== null && delay !== '') ? delay : -500;
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
        var delay      = delayEl ? parseFloat(delayEl.value) : -500;
        if (isNaN(delay)) delay = -500;
        var dev = { mac: mac, adapter: adapter, player_name: name, static_delay_ms: delay };
        if (listenHost) dev.listen_host = listenHost;
        if (listenPort) dev.listen_port = listenPort;
        if (mac) devices.push(dev);
    });
    return devices;
}

function populateBtDeviceRows(devices) {
    document.getElementById('bt-devices-table').innerHTML = '';
    devices.forEach(function(d) {
        addBtDeviceRow(d.player_name || '', d.mac || '', d.adapter || '',
                       d.static_delay_ms, d.listen_host, d.listen_port);
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
    // Persist manually-added adapters
    config.BLUETOOTH_ADAPTERS = btManualAdapters.filter(function(a) { return a.mac || a.id; });
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
        ['SENDSPIN_SERVER', 'TZ'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key];
        });
        updateTzPreview();

        // Restore manual adapters before re-running loadBtAdapters so merging picks them up
        btManualAdapters = config.BLUETOOTH_ADAPTERS || [];
        await loadBtAdapters();

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
loadConfig();   // calls loadBtAdapters() internally after restoring btManualAdapters
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
    """Set player volume. Accepts player_name (single) or player_names (list) or neither (all)."""
    try:
        data = request.get_json()
        volume = max(0, min(100, int(data.get('volume', 100))))
        player_names = data.get('player_names')  # list → multi-player
        player_name  = data.get('player_name')   # str  → single (compat)

        if player_names is not None:
            targets = [c for c in _clients if getattr(c, 'player_name', None) in player_names]
        elif player_name:
            targets = [c for c in _clients if getattr(c, 'player_name', None) == player_name]
        else:
            targets = _clients[:1]  # legacy: first client

        results = []
        for client in targets:
            if client.bluetooth_sink_name:
                r = subprocess.run(
                    ['pactl', 'set-sink-volume', client.bluetooth_sink_name, f'{volume}%'],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    client.status['volume'] = volume
                    # Persist per-device volume so it survives restarts
                    mac = getattr(getattr(client, 'bt_manager', None), 'mac_address', None)
                    if mac and CONFIG_FILE.exists():
                        try:
                            with open(CONFIG_FILE, 'r') as f:
                                cfg = json.load(f)
                            cfg.setdefault('LAST_VOLUMES', {})[mac] = volume
                            with open(CONFIG_FILE, 'w') as f:
                                json.dump(cfg, f, indent=2)
                        except Exception as e:
                            logger.debug(f"Could not save volume for {mac}: {e}")
                    results.append({'player': getattr(client, 'player_name', '?'), 'ok': True})
                else:
                    results.append({'player': getattr(client, 'player_name', '?'), 'ok': False})
        if not results:
            return jsonify({'success': False, 'error': 'No clients available'}), 503
        return jsonify({'success': True, 'volume': volume, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mute', methods=['POST'])
def set_mute():
    """Toggle or set mute. Accepts player_name (single), player_names (list), or neither (all)."""
    try:
        data = request.get_json() or {}
        player_names = data.get('player_names')
        player_name  = data.get('player_name')
        mute_value   = data.get('mute')  # True=mute, False=unmute, omit=toggle

        if player_names is not None:
            targets = [c for c in _clients if getattr(c, 'player_name', None) in player_names]
        elif player_name:
            targets = [c for c in _clients if getattr(c, 'player_name', None) == player_name]
        else:
            targets = _clients[:1]

        pactl_arg = 'toggle' if mute_value is None else ('1' if mute_value else '0')
        results = []
        for client in targets:
            if client.bluetooth_sink_name:
                r = subprocess.run(
                    ['pactl', 'set-sink-mute', client.bluetooth_sink_name, pactl_arg],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    info = subprocess.run(
                        ['pactl', 'get-sink-mute', client.bluetooth_sink_name],
                        capture_output=True, text=True, timeout=2
                    )
                    muted = 'yes' in info.stdout.lower()
                    client.status['muted'] = muted
                    results.append({'player': getattr(client, 'player_name', '?'),
                                    'ok': True, 'muted': muted})
                else:
                    results.append({'player': getattr(client, 'player_name', '?'), 'ok': False})
        if not results:
            return jsonify({'success': False, 'error': 'Client not available'}), 503
        muted = results[0].get('muted', False) if results else False
        return jsonify({'success': True, 'muted': muted, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bt/reconnect', methods=['POST'])
def api_bt_reconnect():
    """Force reconnect a BT device (connect without re-pairing)"""
    try:
        data = request.get_json() or {}
        player_name = data.get('player_name')
        client = next((c for c in _clients if getattr(c, 'player_name', None) == player_name), None)
        if client is None and _clients:
            client = _clients[0]
        if not client or not client.bt_manager:
            return jsonify({'success': False, 'error': 'No BT manager for this player'}), 503

        bt = client.bt_manager

        def _do_reconnect():
            try:
                bt.disconnect_device()
                time.sleep(1)
                bt.connect_device()
            except Exception as e:
                logger.error(f"Force reconnect failed: {e}")

        threading.Thread(target=_do_reconnect, daemon=True).start()
        return jsonify({'success': True, 'message': 'Reconnect started'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bt/pair', methods=['POST'])
def api_bt_pair():
    """Force re-pair a BT device (pair + connect). Device must be in pairing mode."""
    try:
        data = request.get_json() or {}
        player_name = data.get('player_name')
        client = next((c for c in _clients if getattr(c, 'player_name', None) == player_name), None)
        if client is None and _clients:
            client = _clients[0]
        if not client or not client.bt_manager:
            return jsonify({'success': False, 'error': 'No BT manager for this player'}), 503

        bt = client.bt_manager

        def _do_pair():
            try:
                bt.pair_device()
                bt.connect_device()
            except Exception as e:
                logger.error(f"Force pair failed: {e}")

        threading.Thread(target=_do_pair, daemon=True).start()
        return jsonify({'success': True, 'message': 'Pairing started (~25s)'})
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

        # Enrich BLUETOOTH_DEVICES with resolved listen_port / listen_host
        # from running client instances (not saved to file — display only).
        client_map = {getattr(c, 'player_name', None): c for c in _clients}
        mac_map    = {getattr(getattr(c, 'bt_manager', None), 'mac_address', None): c
                      for c in _clients}
        for dev in config.get('BLUETOOTH_DEVICES', []):
            client = client_map.get(dev.get('player_name')) or mac_map.get(dev.get('mac'))
            if client:
                if 'listen_port' not in dev or not dev['listen_port']:
                    dev['listen_port'] = getattr(client, 'listen_port', None)
                if 'listen_host' not in dev or not dev['listen_host']:
                    dev['listen_host'] = (getattr(client, 'listen_host', None)
                                          or client.status.get('ip_address'))

        return jsonify(config)

    elif request.method == 'POST':
        config = request.get_json()
        # Preserve runtime-managed keys not sent by the UI
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                for key in ('LAST_VOLUMES', 'LAST_VOLUME'):
                    if key in existing and key not in config:
                        config[key] = existing[key]
            except Exception:
                pass
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return jsonify({'success': True})


@app.route('/api/logs')
def api_logs():
    """Return real service logs (journalctl or docker logs)"""
    lines = min(request.args.get('lines', 150, type=int), 500)
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
        macs = []
        for line in result.stdout.splitlines():
            if 'Controller' not in line:
                continue
            parts = line.split()
            mac = next(
                (p for p in parts if len(p) == 17 and p.count(':') == 5), None
            )
            if mac:
                macs.append(mac)
        adapters = []
        for i, mac in enumerate(macs):
            show_out = subprocess.run(
                ['bash', '-c',
                 f"printf 'select {mac}\\nshow\\n' | bluetoothctl 2>/dev/null"],
                capture_output=True, text=True, timeout=5
            ).stdout
            powered = 'Powered: yes' in show_out
            alias = next(
                (ln.split('Alias:')[1].strip()
                 for ln in show_out.splitlines() if 'Alias:' in ln),
                f'hci{i}'
            )
            adapters.append({'id': f'hci{i}', 'mac': mac, 'name': alias, 'powered': powered})
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
        seen: set = set()
        names: dict = {}
        ansi_re = re.compile(r'\x1b\[[0-9;]*m')
        # [NEW] Device — first time seen this scan session
        new_pat = re.compile(r'\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)')
        # [CHG] Device MAC Name: ... — already-known device reports name update
        chg_name_pat = re.compile(r'\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)')
        # [CHG] Device MAC RSSI: ... — already-known device seen with signal (active)
        chg_rssi_pat = re.compile(r'\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:')
        active_macs: set = set()
        for line in result.stdout.splitlines():
            clean = ansi_re.sub('', line)
            m = new_pat.search(clean)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                seen.add(mac)
                if name and not re.match(r'^[0-9A-Fa-f]{2}[-:]', name):
                    names[mac] = name
                continue
            m = chg_name_pat.search(clean)
            if m:
                mac = m.group(1).upper()
                names[mac] = m.group(2).strip()
                continue
            m = chg_rssi_pat.search(clean)
            if m:
                active_macs.add(m.group(1).upper())
        # Include already-known devices that were actively seen (CHG RSSI) during scan
        all_macs = seen | active_macs

        # For MACs without a name, look them up in BlueZ device database
        unnamed = {mac for mac in all_macs if mac not in names}
        if unnamed:
            db_result = subprocess.run(
                ['bash', '-c', 'echo "devices" | bluetoothctl 2>/dev/null'],
                capture_output=True, text=True, timeout=5
            )
            dev_pat = re.compile(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)')
            for line in db_result.stdout.splitlines():
                clean = ansi_re.sub('', line)
                m = dev_pat.search(clean)
                if m:
                    mac = m.group(1).upper()
                    name = m.group(2).strip()
                    if mac in unnamed and name and not re.match(r'^[0-9A-Fa-f]{2}[-:]', name):
                        names[mac] = name

        # Check each device's Class / UUIDs to filter audio-capable devices.
        # Major device class 4 (0x0400) = Audio/Video.
        # UUID 0000110b = A2DP Sink (speaker/headphones).
        # Devices with no class info are kept (unknown → better show than hide).
        def is_audio_device(mac: str) -> bool:
            try:
                r = subprocess.run(
                    ['bluetoothctl', 'info', mac],
                    capture_output=True, text=True, timeout=4
                )
                out = r.stdout
                # Check Class field: "Class: 0x240404" — major class bits 12-8
                class_m = re.search(r'Class:\s+(0x[0-9A-Fa-f]+)', out)
                if class_m:
                    cls = int(class_m.group(1), 16)
                    major = (cls >> 8) & 0x1f
                    return major == 4  # 4 = Audio/Video
                # Class unavailable — check for A2DP Sink UUID
                if '0000110b' in out.lower():
                    return True
                # No class info at all — include (device may not be cached yet)
                if 'Class:' not in out and 'UUID:' not in out:
                    return True
                return False
            except Exception:
                return True  # on error, include

        devices = []
        for mac in all_macs:
            if is_audio_device(mac):
                devices.append({'mac': mac, 'name': names.get(mac, mac)})
        # Sort: named devices first, then by MAC
        devices.sort(key=lambda d: (d['name'] == d['mac'], d['name']))
        return jsonify({'devices': devices})
    except Exception as e:
        logger.error(f"BT scan failed: {e}")
        return jsonify({'devices': [], 'error': str(e)})


@app.route('/api/diagnostics')
def api_diagnostics():
    """Return structured health diagnostics"""
    try:
        diag: dict = {}

        # Bluetooth daemon — in LXC the daemon runs on the host with a bridged D-Bus,
        # so systemctl is-active bluetooth will always return 'inactive'. Instead check
        # whether bluetoothctl can list controllers via the available D-Bus socket.
        try:
            r = subprocess.run(
                ['bluetoothctl', 'list'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and 'Controller' in r.stdout:
                diag['bluetooth_daemon'] = 'active'
            else:
                # Fall back to systemctl for non-LXC deployments
                r2 = subprocess.run(
                    ['systemctl', 'is-active', 'bluetooth'],
                    capture_output=True, text=True, timeout=3
                )
                diag['bluetooth_daemon'] = r2.stdout.strip() or 'inactive'
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
