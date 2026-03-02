FROM python:3.12-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

# Build-time system dependencies (needed to compile dbus-python and portaudio bindings)
RUN apt-get update && apt-get install -y \
    gcc \
    pkg-config \
    python3-dev \
    libdbus-1-dev \
    libdbus-glib-1-dev \
    libglib2.0-dev \
    libbluetooth-dev \
    portaudio19-dev \
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
    libglib2.0-0 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy compiled Python packages from builder
COPY --from=builder /install /usr/local

# Create necessary directories
RUN mkdir -p /app /config /var/run/dbus

# Set working directory
WORKDIR /app

# Copy application files
COPY sendspin_client.py web_interface.py config.py mpris.py bluetooth_manager.py entrypoint.sh ./
COPY templates/ templates/
COPY static/ static/

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose web interface port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3 -c "\
import urllib.request, json, sys; \
r = urllib.request.urlopen('http://localhost:8080/api/status'); \
d = json.loads(r.read()); \
devs = d.get('devices', []); \
sys.exit(0 if devs and any(dev.get('connected') for dev in devs) else 1)" || exit 1

# Run entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]
