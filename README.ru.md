# [Sendspin](https://www.sendspin-audio.com/) Bluetooth Bridge

[![GitHub Release](https://img.shields.io/github/v/release/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/releases/latest)
[![Docker Pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Ftrudenboy%2Fsendspin-bt-bridge%2Fsendspin-bt-bridge&query=downloadCount&label=Docker%20Pulls&logo=docker&color=blue)](https://github.com/trudenboy/sendspin-bt-bridge/pkgs/container/sendspin-bt-bridge)
[![HA Installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Faddons.json&query=%24%5B%2285b1ecde_sendspin_bt_bridge%22%5D.total&label=HA%20Installs&logo=homeassistant&color=18bcf2)](https://analytics.home-assistant.io/apps/)
[![GitHub Stars](https://img.shields.io/github/stars/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/stargazers)

[Read in English](README.md) · [📖 Документация](https://trudenboy.github.io/sendspin-bt-bridge/) · [📋 История](HISTORY.ru.md)

Bluetooth-мост для [Music Assistant](https://www.music-assistant.io/) — подключает Bluetooth-колонки к протоколу [Sendspin](https://www.music-assistant.io/player-support/sendspin/) в MA. Работает как Docker-контейнер, аддон для Home Assistant или нативный LXC-контейнер на Proxmox VE / OpenWrt. Предназначен для систем без монитора.

<img width="1400" alt="Инфографика Sendspin Bluetooth Bridge — возможности, архитектура и варианты развёртывания" src="https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/docs-site/public/screenshots/sbb_infographic_ru.png" />

---

<details>
<summary><strong>📑 Содержание</strong></summary>

- [Возможности](#возможности)
- [Развёртывание нескольких bridge](#развёртывание-нескольких-bridge)
- [Протестированное оборудование](#протестированное-оборудование)
- [Варианты развёртывания](#варианты-развёртывания)
  - [Вариант А — Аддон Home Assistant](#вариант-а--аддон-home-assistant)
  - [Вариант Б — Docker Compose](#вариант-б--docker-compose)
  - [Вариант В — Proxmox VE (LXC)](#вариант-в--proxmox-ve-lxc)
  - [Вариант Г — OpenWrt LXC](#вариант-г--openwrt-lxc)
- [Конфигурация](#конфигурация)
- [Архитектура](#архитектура)
- [Устранение неисправностей](#устранение-неисправностей)
- [Разработка](#разработка)
- [Участие в разработке](#участие-в-разработке)
- [Благодарности](#благодарности)
- [Поддержка](#поддержка)

</details>

---

## Возможности

### 🔊 Аудио и воспроизведение
- **[Протокол Sendspin](https://www.sendspin-audio.com/)** — полная поддержка нативного протокола потоковой передачи Music Assistant
- **Несколько устройств** — одновременное подключение нескольких Bluetooth-колонок, каждая как отдельный плеер в MA
- **PipeWire и PulseAudio** — автоопределение аудиосистемы хоста; изолированный `PULSE_SINK` на каждое устройство
- **Отображение аудио формата** — кодек, частота дискретизации, битность для каждого устройства (например, `flac 48000Hz/24-bit/2ch`)
- **Компенсация задержки** — `static_delay_ms` для синхронизации A2DP-буферов в мультирум-группе
- **Предпочтительный формат** — выбор формата для каждого устройства (например, `flac:44100:16:2`)
- **Маршрутизация громкости** — гибрид: через MA API (синхронизация UI MA) или прямой PulseAudio (`VOLUME_VIA_MA`)
- **SBC-кодек по предпочтению** — `PREFER_SBC_CODEC` принудительно выбирает SBC после BT-подключения для снижения нагрузки CPU / стабильной задержки

### 📡 Bluetooth
- **Авто-переподключение** — мгновенное обнаружение отключения через D-Bus; резервный опрос каждые 10 с при отсутствии D-Bus; переподключение с экспоненциальным откатом
- **Управление BT-адаптерами** — автоопределение адаптеров с ручным выбором; привязка колонки к конкретному адаптеру
- **D-Bus-детекция отключений** — мгновенная реакция через dbus-fast; резервный режим — опрос bluetoothctl
- **Сопряжение/удаление устройств** — сканирование, сопряжение, trust и удаление из веб-интерфейса
- **Release / Reclaim** — временная передача колонки для сопряжения с телефоном/ПК, затем возврат
- **Изоляция churn** — автоотключение BT-управления для устройств с частыми переподключениями (`BT_CHURN_THRESHOLD`)
- **Watchdog зомби-воспроизведения** — авто-перезапуск подпроцесса, если играет, но нет аудиоданных 15 с
- **Keepalive-тишина** — опциональные периодические пакеты тишины для предотвращения автоотключения колонки (`keepalive_interval`)

### 🎵 Интеграция с Music Assistant
- **Метаданные воспроизведения** — трек, исполнитель, обложка альбома, позиция в очереди из MA REST API
- **Транспортные кнопки** — предыдущий / следующий / shuffle / repeat для syncgroup и соло-плееров
- **Групповая громкость** — delta-based `group_volume` с сохранением пропорций между колонками
- **Пауза / воспроизведение** — для отдельного устройства, группы или всех плееров сразу

### 🖥️ Веб-интерфейс
- **Дизайн в стиле HA** — CSS design tokens, автоматическая тёмная/светлая тема, шрифт Roboto; живая инъекция темы через HA Ingress
- **Обновления в реальном времени** — Server-Sent Events (SSE) мгновенно отправляют изменения статуса на дашборд
- **Тултип обложки альбома** — наведите на название трека, чтобы увидеть обложку
- **Визуализатор эквалайзера** — анимированные полоски; замороженные красные при воспроизведении без аудиопотока
- **Фильтр по группам** — выпадающий список для отображения устройств конкретной MA-syncgroup
- **Панель диагностики** — адаптеры, синки, D-Bus, состояние каждого устройства одним взглядом
- **Просмотр логов** — фильтрация по уровню серьёзности с автообновлением
- **Редактор конфигурации** — адрес сервера, устройства, адаптеры, таймзона — всё редактируется в UI

### 🔐 Безопасность
- **Защита паролем** — опциональная аутентификация PBKDF2-SHA256 для standalone-режима
- **HA 2FA-логин** — полный HA login_flow с TOTP/MFA при работе как аддон
- **Защита от перебора** — 5 неудачных попыток → блокировка IP на 5 минут

### 🚀 Развёртывание
- **Четыре варианта** — аддон Home Assistant, Docker Compose, Proxmox LXC, OpenWrt LXC
- **Несколько экземпляров bridge** — запуск нескольких мостов против одного сервера MA; каждый регистрирует свои плееры
- **REST API на 37 эндпоинтов** — полный программный контроль (`/api/status`, `/api/volume`, `/api/bt/*`, `/api/ma/*`, …)
- **Динамический уровень логов** — смена `LOG_LEVEL` через API или веб-интерфейс без перезапуска

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

## Протестированное оборудование

| Платформа | Хост | BT адаптер | Колонки |
|-----------|------|------------|---------|
| **HA Addon** | Proxmox VM (HAOS), x86_64 | CSR8510 A10 USB | IKEA ENEBY20, Yandex Station mini, Lenco LS-500 |
| **Proxmox LXC** | HP ProLiant MicroServer Gen8, Ubuntu 24.04 | CSR8510 A10 USB | IKEA ENEBY Portable |
| **OpenWrt LXC** | Turris Omnia (ARMv7), Ubuntu 24.04 | CSR8510 A10 USB | AfterShokz |

ПО: Python 3.12, BlueZ 5.72, PulseAudio 16.1, aiosendspin 4.3.2.
Все три экземпляра работают на v2.22.2 с одним сервером Music Assistant и мультирум-синхронизацией.

📖 [Подробное описание тестового стенда →](https://trudenboy.github.io/sendspin-bt-bridge/ru/test-stand/)

---

## Варианты развёртывания

| | Аддон Home Assistant | Docker Compose | Proxmox LXC | OpenWrt LXC |
|---|---|---|---|---|
| Установка | Магазин аддонов HA (один клик) | `docker compose up` | Однострочный скрипт | Однострочный скрипт |
| Bluetooth | bluetoothd хоста через D-Bus | bluetoothd хоста через D-Bus | Собственный bluetoothd внутри LXC | bluetoothd хоста через D-Bus |
| Аудио | Мост через HA Supervisor | PulseAudio/PipeWire хоста | Собственный PulseAudio внутри LXC | Собственный PulseAudio внутри LXC |
| UI настройки | Панель HA + веб-интерфейс | Веб-интерфейс на :8080 | Веб-интерфейс на :8080 | Веб-интерфейс на :8080 |
| Применение изменений | Перезапуск аддона | Перезапуск контейнера | `systemctl restart` | `systemctl restart` |

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

## Вариант Г — OpenWrt LXC

Запуск в виде **нативного LXC-контейнера** на роутерах OpenWrt (Turris Omnia, x86 OpenWrt и др.) — Docker не нужен. Контейнер использует **`bluetoothd` хоста через D-Bus bridge** с `pulseaudio --system` внутри контейнера.

Полная документация, предварительные требования, шаги ручной установки и известные проблемы — в **[lxc/openwrt/README.md](lxc/openwrt/README.md)**.

**Требования:** ≥1 ГБ RAM, ≥2 ГБ свободного места, USB Bluetooth-адаптер.

### Установка одной командой (на хосте OpenWrt от root)

```sh
wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh | sh
```

Либо скачайте и проверьте скрипт перед запуском:

```sh
wget https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/openwrt/create.sh
less create.sh
sh create.sh
```

Скрипт устанавливает LXC и Bluetooth-пакеты через `opkg`, создаёт контейнер Ubuntu 24.04, настраивает D-Bus bridge и cgroup-правила, запускает установщик внутри контейнера и устанавливает procd-скрипт для автозапуска.

### Основные команды мониторинга

```sh
lxc-attach -n sendspin -- journalctl -u sendspin-client -f
lxc-attach -n sendspin -- systemctl status sendspin-client pulseaudio-system --no-pager
lxc-attach -n sendspin -- pactl list sinks short
lxc-attach -n sendspin -- btctl show
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

### Переменные окружения

| Переменная | По умолчанию | Описание |
|----------|---------|-------------|
| `SENDSPIN_NAME` | `Docker-{hostname}` | Базовое имя плеера в Music Assistant |
| `SENDSPIN_SERVER` | `auto` | Hostname/IP сервера MA; `auto` — обнаружение через mDNS |
| `SENDSPIN_PORT` | `9000` | Порт WebSocket Sendspin в MA |
| `TZ` | `Australia/Melbourne` | Часовой пояс контейнера |
| `WEB_PORT` | `8080` | Порт веб-интерфейса |
| `MA_API_URL` | `` | Базовый URL REST API Music Assistant (напр. `http://192.168.1.10:8123`) — включает метаданные воспроизведения и транспортные кнопки |
| `MA_API_TOKEN` | `` | Долгосрочный токен доступа HA для MA API |
| `VOLUME_VIA_MA` | `true` | Маршрутизация громкости через MA API; `false` = прямой PulseAudio |
| `MUTE_VIA_MA` | `false` | Маршрутизация mute через MA API; `false` = прямой PulseAudio (мгновенно) |
| `LOG_LEVEL` | `INFO` | Уровень логирования (`INFO` или `DEBUG`); изменяется через API в реальном времени |

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
| `web_interface.py` | Точка входа Flask — регистрирует blueprints, запускает сервер Waitress |
| `routes/api.py` | Основное воспроизведение и громкость (6 маршрутов) |
| `routes/api_bt.py` | Управление Bluetooth (7 маршрутов) |
| `routes/api_ma.py` | Интеграция с Music Assistant (10 маршрутов) |
| `routes/api_config.py` | Конфигурация (5 маршрутов) |
| `routes/api_status.py` | Статус и диагностика (6 маршрутов) |
| `routes/views.py` | Рендеринг HTML-страниц |
| `routes/auth.py` | Опциональная защита веб-интерфейса паролем |
| `services/daemon_process.py` | Точка входа subprocess — каждая колонка работает здесь с собственным `PULSE_SINK` |
| `services/bridge_daemon.py` | Подкласс `BridgeDaemon` — обрабатывает события Sendspin внутри subprocess |
| `services/ma_monitor.py` | Постоянный WebSocket-монитор MA — подписывается на события `player_queue_updated` |
| `services/ma_client.py` | Вспомогательные функции MA REST API — обнаружение групп, групповое воспроизведение |
| `services/bluetooth.py` | BT-утилиты — `bt_remove_device()`, `persist_device_enabled()` |
| `services/pulse.py` | Async-утилиты PulseAudio — обнаружение синков, коррекция маршрутизации потоков |
| `services/ma_discovery.py` | mDNS-обнаружение серверов Music Assistant |
| `scripts/translate_ha_config.py` | Транслятор options.json → config.json для HA аддона (вызывается из entrypoint.sh) |
| `entrypoint.sh` | Запуск контейнера — D-Bus, определение аудио-сокета, трансляция конфига HA, запуск приложения |
| `Dockerfile` | Образ контейнера |
| `docker-compose.yml` | Оркестрация Docker Compose |
| `ha-addon/config.yaml` | Манифест аддона Home Assistant |
| `ha-addon/Dockerfile` | Образ аддона HA (тонкая обёртка над основным образом) |
| `ha-addon/run.sh` | Точка входа HA |
| `ha-addon/translations/en.yaml` | Метки UI для HA |
| `lxc/` | Скрипты установки LXC (Proxmox и OpenWrt) |
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
- **Обсуждение в MA**: [music-assistant/discussions #5061](https://github.com/orgs/music-assistant/discussions/5061)
- **Тема на HA Community**: [Sendspin Bluetooth Bridge на HA Community](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- **Discord**: [#sendspin-bt-bridge в MA Discord](https://discord.com/channels/330944238910963714/1479933490991599836)
- **Исходное обсуждение**: [music-assistant/discussions #4677](https://github.com/orgs/music-assistant/discussions/4677)

## Лицензия

Лицензия MIT — подробнее в файле [LICENSE](LICENSE).

---

## История изменений

Полная история версий в [CHANGELOG.md](CHANGELOG.md).

Нарративная история развития проекта (архитектурные решения, вехи, миграция v1 → v2) — в [HISTORY.ru.md](HISTORY.ru.md).
