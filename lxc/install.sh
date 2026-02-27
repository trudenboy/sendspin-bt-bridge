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
GITHUB_REPO="loryanstrant/sendspin-client"
GITHUB_BRANCH="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)   GITHUB_REPO="$2";   shift 2 ;;
    --branch) GITHUB_BRANCH="$2"; shift 2 ;;
    *) shift ;;
  esac
done

BASE="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}"

# ─── Pre-flight ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must be run as root"

msg "Sendspin Client LXC Installer"
msg "Repo: ${GITHUB_REPO}  Branch: ${GITHUB_BRANCH}"
echo ""

# ─── 1. System packages ───────────────────────────────────────────────────────
msg "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip \
  bluetooth bluez bluez-tools \
  pulseaudio pulseaudio-module-bluetooth \
  alsa-utils dbus \
  avahi-daemon avahi-utils libnss-mdns \
  curl wget ca-certificates git jq tzdata procps
ok "System packages installed"

# ─── 2. App directory and files from GitHub ───────────────────────────────────
msg "Downloading application files from GitHub..."
mkdir -p /opt/sendspin-client

wget -q "${BASE}/sendspin_client.py" -O /opt/sendspin-client/sendspin_client.py
wget -q "${BASE}/web_interface.py"   -O /opt/sendspin-client/web_interface.py
wget -q "${BASE}/requirements.txt"   -O /opt/sendspin-client/requirements.txt
chmod +x /opt/sendspin-client/sendspin_client.py
ok "Application files downloaded"

# ─── 3. Python dependencies ───────────────────────────────────────────────────
msg "Installing Python dependencies..."
pip3 install --break-system-packages -q -r /opt/sendspin-client/requirements.txt
ok "Python dependencies installed"

# ─── 4. Config directory ──────────────────────────────────────────────────────
msg "Setting up config directory..."
mkdir -p /config

if [[ ! -f /config/config.json ]]; then
  cat > /config/config.json <<'EOF'
{
  "SENDSPIN_NAME": "Sendspin-LXC",
  "SENDSPIN_SERVER": "auto",
  "BLUETOOTH_MAC": "",
  "TZ": "UTC"
}
EOF
  ok "Default config.json written to /config/config.json"
else
  ok "config.json already exists — skipping"
fi

# ─── 5. PulseAudio system user ────────────────────────────────────────────────
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
ok "pulse user configured (bluetooth + audio groups)"

# ─── 6. PulseAudio system configuration ──────────────────────────────────────
msg "Writing PulseAudio system configuration..."
mkdir -p /etc/pulse/client.conf.d

cat > /etc/pulse/system.pa <<'EOF'
# /etc/pulse/system.pa
# PulseAudio system-mode configuration for Sendspin LXC deployment

.ifexists module-udev-detect.so
    load-module module-udev-detect
.endif

.ifexists module-bluetooth-policy.so
    load-module module-bluetooth-policy
.endif

.ifexists module-bluetooth-discover.so
    load-module module-bluetooth-discover
.endif

.ifexists module-native-protocol-unix.so
    load-module module-native-protocol-unix auth-anonymous=1 socket=/var/run/pulse/native
.endif

.ifexists module-native-protocol-tcp.so
    load-module module-native-protocol-tcp auth-ip-acl=127.0.0.1 auth-anonymous=1
.endif

.ifexists module-stream-restore.so
    load-module module-stream-restore restore_device=false
.endif

.ifexists module-device-restore.so
    load-module module-device-restore
.endif

.ifexists module-card-restore.so
    load-module module-card-restore
.endif
EOF

cat > /etc/pulse/client.conf.d/00-no-autospawn.conf <<'EOF'
autospawn = no
daemon-binary = /bin/true
EOF

ok "PulseAudio configuration written"

# ─── 7. D-Bus policy for PulseAudio ↔ BlueZ ──────────────────────────────────
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

# ─── 8. Environment variables ─────────────────────────────────────────────────
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

# ─── 9. Systemd units ─────────────────────────────────────────────────────────
msg "Installing systemd service units..."

cat > /etc/systemd/system/pulseaudio-system.service <<'EOF'
[Unit]
Description=PulseAudio System-Mode Daemon (Sendspin)
After=dbus.service
Requires=dbus.service
Before=bluetooth.service sendspin-client.service

[Service]
Type=notify
ExecStart=/usr/bin/pulseaudio --system --realtime --disallow-exit --no-cpu-limit --log-target=journal
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5
User=pulse
Group=pulse
LockPersonality=yes
MemoryDenyWriteExecute=yes
NoNewPrivileges=yes
ProtectHome=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
RestrictNamespaces=yes
RestrictRealtime=no

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sendspin-client.service <<'EOF'
[Unit]
Description=Sendspin Client (Music Assistant Player with Bluetooth)
Documentation=https://github.com/loryanstrant/sendspin-client
After=network-online.target dbus.service bluetooth.service pulseaudio-system.service avahi-daemon.service
Wants=network-online.target
Requires=pulseaudio-system.service dbus.service

[Service]
Type=simple
EnvironmentFile=/etc/environment
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/bin/sleep 3
ExecStart=/usr/bin/python3 /opt/sendspin-client/sendspin_client.py
WorkingDirectory=/opt/sendspin-client
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sendspin-client

[Install]
WantedBy=multi-user.target
EOF

ok "Systemd units installed"

# ─── 10. Enable and start services ───────────────────────────────────────────
msg "Enabling and starting services..."
systemctl daemon-reload

for svc in dbus bluetooth avahi-daemon pulseaudio-system sendspin-client; do
  systemctl enable "$svc" 2>/dev/null || warn "Could not enable $svc (may not exist yet)"
done

# Start in dependency order
for svc in dbus bluetooth avahi-daemon pulseaudio-system sendspin-client; do
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
echo -e "    bluetoothctl scan on"
echo ""
echo -e "  ${YELLOW}Note:${NC} Config changes require:"
echo -e "    systemctl restart sendspin-client"
echo ""
