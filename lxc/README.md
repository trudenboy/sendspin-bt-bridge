# Sendspin Client — Proxmox VE LXC Deployment

Run Sendspin Client as a **native LXC container** on Proxmox VE — no Docker required.

## Architecture

The LXC container **cannot** run its own `bluetoothd` due to `AF_BLUETOOTH` kernel namespace restrictions in LXC. Instead:

- `bluetoothd` runs on the **Proxmox host**
- The host's D-Bus socket (`/run/dbus`) is bind-mounted into the container at `/bt-dbus`
- `pulseaudio --system` runs inside the container and connects to the host's BlueZ via this D-Bus bridge
- `btctl` (installed wrapper) invokes `bluetoothctl` pointed at the correct `DBUS_SYSTEM_BUS_ADDRESS`

## Docker vs LXC comparison

| Feature | Docker | LXC (Proxmox) |
|---------|--------|---------------|
| Deployment target | Any Docker host | Proxmox VE 7/8 |
| Bluetooth | Uses host's bluetoothd via D-Bus socket | Uses host's bluetoothd via D-Bus socket (`/bt-dbus`) |
| Audio | Uses host's PulseAudio/PipeWire socket | Own `pulseaudio --system` inside container |
| mDNS discovery | Uses host's avahi-daemon | Own avahi-daemon inside container |
| Config changes | Container restart | `systemctl restart sendspin-client` |
| USB BT adapter | Host passthrough | cgroup passthrough to LXC |

## Prerequisites

- Proxmox VE 7 or 8
- USB Bluetooth adapter (or onboard Bluetooth on the Proxmox host)
- **Ubuntu 24.04** LXC template available in Proxmox (the script downloads it automatically)

## Quick Install

### Option 1: One-line (on Proxmox host as root)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/loryanstrant/sendspin-client/main/lxc/proxmox-create.sh)
```

### Option 2: Download and review first (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/loryanstrant/sendspin-client/main/lxc/proxmox-create.sh -o proxmox-create.sh
# Review the script before running:
less proxmox-create.sh
bash proxmox-create.sh
```

The script interactively prompts for container ID, hostname, RAM, disk, network, and USB Bluetooth passthrough options.

## Manual Install (via Proxmox Web UI)

If you prefer to create the container via the Proxmox web UI:

1. Create a new **privileged** LXC container (**Ubuntu 24.04**, 512 MB RAM, 4 GB disk)
2. Start the container and open a shell (`pct enter <CTID>`)
3. Run the installer:

   **Option A — one-liner:**
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/loryanstrant/sendspin-client/main/lxc/install.sh)
   ```

   **Option B — download and review:**
   ```bash
   curl -fsSL https://raw.githubusercontent.com/loryanstrant/sendspin-client/main/lxc/install.sh -o install.sh
   less install.sh
   bash install.sh
   ```

4. Append the following to `/etc/pve/lxc/<CTID>.conf` on the **Proxmox host**:
   ```
   # AppArmor: unconfined (required for bluetoothd management socket in LXC)
   lxc.apparmor.profile: unconfined
   # Bluetooth HCI devices
   lxc.cgroup2.devices.allow: c 166:* rwm
   # Input devices
   lxc.cgroup2.devices.allow: c 13:* rwm
   # rfkill device (required by bluetoothd)
   lxc.cgroup2.devices.allow: c 10:232 rwm
   # Host D-Bus socket — gives PulseAudio and btctl access to the host's bluetoothd
   lxc.mount.entry: /run/dbus bt-dbus none bind,create=dir 0 0
   # USB devices (if using a USB Bluetooth adapter — grants access to all USB devices)
   lxc.cgroup2.devices.allow: c 189:* rwm
   ```

5. Restart the container: `pct restart <CTID>`

## Bluetooth Speaker Pairing

Bluetooth runs on the **Proxmox host**. The `btctl` wrapper inside the container routes commands to the host's `bluetoothd` via the D-Bus bridge.

```bash
# Enter the container
pct enter <CTID>

# Start interactive Bluetooth manager (uses host's bluetoothd via D-Bus bridge)
btctl

# Inside btctl:
power on
scan on
# Wait for your speaker to appear, then:
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```

Then set `BLUETOOTH_MAC` in `/config/config.json` and restart the service:

```bash
systemctl restart sendspin-client
```

## Monitoring

```bash
# View application logs
pct exec <CTID> -- journalctl -u sendspin-client -f

# Check service statuses
# Note: bluetooth.service is intentionally disabled inside the container
pct exec <CTID> -- systemctl status sendspin-client pulseaudio-system avahi-daemon --no-pager

# List audio sinks (confirm Bluetooth sink is present)
pct exec <CTID> -- pactl list sinks short

# Check Bluetooth adapter (via host D-Bus bridge)
pct exec <CTID> -- btctl show

# Verify PulseAudio socket
pct exec <CTID> -- ls -la /var/run/pulse/native
```

## Manual USB Bluetooth Passthrough

To pass through a specific USB Bluetooth adapter, find its device numbers on the Proxmox host:

```bash
lsusb | grep -i bluetooth
# Example output: Bus 001 Device 003: ID 0a12:0001 Cambridge Silicon Radio, Ltd Bluetooth Dongle

# Map Bus 001 Device 003 → /dev/bus/usb/001/003
```

Then add to `/etc/pve/lxc/<CTID>.conf`:
```
lxc.cgroup2.devices.allow: c 189:* rwm
lxc.mount.entry: /dev/bus/usb dev/bus/usb none bind,optional,create=dir 0 0
```

> **Note:** `c 189:* rwm` grants access to **all** USB devices in the LXC.
> For tighter control, identify the specific USB device bus/device number with `lsusb` and restrict accordingly.

## Notes

- `bluetooth.service` is intentionally **disabled** inside the container — the host's `bluetoothd` is used instead
- `btctl` is a wrapper for `bluetoothctl` that sets `DBUS_SYSTEM_BUS_ADDRESS` to the bind-mounted host socket at `/bt-dbus`
- Config changes in `/config/config.json` take effect after `systemctl restart sendspin-client` (no container restart needed)
- The privileged container and `lxc.apparmor.profile: unconfined` are required for Bluetooth hardware passthrough; hardening further would require upstream PVE support for AppArmor profiles that permit Bluetooth management sockets
