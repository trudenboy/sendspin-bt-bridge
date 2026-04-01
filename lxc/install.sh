#!/usr/bin/env bash
# install.sh - Sendspin Client LXC installer
# Runs inside the LXC container as root. Idempotent (safe to re-run).
# Usage: bash install.sh [--repo owner/repo] [--branch name]

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

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

REF_KIND="heads"
if [[ "${GITHUB_BRANCH}" == v* ]]; then
  REF_KIND="tags"
fi
ARCHIVE_URL="https://github.com/${GITHUB_REPO}/archive/refs/${REF_KIND}/${GITHUB_BRANCH}.tar.gz"
SCRIPT_TMP_DIR=""

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

  for dir in services routes templates static lxc scripts; do
    if [[ -d "${src_root}/${dir}" ]]; then
      rm -rf "${dest_root}/${dir}"
      cp -a "${src_root}/${dir}" "${dest_root}/${dir}"
    fi
  done

  chmod +x "${dest_root}/sendspin_client.py"
}

record_release_ref() {
  local dest_root="$1"
  printf '%s\n' "${GITHUB_BRANCH}" > "${dest_root}/.release-ref"
}

install_vue_frontend() {
  local dest_root="$1"
  local vue_dir="${dest_root}/static/vue"
  local vue_asset_url

  if [[ "${REF_KIND}" == "tags" ]]; then
    vue_asset_url="https://github.com/${GITHUB_REPO}/releases/download/${GITHUB_BRANCH}/vue-dist.tar.gz"
  else
    local latest_tag
    latest_tag=$(wget -qO- "https://api.github.com/repos/${GITHUB_REPO}/releases" 2>/dev/null | \
      python3 -c "import sys,json; tags=[r['tag_name'] for r in json.load(sys.stdin) if not r['draft']]; print(tags[0] if tags else '')" 2>/dev/null || echo "")
    if [[ -n "${latest_tag}" ]]; then
      vue_asset_url="https://github.com/${GITHUB_REPO}/releases/download/${latest_tag}/vue-dist.tar.gz"
    fi
  fi

  if [[ -n "${vue_asset_url:-}" ]]; then
    local tmp_vue
    tmp_vue="$(mktemp)"
    if wget -q -O "${tmp_vue}" "${vue_asset_url}" 2>/dev/null && [[ -s "${tmp_vue}" ]]; then
      rm -rf "${vue_dir}"
      mkdir -p "${vue_dir}"
      if tar -xzf "${tmp_vue}" -C "${vue_dir}"; then
        rm -f "${tmp_vue}"
        ok "Vue frontend installed"
        return 0
      fi
    fi
    rm -f "${tmp_vue}"
  fi

  warn "Vue frontend not available — UI will use legacy template"
  return 0
}

# ─── Pre-flight ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must be run as root"

msg "Sendspin Client LXC Installer"
msg "Repo: ${GITHUB_REPO}  Branch: ${GITHUB_BRANCH}"
echo ""

SCRIPT_TMP_DIR=$(mktemp -d)
SNAPSHOT_ROOT=$(download_repo_snapshot "${SCRIPT_TMP_DIR}")
[[ -n "${SNAPSHOT_ROOT}" && -d "${SNAPSHOT_ROOT}" ]] || die "Failed to download repository snapshot"

# ─── 1. System packages ───────────────────────────────────────────────────────
msg "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv python3-full \
  bluez-tools \
  pulseaudio pulseaudio-module-bluetooth \
  alsa-utils dbus libportaudio2 \
  avahi-daemon avahi-utils libnss-mdns \
  curl wget ca-certificates git jq tzdata procps \
  gcc python3-dev
ok "System packages installed"

# ─── 2. App directory and files from GitHub ───────────────────────────────────
msg "Downloading application files from GitHub..."
mkdir -p /opt/sendspin-client
sync_app_tree "${SNAPSHOT_ROOT}" /opt/sendspin-client
record_release_ref /opt/sendspin-client
ok "Application files downloaded"

# ─── 2b. Vue frontend ─────────────────────────────────────────────────────────
msg "Installing Vue frontend..."
install_vue_frontend /opt/sendspin-client

# ─── 3. Python dependencies ───────────────────────────────────────────────────
msg "Installing Python dependencies..."

# Force-upgrade system-managed packages that pip can't uninstall (no RECORD file)
for pkg in typing-extensions blinker; do
  pip3 install --break-system-packages --ignore-installed -q "$pkg" 2>/dev/null || true
