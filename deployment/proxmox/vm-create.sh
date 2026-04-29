#!/usr/bin/env bash
# proxmox-vm-create.sh — Create a Sendspin test VM (Ubuntu 24.04 + Docker) on Proxmox VE
# Run as root on the Proxmox host: bash proxmox-vm-create.sh
#
# Creates VM 105 with cloud-init, USB BT passthrough, Docker + audio provisioning.
# After completion, deploy the bridge with: scripts/proxmox-vm-deploy.sh

set -euo pipefail

# ─── Colours & helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

msg()    { echo -e "${CYAN}${BOLD}[Sendspin VM]${NC} $*"; }
ok()     { echo -e "  ${GREEN}✓${NC} $*"; }
warn()   { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()    { echo -e "  ${RED}✗${NC}  $*" >&2; }
die()    { err "$*"; exit 1; }
prompt() { echo -e "  ${BLUE}?${NC}  $*"; }

header() {
  echo ""
  echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${CYAN}${BOLD}  Sendspin Client — Proxmox VM Installer${NC}"
  echo -e "${CYAN}${BOLD}  Ubuntu 24.04 LTS + Docker${NC}"
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

[[ $EUID -eq 0 ]]          || die "Must be run as root on Proxmox host"
command -v qm   &>/dev/null || die "qm not found — is this a Proxmox VE host?"
command -v pvesh &>/dev/null || die "pvesh not found — is this a Proxmox VE host?"
ok "Proxmox VE host confirmed"

# ─── Interactive prompts ──────────────────────────────────────────────────────
msg "VM configuration"
echo ""

NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "105")

ask VMID           "VM ID"                            "$NEXT_ID"
ask HOSTNAME       "Hostname"                         "sendspin-test"
ask RAM            "RAM (MB)"                         "2048"
ask DISK           "Disk size (GB)"                   "16"
ask CORES          "CPU cores"                        "2"
ask STORAGE        "Storage pool (thin-provisioned)"  "local-lvm"
ask BRIDGE         "Network bridge"                   "vmbr0"
ask IP             "IP address (CIDR)"                "192.168.10.105/24"
ask GATEWAY        "Gateway"                          "192.168.10.1"
ask DNS            "DNS server"                       "192.168.10.1"
ask TZ             "Timezone"                         "Europe/Moscow"

ask_yesno USB_BT   "Pass through USB BT adapter (BLE mapping)?" "y"

if [[ "$USB_BT" == "y" ]]; then
  ask USB_MAPPING  "USB mapping name"                 "BLE"
fi

echo ""

# ─── Validate VM doesn't exist ────────────────────────────────────────────────
if qm status "$VMID" &>/dev/null; then
  die "VM $VMID already exists. Remove it first: qm destroy $VMID --purge"
fi
ok "VM $VMID is available"

# ─── Cloud image ──────────────────────────────────────────────────────────────
msg "Checking for Ubuntu 24.04 cloud image..."

CLOUD_IMAGE="/var/lib/vz/template/iso/ubuntu-24.04-amd64.img"
CLOUD_IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"

if [[ -f "$CLOUD_IMAGE" ]]; then
  ok "Cloud image found: $CLOUD_IMAGE"
else
  msg "Downloading Ubuntu 24.04 cloud image..."
  wget -q --show-progress -O "$CLOUD_IMAGE" "$CLOUD_IMAGE_URL" &
  spinner $! "Downloading noble-server-cloudimg-amd64.img..."
  wait
  [[ -f "$CLOUD_IMAGE" ]] || die "Cloud image download failed"
  ok "Cloud image downloaded"
fi

# ─── SSH key ──────────────────────────────────────────────────────────────────
msg "Detecting SSH public key..."

SSH_KEY=""
for key_file in /root/.ssh/id_rsa.pub /root/.ssh/id_ed25519.pub; do
  if [[ -f "$key_file" ]]; then
    SSH_KEY="$key_file"
    break
  fi
done

if [[ -z "$SSH_KEY" ]]; then
  die "No SSH public key found in /root/.ssh/. Generate one: ssh-keygen -t ed25519"
fi
ok "Using SSH key: $SSH_KEY"

# ─── Create VM ────────────────────────────────────────────────────────────────
msg "Creating VM $VMID ($HOSTNAME)..."

qm create "$VMID" \
  --name "$HOSTNAME" \
  --ostype l26 \
  --cpu host \
  --cores "$CORES" \
  --memory "$RAM" \
  --net0 "virtio,bridge=${BRIDGE}" \
  --agent 1 \
  --onboot 0 \
  --scsihw virtio-scsi-single
ok "VM created"

# Import cloud image as disk
msg "Importing cloud image to ${STORAGE}..."
qm importdisk "$VMID" "$CLOUD_IMAGE" "$STORAGE" --format raw &>/dev/null &
spinner $! "Importing disk..."
wait
ok "Disk imported"

# Attach disk and configure boot
qm set "$VMID" --scsi0 "${STORAGE}:vm-${VMID}-disk-0,discard=on,iothread=1,ssd=1"
qm set "$VMID" --boot order=scsi0
qm set "$VMID" --serial0 socket --vga serial0
ok "Boot configured"

# Resize disk
if [[ "$DISK" -gt 4 ]]; then
  qm resize "$VMID" scsi0 "${DISK}G"
  ok "Disk resized to ${DISK}G"
fi

# ─── Cloud-init ───────────────────────────────────────────────────────────────
msg "Configuring cloud-init..."

qm set "$VMID" --ide2 "${STORAGE}:cloudinit"
qm set "$VMID" --ciuser ubuntu
qm set "$VMID" --sshkeys "$SSH_KEY"
qm set "$VMID" --ipconfig0 "ip=${IP},gw=${GATEWAY}"
qm set "$VMID" --nameserver "$DNS"
qm set "$VMID" --searchdomain "local"
ok "Cloud-init configured: user=ubuntu, ip=${IP}"

# ─── USB passthrough ─────────────────────────────────────────────────────────
if [[ "$USB_BT" == "y" ]]; then
  msg "Configuring USB BT passthrough (mapping=${USB_MAPPING})..."
  qm set "$VMID" --usb0 "mapping=${USB_MAPPING}"
  ok "USB mapping '${USB_MAPPING}' assigned to VM"
fi

# ─── Start VM ─────────────────────────────────────────────────────────────────
msg "Starting VM $VMID..."
qm start "$VMID"
ok "VM started"

# Wait for SSH
msg "Waiting for SSH to become available..."
VM_IP="${IP%%/*}"
MAX_WAIT=120
elapsed=0
while ! ssh -o ConnectTimeout=2 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "ubuntu@${VM_IP}" "echo ok" &>/dev/null; do
  sleep 3
  elapsed=$((elapsed + 3))
  if [[ $elapsed -ge $MAX_WAIT ]]; then
    die "SSH not available after ${MAX_WAIT}s. Check VM console: qm terminal $VMID"
  fi
  printf "\r  ${CYAN}⠼${NC}  Waiting for SSH... (%ds / %ds)" "$elapsed" "$MAX_WAIT"
done
printf "\r%-60s\r" " "
ok "SSH available at ubuntu@${VM_IP}"

# ─── Provision: system packages ───────────────────────────────────────────────
msg "Provisioning VM (this takes a few minutes)..."

SSH_CMD="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ubuntu@${VM_IP}"

# Set timezone
$SSH_CMD "sudo timedatectl set-timezone '$TZ'" 2>/dev/null
ok "Timezone set to $TZ"

# Wait for cloud-init to finish (apt lock)
$SSH_CMD "sudo cloud-init status --wait" &>/dev/null &
spinner $! "Waiting for cloud-init to finish..."
wait
ok "Cloud-init complete"

# System update + essential packages
msg "Installing system packages..."
$SSH_CMD "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
  sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq && \
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    bluetooth bluez pulseaudio-module-bluetooth \
    linux-modules-extra-\$(uname -r) \
    ca-certificates curl gnupg lsb-release qemu-guest-agent" &>/dev/null &
spinner $! "Installing bluetooth, audio, kernel modules, utilities..."
wait
ok "System packages installed"

# Enable qemu-guest-agent
$SSH_CMD "sudo systemctl enable --now qemu-guest-agent" &>/dev/null
ok "QEMU guest agent enabled"

# ─── Provision: Docker CE ─────────────────────────────────────────────────────
msg "Installing Docker CE..."
$SSH_CMD "curl -fsSL https://get.docker.com | sudo sh" &>/dev/null &
spinner $! "Installing Docker..."
wait
ok "Docker installed"

# Add ubuntu user to required groups
$SSH_CMD "sudo usermod -aG docker,bluetooth,audio ubuntu" 2>/dev/null
ok "User 'ubuntu' added to docker, bluetooth, audio groups"

# ─── Provision: directories ───────────────────────────────────────────────────
msg "Creating directories..."
$SSH_CMD "sudo mkdir -p /etc/docker/Sendspin && sudo chown ubuntu:ubuntu /etc/docker/Sendspin"
ok "/etc/docker/Sendspin created"

# Enable PipeWire user session at boot (lingering)
$SSH_CMD "sudo loginctl enable-linger ubuntu" 2>/dev/null
ok "User lingering enabled (PipeWire session persists)"

# ─── Provision: Bluetooth service ─────────────────────────────────────────────
msg "Configuring Bluetooth..."
$SSH_CMD "sudo modprobe bluetooth && sudo modprobe btusb" 2>/dev/null
$SSH_CMD "echo -e 'bluetooth\nbtusb' | sudo tee /etc/modules-load.d/bluetooth.conf > /dev/null"
$SSH_CMD "sudo systemctl enable bluetooth && sudo systemctl start bluetooth" 2>/dev/null
ok "Bluetooth service enabled (kernel modules persisted)"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  VM $VMID ($HOSTNAME) created successfully!${NC}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}SSH:${NC}          ssh ubuntu@${VM_IP}"
echo -e "  ${BOLD}Config dir:${NC}   /etc/docker/Sendspin"
echo -e "  ${BOLD}BT adapter:${NC}   ${USB_BT:+${USB_MAPPING} mapping}${USB_BT:-none}"
echo ""
echo -e "  ${BOLD}Next step:${NC}    Deploy the bridge with:"
echo -e "                ${CYAN}scripts/proxmox-vm-deploy.sh${NC}"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    qm status $VMID            # Check VM status"
echo -e "    qm stop $VMID              # Stop VM"
echo -e "    qm start $VMID             # Start VM"
echo -e "    qm destroy $VMID --purge   # Remove VM completely"
echo ""
