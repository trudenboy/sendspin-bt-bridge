---
title: Sendspin Bluetooth Bridge
description: Bluetooth-мост для Music Assistant — подключает Bluetooth-колонки к протоколу Sendspin MA
hero:
  tagline: Подключите Bluetooth-колонки к Music Assistant без лишнего железа и облаков
  image:
    file: ../../assets/logo.svg
  actions:
    - text: Установить
      link: /sendspin-bt-bridge/installation/ha-addon/
      icon: right-arrow
      variant: primary
    - text: Настройка
      link: /sendspin-bt-bridge/configuration/
      icon: setting
    - text: GitHub
      link: https://github.com/trudenboy/sendspin-bt-bridge
      icon: github
      variant: minimal
---

import { Card, CardGrid, LinkCard } from '@astrojs/starlight/components';

## Что это такое

**Sendspin Bluetooth Bridge** — мост между [Music Assistant](https://www.music-assistant.io/) и Bluetooth-колонками. Запускает CLI-плеер `sendspin` как подпроцесс, управляет Bluetooth-подключениями через `bluetoothctl` и предоставляет веб-интерфейс для мониторинга и настройки. Работает на Raspberry Pi, в Home Assistant, в Docker и Proxmox LXC.

## Возможности

<CardGrid>
  <Card title="Несколько устройств" icon="list-format">
    Одновременное подключение нескольких Bluetooth-колонок. Каждая отображается как отдельный плеер в Music Assistant.
  </Card>
  <Card title="Авто-переподключение" icon="refresh">
    Мониторинг соединений каждые 10 с. При обрыве — автоматическое переподключение.
  </Card>
  <Card title="Веб-интерфейс" icon="laptop">
    Панель мониторинга в стиле Home Assistant. Управление громкостью, паузой, BT-адаптерами. Автоматическая тёмная/светлая тема.
  </Card>
  <Card title="PipeWire и PulseAudio" icon="setting">
    Автоматическое определение аудиосистемы хоста. Поддержка обеих систем без ручной настройки.
  </Card>
  <Card title="Группное управление" icon="bars">
    Регулировка громкости и отключение звука на всех плеерах одновременно из веб-интерфейса.
  </Card>
  <Card title="Компенсация задержки" icon="seti:clock">
    Поле `static_delay_ms` компенсирует буферную задержку A2DP для синхронизации группового воспроизведения.
  </Card>
</CardGrid>

## Варианты развёртывания

| | Home Assistant Addon | Docker Compose | Proxmox LXC |
|---|---|---|---|
| Установка | Магазин аддонов HA | `docker compose up` | Однострочный скрипт |
| Bluetooth | bluetoothd хоста через D-Bus | bluetoothd хоста через D-Bus | Собственный bluetoothd |
| Аудио | HA Supervisor bridge | PulseAudio/PipeWire хоста | Собственный PulseAudio |
| Настройка | Панель HA + веб UI | Веб UI на :8080 | Веб UI на :8080 |

<CardGrid>
  <LinkCard title="Установка: Home Assistant Addon" href="/sendspin-bt-bridge/installation/ha-addon/" />
  <LinkCard title="Установка: Docker Compose" href="/sendspin-bt-bridge/installation/docker/" />
  <LinkCard title="Установка: Proxmox LXC" href="/sendspin-bt-bridge/installation/lxc/" />
  <LinkCard title="Настройка" href="/sendspin-bt-bridge/configuration/" />
</CardGrid>
