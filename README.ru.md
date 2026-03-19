# [Sendspin](https://www.sendspin-audio.com/) Bluetooth Bridge

[![GitHub Release](https://img.shields.io/github/v/release/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/releases/latest)
[![Docker Pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Ftrudenboy%2Fsendspin-bt-bridge%2Fsendspin-bt-bridge&query=downloadCount&label=Docker%20Pulls&logo=docker&color=blue)](https://github.com/trudenboy/sendspin-bt-bridge/pkgs/container/sendspin-bt-bridge)
[![HA Installs](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Faddons.json&query=%24%5B%2285b1ecde_sendspin_bt_bridge%22%5D.total&label=HA%20Installs&logo=homeassistant&color=18bcf2)](https://analytics.home-assistant.io/apps/)
[![GitHub Stars](https://img.shields.io/github/stars/trudenboy/sendspin-bt-bridge?style=flat&logo=github)](https://github.com/trudenboy/sendspin-bt-bridge/stargazers)
[![Try Demo](https://img.shields.io/badge/Try_Demo-Live-brightgreen?style=flat&logo=render)](https://sendspin-demo.onrender.com)

[Read in English](README.md) · [Документация](https://trudenboy.github.io/sendspin-bt-bridge/ru/) · [Демо](https://sendspin-demo.onrender.com) · [История проекта](HISTORY.ru.md)

Превратите Bluetooth-колонки и наушники в нативные плееры [Music Assistant](https://www.music-assistant.io/) на протоколе [Sendspin](https://www.music-assistant.io/player-support/sendspin/).

Sendspin Bluetooth Bridge — это local-first мост для headless-сценариев на Home Assistant, Docker, Raspberry Pi и LXC. Каждое Bluetooth-устройство появляется в Music Assistant как отдельный плеер, а управление, диагностика и настройка доступны через веб-интерфейс.

## Что делает проект

- Превращает обычные Bluetooth-колонки и наушники в плееры Music Assistant.
- Подключает несколько устройств одновременно, с отдельным изолированным playback-процессом на каждую колонку.
- Даёт веб-интерфейс для настройки, Bluetooth pairing flow, диагностики, логов и бэкапа конфигурации.
- Поддерживает развёртывание как Home Assistant addon, Docker, Raspberry Pi, Proxmox VE LXC и OpenWrt LXC.
- Позволяет масштабироваться на несколько комнат через несколько bridge-экземпляров против одного MA-сервера.

![Инфографика Sendspin Bluetooth Bridge — возможности, архитектура и варианты развёртывания](docs-site/public/screenshots/sbb_infographic_ru.png)

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

- **Несколько устройств** — публикует несколько Bluetooth-колонок как отдельные плееры Music Assistant.
- **Изоляция по колонкам** — один subprocess на устройство для более предсказуемой маршрутизации и локализации сбоев.
- **Восстановление Bluetooth** — D-Bus-детекция отключений, резервный reconnect polling и защита от churn.
- **Интеграция с Music Assistant** — now playing, transport controls, group volume и token flows.
- **Веб-интерфейс** — pairing, управление адаптерами, диагностика, логи, backup/restore конфигурации и проверка обновлений.
- **Гибкое развёртывание** — Home Assistant addon, Docker, Raspberry Pi и нативные LXC-сценарии.
- **Планирование нескольких bridge** — `WEB_PORT`, `BASE_LISTEN_PORT` и per-device listener overrides для больших установок.
- **REST API и live updates** — удобные для автоматизации эндпоинты и статус-обновления через SSE.

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
- [Лицензия](LICENSE)
- [История изменений](CHANGELOG.md)
- [История проекта](HISTORY.ru.md)