done

ARCH=$(uname -m)
if [[ "$ARCH" == "armv7l" || "$ARCH" == "armhf" ]]; then
  warn "ARM 32-bit detected — applying av compatibility workaround"
  # av>=14 (required by sendspin) fails to compile on armhf: AV_HWDEVICE_TYPE_D3D12VA
  # is absent in Ubuntu 24.04's ffmpeg 6.1. av==12.3.0 is the latest compatible version.
  # The FLAC decoder API difference (nb_channels missing in av<13) is handled by
  # a monkey-patch in services/daemon_process.py at startup.
  pip3 install --break-system-packages -q av==12.3.0

  # Install sendspin without its av>=14 dependency
  pip3 install --break-system-packages -q --no-deps 'sendspin>=5.3.0,<6'

  # Install sendspin's other transitive dependencies
  pip3 install --break-system-packages -q \
    aiosendspin pychromecast qrcode readchar sounddevice \
    numpy pillow zeroconf casttube protobuf ifaddr

  # Install remaining requirements.txt deps (exclude sendspin line)
  grep -v '^sendspin' /opt/sendspin-client/requirements.txt | \
    pip3 install --break-system-packages -q -r /dev/stdin
else
  pip3 install --break-system-packages -q -r /opt/sendspin-client/requirements.txt
fi
ok "Python dependencies installed"

# ─── 4. Config directory ──────────────────────────────────────────────────────
msg "Setting up config directory..."
mkdir -p /config

if [[ ! -f /config/config.json ]]; then
  cat > /config/config.json <<'EOF'
{
  "SENDSPIN_SERVER": "auto",
  "BLUETOOTH_DEVICES": [],
  "TZ": "UTC"
}
EOF
  ok "Default config.json written to /config/config.json"
else
  ok "config.json already exists — skipping"
fi

# ─── 5. Bluetooth D-Bus bridge mount point ────────────────────────────────────
# The container uses the Proxmox HOST's bluetoothd via a bind-mounted D-Bus socket.
# /bt-dbus is the persistent mount point (must exist before the LXC mount applies).
msg "Creating Bluetooth D-Bus mount point..."
mkdir -p /bt-dbus
ok "/bt-dbus created (host D-Bus socket will be bind-mounted here)"

# ─── 6. PulseAudio system user ────────────────────────────────────────────────
msg "Setting up pulse system user..."
if ! id pulse &>/dev/null; then
  useradd --system --home-dir /var/run/pulse --shell /bin/false --comment "PulseAudio system user" pulse
  ok "Created pulse system user"
else
  ok "pulse user already exists — skipping"
fi

usermod -aG bluetooth pulse 2>/dev/null || true
usermod -aG audio    pulse 2>/dev/null || true

mkdir -p /var/run/pulse
chown pulse:pulse /var/run/pulse
chmod 755 /var/run/pulse

# Ensure /var/run/pulse is recreated on boot (tmpfs)
cat > /etc/tmpfiles.d/pulse.conf <<'EOF'
d /var/run/pulse 0755 pulse pulse -
EOF

ok "pulse user configured (bluetooth + audio groups)"

# ─── 7. PulseAudio system configuration ──────────────────────────────────────
msg "Writing PulseAudio system configuration..."
mkdir -p /etc/pulse/client.conf.d

# CPU-optimal daemon.conf — trivial resampler, s16le, 48kHz to match MA output
cp "${SNAPSHOT_ROOT}/lxc/pulse-daemon.conf" /etc/pulse/daemon.conf
ok "PulseAudio daemon.conf written (trivial resampler + 48kHz + s16le)"

# System-mode PA config with Bluetooth modules
cp "${SNAPSHOT_ROOT}/lxc/pulse-system.pa" /etc/pulse/system.pa
ok "PulseAudio system.pa written (bluetooth-discover + null fallback)"

cat > /etc/pulse/client.conf.d/00-no-autospawn.conf <<'EOF'
autospawn = no
daemon-binary = /bin/true
EOF

ok "PulseAudio configuration written"

# ─── 8. D-Bus policy for PulseAudio ↔ BlueZ ──────────────────────────────────
msg "Writing D-Bus policy for PulseAudio Bluetooth access..."
cat > /etc/dbus-1/system.d/pulseaudio-bluetooth.conf <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy user="pulse">
    <allow own="org.pulseaudio.Server"/>
    <allow send_destination="org.bluez"/>
    <allow send_interface="org.bluez.MediaEndpoint1"/>
    <allow send_interface="org.bluez.MediaTransport1"/>
    <allow receive_sender="org.bluez"/>
  </policy>
  <policy context="default">
    <allow send_destination="org.pulseaudio.Server"/>
  </policy>
