---
title: Test Stand
description: Reference deployment topology with hardware specifications, software versions, and network layout
---

Reference deployment used for development and testing of Sendspin BT Bridge v2.12.2.

## Physical topology

```mermaid
graph TB
    subgraph net["LAN 192.168.10.0/24"]
        direction TB

        subgraph turris["Turris Omnia — Marvell Armada 385 ARMv7 / 2 GB RAM"]
            direction TB
            T_HOST["TurrisOS 9.0.4 / OpenWrt<br/>turris.my.lan<br/>router + DHCP + DNS"]
            T_USB["USB: CSR8510 A10<br/>0a12:0001"]
            subgraph T_LXC["LXC sendspin — Ubuntu 24.04 armv7l — turris-lxc.my.lan"]
                T_DBUS["D-Bus system bus<br/>bind-mount from host"]
                T_PA["PulseAudio 16.1 --system<br/>user pulse uid=109"]
                T_BLUEZ["BlueZ 5.72<br/>bluetoothctl"]
                T_SBB["SBB v2.12.2<br/>Python 3.12.3<br/>aiosendspin 4.3.2"]
                T_WEB["Flask 3.1.3 + Waitress 3.0.2<br/>:8080"]
                T_DBUS --> T_BLUEZ
                T_DBUS --> T_PA
                T_PA --> T_SBB
                T_BLUEZ --> T_SBB
                T_SBB --> T_WEB
            end
            T_HOST --> T_USB
            T_USB -.->|D-Bus passthrough| T_DBUS
        end

        subgraph proxmox["HP ProLiant MicroServer Gen8 — Celeron G1610T 2.3 GHz / 16 GB RAM"]
            direction TB
            P_HOST["Proxmox VE 8.4.16<br/>Debian 12 Bookworm<br/>Kernel 6.8.12-18-pve<br/>proxmox.my.lan"]
            P_USB1["USB Bus 4: CSR8510 A10<br/>PVE mapping: Audio"]
            P_USB2["USB Bus 2: CSR8510 A10<br/>PVE mapping: aTick"]
            P_ZIG["USB: SONOFF Zigbee 3.0<br/>1a86:55d4"]

            subgraph P_VM["VM 104 haos — QEMU/KVM — 2 vCPU / 6 GB RAM / 64 GB disk"]
                P_HAOS["Home Assistant OS<br/>haos.my.lan"]
                P_ADDON["SBB v2.12.2 addon<br/>85b1ecde-sendspin-bt-bridge<br/>3 devices / sync group"]
                P_HAOS --> P_ADDON
            end

            subgraph P_CT["CT 101 sendspin — LXC — 2 vCPU / 1 GB RAM / 8 GB disk"]
                P_DBUS["D-Bus system bus"]
                P_PA["PulseAudio 16.1"]
                P_BLUEZ2["BlueZ 5.72"]
                P_SBB["SBB v2.12.2<br/>Ubuntu 24.04 x86_64<br/>Python 3.12.3"]
                P_WEB2["Flask 3.1.3 + Waitress 3.0.2<br/>:8080"]
                P_DBUS --> P_BLUEZ2
                P_DBUS --> P_PA
                P_PA --> P_SBB
                P_BLUEZ2 --> P_SBB
                P_SBB --> P_WEB2
            end

            P_HOST --> P_USB1
            P_HOST --> P_USB2
            P_HOST --> P_ZIG
            P_USB1 -->|USB passthrough| P_VM
            P_USB2 -.->|D-Bus passthrough| P_DBUS
            P_ZIG -->|USB passthrough| P_VM
        end

        subgraph ma_box["Music Assistant Server"]
            MA["MA haos.my.lan:8095<br/>Sendspin protocol :9000<br/>mDNS auto-discovery"]
        end
    end

    P_ADDON -->|"WS :8928 :8929 :8932"| MA
    P_SBB -->|"WS :8928"| MA
    T_SBB -->|"WS :8928"| MA

    style net fill:none,stroke:#666
    style turris fill:#1a3a1a,stroke:#4a4
    style proxmox fill:#1a1a3a,stroke:#44a
    style ma_box fill:#3a1a1a,stroke:#a44
    style P_VM fill:#2a2a1a,stroke:#aa4
    style P_CT fill:#1a2a2a,stroke:#4aa
    style T_LXC fill:#1a2a2a,stroke:#4aa
```

