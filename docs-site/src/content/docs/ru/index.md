---
title: Sendspin Bluetooth Bridge
description: Превратите Bluetooth-колонки и наушники в плееры Music Assistant — аддон Home Assistant, Docker, Raspberry Pi и LXC
hero:
  tagline: Превратите Bluetooth-колонки в плееры Music Assistant — локально, headless и с поддержкой мультирума
  image:
    file: ../../../assets/logo.svg
  actions:
    - text: Установить
      link: /sendspin-bt-bridge/installation/ha-addon/
      icon: right-arrow
      variant: primary
    - text: Сравнить развёртывания
      link: '#варианты-развёртывания'
      icon: list-format
    - text: GitHub
      link: https://github.com/trudenboy/sendspin-bt-bridge
      icon: github
      variant: minimal
---

import { Aside } from '@astrojs/starlight/components';

![Инфографика Sendspin Bluetooth Bridge — возможности, архитектура и варианты развёртывания](/sendspin-bt-bridge/screenshots/sbb_infographic_ru.png)

## Что это такое

**Sendspin Bluetooth Bridge** превращает Bluetooth-колонки и наушники в нативные плееры [Music Assistant](https://www.music-assistant.io/), подключая их к протоколу [Sendspin](https://www.music-assistant.io/player-support/sendspin/) в MA.

Каждое настроенное Bluetooth-устройство появляется в Music Assistant как отдельный плеер. Можно оставить всё внутри локальной сети, объединять комнаты в группы, управлять Bluetooth через веб-интерфейс и запускать мост в Home Assistant, Docker, на Raspberry Pi, Proxmox VE или OpenWrt.

![Веб-панель управления с несколькими Bluetooth-колонками и живыми статусами воспроизведения](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

## Что важно в текущей релизной линии

<CardGrid>
  <Card title="Один subprocess на колонку" icon="seti:play-list">
    Bridge использует многопроцессный рантайм: главный процесс ведёт Bluetooth, API и UI, а каждая колонка работает в собственном Sendspin-daemon subprocess с отдельной аудио-маршрутизацией.
  </Card>
  <Card title="Гибкое планирование портов" icon="seti:terminal">
    Глобальные override-параметры <code>WEB_PORT</code> и <code>BASE_LISTEN_PORT</code> упрощают запуск нескольких bridge-инстансов или параллельных треков аддона HA на одном хосте.
  </Card>
  <Card title="Переопределения на уровне устройства" icon="setting">
    Для сложных сетевых схем можно закрепить плеер за собственным <code>listen_port</code> и задать рекламируемый адрес через <code>listen_host</code>.
  </Card>
  <Card title="Треки аддона HA" icon="seti:flag">
    Stable, RC и beta аддоны используют разные ingress-порты и диапазоны player-port, поэтому их проще различать и безопаснее тестировать параллельно.
  </Card>
  <Card title="Переподключение и диагностика" icon="refresh">
    D-Bus-детекция отключений, резервный polling, runtime diagnostics и SSE-обновления упрощают headless-развёртывания.
  </Card>
  <Card title="Веб-интерфейс и API" icon="laptop">
    Через панель можно выполнять pairing, смотреть диагностику и логи, проверять обновления, делать backup/restore конфига и связывать bridge с Music Assistant; всё это доступно и через REST API.
  </Card>
</CardGrid>

<Aside type="caution">
  Можно запускать несколько bridge-инстансов против одного сервера Music Assistant, включая несколько треков аддона HA на одном HAOS-хосте. Но <strong>не</strong> назначайте одну и ту же Bluetooth-колонку в несколько работающих bridge/addon одновременно.
</Aside>

## Варианты развёртывания

| | Home Assistant Addon | Docker / Raspberry Pi | Proxmox / OpenWrt LXC |
|---|---|---|---|
| Установка | Магазин аддонов | `docker compose up -d` | Скрипт на хосте |
| Веб-интерфейс | HA Ingress (`8080` / `8081` / `8082`) + опциональный прямой `WEB_PORT` listener | Прямой `WEB_PORT` listener (по умолчанию `8080`) | Прямой `WEB_PORT` listener (по умолчанию `8080`) |
| Порты плееров | Channel default `BASE_LISTEN_PORT` (`8928+`, `9028+`, `9128+`) | `BASE_LISTEN_PORT` (по умолчанию `8928+`) | `BASE_LISTEN_PORT` (по умолчанию `8928+`) |
| Bluetooth stack | `bluetoothd` хоста через Supervisor/runtime mounts | `bluetoothd` хоста через D-Bus | `bluetoothd` хоста через D-Bus bridge |
| Аудио | Аудиомост HA | PulseAudio / PipeWire хоста | PulseAudio внутри контейнера |
| Для кого | Пользователи HAOS / Supervised | Обычные Linux-хосты и Raspberry Pi | Proxmox VE, роутеры, appliance-сценарии |

## Развёртывание нескольких bridge

Запустите несколько bridge-инстансов против одного сервера Music Assistant, чтобы покрыть все комнаты — каждый bridge обслуживает колонки в своей Bluetooth-зоне.

[![Схема развёртывания: план этажа с зонами и адаптерами](/sendspin-bt-bridge/diagrams/multiroom-diagram.png)](/sendspin-bt-bridge/diagrams/multiroom-diagram/)

## С чего начать

<CardGrid>
  <LinkCard title="Установить в Home Assistant" href="/sendspin-bt-bridge/installation/ha-addon/" description="Stable, RC и beta tracks, поведение ingress и direct listener" />
  <LinkCard title="Установить через Docker" href="/sendspin-bt-bridge/installation/docker/" description="Универсальная установка на Linux-хост с override-параметрами WEB_PORT и BASE_LISTEN_PORT" />
  <LinkCard title="Установить на Raspberry Pi" href="/sendspin-bt-bridge/installation/raspberry-pi/" description="Docker-гайд для Pi, pre-flight check и one-liner installer" />
  <LinkCard title="Установить в Proxmox / OpenWrt LXC" href="/sendspin-bt-bridge/installation/lxc/" description="Нативное LXC-развёртывание с использованием Bluetooth-стека хоста через D-Bus" />
  <LinkCard title="Конфигурация" href="/sendspin-bt-bridge/configuration/" description="Настройки bridge, поля устройств, адаптеры, auth и логика обновлений" />
  <LinkCard title="Архитектура" href="/sendspin-bt-bridge/architecture/" description="Модель процессов, IPC, маршрутизация аудио, lifecycle Bluetooth и HA ingress" />
  <LinkCard title="API Reference" href="/sendspin-bt-bridge/api/" description="REST-эндпоинты для статуса, диагностики, Bluetooth, Music Assistant и обновлений" />
  <LinkCard title="История релизов" href="https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CHANGELOG.md" description="Актуальные release notes, включая изменения v2.40.5 по портам и HA-track" />
</CardGrid>

## Сообщество

- [Обсуждение в сообществе MA](https://github.com/orgs/music-assistant/discussions/5061)
- [Тема на HA Community](https://community.home-assistant.io/t/sendspin-bluetooth-bridge-turn-any-bt-speaker-into-an-ma-player-and-ha/993762)
- [Канал в Discord](https://discord.com/channels/330944238910963714/1479933490991599836)
