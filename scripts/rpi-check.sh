#!/usr/bin/env bash
# rpi-check.sh — Pre-flight diagnostics for Sendspin Bluetooth Bridge
#
# Run on the Docker host BEFORE starting the container:
#   curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
#
# Checks: Docker, Bluetooth, audio system, UID, memory, architecture.
# Outputs recommended .env values for docker-compose.yml.

set -euo pipefail

# ── Helpers ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

pass=0; warn=0; fail=0

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; ((pass++)); }
skip() { echo -e "  ${YELLOW}⚠️  $1${NC}"; ((warn++)); }
bad()  { echo -e "  ${RED}❌ $1${NC}"; ((fail++)); }
info() { echo -e "  ${CYAN}ℹ  $1${NC}"; }

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Sendspin Bluetooth Bridge — Pre-flight Check${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

# ── 1. Architecture ─────────────────────────────────────────────────────────
echo -e "${BOLD}1. Platform${NC}"
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)   ok "Architecture: $ARCH (amd64) — fully supported" ;;
  aarch64)  ok "Architecture: $ARCH (arm64) — fully supported" ;;
  armv7l|armhf)
    skip "Architecture: $ARCH (armv7) — best-effort; 1–2 speakers recommended"
    ;;
  *)        skip "Architecture: $ARCH — untested" ;;
esac

# Detect Raspberry Pi model
if [ -f /proc/device-tree/model ]; then
  MODEL=$(tr -d '\0' < /proc/device-tree/model)
  info "Hardware: $MODEL"
fi
echo ""

# ── 2. Memory ────────────────────────────────────────────────────────────────
echo -e "${BOLD}2. Memory${NC}"
if command -v free &>/dev/null; then
  MEM_MB=$(free -m | awk '/^Mem:/ {print $2}')
  if [ "$MEM_MB" -ge 1024 ]; then
    ok "RAM: ${MEM_MB} MB — sufficient for multiple speakers"
  elif [ "$MEM_MB" -ge 512 ]; then
    skip "RAM: ${MEM_MB} MB — sufficient for 1–2 speakers"
  else
    bad "RAM: ${MEM_MB} MB — may be too low; recommend ≥512 MB"
  fi
else
  skip "Cannot detect memory (free command not found)"
fi
echo ""

# ── 3. Docker ────────────────────────────────────────────────────────────────
echo -e "${BOLD}3. Docker${NC}"
if command -v docker &>/dev/null; then
  DOCKER_VER=$(docker --version 2>/dev/null | head -1)
  ok "Docker installed: $DOCKER_VER"

  if docker info &>/dev/null; then
    ok "Docker daemon is running"
  else
    bad "Docker daemon is not running or current user has no access"
    info "Try: sudo systemctl start docker && sudo usermod -aG docker \$USER"
  fi
else
  bad "Docker is not installed"
  info "Install: curl -fsSL https://get.docker.com | sh"
fi

if command -v docker &>/dev/null && docker compose version &>/dev/null; then
  ok "Docker Compose available"
else
  skip "Docker Compose plugin not found — install with: sudo apt install docker-compose-plugin"
fi
echo ""

# ── 4. Bluetooth ─────────────────────────────────────────────────────────────
echo -e "${BOLD}4. Bluetooth${NC}"
BT_OK=false
if systemctl is-active bluetooth &>/dev/null; then
  ok "bluetoothd service is running"
  BT_OK=true
elif command -v bluetoothctl &>/dev/null; then
  skip "bluetoothd service not detected via systemctl, but bluetoothctl is available"
  BT_OK=true
else
  bad "bluetoothd is not running and bluetoothctl not found"
  info "Install: sudo apt install bluez && sudo systemctl enable --now bluetooth"
fi

PAIRED_MACS=()
if $BT_OK && command -v bluetoothctl &>/dev/null; then
  # Check for controller
  if bluetoothctl list 2>/dev/null | grep -q "Controller"; then
    CTRL=$(bluetoothctl list 2>/dev/null | head -1)
    ok "BT controller found: $CTRL"
  else
    bad "No Bluetooth controller detected"
    info "Check USB adapter connection or built-in BT"
  fi

  # Check for paired devices
  PAIRED=$(bluetoothctl devices Paired 2>/dev/null || bluetoothctl devices 2>/dev/null || echo "")
  if [ -n "$PAIRED" ]; then
    PAIR_COUNT=$(echo "$PAIRED" | grep -c "Device" || true)
    ok "Paired devices: $PAIR_COUNT"
    while IFS= read -r line; do
      MAC=$(echo "$line" | awk '{print $2}')
      NAME=$(echo "$line" | cut -d' ' -f3-)
      info "  $MAC — $NAME"
      PAIRED_MACS+=("$MAC")
    done <<< "$PAIRED"
  else
    skip "No paired Bluetooth devices found"
    info "Pair your speaker first: bluetoothctl → scan on → pair <MAC> → trust <MAC>"
  fi
