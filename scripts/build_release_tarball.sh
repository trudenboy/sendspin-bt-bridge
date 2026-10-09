#!/usr/bin/env bash
# Build the release source tarball LXC installs download: the tagged tree
# plus the compiled web UI in src/sendspin_bridge/spa (LXC hosts have no
# Node toolchain, so the SPA must arrive pre-built).
#
#   scripts/build_release_tarball.sh <tag> <version>   → sendspin-bt-bridge-<version>.tar.gz
set -euo pipefail

TAG="$1"
VERSION="$2"
PREFIX="sendspin-bt-bridge-${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

git archive --format=tar --prefix="${PREFIX}/" "${TAG}" | tar -x -C "${WORK}"
(
  cd "${WORK}/${PREFIX}/ui"
  npm ci --no-audit --no-fund
  npm run build
)
mkdir -p "${WORK}/${PREFIX}/src/sendspin_bridge/spa"
cp -a "${WORK}/${PREFIX}/ui/dist/." "${WORK}/${PREFIX}/src/sendspin_bridge/spa/"
rm -rf "${WORK}/${PREFIX}/ui/node_modules" "${WORK}/${PREFIX}/ui/dist"

tar -czf "${PREFIX}.tar.gz" -C "${WORK}" "${PREFIX}"
echo "${PREFIX}.tar.gz"
