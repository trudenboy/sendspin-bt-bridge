# Sendspin Docker Client

A Docker-based Sendspin client with Bluetooth speaker support and web-based configuration interface. Perfect for running on headless systems like Raspberry Pi or Home Assistant installations.

## Features

- ✅ **Sendspin Protocol**: Full support for Music Assistant's Sendspin protocol
- 🔊 **Bluetooth Audio**: Automatic connection and reconnection to Bluetooth speakers
- 🌐 **Web Interface**: Easy configuration and real-time status monitoring
- 🐳 **Docker Ready**: Fully containerized with minimal host dependencies
- 🔄 **Auto-reconnect**: Automatically handles Bluetooth speaker disconnections
- 📊 **Status Reporting**: Reports speaker state and container info to Music Assistant
- 🕐 **Timezone Support**: Configurable timezone for accurate scheduling

<img width="1233" height="657" alt="image" src="https://github.com/user-attachments/assets/d5f3f733-b073-4a08-b23a-feb3efc4daf7" />
<br><br>

<img width="1187" height="257" alt="image" src="https://github.com/user-attachments/assets/72080af9-819a-4f63-9f1f-81e87a469d03" />



## Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Bluetooth adapter on the host system (if using Bluetooth speakers)
- Music Assistant server running on your network

**Important**: If using Bluetooth speakers, they must be paired with the host system before starting the container. The container will use the host's Bluetooth adapter via D-Bus.

### Bluetooth Setup on Host

Ensure Bluetooth is enabled on the host:

```bash
# Check if Bluetooth is available
hciconfig

# Enable Bluetooth if needed
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Ensure your user (or the container) can access Bluetooth
sudo usermod -a -G bluetooth $USER
```

To pair a Bluetooth speaker on the host:
```bash
bluetoothctl
scan on
# Wait for your device to appear
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```


### Permissions

The container requires:
- `privileged: true` for Bluetooth access
- `network_mode: host` for mDNS discovery
- Access to `/var/run/dbus` for Bluetooth communication
- Access to `/dev/bus/usb` for USB Bluetooth adapters



### Installation

1. Create the configuration directory:
```bash
sudo mkdir -p /etc/docker/Sendspin
```

2. Customise Docker compose & deploy using pre-built image (Recommended)

```yaml
version: '3.8'

services:
  sendspin-client:
    image: ghcr.io/loryanstrant/sendspin-client:latest
    container_name: sendspin-client
    restart: unless-stopped
    network_mode: host
    privileged: true
    
    volumes:
      - /var/run/dbus:/var/run/dbus
      - /etc/docker/Sendspin:/config
    
    environment:
      - SENDSPIN_NAME=Living Room
      - SENDSPIN_SERVER=ma.local
      - SENDSPIN_PORT=9000
      - BLUETOOTH_MAC=04:57:91:D8:EC:9D
      - TZ=Australia/Melbourne
      - WEB_PORT=8080
    
    devices:
      - /dev/bus/usb:/dev/bus/usb
    
    cap_add:
      - NET_ADMIN
      - NET_RAW
      - SYS_ADMIN
```


3. Access the web interface:
```
http://your-host-ip:8080
```

## Configuration

The Sendspin client can also be configured through the web interface at `http://your-host:8080`.

### Configuration Options

- **Player Name**: The name that appears in Music Assistant (configured via web UI)
- **Server**: Use `auto` for automatic mDNS discovery, or specify a hostname/IP
- **Bluetooth MAC**: MAC address of your Bluetooth speaker (optional)
- **Timezone**: Your local timezone

**Note**: Configuration changes require a container restart to take effect.
### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `SENDSPIN_NAME` | Player name in Music Assistant | `Docker-{hostname}` | No |
| `SENDSPIN_SERVER` | Music Assistant server hostname/IP | `ma.strant.casa` | Yes |
| `SENDSPIN_PORT` | Sendspin server port | `9000` | Yes |
| `BLUETOOTH_MAC` | Bluetooth speaker MAC address | - | No* |
| `TZ` | Timezone (e.g., `Australia/Melbourne`) | `Australia/Melbourne` | No |
| `WEB_PORT` | Web interface port | `8080` | No |

