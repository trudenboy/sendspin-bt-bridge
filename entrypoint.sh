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
DBUS_STATUS="✗ not found"
if [ -S /var/run/dbus/system_bus_socket ]; then
    DBUS_STATUS="✓ host socket mounted"
    ln -sf /var/run/dbus/system_bus_socket /run/dbus/system_bus_socket 2>/dev/null || true
else
    echo "WARNING: Host D-Bus socket not found, Bluetooth may not work"
fi

# Check if Bluetooth is available on host
BT_STATUS="✗ no controller"
BT_PAIRED=0
if bluetoothctl show 2>&1 | grep -qE "Controller|Discovering|Powered"; then
    BT_ADAPTER=$(bluetoothctl list 2>/dev/null | head -1 | awk '{print $2}' || echo "unknown")
    BT_STATUS="✓ $BT_ADAPTER"
    BT_PAIRED=$(bluetoothctl devices Paired 2>/dev/null | grep -c "Device" || bluetoothctl devices 2>/dev/null | grep -c "Device" || echo "0")
else
    echo "WARNING: No Bluetooth controller available"
fi

# Use host's PipeWire/PulseAudio
AUDIO_STATUS="✗ no socket found"
if [ -n "${PULSE_SERVER:-}" ]; then
    AUDIO_STATUS="✓ pre-configured ($PULSE_SERVER)"
elif [ -S /run/audio/pulse.sock ]; then
    export PULSE_SERVER=unix:/run/audio/pulse.sock
    AUDIO_STATUS="✓ HA audio bridge (/run/audio/pulse.sock)"
elif [ -S "/run/user/${AUDIO_UID:-1000}/pulse/native" ]; then
    export PULSE_SERVER="unix:/run/user/${AUDIO_UID:-1000}/pulse/native"
    AUDIO_STATUS="✓ PulseAudio (/run/user/${AUDIO_UID:-1000}/pulse/native)"
elif [ -S "/run/user/${AUDIO_UID:-1000}/pipewire-0" ]; then
    AUDIO_STATUS="✓ PipeWire (/run/user/${AUDIO_UID:-1000}/pipewire-0)"
else
    echo "WARNING: Host audio socket not found"
fi

# Detect platform
PLATFORM=$(uname -m)
case "$PLATFORM" in
  x86_64)  PLATFORM_LABEL="amd64" ;;
  aarch64) PLATFORM_LABEL="arm64" ;;
  armv7l)  PLATFORM_LABEL="armv7" ;;
  *)       PLATFORM_LABEL="$PLATFORM" ;;
esac

# Get version from Python
VERSION=$(python3 -c "from config import VERSION; print(VERSION)" 2>/dev/null || echo "unknown")

# Detect config
CONFIG_PATH="${CONFIG_DIR:-/config}/config.json"
if [ -f "$CONFIG_PATH" ]; then
    DEV_COUNT=$(python3 -c "import json; c=json.load(open('$CONFIG_PATH')); print(len(c.get('BLUETOOTH_DEVICES',{})) or (1 if c.get('BLUETOOTH_MAC') else 0))" 2>/dev/null || echo "?")
    CONFIG_STATUS="✓ $CONFIG_PATH ($DEV_COUNT devices)"
else
    CONFIG_STATUS="✗ $CONFIG_PATH not found (will use defaults)"
fi

# MA server setting
MA_SERVER="${SENDSPIN_SERVER:-auto}"

# Sink count
SINK_COUNT=$(pactl list short sinks 2>/dev/null | wc -l | tr -d ' ' || echo "0")

# ── Structured diagnostics ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Sendspin Bridge v${VERSION} Diagnostics"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Platform:    %-38s ║\n" "$PLATFORM ($PLATFORM_LABEL)"
printf "║  Audio:       %-38s ║\n" "$AUDIO_STATUS"
printf "║  Sinks:       %-38s ║\n" "$SINK_COUNT available"
printf "║  Bluetooth:   %-38s ║\n" "$BT_STATUS"
printf "║  Paired:      %-38s ║\n" "$BT_PAIRED devices"
printf "║  D-Bus:       %-38s ║\n" "$DBUS_STATUS"
printf "║  Config:      %-38s ║\n" "$CONFIG_STATUS"
printf "║  MA Server:   %-38s ║\n" "$MA_SERVER"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# In HA addon mode, use /data (persistent volume) for runtime config
if [ -f /data/options.json ]; then
    export CONFIG_DIR=/data
fi

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
