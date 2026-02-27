#!/usr/bin/env python3
"""
Web Interface for Sendspin Client
Provides configuration and monitoring UI
"""

import json
import logging
import os
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
    'SENDSPIN_SERVER': 'auto',  # Use 'auto' for mDNS discovery
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

def get_client_status_for(client):
    """Get status dict for a specific client"""
    try:
        if client is None:
            return {
                'connected': False,
                'server_connected': False,
                'bluetooth_connected': False,
                'bluetooth_available': False,
                'playing': False,
                'error': 'Client not running',
                'version': VERSION,
                'build_date': BUILD_DATE,
            }

        if not hasattr(client, 'status'):
            return {
                'connected': False,
                'server_connected': False,
                'bluetooth_connected': False,
                'bluetooth_available': False,
                'playing': False,
                'error': 'Client initializing',
                'version': VERSION,
                'build_date': BUILD_DATE,
            }

        status = client.status.copy()

        # Calculate uptime
        if 'uptime_start' in status:
            uptime = datetime.now() - status['uptime_start']
            status['uptime'] = str(timedelta(seconds=int(uptime.total_seconds())))
            del status['uptime_start']

        # Add version info
        status['version'] = VERSION
        status['build_date'] = BUILD_DATE

        # Check if process is running
        if client.process:
            status['connected'] = client.process.poll() is None
        else:
            status['connected'] = False

        # Add player_name for multi-device identification
        status['player_name'] = getattr(client, 'player_name', None)

        logger.debug(f"Status retrieved: {status}")
        return status

    except Exception as e:
        logger.error(f"Error getting client status: {e}", exc_info=True)
        return {
            'connected': False,
            'server_connected': False,
            'bluetooth_connected': False,
            'bluetooth_available': False,
            'playing': False,
            'error': str(e),
            'version': VERSION,
            'build_date': BUILD_DATE,
        }

def get_client_status():
    """Get status from the first client (backward compat)"""
    if not _clients:
        logger.warning("No clients registered")
        return {
            'connected': False,
            'server_connected': False,
            'bluetooth_connected': False,
            'bluetooth_available': False,
            'playing': False,
            'error': 'No clients',
            'version': VERSION,
            'build_date': BUILD_DATE,
        }
    return get_client_status_for(_clients[0])

