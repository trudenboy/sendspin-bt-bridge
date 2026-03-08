#!/usr/bin/env bash
# upgrade.sh — Update Sendspin Client to the latest version
# Runs inside the LXC container as root. Preserves /config/config.json.
# Usage: bash upgrade.sh [--repo owner/repo] [--branch name]

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

msg()  { echo -e "${CYAN}${BOLD}[Sendspin]${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*" >&2; }
die()  { err "$*"; exit 1; }

# ─── Argument parsing ─────────────────────────────────────────────────────────
GITHUB_REPO="trudenboy/sendspin-bt-bridge"
GITHUB_BRANCH="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)   GITHUB_REPO="$2";   shift 2 ;;
    --branch) GITHUB_BRANCH="$2"; shift 2 ;;
    *) shift ;;
  esac
done

BASE="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}"
APP_DIR="/opt/sendspin-client"

# ─── Pre-flight ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must be run as root"
[[ -d "$APP_DIR" ]] || die "App directory ${APP_DIR} not found. Run install.sh first."

msg "Sendspin Client Upgrade"
msg "Repo: ${GITHUB_REPO}  Branch: ${GITHUB_BRANCH}"

# Save current version
OLD_VERSION=$(python3 -c "
import sys; sys.path.insert(0, '${APP_DIR}')
try:
    from config import VERSION; print(VERSION)
except: print('unknown')
" 2>/dev/null || echo "unknown")
msg "Current version: ${OLD_VERSION}"
echo ""

# ─── 1. Download application files ───────────────────────────────────────────
msg "Downloading application files..."

# Root Python files
for file in sendspin_client.py web_interface.py config.py state.py bluetooth_manager.py; do
  wget -q "${BASE}/${file}" -O "${APP_DIR}/${file}"
done

# services/ module
mkdir -p "${APP_DIR}/services"
for file in __init__.py bluetooth.py ma_client.py bridge_daemon.py daemon_process.py ma_monitor.py pulse.py; do
  wget -q "${BASE}/services/${file}" -O "${APP_DIR}/services/${file}"
done

# routes/ module
mkdir -p "${APP_DIR}/routes"
for file in __init__.py api.py views.py auth.py; do
  wget -q "${BASE}/routes/${file}" -O "${APP_DIR}/routes/${file}"
done

# HTML templates
mkdir -p "${APP_DIR}/templates"
for file in index.html login.html; do
  wget -q "${BASE}/templates/${file}" -O "${APP_DIR}/templates/${file}"
done

# Static assets
mkdir -p "${APP_DIR}/static"
for file in app.js style.css favicon.svg favicon.png; do
  wget -q "${BASE}/static/${file}" -O "${APP_DIR}/static/${file}"
done

wget -q "${BASE}/requirements.txt" -O "${APP_DIR}/requirements.txt"
ok "Application files downloaded"

# ─── 2. Update Python dependencies ───────────────────────────────────────────
msg "Updating Python dependencies..."

ARCH=$(uname -m)
if [[ "$ARCH" == "armv7l" || "$ARCH" == "armhf" ]]; then
  # armv7l: sendspin requires av>=14 which doesn't compile on armhf.
  # Keep av==12.3.0 and install sendspin with --no-deps.
  pip3 install --break-system-packages -q --no-deps 'sendspin>=5.1.3,<6' 2>/dev/null || true
  grep -v '^sendspin' "${APP_DIR}/requirements.txt" | \
    pip3 install --break-system-packages -q -r /dev/stdin 2>/dev/null || true
else
  pip3 install --break-system-packages -q -r "${APP_DIR}/requirements.txt" 2>/dev/null || true
fi
ok "Python dependencies updated"

# ─── 3. Update systemd units ─────────────────────────────────────────────────
msg "Updating systemd service units..."
wget -q "${BASE}/lxc/pulseaudio-system.service" -O /etc/systemd/system/pulseaudio-system.service
wget -q "${BASE}/lxc/sendspin-client.service"   -O /etc/systemd/system/sendspin-client.service
systemctl daemon-reload
ok "Systemd units updated"

# Mask bluetooth.service — bluetoothd runs on the HOST, not in the container.
# An accidental restart inside LXC crashes (no mgmt socket) and breaks A2DP.
if ! systemctl is-enabled bluetooth 2>/dev/null | grep -q masked; then
  systemctl stop    bluetooth 2>/dev/null || true
  systemctl disable bluetooth 2>/dev/null || true
  systemctl mask    bluetooth 2>/dev/null || true
  ok "bluetooth.service masked (uses host bluetoothd)"
fi

# ─── 4. Restart service ──────────────────────────────────────────────────────
msg "Restarting sendspin-client..."
systemctl restart sendspin-client
sleep 2

if systemctl is-active --quiet sendspin-client; then
  ok "sendspin-client is running"
else
  warn "sendspin-client may have failed to start — check: journalctl -u sendspin-client -n 30"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
NEW_VERSION=$(python3 -c "
import sys; sys.path.insert(0, '${APP_DIR}')
try:
    from config import VERSION; print(VERSION)
except: print('unknown')
" 2>/dev/null || echo "unknown")

echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Upgrade complete: ${OLD_VERSION} → ${NEW_VERSION}${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Check logs:${NC}  journalctl -u sendspin-client -n 20"
echo -e "  ${BOLD}Config:${NC}      /config/config.json (preserved, not modified)"
echo ""
