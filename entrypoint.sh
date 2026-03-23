#!/bin/bash
set -euo pipefail

# Ensure cwd is /app — S6 overlay starts services from / regardless of WORKDIR
cd /app

echo "=== Starting Sendspin Client Container ==="

RUNTIME_UID=$(id -u 2>/dev/null || echo "?")
RUNTIME_GID=$(id -g 2>/dev/null || echo "?")
RUNTIME_USER=$(id -un 2>/dev/null || echo "unknown")

# HA Addon mode: /data/options.json is written by HA Supervisor before start.
# Translate it to /data/config.json so the rest of the startup is uniform.
if [ -f /data/options.json ]; then
    echo "HA Addon mode detected — reading /data/options.json"
    export CONFIG_DIR=/data
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
AUDIO_SOCKET_PATH=""
AUDIO_SOCKET_OWNER=""
AUDIO_PROBE_STATUS="not attempted"
AUDIO_PROBE_ERROR=""
AUDIO_WARNING=""
AUDIO_HINT=""
if [ -n "${PULSE_SERVER:-}" ]; then
    AUDIO_STATUS="✓ pre-configured ($PULSE_SERVER)"
    case "$PULSE_SERVER" in
        unix:*) AUDIO_SOCKET_PATH="${PULSE_SERVER#unix:}" ;;
    esac
elif [ -S /run/audio/pulse.sock ]; then
    export PULSE_SERVER=unix:/run/audio/pulse.sock
    AUDIO_STATUS="✓ HA audio bridge (/run/audio/pulse.sock)"
    AUDIO_SOCKET_PATH="/run/audio/pulse.sock"
elif [ -S "/run/user/${AUDIO_UID:-1000}/pulse/native" ]; then
    export PULSE_SERVER="unix:/run/user/${AUDIO_UID:-1000}/pulse/native"
    AUDIO_STATUS="✓ PulseAudio (/run/user/${AUDIO_UID:-1000}/pulse/native)"
    AUDIO_SOCKET_PATH="/run/user/${AUDIO_UID:-1000}/pulse/native"
elif [ -S "/run/user/${AUDIO_UID:-1000}/pipewire-0" ]; then
    AUDIO_STATUS="✓ PipeWire (/run/user/${AUDIO_UID:-1000}/pipewire-0)"
    AUDIO_SOCKET_PATH="/run/user/${AUDIO_UID:-1000}/pipewire-0"
else
    echo "WARNING: Host audio socket not found"
fi

if [ -n "$AUDIO_SOCKET_PATH" ] && [ -S "$AUDIO_SOCKET_PATH" ]; then
    SOCKET_UID=$(stat -c '%u' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
    SOCKET_GID=$(stat -c '%g' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
    SOCKET_MODE=$(stat -c '%a' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
    AUDIO_SOCKET_OWNER="${SOCKET_UID}:${SOCKET_GID} mode ${SOCKET_MODE}"
    case "$AUDIO_SOCKET_PATH" in
        /run/user/*)
            SOCKET_RUNTIME_UID=$(printf '%s' "$AUDIO_SOCKET_PATH" | cut -d/ -f4)
            if [ -n "$SOCKET_RUNTIME_UID" ] && [ "$SOCKET_RUNTIME_UID" != "$RUNTIME_UID" ]; then
                AUDIO_WARNING="User-scoped audio socket targets UID ${SOCKET_RUNTIME_UID}, but container runs as UID ${RUNTIME_UID}"
                AUDIO_HINT='If `pactl` shows "Connection refused", try a diagnostic Docker Compose override: user: "${AUDIO_UID:-1000}:${AUDIO_UID:-1000}"'
            fi
            ;;
    esac
fi

if command -v pactl >/dev/null 2>&1; then
    if pactl info >/tmp/sendspin-pactl-info.log 2>&1; then
        AUDIO_PROBE_STATUS="✓ pactl info ok"
    else
        AUDIO_PROBE_STATUS="✗ pactl info failed"
        AUDIO_PROBE_ERROR=$(head -1 /tmp/sendspin-pactl-info.log 2>/dev/null | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')
        if [ -z "$AUDIO_PROBE_ERROR" ]; then
            AUDIO_PROBE_ERROR="No response from PulseAudio/PipeWire server"
        fi
        if [ -z "$AUDIO_HINT" ] && [ -n "$AUDIO_SOCKET_PATH" ]; then
            AUDIO_HINT='Check container UID, mounted audio socket path, and `PULSE_SERVER`/`XDG_RUNTIME_DIR` values'
        fi
    fi
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
    DEV_COUNT=$(python3 -c "import json; c=json.load(open('$CONFIG_PATH')); print(len(c.get('BLUETOOTH_DEVICES',[])))" 2>/dev/null || echo "?")
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
printf "║  Runtime UID: %-38s ║\n" "$RUNTIME_UID:$RUNTIME_GID ($RUNTIME_USER)"
printf "║  Audio Probe: %-38s ║\n" "$AUDIO_PROBE_STATUS"
printf "║  Sinks:       %-38s ║\n" "$SINK_COUNT available"
printf "║  Bluetooth:   %-38s ║\n" "$BT_STATUS"
printf "║  Paired:      %-38s ║\n" "$BT_PAIRED devices"
printf "║  D-Bus:       %-38s ║\n" "$DBUS_STATUS"
printf "║  Config:      %-38s ║\n" "$CONFIG_STATUS"
printf "║  MA Server:   %-38s ║\n" "$MA_SERVER"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

if [ -n "$AUDIO_SOCKET_PATH" ]; then
    echo "Audio socket path: $AUDIO_SOCKET_PATH"
fi
if [ -n "$AUDIO_SOCKET_OWNER" ]; then
    echo "Audio socket owner: $AUDIO_SOCKET_OWNER"
fi
if [ -n "$AUDIO_PROBE_ERROR" ]; then
    echo "Audio probe error: $AUDIO_PROBE_ERROR"
fi
if [ -n "$AUDIO_WARNING" ]; then
    echo "WARNING: $AUDIO_WARNING"
fi
if [ -n "$AUDIO_HINT" ]; then
    echo "Hint: $AUDIO_HINT"
fi
if [ -n "$AUDIO_SOCKET_PATH" ] || [ -n "$AUDIO_PROBE_ERROR" ] || [ -n "$AUDIO_WARNING" ]; then
    echo ""
fi

# Start the Sendspin client (includes web interface)
echo "Starting Sendspin client with web interface..."
exec python3 /app/sendspin_client.py
