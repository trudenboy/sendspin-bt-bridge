---
title: Установка — LXC (Proxmox и OpenWrt)
description: Установка Sendspin Bluetooth Bridge в LXC-контейнер на Proxmox VE или OpenWrt с использованием Bluetooth-стека хоста
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Почему LXC?

LXC — нативный non-Docker вариант для appliance-хостов вроде Proxmox VE и OpenWrt. Bridge работает внутри контейнера, а **Bluetooth-стек хоста прокидывается через D-Bus**, при этом PulseAudio запускается внутри самого контейнера.

| Платформа | Bluetooth | Аудио | Способ установки |
|---|---|---|---|
| **Proxmox VE** | `bluetoothd` хоста через D-Bus bridge | PulseAudio внутри контейнера | `proxmox-create.sh` |
| **OpenWrt / TurrisOS** | `bluetoothd` хоста через D-Bus bridge | PulseAudio внутри контейнера | `openwrt/create.sh` |

## Proxmox VE

### Быстрая установка

На хосте Proxmox:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh)
```

### Ручной путь

<Steps>

1. Создайте **привилегированный Ubuntu 24.04** LXC-контейнер.
2. Запустите установщик внутри контейнера:

   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)
   ```

3. Добавьте на хосте Proxmox необходимые правила D-Bus / устройств в `/etc/pve/lxc/<CTID>.conf`.
4. Перезапустите контейнер.

</Steps>

Подробный Proxmox-гайд по-прежнему находится в [`lxc/README.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/README.md).

## OpenWrt / TurrisOS

### Быстрая установка

На хосте OpenWrt:

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

Полный OpenWrt-специфичный гайд находится в [`lxc/openwrt/README.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/lxc/openwrt/README.md).

## Сопряжение колонки

Сопрягайте изнутри контейнера через `btctl` — это wrapper, который обращается к Bluetooth-демону хоста через D-Bus bridge:

```bash
btctl
power on
scan on
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
exit
```

## Планирование портов в LXC-развёртывании

После первого запуска настройте порты в `/config/config.json` (или через веб-интерфейс, затем перезапустите сервис):

```json
{
  "WEB_PORT": 8080,
  "BASE_LISTEN_PORT": 8928,
  "BLUETOOTH_DEVICES": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "player_name": "Колонка в гостиной",
      "listen_port": 8935,
      "listen_host": "192.168.1.50"
    }
  ]
}
```

- **`WEB_PORT`** управляет прямым listener'ом веб-интерфейса/API у контейнера.
- **`BASE_LISTEN_PORT`** задаёт базовый блок Sendspin-портов для устройств без явного `listen_port`.
- **`listen_port`** переопределяет player-port для одного устройства.
- **`listen_host`** меняет рекламируемый host/IP плеера и не влияет на bind-адрес.

## Несколько LXC bridge на одном хосте

Если на одном Proxmox- или OpenWrt-хосте работает несколько bridge-контейнеров:

- задайте каждому контейнеру уникальный `WEB_PORT`
- задайте каждому контейнеру уникальный `BASE_LISTEN_PORT`
- оставляйте каждую Bluetooth-колонку назначенной только одному работающему bridge

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
  В LXC-режиме контейнер намеренно использует Bluetooth-демон хоста через D-Bus. Не пытайтесь включать отдельный `bluetoothd` внутри контейнера.
</Aside>
