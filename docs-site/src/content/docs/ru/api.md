---
title: API Reference
description: REST API Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

Веб-интерфейс предоставляет REST API на порту `8080`. Все эндпоинты возвращают JSON.

<Aside type="caution">
  По умолчанию API не требует аутентификации. В сети `host` любой участник LAN имеет доступ. Не открывайте порт 8080 в интернет.

  Если установлен `AUTH_ENABLED=true`, все эндпоинты кроме `/login`, `/logout`, `/api/status` и `/static/*` требуют действующую сессионную куку, полученную через авторизацию в веб-интерфейсе.
</Aside>

## Статус и мониторинг

### `GET /api/status`

Статус всех плееров.

**Ответ:**
```json
[
  {
    "player_name": "Колонка в гостиной",
    "mac": "AA:BB:CC:DD:EE:FF",
    "websocket_url": "ws://192.168.1.10:8928/sendspin",
    "connected": true,
    "playing": true,
    "bluetooth_connected": true,
    "bluetooth_since": "2026-03-02T10:00:00",
    "server_connected": true,
    "server_since": "2026-03-02T10:00:01",
    "volume": 48,
    "current_track": "Song Title",
    "current_artist": "Artist Name",
    "audio_format": "flac 48000Hz/24-bit/2ch",
    "adapter_mac": "C0:FB:F9:62:D6:9D",
    "adapter_id": "hci0",
    "sync_status": "In sync",
    "sync_delay_ms": -600,
    "management_enabled": true
  }
]
```

### `GET /api/status/stream`

Server-Sent Events поток. Браузер подключается один раз; сервер отправляет `data: {...}` при каждом изменении состояния устройства. Веб-интерфейс использует его вместо polling.

```
GET /api/status/stream
Accept: text/event-stream

data: [{"player_name": "Колонка в гостиной", "playing": true, ...}]
data: [{"player_name": "Колонка в гостиной", "playing": false, ...}]
```

### `GET /api/diagnostics`

Структурированная диагностика: адаптеры, синки, D-Bus, статус каждого устройства.

### `GET /api/version`

```json
{ "version": "2.6.2", "build_date": "2026-03-04" }
```

### `GET /api/logs`

Последние строки лога приложения.

**Query параметры:**
- `lines` — количество строк (по умолчанию 100)

## Управление воспроизведением

### `POST /api/pause`

Пауза/воспроизведение для конкретного плеера.

**Body:** `{ "player_name": "Гостиная" }`

### `POST /api/pause_all`

Пауза/воспроизведение на всех плеерах.

**Body:** `{ "action": "pause" }` или `{ "action": "play" }`

### `POST /api/volume`

Установить громкость.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "value": 75 }`

- `mac` — MAC-адрес устройства
- `value` — 0–100

### `POST /api/mute`

Включить/выключить mute.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "muted": true }`

## Bluetooth-управление

### `POST /api/bt/reconnect`

Принудительное переподключение BT устройства.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF" }`

### `POST /api/bt/pair`

Запустить процедуру паринга (~25 сек). Устройство должно быть в режиме паринга.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "adapter": "hci0" }`

### `POST /api/bt/management`

Переключить режим управления (Release/Reclaim).

**Body:** `{ "player_name": "Гостиная", "enabled": false }`

### `POST /api/bt/scan`

Запускает фоновое сканирование BT-устройств (~10 сек). Возвращает ответ немедленно.

**Ответ:** `{ "job_id": "550e8400-e29b-41d4-a716-446655440000" }`

### `GET /api/bt/scan/result/<job_id>`

Опрос результатов сканирования.

**Ответ при выполнении:**
```json
{ "status": "running" }
```

**Ответ после завершения:**
```json
{
  "status": "done",
  "devices": [
    { "mac": "AA:BB:CC:DD:EE:FF", "name": "JBL Flip 5" }
  ]
}
```

**Ответ при ошибке:**
```json
{ "status": "done", "error": "Scan failed: bluetoothctl timed out" }
```

### `GET /api/bt/adapters`

Список доступных BT-адаптеров.

### `GET /api/bt/paired`

Список спаренных устройств по каждому адаптеру.

## Конфигурация

### `GET /api/config`

Текущая конфигурация из `config.json`.

### `POST /api/config`

Сохранить конфигурацию.

**Body:** JSON объект с полями конфигурации (см. раздел [Настройка](/sendspin-bt-bridge/ru/configuration/)).

## Сервис

### `POST /api/restart`

Перезапустить сервис. Поддерживаемые методы: `systemd`, `docker`, `ha`.

**Body:** `{ "method": "auto" }`

## Примеры использования

```bash
# Получить статус всех плееров
curl http://localhost:8080/api/status

# Подписаться на обновления в реальном времени (SSE)
curl -N http://localhost:8080/api/status/stream

# Установить громкость 50% на конкретном устройстве
curl -X POST http://localhost:8080/api/volume \
  -H 'Content-Type: application/json' \
  -d '{"mac": "AA:BB:CC:DD:EE:FF", "value": 50}'

# Поставить на паузу конкретный плеер
curl -X POST http://localhost:8080/api/pause \
  -H 'Content-Type: application/json' \
  -d '{"player_name": "Гостиная"}'

# Поставить на паузу все плееры
curl -X POST http://localhost:8080/api/pause_all \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Получить диагностику
curl http://localhost:8080/api/diagnostics | python3 -m json.tool

# Запустить BT-сканирование и опросить результат
JOB=$(curl -s -X POST http://localhost:8080/api/bt/scan | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
curl http://localhost:8080/api/bt/scan/result/$JOB
```
