#!/usr/bin/env bash
# rpi-install.sh — One-liner installer for Sendspin Bluetooth Bridge
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-install.sh | bash
#
# Non-interactive (CI/automation):
#   NONINTERACTIVE=1 curl -sSL ... | bash
#
# Works on: Raspberry Pi OS, Ubuntu, Debian (amd64 / arm64 / armv7)

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main"
PROJECT_DIR="$HOME/sendspin-bt-bridge"

# ── Helpers ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

pass=0; warn=0; fail=0

ok()   { echo -e "  ${GREEN}✅ $1${NC}"; ((++pass)); }
skip() { echo -e "  ${YELLOW}⚠️  $1${NC}"; ((++warn)); }
bad()  { echo -e "  ${RED}❌ $1${NC}"; ((++fail)); }
info() { echo -e "  ${CYAN}ℹ  $1${NC}"; }
step() { echo ""; echo -e "${BOLD}$1${NC}"; }

# Detect non-interactive mode: piped stdin, or NONINTERACTIVE=1
INTERACTIVE=true
if [ ! -t 0 ] || [ "${NONINTERACTIVE:-0}" = "1" ]; then
  INTERACTIVE=false
fi

ask() {
  # ask "prompt" "default"  — returns answer; uses default when non-interactive
  local prompt="$1" default="${2:-}"
  if $INTERACTIVE; then
    echo -en "  ${CYAN}$prompt${NC} "
    read -r answer </dev/tty || answer="$default"
    echo "${answer:-$default}"
  else
    echo "$default"
  fi
}

ask_yn() {
  # ask_yn "prompt" "Y"  — returns 0 (yes) or 1 (no)
  local prompt="$1" default="${2:-Y}"
  local answer
  answer=$(ask "$prompt" "$default")
  case "${answer,,}" in
    y|yes|"") return 0 ;;
    *)        return 1 ;;
  esac
}

abort() { echo -e "\n  ${RED}${BOLD}⛔ $1${NC}\n"; exit 1; }

# ── 1. Banner + Confirmation ────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Sendspin Bluetooth Bridge — Installer${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

if $INTERACTIVE; then
  ask_yn "This will install Sendspin BT Bridge. Continue? [Y/n]" "Y" || abort "Installation cancelled."
fi

# ── 2. Preflight Checks ─────────────────────────────────────────────────────
step "1. Platform"
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)        ok "Architecture: $ARCH (amd64) — fully supported" ;;
  aarch64)       ok "Architecture: $ARCH (arm64) — fully supported" ;;
  armv7l|armhf)  skip "Architecture: $ARCH (armv7) — best-effort; 1–2 speakers recommended" ;;
  *)             skip "Architecture: $ARCH — untested" ;;
esac

if [ -f /proc/device-tree/model ]; then
  MODEL=$(tr -d '\0' < /proc/device-tree/model)
  info "Hardware: $MODEL"
fi

step "2. Memory"
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

step "3. Docker"
DOCKER_INSTALLED=false
if command -v docker &>/dev/null; then
  DOCKER_VER=$(docker --version 2>/dev/null | head -1)
  ok "Docker installed: $DOCKER_VER"
  DOCKER_INSTALLED=true

  if docker info &>/dev/null; then
    ok "Docker daemon is running"
  else
    bad "Docker daemon is not running or current user has no access"
    info "Try: sudo systemctl start docker && sudo usermod -aG docker \$USER"
  fi
else
  bad "Docker is not installed"
fi

COMPOSE_OK=false
if $DOCKER_INSTALLED && docker compose version &>/dev/null; then
  ok "Docker Compose available"
  COMPOSE_OK=true
else
  skip "Docker Compose plugin not found"
fi

step "4. Bluetooth"
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
  if bluetoothctl list 2>/dev/null | grep -q "Controller"; then
    CTRL=$(bluetoothctl list 2>/dev/null | head -1)
    ok "BT controller found: $CTRL"
  else
    bad "No Bluetooth controller detected"
    info "Check USB adapter connection or built-in BT"
  fi

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
  fi
