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
- Подключает несколько устройств одновременно, с отдельным изолированным playback-процессом на каждую колонку.
- Даёт веб-интерфейс для настройки, Bluetooth pairing flow, диагностики, логов и бэкапа конфигурации.
- Поддерживает развёртывание как Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC и OpenWrt LXC.
- Позволяет масштабироваться на несколько комнат через несколько bridge-экземпляров против одного MA-сервера.

![Инфографика Sendspin Bluetooth Bridge — возможности, архитектура и варианты развёртывания](docs-site/public/screenshots/sbb_infographic_ru.png)

## Что потребуется

- Bluetooth-колонка или наушники — подойдёт любое устройство с профилем A2DP.
- Сервер [Music Assistant](https://www.music-assistant.io/) (v2.3+) с включённым провайдером [Sendspin](https://www.music-assistant.io/player-support/sendspin/).
- Linux-хост с USB или встроенным Bluetooth-адаптером — Raspberry Pi, NUC, Proxmox VM или Home Assistant OS.

Командная строка не нужна. Веб-интерфейс полностью берёт на себя поиск Bluetooth-устройств, сопряжение и настройку Music Assistant.

## Быстрый старт: Home Assistant

Самый быстрый путь — установить Home Assistant addon.

[![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

1. Добавьте репозиторий в Home Assistant.
2. Установите **Sendspin Bluetooth Bridge** через Add-on Store.
3. Запустите аддон и откройте веб-интерфейс из боковой панели HA.
4. Добавьте Bluetooth-колонки и подключите функции Music Assistant в **Configuration → Music Assistant**.

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
- **Мультирум** — один bridge на комнату или один bridge на несколько колонок. Несколько bridge-экземпляров работают с одним сервером Music Assistant для озвучки всего дома.
- **Пять вариантов развёртывания** — Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC и OpenWrt LXC — один и тот же bridge, один веб-интерфейс, одни и те же функции везде.
- **REST API и live-обновления** — 60+ эндпоинтов для автоматизации и поток статусов в реальном времени через SSE для кастомных дашбордов и интеграций.

## Коротко о roadmap

Дорожная карта теперь синхронизирована с **реальным состоянием кода**, а не со старым списком планируемых рефакторингов.

- **Сейчас:** довести до конца уже начатый v2 refactor — snapshot-first чтение, явное владение реестром устройств и уменьшение роли `state.py`.
- **Дальше:** формализовать IPC-контракты, event history, диагностику, telemetry и lifecycle конфигурации.
- **Потом:** усилить onboarding, recovery UX, подсказки по latency и capability-aware поведение UI/API.
- **После этого:** переходить к backend abstraction для v3 и только затем аккуратно добавлять соседние backend'ы вроде local sink или ALSA.

Полная англоязычная версия находится в [`ROADMAP.md`](ROADMAP.md), а краткая русская — в [`ROADMAP.ru.md`](ROADMAP.ru.md).

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
