#!/usr/bin/env bash
# proxmox-create.sh - Create a Sendspin Client LXC container on Proxmox VE
# Run as root on the Proxmox host.
# Inspired by tteck/Proxmox community scripts.

set -euo pipefail

# ─── Colours & helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

msg()    { echo -e "${CYAN}${BOLD}[Sendspin]${NC} $*"; }
ok()     { echo -e "  ${GREEN}✓${NC} $*"; }
warn()   { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()    { echo -e "  ${RED}✗${NC}  $*" >&2; }
die()    { err "$*"; exit 1; }
prompt() { echo -e "  ${BLUE}?${NC}  $*"; }

header() {
  echo ""
  echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${CYAN}${BOLD}  Sendspin Client — Proxmox LXC Installer${NC}"
  echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""
}

spinner() {
  local pid=$1 msg=${2:-"Working..."}
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${CYAN}%s${NC}  %s" "${frames[$((i % ${#frames[@]}))]}" "$msg"
    (( i++ )) || true
    sleep 0.1
  done
  printf "\r%-60s\r" " "
}

ask() {
  # ask VAR "Prompt" "default"
  local var="$1" msg="$2" default="${3:-}"
  if [[ -n "$default" ]]; then
    prompt "${msg} [${BOLD}${default}${NC}]: "
  else
    prompt "${msg}: "
  fi
  read -r "$var" </dev/tty
  if [[ -z "${!var}" && -n "$default" ]]; then
    printf -v "$var" '%s' "$default"
  fi
}

ask_yesno() {
  local var="$1" msg="$2" default="${3:-y}"
  local yn_display
  if [[ "$default" == "y" ]]; then yn_display="Y/n"; else yn_display="y/N"; fi
  prompt "${msg} [${BOLD}${yn_display}${NC}]: "
  local answer
  read -r answer </dev/tty
  answer="${answer:-$default}"
  if [[ "$answer" =~ ^[Yy] ]]; then printf -v "$var" 'y'; else printf -v "$var" 'n'; fi
}

# ─── Pre-flight checks ────────────────────────────────────────────────────────
header

msg "Running pre-flight checks..."

[[ $EUID -eq 0 ]]         || die "Must be run as root"
command -v pvesh &>/dev/null || die "pvesh not found — is this a Proxmox VE host?"
command -v pct   &>/dev/null || die "pct not found — is this a Proxmox VE host?"
command -v pveam &>/dev/null || die "pveam not found — is this a Proxmox VE host?"
ok "Proxmox VE host confirmed"

# ─── Bluetooth on the Proxmox host ───────────────────────────────────────────
# The LXC container cannot run its own bluetoothd (AF_BLUETOOTH kernel limitation
# in LXC network namespaces). Instead, bluetoothd runs on the Proxmox HOST and
# the container accesses it via a bind-mounted D-Bus socket at /bt-dbus.
msg "Ensuring Bluetooth (bluez) is installed on the host..."
if ! command -v bluetoothctl &>/dev/null; then
  apt-get install -y -qq bluez
  ok "bluez installed on host"
else
  ok "bluez already installed on host"
fi

if ! systemctl is-active --quiet bluetooth 2>/dev/null; then
  systemctl enable --now bluetooth
  ok "bluetooth.service enabled and started on host"
else
  ok "bluetooth.service already running on host"
fi

# Verify at least one HCI adapter is present
if ! command -v hciconfig &>/dev/null || ! hciconfig 2>/dev/null | grep -q "hci"; then
  warn "No Bluetooth HCI adapter detected on host. Bluetooth may not work."
  warn "Ensure a USB Bluetooth adapter is connected to the Proxmox host."
else
  HCI_ADDR=$(hciconfig 2>/dev/null | grep "BD Address" | awk '{print $3}' | head -1)
  ok "Bluetooth HCI adapter found: ${HCI_ADDR}"
fi

# ─── Interactive prompts ──────────────────────────────────────────────────────
msg "Container configuration"
echo ""

# Next available CTID
NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "100")

ask CTID         "Container ID"                      "$NEXT_ID"
ask HOSTNAME     "Hostname"                          "sendspin"
ask RAM          "RAM (MB)"                          "512"
ask DISK         "Disk size (GB)"                    "4"
ask CORES        "CPU cores"                         "2"
ask STORAGE      "Storage pool"                      "local-lvm"
ask BRIDGE       "Network bridge"                    "vmbr0"
ask IP           "IP address (CIDR, or 'dhcp')"      "dhcp"

if [[ "$IP" != "dhcp" ]]; then
  ask GATEWAY    "Gateway"                           ""
fi

ask GITHUB_REPO  "GitHub repo (owner/repo)"          "loryanstrant/sendspin-client"
ask GITHUB_BRANCH "Branch"                           "main"

ask_yesno USB_BT "Pass through USB Bluetooth adapter?" "y"

echo ""
warn "You will be prompted to set the LXC root password."
echo ""

# ─── Ubuntu 24.04 template ───────────────────────────────────────────────────
msg "Checking for Ubuntu 24.04 template..."

# Ubuntu 24.04 is required — ships with Python 3.12 which sendspin needs
TEMPLATE=$(pveam available --section system 2>/dev/null \
  | grep "ubuntu-24.04" | sort -V | tail -1 | awk '{print $2}')

if [[ -z "$TEMPLATE" ]]; then
  die "No Ubuntu 24.04 template found in pveam. Run: pveam update"
fi

ok "Found template: ${TEMPLATE}"

# Check if already downloaded
if ! pveam list local 2>/dev/null | grep -q "$TEMPLATE"; then
  msg "Downloading template ${TEMPLATE}..."
  pveam update &>/dev/null &
  spinner $! "Updating template list..."
  pveam download local "$TEMPLATE" &
  spinner $! "Downloading ${TEMPLATE}..."
  wait
  pveam list local 2>/dev/null | grep -q "$TEMPLATE" || die "Template download failed: ${TEMPLATE}"
  ok "Template downloaded"
else
  ok "Template already present"
fi

TEMPLATE_PATH="local:vztmpl/${TEMPLATE}"

# ─── Create container ─────────────────────────────────────────────────────────
msg "Creating LXC container ${CTID}..."

NET_OPTS="name=eth0,bridge=${BRIDGE}"
if [[ "$IP" == "dhcp" ]]; then
  NET_OPTS="${NET_OPTS},ip=dhcp"
else
  NET_OPTS="${NET_OPTS},ip=${IP}"
  if [[ -n "${GATEWAY:-}" ]]; then
    NET_OPTS="${NET_OPTS},gw=${GATEWAY}"
  fi
fi

pct create "$CTID" "$TEMPLATE_PATH" \
  --hostname     "$HOSTNAME"        \
  --memory       "$RAM"             \
  --cores        "$CORES"           \
  --rootfs       "${STORAGE}:${DISK}" \
  --net0         "$NET_OPTS"        \
  --unprivileged 0                  \
  --features     nesting=1          \
  --onboot       1                  \
  --ostype       ubuntu             \
  --password \
  2>&1

ok "Container ${CTID} created"

# ─── LXC config — cgroup rules & USB passthrough ─────────────────────────────
msg "Adding cgroup device rules to container config..."

LXC_CONF="/etc/pve/lxc/${CTID}.conf"

cat >> "$LXC_CONF" <<'EOF'
# AppArmor: unconfined (required for bluetoothd management socket in LXC)
lxc.apparmor.profile: unconfined
# Bluetooth HCI devices
lxc.cgroup2.devices.allow: c 166:* rwm
# Input devices
lxc.cgroup2.devices.allow: c 13:* rwm
# rfkill device (required by bluetoothd)
lxc.cgroup2.devices.allow: c 10:232 rwm
# USB device tree (bind mount, recursive for all USB buses)
lxc.mount.entry: /dev/bus/usb dev/bus/usb none rbind,optional,create=dir 0 0
EOF

if [[ "${USB_BT}" == "y" ]]; then
  cat >> "$LXC_CONF" <<'EOF'
# USB devices (for USB Bluetooth adapter passthrough)
# WARNING: c 189:* grants access to ALL USB devices in the LXC.
# For tighter control, identify the specific device with lsusb and restrict
# to a single bus/device — see the "Manual USB Bluetooth Passthrough" section
# in lxc/README.md.
lxc.cgroup2.devices.allow: c 189:* rwm
EOF
  ok "USB Bluetooth cgroup rules added"
fi

ok "cgroup device rules written to ${LXC_CONF}"

# ─── First start — run install.sh ─────────────────────────────────────────────
# install.sh creates /bt-dbus (the D-Bus bridge mount point) among other things.
# The D-Bus bind-mount entry is added AFTER install.sh, then the container is
# restarted so the mount applies.
msg "Starting container ${CTID} (first start)..."
pct start "$CTID"
ok "Container started"

msg "Waiting for container to be ready..."
timeout 60 pct exec "$CTID" -- systemctl is-system-running --wait 2>/dev/null || true

msg "Downloading install.sh from GitHub..."

INSTALL_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${GITHUB_BRANCH}/lxc/install.sh"

pct exec "$CTID" -- bash -c "curl -fsSL '${INSTALL_URL}' -o /root/install.sh 2>/dev/null || wget -q '${INSTALL_URL}' -O /root/install.sh"
pct exec "$CTID" -- chmod +x /root/install.sh
ok "install.sh downloaded into container"

msg "Running install.sh inside container ${CTID}..."
echo ""
pct exec "$CTID" -- bash /root/install.sh --repo "$GITHUB_REPO" --branch "$GITHUB_BRANCH"

# ─── D-Bus bridge mount ───────────────────────────────────────────────────────
# Bind-mount the host's D-Bus socket directory into the container at /bt-dbus.
# This gives PulseAudio and bluetoothctl (via btctl) access to the host bluetoothd.
# /bt-dbus was created by install.sh above; the mount requires a container restart.
msg "Adding host D-Bus socket mount to ${LXC_CONF}..."
cat >> "$LXC_CONF" <<'EOF'
# Host D-Bus socket — gives PulseAudio and btctl access to the host's bluetoothd.
# Bluetooth runs on the Proxmox host (AF_BLUETOOTH is not available in LXC namespaces).
lxc.mount.entry: /run/dbus bt-dbus none bind,create=dir 0 0
EOF
ok "D-Bus bridge mount entry added"

msg "Restarting container to apply D-Bus mount..."
pct stop "$CTID"
# Wait for container to stop before restarting
for _i in $(seq 1 60); do
  pct status "$CTID" 2>/dev/null | grep -q "running" || break
  sleep 0.5
done
pct start "$CTID"
timeout 60 pct exec "$CTID" -- systemctl is-system-running --wait 2>/dev/null || true
ok "Container restarted with D-Bus bridge active"

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  LXC container created and configured!${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Attempt to resolve container IP for summary
CONTAINER_IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || echo "<container-ip>")

echo -e "  ${BOLD}Container ID:${NC}   ${CTID}"
echo -e "  ${BOLD}Hostname:${NC}       ${HOSTNAME}"
echo -e "  ${BOLD}IP address:${NC}     ${CONTAINER_IP}"
echo -e "  ${BOLD}Web UI:${NC}         http://${CONTAINER_IP}:8080"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "    1. Pair your Bluetooth speaker (see commands below)"
echo -e "    2. Set BLUETOOTH_MAC in web UI: http://${CONTAINER_IP}:8080"
echo -e "    3. Restart the service: ${CYAN}pct exec ${CTID} -- systemctl restart sendspin-client${NC}"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    # Enter the container:"
echo -e "    ${CYAN}pct enter ${CTID}${NC}"
echo ""
echo -e "    # View logs:"
echo -e "    ${CYAN}pct exec ${CTID} -- journalctl -u sendspin-client -f${NC}"
echo ""
echo -e "    # Pair a Bluetooth speaker (from inside the container):"
echo -e "    ${CYAN}pct exec ${CTID} -- btctl${NC}   # opens bluetoothctl via host D-Bus"
echo -e "    ${BLUE}  > scan on${NC}"
echo -e "    ${BLUE}  > pair <MAC>${NC}"
echo -e "    ${BLUE}  > trust <MAC>${NC}"
echo -e "    ${BLUE}  > connect <MAC>${NC}"
echo -e "    ${BLUE}  > quit${NC}"
echo ""
echo -e "    # Or pair directly on the host:"
echo -e "    ${CYAN}bluetoothctl scan on${NC}   # from the Proxmox host"
echo ""
echo -e "  ${YELLOW}Note:${NC} Config changes → ${CYAN}pct exec ${CTID} -- systemctl restart sendspin-client${NC}"
echo ""
