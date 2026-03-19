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

APP_DIR="/opt/sendspin-client"
REF_KIND="heads"
if [[ "${GITHUB_BRANCH}" == v* ]]; then
  REF_KIND="tags"
fi
ARCHIVE_URL="https://github.com/${GITHUB_REPO}/archive/refs/${REF_KIND}/${GITHUB_BRANCH}.tar.gz"
SCRIPT_TMP_DIR=""
STAGE_APP=""
BACKUP_APP=""
SENDSPIN_UNIT_BACKUP=""
PULSE_UNIT_BACKUP=""

cleanup() {
  if [[ -n "${SCRIPT_TMP_DIR}" && -d "${SCRIPT_TMP_DIR}" ]]; then
    rm -rf "${SCRIPT_TMP_DIR}"
  fi
}
trap cleanup EXIT

download_repo_snapshot() {
  local extract_dir="$1"
  wget -qO- "${ARCHIVE_URL}" | tar -xzf - -C "${extract_dir}"
  find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1
}

sync_app_tree() {
  local src_root="$1"
  local dest_root="$2"

  mkdir -p "${dest_root}"
  find "${src_root}" -maxdepth 1 -type f \( -name '*.py' -o -name 'requirements.txt' \) -exec cp -a {} "${dest_root}/" \;

  for dir in services routes demo templates static lxc scripts; do
    rm -rf "${dest_root}/${dir}"
    cp -a "${src_root}/${dir}" "${dest_root}/${dir}"
  done

  chmod +x "${dest_root}/sendspin_client.py"
}

update_python_dependencies() {
  local requirements_file="$1"
  local arch

  arch=$(uname -m)
  if [[ "${arch}" == "armv7l" || "${arch}" == "armhf" ]]; then
    # armv7l: sendspin requires av>=14 which doesn't compile on armhf.
    # Keep av==12.3.0 and install sendspin with --no-deps.
    # The FLAC decoder API difference (nb_channels missing in av<13) is handled by
    # a monkey-patch in services/daemon_process.py at startup.
    pip3 install --break-system-packages -q --no-deps 'sendspin>=5.3.0,<6' 2>/dev/null || true
    grep -v '^sendspin' "${requirements_file}" | \
      pip3 install --break-system-packages -q -r /dev/stdin 2>/dev/null || true
  else
    pip3 install --break-system-packages -q -r "${requirements_file}" 2>/dev/null || true
  fi
}

validate_import_tree() {
  local app_root="$1"
  APP_ROOT="${app_root}" python3 - <<'PY'
import importlib
import os
import sys

app_root = os.environ["APP_ROOT"]
sys.path = [app_root] + [p for p in sys.path if p not in ("", app_root)]
for module_name in (
    "config",
    "state",
    "services.ma_artwork",
    "services.ma_monitor",
    "routes.api_ma",
    "web_interface",
):
    importlib.import_module(module_name)
print("import-ok")
PY
}

backup_systemd_units() {
  SENDSPIN_UNIT_BACKUP="${SCRIPT_TMP_DIR}/sendspin-client.service"
  PULSE_UNIT_BACKUP="${SCRIPT_TMP_DIR}/pulseaudio-system.service"
  cp -a /etc/systemd/system/sendspin-client.service "${SENDSPIN_UNIT_BACKUP}" 2>/dev/null || true
  cp -a /etc/systemd/system/pulseaudio-system.service "${PULSE_UNIT_BACKUP}" 2>/dev/null || true
}

install_systemd_units() {
  local app_root="$1"
  cp "${app_root}/lxc/pulseaudio-system.service" /etc/systemd/system/pulseaudio-system.service
  cp "${app_root}/lxc/sendspin-client.service" /etc/systemd/system/sendspin-client.service
}

restore_systemd_units() {
  [[ -f "${SENDSPIN_UNIT_BACKUP}" ]] && cp "${SENDSPIN_UNIT_BACKUP}" /etc/systemd/system/sendspin-client.service
  [[ -f "${PULSE_UNIT_BACKUP}" ]] && cp "${PULSE_UNIT_BACKUP}" /etc/systemd/system/pulseaudio-system.service
}

smoke_check_service() {
  local attempt

  for attempt in $(seq 1 15); do
    if systemctl is-active --quiet sendspin-client; then
      if validate_import_tree "${APP_DIR}" >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5) as resp:
    if resp.status >= 400:
        raise RuntimeError(f"Unexpected HTTP status {resp.status}")
PY
      then
        return 0
      fi
    fi
    sleep 2
  done

  journalctl -u sendspin-client -n 60 --no-pager >&2 || true
  return 1
}

rollback_update() {
  warn "Smoke check failed — rolling back to ${OLD_VERSION}"
  systemctl stop sendspin-client 2>/dev/null || true
  rm -rf "${APP_DIR}"
  mv "${BACKUP_APP}" "${APP_DIR}"
  restore_systemd_units
  systemctl daemon-reload

  if systemctl restart sendspin-client && smoke_check_service; then
    ok "Rollback succeeded — restored ${OLD_VERSION}"
  else
    journalctl -u sendspin-client -n 80 --no-pager >&2 || true
    die "Rollback failed — manual intervention required"
  fi
}

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

SCRIPT_TMP_DIR=$(mktemp -d)
STAGE_APP="${SCRIPT_TMP_DIR}/app-stage"
BACKUP_APP="${SCRIPT_TMP_DIR}/app-backup"
SNAPSHOT_ROOT=$(download_repo_snapshot "${SCRIPT_TMP_DIR}")
[[ -n "${SNAPSHOT_ROOT}" && -d "${SNAPSHOT_ROOT}" ]] || die "Failed to download repository snapshot"

# ─── 1. Download application files ───────────────────────────────────────────
msg "Downloading application files..."
sync_app_tree "${SNAPSHOT_ROOT}" "${STAGE_APP}"
ok "Application files downloaded"

NEW_VERSION=$(python3 -c "
import sys; sys.path.insert(0, '${STAGE_APP}')
try:
    from config import VERSION; print(VERSION)
except: print('unknown')
" 2>/dev/null || echo "unknown")

# ─── 2. Update Python dependencies ───────────────────────────────────────────
msg "Updating Python dependencies..."
update_python_dependencies "${STAGE_APP}/requirements.txt"
ok "Python dependencies updated"

# ─── 3. Validate staged tree ──────────────────────────────────────────────────
msg "Validating staged application tree..."
validate_import_tree "${STAGE_APP}" >/dev/null
ok "Staged imports succeeded"

# ─── 4. Update systemd units ─────────────────────────────────────────────────
msg "Updating systemd service units..."
backup_systemd_units
install_systemd_units "${STAGE_APP}"
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

# ─── 5. Swap staged tree and restart service ─────────────────────────────────
msg "Restarting sendspin-client..."
mv "${APP_DIR}" "${BACKUP_APP}"
mv "${STAGE_APP}" "${APP_DIR}"

if systemctl restart sendspin-client && smoke_check_service; then
  ok "sendspin-client is running"
else
  rollback_update
  exit 1
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Upgrade complete: ${OLD_VERSION} → ${NEW_VERSION}${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Check logs:${NC}  journalctl -u sendspin-client -n 20"
echo -e "  ${BOLD}Config:${NC}      /config/config.json (preserved, not modified)"
echo ""
