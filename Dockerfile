# UV_STAGE selects which uv source stage feeds the builder's COPY
# below. Default works for amd64/arm64; the armv7 release job
# overrides this to ``uv-armv7``. The ARG must precede every FROM that
# references it — BuildKit requires the global form here.
ARG UV_STAGE=uv-default

# uv source stages.
#
# Default — pull the static binary directly from the upstream image.
# This is what amd64/arm64 builds use; the COPY --from below short-
# circuits to a single layer copy (~50 MB pull amortised across builds).
FROM ghcr.io/astral-sh/uv:0.12.9 AS uv-default

# armv7 fallback — the upstream image has no linux/arm/v7 manifest, so install
# the same pinned uv version from its PyPI armv7 wheel instead.
# Selected by passing --build-arg UV_STAGE=uv-armv7 in the release workflow.
FROM python:3.13-slim AS uv-armv7
RUN pip install --no-cache-dir --root-user-action=ignore "uv==0.12.5" && \
    cp "$(command -v uv)" /uv

FROM ${UV_STAGE} AS uv-source

FROM python:3.13-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH
ARG TARGETVARIANT

# uv: single static binary, ~10–20× faster resolve/install than pip.
# Pinned to a specific minor for reproducibility; bump deliberately.
# Source stage is chosen above based on the target platform.
COPY --from=uv-source /uv /usr/local/bin/uv

# Build-time system dependencies (PyGObject / cairo; dbus-fast on armv7)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    pkg-config \
    python3-dev \
    libcairo2-dev \
    libgirepository-2.0-dev \
    libjpeg-dev \
    zlib1g-dev \
    libtiff-dev \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: Python dependencies from the exported pinset.
COPY requirements.txt /tmp/
# `--no-binary` whitelist for armv7: these ship no `linux_armv7l` wheel
# anywhere (PyPI nor piwheels) so under `--only-binary :all:` the resolver
# would error out. PyGObject is built from source against the GIR in this
# stage. Lift entries when upstream starts shipping armv7 wheels.
RUN if [ "${TARGETARCH}${TARGETVARIANT}" = "armv7" ]; then \
        uv pip install --system --no-cache --prefix=/install \
            --only-binary :all: \
            --no-binary dbus-fast \
            --no-binary mpris-api \
            --no-binary pyric \
            --no-binary pygobject \
            --no-binary pycairo \
            --extra-index-url https://www.piwheels.org/simple \
            --index-strategy unsafe-best-match \
            -r /tmp/requirements.txt; \
    else \
        uv pip install --system --no-cache --prefix=/install -r /tmp/requirements.txt; \
    fi

# Layer 3: the bridge package itself (sendspin_bridge). Install into the same
# /install prefix the runtime stage will pick up via `COPY --from=builder`.
# --no-deps because everything is already in layer 1.
COPY src/ /build/src/
COPY pyproject.toml VERSION /build/
RUN uv pip install --system --no-cache --no-deps --prefix=/install /build

# Strip bloat from installed packages before copying to runtime stage
RUN find /install -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name tests -path '*/numpy/*' -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name '*.dist-info' -exec sh -c 'rm -rf "$1"/RECORD "$1"/LICENSE* "$1"/NOTICE*' _ {} \; 2>/dev/null; \
    rm -rf /install/lib/python3.13/site-packages/pip \
           /install/lib/python3.13/site-packages/pygments \
           /install/bin/pip* \
           /install/bin/pygmentize; \
    # Strip debug symbols from native libraries (~20-40 MB savings)
    find /install \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded {} + 2>/dev/null; \
    true

# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

# S6 overlay version
ARG S6_OVERLAY_VERSION=3.2.3.2

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Australia/Melbourne \
    S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=30000

# Runtime system dependencies only (no build tools)
ARG TARGETARCH
ARG TARGETVARIANT
RUN apt-get update && apt-get install -y --no-install-recommends \
    bluetooth \
    bluez \
    bluez-tools \
    alsa-utils \
    gosu \
    pulseaudio \
    pulseaudio-module-bluetooth \
    gstreamer1.0-plugins-base \
    gstreamer1.0-pulseaudio \
    gir1.2-gstreamer-1.0 \
    libcairo2 \
    libgirepository-2.0-0 \
    dbus \
    libdbus-1-3 \
    libdbus-glib-1-2 \
    libglib2.0-0t64 \
    tzdata \
    xz-utils \
    curl \
    && if [ "${TARGETARCH}${TARGETVARIANT}" = "armv7" ]; then \
        apt-get install -y --no-install-recommends \
            libjpeg62-turbo libpng16-16t64 libtiff6 libwebp7 libfreetype6; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Strip unused Python stdlib modules + runtime cruft.
