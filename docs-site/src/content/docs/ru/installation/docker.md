---
title: Установка — Docker Compose
description: Запуск Sendspin Bluetooth Bridge в Docker Compose
---

import { Aside } from '@astrojs/starlight/components';

## Требования

- Docker Engine и Docker Compose
- Bluetooth-адаптер на хосте
- PulseAudio или PipeWire на хосте
- Music Assistant Server в вашей сети

Docker-образ поддерживает архитектуры `linux/amd64`, `linux/arm64` и `linux/arm/v7`.

<Aside type="tip">
  Используете **Raspberry Pi**? Смотрите отдельное [руководство по Raspberry Pi](/ru/installation/raspberry-pi/) с инструкциями для конкретных моделей и скриптом предварительной проверки.
</Aside>

## Предварительная проверка

Перед запуском убедитесь, что хост готов:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
```

Скрипт проверяет Docker, Bluetooth, аудиосистему, UID и выводит рекомендуемые значения `.env`. Работает на любом Linux-хосте, не только на Raspberry Pi.

## Быстрый старт

<Steps>

1. **Сначала сопрягите Bluetooth-колонку на хосте**

   ```bash
   bluetoothctl
   scan on
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   connect AA:BB:CC:DD:EE:FF
   exit
   ```

2. **Создайте `.env`** с вашими настройками:

   ```env
   BLUETOOTH_MAC=AA:BB:CC:DD:EE:FF
   AUDIO_UID=1000
   TZ=Europe/Moscow
   ```

   <Aside type="caution">
     Проверьте UID командой `id -u`. Если он не 1000, укажите `AUDIO_UID` соответственно — иначе аудио не будет работать.
   </Aside>

3. **Создайте `docker-compose.yml`**

   ```yaml
   services:
     sendspin-client:
       image: ghcr.io/trudenboy/sendspin-bt-bridge:latest
       container_name: sendspin-client
       restart: unless-stopped
       network_mode: host
       volumes:
         - /var/run/dbus:/var/run/dbus
         - /run/user/${AUDIO_UID:-1000}/pulse:/run/user/${AUDIO_UID:-1000}/pulse
         - /run/user/${AUDIO_UID:-1000}/pipewire-0:/run/user/${AUDIO_UID:-1000}/pipewire-0
         - /etc/docker/Sendspin:/config
       environment:
         - SENDSPIN_SERVER=auto
         - BLUETOOTH_MAC=${BLUETOOTH_MAC:-}
         - TZ=${TZ:-UTC}
         - WEB_PORT=8080
         - CONFIG_DIR=/config
         - PULSE_SERVER=unix:/run/user/${AUDIO_UID:-1000}/pulse/native
         - XDG_RUNTIME_DIR=/run/user/${AUDIO_UID:-1000}
       devices:
         - /dev/bus/usb:/dev/bus/usb
       cap_add:
         - NET_ADMIN
         - NET_RAW
   ```

4. **Запустите контейнер**

   ```bash
   docker compose up -d
   ```

5. **Проверьте запуск**

   ```bash
   docker logs sendspin-client
   ```

   Проверьте таблицу диагностики — не должно быть отметок ✗.

6. **Откройте веб-интерфейс**

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

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `SENDSPIN_SERVER` | `auto` | Адрес MA сервера; `auto` использует mDNS |
| `BLUETOOTH_MAC` | — | MAC колонки (можно настроить через веб-интерфейс) |
| `AUDIO_UID` | `1000` | UID пользователя хоста для путей аудио-сокетов |
| `TZ` | `UTC` | Часовой пояс контейнера |
| `WEB_PORT` | `8080` | Порт веб-интерфейса |
| `PULSE_SERVER` | — | Путь к PulseAudio сокету |

## Просмотр логов

```bash
docker logs -f sendspin-client
```

## Проверка через API

После запуска контейнера проверьте эндпоинт preflight:

```bash
curl -s http://localhost:8080/api/preflight | python3 -m json.tool
```

## Несколько устройств

Для управления несколькими колонками настройте их через веб-интерфейс на `http://localhost:8080` → раздел **Bluetooth Devices**. Каждое устройство создаёт отдельный плеер в MA.
