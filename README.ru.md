# [Sendspin](https://www.sendspin-audio.com/) Bluetooth Bridge

[![GitHub Release](https://img.shields.io/github/v/release/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/releases/latest)
[![Docker Pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Ftrudenboy%2Fsendspin-bt-bridge%2Fsendspin-bt-bridge&query=downloadCount&label=Docker%20Pulls&logo=docker&color=blue)](https://github.com/trudenboy/sendspin-bt-bridge/pkgs/container/sendspin-bt-bridge)
[![HA Installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Faddons.json&query=%24%5B%2285b1ecde_sendspin_bt_bridge%22%5D.total&label=HA%20Installs&logo=homeassistant&color=18bcf2)](https://analytics.home-assistant.io/apps/)
[![GitHub Stars](https://img.shields.io/github/stars/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/stargazers)
[![Try Demo](https://img.shields.io/badge/Try_Demo-Live-brightgreen?style=flat&logo=render)](https://sendspin-demo.onrender.com)

[Read in English](README.md) · [Документация](https://trudenboy.github.io/sendspin-bt-bridge/ru/) · [Дорожная карта](ROADMAP.ru.md) · [Демо](https://sendspin-demo.onrender.com) · [История проекта](HISTORY.ru.md)

Превратите Bluetooth-колонки и наушники в нативные плееры [Music Assistant](https://www.music-assistant.io/) на протоколе [Sendspin](https://www.music-assistant.io/player-support/sendspin/).

Sendspin Bluetooth Bridge — это local-first мост для headless-сценариев на Home Assistant, Docker, Raspberry Pi и LXC. Каждое Bluetooth-устройство появляется в Music Assistant как отдельный плеер, а управление, диагностика и настройка доступны через веб-интерфейс.

## Что делает проект

- Превращает обычные Bluetooth-колонки и наушники в плееры Music Assistant.
- Подключает несколько устройств одновременно: BridgeOrchestrator координирует отдельный изолированный playback-подпроцесс на каждую колонку.
- Даёт веб-интерфейс для настройки, Bluetooth pairing flow, диагностики, логов и бэкапа конфигурации.
- Подсказывает следующие шаги через onboarding checklist, recovery guidance и action-oriented диагностику.
- Поддерживает развёртывание как Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC и OpenWrt LXC.
- Позволяет масштабироваться на несколько комнат через несколько bridge-экземпляров против одного MA-сервера.

![Инфографика Sendspin Bluetooth Bridge — возможности, архитектура и варианты развёртывания](docs-site/public/screenshots/sbb_infographic_ru.png)

## Что потребуется

- Bluetooth-колонка или наушники — подойдёт любое устройство с профилем A2DP.
- Сервер [Music Assistant](https://www.music-assistant.io/) (v2.3+) с включённым провайдером [Sendspin](https://www.music-assistant.io/player-support/sendspin/).
- Linux-хост с USB или встроенным Bluetooth-адаптером — Raspberry Pi, NUC, Proxmox VM или Home Assistant OS.

Командная строка не нужна. Веб-интерфейс полностью берёт на себя поиск Bluetooth-устройств, сопряжение и настройку Music Assistant.

## Режимы работы

У проекта теперь есть два практических режима:

- **Production mode** — реальный Bluetooth, PulseAudio/PipeWire и интеграция с Music Assistant.
- **Demo mode** — детерминированный UI/test stand для документации, скриншотов и UX-проверок без железа.

Локальный demo запускается из корня репозитория:

```bash
DEMO_MODE=true python sendspin_client.py
```

После запуска откройте `http://127.0.0.1:8080/`. Demo mode поднимает стабильный стенд с девятью устройствами, преднастроенными diagnostics/logs, group state и данными Music Assistant, поэтому UI можно изучать и документировать без живого Bluetooth-окружения.

## Быстрый старт: Home Assistant

Самый быстрый путь — установить Home Assistant addon.

[![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

1. Добавьте репозиторий в Home Assistant.
2. Установите **Sendspin Bluetooth Bridge** через Add-on Store.
3. Запустите аддон и откройте веб-интерфейс из боковой панели HA.
4. Добавьте Bluetooth-колонки, затем откройте **Configuration → Music Assistant**, чтобы подключить или перенастроить Music Assistant. Dashboard сам подскажет следующий безопасный шаг через onboarding checklist и recovery guidance.

Полный гайд по Home Assistant: <https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/ha-addon/>

## Выберите вариант развёртывания

| Вариант | Для кого | Путь установки | Документация |
|---|---|---|---|
| **Home Assistant Addon** | Пользователи HAOS / Supervised | Add-on Store | [Открыть гайд](https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/ha-addon/) |
| **Docker** | Обычные Linux-хосты | `docker compose up -d` | [Открыть гайд](https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/docker/) |
| **Raspberry Pi** | Установки на Pi | Настройка на базе Docker | [Открыть гайд](https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/raspberry-pi/) |
| **Proxmox / OpenWrt LXC** | Маршрутизаторы, appliance-сценарии и лёгкие хосты | Bootstrap-скрипт на хосте | [Открыть гайд](https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/lxc/) |

## Ключевые возможности

- **Синхронизированный стриминг** — использует протокол [Sendspin](https://www.music-assistant.io/player-support/sendspin/) для передачи lossless-аудио с выравниванием по времени, чтобы сгруппированные колонки играли синхронно в разных комнатах.
- **Без командной строки** — поиск Bluetooth-устройств, сопряжение и подключение к Music Assistant целиком через веб-интерфейс. Никакого `bluetoothctl`, конфигов вручную или SSH.
- **Глубокая интеграция с Music Assistant** — текущий трек, обложка, управление воспроизведением, групповая громкость, shuffle и repeat — всё синхронизируется в реальном времени через постоянное соединение с сервером MA.
- **Автоматизации Home Assistant** — каждая Bluetooth-колонка становится плеером Music Assistant, видимым в HA. Используйте в автоматизациях, скриптах, сценах, дашбордах и с голосовыми ассистентами.
- **Надёжный Bluetooth** — автоматическое переподключение, детекция отключений и мониторинг состояния устройств поддерживают связь с колонками без ручного вмешательства.
- **Подсказки по настройке и восстановлению** — встроенные onboarding/recovery-поверхности и bug report с автоподсказкой по диагностике ускоряют настройку и разбор проблем.
- **Стабильный demo-стенд** — публичное live demo и `DEMO_MODE=true python sendspin_client.py` дают повторяемую среду для UI-проверок, скриншотов и демонстраций без Bluetooth-железа.
- **Мультирум** — один bridge на комнату или один bridge на несколько колонок. Несколько bridge-экземпляров работают с одним сервером Music Assistant для озвучки всего дома.
- **Пять вариантов развёртывания** — Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC и OpenWrt LXC — один и тот же bridge, один веб-интерфейс, одни и те же функции везде.
- **REST API и live-обновления** — 60+ эндпоинтов для автоматизации и поток статусов в реальном времени через SSE для кастомных дашбордов и интеграций.

## Коротко о roadmap

Дорожная карта теперь синхронизирована с **v3-волной**, которая стартует от уже shipped `v2.46.x` runtime, а не от старого списка рефакторингов.

- **Сейчас:** закрыть последние pre-v3 UX-пробелы и положить foundation в виде backend abstraction плюс config schema v2.
- **Следующий крупный продуктовый шаг:** выпустить USB DAC и wired audio players как первый adjacent backend, а затем добавить custom PulseAudio sink tooling там, где он реально расширяет room layout.
- **Потом:** добавить audio health visibility, signal path clarity и guided delay tuning, чтобы Bluetooth и wired players имели одну общую observability story.
- **После этого:** развивать AI-assisted diagnostics и planning развёртывания, а уже потом — централизованное управление несколькими bridge.

Полная англоязычная версия находится в [`ROADMAP.md`](ROADMAP.md), а краткая русская - в [`ROADMAP.ru.md`](ROADMAP.ru.md).

## Контракты runtime

Bridge уже рассматривает несколько runtime-поверхностей как операторские контракты:

- **Lifecycle publication** — startup и shutdown проходят через явные события `bridge.startup.started`, `bridge.startup.failed`, `bridge.startup.completed`, `bridge.shutdown.started` и `bridge.shutdown.completed`. Те же фазы отражаются в `startup_progress` и `runtime_info`.
- **Diagnostics и telemetry** — `/api/diagnostics` и `/api/bridge/telemetry` являются каноническими endpoint'ами для runtime inspection. Они публикуют `startup_progress`, `runtime_info`, состояние hook delivery и `contract_versions` для config schema и subprocess IPC protocol.
- **Runtime hooks** — `/api/hooks` отдает те же bridge/device events, что питают diagnostics, поэтому автоматики могут подписываться на стабильный event stream, а не парсить логи.

## Операторский UX

- **Onboarding и recovery** — header guidance показывает пятишаговый setup checklist, recovery notices и безопасные действия вроде reconnect speaker или reclaim Bluetooth management после release.
- **Повторная настройка Music Assistant** — раздел **Configuration → Music Assistant** можно открыть в любой момент, чтобы переподключиться к MA или перевести bridge на другой MA-инстанс. В Home Assistant addon кнопка **Sign in with Home Assistant** умеет по возможности тихо получить или переиспользовать MA token через Ingress, а при необходимости откатывается к обычному HA login flow.
- **Диагностика для support-flow** — **Submit bug report** скачивает masked diagnostics и открывает GitHub issue с предложенным описанием, уже собранным из текущей диагностики, recovery guidance и свежих issue logs.

## Карта документации

Полные инструкции и справка живут на docs site:

- [Установка](https://trudenboy.github.io/sendspin-bt-bridge/ru/installation/ha-addon/)
- [Конфигурация](https://trudenboy.github.io/sendspin-bt-bridge/ru/configuration/)
- [Веб-интерфейс](https://trudenboy.github.io/sendspin-bt-bridge/ru/web-ui/)
- [Устройства](https://trudenboy.github.io/sendspin-bt-bridge/ru/devices/)
- [API Reference](https://trudenboy.github.io/sendspin-bt-bridge/ru/api/)
- [Устранение неисправностей](https://trudenboy.github.io/sendspin-bt-bridge/ru/troubleshooting/)
- [Архитектура](https://trudenboy.github.io/sendspin-bt-bridge/ru/architecture/)
- [Тестовый стенд](https://trudenboy.github.io/sendspin-bt-bridge/ru/test-stand/)

## Сообщество и поддержка

- [GitHub Issues](https://github.com/trudenboy/sendspin-bt-bridge/issues)
- [Обсуждение в сообществе Music Assistant](https://github.com/orgs/music-assistant/discussions/5061)
- [Тема в сообществе Home Assistant](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- [Канал в Discord](https://discord.com/channels/330944238910963714/1479933490991599836)

## Ссылки по проекту

- [Участие в разработке](CONTRIBUTING.md)
- [Дорожная карта (RU)](ROADMAP.ru.md)
- [Roadmap (EN)](ROADMAP.md)
- [Лицензия](LICENSE)
- [История изменений](CHANGELOG.md)
- [История проекта](HISTORY.ru.md)
