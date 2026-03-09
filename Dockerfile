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
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Australia/Melbourne

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
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Create necessary directories
RUN mkdir -p /app /config /var/run/dbus

# Set working directory
WORKDIR /app

# Copy entrypoint separately so its layer is independent of Python code changes
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

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
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"WEB_PORT\",\"8080\")}/api/status')" || exit 1

# Run entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]
