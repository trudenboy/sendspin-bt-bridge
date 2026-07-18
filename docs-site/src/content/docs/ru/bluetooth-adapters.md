---
title: Bluetooth-адаптеры
description: Рекомендованные USB Bluetooth-адаптеры для мультирум-стриминга через Sendspin BT Bridge на HAOS, Docker и LXC
---

## Почему адаптер важен

Bridge одновременно транслирует A2DP-аудио на все настроенные колонки.
Каждый SBC-поток потребляет ~345 кбит/с Bluetooth-полосы, поэтому выбор
адаптера напрямую влияет на стабильность соединений, дальность и
количество колонок, которые можно обслуживать с одного контроллера.

### Критерии выбора

| Критерий | Почему важен |
|---|---|
| **Bluetooth 5.0+** | 4× дальность LE, лучшая coexistence при множественных подключениях |
| **Чипсет с нативной поддержкой btusb** | Plug-and-play на HAOS без установки драйверов |
| **Firmware в linux-firmware** | HAOS не позволяет ставить пакеты — firmware должен быть в ядре |
| **USB 2.0 nano форм-фактор** | Чистый проброс через Proxmox, не мешает соседним портам |
| **A2DP + SBC** | Обязательно для аудиостриминга |
| **Стабильный reconnect** | Headless-система без UI для ручного восстановления |

## Сколько адаптеров нужно?

Один Bluetooth-адаптер поддерживает до 7 активных ACL-соединений, но
A2DP-стриминг потребляет значительную полосу. Для надёжной работы:

| Колонки | Рекомендуемое кол-во адаптеров |
|---|---|
| 1–3 | 1 адаптер |
| 4–5 | 2 адаптера (2–3 колонки на каждый) |
| 6+ | 3+ адаптера, один на 2–3 колонки |

:::tip[Референс из продакшена]
Собственный тестовый стенд проекта (HAOS) работает с 2× CSR8510 A10 и 6
настроенными колонками (3 на адаптер) и мигрирует на донглы RTL8761B для
улучшения дальности и стабильности BT 5.0.
:::

## Рекомендованные адаптеры

Все перечисленные ниже адаптеры используют чипсет **Realtek RTL8761B** —
де-факто стандарт для BT 5.0 USB-донглов на Linux. Драйвер `btusb`
распознаёт их начиная с ядра 5.8, а нужная прошивка
(`rtl_bt/rtl8761bu_fw.bin`) входит в `linux-firmware` с 2020 года.

### 1. TP-Link UB500 (v1 / v2) — лучший выбор

| Параметр | Значение |
|---|---|
| Чипсет | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Драйвер Linux | btusb (ядро ≥ 5.8) |
| USB ID | `2357:0604` |
| Дальность | ~20 м (Class 1.5) |
| Цена | ~$12–15 / ~1000–1500 ₽ |

Самый протестированный BT 5.0 nano-донгл на Linux. Firmware включён в
каждый современный linux-firmware, HAOS подхватывает адаптер сразу после
USB-проброса.

:::caution[Внимание к версии]
Покупайте именно **v1** или **v2**. Ревизия **v3** имеет чипсет BT 5.4 с
непроверенной совместимостью с HAOS.
:::

### 2. ASUS USB-BT500 — проверенная альтернатива

| Параметр | Значение |
|---|---|
| Чипсет | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Драйвер Linux | btusb (ядро ≥ 5.14 по USB ID) |
| USB ID | `0b05:190e` |
| Дальность | ~10 м (Classic / A2DP) |
| Цена | ~$15–20 / ~1500–2000 ₽ |

Тот же чипсет RTL8761B в чуть лучше экранированном корпусе ASUS. Более
890 отчётов на linux-hardware.org и хорошая документация в сообществе
Home Assistant.

### 3. Plugable USB-BT5

| Параметр | Значение |
|---|---|
| Чипсет | Realtek RTL8761B |
| Bluetooth | 5.0 (BR/EDR + LE) |
| Дальность | ~40 м (LE), ~10 м (Classic) |
| Цена | ~$19 / ~1800 ₽ |

Гарантия 2 года и пожизненная техподдержка. Страница продукта заявляет
«incompatible with Linux», но лежащий в основе RTL8761B отлично работает
через `btusb`.

### 4. EDUP EP-B3536 — вариант BT 5.1

| Параметр | Значение |
|---|---|
| Чипсет | Realtek RTL8761BUV |
| Bluetooth | 5.1 |
| Цена | ~$10–12 / ~900–1200 ₽ |

Эволюция RTL8761B с BT 5.1 direction finding (не критично для A2DP, но
приятный бонус). Совместимый драйвер `btusb`; может потребоваться более
свежая версия linux-firmware для firmware blob.

