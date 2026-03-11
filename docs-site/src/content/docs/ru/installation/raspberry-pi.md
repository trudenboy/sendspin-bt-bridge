---
title: Установка — Raspberry Pi
description: Запуск Sendspin Bluetooth Bridge на Raspberry Pi с Docker
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Поддерживаемые модели

| Модель | Архитектура | Docker-платформа | Статус |
|--------|------------|-----------------|--------|
| **Raspberry Pi 5** | aarch64 | `linux/arm64` | ✅ Рекомендуется |
| **Raspberry Pi 4** (2/4/8 ГБ) | aarch64 | `linux/arm64` | ✅ Рекомендуется |
| **Raspberry Pi 3 Model B+** | armv7l | `linux/arm/v7` | ⚠️ Макс. 1–2 колонки |
| **Raspberry Pi Zero 2 W** | aarch64 | `linux/arm64` | ⚠️ Мало RAM (512 МБ) |

<Aside type="tip">
  Используйте **64-битную Raspberry Pi OS** (aarch64) — она обеспечивает лучшую производительность и полную совместимость.
  32-битная ОС (armv7) работает, но может быть ограничена при нескольких колонках.
</Aside>

## Быстрый старт (Установка одной командой)

Самый быстрый способ начать — одна команда, которая сделает всё:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-install.sh | bash
```

Установщик выполнит:
- Проверку системы (архитектура, RAM, Docker, Bluetooth, аудио)
- Установку Docker, если не установлен
- Загрузку `docker-compose.yml`
- Генерацию `.env` с автоопределёнными настройками
- Интерактивное сопряжение Bluetooth-колонки (по желанию)
- Скачивание образа и запуск контейнера

<Aside type="tip">
  Для CI/автоматизации используйте неинтерактивный режим:
  ```bash
  NONINTERACTIVE=1 curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-install.sh | bash
  ```
  Он автоматически определит настройки и пропустит интерактивные вопросы.
</Aside>

После завершения установки веб-интерфейс доступен по адресу `http://<ip-raspberry-pi>:8080`.

## Ручная установка

Если вы предпочитаете пошаговый контроль, следуйте инструкциям ниже.

### Предварительные требования

<Steps>

1. **Raspberry Pi OS** (Bookworm или новее) установлена и обновлена

2. **Docker Engine** установлен:

   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   # Перелогиньтесь, чтобы изменение группы вступило в силу
   ```

3. **Bluetooth-адаптер** — встроенный или USB (CSR8510, TP-Link UB500 и т.д.)

4. **Аудиосистема** — PipeWire (по умолчанию в Bookworm) или PulseAudio:

   ```bash
   # Проверьте, какая аудиосистема запущена
   pactl info | grep "Server Name"
   # Ожидаемый вывод: "PulseAudio (on PipeWire ...)" или "pulseaudio"
   ```

5. **Колонка сопряжена на хосте** (не внутри Docker):

   ```bash
   bluetoothctl
   scan on
   # Дождитесь появления колонки, затем:
   pair AA:BB:CC:DD:EE:FF
   trust AA:BB:CC:DD:EE:FF
   connect AA:BB:CC:DD:EE:FF
   exit
   ```

</Steps>

### Предварительная проверка

Запустите диагностический скрипт **перед** запуском контейнера:

```bash
curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/scripts/rpi-check.sh | bash
```

Скрипт проверяет Docker, Bluetooth, аудиосистему, UID и память — и выводит рекомендуемые значения для `.env`.

### Установка

<Steps>

1. **Создайте директорию проекта**

   ```bash
   mkdir ~/sendspin-bt-bridge && cd ~/sendspin-bt-bridge
   ```

2. **Сохраните файл `.env`** из вывода диагностического скрипта:

   ```env
   # Настройте Bluetooth-устройства через веб-интерфейс: http://localhost:8080
   AUDIO_UID=1000
   TZ=Europe/Moscow
   ```

3. **Скачайте `docker-compose.yml`**

   ```bash
   curl -sSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/docker-compose.yml -o docker-compose.yml
   ```

   Или создайте вручную:

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
         - ./config:/config
       environment:
         - SENDSPIN_SERVER=auto
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

5. **Проверьте диагностику при запуске**

   ```bash
   docker logs sendspin-client
   ```

   Вы должны увидеть таблицу диагностики со всеми проверками:
   ```
   ╔══════════════════════════════════════════════════════╗
   ║  Sendspin Bridge v2.16.3 Diagnostics
   ╠══════════════════════════════════════════════════════╣
   ║  Platform:    aarch64 (arm64)
   ║  Audio:       ✓ PulseAudio (...)
   ║  Bluetooth:   ✓ 00:1A:7D:DA:71:13
   ║  D-Bus:       ✓ host socket mounted
   ╚══════════════════════════════════════════════════════╝
   ```

   Также можно проверить через API:

   ```bash
   curl -s http://localhost:8080/api/preflight | python3 -m json.tool
   ```

6. **Откройте веб-интерфейс**

   ```
   http://<ip-raspberry-pi>:8080
   ```

</Steps>

## Обновление

```bash
cd ~/sendspin-bt-bridge
docker compose pull
docker compose up -d
```

## Устранение неполадок

### Нет звука (тишина)

1. Проверьте подключение колонки: `bluetoothctl info AA:BB:CC:DD:EE:FF | grep Connected`
2. Проверьте аудио-синк: `pactl list short sinks | grep bluez`
3. Проверьте mute: `pactl get-sink-mute <sink_index>`
4. Проверьте логи контейнера: `docker logs sendspin-client | grep -E "Audio worker|daemon stderr"`

### Несовпадение UID

Если UID вашего пользователя не 1000 (проверьте: `id -u`), укажите `AUDIO_UID` в `.env`:

```env
AUDIO_UID=1001  # Ваш реальный UID
```

### PipeWire или PulseAudio

Raspberry Pi OS Bookworm использует PipeWire по умолчанию с PulseAudio-совместимостью. Мост работает с обеими системами. Для проверки:

```bash
pactl info | grep "Server Name"
# PipeWire: "PulseAudio (on PipeWire 1.x.x)"
# PulseAudio: "pulseaudio"
```

### Лимиты ресурсов

| Модель | RAM | Рекомендуемое кол-во колонок |
|--------|-----|----------------------------|
| RPi 5 (4/8 ГБ) | 4–8 ГБ | 3–4+ |
| RPi 4 (2 ГБ) | 2 ГБ | 2–3 |
| RPi 4 (1 ГБ) | 1 ГБ | 1–2 |
| RPi 3 (1 ГБ) | 1 ГБ | 1 |
| RPi Zero 2 W | 512 МБ | 1 |

### Зачем `network_mode: host`

Контейнер использует `network_mode: host`, потому что ему нужен:
- **mDNS** для автообнаружения Music Assistant в локальной сети
- **D-Bus** доступ к `bluetoothd` хоста для управления Bluetooth

Это значит, что контейнер разделяет сетевой стек хоста — веб-интерфейс доступен по адресу `http://<ip-pi>:8080` напрямую.
