#!/bin/bash
set -euo pipefail

# Ensure cwd is /app — S6 overlay starts services from / regardless of WORKDIR
cd /app

echo "=== Starting Sendspin Client Container ==="

RUNTIME_UID=$(id -u 2>/dev/null || echo "?")
RUNTIME_GID=$(id -g 2>/dev/null || echo "?")
RUNTIME_USER=$(id -un 2>/dev/null || echo "unknown")
APP_RUNTIME_UID="$RUNTIME_UID"
APP_RUNTIME_GID="$RUNTIME_GID"
APP_RUNTIME_USER="$RUNTIME_USER"
APP_RUNTIME_SPEC=""
APP_RUNTIME_HOME=""
AUDIO_SOCKET_GID=""
STARTUP_WAIT_STATUS="not needed"
STARTUP_WAIT_ERROR=""
CONFIG_PATH=""
CONFIG_STATUS="✗ config not checked"
DEV_COUNT=0
BT_STATUS="✗ no controller"
BT_PAIRED=0
DBUS_STATUS="✗ not found"
AUDIO_STATUS="✗ no socket found"
AUDIO_SOCKET_PATH=""
AUDIO_SOCKET_OWNER=""
AUDIO_PROBE_STATUS="not attempted"
AUDIO_PROBE_ERROR=""
AUDIO_WARNING=""
AUDIO_HINT=""
BLUEZ_VERSION="unknown"
AUDIO_SERVER_VERSION="unknown"
KERNEL_VERSION="unknown"
PYTHON_VERSION="unknown"

_probe_pactl_as_runtime() {
    if ! command -v pactl >/dev/null 2>&1; then
        return 1
    fi
    if [ -n "$APP_RUNTIME_SPEC" ] && command -v gosu >/dev/null 2>&1; then
        gosu "$APP_RUNTIME_SPEC" pactl info >/tmp/sendspin-pactl-info.log 2>&1
        return $?
    fi
    pactl info >/tmp/sendspin-pactl-info.log 2>&1
}

_prepare_runtime_paths() {
    if [ -z "$APP_RUNTIME_SPEC" ] || [ "$APP_RUNTIME_UID" = "$RUNTIME_UID" ]; then
        return 0
    fi

    APP_RUNTIME_HOME="/tmp/sendspin-runtime-${APP_RUNTIME_UID}"
    mkdir -p "$APP_RUNTIME_HOME"
    chown "$APP_RUNTIME_UID:$APP_RUNTIME_GID" "$APP_RUNTIME_HOME" 2>/dev/null || true

    CONFIG_RUNTIME_DIR="${CONFIG_DIR:-/config}"
    if [ -e "$CONFIG_RUNTIME_DIR" ]; then
        echo "Preparing ${CONFIG_RUNTIME_DIR} for runtime UID ${APP_RUNTIME_UID}:${APP_RUNTIME_GID}"
        if ! chown -R "$APP_RUNTIME_UID:$APP_RUNTIME_GID" "$CONFIG_RUNTIME_DIR" 2>/dev/null; then
            echo "WARNING: Could not update ownership for ${CONFIG_RUNTIME_DIR}; config writes may fail"
        fi
    fi
}

_refresh_config_diagnostics() {
    CONFIG_PATH="${CONFIG_DIR:-/config}/config.json"
    DEV_COUNT=0
    if [ -f "$CONFIG_PATH" ]; then
        DEV_COUNT=$(python3 -c "import json; c=json.load(open('$CONFIG_PATH')); print(len(c.get('BLUETOOTH_DEVICES',[])))" 2>/dev/null || echo "?")
        CONFIG_STATUS="✓ $CONFIG_PATH ($DEV_COUNT devices)"
    else
        CONFIG_STATUS="✗ $CONFIG_PATH not found (will use defaults)"
    fi
}

_configured_devices_present() {
    [ "${DEV_COUNT:-0}" != "?" ] && [ "${DEV_COUNT:-0}" -gt 0 ] 2>/dev/null
}

_refresh_dbus_status() {
    DBUS_STATUS="✗ not found"
    if [ -S /var/run/dbus/system_bus_socket ]; then
        DBUS_STATUS="✓ host socket mounted"
        ln -sf /var/run/dbus/system_bus_socket /run/dbus/system_bus_socket 2>/dev/null || true
        return 0
    fi
    return 1
}