fi

step "5. Audio System"
DETECTED_UID=$(id -u)

if command -v pw-cli &>/dev/null && pw-cli info 0 &>/dev/null; then
  ok "PipeWire is running"
elif systemctl --user is-active pipewire &>/dev/null 2>&1; then
  ok "PipeWire service is active"
fi

if command -v pactl &>/dev/null && pactl info &>/dev/null 2>&1; then
  PA_SERVER=$(pactl info 2>/dev/null | grep "Server Name" | cut -d: -f2- | xargs)
  if echo "$PA_SERVER" | grep -qi pipewire; then
    ok "PulseAudio API available (via PipeWire): $PA_SERVER"
  else
    ok "PulseAudio is running: $PA_SERVER"
  fi
else
  skip "No audio system detected — install PipeWire or PulseAudio"
fi

PULSE_SOCK="/run/user/${DETECTED_UID}/pulse/native"
PW_SOCK="/run/user/${DETECTED_UID}/pipewire-0"
if [ -S "$PULSE_SOCK" ]; then
  ok "PulseAudio socket found: $PULSE_SOCK"
elif [ -S "$PW_SOCK" ]; then
  ok "PipeWire socket found: $PW_SOCK"
else
  skip "No audio socket found at /run/user/${DETECTED_UID}/"
fi

step "6. User & UID"
ok "Current user: $(whoami) (UID: $DETECTED_UID)"
if [ "$DETECTED_UID" -ne 1000 ]; then
  skip "UID is not 1000 — will set AUDIO_UID=$DETECTED_UID in .env"
else
  ok "UID is 1000 (default)"
fi

step "7. D-Bus"
if [ -S /var/run/dbus/system_bus_socket ]; then
  ok "D-Bus system socket found"
else
  bad "D-Bus system socket not found — Bluetooth will not work in container"
  info "Start D-Bus: sudo systemctl start dbus"
fi

# ── Preflight Summary ───────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Preflight: ${GREEN}${pass} passed${NC}, ${YELLOW}${warn} warnings${NC}, ${RED}${fail} failed${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"

if [ "$fail" -gt 0 ]; then
  echo ""
  echo -e "  ${YELLOW}⚠️  Some checks failed — the bridge may not work correctly.${NC}"
  if $INTERACTIVE; then
    ask_yn "  Continue anyway? [y/N]" "N" || abort "Fix the issues above and re-run."
  else
    info "Non-interactive mode — continuing despite failures"
  fi
fi

# ── 3. Install Docker if Missing ────────────────────────────────────────────
if ! $DOCKER_INSTALLED; then
  step "8. Install Docker"
  if $INTERACTIVE; then
    ask_yn "Docker is not installed. Install via get.docker.com? [Y/n]" "Y" || abort "Docker is required. Install it manually and re-run."
  fi
  info "Installing Docker (this may take a few minutes)..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo ""
  skip "You may need to log out and back in for Docker group membership to take effect"
  info "If 'docker compose' fails below, log out/in and re-run this script"
  echo ""

  # Re-check compose
  if docker compose version &>/dev/null; then
    COMPOSE_OK=true
  fi
fi

# ── 4. Verify Docker Compose ────────────────────────────────────────────────
if ! $COMPOSE_OK; then
  # One more check (docker may have just been installed)
  if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    COMPOSE_OK=true
  fi
fi

if ! $COMPOSE_OK; then
  abort "Docker Compose is required but not found. Install it:\n  sudo apt install docker-compose-plugin"
fi

# ── 5. Create Project Directory ─────────────────────────────────────────────
step "9. Project Directory"
if [ -d "$PROJECT_DIR" ]; then
  ok "Project directory exists: $PROJECT_DIR"
else
  mkdir -p "$PROJECT_DIR"
  ok "Created project directory: $PROJECT_DIR"
