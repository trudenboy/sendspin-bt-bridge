# Sendspin Bluetooth Bridge

[Read in English](README.md) · [📖 Документация](https://trudenboy.github.io/sendspin-bt-bridge/)

Bluetooth-мост для [Music Assistant](https://www.music-assistant.io/) — подключает Bluetooth-колонки к протоколу Sendspin в MA. Работает как Docker-контейнер, аддон для Home Assistant или нативный LXC-контейнер на Proxmox VE. Предназначен для систем без монитора.

## Возможности

- **Протокол Sendspin**: Полная поддержка нативного протокола потоковой передачи Music Assistant
- **Несколько устройств**: Одновременное подключение нескольких Bluetooth-колонок, каждая отображается как отдельный плеер в MA
- **Авто-переподключение**: Мониторинг соединений каждые 10 с с автоматическим переподключением
- **Веб-интерфейс в стиле HA**: Панель мониторинга оформлена в визуальном языке Home Assistant/Music Assistant — CSS design tokens, автоматическая тёмная/светлая тема, шрифт Roboto; живая инъекция темы при открытии через HA Ingress
- **Три варианта развёртывания**: Аддон Home Assistant, Docker Compose или Proxmox LXC
- **PipeWire и PulseAudio**: Автоматическое определение аудиосистемы хоста
- **Отображение аудио формата**: Кодек, частота дискретизации и битность отображаются для каждого устройства (например, `flac 48000Hz/24-bit/2ch`)
- **Групповое управление**: Регулировка громкости и отключение звука на нескольких плеерах из веб-интерфейса
- **Интеграция с MA API**: Расширенные данные воспроизведения (трек, исполнитель, альбом, позиция в очереди) и транспортные кнопки (предыдущий/следующий/shuffle/repeat) из MA REST API — работает как для участников syncgroup, так и для соло-плееров
- **Управление BT-адаптерами**: Автоматическое определение адаптеров с возможностью ручного выбора; привязка каждой колонки к конкретному адаптеру
- **Компенсация задержки**: Поле `static_delay_ms` компенсирует буферную задержку A2DP
- **Эндпоинт диагностики**: `/api/diagnostics` возвращает структурированную информацию о состоянии — адаптеры, синки, D-Bus, статус каждого устройства
- **Несколько экземпляров bridge**: Можно запустить несколько bridge (контейнеров/LXC/аддонов) против одного сервера MA — каждый регистрирует свои плееры независимо

<img width="1400" alt="Веб-панель мониторинга — полная страница, тёмная тема" src="https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/docs-site/public/screenshots/screenshot-dashboard-full.png" />
<br><br>
<img width="1019" height="245" alt="Плееры в MA" src="https://github.com/user-attachments/assets/7654570b-bada-4cca-a195-4ced53c8d398" />
<br><br>
<img width="733" height="782" alt="Плееры в MA" src="https://github.com/user-attachments/assets/8bbdd3b0-b61f-4139-a0f0-03d4b904d555" />

---

## Развёртывание нескольких bridge

Запустите несколько экземпляров bridge, указывающих на один сервер Music Assistant, чтобы охватить все комнаты — каждый bridge обслуживает колонки в пределах своей зоны Bluetooth.

[![Схема развёртывания: план этажа с зонами и адаптерами](https://trudenboy.github.io/sendspin-bt-bridge/diagrams/multiroom-diagram.png)](https://trudenboy.github.io/sendspin-bt-bridge/diagrams/multiroom-diagram/)

---

## Варианты развёртывания

| | Аддон Home Assistant | Docker Compose | Proxmox LXC |
|---|---|---|---|
| Установка | Магазин аддонов HA (один клик) | `docker compose up` | Однострочный скрипт |
| Bluetooth | bluetoothd хоста через D-Bus | bluetoothd хоста через D-Bus | Собственный bluetoothd внутри LXC |
| Аудио | Мост через HA Supervisor | PulseAudio/PipeWire хоста | Собственный PulseAudio внутри LXC |
| UI настройки | Панель HA + веб-интерфейс | Веб-интерфейс на :8080 | Веб-интерфейс на :8080 |
| Применение изменений | Перезапуск аддона | Перезапуск контейнера | `systemctl restart` |

---

## Вариант А — Аддон Home Assistant

### Установка

**1. Добавьте репозиторий аддонов в Home Assistant:**

[![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

Или вручную: **Настройки → Аддоны → Магазин аддонов → ⋮ → Репозитории** → добавьте `https://github.com/trudenboy/sendspin-bt-bridge`

**2.** Найдите **Sendspin Bluetooth Bridge** в магазине и нажмите **Установить**.

### Настройка

На вкладке **Конфигурация** аддона:

```yaml
sendspin_server: auto          # или hostname/IP вашего MA
sendspin_port: 9000
bluetooth_devices:
  - mac: "AA:BB:CC:DD:EE:FF"
    player_name: "Колонка в гостиной"
  - mac: "11:22:33:44:55:66"
    player_name: "Колонка на кухне"
    adapter: hci1              # опционально — только для конфигураций с несколькими адаптерами
    static_delay_ms: -500      # опционально — компенсация задержки A2DP в мс
```

Аддон предоставляет веб-интерфейс через **HA Ingress** (переброс портов не нужен) и отображается в боковой панели HA. Интерфейс автоматически подхватывает вашу тему HA (светлую/тёмную) через API postMessage `setTheme`.

### Требования

- Home Assistant OS или Supervised
- Bluetooth-адаптер, доступный хосту
- Сервер Music Assistant, работающий в сети (любой хост)

### Маршрутизация аудио (HA OS)

Аддон запрашивает `audio: true` в манифесте, поэтому HA Supervisor автоматически подставляет `PULSE_SERVER`. Ручная настройка сокетов не требуется.

---

## Вариант Б — Docker Compose

### Предварительные требования

- Docker и Docker Compose
- Bluetooth-колонки **сопряжены** с хостом до запуска контейнера
- Сервер Music Assistant, работающий в сети

### Сначала сопрягите колонки на хосте

```bash
bluetoothctl
scan on
pair  XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```

### docker-compose.yml

```yaml
services:
  sendspin-client:
    image: ghcr.io/trudenboy/sendspin-bt-bridge:latest
    container_name: sendspin-client
    restart: unless-stopped
    network_mode: host
    privileged: true

    volumes:
      - /var/run/dbus:/var/run/dbus
      - /run/user/1000/pulse:/run/user/1000/pulse   # PulseAudio
      - /etc/docker/Sendspin:/config

    environment:
      - SENDSPIN_SERVER=auto
      - TZ=Europe/Moscow
      - WEB_PORT=8080

    devices:
      - /dev/bus/usb:/dev/bus/usb

    cap_add:
      - NET_ADMIN
      - NET_RAW
      - SYS_ADMIN
```

Создайте директорию конфигурации и запустите:

```bash
sudo mkdir -p /etc/docker/Sendspin
docker compose up -d
```

> **Обновляетесь с более ранней версии?** Ранее образ публиковался как `ghcr.io/loryanstrant/sendspin-client`. Обновите файл compose или команду pull на `ghcr.io/trudenboy/sendspin-bt-bridge:latest`.

Откройте веб-интерфейс по адресу `http://IP-вашего-хоста:8080` для добавления Bluetooth-устройств и настройки сервера MA.

---

## Вариант В — Proxmox VE (LXC)

Запуск в виде **нативного LXC-контейнера** — Docker не нужен. Контейнер использует **`bluetoothd` хоста через D-Bus bridge** (AF_BLUETOOTH недоступен в LXC-пространствах имён), с `pulseaudio --system` и `avahi-daemon` внутри контейнера.

Полная документация, предварительные требования, шаги ручной установки, инструкции по сопряжению и команды мониторинга — в **[lxc/README.md](lxc/README.md)**.

### Установка одной командой (на хосте Proxmox от root)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh)
```

Либо скачайте и проверьте скрипт перед запуском:

```bash
curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/proxmox-create.sh -o proxmox-create.sh
less proxmox-create.sh
bash proxmox-create.sh
```

Скрипт интерактивно запрашивает ID контейнера, имя хоста, RAM, диск, сеть и проброску USB Bluetooth.

### Ручная установка (через интерфейс Proxmox)

1. Создайте новый **привилегированный** LXC-контейнер (**Ubuntu 24.04**, 512 МБ RAM, 4 ГБ диск)
2. Запустите контейнер и откройте оболочку (`pct enter <CTID>`)
3. Запустите установщик:
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)
   ```
4. Добавьте в `/etc/pve/lxc/<CTID>.conf` на **хосте Proxmox**:
   ```
   lxc.apparmor.profile: unconfined
   lxc.cgroup2.devices.allow: c 166:* rwm
   lxc.cgroup2.devices.allow: c 13:* rwm
   lxc.cgroup2.devices.allow: c 10:232 rwm
   lxc.mount.entry: /run/dbus bt-dbus none bind,create=dir 0 0
   lxc.cgroup2.devices.allow: c 189:* rwm
   ```
5. Перезапустите контейнер: `pct restart <CTID>`

### Сопряжение Bluetooth внутри LXC

```bash
pct enter <CTID>
btctl
power on
scan on
pair  XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
exit
```

Затем добавьте устройство в `/config/config.json` и перезапустите сервис:

```bash
pct exec <CTID> -- systemctl restart sendspin-client
```

### Основные команды мониторинга

```bash
pct exec <CTID> -- journalctl -u sendspin-client -f
pct exec <CTID> -- systemctl status sendspin-client pulseaudio-system avahi-daemon --no-pager
pct exec <CTID> -- pactl list sinks short
pct exec <CTID> -- btctl show
```

---

## Конфигурация

### Конфигурация нескольких устройств (`/config/config.json`)

Рекомендуемый способ настройки нескольких колонок:

```json
{
  "SENDSPIN_NAME": "Sendspin-Bridge",
  "SENDSPIN_SERVER": "auto",
  "SENDSPIN_PORT": "9000",
  "BLUETOOTH_DEVICES": [
    {
      "mac": "80:99:E7:C2:0B:D3",
      "player_name": "Гостиная",
      "adapter": "hci0"
    },
    {
      "mac": "FC:58:FA:EB:08:6C",
      "player_name": "Кухня",
      "adapter": "hci1"
    }
  ],
  "TZ": "Europe/Moscow"
}
```

Каждое устройство в `BLUETOOTH_DEVICES` запускает отдельный плеер Sendspin и менеджер Bluetooth. Оба отображаются как самостоятельные плееры в Music Assistant.

Поле `adapter` опционально — опустите его, если у вас один Bluetooth-адаптер. Укажите `hci0`, `hci1` и т.д. при наличии нескольких адаптеров, чтобы привязать колонку к конкретному.

### Конфигурация одного устройства (поддерживается)

```json
{
  "SENDSPIN_SERVER": "auto",
  "BLUETOOTH_MAC": "AA:BB:CC:DD:EE:FF"
}
```

### Переменные окружения

| Переменная | По умолчанию | Описание |
|----------|---------|-------------|
| `SENDSPIN_NAME` | `Docker-{hostname}` | Базовое имя плеера в Music Assistant |
| `SENDSPIN_SERVER` | `auto` | Hostname/IP сервера MA; `auto` — обнаружение через mDNS |
| `SENDSPIN_PORT` | `9000` | Порт WebSocket Sendspin в MA |
| `BLUETOOTH_MAC` | `` | MAC одной колонки (устарело; используйте `BLUETOOTH_DEVICES` в config.json) |
| `TZ` | `Australia/Melbourne` | Часовой пояс контейнера |
| `WEB_PORT` | `8080` | Порт веб-интерфейса |
| `MA_API_URL` | `` | Базовый URL REST API Music Assistant (напр. `http://192.168.1.10:8123`) — включает метаданные воспроизведения и транспортные кнопки |
| `MA_API_TOKEN` | `` | Долгосрочный токен доступа HA для MA API |

Переменные окружения перекрываются значениями из `/config/config.json`, если файл существует.

### Веб-интерфейс

Веб-интерфейс по адресу `http://ваш-хост:8080` (или через HA Ingress) предоставляет:

- **Дизайн в стиле HA**: CSS custom properties с design tokens HA; автоматическая тёмная/светлая тема через `prefers-color-scheme`; живая синхронизация темы при открытии через HA Ingress
- **Карточки устройств**: Статус соединения, состояние воспроизведения, аудио формат (кодек/частота/битность), статус синхронизации, регулятор громкости, отключение звука
- **Кнопки переподключения/перепривязки**: Ручное переподключение или полная перепривязка без перезапуска
- **Групповое управление**: Установка громкости или отключение звука на всех устройствах сразу
- **BT-сканирование**: Поиск ближайших аудиоустройств и добавление в конфиг прямо из интерфейса
- **Панель BT-адаптеров**: Просмотр определённых адаптеров, выбор адаптера для устройства
- **Страница настройки**: Редактирование адреса сервера, добавление/удаление Bluetooth-устройств, автодополнение часовых поясов IANA
- **Системная информация**: IP-адрес, имя хоста, время работы, статус аудио-синка, WebSocket URL каждого плеера

---

## Архитектура

Bridge работает как **многопроцессное приложение**: один главный процесс управляет Bluetooth, веб-API и интеграцией с Music Assistant, тогда как каждая настроенная колонка получает собственный изолированный subprocess с выделенным контекстом PulseAudio (`PULSE_SINK`).

```
┌─────────────────────────────────────┐
│     Сервер Music Assistant          │
│  (встроенный провайдер Sendspin)    │
└──────────────┬──────────────────────┘
               │ WebSocket ws://<ma>:9000/sendspin
               │
┌──────────────▼──────────────────────┐
│     Sendspin Bluetooth Bridge       │
│                                     │
│  ┌─────────────────────────────┐    │
│  │  SendspinClient (на устр.)  │    │
│  │  · daemon-subprocess        │    │
│  │    (PULSE_SINK=bt_sink)     │    │
│  │  · отслеживание воспр.      │    │
│  │  · синхронизация громкости  │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │  BluetoothManager           │    │
│  │  · интерфейс bluetoothctl   │    │
│  │  · авто-переподкл. каждые 10с│   │
│  │  · синк PipeWire/PulseAudio │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │  Веб-интерфейс (Flask/8080) │    │
│  │  · панель мониторинга       │    │
│  │  · редактор конфигурации    │    │
│  │  · групповое управление     │    │
│  └─────────────────────────────┘    │
└──────────┬──────────────────────────┘
           │ Bluetooth A2DP
    ┌──────┴──────┐
    │  Колонка 1  │  Колонка 2  │  ...
    └─────────────┘
```

📖 **Полная документация по архитектуре** с диаграммами Mermaid — модель процессов, IPC-протокол, маршрутизация аудио, конечный автомат Bluetooth, интеграция с MA, аутентификация и graceful degradation:
**[trudenboy.github.io/sendspin-bt-bridge/architecture/](https://trudenboy.github.io/sendspin-bt-bridge/architecture/)**

---

## Устранение неисправностей

### Bluetooth-колонка не подключается

```bash
# Docker
docker exec -it sendspin-client bluetoothctl info XX:XX:XX:XX:XX:XX

# Сопряжение вручную
docker exec -it sendspin-client bluetoothctl
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX

# Проверка логов
docker logs -f sendspin-client
```

### Не удаётся подключиться к Music Assistant

1. Проверьте правильность `SENDSPIN_SERVER` или используйте `auto`
2. Убедитесь, что установлен `network_mode: host` (обязательно для mDNS)
3. Проверьте логи MA на наличие входящих Sendspin-соединений
4. Убедитесь, что порт 9000 доступен с хоста

### Проблемы с маршрутизацией аудио (Docker)

Контейнеру необходим доступ к аудио-сокету хоста. Добавьте в `docker-compose.yml`:

```yaml
volumes:
  - /run/user/1000/pulse:/run/user/1000/pulse
environment:
  - PULSE_SERVER=unix:/run/user/1000/pulse/native
```

Для PipeWire:
```yaml
volumes:
  - /run/user/1000/pipewire-0:/run/user/1000/pipewire-0
```

### Диагностика

`GET /api/diagnostics` возвращает JSON-снимок состояния системы — определённые адаптеры, PulseAudio-синки, доступность D-Bus и статус каждого устройства. Удобно для удалённой отладки без доступа к оболочке.

```bash
curl http://ваш-хост:8080/api/diagnostics | python3 -m json.tool
```

### Нет аудио-синка после подключения Bluetooth

Приложение автоматически проверяет несколько шаблонов именования:
- `bluez_output.{MAC}.1` (PipeWire)
- `bluez_output.{MAC}.a2dp-sink`
- `bluez_sink.{MAC}.a2dp_sink` (PulseAudio)
- `bluez_sink.{MAC}`

Выведите список доступных синков, чтобы проверить активный:

```bash
docker exec -it sendspin-client pactl list short sinks
```

---

## Разработка

```bash
# Клонирование
git clone https://github.com/trudenboy/sendspin-bt-bridge.git
cd sendspin-bt-bridge

# Сборка и локальный запуск
docker compose up --build

# Просмотр логов
docker logs -f sendspin-client

# Запуск без Docker (требуются системные пакеты BT/аудио)
pip install -r requirements.txt
python sendspin_client.py
```

### Структура проекта

| Файл / Директория | Назначение |
|------|---------|
| `sendspin_client.py` | Основная оркестрация — инициализация `SendspinClient`, `BluetoothManager`, `main()` |
| `bluetooth_manager.py` | `BluetoothManager` — сопряжение/подключение/переподключение через subprocess `bluetoothctl` |
| `config.py` | Управление конфигурацией — `load_config()`, `DEFAULT_CONFIG`, `VERSION`, вспомогательные функции auth |
| `state.py` | Общее состояние рантайма — список клиентов, SSE-сигнализация, задачи сканирования, кэш MA |
| `mpris.py` | MPRIS D-Bus интеграция — регистрирует каждый плеер в медиашине хоста |
| `web_interface.py` | Точка входа Flask — регистрирует blueprints, запускает сервер Waitress |
| `routes/api.py` | Все REST-эндпоинты `/api/*` |
| `routes/views.py` | Рендеринг HTML-страниц |
| `routes/auth.py` | Опциональная защита веб-интерфейса паролем |
| `services/daemon_process.py` | Точка входа subprocess — каждая колонка работает здесь с собственным `PULSE_SINK` |
| `services/bridge_daemon.py` | Подкласс `BridgeDaemon` — обрабатывает события Sendspin внутри subprocess |
| `services/ma_monitor.py` | Постоянный WebSocket-монитор MA — подписывается на события `player_queue_updated` |
| `services/ma_client.py` | Вспомогательные функции MA REST API — обнаружение групп, групповое воспроизведение |
| `services/bluetooth.py` | BT-утилиты — `bt_remove_device()`, `persist_device_enabled()` |
| `services/pulse.py` | Async-утилиты PulseAudio — обнаружение синков, коррекция маршрутизации потоков |
| `scripts/translate_ha_config.py` | Транслятор options.json → config.json для HA аддона (вызывается из entrypoint.sh) |
| `entrypoint.sh` | Запуск контейнера — D-Bus, определение аудио-сокета, трансляция конфига HA, запуск приложения |
| `Dockerfile` | Образ контейнера |
| `docker-compose.yml` | Оркестрация Docker Compose |
| `ha-addon/config.yaml` | Манифест аддона Home Assistant |
| `ha-addon/Dockerfile` | Образ аддона HA (тонкая обёртка над основным образом) |
| `ha-addon/run.sh` | Точка входа HA |
| `ha-addon/translations/en.yaml` | Метки UI для HA |
| `lxc/` | Скрипты установки Proxmox LXC |
| `docs-site/` | Документационный сайт Astro Starlight (деплоится на GitHub Pages) |

---

## Участие в разработке

1. Сделайте форк репозитория
2. Создайте ветку с функциональностью от `integration`
3. Внесите изменения
4. Отправьте pull request против `integration`

---

## Благодарности

- Создано для [Music Assistant](https://www.music-assistant.io/)
- Использует CLI `sendspin` из проекта MA
- Вдохновлено [sendspin-go](https://github.com/Sendspin/sendspin-go)
- Изначально форкнут из [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client)
- Создано по результатам обсуждения в сообществе MA: [Sendspin Bluetooth Bridge #4677](https://github.com/orgs/music-assistant/discussions/4677)

## Поддержка

- **Проблемы**: [GitHub Issues](https://github.com/trudenboy/sendspin-bt-bridge/issues)
- **Исходное обсуждение**: [music-assistant/discussions #4677](https://github.com/orgs/music-assistant/discussions/4677)
- **Сообщество Music Assistant**: [Discord](https://discord.gg/kaVm8hGpne)

## Лицензия

Лицензия MIT — подробнее в файле [LICENSE](LICENSE).

---

## История изменений

Полная история версий в [CHANGELOG.md](CHANGELOG.md).

Нарративная история развития проекта (архитектурные решения, вехи, миграция v1 → v2) — в [HISTORY.ru.md](HISTORY.ru.md).