### 5. Zexmte / MPOW BT 5.0 Nano — бюджетный вариант

| Параметр | Значение |
|---|---|
| Чипсет | Realtek RTL8761B (номинально) |
| Bluetooth | 5.0 |
| Цена | ~$8–10 / ~700–1000 ₽ |

Самый дешёвый вариант на RTL8761B. Подходит, если нужно купить несколько
адаптеров сразу. После получения проверьте USB ID — в некоторых партиях
может оказаться другой чипсет.

## Сводная таблица

| # | Модель | Чипсет | BT | Ядро Linux | Цена | Рейтинг |
|---|---|---|---|---|---|---|
| 1 | TP-Link UB500 v1/v2 | RTL8761B | 5.0 | ≥ 5.8 | ~$12 | ⭐⭐⭐⭐⭐ |
| 2 | ASUS USB-BT500 | RTL8761B | 5.0 | ≥ 5.14 | ~$17 | ⭐⭐⭐⭐⭐ |
| 3 | Plugable USB-BT5 | RTL8761B | 5.0 | ≥ 5.8 | ~$19 | ⭐⭐⭐⭐ |
| 4 | EDUP EP-B3536 | RTL8761BUV | 5.1 | ≥ 5.8 | ~$11 | ⭐⭐⭐⭐ |
| 5 | Zexmte BT 5.0 | RTL8761B | 5.0 | ≥ 5.8 | ~$9 | ⭐⭐⭐ |

## Coexistence USB BT-донгла со встроенным WiFi на Raspberry Pi

