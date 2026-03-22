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
| **General** | Имя bridge, timezone, latency, web/UI-порты, smooth restart, update policy |
| **Devices** | Таблица колонок, сканирование, импорт paired-устройств |
| **Bluetooth** | Имена адаптеров, reconnect policy, codec preference |
| **Music Assistant** | Token flows, endpoint MA, monitor, routing toggles |
| **Security** | Local auth, session timeout, защита от перебора |

### Вкладка General

**General** содержит настройки всего экземпляра bridge:

- **Bridge name** — добавляется к именам плееров как `Player @ Name`.
- **Timezone** — с live preview текущего времени.
- **PulseAudio latency (ms)** — больше значение = выше устойчивость на слабом железе.
- **Web UI port** — порт прямого доступа в браузере для standalone или дополнительный прямой порт в режиме HA addon.
- **Base player listen port** — стартовый порт для автоматически назначаемых sendspin listener'ов устройств.
- **Smooth restart** — mute перед перезапуском и показ прогресса.
- **Check for updates / Auto-update** — доступны вне режима HA addon.

Если оставить поля портов пустыми, standalone-режим использует **8080** для web UI и **8928** для listener'ов плееров. В режиме Home Assistant addon основные channel defaults остаются фиксированными: **8080 / 8928** для stable, **8081 / 9028** для rc и **8082 / 9128** для beta. Если в addon-режиме задать другой `WEB_PORT`, bridge откроет **дополнительный прямой listener**, а HA Ingress продолжит работать через фиксированный порт канала.

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
| **Port** | Опциональный `listen_port`; иначе bridge использует `BASE_LISTEN_PORT + индекс устройства` |
| **Delay** | `static_delay_ms` компенсация задержки |
| **Live** | Runtime badge вроде Playing, Connected, Released или Not seen |

В advanced-части строки доступны:

- **Preferred format** вроде `flac:44100:16:2`.
- **Listen host** (`listen_host`) для переопределения advertised-адреса устройства.
- **Keepalive interval** (`keepalive_interval`) для колонок, которые слишком быстро уходят в сон.

Текущее runtime-поведение завязано на интервале: любой положительный `keepalive_interval` включает keepalive-тишину, значения меньше 30 секунд поднимаются до 30, а `0` или пустое поле keepalive выключают. В старых конфигурациях Home Assistant addon ещё может встречаться legacy-флаг `keepalive_silence`, но актуальное поведение bridge определяется через `keepalive_interval > 0`.

### Вкладка Bluetooth