## Audio routing

```mermaid
graph LR
    subgraph haos_bridge["HAOS Addon — hci0 C0:FB:F9:62:D6:9D"]
        direction TB
        H_MA["MA :9000"] -->|FLAC 44100/16/2| H_D1["daemon :8928<br/>PULSE_SINK=bluez_sink<br/>.FC_58_FA_EB_08_6C<br/>.a2dp_sink"]
        H_MA -->|FLAC 44100/16/2| H_D2["daemon :8929<br/>PULSE_SINK=bluez_sink<br/>.2C_D2_6B_B8_EC_5B<br/>.a2dp_sink"]
        H_MA -->|FLAC 44100/16/2| H_D3["daemon :8932<br/>PULSE_SINK=bluez_sink<br/>.30_21_0E_0A_AE_5A<br/>.a2dp_sink"]
    end

    subgraph proxmox_bridge["Proxmox LXC — hci0 00:15:83:FF:8F:2B"]
        P_MA["MA :9000"] -->|FLAC 44100/16/2| P_D1["daemon :8928<br/>PULSE_SINK=bluez_sink<br/>.6C_5C_3D_35_17_99<br/>.a2dp_sink"]
    end

    subgraph turris_bridge["Turris LXC — hci0 C0:FB:F9:62:D7:D6"]
        T_MA["MA :9000"] -->|FLAC 44100/16/2| T_D1["daemon :8928<br/>PULSE_SINK=bluez_sink<br/>.20_74_CF_61_FB_D8<br/>.a2dp_sink"]
    end

    H_D1 -->|"A2DP / -500ms"| S1["ENEBY20<br/>58%"]
    H_D2 -->|"A2DP / -500ms"| S2["Yandex mini<br/>52%"]
    H_D3 -->|"A2DP / -500ms"| S3["Lenco LS-500<br/>52%"]
    P_D1 -->|"A2DP / -900ms"| S4["ENEBY Portable<br/>59%"]
    T_D1 -->|"A2DP / -500ms"| S5["AfterShokz<br/>67%"]

    style haos_bridge fill:#2a2a1a,stroke:#aa4
    style proxmox_bridge fill:#1a2a2a,stroke:#4aa
    style turris_bridge fill:#1a3a1a,stroke:#4a4
```

## MA player registry

```mermaid
graph TB
    subgraph ma_players["Music Assistant — 9 registered players"]
        direction TB

        subgraph sync_group["Sync Group: Sendspin BT"]
            SG["whole-home multiroom"]
        end

        subgraph bt_players["Sendspin BT Bridge players"]
            direction LR
            P1["ENEBY20 @ HAOS<br/>host: 85b1ecde"]
            P2["Yandex mini @ HAOS<br/>host: 85b1ecde"]
            P3["Lenco LS-500 @ HAOS<br/>host: 85b1ecde"]
            P4["ENEBY Portable @ LXC<br/>host: sendspin"]
            P5["AfterShokz @ OpenWRT<br/>host: ubuntu"]
        end

        subgraph other_players["Other players"]
            direction LR
            P6["MacBook Pro<br/>Sendspin desktop"]
            P7["Web Chrome x2<br/>browser players"]
        end

        SG ---|member| P1
        SG ---|member| P2
        SG ---|member| P3
        SG ---|member| P4
        SG ---|member| P5
    end

    style ma_players fill:none,stroke:#666
    style sync_group fill:#3a1a1a,stroke:#a44
    style bt_players fill:#1a1a3a,stroke:#44a
    style other_players fill:#1a1a1a,stroke:#666
```

## Bridge instances

