FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

# Build-time system dependencies (needed to compile dbus-python, portaudio bindings,
# and PyAV on architectures without pre-built wheels)
RUN apt-get update && apt-get install -y \
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

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

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
RUN apt-get update && apt-get install -y \
    bluetooth \
    bluez \
    bluez-tools \
    alsa-utils \
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
    && rm -rf /var/lib/apt/lists/*

# Install S6 overlay (multi-arch aware)
ARG TARGETARCH
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
    chmod +x /etc/s6-overlay/s6-rc.d/sendspin/run && \
    chmod +x /etc/s6-overlay/s6-rc.d/sendspin/finish

# Copy application files
COPY sendspin_client.py web_interface.py config.py bluetooth_manager.py state.py ./
COPY routes/ routes/
COPY services/ services/
COPY scripts/ scripts/
COPY templates/ templates/
COPY static/ static/

# Expose web interface port
EXPOSE 8080

# Health check — verify only that the web UI is reachable (BT disconnected is normal)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"WEB_PORT\",\"8080\")}/api/health')" || exit 1

# S6 overlay manages process lifecycle (PID 1, signal forwarding, zombie reaping).
# The sendspin longrun service (rootfs/etc/s6-overlay/s6-rc.d/sendspin/run)
# calls /app/entrypoint.sh which handles D-Bus, audio, and app startup.
ENTRYPOINT ["/init"]