![Вкладка Bluetooth с inventory адаптеров и recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

**Bluetooth** объединяет inventory и политику восстановления:

- Понятные имена адаптеров для dashboard.
- Ручные записи адаптеров, если автоопределение неполное.
- Refresh detection.
- Настройку **BT check interval**.
- Настройку **Auto-disable threshold**. При достижении порога устройство сохраняется как disabled, пока вы не включите его снова.
- Переключатель **Prefer SBC codec**.

### Вкладка Music Assistant

![Карточка Connection status в Music Assistant с действием Reconfigure и текущим состоянием интеграции](/sendspin-bt-bridge/screenshots/screenshot-ma-connection-status.png)

![Вкладка Music Assistant с token actions и bridge integration settings](/sendspin-bt-bridge/screenshots/screenshot-advanced-settings.png)

**Music Assistant** объединяет состояние соединения и auth helper'ы:

- summary **Connection status**,
- действие **Reconfigure** прямо в карточке статуса соединения — основной способ осознанно переоткрыть auth-flow после первичной настройки,
- **Discover** для поиска сервера или повторного использования уже известного URL,
- **Get token** с логином/паролем MA. При успехе bridge сохраняет `MA_API_URL`, long-lived `MA_API_TOKEN` и `MA_USERNAME`, но **не** сохраняет пароль,
- fallback через **Home Assistant OAuth / MFA**, если экземпляр MA работает поверх HA и прямой логин MA этого требует,
- **Get token automatically** для HA-backed MA. Этот helper показывается только в addon/Ingress-режиме, потому что silent bootstrap токена зависит от живой browser-session Home Assistant. В HA Ingress UI сначала пробует silent auth через текущий HA browser token, а затем при необходимости переходит к popup-flow,
- ручное поле **MA API token**,
- поля **MA server** и **MA WebSocket port**,
- переключатели **WebSocket monitor**, **Route volume through MA**, **Route mute through MA**.

Когда bridge уже подключён, auth controls убираются с глаз до тех пор, пока вы не нажмёте **Reconfigure** или не попадёте сюда по guidance-CTA, который явно просит обновить токен.

### Вкладка Security

В standalone-режиме вкладка **Security** управляет локальным доступом к веб-интерфейсу:

- **Enable web UI authentication**,
- **Session timeout** (1–168 часов),
- **Brute-force protection**,
- поля **Max attempts**, **Window** и **Lockout**,
- flow **Set password**.

Standalone-login использует CSRF-защищённые формы и cookie с `SameSite=Lax` + `HttpOnly`. В режиме Home Assistant addon доступ всегда контролирует сам HA / Ingress, поэтому standalone-контролы скрываются.

### Действия в footer

Нижняя панель общая для всех вкладок:

- **Save** записывает `config.json`.
- **Save & Restart** сохраняет и сразу перезапускает сервис. Используйте это для изменений портов, auth/session-настроек и любых параметров, применяемых на старте.
- **Cancel** восстанавливает последние сохранённые значения формы.
- **Download** экспортирует share-safe JSON-файл с timestamp и без секретов вроде `MA_API_TOKEN`, password hash и secret key.
- **Upload** импортирует ранее сохранённый config и сохраняет существующие password hash, secret key и MA token на стороне сервера.

## Параметры аддона Home Assistant

![Панель Configuration аддона HA с core options](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config.png)

В режиме аддона Home Assistant Supervisor показывает собственную вкладку **Configuration**.

Откройте **Настройки → Аддоны → Sendspin Bluetooth Bridge → Configuration**.

Основные поля аддона:

| Параметр | Назначение |
|---|---|
| **sendspin_server** | Хост/IP Music Assistant или `auto` для mDNS |
| **sendspin_port** | Порт Sendspin WebSocket, обычно `9000` |
| **web_port** | Необязательный прямой host-network web-порт; Ingress продолжает использовать фиксированный addon-порт |
| **base_listen_port** | Стартовый порт для автоматически назначаемых listener'ов устройств |
| **bridge_name** | Необязательная метка экземпляра bridge |
| **tz** | Часовой пояс IANA |
| **pulse_latency_msec** | Подсказка размера аудиобуфера |
| **prefer_sbc_codec** | Предпочтение более лёгкого кодека |
| **bt_check_interval** | Интервал polling-проверки |
| **bt_max_reconnect_fails** | Порог auto-disable |
| **auth_enabled** | Standalone-style auth toggle для прямого доступа; в HA addon mode auth всё равно принудительно контролируется HA |
| **ma_api_url / ma_api_token** | REST-интеграция с Music Assistant |
| **volume_via_ma / mute_via_ma** | Маршрутизация управления через MA |
| **update_channel** | Выбор release-lane для in-app update checks и предупреждений |
| **log_level** | Базовый уровень логирования |

![Списки устройств и адаптеров аддона HA вместе с диалогом редактирования устройства](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-config-bottom.png)

![Диалог редактирования устройства в конфигурации аддона HA](/sendspin-bt-bridge/screenshots/screenshot-ha-addon-device-edit.png)

Через addon form доступны и полные структуры **Bluetooth devices** и **Bluetooth adapters**. Старые addon-конфиги могут по-прежнему сохранять legacy-поле `keepalive_silence` при трансляции, но текущее runtime-поведение определяется через `keepalive_interval`.

Что важно для addon-режима:

- auth в Home Assistant / Ingress всегда принудительно включена;
- пользовательский `web_port` добавляет только дополнительный прямой listener и не переносит HA Ingress;
- silent bootstrap токена HA для Music Assistant доступен только при открытом UI через аутентифицированную HA/Ingress browser-session.

Что важно для addon-режима:

- auth в Home Assistant / Ingress всегда принудительно включена;
- пользовательский `web_port` добавляет только дополнительный прямой listener и не переносит HA Ingress;
- silent bootstrap токена HA для Music Assistant доступен только при открытом UI через аутентифицированную HA/Ingress browser-session.

## Стратегия портов и listener'ов

### Верхнеуровневые web- и listen-порты

Bridge поддерживает два необязательных top-level override-поля:

| Ключ | К чему применяется | Значение по умолчанию | Что важно |
|---|---|---|---|
| `WEB_PORT` | listener веб-интерфейса | `8080` вне HA addon mode | В режиме Home Assistant addon фиксированный ingress listener продолжает работать на дефолтном порту трека аддона; настроенный `WEB_PORT` открывает только дополнительный прямой listener. |
| `BASE_LISTEN_PORT` | автоматически назначаемые per-device Sendspin listener'ы | `8928` вне HA addon mode | Используется как стартовый порт, если устройство не задаёт собственный `listen_port`. |

В Home Assistant addon mode дефолтные значения специально разведены, чтобы избежать коллизий, если на одном HAOS-хосте работают разные варианты аддона:

| Установленный трек аддона | Порт ingress / web по умолчанию | Base listen port по умолчанию |
|---|---|---|
| Stable | `8080` | `8928` |
| RC | `8081` | `9028` |
| Beta | `8082` | `9128` |

Используйте overrides, если вам нужен:

- прямой не-Ingress web listener в addon-режиме;
- нестандартный web-порт для Docker/LXC/systemd;
- отдельный диапазон listener'ов для большого числа устройств на одном хосте.

### Персональные listener overrides устройств

Каждое Bluetooth-устройство может также задавать собственные `listen_host` и `listen_port`.

Используйте device-level overrides, если:

- конкретной колонке нужен стабильный известный порт;
- вы делите устройства между несколькими bridge-экземплярами и хотите явный план портов;
- нужно убрать коллизию, не двигая весь базовый диапазон bridge.

## Справочник по `config.json`

### Основные ключи

| Ключ | Тип | Описание |
|---|---|---|
| `SENDSPIN_SERVER` | string | Хост Music Assistant или `auto` |
| `SENDSPIN_PORT` | integer | Порт Sendspin WebSocket |
| `WEB_PORT` | integer или `null` | Override прямого web-порта |
| `BASE_LISTEN_PORT` | integer или `null` | Необязательный base port для автоматически назначаемых listener'ов устройств |
| `BRIDGE_NAME` | string | Необязательная метка экземпляра |
| `TZ` | string | Часовой пояс IANA |
| `PULSE_LATENCY_MSEC` | integer | Подсказка аудиобуфера |
| `BT_CHECK_INTERVAL` | integer | Интервал проверки Bluetooth |
| `BT_MAX_RECONNECT_FAILS` | integer | Порог auto-disable |
| `PREFER_SBC_CODEC` | boolean | Предпочтение кодека с меньшей нагрузкой |
| `AUTH_ENABLED` | boolean | Включить локальную auth-защиту вне HA addon mode; в HA addon mode auth всегда принудительно включена |
| `SESSION_TIMEOUT_HOURS` | integer | Срок жизни browser-сессии |
| `BRUTE_FORCE_PROTECTION` | boolean | Включить временную блокировку после неудачных входов |
| `BRUTE_FORCE_MAX_ATTEMPTS` | integer | Максимум попыток в окне |
| `BRUTE_FORCE_WINDOW_MINUTES` | integer | Rolling window для неудачных входов |
| `BRUTE_FORCE_LOCKOUT_MINUTES` | integer | Длительность блокировки |
| `MA_API_URL` | string | URL REST API Music Assistant |
| `MA_API_TOKEN` | string | Токен Music Assistant API |
| `MA_USERNAME` | string | Username, использованный при последнем успешном MA login-flow |
| `MA_WEBSOCKET_MONITOR` | boolean | Live monitor now-playing и очереди |
| `VOLUME_VIA_MA` | boolean | Пропускать volume через MA |
| `MUTE_VIA_MA` | boolean | Пропускать mute через MA |
| `SMOOTH_RESTART` | boolean | Mute перед перезапуском и показ прогресса |
| `UPDATE_CHANNEL` | string | Канал обновлений: `stable`, `rc` или `beta` |
| `AUTO_UPDATE` | boolean | Разрешить auto-update там, где он поддерживается |
| `CHECK_UPDATES` | boolean | Включить проверку обновлений |
| `LOG_LEVEL` | string | Базовый уровень логирования |
| `TRUSTED_PROXIES` | array | Дополнительные proxy IP, которым разрешено передавать trusted Ingress headers |

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
| `listen_host` | Переопределение advertised host для listener'а этого устройства |
| `listen_port` | Пользовательский порт listener'а; если не задан, runtime использует `BASE_LISTEN_PORT + индекс устройства` |
| `preferred_format` | Предпочтительный аудиоформат |
| `keepalive_silence` | Legacy-совместимый флаг из старых addon-конфигов; отдельного переключателя для него в текущем web UI нет |
| `keepalive_interval` | Интервал keepalive-тишины в секундах; любое положительное значение включает keepalive, минимальный эффективный интервал — 30 секунд |
| `enabled` | При `false` устройство пропускается на старте |

Каждый эффективный `listen_port` должен быть уникальным для устройства. Если на одном хосте работает несколько bridge-экземпляров, задайте им разные диапазоны `BASE_LISTEN_PORT` или явные `listen_port` для каждого устройства.

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

## Планирование портов и особенности HA Ingress

- **HA Ingress** продолжает использовать addon channel port даже если вы настроили собственный `WEB_PORT`.
- **Multi-bridge setups** должны использовать непересекающиеся диапазоны `WEB_PORT` и `BASE_LISTEN_PORT`.
- **Per-device overrides важнее**: `listen_port` и `listen_host` перекрывают top-level defaults.
- **Конфликт портов критичен для daemon**: дублирующиеся `listen_port` не дадут listener'у устройства забиндиться.

## Переменные окружения

Bridge напрямую читает небольшой набор runtime/bootstrap-переменных окружения. `CONFIG_DIR` всегда определяет, где находится `config.json`, а env-override для `WEB_PORT` / `BASE_LISTEN_PORT` разрешаются раньше сохранённых значений конфига.

| Переменная | Описание |
|---|---|
| `WEB_PORT` | Override прямого web UI порта. В standalone default — `8080`; addon channels сохраняют фиксированные primary ports и могут открыть этот порт дополнительно |
| `BASE_LISTEN_PORT` | Стартовый порт для auto-assigned player listeners. Stable default — `8928`, rc — `9028`, beta — `9128` |
| `TZ` | Override часового пояса, используемый при инициализации локального времени runtime |
| `BRIDGE_NAME` | Необязательный override имени bridge до появления сохранённого имени |
| `CONFIG_DIR` | Путь к директории конфига |
