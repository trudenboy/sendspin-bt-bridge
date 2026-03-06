---
title: Sendspin Bluetooth Bridge
description: Превратите любую Bluetooth-колонку в плеер Music Assistant — мультирум без покупки нового оборудования
hero:
  tagline: Превратите Bluetooth-колонки в плееры Music Assistant — без нового железа и без облака
  image:
    file: ../../../assets/logo.svg
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


## Что это такое

У вас наверняка уже есть Bluetooth-колонки — портативная на кухне, беспроводные наушники, звуковая панель в спальне. **Sendspin Bluetooth Bridge** позволяет использовать их все в [Music Assistant](https://www.music-assistant.io/) без покупки нового оборудования.

После установки каждая Bluetooth-колонка появляется в Music Assistant как обычный плеер — точно так же, как Sonos или Chromecast. Можно играть музыку на одной колонке, синхронизировать несколько комнат одновременно или управлять всем с телефона или панели Home Assistant.

Всё работает только в вашей локальной сети: никаких облачных аккаунтов, подписок и интернета для воспроизведения не нужно.

![Веб-панель управления с 6 Bluetooth-колонками: статус воспроизведения, громкость и состояние синхронизации в реальном времени](/sendspin-bt-bridge/screenshots/screenshot-dashboard-full.png)

## Что нужно

- Raspberry Pi, компьютер с Home Assistant или любая Linux-машина в домашней сети
- Bluetooth-адаптер (большинство моделей Raspberry Pi имеют встроенный)
- Одна или несколько Bluetooth-колонок

## Возможности

<CardGrid>
  <Card title="Любая Bluetooth-колонка" icon="laptop">
    Работает с любой A2DP-колонкой — портативной, настольной, звуковой панелью, беспроводными наушниками. Без привязки к бренду.
  </Card>
  <Card title="Несколько колонок одновременно" icon="list-format">
    Подключите несколько колонок сразу. Каждая появляется как отдельный плеер в Music Assistant — играйте разные треки в разных комнатах или объедините их для синхронного звука.
  </Card>
  <Card title="Всегда на связи" icon="refresh">
    Каждые 10 секунд проверяет Bluetooth-соединения и переподключается автоматически при обрыве — без вашего участия.
  </Card>
  <Card title="Синхронный мультирум" icon="seti:clock">
    Колонки можно объединить в группу в Music Assistant для одновременного воспроизведения. Компенсация задержки (`static_delay_ms`) удерживает их в синхроне даже при разных размерах буфера A2DP.
  </Card>
  <Card title="Веб-интерфейс" icon="laptop">
    Живая панель управления показывает статус каждой колонки, трек, громкость и состояние синхронизации. Регулируйте громкость, выключайте звук или ставьте на паузу всё сразу — с телефона или компьютера.
  </Card>
  <Card title="Интеграция с Home Assistant" icon="setting">
    Устанавливается как нативный аддон HA. Колонки становятся медиаплеерами в HA — используйте их в автоматизациях, на панелях управления, с голосовым помощником и в сценах.
  </Card>
</CardGrid>

## Примеры использования

### Мультирум

Объедините две или больше Bluetooth-колонок в Music Assistant и воспроизводите один трек во всех комнатах одновременно. Мост компенсирует задержку Bluetooth, и звук остаётся синхронным — вы не услышите эхо, переходя из комнаты в комнату.

**Пример:** портативная колонка на кухне + наушники в спальне + звуковая панель в гостиной — всё играет один плейлист, управление с телефона через Music Assistant.

### Автоматизации в Home Assistant

Поскольку каждая Bluetooth-колонка становится объектом `media_player` в Home Assistant, её можно использовать в любой автоматизации:

```yaml
# Включить утреннюю сводку на кухонной колонке в 7:30
automation:
  trigger:
    platform: time
    at: "07:30:00"
  action:
    service: media_player.play_media
    target:
      entity_id: media_player.kitchen_speaker
    data:
      media_content_id: "https://feeds.example.com/news.mp3"
      media_content_type: music
```

Другие идеи:
- **Звонок в дверь** — воспроизвести мелодию на всех колонках, когда кто-то нажимает на звонок
- **Режим «спокойной ночи»** — плавно убавить громкость и поставить все колонки на паузу перед сном
- **Присутствие в комнате** — включить музыку на колонке при входе в комнату (с датчиком движения)
- **Голосовое объявление** — зачитать прогноз погоды через TTS каждое утро

### Headless Home Assistant

Если Home Assistant работает на Raspberry Pi со встроенным Bluetooth-адаптером, можно использовать его напрямую — дополнительного железа не нужно. Мост запускается как аддон рядом с HA и сразу открывает Bluetooth-колонки для Music Assistant.

<Aside type="tip">
  Если вы хотите охватить колонки в нескольких комнатах, а Raspberry Pi не добивает до всех, запустите мост на втором Raspberry Pi (или в контейнере Proxmox LXC) в другом конце дома — он подключится к тому же серверу Music Assistant.
</Aside>

## Развёртывание нескольких бриджей

Запустите несколько экземпляров моста на одном сервере Music Assistant, чтобы охватить все комнаты — каждый бридж обслуживает колонки в своей зоне Bluetooth-досягаемости.

![Диаграмма развёртывания: два бриджа с двумя BT-адаптерами каждый, четыре колонки в четырёх комнатах, все подключены к одному серверу Music Assistant](/sendspin-bt-bridge/diagrams/deployment-multiroom.svg)

[Открыть диаграмму multiroom (Mermaid)](/sendspin-bt-bridge/diagrams/multiroom-diagram/)

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
  <LinkCard title="Архитектура" href="/sendspin-bt-bridge/architecture/" description="Процессная модель, IPC, маршрутизация звука, автомат BT, аутентификация" />
  <LinkCard title="История проекта" href="https://github.com/trudenboy/sendspin-bt-bridge/blob/main/HISTORY.ru.md" description="Архитектурная эволюция, вехи, миграция v1 → v2" />
  <LinkCard title="API Reference" href="/sendspin-bt-bridge/api/" />
</CardGrid>
