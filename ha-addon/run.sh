#!/bin/bash
# Home Assistant Supervisor entry point for Sendspin Bluetooth Bridge
# Reads /data/options.json provided by HA Supervisor, generates the app's
# /config/config.json, configures audio, then execs the main entrypoint.

set -euo pipefail

echo "=== Sendspin Bluetooth Bridge - HA Addon Startup ==="

OPTIONS_FILE="/data/options.json"

if [ ! -f "$OPTIONS_FILE" ]; then
    echo "ERROR: $OPTIONS_FILE not found (expected HA Supervisor options)"
    exit 1
fi

echo "Reading addon options from $OPTIONS_FILE..."

# Ensure config directory exists
mkdir -p /config

# Translate /data/options.json → /config/config.json
python3 - <<'EOF'
import json, sys, os

with open('/data/options.json') as f:
    opts = json.load(f)

# Timezone: use options value, or auto-detect from Supervisor
tz = (opts.get('tz') or '').strip()
if not tz:
    try:
        import urllib.request
        token = os.environ.get('SUPERVISOR_TOKEN', '')
        req = urllib.request.Request(
            'http://supervisor/host/info',
            headers={'Authorization': f'Bearer {token}'}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            host_info = json.load(r)
            tz = host_info.get('data', {}).get('timezone', '') or ''
    except Exception:
        pass
if not tz:
    tz = 'UTC'

# bluetooth_adapters: convert [{id, mac?}] → [{id, mac, name}]
raw_adapters = opts.get('bluetooth_adapters', []) or []
adapters = [{'id': a['id'], 'mac': a.get('mac', ''), 'name': a['id']}
            for a in raw_adapters if a.get('id')]

config = {
    'SENDSPIN_SERVER':    opts.get('sendspin_server', 'auto'),
    'SENDSPIN_PORT':      str(opts.get('sendspin_port', 9000)),
    'BLUETOOTH_DEVICES':  opts.get('bluetooth_devices', []),
    'BLUETOOTH_ADAPTERS': adapters,
    'TZ':                 tz,
}

# Preserve LAST_VOLUME if already saved by the app
existing = {}
try:
    with open('/config/config.json') as f:
        existing = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    pass

if 'LAST_VOLUME' in existing:
    config['LAST_VOLUME'] = existing['LAST_VOLUME']

with open('/config/config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f"Generated /config/config.json with {len(config['BLUETOOTH_DEVICES'])} device(s), TZ={config['TZ']}, {len(config['BLUETOOTH_ADAPTERS'])} adapter(s)")
EOF

# Signal to entrypoint.sh that config is already generated (skip its duplicate block)
export HA_ADDON_CONFIG_DONE=1

# HA Supervisor audio bridge setup
# Supervisor injects PULSE_SERVER when `audio: true` is set in config.yaml.
# If it's already set, trust it. Otherwise fall back to known HA OS paths.
if [ -n "${PULSE_SERVER:-}" ]; then
    echo "Using HA Supervisor audio bridge: $PULSE_SERVER"
elif [ -S /run/audio/pulse.sock ]; then
    export PULSE_SERVER=unix:/run/audio/pulse.sock
    echo "Using HA audio socket: /run/audio/pulse.sock"
elif [ -S /run/user/1000/pulse/native ]; then
    export PULSE_SERVER=unix:/run/user/1000/pulse/native
    echo "Using PulseAudio socket: /run/user/1000/pulse/native"
else
    echo "WARNING: No audio socket found. Audio routing may not work."
fi

# Hand off to the main entrypoint (D-Bus, Bluetooth checks, app start)
exec /app/entrypoint.sh
