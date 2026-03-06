# Sendspin BT Bridge — OpenWrt LXC Deployment

Deploy Sendspin BT Bridge as a native LXC container on OpenWrt-based devices — no Docker required.

## Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 768 MB | 1 GB+ |
| Storage | 2 GB free | 4 GB+ |
| USB | 1 free USB port | — |
| Bluetooth | USB BT 4.0+ adapter | CSR 4.0 or equivalent |

**Tested on:** Turris Omnia (TurrisOS 9.x), OpenWrt 23.x on x86

**Supported architectures:** armv7l (armhf), aarch64 (arm64), x86_64 (amd64)

## Architecture

The LXC container **cannot** run its own `bluetoothd` (AF_BLUETOOTH is not available in LXC network namespaces). Instead:

- `bluetoothd` runs on the **OpenWrt host**
- The host's D-Bus socket is bind-mounted into the container at `/bt-dbus`
- `pulseaudio --system` runs inside the container and connects to BlueZ via D-Bus
- `btctl` wrapper inside the container routes `bluetoothctl` commands through the host's D-Bus

```
┌── OpenWrt Host ──────────────────┐
│  bluetoothd ← USB BT adapter    │
│  D-Bus (/var/run/dbus)           │
│     ↕ bind-mount                 │
│  ┌── LXC Container ───────────┐ │
│  │  /bt-dbus ← host D-Bus     │ │
│  │  pulseaudio --system        │ │
│  │  sendspin_client.py         │ │
│  │  Web UI :8080               │ │
│  └─────────────────────────────┘ │
└──────────────────────────────────┘
```

## Quick Install

### One-liner (on OpenWrt host as root)

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

### Download and review first (recommended)

```sh
wget https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh
less create.sh
sh create.sh
```

### Non-interactive

```sh
sh create.sh --name sendspin --ip 192.168.1.200/24 --yes
```

The script will:
1. Install LXC and Bluetooth packages on the host via `opkg`
2. Load and persist Bluetooth kernel modules
3. Create an Ubuntu 24.04 LXC container
4. Configure D-Bus bridge, cgroup rules, and autostart
5. Run `install.sh` inside the container (installs app + dependencies)
6. Install a procd init.d script for container autostart

## Manual Install

If you prefer to set up everything manually:

### 1. Install packages on the host

```sh
opkg update
opkg install lxc lxc-attach lxc-create lxc-start lxc-stop lxc-info lxc-ls \
             lxc-common lxc-hooks lxc-templates lxc-init liblxc
opkg install kmod-bluetooth bluez-daemon bluez-utils dbus dbus-utils
```

### 2. Load Bluetooth kernel modules

```sh
modprobe bluetooth btusb
echo bluetooth > /etc/modules.d/99-bluetooth
echo btusb > /etc/modules.d/99-btusb
```

### 3. Start services

```sh
/etc/init.d/dbus enable && /etc/init.d/dbus start
/etc/init.d/bluetoothd enable && /etc/init.d/bluetoothd start
```

### 4. Install D-Bus policy

```sh
wget -q https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/dbus-pulseaudio.conf \
     -O /etc/dbus-1/system.d/pulseaudio-lxc.conf
/etc/init.d/dbus reload
```

### 5. Create container

```sh
# Detect architecture
ARCH=$(uname -m)  # armv7l, aarch64, or x86_64

lxc-create -t download -n sendspin -- \
    --dist ubuntu --release noble --arch "$ARCH" \
    --server images.linuxcontainers.org
```

### 6. Configure container

Edit the container config (path varies: `/srv/lxc/sendspin/config` or `/var/lib/lxc/sendspin/config`):

```ini
# Append to existing config:

# Network
lxc.net.0.type = veth
lxc.net.0.link = br-lan
lxc.net.0.flags = up
lxc.net.0.name = eth0

# D-Bus mount (adjust path: use readlink -f /var/run/dbus to find real path)
lxc.mount.entry = /tmp/run/dbus bt-dbus none bind,create=dir 0 0

# cgroup device access
lxc.cgroup2.devices.allow = c 13:* rwm
lxc.cgroup2.devices.allow = c 108:* rwm
lxc.cgroup2.devices.allow = c 189:* rwm

# Autostart
lxc.start.auto = 1
lxc.start.delay = 5
```

> **Important:** Do NOT add `lxc.apparmor.profile` — AppArmor is not supported on OpenWrt.

### 7. Start container and run installer

```sh
lxc-start -n sendspin
lxc-attach -n sendspin -- bash -c \
    "apt-get update && apt-get install -y wget && \
     wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh | bash"
```

## Bluetooth Speaker Pairing

```sh
# Enter the container
lxc-attach -n sendspin

# Use btctl (routes to host's bluetoothd via D-Bus bridge)
btctl
> scan on
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> connect XX:XX:XX:XX:XX:XX
> quit
```

Set `BLUETOOTH_MAC` via the web UI at `http://<container-ip>:8080` and restart:

```sh
lxc-attach -n sendspin -- systemctl restart sendspin-client
```

## Updating

```sh
lxc-attach -n sendspin -- bash -c \
    "wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/upgrade.sh | bash"
```

## Monitoring

```sh
# View logs
lxc-attach -n sendspin -- journalctl -u sendspin-client -f

# Service status
lxc-attach -n sendspin -- systemctl status sendspin-client pulseaudio-system --no-pager

# Audio sinks
lxc-attach -n sendspin -- pactl list sinks short

# Bluetooth (via host D-Bus)
lxc-attach -n sendspin -- btctl show

# Container status (from host)
lxc-info -n sendspin
```

## Known Issues

### `av` package on armv7l

The `sendspin` Python package requires `av>=14.0.0`, but av 14+ fails to compile on armv7l due to a missing `AV_HWDEVICE_TYPE_D3D12VA` constant in Ubuntu 24.04's ffmpeg 6.1.

**Workaround (handled automatically by `install.sh`):**
- `av==12.3.0` is installed instead (last armhf-compatible version)
- `sendspin` is installed with `--no-deps` to skip the av>=14 requirement

### DNS resolution on some OpenWrt devices

Some OpenWrt builds have trouble resolving package repository hostnames. If `opkg update` fails:

```sh
# Add the repo IP to /etc/hosts
echo "151.101.2.132 downloads.openwrt.org" >> /etc/hosts
```

### D-Bus socket path

OpenWrt symlinks `/var/run` → `/tmp/run`. The LXC mount entry must use the **real** path:

```sh
readlink -f /var/run/dbus
# Output: /tmp/run/dbus  ← use this in lxc.mount.entry
```

### Python package compilation time

On ARM devices, some Python packages compile from source (numpy, dbus-fast). This can take 15-30 minutes on an ARM Cortex-A9.

## Files

| File | Purpose |
|------|---------|
| `create.sh` | Host-side installer: installs LXC+BT, creates container, runs install.sh |
| `sendspin-lxc.init` | procd init.d script for container autostart on OpenWrt |
| `dbus-pulseaudio.conf` | D-Bus policy allowing container PA to access host BlueZ |
