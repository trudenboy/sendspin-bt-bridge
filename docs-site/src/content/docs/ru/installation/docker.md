---
title: Установка — Docker Compose
description: Запуск Sendspin Bluetooth Bridge в Docker Compose
---


## Требования

- Docker Engine и Docker Compose
- Bluetooth-адаптер на хосте
- PulseAudio или PipeWire на хосте
- Music Assistant Server на вашей сети

Docker-образ поддерживает архитектуры `linux/amd64`, `linux/arm64` и `linux/arm/v7`.

## Быстрый старт

<Steps>

1. **Клонируйте репозиторий или создайте `docker-compose.yml`**

   ```yaml
   services:
     sendspin-client:
       image: ghcr.io/trudenboy/sendspin-bt-bridge:latest
       container_name: sendspin-client
       network_mode: host
       cap_add:
         - NET_ADMIN
         - NET_RAW
         - SYS_ADMIN
       environment:
         - SENDSPIN_SERVER=auto
         - SENDSPIN_PORT=9000
         - BLUETOOTH_MAC=${BLUETOOTH_MAC:-}
         - TZ=Europe/Moscow
       volumes:
         - /var/run/dbus:/var/run/dbus
         - /run/user/${AUDIO_UID:-1000}/pulse:/run/user/1000/pulse
         - /run/user/${AUDIO_UID:-1000}/pipewire-0:/run/user/1000/pipewire-0
         - /etc/docker/Sendspin:/config
       restart: unless-stopped
   ```

2. **Настройте переменные окружения**

   Создайте файл `.env` рядом с `docker-compose.yml`:

   ```env
   BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
   AUDIO_UID=1000
   ```

   Или передайте через переменные окружения хоста.

3. **Запустите контейнер**

   ```bash
   docker compose up -d
   ```

4. **Откройте веб-интерфейс**

   ```
   http://localhost:8080
   ```

</Steps>

## Требования к сети

Контейнер использует `network_mode: host`, что необходимо для:
- Обнаружения MA сервера через mDNS (`auto`)
- Bluetooth D-Bus доступа

## Capabilities

| Capability | Зачем |
|---|---|
| `NET_ADMIN` | Управление сетевыми интерфейсами (BT) |
| `NET_RAW` | Raw socket доступ для BT |
| `SYS_ADMIN` | Монтирование D-Bus, управление PulseAudio |

<Aside type="caution">
  `privileged: true` **не требуется** и не рекомендуется. Достаточно перечисленных `cap_add`.
</Aside>

## Volumes

| Volume | Описание |
|---|---|
| `/var/run/dbus` | D-Bus сокет для bluetoothctl |
| `/run/user/UID/pulse` | PulseAudio сокет |
| `/run/user/UID/pipewire-0` | PipeWire сокет |
| `/etc/docker/Sendspin` | Директория конфигурации (config.json) |

## Просмотр логов

```bash
docker logs -f sendspin-client
```

## Несколько устройств

Для управления несколькими колонками настройте их через веб-интерфейс на `http://localhost:8080` → раздел **Bluetooth Devices**. Каждое устройство создаёт отдельный процесс pleeera в MA.
