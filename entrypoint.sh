#!/bin/bash
set -euo pipefail

echo "=== Starting Sendspin Client Container ==="

# HA Addon mode: /data/options.json is written by HA Supervisor before start.
# Translate it to /data/config.json so the rest of the startup is uniform.
if [ -f /data/options.json ]; then
    echo "HA Addon mode detected — reading /data/options.json"
    python3 /app/scripts/translate_ha_config.py
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


# In HA addon mode, use /data (persistent volume) for runtime config
if [ -f /data/options.json ]; then
    export CONFIG_DIR=/data
fi

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
