---
title: Установка — Home Assistant Addon
description: Установка Sendspin Bluetooth Bridge как аддона Home Assistant с пояснением stable/RC/beta-треков и планированием портов
---

import { Aside, Steps } from '@astrojs/starlight/components';

## Какой аддон выбрать?

| Аддон | Для чего подходит | Ingress-порт | Базовый player-port | Поведение при старте |
|---|---|---:|---:|---|
| **Stable** | Обычное ежедневное использование | `8080` | `8928` | Auto |
| **RC** | Тестирование release candidate | `8081` | `9028` | Manual |
| **Beta** | Самые ранние prerelease-сборки | `8082` | `9128` | Manual |

<Aside type="caution">
  На одном HAOS-хосте можно установить несколько треков аддона, но <strong>не</strong> настраивайте одну и ту же Bluetooth-колонку в нескольких одновременно работающих аддонах. У Bluetooth-устройства может быть только одно активное подключение.
</Aside>

## Требования

- Home Assistant OS или Supervised
- Bluetooth-адаптер, доступный хосту HA
- Запущенный Music Assistant в вашей сети

## Установка

<Steps>

1. **Добавьте репозиторий аддонов**

   Используйте кнопку для автоматического добавления:

   [![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

   Или вручную: **Настройки → Аддоны → Магазин аддонов → ⋮ → Репозитории** и добавьте:

   ```
   https://github.com/trudenboy/sendspin-bt-bridge
   ```

2. **Выберите вариант аддона**

   Для стабильного трека установите **Sendspin Bluetooth Bridge**. Варианты **RC** и **Beta** ставьте только если вам действительно нужны prerelease-сборки.

3. **Настройте аддон**

   На вкладке **Configuration**:

   ```yaml
   sendspin_server: auto
   sendspin_port: 9000
   web_port: 8090                 # опционально: дополнительный прямой listener в host network
   base_listen_port: 8928         # опционально: базовый порт для плееров без listen_port
   update_channel: stable         # влияет только на проверку обновлений, а не на смену варианта аддона
   bluetooth_devices:
     - mac: "AA:BB:CC:DD:EE:FF"
       player_name: "Колонка в гостиной"
     - mac: "11:22:33:44:55:66"
       player_name: "Колонка на кухне"
       adapter: hci1
       static_delay_ms: -500
       listen_port: 8935          # опционально: переопределение Sendspin-порта для устройства
       listen_host: 192.168.1.50  # опционально: рекламируемый host/IP в отображаемом URL
   ```

4. **Запустите аддон**

   Запустите аддон. Stable по умолчанию стартует автоматически; RC и beta по умолчанию запускаются вручную, чтобы prerelease-установки было проще держать отдельно.

5. **Подключите функции Music Assistant**

   Откройте веб-интерфейс → **Configuration → Music Assistant** и нажмите **🏠 Sign in with Home Assistant**, если хотите получить метаданные MA, транспортные кнопки, queue actions и group-volume sync.

</Steps>

## Как работают порты в режиме аддона

- **Ingress всегда фиксирован для каждого трека.** Stable использует `8080`, RC — `8081`, beta — `8082`.
- **`web_port` не заменяет ingress.** Он открывает дополнительный прямой listener в сети хоста HA. Ссылка в sidebar и кнопка **Open Web UI** по-прежнему ведут через HA Ingress.
- **`base_listen_port` задаёт базовый диапазон Sendspin player-port** для устройств без явного `listen_port`.
- **Поле `listen_port` у устройства имеет приоритет.** Используйте его, если конкретной колонке нужен фиксированный порт.
- **Поле `listen_host` меняет только рекламируемый host/IP.** Внутри плеер всё равно bind'ится на `0.0.0.0`.

## Как открыть веб-интерфейс

Используйте кнопку **Open Web UI** на странице аддона или ссылку в боковой панели HA — это основной ingress-путь.

Если задан `web_port`, появляется и прямой URL в сети хоста HA:

```text
http://<ip-хоста-ha>:<web_port>
```

Это удобно для прямой диагностики и API-доступа, но ingress остаётся основным HA-интегрированным вариантом.

## Семантика канала обновлений

- **Установленный вариант аддона** определяет, на каком кодовом треке вы реально находитесь: stable, RC или beta.
- Опция **`update_channel`** лишь задаёт, какие релизы должен проверять встроенный updater (`stable`, `rc` или `beta`).
- Изменение `update_channel` **не** переключает установленный трек аддона.
- Чтобы перейти со stable на RC/beta, установите соответствующий вариант аддона из Add-on Store.

## Аудио-маршрутизация на HA OS

Аддон запрашивает `audio: true`, поэтому Home Assistant автоматически прокидывает аудиомост. Ручной монтировки сокетов PulseAudio/PipeWire не требуется.

## Применение изменений конфигурации

Изменения устройств, адаптеров, `web_port`, `base_listen_port` и настроек подключения к Music Assistant применяются после перезапуска. Используйте **Restart** на странице аддона или **Save & Restart** в веб-интерфейсе.

<Aside type="tip">
  Если Music Assistant не видит плееры, проверьте, что в MA включён провайдер **Sendspin**.
</Aside>
