---
title: Настройка
description: Актуальный справочник по конфигурации Sendspin Bluetooth Bridge после редизайна интерфейса
---

Sendspin Bluetooth Bridge хранит постоянные настройки в `config.json` внутри директории `/config`. Управлять этими значениями можно через веб-интерфейс, через вкладку настройки аддона Home Assistant или напрямую через файл.

## Поверхности конфигурации

Есть два основных способа управлять настройками:

| Поверхность | Для чего подходит | Что важно |
|---|---|---|
| **Веб-интерфейс** | Ежедневное управление устройствами, адаптерами, auth и runtime-поведением | Работает для Docker, LXC и аддона |
| **Configuration таб аддона HA** | Supervisor-managed параметры аддона | Удобен для HA-native редактирования |

## Конфигурация через веб-интерфейс

![Общий вид обновлённого раздела Configuration](/sendspin-bt-bridge/screenshots/screenshot-config.png)

Новый раздел конфигурации разбит на пять вкладок вместо одной длинной формы.

| Вкладка | Что в ней находится |
|---|---|
| **General** | Имя bridge, timezone, latency, smooth restart, update policy |
| **Devices** | Таблица колонок, сканирование, импорт paired-устройств |
| **Bluetooth** | Имена адаптеров, reconnect policy, codec preference |
| **Music Assistant** | Token flows, endpoint MA, monitor, routing toggles |
| **Security** | Local auth, session timeout, защита от перебора |

### Вкладка General

**General** содержит настройки всего экземпляра bridge:

- **Bridge name** — добавляется к именам плееров как `Player @ Name`.
- **Timezone** — с live preview текущего времени.
- **PulseAudio latency (ms)** — больше значение = выше устойчивость на слабом железе.
- **Smooth restart** — mute перед перезапуском и показ прогресса.
- **Check for updates / Auto-update** — доступны вне режима HA addon.

### Вкладка Devices