:::caution[2.4 GHz coexistence на Pi 4 / Pi 5]
Встроенный WiFi (BCM43455) и любой USB BT-донгл делят ISM-диапазон 2.4 GHz. Если хост подключён к 2.4 GHz сети, конкуренция за эфир может приводить к росту счётчика `Tx excessive retries` в `iwconfig`, зависанию BlueZ, заиканию звука и подвисанию D-Bus-клиентов (например, `btop`). Если роутер поддерживает 5 GHz — переведите хост на 5 GHz. Подробности и команда `nmcli` — в разделе [Звук рассыпается и D-Bus подвисает на Raspberry Pi](/sendspin-bt-bridge/ru/troubleshooting/#звук-рассыпается-и-d-bus-подвисает-на-raspberry-pi-с-usb-bt-донглом) на странице Troubleshooting.
:::

## Программные обходы регрессий адаптера / BlueZ

Bridge содержит **advanced compatibility tools** для конкретных сбоев ядра, BlueZ или PulseAudio. Они находятся в **Configuration → Bluetooth → Advanced recovery workarounds** и скрыты до включения **Advanced compatibility tools** на вкладке General. UI блокирует неподдерживаемые текущим хостом механизмы и показывает причину. Подробности — на [странице Web UI](/sendspin-bt-bridge/ru/web-ui/#расширенные-bluetooth-compatibility-tools).

| Симптом | Тоггл |
|---|---|
| Колонка подключается, но PulseAudio не видит sink (dual-role регрессия BlueZ 5.86, [bluez/bluez#1922](https://github.com/bluez/bluez/issues/1922)) | `EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE` |
| Sink появляется через раз или только после ручного переподключения | `EXPERIMENTAL_PA_MODULE_RELOAD` |
| Весь адаптер замолкает под нагрузкой, и bridge не может его восстановить | `EXPERIMENTAL_ADAPTER_AUTO_RECOVERY` (rfkill check → MGMT/HCI power-cycle → optional USB reset) |
| Не получается допарить вторую колонку на одном адаптере, пока первая стримит | **Temporarily disconnect other speakers** в Scan modal |
| Колонка вообще не отправляет SSP-подтверждение, и bridge ждёт бесконечно | **NoInputNoOutput pair agent** в Scan modal |
| Multi-profile устройство не завершает pairing без HFP/HSP | включите **Authorize HFP/HSP for this pair** для одной попытки; playback остаётся A2DP-only |

Это точечные обезболивающие — их не нужно включать «на всякий случай». Pairing overrides одноразовые, сбрасываются после каждого запроса, а pairing-агент принимает обращения только от выбранного MAC.

## Чего избегать

| Адаптер / чипсет | Проблема |
|---|---|
| **ZEXMTE BT 5.3 Long Range** (антенна 180 м, ASIN `B0CP5WQ7L8`) | Детектится, пэйрится, играет — но A2DP music streaming нестабилен (community report, [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718)) |
| **TP-Link UB500 Plus** (BT 5.3 с регулируемой внешней антенной, ASIN `B0DHJHMHFS`) | То же — детектится и пэйрится, A2DP нестабилен. Берите обычный UB500 Nano вместо этого. Community report, [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718) |
| **CSR8510 A10** | BT 4.0, ограниченная дальность (~10 м), устаревший чип |
| **Broadcom BCM20702** | BT 4.0, проблемы с загрузкой firmware на immutable-системах |
| **Qualcomm QCA61x4** | Требует проприетарный firmware, нестабилен с bluez |
| **TP-Link UB500 v3** | BT 5.4 с другим чипсетом — совместимость с HAOS не подтверждена |
| **WiFi + BT combo** | Конфликт с существующим WiFi, сложный USB-проброс |
| **BT 5.2+ LE Audio донглы** | Кодек LC3 пока не поддерживается PulseAudio 17 |
| **aptX / Snapdragon Sound transmitters** (например Creative BT-W6, `B0DG34HRNC`) | Проприетарный стек; мост стримит строго через A2DP/SBC |

:::tip[Паттерн из community-отчётов]
**Long-range / high-gain-antenna варианты BT 5.3+ донглов на бумаге выглядят соблазнительно, но на практике стабильно проигрывают по A2DP-стримингу.** Связка новый Realtek + большая антенна + BlueZ ≥ 5.78 firmware-quirk-и сейчас хрупче, чем скучный `RTL8761B` BT 5.0 nano. Если нужен больший радиус — берите **активный USB-удлинитель** (5–10 м с repeater'ом) вместо long-range донгла.
:::

## Community-tested адаптеры (Amazon ASIN)

Community-датапоинты из [#295](https://github.com/trudenboy/sendspin-bt-bridge/issues/295#issuecomment-4441986718) (sirs2k, протестировано на HAOS, hci0). Все четыре детектятся и пэйрятся; различает их поведение A2DP music streaming:

| Вердикт | Продукт | ASIN | BT | Заметка |
|---|---|---|---|---|
| ⭐⭐⭐⭐⭐ Best | UGREEN 80889 USB Bluetooth 5.0 Adapter | [`B08R8992YC`](https://www.amazon.com.au/dp/B08R8992YC) | 5.0 | Realtek RTL8761B nano, модель 80889 |
| ⭐⭐⭐⭐ Good | TP-Link UB500 Nano (UK Version) | [`B09C25VRXD`](https://www.amazon.com.au/dp/B09C25VRXD) | 5.0 | Та же v1/v2 железка, что и глобальный TP-Link UB500 |
| ❌ Avoid | TP-Link UB500 **Plus** (BT 5.3 + внешняя антенна) | [`B0DHJHMHFS`](https://www.amazon.com.au/dp/B0DHJHMHFS) | 5.3 | Пэйрится и играет, A2DP music нестабилен |
| ❌ Avoid | ZEXMTE BT 5.3 Long Range (антенна 180 м) | [`B0CP5WQ7L8`](https://www.amazon.com.au/dp/B0CP5WQ7L8) | 5.3 | Пэйрится и играет, A2DP music нестабилен |

Есть свой датапоинт — модель, ASIN, версия BlueZ, держит ли A2DP под нагрузкой? Пожалуйста, [откройте issue](https://github.com/trudenboy/sendspin-bt-bridge/issues/new) — будущие читатели поблагодарят.

## Миграция с CSR8510 на RTL8761B

Если вы обновляете старые адаптеры CSR8510 A10:

1. Купите 2× TP-Link UB500 v1/v2 (или любой RTL8761B-донгл из списка выше).
2. **Proxmox**: обновите USB device mappings на новые VID:PID.
3. **HAOS**: адаптеры подхватятся автоматически (`btusb` + `linux-firmware`).
4. Проверьте `bluetoothctl list` — должны быть видны два контроллера.
5. Обновите MAC-адреса адаптеров в конфигурации bridge (hci0 / hci1).
6. Выполните re-pair для каждой колонки и протестируйте A2DP.
7. Наблюдайте за стабильностью reconnect в течение 24 часов перед тем, как считать миграцию завершённой.

## Схема USB-проброса через Proxmox

Типичная конфигурация с двумя адаптерами для 4–5 колонок:

```
Proxmox Host
├── USB Mapping "Audio"  → TP-Link UB500 #1 (hci0) → 2–3 колонки
├── USB Mapping "BT2"    → TP-Link UB500 #2 (hci1) → 2 колонки
└── HAOS VM
    └── Sendspin BT Bridge
        ├── BluetoothManager (hci0)
        └── BluetoothManager (hci1)
```

Подробнее о привязке колонок к адаптерам и управлении устройствами —
в разделе [Устройства и адаптеры](/sendspin-bt-bridge/ru/devices/).
