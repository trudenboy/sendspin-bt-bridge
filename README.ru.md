# [Sendspin](https://www.sendspin-audio.com/) Bluetooth Bridge

[![GitHub Release](https://img.shields.io/github/v/release/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/releases/latest)
[![Docker Pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Ftrudenboy%2Fsendspin-bt-bridge%2Fsendspin-bt-bridge&query=downloadCount&label=Docker%20Pulls&logo=docker&color=blue)](https://github.com/trudenboy/sendspin-bt-bridge/pkgs/container/sendspin-bt-bridge)
[![HA Installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Faddons.json&query=%24%5B%2285b1ecde_sendspin_bt_bridge%22%5D.total&label=HA%20Installs&logo=homeassistant&color=18bcf2)](https://analytics.home-assistant.io/apps/)
[![GitHub Stars](https://img.shields.io/github/stars/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/stargazers)
[![Try Demo](https://img.shields.io/badge/Try_Demo-Live-brightgreen?style=flat&logo=render)](https://sendspin-demo.onrender.com)

[Read in English](README.md) · [📖 Документация](https://trudenboy.github.io/sendspin-bt-bridge/) · [🎮 Демо](https://sendspin-demo.onrender.com) · [📋 История](HISTORY.ru.md)

Bluetooth-мост для [Music Assistant](https://www.music-assistant.io/), который превращает Bluetooth-колонки и наушники в нативные плееры [Sendspin](https://www.music-assistant.io/player-support/sendspin/) в MA. Актуальная релизная линия v2.40.5 работает как аддон Home Assistant (stable / RC / beta), Docker-контейнер, установка на Raspberry Pi или нативное LXC-развёртывание на Proxmox VE / OpenWrt — всё это рассчитано на headless и local-first мультирум-сценарии.

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
- [Демо-режим](#демо-режим)
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
- **Управление питанием адаптера** — перезагрузка BT-адаптера из веб-интерфейса

### 🎵 Интеграция с Music Assistant
- **Авторизация в MA без пароля в режиме аддона** — кнопка «Войти через Home Assistant» создаёт долгоживущий токен MA через Ingress JSONRPC, без ручного ввода учётных данных
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
- **Режимы сетки и списка** — при количестве устройств больше 6 интерфейс по умолчанию переключается в режим списка и запоминает ручной выбор
- **Переработанные вкладки Configuration** — General, Devices, Bluetooth, Music Assistant и Security разбивают настройку на логичные карточки
- **Глубокие ссылки в настройки** — шестерёнки устройств/адаптеров и empty-state CTA ведут в точную конфигурационную карточку и подсвечивают нужную строку
- **Бэкап и восстановление конфига** — скачивание / загрузка JSON-конфигурации из веб-интерфейса
- **Модальное окно информации об устройстве** — просмотр деталей BT-устройства с копированием в один клик
- **Обратный отсчёт до сканирования** — визуальный таймер готовности следующего BT-сканирования
- **Бейдж обновления и модалка** — проверка обновлений, краткие release notes и runtime-specific action
- **Бейдж версии → release notes** — клик по версии открывает страницу релиза на GitHub
- **Ссылка на профиль** — кликабельное имя пользователя ведёт на профиль MA или HA в зависимости от метода аутентификации

### 🔐 Безопасность
- **Защита паролем** — опциональная аутентификация PBKDF2-SHA256 для standalone-режима
- **HA 2FA-логин** — полный HA login_flow с TOTP/MFA при работе как аддон
- **Таймаут сессии** — настраиваемое время жизни browser-сессии для standalone auth
- **Защита от перебора** — по умолчанию 5 неудачных попыток → блокировка IP на 5 минут, с настраиваемым окном и длительностью

### 🚀 Развёртывание
- **Треки аддона HA: stable / RC / beta** — prerelease-варианты используют разные ingress-порты, диапазоны Sendspin-портов, поведение при старте и branding, поэтому их безопаснее тестировать параллельно
- **Ручное планирование портов** — глобальные override-параметры `WEB_PORT` и `BASE_LISTEN_PORT` помогают, когда несколько bridge работают на одном хосте или HAOS-инстансе
- **Порты на уровне устройства** — `listen_port` фиксирует порт конкретного плеера, а `listen_host` меняет рекламируемый host/IP для этого плеера
- **Несколько экземпляров bridge** — запуск нескольких мостов против одного сервера MA; каждый регистрирует свои плееры
- **REST API на 42 эндпоинтов** — полный программный контроль (`/api/status`, `/api/volume`, `/api/bt/*`, `/api/ma/*`, …)
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

### Пример живой топологии

Два экземпляра bridge подключены к одному серверу MA: аддон HA на HAOS (4 колонки, 2 BT-адаптера) и Docker-контейнер в Proxmox LXC (2 колонки, 1 BT-адаптер). Все активные колонки объединены в одну sync group для синхронного воспроизведения.

![Живая топология multi-bridge](multiroom-live.png)

---

## Протестированное оборудование

| Платформа | Хост | BT адаптер | Колонки |
|-----------|------|------------|---------|
| **HA Addon** | Proxmox VM (HAOS), x86_64 | CSR8510 A10 USB | IKEA ENEBY20, Yandex Station mini, Lenco LS-500 |
| **Proxmox LXC** | HP ProLiant MicroServer Gen8, Ubuntu 24.04 | CSR8510 A10 USB | IKEA ENEBY Portable |
| **OpenWrt LXC** | Turris Omnia (ARMv7), Ubuntu 24.04 | CSR8510 A10 USB | AfterShokz |

ПО: Python 3.12, BlueZ 5.72, PulseAudio 16.1, sendspin 5.x.
Текущая документация отражает релизную линию v2.40.5 и её актуальное поведение для HA addon / Docker / LXC-развёртываний.

📖 [Подробное описание тестового стенда →](https://trudenboy.github.io/sendspin-bt-bridge/ru/test-stand/)

---

## Поддерживаемые платформы

Docker-образы собираются для трёх архитектур. Поддерживаются все популярные устройства Home Assistant.

| Архитектура | Docker-платформа | Устройства HA | Статус |
|---|---|---|---|
| **amd64** (x86_64) | `linux/amd64` | Intel NUC, mini PC, Proxmox/VMware VM | ✅ Протестировано |
| **aarch64** (ARM64) | `linux/arm64` | HA Green, HA Yellow, Raspberry Pi 4/5, ODROID N2+ | ✅ Проверено сообществом |
| **armv7** (ARM 32-bit) | `linux/arm/v7` | Raspberry Pi 3, ODROID XU4, Tinker Board | ⚠️ Best-effort |

> **Примечание:** Устройства armv7 (например, Raspberry Pi 3 с 1 ГБ ОЗУ) могут упираться в ресурсы при одновременной работе с несколькими Bluetooth-колонками. Для лучшего опыта рекомендуются aarch64 или amd64.

---

## Варианты развёртывания

| | Аддон Home Assistant | Docker Compose | Proxmox LXC | OpenWrt LXC |
|---|---|---|---|---|
| Установка | Магазин аддонов HA (stable / RC / beta) | `docker compose up` | Однострочный скрипт | Однострочный скрипт |
| Bluetooth | bluetoothd хоста через Supervisor/runtime mounts | bluetoothd хоста через D-Bus | bluetoothd хоста через D-Bus bridge | bluetoothd хоста через D-Bus bridge |
| Аудио | Мост через HA Supervisor | PulseAudio/PipeWire хоста | Собственный PulseAudio внутри LXC | Собственный PulseAudio внутри LXC |
| UI настройки | HA Ingress (`8080` / `8081` / `8082`) + опциональный прямой `WEB_PORT` listener | Прямой `WEB_PORT` listener (по умолчанию `8080`) | Прямой `WEB_PORT` listener (по умолчанию `8080`) | Прямой `WEB_PORT` listener (по умолчанию `8080`) |
| Порты плееров | Channel default `BASE_LISTEN_PORT` (`8928+`, `9028+`, `9128+`) | `BASE_LISTEN_PORT` (по умолчанию `8928+`) | `BASE_LISTEN_PORT` (по умолчанию `8928+`) | `BASE_LISTEN_PORT` (по умолчанию `8928+`) |
| Применение изменений | Перезапуск аддона | Перезапуск контейнера | `systemctl restart` | `systemctl restart` |

---

## Вариант А — Аддон Home Assistant

### Установка

**1. Добавьте репозиторий аддонов в Home Assistant:**

[![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

Или вручную: **Настройки → Аддоны → Магазин аддонов → ⋮ → Репозитории** → добавьте `https://github.com/trudenboy/sendspin-bt-bridge`

**2.** Установите нужный вариант аддона:
- **Sendspin Bluetooth Bridge** → stable-трек
- **Sendspin Bluetooth Bridge (RC)** → RC-трек
- **Sendspin Bluetooth Bridge (Beta)** → beta-трек

Stable рекомендуется для обычного использования. RC и beta используют разные defaults, поэтому их можно безопаснее тестировать параллельно на одном HAOS-хосте.

### Настройка

На вкладке **Configuration** аддона:

```yaml
sendspin_server: auto
sendspin_port: 9000
web_port: 8090                 # опционально — дополнительный прямой listener в сети хоста
base_listen_port: 8928         # опционально — базовый per-device player port
update_channel: stable         # влияет только на проверку обновлений, не меняет установленный трек аддона
bluetooth_devices:
  - mac: "AA:BB:CC:DD:EE:FF"
    player_name: "Колонка в гостиной"
  - mac: "11:22:33:44:55:66"
    player_name: "Колонка на кухне"
    adapter: hci1
    static_delay_ms: -500
    listen_port: 8935          # опционально — Sendspin-порт для конкретного устройства
    listen_host: 192.168.1.50  # опционально — рекламируемый host/IP для отображаемого URL плеера
```

**Как работают ingress и direct listener:**
- Stable использует ingress-порт **8080**, RC — **8081**, beta — **8082**
- `web_port` **не** заменяет ingress — он открывает **дополнительный** прямой host-network listener
- `base_listen_port` меняет базовый блок player-port для устройств без явного `listen_port`
- `listen_host` меняет только рекламируемый host/IP; сами плееры всё равно bind'ятся на `0.0.0.0`

Основной веб-интерфейс аддон всегда публикует через **HA Ingress** и показывает в боковой панели HA. Если задать `web_port`, появляется и прямой URL вида `http://<ip-хоста-ha>:8090` для диагностики или API-доступа.

### Требования

- Home Assistant OS или Supervised
- Bluetooth-адаптер, доступный хосту
- Сервер Music Assistant в вашей сети (на любом хосте)

### Маршрутизация аудио (HA OS)

Аддон запрашивает `audio: true` в манифесте, поэтому HA Supervisor автоматически инжектирует `PULSE_SERVER`. Ручная настройка сокетов не требуется.

### Каналы обновлений аддона

- Установленный вариант аддона определяет, на каком кодовом треке вы реально находитесь: stable, RC или beta.
- Настройка `update_channel` внутри приложения только задаёт, какие релизы проверяет встроенная проверка обновлений; она **не** переключает установленный трек аддона.
- Stable по умолчанию стартует автоматически. RC и beta по умолчанию запускаются вручную.
- **Важно:** не настраивайте одно и то же Bluetooth-устройство в нескольких работающих аддонах одновременно.

---

## Вариант Б — Docker Compose

### Предварительные требования

- Docker и Docker Compose
- Bluetooth-колонки **сопряжены** с хостом до запуска контейнера
- Сервер Music Assistant, работающий в вашей сети

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

Создайте директорию конфигурации и запустите:

```bash
sudo mkdir -p /etc/docker/Sendspin
docker compose up -d
```

> **Обновляетесь с более ранней версии?** Ранее образ публиковался как `ghcr.io/loryanstrant/sendspin-client`. Обновите файл compose или команду pull на `ghcr.io/trudenboy/sendspin-bt-bridge:latest`.

Откройте веб-интерфейс по адресу `http://IP-вашего-хоста:${WEB_PORT:-8080}` для добавления Bluetooth-устройств и настройки сервера MA.

**Планирование портов в Docker-режиме:**
- `WEB_PORT` меняет прямой listener веб-интерфейса/API
- `BASE_LISTEN_PORT` задаёт базовый player-port для устройств без явного `listen_port`
- `listen_port` / `listen_host` для конкретных устройств можно добавить позже в `/config/config.json` или через веб-интерфейс
- если несколько bridge-контейнеров работают на одном хосте, задайте каждому уникальные `WEB_PORT` и `BASE_LISTEN_PORT` и **не** используйте одну и ту же Bluetooth-колонку в двух работающих bridge

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
  "SENDSPIN_SERVER": "auto",
  "SENDSPIN_PORT": 9000,
  "WEB_PORT": 8080,
  "BASE_LISTEN_PORT": 8928,
  "BLUETOOTH_DEVICES": [
    {
      "mac": "80:99:E7:C2:0B:D3",
      "player_name": "Гостиная",
      "adapter": "hci0",
      "listen_port": 8931
    },
    {
      "mac": "FC:58:FA:EB:08:6C",
      "player_name": "Кухня",
      "adapter": "hci1",
      "listen_host": "192.168.1.50"
    }
  ],
  "TZ": "Europe/Moscow"
}
```

Каждое устройство в `BLUETOOTH_DEVICES` запускает отдельный плеер Sendspin и менеджер Bluetooth. Все устройства отображаются как самостоятельные плееры в Music Assistant.

- `WEB_PORT` управляет прямым listener'ом веб-интерфейса/API в Docker, Raspberry Pi и LXC-установках. В режиме HA addon то же значение открывает **дополнительный** прямой listener, а ingress остаётся на channel default порту.
- `BASE_LISTEN_PORT` — это базовый Sendspin listener port. Устройства без явного `listen_port` получают `BASE_LISTEN_PORT + индекс_устройства`.
- `listen_port` фиксирует порт конкретного плеера.
- `listen_host` переопределяет рекламируемый host/IP для этого плеера. Он **не** меняет bind-адрес (`0.0.0.0`).
- Поле `adapter` опционально — опустите его, если у вас один Bluetooth-адаптер.

Большинство изменений конфигурации сразу записываются в `config.json`, но для применения Bluetooth-топологии (устройства/адаптеры), настроек подключения к MA, web/player-port и latency по-прежнему нужен перезапуск сервиса. Runtime-переключатели вроде `LOG_LEVEL` и обновления пароля/аутентификации применяются сразу.

### Переменные окружения

| Переменная | По умолчанию | Описание |
|----------|---------|-------------|
| `SENDSPIN_NAME` | `Docker-{hostname}` | Базовое имя плеера в Music Assistant |
| `SENDSPIN_SERVER` | `auto` | Hostname/IP сервера MA; `auto` — обнаружение через mDNS |
| `SENDSPIN_PORT` | `9000` | Порт WebSocket Sendspin в MA |
| `TZ` | `Australia/Melbourne` | Часовой пояс контейнера |
| `WEB_PORT` | `8080` | Порт веб-интерфейса/API в standalone-режимах; в режиме HA addon добавляет второй прямой listener и не заменяет ingress |
| `BASE_LISTEN_PORT` | `8928` | Базовый Sendspin player-port для устройств без явного `listen_port` |
| `MA_API_URL` | `` | Базовый URL REST API Music Assistant (напр. `http://192.168.1.10:8123`) — включает метаданные воспроизведения и транспортные кнопки |
| `MA_API_TOKEN` | `` | Долгосрочный токен доступа HA для MA API |
| `VOLUME_VIA_MA` | `true` | Маршрутизация громкости через MA API; `false` = прямой PulseAudio |
| `MUTE_VIA_MA` | `false` | Маршрутизация mute через MA API; `false` = прямой PulseAudio (мгновенно) |
| `LOG_LEVEL` | `INFO` | Уровень логирования (`INFO` или `DEBUG`); изменяется через API в реальном времени |
| `DEMO_MODE` | `false` | Запуск с эмуляцией оборудования — BT/PulseAudio/MA не нужны |

Для стартового планирования портов можно явно задавать `WEB_PORT` и `BASE_LISTEN_PORT`. В режиме Home Assistant addon эти же значения доступны через addon options.

### Веб-интерфейс

Веб-интерфейс доступен на настроенном `WEB_PORT` в Docker / Raspberry Pi / LXC-установках. В режиме HA addon основная ссылка в sidebar всегда остаётся на ingress-порту канала (stable `8080`, RC `8081`, beta `8082`); настройка `WEB_PORT` лишь добавляет второй прямой listener. Интерфейс предоставляет:

- **Дашборд устройств в реальном времени**: карточки устройств или режим списка со статусом Bluetooth, воспроизведения, маршрутизации, синхронизации и Music Assistant
- **Запоминаемый режим сетки/списка**: режим списка становится режимом по умолчанию при количестве устройств больше 6, а ручной выбор хранится в браузере
- **Вкладки Configuration по задачам**: General, Devices, Bluetooth, Music Assistant и Security делают редактирование короче и понятнее
- **Глубокие ссылки по месту**: шестерёнки и действия из empty-state ведут в точную конфигурационную карточку и подсвечивают нужную строку
- **Сценарии discovery**: поиск nearby devices, импорт already paired hardware и соблюдение scan cooldown прямо в UI
- **Diagnostics и Logs**: health summary, routing details, runtime data по устройствам, скачивание diagnostics, фильтры логов и переключение log level без перезапуска
- **Обновления и bug-report flow**: бейдж версии, модалка обновления, скачивание diagnostics и помощники для GitHub issue

---

## Архитектура

Bridge работает как **многопроцессное приложение**: один главный процесс управляет Bluetooth, веб-API и интеграцией с Music Assistant, тогда как каждая настроенная колонка получает собственный изолированный subprocess с выделенным контекстом PulseAudio (`PULSE_SINK`). В режиме HA addon то же Flask/Waitress-приложение может одновременно публиковать фиксированный ingress-listener и дополнительный прямой listener на `WEB_PORT`.

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
│  │  Веб-интерфейс (Flask/WSGI) │    │
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
curl http://ваш-хост:${WEB_PORT:-8080}/api/diagnostics | python3 -m json.tool
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

## Демо-режим

Попробуйте веб-интерфейс без оборудования — не нужен Bluetooth-адаптер, колонки или Music Assistant.

**[🎮 Демо →](https://sendspin-demo.onrender.com)**

Установите `DEMO_MODE=true`, чтобы заменить все аппаратные слои (BlueZ, D-Bus, PulseAudio, MA API) интеллектуальными моками. Пять эмулированных колонок с реалистичным статусом, прогрессом воспроизведения, уровнем заряда и синхро-группами. Все элементы управления (громкость, mute, play/pause, next/previous, shuffle, repeat) работают интерактивно.

```bash
# Локальный запуск
DEMO_MODE=true pip install sendspin flask waitress websockets
DEMO_MODE=true python sendspin_client.py
# Открыть http://localhost:8080
```

### Развёртывание на Render.com

В репозитории есть `render.yaml` — нажмите кнопку ниже для деплоя:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/trudenboy/sendspin-bt-bridge)

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
| `routes/api_bt.py` | Управление Bluetooth (9 маршрутов) |
| `routes/api_ma.py` | Интеграция с Music Assistant (10 маршрутов) |
| `routes/api_config.py` | Конфигурация (9 маршрутов) |
| `routes/api_status.py` | Статус и диагностика (8 маршрутов) |
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


## Треки аддона и планирование портов

Манифест в `ha-addon/`, который лежит в репозитории, соответствует stable-варианту аддона Home Assistant. Когда публикуются RC или beta-варианты, они устанавливаются как отдельные треки с разными портами по умолчанию:

| Трек | Ingress / веб-порт по умолчанию | Базовый порт плееров по умолчанию |
|---|---|---|
| Stable | `8080` | `8928` |
| RC | `8081` | `9028` |
| Beta | `8082` | `9128` |

Изменение настройки `update_channel` внутри приложения **не** переключает установленный трек аддона Home Assistant. Эта настройка влияет только на проверку prerelease-обновлений и предупреждающие тексты в интерфейсе.

Если на одном хосте работают несколько bridge-инстансов или несколько вариантов аддона, используйте эти дефолты как отправную точку и задавайте `WEB_PORT`, `BASE_LISTEN_PORT` или индивидуальные `listen_port` вручную, когда нужна дополнительная изоляция.
