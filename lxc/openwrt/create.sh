#!/bin/sh
# create.sh — Create a Sendspin BT Bridge LXC container on OpenWrt
# Runs on the OpenWrt host as root.
# Works on any OpenWrt-based system with sufficient resources (≥1GB RAM, ≥2GB storage).
#
# Usage:
#   sh create.sh                    # interactive
#   sh create.sh --name sendspin    # with defaults

set -e

# ─── Colours & helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

msg()    { printf "${CYAN}${BOLD}[Sendspin]${NC} %s\n" "$*"; }
ok()     { printf "  ${GREEN}✓${NC} %s\n" "$*"; }
warn()   { printf "  ${YELLOW}⚠${NC}  %s\n" "$*"; }
err()    { printf "  ${RED}✗${NC}  %s\n" "$*" >&2; }
die()    { err "$*"; exit 1; }
prompt() { printf "  ${BLUE}?${NC}  %s" "$*"; }

header() {
    printf "\n"
    printf "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "${CYAN}${BOLD}  Sendspin BT Bridge — OpenWrt LXC Installer${NC}\n"
    printf "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
    printf "\n"
}

ask() {
    local var="$1" text="$2" default="$3"
    if [ -n "$default" ]; then
        prompt "$text [$default]: "
    else
        prompt "$text: "
    fi
    read -r answer </dev/tty
    eval "$var=\"\${answer:-$default}\""
}

# ─── Argument parsing ─────────────────────────────────────────────────────────
CONTAINER_NAME=""
CONTAINER_IP=""
LXC_SERVER=""
GITHUB_REPO="trudenboy/sendspin-bt-bridge"
GITHUB_BRANCH="main"
SKIP_PROMPTS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --name)   CONTAINER_NAME="$2"; shift 2 ;;
        --ip)     CONTAINER_IP="$2";   shift 2 ;;
        --server) LXC_SERVER="$2";     shift 2 ;;
        --repo)   GITHUB_REPO="$2";    shift 2 ;;
        --branch) GITHUB_BRANCH="$2";  shift 2 ;;
        --yes|-y) SKIP_PROMPTS="1";    shift ;;
        *) shift ;;
    esac
done

# ─── Pre-flight checks ───────────────────────────────────────────────────────
header

msg "Running pre-flight checks..."

[ "$(id -u)" -eq 0 ] || die "Must be run as root"

command -v opkg >/dev/null 2>&1 || die "opkg not found — is this an OpenWrt system?"
ok "OpenWrt host confirmed"

# Check available RAM
TOTAL_RAM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
TOTAL_RAM_MB=$((TOTAL_RAM_KB / 1024))
if [ "$TOTAL_RAM_MB" -lt 768 ]; then
    warn "Only ${TOTAL_RAM_MB} MB RAM detected. Minimum recommended: 1024 MB."
    warn "The LXC container may run out of memory during Python package compilation."
fi
ok "RAM: ${TOTAL_RAM_MB} MB"

# Check available storage (root filesystem)
AVAIL_KB=$(df / 2>/dev/null | awk 'NR==2 {print $4}')
AVAIL_MB=$((AVAIL_KB / 1024))
if [ "$AVAIL_MB" -lt 1500 ]; then
    warn "Only ${AVAIL_MB} MB storage available. Minimum recommended: 2048 MB."
    warn "Consider using external storage (USB/SD) for LXC containers."
fi
ok "Storage: ${AVAIL_MB} MB available"

# ─── Auto-detect system properties ───────────────────────────────────────────
msg "Detecting system configuration..."

# Architecture
HOST_ARCH=$(uname -m)
case "$HOST_ARCH" in
    armv7l|armhf)   LXC_ARCH="armhf" ;;
    aarch64|arm64)  LXC_ARCH="arm64" ;;
    x86_64|amd64)   LXC_ARCH="amd64" ;;
    i686|i386)      LXC_ARCH="i386" ;;
    *)              LXC_ARCH="$HOST_ARCH" ;;
esac
ok "Architecture: ${HOST_ARCH} (LXC: ${LXC_ARCH})"

# LXC path
if [ -d "/srv/lxc" ]; then
    LXC_PATH="/srv/lxc"
elif [ -d "/var/lib/lxc" ]; then
    LXC_PATH="/var/lib/lxc"
