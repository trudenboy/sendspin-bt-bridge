---
title: Установка — LXC (Proxmox и OpenWrt)
description: Установка Sendspin Bluetooth Bridge в LXC контейнер на Proxmox VE или OpenWrt
---


## Преимущества LXC перед Docker

В отличие от Docker, LXC-контейнер имеет **собственный bluetoothd и PulseAudio** (Proxmox) или использует bluetoothd хоста через D-Bus (OpenWrt), что даёт более стабильную работу с Bluetooth: паринг сохраняется между перезапусками, нет конфликтов с хостовым bluetoothd.

## Поддерживаемые платформы

| Платформа | Скрипт | Статус |
|-----------|--------|--------|
| **Proxmox VE** 7/8 | [`proxmox-create.sh`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/proxmox-create.sh) | ✅ Стабильно |
| **OpenWrt** 23.x+ / TurrisOS 9.x | [`openwrt/create.sh`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/create.sh) | ✅ Стабильно |

## Proxmox VE

### Требования

- Proxmox VE 7.x или 8.x
- USB Bluetooth-адаптер (рекомендуется один адаптер — одна колонка)

### Быстрая установка

На хосте Proxmox:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh)
```

Скрипт интерактивно запрашивает ID контейнера, имя хоста, RAM, диск, сеть и проброску USB Bluetooth.

### Ручная установка

<Steps>

1. Создайте новый **привилегированный** LXC-контейнер (**Ubuntu 24.04**, 512 МБ RAM, 4 ГБ диск)
2. Запустите контейнер и откройте оболочку (`pct enter <CTID>`)
3. Запустите установщик:
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)
   ```
4. Добавьте в `/etc/pve/lxc/<CTID>.conf` на **хосте Proxmox**:
   ```
   lxc.apparmor.profile: unconfined
   lxc.cgroup2.devices.allow: c 166:* rwm
   lxc.cgroup2.devices.allow: c 13:* rwm
   lxc.cgroup2.devices.allow: c 10:232 rwm
   lxc.mount.entry: /run/dbus bt-dbus none bind,create=dir 0 0
   lxc.cgroup2.devices.allow: c 189:* rwm
   ```
5. Перезапустите контейнер: `pct restart <CTID>`

</Steps>

## OpenWrt / TurrisOS

### Требования

- OpenWrt 23.x+ или TurrisOS 9.x
- ≥1 ГБ RAM, ≥2 ГБ свободного места
- USB Bluetooth-адаптер

### Быстрая установка

На хосте OpenWrt:

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

Скрипт устанавливает LXC и Bluetooth-пакеты через `opkg`, создаёт контейнер Ubuntu 24.04, настраивает D-Bus bridge и cgroup-правила, устанавливает procd-скрипт для автозапуска.

Полные шаги ручной установки и известные проблемы — в [lxc/openwrt/README.md](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/README.md).

## Паринг колонки

Если колонка ещё не спарена:

1. Переведите колонку в режим паринга
2. В веб-интерфейсе нажмите **🔍 Scan** и подождите ~10 секунд
3. Нажмите **Re-pair** рядом с найденным устройством

Или через `bluetoothctl` внутри контейнера:

```bash
bluetoothctl
# power on
# scan on
# pair AA:BB:CC:DD:EE:FF
# trust AA:BB:CC:DD:EE:FF
# connect AA:BB:CC:DD:EE:FF
```

## Управление сервисом

```bash
systemctl status sendspin-client
systemctl restart sendspin-client
journalctl -u sendspin-client -f
```

## Обновление

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/upgrade.sh)
```

<Aside type="tip">
  Для нескольких колонок рекомендуется создать отдельный LXC контейнер под каждый USB Bluetooth адаптер. Это изолирует bluetoothd и PulseAudio, устраняя конфликты кодеков.
</Aside>