*Leave empty to disable Bluetooth functionality


### Web Interface

The web interface provides:

- **Real-time Status**: View connection status, playback state, and system info
- **Configuration**: Change settings without editing files
- **Monitoring**: See when the speaker connects/disconnects
- **System Info**: View IP address, hostname, and uptime




## Troubleshooting

### Bluetooth Speaker Won't Connect

1. **Check Bluetooth availability**:
```bash
docker exec -it sendspin-client bluetoothctl
power on
scan on
```

2. **Pair manually if needed**:
```bash
docker exec -it sendspin-client bluetoothctl
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

3. **Check container logs**:
```bash
docker logs sendspin-client
```

### Can't Connect to Music Assistant

1. Verify the server address and port in your configuration
2. Ensure the container is on the same network (using `network_mode: host`)
3. Check Music Assistant logs for connection attempts
4. Verify firewall rules aren't blocking port 9000

### Web Interface Not Accessible

1. Check if the port is correct: `docker ps`
2. Ensure no other service is using port 8080
3. Try accessing via the host's IP: `http://192.168.1.x:8080`

### Container Restarts Frequently

1. Check logs: `docker logs sendspin-client`
2. Verify Bluetooth adapter is working: `hciconfig`
3. Check D-Bus is running: `ps aux | grep dbus`

## Architecture

```
┌─────────────────────────────────────┐
│     Music Assistant Server          │
│         (Sendspin Server)           │
└──────────────┬──────────────────────┘
               │ WebSocket (port 9000)
               │
┌──────────────▼──────────────────────┐
│     Sendspin Docker Client          │
│  ┌──────────────────────────────┐   │
│  │   Python Sendspin Client     │   │
│  │   - aiosendspin library      │   │
│  │   - Audio streaming          │   │
│  │   - Status reporting         │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │   Bluetooth Manager          │   │
│  │   - bluetoothctl interface   │   │
│  │   - Auto-reconnect logic     │   │
│  │   - Connection monitoring    │   │
│  └──────────────────────────────┘   │
│  ┌──────────────────────────────┐   │
│  │   Web Interface (Flask)      │   │
│  │   - Configuration UI         │   │
│  │   - Status dashboard         │   │
│  │   - Real-time monitoring     │   │
│  └──────────────────────────────┘   │
└──────────────┬──────────────────────┘
               │ Bluetooth
               │
┌──────────────▼──────────────────────┐
│      Bluetooth Speaker              │
└─────────────────────────────────────┘
```

## Development

### Building from Source

```bash
git clone https://github.com/loryanstrant/Sendspin-client.git
cd Sendspin-client
docker build -t sendspin-client .
```

### Running Tests

```bash
# Test Bluetooth connectivity
docker exec -it sendspin-client bluetoothctl info XX:XX:XX:XX:XX:XX

# Test Sendspin connection
docker logs -f sendspin-client
```

### Modifying the Code

The main components are:

- `sendspin_client.py`: Core Sendspin client with Bluetooth management
- `web_interface.py`: Flask web application for UI
- `entrypoint.sh`: Container startup script
- `Dockerfile`: Container build configuration
- `docker-compose.yml`: Docker Compose orchestration

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Development Approach
<img width="256" height="256" alt="image" src="https://github.com/user-attachments/assets/3e6903cf-2bfa-4f10-bc25-8bf3de5e2f3a" />


## License

MIT License - see LICENSE file for details

## Credits

- Built for [Music Assistant](https://www.music-assistant.io/)
- Uses [aiosendspin](https://github.com/Sendspin/aiosendspin) library
- Inspired by [sendspin-go](https://github.com/Sendspin/sendspin-go)

## Support

- **Issues**: [GitHub Issues](https://github.com/loryanstrant/Sendspin-client/issues)
- **Music Assistant**: [Discord](https://discord.gg/kaVm8hGpne)

## Changelog

### v1.0.0 (2026-01-01)

- Initial release
- Sendspin protocol support
- Bluetooth speaker management
- Web-based configuration interface
- Docker container with auto-reconnect
- GHCR image publishing
