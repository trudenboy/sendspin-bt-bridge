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
VERSION = "1.3.15"
BUILD_DATE = "2026-03-01"

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


def _bt_remove_device(mac: str, adapter_mac: str = '') -> None:
    """Remove device from BT stack (disconnect + unpair). Fire-and-forget."""
    def _run():
        cmds = []
        if adapter_mac:
            cmds.append(f'select {adapter_mac}')
        cmds.append(f'remove {mac}')
        cmd_str = '\n'.join(cmds) + '\n'
        try:
            subprocess.run(['bluetoothctl'], input=cmd_str,
                           capture_output=True, text=True, timeout=10)
            logger.info(f"BT stack: removed {mac} (adapter: {adapter_mac or 'default'})")
        except Exception as e:
            logger.warning(f"BT stack cleanup failed for {mac}: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _persist_device_enabled(player_name: str, enabled: bool) -> None:
    if not CONFIG_FILE.exists():
        return
    try:
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
        for dev in cfg.get('BLUETOOTH_DEVICES', []):
            if dev.get('player_name') == player_name:
                dev['enabled'] = enabled
                break
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not persist enabled flag for '{player_name}': {e}")


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
        elif os.path.exists('/data/options.json'):
            _runtime_cache = 'ha_addon'
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
        status['static_delay_ms'] = getattr(client, 'static_delay_ms', None)

        bt_mgr = getattr(client, 'bt_manager', None)
        status['bluetooth_mac'] = bt_mgr.mac_address if bt_mgr else None
        status['bluetooth_adapter'] = bt_mgr.adapter if bt_mgr else None
        status['has_sink'] = bool(getattr(client, 'bluetooth_sink_name', None))
        status['bt_management_enabled'] = getattr(client, 'bt_management_enabled', True)

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
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            /* HA default light theme */
            --primary-color: #03a9f4;
            --dark-primary-color: #0288d1;
            --accent-color: #ff9800;
            --primary-text-color: #212121;
            --secondary-text-color: #727272;
            --disabled-text-color: #bdbdbd;
            --primary-background-color: #fafafa;
            --secondary-background-color: #e5e5e5;
            --card-background-color: #ffffff;
            --ha-card-border-radius: 12px;
            --divider-color: rgba(0, 0, 0, .12);
            --error-color: #db4437;
            --success-color: #43a047;
            --warning-color: #ffa600;
            --info-color: #039be5;
            --ha-card-box-shadow: 0 2px 2px 0 rgba(0,0,0,.14),
                                  0 1px 5px 0 rgba(0,0,0,.12),
                                  0 3px 1px -2px rgba(0,0,0,.2);
            --code-background-color: #1e293b;
            --code-text-color: #e2e8f0;
            --app-header-background-color: var(--primary-color);
            --app-header-text-color: #fff;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --primary-color: #03a9f4;
                --primary-background-color: #111111;
                --secondary-background-color: #202020;
                --card-background-color: #1c1c1c;
                --primary-text-color: #e1e1e1;
                --secondary-text-color: #9b9b9b;
                --disabled-text-color: rgba(225,225,225,.5);
                --divider-color: rgba(225, 225, 225, .12);
                --ha-card-box-shadow: 0 2px 2px 0 rgba(0,0,0,.4),
                                      0 1px 5px 0 rgba(0,0,0,.3),
                                      0 3px 1px -2px rgba(0,0,0,.5);
                --code-background-color: #0f172a;
            }
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Roboto', -apple-system, BlinkMacSystemFont, 'Segoe UI',
                         'Helvetica Neue', Arial, sans-serif;
            background: var(--primary-background-color);
            color: var(--primary-text-color);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }

        /* Header */
        .header {
            background: var(--app-header-background-color);
            border-radius: var(--ha-card-border-radius); padding: 20px;
            margin-bottom: 20px; box-shadow: var(--ha-card-box-shadow);
            display: flex; justify-content: space-between; align-items: center;
        }
        .header h1 { color: var(--app-header-text-color); font-size: 28px; }
        .version-info { text-align: right; font-size: 12px; color: var(--app-header-text-color); opacity: 0.85; }
        #system-info { color: rgba(255,255,255,0.7) !important; }

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
            background: var(--card-background-color);
            border-radius: var(--ha-card-border-radius); padding: 16px 20px;
            box-shadow: var(--ha-card-box-shadow);
            display: flex; align-items: center; gap: 0; flex-wrap: wrap;
        }
        .device-card-actions {
            width: 100%; display: flex; align-items: center; gap: 8px;
            padding-top: 10px; margin-top: 10px; border-top: 1px solid var(--divider-color);
            flex-wrap: wrap;
        }
        .btn-bt-action {
            padding: 4px 12px; font-size: 12px; font-weight: 600; border: none;
            border-radius: 5px; cursor: pointer; color: white; transition: opacity 0.15s;
        }
        .btn-bt-action:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-bt-reconnect { background: var(--primary-color); }
        .btn-bt-reconnect:hover:not(:disabled) { background: var(--dark-primary-color); }
        .btn-bt-pair { background: var(--warning-color); }
        .btn-bt-pair:hover:not(:disabled) { background: #e09600; }
        .btn-bt-release { background: var(--error-color); }
        .btn-bt-release:hover:not(:disabled) { background: #c43328; }
        .btn-bt-reclaim { background: var(--success-color); }
        .btn-bt-reclaim:hover:not(:disabled) { background: #388e3c; }
        .bt-action-status { font-size: 12px; color: var(--secondary-text-color); }
        /* Group controls */
        .group-controls {
            background: var(--card-background-color);
            border-radius: var(--ha-card-border-radius); padding: 12px 20px;
            box-shadow: var(--ha-card-box-shadow); margin-bottom: 14px;
            display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
        }
        .group-controls-label {
            font-size: 12px; font-weight: 700; color: var(--primary-color);
            text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap;
        }
        .group-select-info { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
        .group-vol-row { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 180px; }
        .group-vol-slider { flex: 1; height: 4px; accent-color: var(--primary-color); cursor: pointer; }
        .group-vol-pct { min-width: 36px; font-size: 13px; color: var(--secondary-text-color); text-align: right; }
        .btn-group-mute {
            padding: 4px 12px; font-size: 12px; font-weight: 600;
            border: 1px solid var(--divider-color);
            border-radius: 5px; background: var(--card-background-color);
            color: var(--primary-text-color); cursor: pointer;
        }
        .btn-group-mute.muted { background: rgba(219,68,55,.1); border-color: var(--error-color); color: var(--error-color); }
        .device-select-cb { width: 15px; height: 15px; accent-color: var(--primary-color); cursor: pointer; }
        .device-card-identity {
            min-width: 180px; max-width: 220px; padding-right: 20px;
            border-right: 1px solid var(--divider-color); margin-right: 0; flex-shrink: 0;
        }
        .device-card-title {
            font-size: 15px; font-weight: 700; color: var(--primary-color); margin-bottom: 2px;
        }
        .device-mac {
            font-size: 11px; color: var(--secondary-text-color); font-family: 'Courier New', monospace;
        }
        .device-rows {
            display: flex; flex: 1; gap: 0;
        }
        .device-rows > div {
            flex: 1; padding: 0 16px; border-right: 1px solid var(--divider-color);
        }
        .device-rows > div:last-child { border-right: none; }
        .device-rows .status-label { font-size: 11px; color: var(--secondary-text-color); margin-bottom: 3px; text-transform: uppercase; letter-spacing: 0.04em; }
        .device-rows .status-value { font-size: 13px; font-weight: 600; color: var(--primary-text-color); }

        /* Status indicators */
        .status-indicator {
            display: inline-block; width: 10px; height: 10px;
            border-radius: 50%; margin-right: 6px; flex-shrink: 0;
        }
        .status-indicator.active   { background: var(--success-color); box-shadow: 0 0 8px var(--success-color); }
        .status-indicator.inactive { background: var(--error-color); }

        /* Volume slider */
        .volume-row { display: flex; align-items: center; gap: 6px; }
        .volume-slider { flex: 1; height: 4px; accent-color: var(--primary-color); cursor: pointer; }
        .volume-pct { min-width: 36px; text-align: right; font-size: 13px; color: var(--secondary-text-color); }

        /* Config section */
        .config-section {
            background: var(--card-background-color);
            border-radius: var(--ha-card-border-radius); padding: 20px;
            box-shadow: var(--ha-card-box-shadow); margin-bottom: 20px;
        }
        .config-section summary, .logs-section summary {
            color: var(--primary-color); font-size: 20px; font-weight: 700;
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
        .form-group label { display: block; margin-bottom: 5px; color: var(--primary-text-color); font-weight: 500; }
        .form-group input {
            width: 100%; padding: 10px; border: 1px solid var(--divider-color);
            border-radius: 5px; font-size: 14px;
            background: var(--card-background-color); color: var(--primary-text-color);
        }
        .form-group input:focus { outline: none; border-color: var(--primary-color); }
        .form-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 15px; }

        /* BT device table */
        .bt-header {
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 9rem 4.5rem 5.5rem 30px;
            gap: 8px; margin-bottom: 4px; padding: 0 2px;
            font-size: 11px; font-weight: 600; color: var(--secondary-text-color); text-transform: uppercase;
        }
        .bt-device-row {
            display: grid; grid-template-columns: 1fr 1.3fr 0.9fr 9rem 4.5rem 5.5rem 30px;
            gap: 8px; align-items: center; margin-bottom: 8px;
        }
        .bt-device-row input, .bt-device-row select {
            padding: 7px 10px; border: 1px solid var(--divider-color);
            border-radius: 5px; font-size: 13px;
            background: var(--card-background-color); color: var(--primary-text-color); width: 100%;
        }
        .bt-device-row input:focus, .bt-device-row select:focus {
            outline: none; border-color: var(--primary-color);
        }
        .bt-device-row input.invalid { border-color: var(--error-color); background: rgba(219,68,55,.08); }
        .btn-remove-dev {
            background: transparent; color: var(--error-color); border: none; border-radius: 5px;
            width: 30px; height: 34px; cursor: pointer; font-size: 18px; line-height: 1;
        }
        .btn-remove-dev:hover { background: rgba(219,68,55,.15); }
        .bt-toolbar { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
        .scan-badge {
            font-size: 13px; color: var(--secondary-text-color);
            display: flex; align-items: center; gap: 4px;
        }
        .scan-results-box {
            margin-top: 10px; border: 1px solid var(--divider-color); border-radius: 6px;
            padding: 10px; background: var(--secondary-background-color); display: none;
        }
        .scan-results-title { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 6px; }
        .scan-result-item {
            display: flex; align-items: center; gap: 10px;
            padding: 5px 4px; border-bottom: 1px solid var(--divider-color); font-size: 13px;
        }
        .scan-result-item:last-child { border-bottom: none; }
        .scan-result-mac { font-family: monospace; color: var(--secondary-text-color); font-size: 12px; }
        .paired-box {
            margin-top: 10px; border: 1px solid var(--divider-color); border-radius: 6px;
            padding: 10px; background: var(--secondary-background-color);
        }
        .paired-box-title { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 6px; }

        /* Adapters panel */
        .adapters-card {
            border: 1px solid var(--divider-color); border-radius: 8px;
            margin-bottom: 18px; overflow: hidden;
        }
        .adapters-card-header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 10px 14px; background: var(--secondary-background-color);
            border-bottom: 1px solid var(--divider-color); font-weight: 600;
            color: var(--primary-text-color); font-size: 13px;
        }
        .adapters-card-header div { display: flex; gap: 8px; }
        .adapter-row {
            display: grid;
            grid-template-columns: 5rem 1fr 1.4fr 2.5rem 2rem;
            gap: 8px; align-items: center;
            padding: 7px 14px; border-bottom: 1px solid var(--divider-color); font-size: 13px;
        }
        .adapter-row:last-child { border-bottom: none; }
        .adapter-row.detected { color: var(--primary-text-color); }
        .adapter-row input {
            padding: 5px 8px; border: 1px solid var(--divider-color);
            border-radius: 4px; font-size: 13px; width: 100%;
            background: var(--card-background-color); color: var(--primary-text-color);
        }
        .adapter-row input:focus { outline: none; border-color: var(--primary-color); }
        .dot { font-size: 16px; text-align: center; }
        .dot.green { color: var(--success-color); }
        .dot.grey  { color: var(--secondary-text-color); }
        .mono { font-family: 'Courier New', monospace; font-size: 12px; color: var(--secondary-text-color); }
        .btn-remove-adapter {
            background: transparent; color: var(--error-color); border: none; border-radius: 4px;
            width: 24px; height: 24px; cursor: pointer; font-size: 14px; line-height: 1;
        }
        .btn-remove-adapter:hover { background: rgba(219,68,55,.15); }

        /* Buttons */
        .btn {
            background: var(--primary-color); color: white; padding: 10px 20px;
            border: none; border-radius: 4px; font-size: 15px; font-weight: 500;
            letter-spacing: 0.0892857143em; text-transform: uppercase;
            cursor: pointer; transition: background 0.2s;
        }
        .btn:hover { background: var(--dark-primary-color); }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; }
        .btn-sm { padding: 7px 14px; font-size: 13px; }
        .btn-restart { background: var(--warning-color); }
        .btn-restart:hover { background: #e09600; }
        .btn-refresh { background: var(--success-color); }
        .btn-refresh:hover { background: #388e3c; }
        .btn-scan { background: #8b5cf6; }
        .btn-scan:hover { background: #7c3aed; }

        /* Logs section */
        .logs-section {
            background: var(--card-background-color);
            border-radius: var(--ha-card-border-radius); padding: 20px;
            box-shadow: var(--ha-card-box-shadow);
        }
        .logs-section h2 { color: var(--primary-color); margin-bottom: 15px; }
        .logs-toolbar {
            display: flex; align-items: center; gap: 10px;
            flex-wrap: wrap; margin-bottom: 12px;
        }
        .logs-container {
            background: var(--code-background-color); color: var(--code-text-color);
            padding: 15px; border-radius: 5px;
            font-family: 'Courier New', monospace; font-size: 12px;
            max-height: 400px; overflow-y: auto;
        }
        .log-line { margin-bottom: 3px; line-height: 1.4; word-break: break-all; }
        .log-error   { color: #f87171; }
        .log-warning { color: #fbbf24; }
        .log-info    { color: var(--code-text-color); }
        .log-debug   { color: var(--secondary-text-color); }
        .log-filter { display: inline-flex; gap: 4px; }
        .filter-btn {
            background: var(--secondary-background-color); color: var(--secondary-text-color);
            padding: 4px 12px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px;
            transition: background 0.2s;
        }
        .filter-btn:hover { color: var(--primary-text-color); }
        .filter-btn.active { background: var(--primary-color); color: white; }
        .ts { font-size: 11px; color: var(--secondary-text-color); margin-top: 3px; }

        /* Diagnostics section */
        .diag-section {
            background: var(--card-background-color);
            border-radius: var(--ha-card-border-radius); padding: 20px;
            box-shadow: var(--ha-card-box-shadow); margin-bottom: 20px;
        }
        .diag-section summary {
            color: var(--primary-color); font-size: 20px; font-weight: 700;
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
            font-size: 11px; font-weight: 600; color: var(--secondary-text-color);
            text-transform: uppercase; padding: 4px 8px; text-align: left;
        }
        .diag-table td { padding: 7px 8px; border-top: 1px solid var(--divider-color); font-size: 13px; }
        .diag-dot {
            display: inline-block; width: 9px; height: 9px;
            border-radius: 50%; margin-right: 6px; vertical-align: middle;
        }
        .diag-dot.ok   { background: var(--success-color); box-shadow: 0 0 6px var(--success-color); }
        .diag-dot.err  { background: var(--error-color); }
        .diag-dot.warn { background: var(--warning-color); }

        /* Timezone preview */
        .tz-preview { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
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
                <label>Music Assistant WebSocket port</label>
                <input type="number" name="SENDSPIN_PORT" placeholder="9000" min="1024" max="65535">
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
                <div id="paired-box" class="paired-box" style="display:none;">
                    <div class="paired-box-title">Already paired &#8212; click to add:</div>
                    <div id="paired-list"></div>
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

// ---- Status ----

async function updateStatus() {
    try {
        var resp = await fetch(API_BASE + '/api/status');
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
          '<div id="ddelay-' + i + '" style="display:none;font-size:10px;color:#f59e0b;margin-top:2px;"></div>' +
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
            '<div class="ts" id="dtrack-' + i + '" style="color:#94a3b8;font-style:italic;"></div>' +
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
          '<button type="button" class="btn-bt-action btn-bt-release" id="dbtn-release-' + i + '"' +
            ' onclick="btToggleManagement(' + i + ')">\U0001F513 Release</button>' +
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
        srvTxt.textContent = (dev.server_host && dev.server_port)
            ? 'Connected \u2014 ' + dev.server_host + ':' + dev.server_port
            : 'Connected';
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
            relBtn.textContent = '\U0001F513 Release';
            relBtn.className = 'btn-bt-action btn-bt-release';
        } else {
            relBtn.textContent = '\U0001F512 Reclaim';
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
    // Determine: if any selected player is unmuted → mute all; else unmute all
    var anyUnmuted = false;
    if (lastDevices) {
        lastDevices.forEach(function(dev, i) {
            if (_groupSelected[i] !== false && !dev.muted) anyUnmuted = true;
        });
    }
    var muteVal = anyUnmuted;
    fetch(API_BASE + '/api/mute', {
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

function addBtDeviceRow(name, mac, adapter, delay, listenHost, listenPort) {
    var tbody = document.getElementById('bt-devices-table');
    var row = document.createElement('div');
    row.className = 'bt-device-row';
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
        // Preserve enabled flag (not a form field — comes from live status)
        var livedev = lastDevices && lastDevices.find(function(d) {
            return d.player_name === name || d.bluetooth_mac === mac;
        });
        if (livedev && livedev.bt_management_enabled === false) {
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

function autoAdapter() {
    return (btAdapters.length === 1) ? btAdapters[0].id : '';
}

function addFromScan(mac, name) {
    addBtDeviceRow(name, mac, autoAdapter());
    document.getElementById('scan-results-box').style.display = 'none';
    document.getElementById('scan-status').textContent = '';
}

function addFromPaired(mac, name) {
    addBtDeviceRow(name, mac, autoAdapter());
    document.getElementById('paired-box').style.display = 'none';
}

async function loadPairedDevices() {
    try {
        var resp = await fetch(API_BASE + '/api/bt/paired');
        var data = await resp.json();
        var devices = data.devices || [];
        var box = document.getElementById('paired-box');
        var listDiv = document.getElementById('paired-list');
        if (devices.length === 0) { box.style.display = 'none'; return; }
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
        var config = await resp.json();

        // Populate simple fields
        ['SENDSPIN_SERVER', 'SENDSPIN_PORT', 'TZ'].forEach(function(key) {
            var input = document.querySelector('[name="' + key + '"]');
            if (input && config[key] !== undefined) input.value = config[key];
        });
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
    """Restart the service (systemd, HA addon, or Docker)"""
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
        elif runtime == 'ha_addon':
            def _do_ha_restart():
                import urllib.request as _ur
                time.sleep(0.5)
                token = os.environ.get('SUPERVISOR_TOKEN', '')
                if token:
                    try:
                        req = _ur.Request(
                            'http://supervisor/addons/self/restart',
                            data=b'{}',
                            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                            method='POST'
                        )
                        _ur.urlopen(req, timeout=15)
                    except Exception as e:
                        logger.warning(f'Supervisor restart failed: {e}; falling back to SIGTERM')
                        try:
                            os.kill(1, signal.SIGTERM)
                        except ProcessLookupError:
                            os.kill(os.getpid(), signal.SIGTERM)
                else:
                    os.kill(os.getpid(), signal.SIGTERM)
            threading.Thread(target=_do_ha_restart, daemon=True).start()
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


@app.route('/api/bt/management', methods=['POST'])
def api_bt_management():
    """Release or reclaim the BT adapter for a player."""
    data = request.get_json() or {}
    player_name = data.get('player_name')
    enabled = data.get('enabled')
    if enabled is None:
        return jsonify({'success': False, 'error': 'Missing "enabled" field'}), 400
    client = next((c for c in _clients if getattr(c, 'player_name', None) == player_name), None)
    if not client and _clients:
        client = _clients[0]
    if not client:
        return jsonify({'success': False, 'error': 'No client found'}), 503
    enabled = bool(enabled)
    # set_bt_management_enabled is synchronous — safe to call from Flask thread directly
    threading.Thread(
        target=client.set_bt_management_enabled,
        args=(enabled,),
        daemon=True
    ).start()
    _persist_device_enabled(player_name, enabled)
    action = 'reclaimed' if enabled else 'released'
    return jsonify({'success': True, 'message': f'BT adapter {action}', 'enabled': enabled})


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
        existing = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                for key in ('LAST_VOLUMES', 'LAST_VOLUME'):
                    if key in existing and key not in config:
                        config[key] = existing[key]
            except Exception:
                pass

        # BT stack cleanup for deleted or adapter-changed devices
        old_devices = {d['mac']: d for d in existing.get('BLUETOOTH_DEVICES', []) if d.get('mac')}
        new_devices = {d['mac']: d for d in config.get('BLUETOOTH_DEVICES', []) if d.get('mac')}

        # Build adapter MAC lookup from running clients (already resolved hciN → MAC)
        client_adapter = {
            getattr(getattr(c, 'bt_manager', None), 'mac_address', None):
            getattr(getattr(c, 'bt_manager', None), '_adapter_select', '')
            for c in _clients
        }

        for mac, old_dev in old_devices.items():
            new_dev = new_devices.get(mac)
            adapter_changed = new_dev and new_dev.get('adapter') != old_dev.get('adapter')
            deleted = new_dev is None
            if deleted or adapter_changed:
                adapter_mac = client_adapter.get(mac) or ''
                _bt_remove_device(mac, adapter_mac)

        # Init LAST_VOLUMES for brand-new devices to the group slider value
        default_vol = config.pop('_new_device_default_volume', None)
        if default_vol is not None:
            last_volumes = config.setdefault('LAST_VOLUMES', existing.get('LAST_VOLUMES', {}))
            for mac in new_devices:
                if mac and mac not in last_volumes:
                    last_volumes[mac] = default_vol

        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)

        # In HA addon mode, also push to Supervisor options so the config
        # survives container restarts (run.sh regenerates config.json from options.json).
        if _detect_runtime() == 'ha_addon':
            try:
                import urllib.request as _ur
                token = os.environ.get('SUPERVISOR_TOKEN', '')
                if token:
                    # Map config.json keys → Supervisor options schema
                    sup_devices = []
                    for d in config.get('BLUETOOTH_DEVICES', []):
                        entry = {'mac': d.get('mac', ''), 'player_name': d.get('player_name', '')}
                        if d.get('adapter'):
                            entry['adapter'] = d['adapter']
                        if d.get('static_delay_ms'):
                            entry['static_delay_ms'] = int(d['static_delay_ms'])
                        if d.get('listen_host'):
                            entry['listen_host'] = d['listen_host']
                        if d.get('listen_port'):
                            entry['listen_port'] = int(d['listen_port'])
                        if 'enabled' in d:
                            entry['enabled'] = bool(d['enabled'])
                        sup_devices.append(entry)
                    sup_adapters = [
                        dict({'id': a['id'], 'mac': a.get('mac', '')},
                             **({'name': a['name']} if a.get('name') else {}))
                        for a in config.get('BLUETOOTH_ADAPTERS', [])
                        if a.get('id')
                    ]
                    sup_opts = {
                        'options': {
                            'sendspin_server':    config.get('SENDSPIN_SERVER', 'auto'),
                            'sendspin_port':      int(config.get('SENDSPIN_PORT', 9000)),
                            'tz':                 config.get('TZ', ''),
                            'bluetooth_devices':  sup_devices,
                            'bluetooth_adapters': sup_adapters,
                        }
                    }
                    body = json.dumps(sup_opts).encode()
                    req = _ur.Request(
                        'http://supervisor/addons/self/options',
                        data=body,
                        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                        method='POST'
                    )
                    _ur.urlopen(req, timeout=10)
            except Exception as e:
                logger.warning(f'Failed to sync Supervisor options: {e}')

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
        elif runtime == 'ha_addon':
            import urllib.request as _ur
            token = os.environ.get('SUPERVISOR_TOKEN', '')
            if token:
                req = _ur.Request(
                    'http://supervisor/addons/self/logs',
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'text/plain'}
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode('utf-8', errors='replace')
                all_lines = text.splitlines()
                log_lines = all_lines[-lines:]
            else:
                log_lines = ['(SUPERVISOR_TOKEN not available — check addon permissions)']
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


@app.route('/api/bt/paired')
def api_bt_paired():
    """Return already-paired Bluetooth devices instantly (no scan)"""
    try:
        result = subprocess.run(
            ['bash', '-c', 'echo "devices" | bluetoothctl 2>/dev/null'],
            capture_output=True, text=True, timeout=5
        )
        ansi_re = re.compile(r'\x1b\[[0-9;]*m')
        dev_pat = re.compile(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)')
        devices = []
        seen = set()
        for line in result.stdout.splitlines():
            clean = ansi_re.sub('', line)
            m = dev_pat.search(clean)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                if mac not in seen:
                    seen.add(mac)
                    # Skip entries where name looks like a raw MAC
                    if re.match(r'^[0-9A-Fa-f]{2}[-:]', name):
                        name = ''
                    devices.append({'mac': mac, 'name': name or mac})
        return jsonify({'devices': devices})
    except Exception as e:
        return jsonify({'devices': [], 'error': str(e)})


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
