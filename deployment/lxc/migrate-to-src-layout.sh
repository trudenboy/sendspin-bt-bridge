#!/bin/sh
# One-shot migration helper for LXC users upgrading from pre-2.66 layout.
#
# Sendspin Bluetooth Bridge v2.66.0 moved its Python sources from a flat
# repo-root layout into src/sendspin_bridge/. The pre-2.66 upgrade.sh
# expects `*.py` at the snapshot root and copies them to `/opt/sendspin-
# client/` directly — that copy step finds nothing in v2.66+ snapshots
# and leaves the install corrupt.
#
# Run this script ONCE on each LXC instance before upgrading from
# v2.65.x to v2.66.0+:
#
#     curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/deployment/lxc/migrate-to-src-layout.sh | sh
#
# What it does:
#   1. Downloads the v2.66+ upgrade.sh into /opt/sendspin-client/lxc/.
#      (Old install also has lxc/upgrade.sh; both are accepted.)
#   2. Hands off to it (preserves any args).
#
# Idempotent: re-running on a v2.66+ install just refreshes upgrade.sh.
set -eu

APP_DIR="${APP_DIR:-/opt/sendspin-client}"
REPO_RAW="https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main"

if [ ! -d "${APP_DIR}" ]; then
    echo "ERROR: ${APP_DIR} does not exist; nothing to migrate." >&2
    echo "(For a fresh install run deployment/lxc/install.sh instead.)" >&2
    exit 1
fi

echo "Downloading v2.66+ upgrade.sh into ${APP_DIR}/lxc/..."
mkdir -p "${APP_DIR}/lxc"
curl -fsSL "${REPO_RAW}/deployment/lxc/upgrade.sh" -o "${APP_DIR}/lxc/upgrade.sh"
chmod +x "${APP_DIR}/lxc/upgrade.sh"

echo "Running upgrade..."
exec "${APP_DIR}/lxc/upgrade.sh" "$@"