![Вкладка Devices с таблицей fleet и discovery workflow](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

**Devices** разделена на две роли:

- **Device fleet** — основная таблица повседневных изменений.
- **Discovery & import** — поиск nearby speakers и импорт уже спаренных устройств.

Каждая строка устройства хранит:

| Поле | Для чего нужно |
|---|---|
| **Enabled** | Временно исключить устройство из старта |
| **Player name** | Имя в Music Assistant |
| **MAC** | Bluetooth-адрес колонки |
| **Adapter** | Привязка к конкретному контроллеру |
| **Port** | Опциональный custom sendspin port |
| **Delay** | `static_delay_ms` компенсация задержки |
| **Live** | Runtime badge вроде Playing, Connected, Released или Not seen |

В advanced-части строки доступны **preferred format**, **listen host** и **keepalive interval**.

### Вкладка Bluetooth

![Вкладка Bluetooth с inventory адаптеров и recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

**Bluetooth** объединяет inventory и политику восстановления:

- Понятные имена адаптеров для dashboard.
- Ручные записи адаптеров, если автоопределение неполное.
- Refresh detection.
- Настройку **BT check interval**.
- Настройку **Auto-disable threshold**.
- Переключатель **Prefer SBC codec**.

### Вкладка Music Assistant

![Вкладка Music Assistant с token actions и bridge integration settings](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

**Music Assistant** объединяет состояние соединения и auth helper'ы:

- summary **Connection status**,
- кнопки **Discover** и **Get token**,
- **Get token automatically** в режиме аддона,
- ручное поле **MA API token**,
- поля **MA server** и **MA WebSocket port**,
- переключатели **WebSocket monitor**, **Route volume through MA**, **Route mute through MA**.

### Вкладка Security

В standalone-режиме вкладка **Security** управляет локальным доступом к веб-интерфейсу:

- **Enable web UI authentication**,
- **Session timeout**,
- **Brute-force protection**,
- поля **Max attempts**, **Window** и **Lockout**,
- flow **Set password**.

В режиме Home Assistant addon этими правилами управляет сам HA, поэтому standalone-контролы скрываются.

### Действия в footer

Нижняя панель общая для всех вкладок:

- **Save** записывает `config.json`.
- **Save & Restart** сохраняет и сразу перезапускает сервис.
- **Cancel** восстанавливает последние сохранённые значения формы.
- **Download** экспортирует конфиг в JSON-файл с timestamp.
- **Upload** импортирует ранее сохранённый config и сохраняет чувствительные ключи на стороне сервера.

## Параметры аддона Home Assistant

![Панель Configuration аддона HA с core options](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config.png)

В режиме аддона Home Assistant Supervisor показывает собственную вкладку **Configuration**.

Откройте **Настройки → Аддоны → Sendspin Bluetooth Bridge → Configuration**.

Основные поля аддона:

| Параметр | Назначение |
|---|---|
| **sendspin_server** | Хост/IP Music Assistant или `auto` для mDNS |
| **sendspin_port** | Порт Sendspin WebSocket, обычно `9000` |
| **bridge_name** | Необязательная метка экземпляра bridge |
| **tz** | Часовой пояс IANA |
| **pulse_latency_msec** | Подсказка размера аудиобуфера |
| **prefer_sbc_codec** | Предпочтение более лёгкого кодека |
| **bt_check_interval** | Интервал polling-проверки |
| **bt_max_reconnect_fails** | Порог auto-disable |
| **auth_enabled** | Включение auth flow bridge |
| **ma_api_url / ma_api_token** | REST-интеграция с Music Assistant |
| **volume_via_ma / mute_via_ma** | Маршрутизация управления через MA |
| **log_level** | Базовый уровень логирования |

![Списки устройств и адаптеров аддона HA вместе с диалогом редактирования устройства](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config-bottom.png)

![Диалог редактирования устройства в конфигурации аддона HA](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-device-edit.png)

Через addon form доступны и полные структуры **Bluetooth devices** и **Bluetooth adapters**.

## Справочник по `config.json`

### Основные ключи

| Ключ | Тип | Описание |
|---|---|---|
| `SENDSPIN_SERVER` | string | Хост Music Assistant или `auto` |
| `SENDSPIN_PORT` | integer | Порт Sendspin WebSocket |
| `BRIDGE_NAME` | string | Необязательная метка экземпляра |
| `TZ` | string | Часовой пояс IANA |
| `PULSE_LATENCY_MSEC` | integer | Подсказка аудиобуфера |
| `BT_CHECK_INTERVAL` | integer | Интервал проверки Bluetooth |
| `BT_MAX_RECONNECT_FAILS` | integer | Порог auto-disable |
| `PREFER_SBC_CODEC` | boolean | Предпочтение кодека с меньшей нагрузкой |
| `AUTH_ENABLED` | boolean | Включить локальную auth-защиту |
| `SESSION_TIMEOUT_HOURS` | integer | Срок жизни browser-сессии |
| `BRUTE_FORCE_PROTECTION` | boolean | Включить временную блокировку после неудачных входов |
| `BRUTE_FORCE_MAX_ATTEMPTS` | integer | Максимум попыток в окне |
| `BRUTE_FORCE_WINDOW_MINUTES` | integer | Rolling window для неудачных входов |
| `BRUTE_FORCE_LOCKOUT_MINUTES` | integer | Длительность блокировки |
| `MA_API_URL` | string | URL REST API Music Assistant |
| `MA_API_TOKEN` | string | Токен Music Assistant API |
| `MA_WEBSOCKET_MONITOR` | boolean | Live monitor now-playing и очереди |
| `VOLUME_VIA_MA` | boolean | Пропускать volume через MA |
| `MUTE_VIA_MA` | boolean | Пропускать mute через MA |
| `LOG_LEVEL` | string | Базовый уровень логирования |

### Bluetooth-устройства

```json
{
  "BLUETOOTH_DEVICES": [
    {
      "mac": "AA:BB:CC:DD:EE:FF",
      "player_name": "Колонка в гостиной",
      "adapter": "hci0",
      "static_delay_ms": -500,
      "listen_host": "0.0.0.0",
      "listen_port": 8928,
      "preferred_format": "flac:44100:16:2",
      "keepalive_interval": 60,
      "enabled": true
    }
  ]
}
```

| Поле | Описание |
|---|---|
| `mac` | Bluetooth MAC колонки |
| `player_name` | Имя в Music Assistant |
| `adapter` | ID или MAC адаптера |
| `static_delay_ms` | Фиксированная компенсация задержки |
| `listen_host` | Advertised host для listener этого устройства |
| `listen_port` | Пользовательский порт listener'а |
| `preferred_format` | Предпочтительный аудиоформат |
| `keepalive_interval` | Интервал keepalive-тишины в секундах |
| `enabled` | При `false` устройство пропускается на старте |

### Bluetooth-адаптеры

```json
{
  "BLUETOOTH_ADAPTERS": [
    {
      "id": "hci0",
      "mac": "C0:FB:F9:62:D6:9D",
      "name": "Адаптер в гостиной"
    }
  ]
}
```

| Поле | Описание |
|---|---|
| `id` | Имя интерфейса, например `hci0` |
| `mac` | MAC-адрес адаптера |
| `name` | Понятная метка в UI |

## Переменные окружения

Переменные окружения по-прежнему удобны для bootstrap и automation. Если есть `config.json`, его значения имеют приоритет.

| Переменная | Описание |
|---|---|
| `SENDSPIN_SERVER` | Хост Music Assistant |
| `SENDSPIN_PORT` | Порт Sendspin WebSocket |
| `WEB_PORT` | Порт веб-интерфейса (по умолчанию `8080`) |
| `TZ` | Часовой пояс |
| `CONFIG_DIR` | Путь к директории конфига |
