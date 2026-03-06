---
title: API Reference
description: REST API Sendspin Bluetooth Bridge
---


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
    "connected": true,
    "server_connected": true,
    "bluetooth_connected": true,
    "bluetooth_since": "2026-03-05T10:00:00",
    "server_since": "2026-03-05T10:00:01",
    "playing": true,
    "volume": 48,
    "muted": false,
    "current_track": "Song Title",
    "current_artist": "Artist Name",
    "audio_format": "flac 48000Hz/24-bit/2ch",
    "connected_server_url": "ws://192.168.1.10:8928/sendspin",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter": "C0:FB:F9:62:D6:9D",
    "bluetooth_adapter_name": "Адаптер в гостиной",
    "bluetooth_adapter_hci": "hci0",
    "has_sink": true,
    "sink_name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
    "bt_management_enabled": true,
    "group_id": "abc123",
    "group_name": "Sendspin BT",
    "sync_status": "In sync",
    "sync_delay_ms": -600,
    "static_delay_ms": -600,
    "listen_port": 8928,
    "version": "2.10.6",
    "build_date": "2026-03-05"
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
{ "version": "2.10.6", "build_date": "2026-03-05" }
```

### `GET /api/groups`

Возвращает список устройств, сгруппированных по MA-группам синхронизации. Устройства с одинаковым `group_id` объединяются в одну запись; одиночные плееры (без группы) отображаются отдельно с `group_id: null`.

```json
[
  {
    "group_id": "abc123",
    "group_name": "Sendspin BT",
    "avg_volume": 52,
    "playing": true,
    "members": [
      { "player_name": "Гостиная", "volume": 48, "playing": true, "connected": true, "bluetooth_connected": true }
    ]
  },
  {
    "group_id": null,
    "group_name": null,
    "avg_volume": 70,
    "playing": false,
    "members": [
      { "player_name": "Спальня", "volume": 70, "playing": false, "connected": true, "bluetooth_connected": false }
    ]
  }
]
```

## Управление воспроизведением

### `POST /api/pause_all`

Пауза/воспроизведение на всех плеерах.

**Body:** `{ "action": "pause" }` или `{ "action": "play" }`

### `POST /api/group/pause`

Пауза или воспроизведение конкретной MA-группы. При `action="play"` использует MA REST API (если настроен), чтобы все участники группы возобновили воспроизведение синхронно.

**Body:** `{ "group_id": "abc123", "action": "pause" }` — action: `"pause"` или `"play"`

### `POST /api/volume`

Установить громкость.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "value": 75 }`

- `mac` — MAC-адрес устройства
- `value` — 0–100

### `POST /api/mute`

Включить/выключить mute.

**Body:** `{ "mac": "AA:BB:CC:DD:EE:FF", "muted": true }`

## Интеграция с Music Assistant

Эти эндпоинты требуют настройки `MA_API_URL` и `MA_API_TOKEN`.

### `GET /api/ma/groups`

Возвращает MA-группы синхронизации, обнаруженные через MA REST API.

```json
[
  {
    "id": "ma-syncgroup-abc123",
    "name": "Sendspin BT",
    "members": [
      { "id": "...", "name": "Гостиная", "state": "playing", "volume": 48, "available": true }
    ]
  }
]
```

### `POST /api/ma/rediscover`

Повторное обнаружение MA-групп без перезапуска бриджа. Считывает текущие `MA_API_URL` / `MA_API_TOKEN` из `config.json`.

**Ответ:**
```json
{ "success": true, "syncgroups": 2, "mapped_players": 3, "groups": [{"id": "...", "name": "Sendspin BT"}] }
```

### `GET /api/ma/nowplaying`

Текущие данные о воспроизведении из MA. Возвращает `{"connected": false}` если MA-интеграция не активна.

```json
{
  "connected": true,
  "state": "playing",
  "track": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "image_url": "http://...",
  "elapsed": 142.5,
  "elapsed_updated_at": "2026-03-05T10:01:30",
  "duration": 279,
  "shuffle": false,
  "repeat": "off",
  "queue_index": 3,
  "queue_total": 12,
  "syncgroup_id": "ma-syncgroup-abc123"
}
```

### `POST /api/ma/queue/cmd`

Команда управления воспроизведением для активной MA-группы.

**Body:**
```json
{ "action": "next", "syncgroup_id": "ma-syncgroup-abc123" }
```

| Поле | Описание |
|---|---|
| `action` | `"next"`, `"previous"`, `"shuffle"`, `"repeat"` или `"seek"` |
| `value` | Для `shuffle`: `true`/`false`. Для `repeat`: `"off"`, `"all"`, `"one"`. Для `seek`: секунды (int) |
| `syncgroup_id` | Опционально — целевая группа; без этого поля используется первая активная группа |

### `GET /api/debug/ma`

Дамп состояния MA-интеграции для диагностики: ключи кэша now-playing, обнаруженные группы, ID плееров, живые ID очередей из MA WebSocket.

```json
{
  "cache_keys": ["ma-syncgroup-abc123"],
  "groups": [...],
  "clients": [{ "player_name": "Гостиная", "player_id": "...", "group_id": "abc123" }],
  "live_queue_ids": ["up_abc123def456"]
}
```

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

## Сервис

### `GET /api/logs`

Последние строки лога приложения.

**Query параметры:**
- `lines` — количество строк (по умолчанию `100`)

### `POST /api/restart`

Перезапустить сервис.

### `POST /api/set-password`

Установить или изменить пароль веб-интерфейса. Недоступно в режиме HA addon (используйте управление пользователями HA).

**Body:** `{ "password": "mysecretpassword" }` (минимум 8 символов)

**Ответ:** `{ "success": true }`

### `POST /api/settings/log_level`

Изменить уровень логирования немедленно и сохранить в `config.json`. Изменение распространяется на все подпроцессы — перезапуск не нужен.

**Body:** `{ "level": "debug" }` — `"info"` или `"debug"`

**Ответ:** `{ "success": true, "level": "DEBUG" }`

## Конфигурация

### `GET /api/config`

Текущая конфигурация из `config.json`.

### `POST /api/config`

Сохранить конфигурацию.

**Body:** JSON объект с полями конфигурации (см. раздел [Настройка](/sendspin-bt-bridge/ru/configuration/)).

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

# Поставить на паузу все плееры
curl -X POST http://localhost:8080/api/pause_all \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Поставить на паузу конкретную MA-группу
curl -X POST http://localhost:8080/api/group/pause \
  -H 'Content-Type: application/json' \
  -d '{"group_id": "abc123", "action": "pause"}'

# Перейти к следующему треку (требует настройки MA API)
curl -X POST http://localhost:8080/api/ma/queue/cmd \
  -H 'Content-Type: application/json' \
  -d '{"action": "next"}'

# Запустить BT-сканирование и опросить результат
JOB=$(curl -s -X POST http://localhost:8080/api/bt/scan | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
curl http://localhost:8080/api/bt/scan/result/$JOB

# Получить диагностику
curl http://localhost:8080/api/diagnostics | python3 -m json.tool

# Изменить уровень логирования
curl -X POST http://localhost:8080/api/settings/log_level \
  -H 'Content-Type: application/json' \
  -d '{"level": "debug"}'
```
