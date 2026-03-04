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

# If BLUETOOTH_MAC is set, configure it as default
if [ -n "${BLUETOOTH_MAC:-}" ]; then
    echo "Will configure Bluetooth audio for $BLUETOOTH_MAC after connection..."

    # Try to restore last volume if saved
    if [ -f /config/config.json ]; then
        LAST_VOLUME=$(python3 -c "import json; print(json.load(open('/config/config.json')).get('LAST_VOLUME', ''))" 2>/dev/null || echo "")
        if [ -n "$LAST_VOLUME" ] && [ "$LAST_VOLUME" -gt 0 ] 2>/dev/null; then
            # Format MAC address for PipeWire (replace : with _)
            BT_SINK="bluez_output.$(echo "$BLUETOOTH_MAC" | tr ':' '_').1"
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
        echo "WARNING: dbus-daemon failed to start session bus — MPRIS will not be available"
    fi
fi

# In HA addon mode, use /data (persistent volume) for runtime config
if [ -f /data/options.json ]; then
    export CONFIG_DIR=/data
fi

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