fi
echo ""

# ── 5. Audio System ──────────────────────────────────────────────────────────
echo -e "${BOLD}5. Audio System${NC}"
AUDIO_SYSTEM="unknown"
AUDIO_SOCKET=""
DETECTED_UID=$(id -u)

# Check PipeWire
if command -v pw-cli &>/dev/null && pw-cli info 0 &>/dev/null; then
  ok "PipeWire is running"
  AUDIO_SYSTEM="pipewire"
elif systemctl --user is-active pipewire &>/dev/null 2>&1; then
  ok "PipeWire service is active"
  AUDIO_SYSTEM="pipewire"
fi

# Check PulseAudio (or pipewire-pulse)
if command -v pactl &>/dev/null && pactl info &>/dev/null 2>&1; then
  PA_SERVER=$(pactl info 2>/dev/null | grep "Server Name" | cut -d: -f2- | xargs)
  if echo "$PA_SERVER" | grep -qi pipewire; then
    ok "PulseAudio API available (via PipeWire): $PA_SERVER"
    AUDIO_SYSTEM="pipewire"
  else
    ok "PulseAudio is running: $PA_SERVER"
    [ "$AUDIO_SYSTEM" = "unknown" ] && AUDIO_SYSTEM="pulseaudio"
  fi
elif [ "$AUDIO_SYSTEM" = "unknown" ]; then
  bad "No audio system detected (PulseAudio or PipeWire)"
  info "Install PipeWire: sudo apt install pipewire pipewire-pulse wireplumber"
fi

# Check socket paths
PULSE_SOCK="/run/user/${DETECTED_UID}/pulse/native"
PW_SOCK="/run/user/${DETECTED_UID}/pipewire-0"

if [ -S "$PULSE_SOCK" ]; then
  ok "PulseAudio socket found: $PULSE_SOCK"
  AUDIO_SOCKET="$PULSE_SOCK"
elif [ -S "$PW_SOCK" ]; then
  ok "PipeWire socket found: $PW_SOCK"
  AUDIO_SOCKET="$PW_SOCK"
else
  bad "No audio socket found at /run/user/${DETECTED_UID}/"
  info "Check that audio is running as your user (UID ${DETECTED_UID})"
fi
echo ""

# ── 6. User & UID ────────────────────────────────────────────────────────────
echo -e "${BOLD}6. User & UID${NC}"
ok "Current user: $(whoami) (UID: $DETECTED_UID)"
if [ "$DETECTED_UID" -ne 1000 ]; then
  skip "UID is not 1000 — set AUDIO_UID=$DETECTED_UID in your .env file"
else
  ok "UID is 1000 (default, no .env override needed)"
fi
echo ""

# ── 7. D-Bus ─────────────────────────────────────────────────────────────────
echo -e "${BOLD}7. D-Bus${NC}"
if [ -S /var/run/dbus/system_bus_socket ]; then
  ok "D-Bus system socket found: /var/run/dbus/system_bus_socket"
else
  bad "D-Bus system socket not found — Bluetooth will not work in container"
  info "Start D-Bus: sudo systemctl start dbus"
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Summary: ${GREEN}${pass} passed${NC}, ${YELLOW}${warn} warnings${NC}, ${RED}${fail} failed${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

if [ "$fail" -gt 0 ]; then
  echo -e "${RED}${BOLD}  ⛔ Fix the failed checks above before starting the container.${NC}"
  echo ""
fi

# ── Recommended config ────────────────────────────────────────────────────────
echo -e "${BOLD}Recommended config.json:${NC}"
echo ""
echo '  {'
echo '    "SENDSPIN_SERVER": "auto",'

# BLUETOOTH_DEVICES
if [ ${#PAIRED_MACS[@]} -gt 0 ]; then
  echo '    "BLUETOOTH_DEVICES": ['
  for mac in "${PAIRED_MACS[@]}"; do
    echo "      {\"mac\": \"$mac\", \"adapter\": \"\", \"player_name\": \"\"},"
  done
  echo '    ],'
else
  echo '    "BLUETOOTH_DEVICES": [],'
  echo '    # Add speakers via web UI at http://localhost:8080'
fi

# TZ
TZ_CURRENT=$(cat /etc/timezone 2>/dev/null || echo "UTC")
echo "    \"TZ\": \"$TZ_CURRENT\""
echo '  }'
echo ""

echo -e "${BOLD}Recommended .env file:${NC}"
echo ""
echo "  # Save this as .env next to docker-compose.yml"

# AUDIO_UID
echo "  AUDIO_UID=$DETECTED_UID"

echo ""
echo -e "${CYAN}  Next: docker compose up -d && docker logs -f sendspin-client${NC}"
echo ""
