# Community Engagement Checklist

Шаги для получения официального признания в экосистеме Music Assistant.

---

## Шаг 1 — MA Discord

- Зайди в сервер MA Discord: [discord.gg/kaVm8hGpne](https://discord.gg/kaVm8hGpne)
- Канал: `#addons` или `#developer`
- Напиши пост с представлением проекта:
  - Что делает (мост Bluetooth-колонок через Sendspin)
  - Варианты развёртывания (HA Addon, Docker, Proxmox LXC)
  - Ссылка на репозиторий: https://github.com/trudenboy/sendspin-bt-bridge
  - Скриншоты веб-интерфейса
  - Тег релевантных мейнтейнеров MA

---

## Шаг 2 — GitHub Discussion (Show and Tell) ✅

- ✅ Открыто обсуждение: https://github.com/orgs/music-assistant/discussions/5061
- Категория: **Show and Tell**
- Заголовок: `Sendspin Bluetooth Bridge — multi-device, multiroom, 4 deployment options (companion project)`
- Включено:
  - Описание функций
  - Варианты развёртывания
  - Скриншот веб-UI + инфографика
  - Ссылку на репозиторий
  - Ссылку на оригинальное обсуждение [#4677](https://github.com/orgs/music-assistant/discussions/4677)

---

## Шаг 3 — Community Extensions List

- Сделай форк репозитория `music-assistant/music-assistant.io`
- Добавь запись на странице community-extensions:
  - Название: **Sendspin Bluetooth Bridge**
  - Описание: Bridge Music Assistant Sendspin protocol to Bluetooth speakers
  - Ссылка: https://github.com/trudenboy/sendspin-bt-bridge
  - Категория: **Player**
- Открой Pull Request

---

## Шаг 4 — MA Docs Team

- Свяжись с командой документации MA
- Цель: добавить ссылку на https://www.music-assistant.io/player-support/sendspin/
- Страница документирует протокол Sendspin — должна ссылаться на известные клиентские реализации

---

## Шаг 5 — Upstream PR (loryanstrant/Sendspin-client)

- Открой PR в https://github.com/loryanstrant/Sendspin-client
- Предложи ключевые улучшения из этого форка:
  - Поддержка нескольких устройств одновременно
  - Исправления безопасности (shell injection, XSS)
  - Фикс LXC PulseAudio HFP
- Это укрепит отношения с апстримом и повысит доверие сообщества

---

## Шаг 6 — Лицензия (опционально)

- Текущий `LICENSE`: MIT, Copyright 2026 Loryan Strant
- Если в проект добавлен значительный оригинальный код — обсуди с Loryan Strant
  добавление себя как соавтора копирайта
- MA использует Apache 2.0, но для companion-проектов MIT допустим
