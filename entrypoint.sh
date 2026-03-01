#!/bin/bash
set -euo pipefail

echo "=== Starting Sendspin Client Container ==="

# HA Addon mode: /data/options.json is written by HA Supervisor before start.
# Translate it to /config/config.json so the rest of the startup is uniform.
if [ -f /data/options.json ]; then
    echo "HA Addon mode detected — reading /data/options.json"
    mkdir -p /config
    python3 - <<'PYEOF'
import json, os, subprocess, re

with open('/data/options.json') as f:
    opts = json.load(f)

# Timezone: use options value, fall back to TZ env var (set by HA Supervisor),
# then UTC
tz = (opts.get('tz') or '').strip() or os.environ.get('TZ', '') or 'UTC'

# bluetooth_adapters: auto-detect via bluetoothctl, merge with options entries
raw_adapters = opts.get('bluetooth_adapters', []) or []

detected = []
try:
    out = subprocess.check_output(
        ['bluetoothctl', 'list'], stderr=subprocess.DEVNULL, timeout=5
    ).decode()
    # Lines: "Controller AA:BB:CC:DD:EE:FF Name [default]"
    for i, line in enumerate(out.strip().splitlines()):
        m = re.search(r'Controller\s+([0-9A-Fa-f:]{17})\s+(.*?)(\s+\[default\])?$', line)
        if m:
            detected.append({
                'id': f'hci{i}',
                'mac': m.group(1),
                'name': m.group(2).strip() or f'hci{i}'
            })
except Exception:
    pass

# Merge: detected takes precedence for hw fields; options name wins if set
existing_macs = {a['mac']: a for a in detected if a.get('mac')}
existing_ids  = {a['id']:  a for a in detected if a.get('id')}
for a in raw_adapters:
    # If user supplied a name in options, apply it to the matching detected entry
    opt_name = a.get('name', '').strip()
    if a.get('mac') and a['mac'] in existing_macs:
        if opt_name:
            existing_macs[a['mac']]['name'] = opt_name
    elif a.get('id') and a['id'] in existing_ids:
        if opt_name:
            existing_ids[a['id']]['name'] = opt_name
    # Keep manual entries not found in detected
    elif a.get('mac') and a['mac'] not in existing_macs:
        detected.append({'id': a.get('id', ''), 'mac': a['mac'], 'name': opt_name or a.get('id', '')})
    elif a.get('id') and a['id'] not in existing_ids:
        detected.append({'id': a['id'], 'mac': a.get('mac', ''), 'name': opt_name or a['id']})

adapters = detected

config = {
    'SENDSPIN_SERVER':    opts.get('sendspin_server', 'auto'),
    'SENDSPIN_PORT':      str(opts.get('sendspin_port', 9000)),
    'BLUETOOTH_DEVICES':  opts.get('bluetooth_devices', []),
    'BLUETOOTH_ADAPTERS': adapters,
    'TZ':                 tz,
}

# Preserve runtime state (volumes, release/reclaim flags) from previous config
try:
    with open('/config/config.json') as f:
        existing = json.load(f)
    if 'LAST_VOLUMES' in existing:
        config['LAST_VOLUMES'] = existing['LAST_VOLUMES']
    elif 'LAST_VOLUME' in existing:
        config['LAST_VOLUME'] = existing['LAST_VOLUME']
    # Preserve enabled flags (release/reclaim) per device, matched by MAC
    ex_by_mac = {d.get('mac', ''): d for d in existing.get('BLUETOOTH_DEVICES', []) if d.get('mac')}
    for dev in config.get('BLUETOOTH_DEVICES', []):
        mac = dev.get('mac', '')
        if mac in ex_by_mac and 'enabled' in ex_by_mac[mac]:
            dev['enabled'] = ex_by_mac[mac]['enabled']
except (FileNotFoundError, json.JSONDecodeError):
    pass

with open('/config/config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f"Generated /config/config.json with {len(config['BLUETOOTH_DEVICES'])} device(s), TZ={config['TZ']}, {len(config['BLUETOOTH_ADAPTERS'])} adapter(s)")
PYEOF
fi

# Use host's D-Bus (mounted from host)
echo "Using host D-Bus socket..."
if [ -S /var/run/dbus/system_bus_socket ]; then
    echo "Host D-Bus socket found"
    ln -sf /var/run/dbus/system_bus_socket /run/dbus/system_bus_socket 2>/dev/null || true
else
    echo "WARNING: Host D-Bus socket not found, Bluetooth may not work"
fi

# Check if Bluetooth is available on host
echo "Checking for Bluetooth on host..."
if bluetoothctl show 2>&1 | grep -qE "Controller|Discovering|Powered"; then
    echo "Bluetooth controller detected!"
    bluetoothctl show | head -10 || true
else
    echo "WARNING: No Bluetooth controller available"
    echo "Bluetooth functionality will not be available"
fi

# Use host's PipeWire/PulseAudio
echo "Using host audio system (PipeWire/PulseAudio)..."
if [ -n "${PULSE_SERVER:-}" ]; then
    # Already set — by HA Supervisor (audio: true) or the ha-addon run.sh
    echo "Audio bridge pre-configured: $PULSE_SERVER"
elif [ -S /run/audio/pulse.sock ]; then
    # HA OS audio bridge socket (Supervisor injects this path in some versions)
    export PULSE_SERVER=unix:/run/audio/pulse.sock
    echo "HA audio bridge socket found: /run/audio/pulse.sock"
elif [ -S /run/user/1000/pulse/native ]; then
    echo "Host PulseAudio socket found"
    export PULSE_SERVER=unix:/run/user/1000/pulse/native
elif [ -S /run/user/1000/pipewire-0 ]; then
    echo "Host PipeWire socket found"
else
    echo "WARNING: Host audio socket not found"
fi

# List available audio sinks for debugging
echo "Available audio sinks:"
pactl list short sinks 2>/dev/null || echo "Could not list sinks"

# If BLUETOOTH_MAC is set, configure it as default
if [ -n "${BLUETOOTH_MAC:-}" ]; then
    echo "Will configure Bluetooth audio for $BLUETOOTH_MAC after connection..."
    
    # Try to restore last volume if saved
    if [ -f /config/config.json ]; then
        LAST_VOLUME=$(python3 -c "import json; print(json.load(open('/config/config.json')).get('LAST_VOLUME', ''))" 2>/dev/null || echo "")
        if [ -n "$LAST_VOLUME" ] && [ "$LAST_VOLUME" -gt 0 ] 2>/dev/null; then
            # Format MAC address for PipeWire (replace : with _)
            BT_SINK="bluez_output.$(echo $BLUETOOTH_MAC | tr ':' '_').1"
            echo "Attempting to restore volume to $LAST_VOLUME%..."
            sleep 3  # Give PipeWire time to detect the device
            if pactl set-sink-volume "$BT_SINK" "${LAST_VOLUME}%" 2>/dev/null; then
                echo "✓ Restored volume to $LAST_VOLUME%"
            else
                echo "Could not restore volume (device may not be ready yet)"
            fi
        fi
    fi
fi

# Start a D-Bus session bus so sendspin's MPRIS interface works (track/artist metadata)
if command -v dbus-daemon > /dev/null 2>&1; then
    DBUS_ADDR=$(dbus-daemon --session --fork --print-address 2>/dev/null || true)
    if [ -n "$DBUS_ADDR" ]; then
        export DBUS_SESSION_BUS_ADDRESS="$DBUS_ADDR"
        echo "D-Bus session bus started: $DBUS_SESSION_BUS_ADDRESS"
    else
        echo "WARNING: dbus-daemon failed to start session bus"
    fi
fi

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