# - pip: builder stage strips it from /install, but the python:3.13-slim base
#   image ships its own pip in /usr/local — the COPY --from=builder /install
#   merges over that, leaving the base image's pip behind.  Remove it here.
# - /usr/lib/udev/hwdb.{bin,d}: 22 MB of hardware database for udev — we do
#   not run udevd inside the container (BlueZ/PulseAudio talk to the host's
#   udev via D-Bus), so the local copy never gets queried.
# - /usr/lib/systemd: ~5 MB of systemd unit files / utilities — s6-overlay
#   handles PID 1 / signal forwarding, systemd is unreachable inside the
#   container.
# - /usr/share/doc, /usr/share/man, /usr/share/info: package documentation
#   pulled in by apt-installed runtime deps; no consumer at runtime.
# - tests/ inside pulled wheels: qrcode and pulsectl ship test suites.
RUN rm -rf /usr/local/lib/python3.13/ensurepip \
           /usr/local/lib/python3.13/idlelib \
           /usr/local/lib/python3.13/lib2to3 \
           /usr/local/lib/python3.13/pydoc_data \
           /usr/local/lib/python3.13/turtledemo \
           /usr/local/lib/python3.13/turtle.py \
           /usr/local/lib/python3.13/test \
           /usr/local/lib/python3.13/site-packages/pip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
           /usr/lib/udev/hwdb.bin /usr/lib/udev/hwdb.d \
           /usr/lib/systemd \
           /usr/local/lib/python3.13/site-packages/pulsectl/tests \
           /usr/local/lib/python3.13/site-packages/qrcode/tests \
           /usr/local/lib/python3.13/site-packages/numpy/doc \
    && find /usr/share/doc -mindepth 1 -delete 2>/dev/null \
    && find /usr/share/man -mindepth 1 -delete 2>/dev/null \
    && find /usr/share/info -mindepth 1 -delete 2>/dev/null \
    && find /usr/local/lib/python3.13 -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

# Install S6 overlay (multi-arch aware)
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN S6_ARCH="" && \
    case "${TARGETARCH}" in \
        amd64)  S6_ARCH="x86_64" ;; \
        arm64)  S6_ARCH="aarch64" ;; \
        arm*)   S6_ARCH="armhf" ;; \
        *)      echo "Unsupported arch: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    curl -sSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
        | tar -Jxpf - -C / && \
    curl -sSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz" \
        | tar -Jxpf - -C /

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Create necessary directories
RUN mkdir -p /app /config /var/run/dbus

# Set working directory
WORKDIR /app

# Copy S6 overlay service definitions
COPY rootfs/ /

# Copy entrypoint separately so its layer is independent of Python code changes
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh && \
    chmod +x /init && \
    chmod +x /etc/s6-overlay/s6-rc.d/sendspin/run && \
    chmod +x /etc/s6-overlay/s6-rc.d/sendspin/finish

# sendspin_bridge package itself was installed by the builder stage and
# arrived via `COPY --from=builder /install /usr/local`. We only need the
# raw VERSION file at /app/ for entrypoint.sh's version-printer fallback.
COPY VERSION /app/
# scripts/ is intentionally narrowed to runtime + CI smoke-test entrypoints:
#   translate_ha_config.py   — called by entrypoint.sh when /data/options.json exists (HA addon mode)
#   check_sendspin_compat.py — invoked inside the image by release.yml post-build
#   check_container_runtime.py — invoked inside the image by release.yml post-build
# Dev tooling (proxmox-vm-*, rpi-*, generate_ha_addon_variants, release_notes,
# translate_landing) runs on the host and has no business in the image.
COPY scripts/translate_ha_config.py scripts/check_sendspin_compat.py scripts/check_container_runtime.py scripts/
# Templates / static / config schema travel inside src/sendspin_bridge/ via the editable install above.

# The release workflow intentionally supplies this GitHub App key at build time.
# hadolint ignore=DL3064
ARG BUGREPORTER_PRIVATE_KEY=""
# hadolint ignore=DL3064
ENV GITHUB_APP_PRIVATE_KEY=${BUGREPORTER_PRIVATE_KEY}

# Expose web interface port
EXPOSE 8080

# Health check — shell expansion reads the actual startup-bound port.
# hadolint ignore=DL3025
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD port=$(cat /tmp/sendspin-web-port 2>/dev/null || echo "${WEB_PORT:-8080}") && \
        curl -fsS "http://localhost:${port}/api/health" >/dev/null || exit 1

# S6 init wrapper — /init permissions are set at build time (line 94).
RUN printf '#!/bin/sh\nexec /init "$@"\n' > /s6-init && \
    chmod +x /s6-init

# S6 overlay manages process lifecycle (PID 1, signal forwarding, zombie reaping).
# The sendspin longrun service (rootfs/etc/s6-overlay/s6-rc.d/sendspin/run)
# calls /app/entrypoint.sh which handles D-Bus, audio, and app startup.
ENTRYPOINT ["/s6-init"]
