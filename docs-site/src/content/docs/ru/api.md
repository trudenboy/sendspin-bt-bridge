---
title: API Reference
description: REST API Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

Веб-интерфейс предоставляет REST API на порту `8080`. Все эндпоинты возвращают JSON.

<Aside type="caution">
  API не требует аутентификации. В сети `host` любой участник LAN имеет доступ. Не открывайте порт 8080 в интернет.
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

### `GET /api/diagnostics`

Структурированная диагностика: адаптеры, синки, D-Bus, статус каждого устройства.

### `GET /api/version`

```json
{ "version": "2.5.2", "build_date": "2025-07-01" }
```

### `GET /api/logs`

Последние строки лога приложения.

**Query параметры:**
- `lines` — количество строк (по умолчанию 100)

## Управление воспроизведением

### `POST /api/pause`

Пауза/воспроизведение для конкретного плеера.

**Body:** `{ "index": 0 }`

### `POST /api/pause_all`

Пауза/воспроизведение на всех плеерах.

### `POST /api/volume`

Установить громкость.

**Body:** `{ "index": 0, "volume": 75 }`

- `index` — индекс устройства в списке
- `volume` — 0–100

### `POST /api/mute`

Включить/выключить mute.

**Body:** `{ "index": 0 }`

## Bluetooth-управление

### `POST /api/bt/reconnect`

Принудительное переподключение BT устройства.

**Body:** `{ "index": 0 }`

### `POST /api/bt/pair`

Запустить процедуру паринга (~25 сек). Устройство должно быть в режиме паринга.

**Body:** `{ "index": 0 }`

### `POST /api/bt/management`

Переключить режим управления (Release/Reclaim).

**Body:** `{ "index": 0, "enabled": false }`

### `POST /api/bt/scan`

Сканирование BT-устройств (~10 сек).

**Ответ:**
```json
{
  "devices": [
    { "mac": "AA:BB:CC:DD:EE:FF", "name": "JBL Flip 5" }
  ]
}
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

**Body:** JSON объект с полями конфигурации (см. раздел [Настройка](/sendspin-bt-bridge/configuration/)).

## Сервис

### `POST /api/restart`

Перезапустить сервис. Поддерживаемые методы: `systemd`, `docker`, `ha`.

**Body:** `{ "method": "auto" }`

## Примеры использования

```bash
# Получить статус всех плееров
curl http://localhost:8080/api/status

# Установить громкость 50% на первом плеере
curl -X POST http://localhost:8080/api/volume \
  -H 'Content-Type: application/json' \
  -d '{"index": 0, "volume": 50}'

# Поставить на паузу все плееры
curl -X POST http://localhost:8080/api/pause_all

# Получить диагностику
curl http://localhost:8080/api/diagnostics | python3 -m json.tool
```