### 1. HAOS Addon — `haos.my.lan:8080`

Runs as a Home Assistant addon inside HAOS VM on Proxmox.

| Parameter | Value |
|-----------|-------|
| **Host** | Proxmox VE 8.4.16, VM 104 (HAOS), 2 cores, 6 GB RAM |
| **Platform** | Home Assistant OS |
| **Hostname** | `85b1ecde-sendspin-bt-bridge` |
| **Bridge version** | 2.12.2 (build 2026-03-05) |
| **BT adapter** | CSR8510 A10 via USB passthrough (`C0:FB:F9:62:D6:9D`, hci0) |
| **Audio** | PulseAudio 16.1, A2DP sinks |
| **MA server** | auto:9000 (mDNS) |

**Devices (3):**

| Player | BT MAC | Sendspin port | PA sink | Volume | Delay |
|--------|--------|---------------|---------|--------|-------|
| ENEBY20 @ HAOS | `FC:58:FA:EB:08:6C` | 8928 | `bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink` | 58% | −500 ms |
| Yandex mini @ HAOS | `2C:D2:6B:B8:EC:5B` | 8929 | `bluez_sink.2C_D2_6B_B8_EC_5B.a2dp_sink` | 52% | −500 ms |
| Lenco LS-500 @ HAOS | `30:21:0E:0A:AE:5A` | 8932 | `bluez_sink.30_21_0E_0A_AE_5A.a2dp_sink` | 52% | −500 ms |

All 3 devices share MA sync group `b55d7f67-acc2-4cba-b37e-9fbd3eb3b410` for multiroom playback. MA API integration is active (bi-directional volume/transport sync).

### 2. Proxmox LXC — `proxmox-lxc.my.lan:8080`

Runs as a systemd service inside an unprivileged LXC container on Proxmox.

| Parameter | Value |
|-----------|-------|
| **Host** | Proxmox VE 8.4.16, CT 101, 2 cores, 1 GB RAM, 8 GB disk |
| **OS** | Ubuntu 24.04 LTS (Noble Numbat), x86_64 |
| **Hostname** | `sendspin` |
| **Bridge version** | 2.12.2 (build 2026-03-05) |
| **Python** | 3.12.3 |
| **BlueZ** | 5.72 |
| **PulseAudio** | 16.1 |
| **aiosendspin** | 4.3.2 |
| **Flask** | 3.1.3, Waitress 3.0.2 |
| **BT adapter** | CSR8510 A10 (`00:15:83:FF:8F:2B`, hci0) |
| **MA server** | auto:9000 (mDNS) |

**Devices (1):**

| Player | BT MAC | Sendspin port | PA sink | Volume | Delay |
|--------|--------|---------------|---------|--------|-------|
| ENEBY Portable @ LXC | `6C:5C:3D:35:17:99` | 8928 | `bluez_sink.6C_5C_3D_35_17_99.a2dp_sink` | 59% | −900 ms |

MA API integration active.

### 3. Turris LXC — `turris-lxc.my.lan:8080`

Runs as a systemd service inside an LXC container on Turris Omnia router (OpenWrt).

| Parameter | Value |
|-----------|-------|
| **Host** | Turris Omnia, TurrisOS 9.0.4 (OpenWrt), Marvell Armada 385 ARMv7, 2 GB RAM, 8 GB eMMC |
| **OS** | Ubuntu 24.04.4 LTS (Noble Numbat), armv7l |
| **Hostname** | `ubuntu` |
| **Bridge version** | 2.12.2 (build 2026-03-05) |
| **Python** | 3.12.3 |
| **BlueZ** | 5.72 |
| **PulseAudio** | 16.1 |
| **aiosendspin** | 4.3.2 |
| **Flask** | 3.1.3, Waitress 3.0.2 |
| **BT adapter** | CSR8510 A10 USB (`C0:FB:F9:62:D7:D6`, hci0) |
| **MA server** | auto:9000 (mDNS) |

**Devices (1):**