# HTML Template
def get_version_info():
    return {'VERSION': VERSION, 'BUILD_DATE': BUILD_DATE}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sendspin Client</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            color: #667eea;
            font-size: 28px;
        }
        .version-info {
            text-align: right;
            font-size: 12px;
            color: #666;
        }
        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .status-card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 10px;
        }
        .status-indicator.active {
            background: #10b981;
            box-shadow: 0 0 10px #10b981;
        }
        .status-indicator.inactive {
            background: #ef4444;
        }
        .status-label {
            font-size: 14px;
            color: #666;
            margin-bottom: 5px;
        }
        .status-value {
            font-size: 20px;
            font-weight: 600;
            color: #333;
        }
        .config-section {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }
        .config-section h2 {
            color: #667eea;
            margin-bottom: 15px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #333;
            font-weight: 500;
        }
        .form-group input {
            width: 100%;
            padding: 10px;
            border: 2px solid #e5e7eb;
            border-radius: 5px;
            font-size: 14px;
        }
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            background: #667eea;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn:hover {
            background: #5568d3;
        }
        .logs-section {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .logs-section h2 {
            color: #667eea;
            margin-bottom: 15px;
        }
        .logs-container {
            background: #1e293b;
            color: #e2e8f0;
            padding: 15px;
            border-radius: 5px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            max-height: 400px;
            overflow-y: auto;
        }
        .log-line {
            margin-bottom: 5px;
        }
        .refresh-btn {
            background: #10b981;
            margin-right: 10px;
        }
        .refresh-btn:hover {
            background: #059669;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎵 Sendspin Client</h1>
            <div class="version-info">
                <div>Version: {{ VERSION }}</div>
                <div>Build: {{ BUILD_DATE }}</div>
            </div>
        </div>

        <div class="status-grid">
            <div class="status-card">
                <div class="status-label">Server Connection</div>
                <div class="status-value">
                    <span class="status-indicator" id="server-indicator"></span>
                    <span id="server-status">Checking...</span>
                </div>
                <div style="font-size: 12px; color: #666; margin-top: 5px;" id="server-timestamp"></div>
            </div>
            <div class="status-card">
                <div class="status-label">Bluetooth</div>
                <div class="status-value">
                    <span class="status-indicator" id="bt-indicator"></span>
                    <span id="bt-status">Checking...</span>
                </div>
                <div style="font-size: 12px; color: #666; margin-top: 5px;" id="bt-timestamp"></div>
            </div>
            <div class="status-card">
                <div class="status-label">Playback Status</div>
                <div class="status-value" id="playback-status">Unknown</div>
                <div style="font-size: 14px; color: #666; margin-top: 8px;" id="current-track"></div>
                <div style="font-size: 14px; color: #666; margin-top: 5px;">Volume: <span id="volume-value">100</span>%</div>
            </div>
            <div class="status-card">
                <div class="status-label">Container Info</div>
                <div class="status-value" style="font-size: 14px;" id="container-info">Loading...</div>
            </div>
        </div>

        <div class="config-section">
            <h2>⚙️ Configuration</h2>
            <form id="config-form">
                <div class="form-group">
                    <label>Player Name</label>
                    <input type="text" name="SENDSPIN_NAME" required>
                </div>
                <div class="form-group">
                    <label>Server (use 'auto' for mDNS discovery)</label>
                    <input type="text" name="SENDSPIN_SERVER" required>
                </div>

                <div id="bt-devices-group" class="form-group" style="display:none;">
                    <label>Bluetooth Devices (JSON array)</label>
                    <textarea name="BLUETOOTH_DEVICES" rows="6" style="width:100%;padding:10px;border:2px solid #e5e7eb;border-radius:5px;font-family:monospace;font-size:13px;" placeholder='[{"mac":"AA:BB:CC:DD:EE:FF","adapter":"hci0","player_name":"My Speaker"}]'></textarea>
                    <div style="font-size:12px;color:#666;margin-top:4px;">Each entry: <code>{"mac":"...", "adapter":"hci0", "player_name":"..."}</code></div>
                </div>
                <div id="bt-mac-group" class="form-group">
                    <label>Bluetooth MAC Address</label>
                    <input type="text" name="BLUETOOTH_MAC" placeholder="AA:BB:CC:DD:EE:FF">
                </div>
                <button type="submit" class="btn">Save Configuration</button>
            </form>
        </div>

        <div class="logs-section">
            <h2>📋 Logs</h2>
            <button onclick="refreshLogs()" class="btn refresh-btn">Refresh Logs</button>
            <button onclick="toggleAutoRefresh()" class="btn" id="auto-refresh-btn">Auto-Refresh: Off</button>
            <div class="logs-container" id="logs"></div>
        </div>
    </div>

    <script>
        let autoRefreshLogs = false;
        let refreshInterval;

        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                const status = await response.json();

                // Server status
                const serverIndicator = document.getElementById('server-indicator');
                const serverStatus = document.getElementById('server-status');
                if (status.server_connected) {
                    serverIndicator.className = 'status-indicator active';
                    serverStatus.textContent = 'Connected';
                } else {
                    serverIndicator.className = 'status-indicator inactive';
                    serverStatus.textContent = status.error || 'Disconnected';
                }

                // Bluetooth status
                const btIndicator = document.getElementById('bt-indicator');
                const btStatus = document.getElementById('bt-status');
                if (status.bluetooth_connected) {
                    btIndicator.className = 'status-indicator active';
                    btStatus.textContent = 'Connected';
                } else if (status.bluetooth_available) {
                    btIndicator.className = 'status-indicator inactive';
                    btStatus.textContent = 'Disconnected';
                } else {
                    btIndicator.className = 'status-indicator inactive';
                    btStatus.textContent = 'Not Available';
                }
                
                // Bluetooth timestamp
                if (status.bluetooth_connected_at) {
                    const btTime = new Date(status.bluetooth_connected_at);
                    document.getElementById('bt-timestamp').textContent = 
                        `Since: ${btTime.toLocaleString()}`;
                }
                
                // Server timestamp
                if (status.server_connected_at) {
                    const serverTime = new Date(status.server_connected_at);
                    document.getElementById('server-timestamp').textContent = 
                        `Since: ${serverTime.toLocaleString()}`;
                }

                // Playback status
                document.getElementById('playback-status').textContent = 
                    status.playing ? '▶️ Playing' : '⏸️ Stopped';
                
                // Current track
                const trackElement = document.getElementById('current-track');
                if (status.current_track) {
                    trackElement.textContent = status.current_track;
                    trackElement.style.display = 'block';
                } else {
                    trackElement.style.display = 'none';
                }
                
                // Volume
                if (status.volume !== undefined) {
                    document.getElementById('volume-value').textContent = status.volume;
                }

                // Container info
                const info = [];
                if (status.hostname) info.push(`Host: ${status.hostname}`);
                if (status.ip_address) info.push(`IP: ${status.ip_address}`);
                if (status.uptime) info.push(`Uptime: ${status.uptime}`);
                document.getElementById('container-info').innerHTML = info.join('<br>');

            } catch (error) {
                console.error('Error updating status:', error);
            }
        }

        async function loadConfig() {
            try {
                const response = await fetch('/api/config');
                const config = await response.json();

                // Handle BLUETOOTH_DEVICES vs BLUETOOTH_MAC
                const devices = config.BLUETOOTH_DEVICES;
                if (devices && Array.isArray(devices) && devices.length > 0) {
                    document.getElementById('bt-devices-group').style.display = 'block';
                    document.getElementById('bt-mac-group').style.display = 'none';
                    const ta = document.querySelector('[name="BLUETOOTH_DEVICES"]');
                    if (ta) ta.value = JSON.stringify(devices, null, 2);
                } else {
                    document.getElementById('bt-devices-group').style.display = 'none';
                    document.getElementById('bt-mac-group').style.display = 'block';
                }

                Object.keys(config).forEach(key => {
                    if (key === 'BLUETOOTH_DEVICES') return; // handled above
                    const input = document.querySelector(`[name="${key}"]`);
                    if (input) {
                        input.value = config[key];
                    }
                });
            } catch (error) {
                console.error('Error loading config:', error);
            }
        }

        async function refreshLogs() {
            try {
                const response = await fetch('/api/logs');
                const data = await response.json();
                const logsContainer = document.getElementById('logs');
                logsContainer.innerHTML = data.logs.map(line => 
                    `<div class="log-line">${line}</div>`
                ).join('');
                logsContainer.scrollTop = logsContainer.scrollHeight;
            } catch (error) {
                console.error('Error refreshing logs:', error);
            }
        }

        function toggleAutoRefresh() {
            autoRefreshLogs = !autoRefreshLogs;
            const btn = document.getElementById('auto-refresh-btn');
            
            if (autoRefreshLogs) {
                btn.textContent = 'Auto-Refresh: On';
                btn.style.background = '#10b981';
                refreshInterval = setInterval(refreshLogs, 2000);
                refreshLogs();
            } else {
                btn.textContent = 'Auto-Refresh: Off';
                btn.style.background = '#667eea';
                clearInterval(refreshInterval);
            }
        }

        document.getElementById('config-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const config = Object.fromEntries(formData);

            // Parse BLUETOOTH_DEVICES textarea as JSON if present and non-empty
            if (config.BLUETOOTH_DEVICES !== undefined) {
                const raw = config.BLUETOOTH_DEVICES.trim();
                if (raw) {
                    try {
                        config.BLUETOOTH_DEVICES = JSON.parse(raw);
                    } catch (err) {
                        alert('BLUETOOTH_DEVICES is not valid JSON: ' + err.message);
                        return;
                    }
                } else {
                    config.BLUETOOTH_DEVICES = [];
                }
            }

            try {
                const response = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });

                if (response.ok) {
                    alert('Configuration saved! Restart the container for changes to take effect.');
                } else {
                    alert('Failed to save configuration');
                }
            } catch (error) {
                console.error('Error saving config:', error);
                alert('Error saving configuration');
            }
        });

        // Update status every 2 seconds
        updateStatus();
        setInterval(updateStatus, 2000);

        // Load config on page load
        loadConfig();

        // Load initial logs
        refreshLogs();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """Render the main page"""
    return render_template_string(HTML_TEMPLATE, **get_version_info())

