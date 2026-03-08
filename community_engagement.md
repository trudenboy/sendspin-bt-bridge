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

## Шаг 3 — Community Extensions List ✅

- ✅ PR открыт: https://github.com/music-assistant/music-assistant.io/pull/527
- Добавлено в два места:
  1. **Sendspin player provider page** → таблица "Supported Clients"
  2. **Community Extensions** → секция с описанием и скриншотом
- Ветка: `add-sendspin-bt-bridge` → `beta`

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

---

## Шаг 7 — Reddit (r/homeassistant)

- Сабреддит: [r/homeassistant](https://www.reddit.com/r/homeassistant/)
- Flair: `I Made This!`
- Формат: Gallery post (инфографика + скриншоты) + развёрнутый комментарий от автора
- Лучшее время: вторник–четверг, 14:00–17:00 UTC
- Требования перед публикацией:
  - Активный аккаунт с полезными комментариями в сабреддите
  - Соблюдение правила 90/10 (не более 10% промоушн)
- Изображения для gallery:
  1. `sbb_infographic_en.png` — инфографика (первое изображение, главный hook)
  2. `screenshot-dashboard-full.png` — полный дашборд
  3. `screenshot-device-card-playing.png` — карточка устройства
  4. `ma-players.png` — вид плееров в Music Assistant
  5. `screenshot-diagnostics.png` — панель диагностики
  6. `multiroom-floorplan.png` — мультирум схема (опционально)
- Заголовок: `I built a Bluetooth Bridge for Music Assistant — multi-device multiroom audio from any BT speaker (HA Addon / Docker / Proxmox LXC)`
- Содержание поста:
  - Проблема: MA не поддерживает BT-колонки нативно
  - Решение: мост, превращающий любую BT-колонку в полноценный MA-плеер
  - Ключевые фичи: multi-device, multiroom sync, auto-reconnect, web UI, REST API
  - 4 варианта развёртывания
  - Ссылки: GitHub, документация, MA Discussion (#5061)
  - Призыв к обратной связи и feature requests
- После публикации: активно отвечать на комментарии в первые 24 часа