| Player | BT MAC | Sendspin port | PA sink | Volume | Delay |
|--------|--------|---------------|---------|--------|-------|
| AfterShokz @ OpenWRT | `20:74:CF:61:FB:D8` | 8928 | `bluez_sink.20_74_CF_61_FB_D8.a2dp_sink` | 67% | −500 ms |

:::note[OpenWrt specifics]
The host requires a `pulse` user (uid 109) in `/etc/passwd` for D-Bus EXTERNAL authentication.
Without it, PulseAudio inside the container cannot load `module-bluez5-discover` and BT audio profiles fail with `br-connection-profile-unavailable`. See [OpenWrt LXC README](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/README.md) for details.
:::

## Hardware summary

### Hosts

| Host | Hardware | CPU | RAM | Role |
|------|----------|-----|-----|------|
| **Proxmox** | HP ProLiant MicroServer Gen8 | Intel Celeron G1610T 2.3 GHz, 2 cores | 16 GB | VM/CT hypervisor |
| **Turris Omnia** | CZ.NIC Turris Omnia | Marvell Armada 385 ARMv7 1.6 GHz, 2 cores | 2 GB | Router + LXC host |

### Bluetooth adapters

All adapters are CSR8510 A10 (Cambridge Silicon Radio) USB dongles, USB ID `0a12:0001`.

| Adapter MAC | Location | Speakers |
|-------------|----------|----------|
| `C0:FB:F9:62:D6:9D` | Proxmox → HAOS VM 104 (USB passthrough) | ENEBY20, Yandex mini, Lenco LS-500 |
| `00:15:83:FF:8F:2B` | Proxmox → CT 101 | ENEBY Portable |
| `C0:FB:F9:62:D7:D6` | Turris Omnia USB | AfterShokz |

### Bluetooth speakers

| Speaker | Type | BT MAC | Bridge | Notes |
|---------|------|--------|--------|-------|
| **IKEA ENEBY20** | Shelf speaker | `FC:58:FA:EB:08:6C` | HAOS | Multiroom group member |
| **Yandex Station mini** | Smart speaker | `2C:D2:6B:B8:EC:5B` | HAOS | Multiroom group member |
| **Lenco LS-500** | Turntable with BT | `30:21:0E:0A:AE:5A` | HAOS | Multiroom group member |
| **IKEA ENEBY Portable** | Portable speaker | `6C:5C:3D:35:17:99` | Proxmox LXC | Standalone |
| **AfterShokz** | Bone conduction headset | `20:74:CF:61:FB:D8` | Turris LXC | Standalone |

## Music Assistant

| Parameter | Value |
|-----------|-------|
| **URL** | `http://haos.my.lan:8095` |
| **Host** | HAOS VM 104 on Proxmox |
| **Total players** | 9 (5 BT bridges + 1 sync group + 2 web + 1 desktop) |
| **Sync group** | "Sendspin BT" — groups all speakers for whole-home audio |

## Network

All devices on a flat `192.168.10.0/24` LAN. Turris Omnia is the router/gateway at `turris.my.lan`.

| IP | Host | Service |
|----|------|---------|
| `turris.my.lan` | Turris Omnia | Router, LXC host |
| `haos.my.lan` | HAOS VM | Music Assistant (:8095), Bridge addon (:8080) |
| `proxmox.my.lan` | Proxmox VE | Hypervisor web UI (:8006) |
| `turris-lxc.my.lan` | Turris LXC | Bridge (:8080) |
| `proxmox-lxc.my.lan` | Proxmox CT 101 | Bridge (:8080) |

## Software stack (common)

All LXC bridge instances share the same software stack:

| Component | Version |
|-----------|---------|
| **Sendspin BT Bridge** | 2.12.2 |
| **Ubuntu** | 24.04 LTS |
| **Python** | 3.12.3 |
| **BlueZ** | 5.72 |
| **PulseAudio** | 16.1 |
| **aiosendspin** | 4.3.2 |
| **Flask** | 3.1.3 |
| **Waitress** | 3.0.2 |