_refresh_bluetooth_status() {
    BT_STATUS="✗ no controller"
    BT_PAIRED=0
    # Capture BlueZ (bluetoothctl) version — critical for diagnosing upstream
    # regressions such as bluez/bluez#1922 (5.86 dual-role A2DP sink).
    if command -v bluetoothctl >/dev/null 2>&1; then
        BLUEZ_VERSION=$(bluetoothctl --version 2>/dev/null | awk '{print $NF}')
        [ -z "$BLUEZ_VERSION" ] && BLUEZ_VERSION="unknown"
    fi
    # Unblock Bluetooth RF-kill switch (common on Raspberry Pi built-in adapters)
    if command -v rfkill >/dev/null 2>&1; then
        rfkill unblock bluetooth 2>/dev/null || true
    fi
    if bluetoothctl show 2>&1 | grep -qE "Controller|Discovering|Powered"; then
        BT_ADAPTER=$(bluetoothctl list 2>/dev/null | head -1 | awk '{print $2}' || echo "unknown")
        BT_STATUS="✓ $BT_ADAPTER"
        BT_PAIRED=$(bluetoothctl devices Paired 2>/dev/null | grep -c "Device" || bluetoothctl devices 2>/dev/null | grep -c "Device" || echo "0")
        return 0
    fi
    return 1
}

_refresh_audio_server_version() {
    AUDIO_SERVER_VERSION="unknown"
    if [ ! -r /tmp/sendspin-pactl-info.log ]; then
        return
    fi
    local name ver
    name=$(grep -i "^Server Name:" /tmp/sendspin-pactl-info.log | head -1 | cut -d: -f2- | sed 's/^ *//;s/ *$//')
    ver=$(grep -i "^Server Version:" /tmp/sendspin-pactl-info.log | head -1 | cut -d: -f2- | sed 's/^ *//;s/ *$//')
    if [ -n "$name" ] && [ -n "$ver" ]; then
        AUDIO_SERVER_VERSION="$name $ver"
    elif [ -n "$name" ]; then
        AUDIO_SERVER_VERSION="$name"
    elif [ -n "$ver" ]; then
        AUDIO_SERVER_VERSION="$ver"
    fi
}