elif command -v lxc-config >/dev/null 2>&1; then
    LXC_PATH=$(lxc-config lxc.lxcpath 2>/dev/null || echo "/var/lib/lxc")
else
    LXC_PATH="/var/lib/lxc"
fi

# D-Bus socket path (OpenWrt: /var/run → /tmp/run)
if [ -S "/var/run/dbus/system_bus_socket" ]; then
    DBUS_HOST_PATH=$(readlink -f /var/run/dbus 2>/dev/null || echo "/var/run/dbus")
elif [ -S "/run/dbus/system_bus_socket" ]; then
    DBUS_HOST_PATH="/run/dbus"
elif [ -S "/tmp/run/dbus/system_bus_socket" ]; then
    DBUS_HOST_PATH="/tmp/run/dbus"
else
    # D-Bus not running yet — will be started later; use readlink to predict path
    DBUS_HOST_PATH=$(readlink -f /var/run/dbus 2>/dev/null || echo "/tmp/run/dbus")
fi
ok "D-Bus socket path: ${DBUS_HOST_PATH}"

# Network bridge
BRIDGE=$(uci get network.lan.device 2>/dev/null || \
         uci get network.lan.ifname 2>/dev/null || \
         echo "br-lan")
ok "Network bridge: ${BRIDGE}"

# ─── 1. Install LXC on host ──────────────────────────────────────────────────
msg "Installing LXC packages..."

opkg update >/dev/null 2>&1 || warn "opkg update failed — using cached package lists"

LXC_PKGS="lxc lxc-attach lxc-create lxc-start lxc-stop lxc-info lxc-destroy lxc-ls"
LXC_PKGS="$LXC_PKGS lxc-common lxc-hooks lxc-templates lxc-init liblxc"

for pkg in $LXC_PKGS; do
    opkg install "$pkg" >/dev/null 2>&1 || true
done

command -v lxc-create >/dev/null 2>&1 || die "lxc-create not found after install — check opkg output"
ok "LXC installed"

# Re-detect LXC path after install
if command -v lxc-config >/dev/null 2>&1; then
    LXC_PATH=$(lxc-config lxc.lxcpath 2>/dev/null || echo "$LXC_PATH")
fi
ok "LXC path: ${LXC_PATH}"

# ─── 2. Install Bluetooth stack on host ───────────────────────────────────────
msg "Installing Bluetooth stack..."

BT_PKGS="kmod-bluetooth bluez-daemon bluez-utils dbus dbus-utils"
for pkg in $BT_PKGS; do
    opkg install "$pkg" >/dev/null 2>&1 || true
done
ok "Bluetooth packages installed"

# Load kernel modules
modprobe bluetooth 2>/dev/null || true
modprobe btusb 2>/dev/null || true
modprobe hci_uart 2>/dev/null || true

# Persist kernel modules across reboots
for mod in bluetooth btusb; do
    if [ ! -f "/etc/modules.d/99-${mod}" ]; then
        echo "$mod" > "/etc/modules.d/99-${mod}"
    fi
done
ok "Bluetooth kernel modules loaded and persisted"

# Start bluetoothd
if [ -x /etc/init.d/bluetoothd ]; then
    /etc/init.d/bluetoothd enable 2>/dev/null || true
    /etc/init.d/bluetoothd start 2>/dev/null || true
    ok "bluetoothd started via init.d"
elif [ -x /etc/init.d/bluetooth ]; then
    /etc/init.d/bluetooth enable 2>/dev/null || true
    /etc/init.d/bluetooth start 2>/dev/null || true
    ok "bluetooth started via init.d"
else
    warn "No bluetoothd init script found — bluetooth may need manual start"
fi

# Start D-Bus if not running
if [ -x /etc/init.d/dbus ]; then
    /etc/init.d/dbus enable 2>/dev/null || true
    /etc/init.d/dbus start 2>/dev/null || true
fi

# Re-detect D-Bus path after start
if [ -S "/var/run/dbus/system_bus_socket" ]; then
    DBUS_HOST_PATH=$(readlink -f /var/run/dbus 2>/dev/null || echo "/var/run/dbus")
elif [ -S "/tmp/run/dbus/system_bus_socket" ]; then
    DBUS_HOST_PATH="/tmp/run/dbus"
