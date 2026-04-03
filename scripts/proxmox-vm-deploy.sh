#!/usr/bin/env bash
# proxmox-vm-deploy.sh — Deploy/update sendspin-bt-bridge on the test VM
# Run from your Mac: bash scripts/proxmox-vm-deploy.sh
#
# Assumes VM was created with proxmox-vm-create.sh and is reachable at
# sendspin-test (or 192.168.10.105).

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
VM_HOST="${SENDSPIN_TEST_HOST:-sendspin-test}"
VM_USER="${SENDSPIN_TEST_USER:-ubuntu}"
IMAGE="${SENDSPIN_IMAGE:-ghcr.io/trudenboy/sendspin-bt-bridge:latest}"
WEB_PORT="${SENDSPIN_WEB_PORT:-8080}"
TZ="${SENDSPIN_TZ:-Europe/Moscow}"
CONFIG_DIR="/etc/docker/Sendspin"

# ─── Colours & helpers ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

msg()  { echo -e "${CYAN}${BOLD}[Deploy]${NC} $*"; }
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "  ${RED}✗${NC}  $*" >&2; }
die()  { err "$*"; exit 1; }

SSH_CMD="ssh -o ConnectTimeout=5 ${VM_USER}@${VM_HOST}"

# ─── Pre-flight ───────────────────────────────────────────────────────────────
msg "Deploying bridge to ${VM_HOST}..."

$SSH_CMD "echo ok" &>/dev/null || die "Cannot reach ${VM_HOST}. Is the VM running?"
ok "SSH connection OK"

$SSH_CMD "docker info" &>/dev/null || die "Docker not available on ${VM_HOST}"
ok "Docker available"

# ─── docker-compose.yml ──────────────────────────────────────────────────────
msg "Writing docker-compose.yml..."

$SSH_CMD "cat > ~/docker-compose.yml" << 'COMPOSE_EOF'
services:
  sendspin-client:
    image: IMAGE_PLACEHOLDER
    container_name: sendspin-client
    restart: unless-stopped
    network_mode: host

    volumes:
      - /var/run/dbus:/var/run/dbus
      - /run/user/1000/pulse:/run/user/1000/pulse
      - /run/user/1000/pipewire-0:/run/user/1000/pipewire-0
      - CONFIG_DIR_PLACEHOLDER:/config

    environment:
      - SENDSPIN_SERVER=auto
      - TZ=TZ_PLACEHOLDER
      - WEB_PORT=WEB_PORT_PLACEHOLDER
      - CONFIG_DIR=/config
      - AUDIO_UID=1000
      - AUDIO_GID=1000
      - PULSE_SERVER=unix:/run/user/1000/pulse/native
      - XDG_RUNTIME_DIR=/run/user/1000

    devices:
      - /dev/bus/usb:/dev/bus/usb

    cap_add:
      - NET_ADMIN
      - NET_RAW

    security_opt:
      - apparmor:unconfined
      - seccomp:unconfined

    labels:
      - "com.centurylinklabs.watchtower.enable=true"
COMPOSE_EOF

# Substitute placeholders
$SSH_CMD "sed -i \
  -e 's|IMAGE_PLACEHOLDER|${IMAGE}|' \
  -e 's|CONFIG_DIR_PLACEHOLDER|${CONFIG_DIR}|' \
  -e 's|TZ_PLACEHOLDER|${TZ}|' \
  -e 's|WEB_PORT_PLACEHOLDER|${WEB_PORT}|' \
  ~/docker-compose.yml"
ok "docker-compose.yml created"

# ─── config.json ──────────────────────────────────────────────────────────────
msg "Checking config.json..."

CONFIG_EXISTS=$($SSH_CMD "test -f ${CONFIG_DIR}/config.json && echo yes || echo no")

if [[ "$CONFIG_EXISTS" == "no" ]]; then
  msg "Creating minimal config.json..."
  $SSH_CMD "cat > ${CONFIG_DIR}/config.json" << 'CONFIG_EOF'
{
  "SENDSPIN_SERVER": "auto",
  "SENDSPIN_PORT": 9000,
  "BLUETOOTH_DEVICES": [],
  "BLUETOOTH_ADAPTERS": [],
  "LOG_LEVEL": "INFO"
}
CONFIG_EOF
  ok "Minimal config.json created (add devices via web UI at http://${VM_HOST}:${WEB_PORT})"
else
  ok "config.json already exists, preserving"
fi

# ─── Pull & deploy ───────────────────────────────────────────────────────────
msg "Pulling image..."
$SSH_CMD "docker compose -f ~/docker-compose.yml pull" 2>&1 | tail -2
ok "Image pulled"

msg "Starting container..."
$SSH_CMD "docker compose -f ~/docker-compose.yml up -d" 2>&1 | tail -3
ok "Container started"

# ─── Health check ─────────────────────────────────────────────────────────────
msg "Waiting for health check..."
VM_IP="${VM_HOST}"

# Resolve hostname to IP if needed
if ! [[ "$VM_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  VM_IP=$(ssh -G "${VM_USER}@${VM_HOST}" | awk '/^hostname / {print $2}')
fi

MAX_WAIT=60
elapsed=0
while true; do
  if curl -sf "http://${VM_IP}:${WEB_PORT}/api/health" &>/dev/null; then
    break
  fi
  sleep 3
  elapsed=$((elapsed + 3))
  if [[ $elapsed -ge $MAX_WAIT ]]; then
    warn "Health check not passing after ${MAX_WAIT}s"
    warn "Check logs: ssh ${VM_USER}@${VM_HOST} docker logs sendspin-client"
    break
  fi
done

if [[ $elapsed -lt $MAX_WAIT ]]; then
  ok "Health check passed"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
VERSION=$($SSH_CMD "docker exec sendspin-client python3 -c 'from config import VERSION; print(VERSION)'" 2>/dev/null || echo "unknown")

echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Bridge deployed on ${VM_HOST}!${NC}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Version:${NC}    ${VERSION}"
echo -e "  ${BOLD}Web UI:${NC}     http://${VM_IP}:${WEB_PORT}"
echo -e "  ${BOLD}Container:${NC}  ssh ${VM_USER}@${VM_HOST} docker logs -f sendspin-client"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    ssh ${VM_USER}@${VM_HOST} docker logs -f sendspin-client     # Live logs"
echo -e "    ssh ${VM_USER}@${VM_HOST} docker restart sendspin-client     # Restart"
echo -e "    ssh ${VM_USER}@${VM_HOST} docker exec -it sendspin-client bluetoothctl  # BT debug"
echo -e "    bash scripts/proxmox-vm-deploy.sh                           # Redeploy / update"
echo ""
