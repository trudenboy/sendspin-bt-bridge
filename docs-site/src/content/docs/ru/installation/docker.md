---
title: Установка — Docker Compose
description: Запуск Sendspin Bluetooth Bridge через Docker Compose с поддержкой override-параметров WEB_PORT и BASE_LISTEN_PORT
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Требования

- Docker Engine и Docker Compose
- Bluetooth-адаптер на хосте
- PulseAudio или PipeWire на хосте
- Music Assistant в вашей сети

Публикуемый образ поддерживает `linux/amd64`, `linux/arm64` и `linux/arm/v7`.

<Aside type="tip">
  Устанавливаете на Raspberry Pi? Используйте отдельное [руководство по Raspberry Pi](/ru/installation/raspberry-pi/) с рекомендациями по моделям и one-liner installer.
</Aside>

## Быстрый старт

<Steps>

1. **Сначала сопрягите колонку на хосте**

   ```bash
   bluetoothctl
   scan on
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   connect AA:BB:CC:DD:EE:FF
   exit
   ```

2. **Создайте `.env`**

   ```env
   AUDIO_UID=1000
   TZ=Europe/Moscow
   WEB_PORT=8080
   BASE_LISTEN_PORT=8928
   ```

   <Aside type="caution">
     Проверьте `id -u`. Если аудио-пользователь имеет UID не `1000`, укажите правильное значение в `AUDIO_UID`.
   </Aside>

   <Aside type="tip">
     `AUDIO_UID` определяет, какой user-scoped сокет PipeWire/PulseAudio с хоста будет смонтирован в контейнер. На Raspberry Pi и других системах с PipeWire ошибка в UID часто выглядит как `pactl` / PulseAudio `Connection refused`, даже если путь к сокету существует.
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
         - TZ=${TZ:-UTC}
         - WEB_PORT=${WEB_PORT:-8080}
         - BASE_LISTEN_PORT=${BASE_LISTEN_PORT:-8928}
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
   mkdir -p /etc/docker/Sendspin
   docker compose up -d
   ```

5. **Откройте веб-интерфейс**

   ```text
   http://<ip-хоста>:<WEB_PORT>
   ```

</Steps>

## Планирование портов

- **`WEB_PORT`** управляет прямым listener'ом веб-интерфейса/API в Docker-режиме.
- **`BASE_LISTEN_PORT`** задаёт базовый Sendspin-порт для устройств без явного `listen_port`.
- Каждое устройство без ручного порта получает `BASE_LISTEN_PORT + индекс_устройства`.
- В сложных схемах можно задать `listen_port` и `listen_host` на уровне устройства через веб-интерфейс или `/config/config.json` после первого запуска.

Пример блока устройства в `/config/config.json`:

```json
{
  "mac": "11:22:33:44:55:66",
  "player_name": "Колонка на кухне",
  "listen_port": 8935,
  "listen_host": "192.168.1.50"
}
```

`listen_host` меняет только рекламируемый host/IP для плеера и не влияет на bind-адрес внутри контейнера.

## Несколько bridge-контейнеров на одном хосте

Если вы запускаете несколько bridge-контейнеров на одной машине:

- задайте каждому контейнеру уникальный `WEB_PORT`
- задайте каждому контейнеру уникальный `BASE_LISTEN_PORT`
- **не** настраивайте одну и ту же Bluetooth-колонку в двух работающих контейнерах

## Сеть и capabilities

`network_mode: host` обязателен для:

- mDNS-обнаружения при `SENDSPIN_SERVER=auto`
- доступа к Bluetooth-стеку хоста через D-Bus

Необходимые capabilities:

| Capability | Назначение |
|---|---|
| `NET_ADMIN` | Управление Bluetooth-адаптером |
| `NET_RAW` | Raw Bluetooth/HCI socket access |

<Aside type="caution">
  Для стандартного Docker-развёртывания выше `privileged: true` не требуется.
</Aside>

## Проверка контейнера

```bash
docker logs -f sendspin-client
curl -s http://localhost:${WEB_PORT:-8080}/api/preflight | python3 -m json.tool
```

В новых образах startup diagnostics также показывают:

- runtime UID/GID внутри контейнера
- выбранный путь к audio socket
- владельца/права сокета
- результат живого `pactl info` probe
- отдельное предупреждение, если контейнер работает под другим UID, чем user-scoped audio socket на хосте

## Troubleshooting для user-scoped PipeWire / PulseAudio

Если на хосте аудио работает нормально, но в контейнере всё ещё видно `Connection refused` или `pactl` не может подключиться, проверьте:

```bash
docker exec sendspin-client ls -la /run/user/${AUDIO_UID:-1000}/pulse/
docker exec sendspin-client env | grep -E 'PULSE|XDG'
docker exec sendspin-client id
docker inspect sendspin-client --format '{{json .Mounts}}'
```

И на хосте:

```bash
id
pactl info
ls -la /run/user/${AUDIO_UID:-1000}/pulse/
```

Если смонтированный host audio socket принадлежит вашему обычному login user, а процесс в контейнере работает как `root`, попробуйте такой **временный диагностический тест** в `docker-compose.yml`:

```yaml
services:
  sendspin-client:
    user: "${AUDIO_UID:-1000}:${AUDIO_UID:-1000}"
```

После этого перезапустите контейнер и проверьте, начал ли работать `pactl`. Это именно шаг диагностики: он помогает быстро подтвердить проблему несовпадения UID/session при доступе к host audio socket.

## Применение изменений конфигурации

Изменения устройств, адаптеров, `WEB_PORT`, `BASE_LISTEN_PORT` и настроек подключения к Music Assistant требуют перезапуска контейнера.
