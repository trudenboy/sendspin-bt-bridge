FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH
ARG TARGETVARIANT

# Build-time system dependencies (needed to compile dbus-python, portaudio bindings,
# and PyAV on architectures without pre-built wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    pkg-config \
    python3-dev \
    libdbus-1-dev \
    libdbus-glib-1-dev \
    libglib2.0-dev \
    libbluetooth-dev \
    portaudio19-dev \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libavfilter-dev \
    libswscale-dev \
    libswresample-dev \
    libjpeg-dev \
    zlib1g-dev \
    libtiff-dev \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: All dependencies except sendspin — cached across releases.
# SENDSPIN_VERSION is intentionally declared later so version bumps never
# invalidate this expensive layer (numpy/PyAV compile from source on armv7).
COPY requirements.txt /tmp/
RUN grep -v '^sendspin' /tmp/requirements.txt > /tmp/requirements-deps.txt && \
    if [ "${TARGETARCH}${TARGETVARIANT}" = "armv7" ]; then \
        pip install --no-cache-dir --prefer-binary --prefix=/install \
            --extra-index-url https://www.piwheels.org/simple \
            -r /tmp/requirements-deps.txt \
            "aiosendspin-mpris~=2.1.1" \
            "av>=15.0.0,<16.0.0" \
            "qrcode>=8.0" \
            "readchar>=4.0.0" \
            "rich>=13.0.0" \
            "sounddevice>=0.4.6"; \
    else \
        pip install --no-cache-dir --prefix=/install -r /tmp/requirements-deps.txt; \
    fi

# Layer 2: sendspin package only — lightweight, rebuilt each release.
# armv7 uses --no-deps because all transitive deps are explicit in layer 1;
# amd64/arm64 omit it so pip resolves transitive deps like aiosendspin-mpris.
ARG SENDSPIN_VERSION=""
RUN NO_DEPS="" && \
    if [ "${TARGETARCH}${TARGETVARIANT}" = "armv7" ]; then NO_DEPS="--no-deps"; fi && \
    if [ -n "${SENDSPIN_VERSION}" ]; then \
        pip install --no-cache-dir --prefix=/install ${NO_DEPS} "sendspin==${SENDSPIN_VERSION}"; \
    else \
        pip install --no-cache-dir --prefix=/install ${NO_DEPS} "sendspin>=5.3.0,<6.0.0"; \
    fi

# Strip bloat from installed packages before copying to runtime stage
RUN find /install -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name tests -path '*/numpy/*' -exec rm -rf {} + 2>/dev/null; \
    find /install -type d -name '*.dist-info' -exec sh -c 'rm -rf "$1"/RECORD "$1"/LICENSE* "$1"/NOTICE*' _ {} \; 2>/dev/null; \
    rm -rf /install/lib/python3.12/site-packages/pip \
           /install/lib/python3.12/site-packages/pygments \
           /install/bin/pip* \
           /install/bin/pygmentize; \
    # Strip debug symbols from native libraries (~20-40 MB savings)
    find /install \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded {} + 2>/dev/null; \
    true

# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# S6 overlay version
ARG S6_OVERLAY_VERSION=3.2.0.2

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
    libportaudio2 \
    dbus \
    libdbus-1-3 \
    libdbus-glib-1-2 \
    libglib2.0-0t64 \
    tzdata \
    xz-utils \
    curl \
    && if [ "${TARGETARCH}${TARGETVARIANT}" = "armv7" ]; then \
        apt-get install -y --no-install-recommends \
            libavcodec61 libavdevice61 libavfilter10 libavformat61 \
            libavutil59 libswresample5 libswscale8; \
    else \
        # On amd64/arm64 PyAV bundles its own FFmpeg in av.libs/.
        # Remove transitive FFmpeg/GStreamer/codec deps pulled by pulseaudio
        # (~107 MB) — pactl/paplay work fine without them.
        # Keep libasound2-plugins — it provides the ALSA→PulseAudio bridge
        # (libasound_module_pcm_pulse.so) required by sounddevice/PortAudio.
        dpkg --force-depends -r \
            iso-codes \
            libavcodec61 libavfilter10 libavformat61 libavdevice61 \
            libavutil59 libswresample5 libswscale8 \
            libx265-215 libx264-164 libaom3 libsvtav1enc2 \
            libdav1d7 libvpx9 librsvg2-2 libcodec2-1.2 \
            libgstreamer-plugins-base1.0-0 \
            2>/dev/null || true; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Strip unused Python stdlib modules + runtime cruft.
# - pip: builder stage strips it from /install, but the python:3.12-slim base
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
RUN rm -rf /usr/local/lib/python3.12/ensurepip \
           /usr/local/lib/python3.12/idlelib \
           /usr/local/lib/python3.12/lib2to3 \
           /usr/local/lib/python3.12/pydoc_data \
           /usr/local/lib/python3.12/turtledemo \
           /usr/local/lib/python3.12/turtle.py \
           /usr/local/lib/python3.12/test \
           /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
           /usr/lib/udev/hwdb.bin /usr/lib/udev/hwdb.d \
           /usr/lib/systemd \
           /usr/local/lib/python3.12/site-packages/pulsectl/tests \
           /usr/local/lib/python3.12/site-packages/qrcode/tests \
           /usr/local/lib/python3.12/site-packages/numpy/doc \
    && find /usr/share/doc -mindepth 1 -delete 2>/dev/null \
    && find /usr/share/man -mindepth 1 -delete 2>/dev/null \
    && find /usr/share/info -mindepth 1 -delete 2>/dev/null \
    && find /usr/local/lib/python3.12 -name __pycache__ -exec rm -rf {} + 2>/dev/null; true

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

# Copy application files
COPY *.py ./
COPY routes/ routes/
COPY services/ services/
# scripts/ is intentionally narrowed to runtime + CI smoke-test entrypoints:
#   translate_ha_config.py   — called by entrypoint.sh when /data/options.json exists (HA addon mode)
#   check_sendspin_compat.py — invoked inside the image by release.yml post-build
#   check_container_runtime.py — invoked inside the image by release.yml post-build
# Dev tooling (proxmox-vm-*, rpi-*, generate_ha_addon_variants, release_notes,
# translate_landing) runs on the host and has no business in the image.
COPY scripts/translate_ha_config.py scripts/check_sendspin_compat.py scripts/check_container_runtime.py scripts/
COPY templates/ templates/
COPY static/ static/

# GitHub App private key for bug report proxy (base64-encoded PEM)
ARG BUGREPORTER_PRIVATE_KEY=""
ENV GITHUB_APP_PRIVATE_KEY=${BUGREPORTER_PRIVATE_KEY}

# Expose web interface port
EXPOSE 8080

# Health check — hit /api/health directly via curl (no Python import; survives src-layout move)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS "http://localhost:${WEB_PORT:-8080}/api/health" >/dev/null || exit 1

# S6 init wrapper — /init permissions are set at build time (line 94).
RUN printf '#!/bin/sh\nexec /init "$@"\n' > /s6-init && \
    chmod +x /s6-init

# S6 overlay manages process lifecycle (PID 1, signal forwarding, zombie reaping).
# The sendspin longrun service (rootfs/etc/s6-overlay/s6-rc.d/sendspin/run)
# calls /app/entrypoint.sh which handles D-Bus, audio, and app startup.
ENTRYPOINT ["/s6-init"]
