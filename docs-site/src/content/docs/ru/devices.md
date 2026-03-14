---
title: Устройства и адаптеры
description: Добавление колонок, привязка адаптеров и актуальные device-management flows в Sendspin Bluetooth Bridge
---

## Empty state и первый запуск

![Пустое состояние dashboard с кнопкой Scan for devices](/sendspin-bt-bridge/screenshots/screenshot-empty-no-devices.png)

Если bridge уже видит Bluetooth-адаптер, но ни одной колонки ещё не настроено, dashboard показывает **Scan for devices**. Этот shortcut теперь переводит прямо в **Configuration → Devices → Discovery & import** и сразу запускает сканирование.

Если же адаптер вообще не найден, empty state предлагает **Add adapter**, открывает **Configuration → Bluetooth**, создаёт пустую строку адаптера и ставит фокус в первое поле.

## Добавление колонки

![Вкладка Devices с fleet table и discovery workflow](/sendspin-bt-bridge/screenshots/screenshot-config-devices.png)

Рекомендуемый сценарий такой:

1. Откройте **Configuration → Devices**.
2. Нажмите **Scan** в карточке **Discovery & import**.
3. В результатах выберите **Add** или **Add & Pair**.
4. Заполните имя плеера и дополнительные поля.
5. Сохраните конфиг и при необходимости выполните перезапуск.

### Поведение сканирования

- Сканирование идёт в фоне и интерфейс периодически опрашивает результат.
- Найденные устройства можно сразу добавить в fleet table.
- **Add & Pair** выполняет pairing/trust/connect до добавления строки в конфиг.
- После завершения сканирования кнопка уходит в cooldown и не даёт спамить повторные попытки.

### Already paired

Блок **Already paired** позволяет импортировать устройства, которые хост уже знает, без повторного сканирования.

## Таблица Device fleet

**Device fleet** — основное место для управления колонками.

| Колонка | За что отвечает |
|---|---|
| **Enabled** | Временно исключает устройство из запуска |
| **Player name** | Имя в Music Assistant |
| **MAC** | Bluetooth-адрес |
| **Adapter** | Привязка к конкретному контроллеру |
| **Port** | Пользовательский sendspin listener port |
| **Delay** | `static_delay_ms` |
| **Live** | Runtime badge из работающего bridge |
| **Remove** | Удаление строки из конфига |

При раскрытии строки появляются advanced-поля: **preferred format**, **listen host** и **keepalive interval**.

## Управление адаптерами

![Вкладка Bluetooth с naming адаптеров и recovery policy](/sendspin-bt-bridge/screenshots/screenshot-config-adapters.png)

Во вкладке **Bluetooth** доступны:

- понятные имена адаптеров,
- ручные adapter entries,
- refresh detection,
- policy-параметры восстановления,
- переключатель **Prefer SBC codec**.

### Привязка колонки к адаптеру

В поле **Adapter** можно указывать:

- `hci0`, `hci1` и т.д.,
- или MAC-адрес адаптера.

Использование MAC особенно удобно в LXC-окружениях, где имя `hciN` может меняться после перезагрузки.

## Глубокие ссылки из dashboard

Dashboard теперь возвращает вас в точное место редактирования, а не просто открывает общий раздел:

- **Шестерёнка устройства** → подсвечивает нужную строку в **Configuration → Devices**.
- **Shortcut/gear адаптера** → подсвечивает нужную строку в **Configuration → Bluetooth**.
- **Бейдж группы** → открывает соответствующие настройки группы в Music Assistant в новой вкладке.

## Grid и list режимы

Один и тот же fleet можно просматривать в двух layout'ах:

- **Grid view** для небольшого числа устройств.
- **List view** для больших fleet'ов, с sortable columns и expandable rows.

По умолчанию bridge переключается в **list view, если видно больше 6 устройств**, но ваш ручной выбор запоминается в браузере и используется при следующем открытии.

## Re-pair, Release и Reclaim

Из dashboard доступны такие действия:

| Действие | Когда использовать |
|---|---|
| **Reconnect** | Нужно принудительно переподключить BT без правки конфига |
| **Re-pair** | Pairing/trust state на хосте сломано или устарело |
| **Release** | Нужно временно вернуть колонку телефону или ПК |
| **Reclaim** | Нужно снова отдать Bluetooth-управление bridge |

**Release** не удаляет устройство из конфига — bridge просто перестаёт активно его переподключать до нажатия **Reclaim**.

## Delay tuning и keepalive

Для сложных колонок особенно полезны такие поля:

- **`static_delay_ms`** — компенсация различий Bluetooth-латентности при групповом воспроизведении.
- **`keepalive_interval`** — периодическая тишина, чтобы некоторые колонки не засыпали между треками.
- **`preferred_format`** — уменьшает ресэмплинг или нагрузку CPU в зависимости от настроек MA.