fi

# ─── 3. Install D-Bus policy on host ─────────────────────────────────────────
msg "Installing D-Bus policy for PulseAudio ↔ BlueZ..."

DBUS_POLICY_DIR="/etc/dbus-1/system.d"
mkdir -p "$DBUS_POLICY_DIR"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -f "${SCRIPT_DIR}/dbus-pulseaudio.conf" ]; then
    cp "${SCRIPT_DIR}/dbus-pulseaudio.conf" "${DBUS_POLICY_DIR}/pulseaudio-lxc.conf"
else
    BASE_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}"
    wget -q "${BASE_URL}/lxc/openwrt/dbus-pulseaudio.conf" -O "${DBUS_POLICY_DIR}/pulseaudio-lxc.conf" || \
    cat > "${DBUS_POLICY_DIR}/pulseaudio-lxc.conf" <<'DBUSEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <policy context="default">
    <allow own="org.pulseaudio.Server"/>
    <allow send_destination="org.bluez"/>
    <allow send_interface="org.bluez.MediaEndpoint1"/>
    <allow send_interface="org.bluez.MediaTransport1"/>
    <allow send_interface="org.bluez.Profile1"/>
    <allow send_interface="org.freedesktop.DBus.ObjectManager"/>
    <allow send_interface="org.freedesktop.DBus.Properties"/>
  </policy>
</busconfig>
DBUSEOF
fi

# Reload D-Bus to pick up new policy
if [ -x /etc/init.d/dbus ]; then
    /etc/init.d/dbus reload 2>/dev/null || /etc/init.d/dbus restart 2>/dev/null || true
fi
ok "D-Bus policy installed"

# ─── 4. Interactive prompts ───────────────────────────────────────────────────
msg "Container configuration"
printf "\n"

[ -z "$CONTAINER_NAME" ] && { [ -n "$SKIP_PROMPTS" ] && CONTAINER_NAME="sendspin" || ask CONTAINER_NAME "Container name" "sendspin"; }
[ -z "$CONTAINER_IP" ]   && { [ -n "$SKIP_PROMPTS" ] && CONTAINER_IP="dhcp"       || ask CONTAINER_IP   "IP address (CIDR or 'dhcp')" "dhcp"; }
[ -z "$LXC_SERVER" ]     && { [ -n "$SKIP_PROMPTS" ] && LXC_SERVER="images.linuxcontainers.org" || ask LXC_SERVER "LXC template server" "images.linuxcontainers.org"; }

# Parse static IP if not dhcp
STATIC_IP=""
STATIC_GW=""
STATIC_DNS=""
if [ "$CONTAINER_IP" != "dhcp" ]; then
    STATIC_IP="$CONTAINER_IP"
    # Ensure CIDR notation
    case "$STATIC_IP" in
        */*) ;; # already has /mask
        *)   STATIC_IP="${STATIC_IP}/24" ;;
    esac
    if [ -z "$SKIP_PROMPTS" ]; then
        ask STATIC_GW  "Gateway" "$(echo "$STATIC_IP" | sed 's/\.[0-9]*\/.*/.1/')"
        ask STATIC_DNS "DNS server" "1.1.1.1"
    else
        STATIC_GW=$(echo "$STATIC_IP" | sed 's/\.[0-9]*\/.*/.1/')
        STATIC_DNS="1.1.1.1"
    fi
fi

printf "\n"
msg "Configuration summary:"
ok "Name:       ${CONTAINER_NAME}"
ok "LXC path:   ${LXC_PATH}"
ok "Arch:       ${LXC_ARCH}"
ok "IP:         ${CONTAINER_IP}"
ok "Bridge:     ${BRIDGE}"
ok "D-Bus:      ${DBUS_HOST_PATH}"
ok "LXC server: ${LXC_SERVER}"
printf "\n"

# Check if container already exists
if [ -d "${LXC_PATH}/${CONTAINER_NAME}" ]; then
    die "Container '${CONTAINER_NAME}' already exists at ${LXC_PATH}/${CONTAINER_NAME}"
fi

# ─── 5. Create LXC container ─────────────────────────────────────────────────
msg "Creating Ubuntu 24.04 LXC container '${CONTAINER_NAME}'..."
msg "This may take a few minutes (downloading template)..."