fi

# ── 6. Download docker-compose.yml ──────────────────────────────────────────
step "10. Docker Compose File"
if [ -f "$PROJECT_DIR/docker-compose.yml" ]; then
  ok "docker-compose.yml already exists — skipping download"
else
  curl -sSL "${REPO_RAW}/docker-compose.yml" -o "$PROJECT_DIR/docker-compose.yml"
  ok "Downloaded docker-compose.yml"
fi

# ── 7. Generate .env File ───────────────────────────────────────────────────
step "11. Environment Configuration"

# Determine Bluetooth device MAC
BT_MAC=""
if [ ${#PAIRED_MACS[@]} -gt 0 ]; then
  BT_MAC="${PAIRED_MACS[0]}"
  info "Using first paired device: $BT_MAC"
elif $INTERACTIVE; then
  BT_MAC=$(ask "Enter your Bluetooth speaker MAC address (or leave blank):" "")
fi

# Determine timezone
TZ_CURRENT=$(cat /etc/timezone 2>/dev/null || echo "UTC")

WRITE_ENV=true
if [ -f "$PROJECT_DIR/.env" ]; then
  skip ".env file already exists at $PROJECT_DIR/.env"
  if $INTERACTIVE; then
    ask_yn "  Overwrite existing .env? [y/N]" "N" && WRITE_ENV=true || WRITE_ENV=false
  else
    WRITE_ENV=false
    info "Non-interactive mode — keeping existing .env"
  fi
fi

if $WRITE_ENV; then
  cat > "$PROJECT_DIR/.env" <<EOF
# Sendspin BT Bridge — generated by rpi-install.sh on $(date -Iseconds)
AUDIO_UID=${DETECTED_UID}
TZ=${TZ_CURRENT}
SENDSPIN_SERVER=auto
WEB_PORT=8080
EOF
  ok "Generated .env file"
  info "  AUDIO_UID=${DETECTED_UID}"
  info "  TZ=${TZ_CURRENT}"
else
  ok "Kept existing .env file"
fi

# Write initial config.json with device (if MAC provided)
CONFIG_DIR="${PROJECT_DIR}/config"
CONFIG_FILE="${CONFIG_DIR}/config.json"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_FILE" ]; then
  if [ -n "$BT_MAC" ]; then
    cat > "$CONFIG_FILE" <<EOF
{
  "SENDSPIN_SERVER": "auto",
  "BLUETOOTH_DEVICES": [
    {"mac": "$BT_MAC", "adapter": "", "player_name": ""}
  ],
  "TZ": "$TZ_CURRENT"
}
EOF
    ok "Created config.json with device $BT_MAC"
  else
    cat > "$CONFIG_FILE" <<EOF
{
  "SENDSPIN_SERVER": "auto",
  "BLUETOOTH_DEVICES": [],
  "TZ": "$TZ_CURRENT"
}
EOF
    ok "Created config.json (add speakers via web UI at http://localhost:8080)"
  fi
else
  ok "config.json already exists — skipping"
fi

# ── 8. Bluetooth Pairing ────────────────────────────────────────────────────
step "12. Bluetooth Pairing"
if $INTERACTIVE && $BT_OK && command -v bluetoothctl &>/dev/null; then
  if ask_yn "Would you like to pair a Bluetooth speaker now? [y/N]" "N"; then
    info "Scanning for Bluetooth devices (15 seconds)..."
    echo ""

    # Start scan, collect output, stop after 15s
    SCAN_FILE=$(mktemp)
    bluetoothctl --timeout 15 scan on > "$SCAN_FILE" 2>&1 || true

    # Gather discovered devices (deduplicated)
    DISCOVERED=$(bluetoothctl devices 2>/dev/null | sort -u)
    rm -f "$SCAN_FILE"

    if [ -z "$DISCOVERED" ]; then
      skip "No devices discovered"
    else
      echo -e "  ${BOLD}Discovered devices:${NC}"
      DISC_MACS=()
      DISC_NAMES=()
      IDX=1
      while IFS= read -r line; do
        D_MAC=$(echo "$line" | awk '{print $2}')
        D_NAME=$(echo "$line" | cut -d' ' -f3-)
        DISC_MACS+=("$D_MAC")
        DISC_NAMES+=("$D_NAME")
        echo -e "    ${CYAN}${IDX})${NC} $D_MAC — $D_NAME"
        ((IDX++))
      done <<< "$DISCOVERED"

      CHOICE=$(ask "Select a device number to pair (or press Enter to skip):" "")
      if [ -n "$CHOICE" ] && [ "$CHOICE" -ge 1 ] 2>/dev/null && [ "$CHOICE" -le "${#DISC_MACS[@]}" ] 2>/dev/null; then
        SEL_MAC="${DISC_MACS[$((CHOICE - 1))]}"
        SEL_NAME="${DISC_NAMES[$((CHOICE - 1))]}"
        info "Pairing with $SEL_NAME ($SEL_MAC)..."

        bluetoothctl pair "$SEL_MAC" </dev/tty 2>&1 || true
        sleep 1
        bluetoothctl trust "$SEL_MAC" 2>&1 || true
        sleep 1
        bluetoothctl connect "$SEL_MAC" 2>&1 || true
        sleep 2

        # Verify connection
        if bluetoothctl info "$SEL_MAC" 2>/dev/null | grep -q "Connected: yes"; then
          ok "Connected to $SEL_NAME ($SEL_MAC)"
          # Update config.json with paired device
          if [ -f "$CONFIG_FILE" ]; then
            python3 -c "
import json
with open('$CONFIG_FILE') as f:
    cfg = json.load(f)
devs = cfg.get('BLUETOOTH_DEVICES', [])
if not any(d.get('mac') == '$SEL_MAC' for d in devs):
    devs.append({'mac': '$SEL_MAC', 'adapter': '', 'player_name': ''})
    cfg['BLUETOOTH_DEVICES'] = devs
    with open('$CONFIG_FILE', 'w') as f:
        json.dump(cfg, f, indent=2)
" 2>/dev/null && ok "Added $SEL_MAC to config.json" || true
          fi
        else
          skip "Pairing initiated but device may not be connected yet"
          info "You can manually connect later: bluetoothctl connect $SEL_MAC"
        fi
      else
        info "Skipping pairing"
      fi
    fi
  else
    info "Skipping Bluetooth pairing"
  fi
else
  if ! $INTERACTIVE; then
    info "Non-interactive mode — skipping Bluetooth pairing"
  elif ! $BT_OK; then
    skip "Bluetooth not available — skipping pairing"
  fi
fi

# ── 9. Pull and Start ───────────────────────────────────────────────────────
step "13. Pull & Start"
info "Pulling latest image and starting the bridge..."
echo ""

cd "$PROJECT_DIR"
docker compose pull
docker compose up -d

# ── 10. Success ──────────────────────────────────────────────────────────────
HOSTNAME_VAL=$(hostname 2>/dev/null || echo "localhost")
WEB_PORT=$(grep '^WEB_PORT=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2 || echo "8080")
WEB_PORT="${WEB_PORT:-8080}"

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅ Sendspin Bluetooth Bridge is running!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Web UI:${NC}       http://${HOSTNAME_VAL}:${WEB_PORT}"
echo -e "  ${BOLD}Logs:${NC}         docker logs -f sendspin-client"
echo -e "  ${BOLD}Preflight:${NC}    curl http://localhost:${WEB_PORT}/api/preflight"
echo -e "  ${BOLD}Project dir:${NC}  $PROJECT_DIR"
echo ""
echo -e "  ${CYAN}To stop:${NC}      cd $PROJECT_DIR && docker compose down"
echo -e "  ${CYAN}To update:${NC}    cd $PROJECT_DIR && docker compose pull && docker compose up -d"
echo ""
