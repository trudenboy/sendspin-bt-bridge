---
title: Bluetooth-колонки
description: Bluetooth-колонки и наушники, протестированные с Sendspin BT Bridge — подтверждённые, с оговорками и с задокументированными quirk-ами
---

На этой странице собраны полевые датапоинты по Bluetooth-колонкам и наушникам, через которые прогонялся мост. Разбивка:

- **Подтверждённые** — A2DP играет чисто под обычной нагрузкой. Если для этого потребовалась конкретная версия моста, версия BlueZ или experimental-тоггл — это указано в строке.
- **С оговорками** — играет, но требуется non-default опция, апгрейд хоста или workaround.
- **Documented quirks (без подтверждённого фикса)** — стабильно ломается так, как мост не может починить сам. Перечислено, чтобы будущие читатели быстрее узнавали симптом.

Каждая строка ссылается на оригинальный issue (или на инвентарь production-флота в [`CLAUDE.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CLAUDE.md#configured-bluetooth-devices-production)), чтобы можно было прочитать полный диагностический тред.

:::tip[Добавьте свою колонку]
Если вашей модели в списке нет, пожалуйста, [откройте issue](https://github.com/trudenboy/sendspin-bt-bridge/issues/new) с моделью, версией BlueZ, версией моста и бандлом `Diagnostics → Download`. Датапоинты — и **положительные**, и **отрицательные** — делают этот список полезным.
:::

## Подтверждённые

| Колонка / наушники | Источник | Заметка |
|---|---|---|
| **IKEA ENEBY20** | Production-флот ([`CLAUDE.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CLAUDE.md#configured-bluetooth-devices-production)) | A2DP, multiroom; на `hci0` (CSR8510 A10) |
| **IKEA ENEBY Portable** | Production-флот | Та же серия что ENEBY20 |
| **IKEA VAPPEBY** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) (chino-lu, Pi 5 + ASUS USB-BT500) | A2DP в multiroom на BlueZ 5.85 |
| **Yandex Mini 2** | Production-флот | На `hci1`; quirks standby режима описаны в [Troubleshooting](/ru/troubleshooting/) |
| **Lenco LS-500** | Production-флот | A2DP, на `hci1` |
| **Jam Heavy Metal** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | A2DP в multiroom на BlueZ 5.85 |
| **HMDX Jam** | [#166](https://github.com/trudenboy/sendspin-bt-bridge/issues/166) — починено в v2.60.2 | Потребовался explicit `Device1.ConnectProfile(A2DP_SINK_UUID)` потому что колонка также рекламирует A2DP source / HFP. Автоматически с v2.60.2 |
| **IKEA Kallsup** | [#166](https://github.com/trudenboy/sendspin-bt-bridge/issues/166), [#162](https://github.com/trudenboy/sendspin-bt-bridge/issues/162) — починено в v2.60.2 | Тот же A2DP-Sink ConnectProfile fallback что у HMDX Jam |
| **Xiaomi 小爱音箱 (Mi Speaker)** | [#172](https://github.com/trudenboy/sendspin-bt-bridge/issues/172) — адресовано в v2.61.0 | Stale BlueZ disk-cache очищается на remove; re-pair восстанавливает после `BlueZ has no record` |
| **EDIFIER B3 Soundbar** | [#123](https://github.com/trudenboy/sendspin-bt-bridge/issues/123) — v2.55.3 добавил sink-mute detection | Если когда-нибудь "audio плеит, но звука нет" — карточка устройства теперь покажет **Sink muted** с one-click **Unmute** |
| **Samsung Soundbar M360 M-Series** | [#254](https://github.com/trudenboy/sendspin-bt-bridge/issues/254) | Сама колонка в порядке. Изначальный отчёт о breaking adapter detection оказался не связан (адаптер пропал во время container update) |
| **Anker Soundcore 2 / Soundcore 3** | [#291](https://github.com/trudenboy/sendspin-bt-bridge/issues/291) | По одной — играют чисто. Две на одном адаптере упираются в BR/EDR airtime contention — берите один адаптер на 2–3 колонки (см. [Bluetooth-адаптеры › Сколько адаптеров](/ru/bluetooth-adapters/#how-many-adapters-do-i-need)) |
| **Sony STR-DN1080** (network/AV-ресивер) | [#161](https://github.com/trudenboy/sendspin-bt-bridge/issues/161) | На PipeWire-pulse: поднять `pulse_latency_msec` (Rowr21 достиг стабильного sync на 550 мс при AVR в группе) |

## С оговорками

| Колонка / наушники | Источник | Требование |
|---|---|---|
| **Sony WH-1000XM4** | [#269](https://github.com/trudenboy/sendspin-bt-bridge/issues/269) — подтверждено arisonpl | **BlueZ ≥ 5.79** (5.82 верифицирована) **и** мост ≥ v2.70.0. На BlueZ 5.66 (RPi OS Bookworm) AVDTP-collision fast-path-skip срабатывает неправильно; нужны обе половины апгрейда. См. [Troubleshooting › Reconnect loop on Sony WH-1000XM4](/ru/troubleshooting/) |
| **Samsung Q910B soundbar** | [#210](https://github.com/trudenboy/sendspin-bt-bridge/issues/210) | Нужен **Class of Device override `0x00010c`** на адаптере + рестарт HA, чтобы очистить залипший runtime-state BlueZ. У chipset'а ATS2851 неполная поддержка Linux, но CoD workaround разблокирует пэйринг. Описан в [Class of Device override — preset reference](/ru/troubleshooting/) |
| **Synergy S65** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | Работает на BlueZ **5.85**. BlueZ 5.86 ломает volume control именно для этой колонки — пин на 5.85, если она в группе |
| **JBL PartyBox Encore 2 (ENC ESS 2)** | [#213](https://github.com/trudenboy/sendspin-bt-bridge/issues/213) | **Работает соло.** Ломается при добавлении в multi-speaker группу: JBL "локает" контроллер, остальные колонки теряют звук. JBL-specific (TMAP 1.0 / PBP 1.0 / JBL PartyBoost peer modes). Стримьте на неё отдельно |
| **AfterShokz OpenMove** | Production-флот (configured, disabled) | Bone-conduction наушники; пэйрит/коннектит OK, но в reference-конфиге отключены, потому что это портативное устройство, которое уходит из радиуса. Работает пока в радиусе BT |

## Documented quirks (без подтверждённого фикса в треде)

| Колонка / наушники | Источник | Симптом |
|---|---|---|
| **HK Onyx Studio 3** | [#191](https://github.com/trudenboy/sendspin-bt-bridge/issues/191) | `ServicesResolved did not reach True within 10s` + `A2DP Sink ConnectProfile: UnknownObject`, колонка отваливается ~3 с после connect. Матчит класс регрессий [bluez/bluez#1098](https://github.com/bluez/bluez/issues/1098) / [#1922](https://github.com/bluez/bluez/issues/1922). **Reset & Reconnect** с карточки устройства иногда вытаскивает; постоянного фикса в треде не было |

## Как читать эти уровни

- **Подтверждённые** ≠ "работает на любом хосте". Чистая на PulseAudio 17 + BlueZ 5.82 колонка всё ещё может потребовать тюнинг `pulse_latency_msec` на PipeWire-pulse, или замену адаптера, если onboard-контроллер перегружен. Если сомневаетесь — сверьтесь с [Bluetooth-адаптеры](/ru/bluetooth-adapters/) и [Troubleshooting](/ru/troubleshooting/).
- Записи в **С оговорками** означают, что workaround **уже доступен в мосту или в доках** — не нужно собирать кастомный firmware.
- Раздел **Documented quirks** существует, чтобы будущий оператор быстро узнавал отпечаток, а не дебажил с нуля. Если найдёте workaround — расскажите, чтобы мы переклассифицировали колонку.

## Production-флот для справки

HAOS-стенд проекта прямо сейчас гоняет 3 enabled-колонки в продакшене:

| Колонка | MAC (masked) | Адаптер | Static delay |
|---|---|---|---|
| ENEBY20 | `FC:58:FA:EB:08:6C` | `hci0` (CSR8510 A10) | 0 мс |
| Yandex Mini 2 (007 a) | `2C:D2:6B:B8:EC:5B` | `hci1` (CSR8510 A10) | 0 мс |
| Lenco LS-500 | `30:21:0E:0A:AE:5A` | `hci1` | 0 мс |

Ещё три колонки (WH-1000XM4, ENEBY Portable, AfterShokz OpenMove) сконфигурированы, но disabled в этом флоте — они перечислены в [`CLAUDE.md`](https://github.com/trudenboy/sendspin-bt-bridge/blob/main/CLAUDE.md#configured-bluetooth-devices-production) для traceability.
