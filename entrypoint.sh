#!/bin/bash
set -e

echo "=== Starting Sendspin Client Container ==="

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
    bluetoothctl show | head -10
else
    echo "WARNING: No Bluetooth controller available"
    echo "Bluetooth functionality will not be available"
fi

# Use host's PipeWire/PulseAudio
echo "Using host audio system (PipeWire/PulseAudio)..."
if [ -S /run/user/1000/pulse/native ]; then
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
if [ -n "$BLUETOOTH_MAC" ]; then
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

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