lxc-create -t download -n "$CONTAINER_NAME" -- \
    --dist ubuntu --release noble --arch "$LXC_ARCH" \
    --server "$LXC_SERVER"

[ -d "${LXC_PATH}/${CONTAINER_NAME}" ] || die "Container creation failed"
ok "Container created at ${LXC_PATH}/${CONTAINER_NAME}"

# ─── 6. Configure LXC container ──────────────────────────────────────────────
msg "Configuring container..."

LXC_CONF="${LXC_PATH}/${CONTAINER_NAME}/config"

# Generate a random MAC address (locally administered)
RANDOM_MAC=$(printf '02:%02x:%02x:%02x:%02x:%02x' \
    $((RANDOM % 256)) $((RANDOM % 256)) $((RANDOM % 256)) \
    $((RANDOM % 256)) $((RANDOM % 256)))

# Append Sendspin-specific config
cat >> "$LXC_CONF" <<LXCEOF

# === Sendspin BT Bridge config ===

# Network
lxc.net.0.type = veth
lxc.net.0.link = ${BRIDGE}
lxc.net.0.flags = up
lxc.net.0.name = eth0
lxc.net.0.hwaddr = ${RANDOM_MAC}

# Host D-Bus socket mount (for Bluetooth access)
lxc.mount.entry = ${DBUS_HOST_PATH} bt-dbus none bind,create=dir 0 0

# cgroup device access
lxc.cgroup2.devices.allow = c 13:* rwm
lxc.cgroup2.devices.allow = c 108:* rwm
lxc.cgroup2.devices.allow = c 189:* rwm

# Autostart
lxc.start.auto = 1
lxc.start.delay = 5
LXCEOF

# Remove AppArmor lines if present (not supported on OpenWrt)
if grep -q 'lxc.apparmor' "$LXC_CONF" 2>/dev/null; then
    sed -i '/lxc\.apparmor/d' "$LXC_CONF"
    ok "Removed AppArmor config (not supported on OpenWrt)"
fi

ok "LXC config written"

# ─── 7. Start container ──────────────────────────────────────────────────────
msg "Starting container '${CONTAINER_NAME}'..."
lxc-start -n "$CONTAINER_NAME"

# Wait for container to be ready
msg "Waiting for container to initialize..."
WAIT_COUNT=0
while [ $WAIT_COUNT -lt 60 ]; do
    if lxc-attach -n "$CONTAINER_NAME" -- /bin/true 2>/dev/null; then
        break
    fi
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

STATE=$(lxc-info -n "$CONTAINER_NAME" -sH 2>/dev/null)
[ "$STATE" = "RUNNING" ] || die "Container failed to start (state: ${STATE})"
ok "Container is running"

# ─── 8. Configure network inside container ───────────────────────────────────
msg "Configuring network inside container..."

if [ -n "$STATIC_IP" ]; then
    lxc-attach -n "$CONTAINER_NAME" -- bash -c "
        mkdir -p /etc/systemd/network
        cat > /etc/systemd/network/10-eth0.network <<'NETEOF'
[Match]
Name=eth0

[Network]
Address=${STATIC_IP}
Gateway=${STATIC_GW}
DNS=${STATIC_DNS}
NETEOF
        systemctl enable systemd-networkd 2>/dev/null || true
        systemctl restart systemd-networkd 2>/dev/null || true
    "
    ok "Static IP configured: ${STATIC_IP}"
else
    lxc-attach -n "$CONTAINER_NAME" -- bash -c "
        mkdir -p /etc/systemd/network
        cat > /etc/systemd/network/10-eth0.network <<'NETEOF'
[Match]
Name=eth0

[Network]
DHCP=yes
NETEOF
        systemctl enable systemd-networkd 2>/dev/null || true
        systemctl restart systemd-networkd 2>/dev/null || true
    "
    ok "DHCP configured on eth0"
fi

# Wait for network
sleep 3
CONTAINER_ADDR=$(lxc-attach -n "$CONTAINER_NAME" -- hostname -I 2>/dev/null | awk '{print $1}')
if [ -n "$CONTAINER_ADDR" ]; then
    ok "Container IP: ${CONTAINER_ADDR}"
else
    warn "Could not detect container IP — network may still be initializing"
    CONTAINER_ADDR="<container-ip>"