</busconfig>
EOF
ok "D-Bus policy written"

# ─── 9. Environment variables ─────────────────────────────────────────────────
msg "Setting environment variables in /etc/environment..."

set_env_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" /etc/environment 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" /etc/environment
  else
    echo "${key}=${val}" >> /etc/environment
  fi
}

set_env_var "PULSE_SERVER"  "unix:/var/run/pulse/native"
set_env_var "CONFIG_DIR"    "/config"
set_env_var "WEB_PORT"      "8080"
ok "Environment variables set"

# ─── 10. Systemd units ────────────────────────────────────────────────────────
msg "Installing systemd service units..."

cp "${SNAPSHOT_ROOT}/lxc/pulseaudio-system.service" /etc/systemd/system/pulseaudio-system.service
cp "${SNAPSHOT_ROOT}/lxc/sendspin-client.service" /etc/systemd/system/sendspin-client.service

ok "Systemd units installed"

# ─── 11. Network configuration (LXC) ─────────────────────────────────────────
# In LXC containers, netplan may fail (udevadm not available). Use systemd-networkd
# as a reliable fallback for DHCP on eth0.
if [[ -d /proc/1/ns/pid ]] && ! command -v netplan &>/dev/null; then
  if [[ ! -f /etc/systemd/network/10-eth0.network ]]; then
    msg "Configuring systemd-networkd for eth0 (DHCP)..."
    mkdir -p /etc/systemd/network
    cat > /etc/systemd/network/10-eth0.network <<'NETEOF'
[Match]
Name=eth0

[Network]
DHCP=yes
NETEOF
    systemctl enable systemd-networkd 2>/dev/null || true
    ok "systemd-networkd configured with DHCP on eth0"
  fi
fi

# ─── 12. btctl wrapper ────────────────────────────────────────────────────────
# btctl wraps bluetoothctl to use the host's D-Bus socket at /bt-dbus.
# Bluetooth runs on the HOST; the container accesses it via bind-mount.
msg "Installing btctl wrapper..."
cat > /usr/local/bin/btctl <<'BTCTL'
#!/usr/bin/env bash
exec env DBUS_SYSTEM_BUS_ADDRESS=unix:path=/bt-dbus/system_bus_socket bluetoothctl "$@"
BTCTL
chmod +x /usr/local/bin/btctl
ok "btctl installed at /usr/local/bin/btctl"

# ─── 13. Enable and start services ───────────────────────────────────────────
msg "Enabling and starting services..."
systemctl daemon-reload

# bluetooth.service (local bluetoothd) is NOT needed — we use the host's bluetoothd.
# Mask it to prevent accidental starts which crash (no mgmt socket in LXC)
# and can disrupt PulseAudio's A2DP state.
systemctl stop    bluetooth 2>/dev/null || true
systemctl disable bluetooth 2>/dev/null || true
systemctl mask    bluetooth 2>/dev/null || true

for svc in dbus avahi-daemon pulseaudio-system sendspin-client; do
  systemctl enable "$svc" 2>/dev/null || warn "Could not enable $svc (may not exist yet)"
done

# Start in dependency order
for svc in dbus avahi-daemon pulseaudio-system sendspin-client; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    ok "$svc already running"
  else
    systemctl start "$svc" 2>/dev/null && ok "$svc started" || warn "$svc failed to start (check: journalctl -u $svc)"
  fi
done

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Sendspin Client installed successfully!${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Detect container IP
CONTAINER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<container-ip>")
echo -e "  ${BOLD}Web UI:${NC}        http://${CONTAINER_IP}:8080"
echo -e "  ${BOLD}Config file:${NC}   /config/config.json"
echo -e "  ${BOLD}App logs:${NC}      journalctl -u sendspin-client -f"
echo ""
echo -e "  ${BOLD}Key commands:${NC}"
echo -e "    systemctl status sendspin-client"
echo -e "    systemctl restart sendspin-client"
echo -e "    pactl list sinks short"
echo -e "    btctl scan on        # scan for BT devices (uses host bluetoothd)"
echo ""
echo -e "  ${YELLOW}Note:${NC} Config changes require:"
echo -e "    systemctl restart sendspin-client"
echo ""