@app.route('/api/volume', methods=['POST'])
def set_volume():
    """Set player volume"""
    try:
        data = request.get_json()
        volume = data.get('volume', 100)
        
        # Validate volume
        volume = max(0, min(100, int(volume)))
        
        # Get client and send volume command via pactl to both sendspin and bluetooth
        client = _clients[0] if _clients else None
        if client and client.bluetooth_sink_name:
            # Set Bluetooth speaker volume
            result = subprocess.run(
                ['pactl', 'set-sink-volume', client.bluetooth_sink_name, f'{volume}%'],
                capture_output=True,
                text=True,
                timeout=2
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
    # Multi-client: return devices array + first client's fields at top level
    first = get_client_status_for(_clients[0])
    result = {**first, 'devices': [get_client_status_for(c) for c in _clients]}
    return jsonify(result)

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """API endpoint for configuration"""
    if request.method == 'GET':
        # Load config
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()
        return jsonify(config)
    
    elif request.method == 'POST':
        # Save config
        config = request.get_json()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return jsonify({'success': True})

@app.route('/api/logs')
def api_logs():
    """API endpoint for logs"""
    try:
        # Read container logs
        import subprocess
        # Provide helpful message about viewing logs
        logs = [
            "Container logs are available via:",
            "  - docker logs sendspin-client",
            "  - docker compose logs",
            "",
            f"Web interface: v{VERSION} ({BUILD_DATE})",
            "Status: Running normally"
        ]
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return jsonify({'logs': [f'Error reading logs: {e}']})

def main():
    """Start the web interface"""
    port = int(os.getenv('WEB_PORT', 8080))
    logger.info(f"Starting web interface on port {port}")
    serve(app, host='0.0.0.0', port=port, threads=4)

if __name__ == '__main__':
    main()
