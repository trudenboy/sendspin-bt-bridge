#!/usr/bin/env bash
# Setup GitHub issue labels for sendspin-bt-bridge
# Usage: ./scripts/setup-labels.sh [owner/repo]
#
# Requires: gh CLI authenticated

set -euo pipefail

REPO="${1:-trudenboy/sendspin-bt-bridge}"

create_or_update_label() {
  local name="$1" color="$2" description="$3"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$description" --force 2>/dev/null \
    && echo "  ✅ $name" \
    || echo "  ❌ Failed: $name"
}

echo "Setting up labels for $REPO"
echo ""

echo "📂 Category labels:"
create_or_update_label "bluetooth"    "1E90FF" "Bluetooth connection and pairing"
create_or_update_label "audio"        "9B59B6" "PulseAudio / PipeWire / codecs"
create_or_update_label "web-ui"       "2ECC71" "Web interface"
create_or_update_label "api"          "3498DB" "REST API"
create_or_update_label "config"       "F39C12" "Configuration"
create_or_update_label "multiroom"    "E74C3C" "Multiroom sync"
create_or_update_label "ha-addon"     "00BCD4" "Home Assistant addon"
create_or_update_label "docker"       "2496ED" "Docker deployment"
create_or_update_label "lxc"          "FF9800" "Proxmox / OpenWrt LXC"
echo ""

echo "🔴 Priority labels:"
create_or_update_label "priority: critical" "B60205" "Service down / data loss"
create_or_update_label "priority: high"     "D93F0B" "Major feature broken"
create_or_update_label "priority: low"      "0E8A16" "Cosmetic / minor"
echo ""

echo "📋 Status labels:"
create_or_update_label "needs-info" "FBCA04" "Waiting for more information from reporter"
create_or_update_label "confirmed"  "0075CA" "Reproduced by maintainer"
create_or_update_label "wontfix"    "FFFFFF" "Not planned"
create_or_update_label "duplicate"  "CFD3D7" "Duplicate of another issue"
echo ""

echo "Done! 🎉"
