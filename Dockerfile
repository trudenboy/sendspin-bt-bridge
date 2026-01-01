FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Australia/Melbourne

# Install system dependencies for Bluetooth and audio
RUN apt-get update && apt-get install -y \
    bluetooth \
    bluez \
    bluez-tools \
    libbluetooth-dev \
    alsa-utils \
    pulseaudio \
    pulseaudio-module-bluetooth \
    portaudio19-dev \
    libportaudio2 \
    dbus \
    libdbus-1-dev \
    libdbus-glib-1-dev \
    libglib2.0-dev \
    pkg-config \
    gcc \
    python3-dev \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create necessary directories
RUN mkdir -p /app /config /var/run/dbus

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY sendspin_client.py .
COPY web_interface.py .
COPY entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose web interface port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')" || exit 1

# Run entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]