_refresh_audio_runtime_detection() {
    APP_RUNTIME_UID="$RUNTIME_UID"
    APP_RUNTIME_GID="$RUNTIME_GID"
    APP_RUNTIME_USER="$RUNTIME_USER"
    APP_RUNTIME_SPEC=""
    APP_RUNTIME_HOME=""
    AUDIO_SOCKET_GID=""
    AUDIO_SOCKET_OWNER=""
    AUDIO_PROBE_STATUS="not attempted"
    AUDIO_PROBE_ERROR=""
    AUDIO_WARNING=""
    AUDIO_HINT=""
    AUDIO_STATUS="✗ no socket found"
    AUDIO_SOCKET_PATH=""

    if [ -n "${PULSE_SERVER:-}" ]; then
        case "$PULSE_SERVER" in
            unix:*)
                AUDIO_SOCKET_PATH="${PULSE_SERVER#unix:}"
                if [ -S "$AUDIO_SOCKET_PATH" ]; then
                    AUDIO_STATUS="✓ pre-configured ($PULSE_SERVER)"
                else
                    AUDIO_STATUS="… waiting for pre-configured socket ($AUDIO_SOCKET_PATH)"
                fi
                ;;
            *)
                AUDIO_STATUS="✓ pre-configured ($PULSE_SERVER)"
                ;;
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
    fi

    if [ -n "$AUDIO_SOCKET_PATH" ] && [ -S "$AUDIO_SOCKET_PATH" ]; then
        SOCKET_UID=$(stat -c '%u' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
        SOCKET_GID=$(stat -c '%g' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
        SOCKET_MODE=$(stat -c '%a' "$AUDIO_SOCKET_PATH" 2>/dev/null || echo "?")
        if [ "$SOCKET_GID" != "?" ]; then
            AUDIO_SOCKET_GID="$SOCKET_GID"
        fi
        AUDIO_SOCKET_OWNER="${SOCKET_UID}:${SOCKET_GID} mode ${SOCKET_MODE}"
    fi

    case "$AUDIO_SOCKET_PATH" in
        /run/user/*)
            SOCKET_RUNTIME_UID=$(printf '%s' "$AUDIO_SOCKET_PATH" | cut -d/ -f4)
            if [ -n "$SOCKET_RUNTIME_UID" ] && [ "$RUNTIME_UID" = "0" ] && [ "$SOCKET_RUNTIME_UID" != "0" ]; then
                APP_RUNTIME_UID="${AUDIO_UID:-$SOCKET_RUNTIME_UID}"
                APP_RUNTIME_GID="${AUDIO_GID:-${AUDIO_SOCKET_GID:-$APP_RUNTIME_UID}}"
                APP_RUNTIME_USER="audio-runtime-${APP_RUNTIME_UID}"
                APP_RUNTIME_SPEC="${APP_RUNTIME_UID}:${APP_RUNTIME_GID}"
                AUDIO_WARNING="User-scoped audio socket targets UID ${APP_RUNTIME_UID}; container init stays root but the bridge process will drop to UID ${APP_RUNTIME_UID}"
                # shellcheck disable=SC2016  # backticks are intentional markdown-style code in the operator-facing hint
                AUDIO_HINT='Recent images auto-run the bridge process as AUDIO_UID for user-scoped audio sockets; a global Docker Compose `user:` override should only be a temporary diagnostic step on older images'
            elif [ -n "$SOCKET_RUNTIME_UID" ] && [ "$SOCKET_RUNTIME_UID" != "$RUNTIME_UID" ]; then
                AUDIO_WARNING="User-scoped audio socket targets UID ${SOCKET_RUNTIME_UID}, but container runs as UID ${RUNTIME_UID}"
                AUDIO_HINT='Check that the bridge process is running as the same UID as the mounted user-scoped audio socket'
            fi
            ;;
    esac

    if [ -n "$APP_RUNTIME_SPEC" ] && ! command -v gosu >/dev/null 2>&1; then
        AUDIO_WARNING="User-scoped audio socket detected but gosu is unavailable"
        AUDIO_HINT="Update to a newer image that includes automatic AUDIO_UID privilege drop"
        APP_RUNTIME_UID="$RUNTIME_UID"
        APP_RUNTIME_GID="$RUNTIME_GID"
        APP_RUNTIME_USER="$RUNTIME_USER"
        APP_RUNTIME_SPEC=""
    fi
}

_refresh_audio_probe_status() {
    _refresh_audio_runtime_detection

    if ! command -v pactl >/dev/null 2>&1; then
        AUDIO_PROBE_STATUS="✗ pactl unavailable"
        AUDIO_PROBE_ERROR="pactl is not installed in the container"
        return 1
    fi

    if _probe_pactl_as_runtime; then
        AUDIO_PROBE_STATUS="✓ pactl info ok"
        _refresh_audio_server_version
        return 0
    fi

    _refresh_audio_server_version
    AUDIO_PROBE_STATUS="✗ pactl info failed"
    AUDIO_PROBE_ERROR=$(head -1 /tmp/sendspin-pactl-info.log 2>/dev/null | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')
    if [ -z "$AUDIO_PROBE_ERROR" ]; then
        AUDIO_PROBE_ERROR="No response from PulseAudio/PipeWire server"
    fi
    if [ -z "$AUDIO_HINT" ] && { [ -n "$AUDIO_SOCKET_PATH" ] || [ -n "${PULSE_SERVER:-}" ]; }; then
        # shellcheck disable=SC2016  # backticks are intentional markdown-style code in the operator-facing hint
        AUDIO_HINT='Check the bridge process UID, mounted audio socket path, and `PULSE_SERVER`/`XDG_RUNTIME_DIR` values'
    fi
    return 1
}

_wait_for_startup_dependencies() {
    STARTUP_WAIT_STATUS="not needed"
    STARTUP_WAIT_ERROR=""

    if ! _configured_devices_present; then
        return 0
    fi

    WAIT_ATTEMPTS="${STARTUP_DEPENDENCY_WAIT_ATTEMPTS:-${AUDIO_RUNTIME_WAIT_ATTEMPTS:-45}}"
    WAIT_DELAY="${STARTUP_DEPENDENCY_WAIT_DELAY_SECONDS:-${AUDIO_RUNTIME_WAIT_DELAY_SECONDS:-1}}"
    ATTEMPT=1

    while [ "$ATTEMPT" -le "$WAIT_ATTEMPTS" ]; do
        missing=()
        _refresh_dbus_status || missing+=("D-Bus socket")
        _refresh_bluetooth_status || missing+=("Bluetooth controller")
        _refresh_audio_probe_status || missing+=("audio server")

        if [ "${#missing[@]}" -eq 0 ]; then
            if [ "$ATTEMPT" -gt 1 ]; then
                STARTUP_WAIT_STATUS="✓ after ${ATTEMPT} checks"
            else
                STARTUP_WAIT_STATUS="✓ ready immediately"
            fi
            STARTUP_WAIT_ERROR=""
            return 0
        fi

        missing_summary=$(printf '%s, ' "${missing[@]}")
        missing_summary=${missing_summary%, }
        STARTUP_WAIT_ERROR="Still waiting for ${missing_summary}"

        if [ "$ATTEMPT" -lt "$WAIT_ATTEMPTS" ]; then
            echo "Waiting for startup dependencies before launching bridge (attempt ${ATTEMPT}/${WAIT_ATTEMPTS}): ${missing_summary}"
            sleep "$WAIT_DELAY"
        fi

        ATTEMPT=$((ATTEMPT + 1))
    done

    STARTUP_WAIT_STATUS="✗ timed out after ${WAIT_ATTEMPTS} checks"
    echo "WARNING: Startup dependency wait timed out. ${STARTUP_WAIT_ERROR}"
    return 1
}

_exec_sendspin_client() {
    if [ -n "$APP_RUNTIME_SPEC" ] && command -v gosu >/dev/null 2>&1; then
        export HOME="${APP_RUNTIME_HOME:-/tmp/sendspin-runtime-${APP_RUNTIME_UID}}"
        export USER="$APP_RUNTIME_USER"
        echo "Starting Sendspin client with web interface as UID ${APP_RUNTIME_UID}:${APP_RUNTIME_GID}..."
        exec gosu "$APP_RUNTIME_SPEC" env HOME="$HOME" USER="$USER" python3 /app/sendspin_client.py
    fi

    echo "Starting Sendspin client with web interface..."
    exec python3 /app/sendspin_client.py
}

# HA Addon mode: /data/options.json is written by HA Supervisor before start.
# Translate it to /data/config.json so the rest of the startup is uniform.
if [ -f /data/options.json ]; then
    echo "HA Addon mode detected — reading /data/options.json"
    export CONFIG_DIR=/data
    python3 /app/scripts/translate_ha_config.py
fi

_refresh_config_diagnostics
_wait_for_startup_dependencies || true
_refresh_dbus_status || true
if ! _configured_devices_present; then
    _refresh_bluetooth_status || true
fi
_refresh_audio_probe_status || true

if ! _refresh_dbus_status; then
    echo "WARNING: Host D-Bus socket not found, Bluetooth may not work"
fi
if ! _refresh_bluetooth_status; then
    echo "WARNING: No Bluetooth controller available"
fi
if [ "$AUDIO_STATUS" = "✗ no socket found" ]; then
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

KERNEL_VERSION=$(uname -r 2>/dev/null || echo "unknown")
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
[ -z "$PYTHON_VERSION" ] && PYTHON_VERSION="unknown"

# Get version from Python
VERSION=$(python3 -c "from config import VERSION; print(VERSION)" 2>/dev/null || echo "unknown")

# MA server setting
MA_SERVER="${SENDSPIN_SERVER:-auto}"

# Sink count
if [ -n "$APP_RUNTIME_SPEC" ] && command -v gosu >/dev/null 2>&1; then
    SINK_COUNT=$(gosu "$APP_RUNTIME_SPEC" pactl list short sinks 2>/dev/null | wc -l | tr -d ' ' || echo "0")
else
    SINK_COUNT=$(pactl list short sinks 2>/dev/null | wc -l | tr -d ' ' || echo "0")
fi

# ── Structured diagnostics ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Sendspin Bridge v${VERSION} Diagnostics"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Platform:    %-38s ║\n" "$PLATFORM ($PLATFORM_LABEL)"
printf "║  Kernel:      %-38s ║\n" "$KERNEL_VERSION"
printf "║  Python:      %-38s ║\n" "$PYTHON_VERSION"
printf "║  Audio:       %-38s ║\n" "$AUDIO_STATUS"
printf "║  Audio Srv:   %-38s ║\n" "$AUDIO_SERVER_VERSION"
printf "║  Init UID:    %-38s ║\n" "$RUNTIME_UID:$RUNTIME_GID ($RUNTIME_USER)"
printf "║  App UID:     %-38s ║\n" "$APP_RUNTIME_UID:$APP_RUNTIME_GID ($APP_RUNTIME_USER)"
printf "║  Audio Probe: %-38s ║\n" "$AUDIO_PROBE_STATUS"
printf "║  Startup Wait:%-38s ║\n" " $STARTUP_WAIT_STATUS"
printf "║  Sinks:       %-38s ║\n" "$SINK_COUNT available"
printf "║  Bluetooth:   %-38s ║\n" "$BT_STATUS"
printf "║  BlueZ:       %-38s ║\n" "$BLUEZ_VERSION"
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
if [ -n "$STARTUP_WAIT_ERROR" ]; then
    echo "Startup wait detail: $STARTUP_WAIT_ERROR"
fi
if [ -n "$AUDIO_SOCKET_PATH" ] || [ -n "$AUDIO_PROBE_ERROR" ] || [ -n "$AUDIO_WARNING" ]; then
    echo ""
fi

_prepare_runtime_paths
_exec_sendspin_client