fi

# ─── 9. Run install.sh inside container ───────────────────────────────────────
msg "Downloading and running install.sh inside container..."

INSTALL_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}/lxc/install.sh"

lxc-attach -n "$CONTAINER_NAME" -- bash -c "
    apt-get update -qq && apt-get install -y -qq wget curl ca-certificates >/dev/null 2>&1
    wget -q '${INSTALL_URL}' -O /root/install.sh
    chmod +x /root/install.sh
    bash /root/install.sh --repo '${GITHUB_REPO}' --branch '${GITHUB_BRANCH}'
"

ok "install.sh completed"

# ─── 10. Install init.d script on host ────────────────────────────────────────
msg "Installing init.d autostart script..."

INIT_SCRIPT="/etc/init.d/sendspin-lxc"

if [ -f "${SCRIPT_DIR}/sendspin-lxc.init" ]; then
    cp "${SCRIPT_DIR}/sendspin-lxc.init" "$INIT_SCRIPT"
else
    BASE_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}"
    wget -q "${BASE_URL}/lxc/openwrt/sendspin-lxc.init" -O "$INIT_SCRIPT" 2>/dev/null || \
    warn "Could not download init script — container autostart must be configured manually"
fi

if [ -f "$INIT_SCRIPT" ]; then
    chmod +x "$INIT_SCRIPT"
    # Set container name in the script
    sed -i "s/CONTAINER_NAME=\"\${SENDSPIN_LXC_NAME:-sendspin}\"/CONTAINER_NAME=\"\${SENDSPIN_LXC_NAME:-${CONTAINER_NAME}}\"/" "$INIT_SCRIPT"
    "$INIT_SCRIPT" enable 2>/dev/null || true
    ok "init.d script installed and enabled"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
# Re-detect IP in case it changed
CONTAINER_ADDR=$(lxc-attach -n "$CONTAINER_NAME" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "$CONTAINER_ADDR")

printf "\n"
printf "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${GREEN}${BOLD}  Sendspin BT Bridge deployed successfully!${NC}\n"
printf "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "\n"
printf "  ${BOLD}Container:${NC}   %s\n" "$CONTAINER_NAME"
printf "  ${BOLD}IP address:${NC}  %s\n" "$CONTAINER_ADDR"
printf "  ${BOLD}Web UI:${NC}      http://%s:8080\n" "$CONTAINER_ADDR"
printf "\n"
printf "  ${BOLD}Next steps:${NC}\n"
printf "    1. Connect a USB Bluetooth adapter to this device\n"
printf "    2. Pair your Bluetooth speaker:\n"
printf "       ${CYAN}lxc-attach -n %s -- btctl${NC}\n" "$CONTAINER_NAME"
printf "       ${BLUE}  > scan on${NC}\n"
printf "       ${BLUE}  > pair XX:XX:XX:XX:XX:XX${NC}\n"
printf "       ${BLUE}  > trust XX:XX:XX:XX:XX:XX${NC}\n"
printf "       ${BLUE}  > connect XX:XX:XX:XX:XX:XX${NC}\n"
printf "       ${BLUE}  > quit${NC}\n"
printf "    3. Set BLUETOOTH_MAC in the web UI: http://%s:8080\n" "$CONTAINER_ADDR"
printf "    4. Restart the service:\n"
printf "       ${CYAN}lxc-attach -n %s -- systemctl restart sendspin-client${NC}\n" "$CONTAINER_NAME"
printf "\n"
printf "  ${BOLD}Useful commands:${NC}\n"
printf "    ${CYAN}lxc-attach -n %s${NC}                           # enter container shell\n" "$CONTAINER_NAME"
printf "    ${CYAN}lxc-attach -n %s -- journalctl -u sendspin-client -f${NC}  # view logs\n" "$CONTAINER_NAME"
printf "    ${CYAN}lxc-attach -n %s -- pactl list sinks short${NC}           # audio sinks\n" "$CONTAINER_NAME"
printf "    ${CYAN}/etc/init.d/sendspin-lxc restart${NC}           # restart container\n"
printf "\n"
printf "  ${YELLOW}Note:${NC} Config changes → ${CYAN}lxc-attach -n %s -- systemctl restart sendspin-client${NC}\n" "$CONTAINER_NAME"
printf "\n"
