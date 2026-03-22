---
title: Устранение неполадок
description: Решение частых проблем Sendspin Bluetooth Bridge с учётом текущего UI и deployment model
---

## Сначала посмотрите operator guidance, а потом уже SSH

Прежде чем идти в shell и логи, проверьте встроенные operator-facing поверхности:

- **health/status в шапке** и переключатель checklist
- **onboarding checklist** для пропущенных шагов настройки
- **recovery guidance** для actionable runtime-проблем вроде disconnect, missing sink или released devices
- **Diagnostics**, если нужен более глубокий runtime view за этими подсказками

Эти поверхности опираются на те же данные, что и `/api/diagnostics`, `/api/onboarding/assistant`, `/api/recovery/assistant` и `/api/operator/guidance`.

![Recovery guidance banner с actionable operator recommendations](/sendspin-bt-bridge/screenshots/screenshot-recovery-guidance.png)

## После переподключения звук идёт только на одну колонку

После Bluetooth reconnect PulseAudio может увести активные потоки на sink по умолчанию. Bridge умеет исправлять это автоматически на следующем старте воспроизведения, но если проблема повторяется:

1. Проверьте логи на сообщения про sink routing.
2. Убедитесь, что нужный Bluetooth sink действительно существует.
3. Перезапустите воспроизведение после полного завершения reconnect.

## Music Assistant не видит плеер

Проверьте:

1. В MA включён провайдер Sendspin.
2. `SENDSPIN_SERVER` указывает на правильный хост, либо разрешено `auto` discovery.
3. В логах bridge нет ошибок bind/startup.
4. Используемый sendspin port не занят другим процессом.

Если у устройства не задан явный `listen_port`, помните, что runtime использует **`BASE_LISTEN_PORT + индекс устройства`**. В multi-bridge setups проверьте, что эти диапазоны не пересекаются между контейнерами/экземплярами на одном хосте.

## Путаница с WEB_PORT и HA Ingress

- В standalone-режиме используется прямой браузерный доступ через `WEB_PORT` (по умолчанию **8080**).
- В HA addon mode **Ingress** всегда остаётся на primary channel port (`8080` stable, `8081` rc, `8082` beta).
- Если в addon-режиме задать другой `WEB_PORT`, это добавит **дополнительный прямой listener**, а не перенесёт Ingress.

Если прямой порт не отвечает, проверьте, не занят ли он другим сервисом, и после изменения значения выполните **Save & Restart**.

## Bluetooth не подключается

1. Устройство действительно спарено на уровне хоста.
2. D-Bus доступен bridge.
3. Адаптер включён.
4. Попробуйте **Re-pair** из dashboard.

Если используется несколько адаптеров, отдельно проверьте, что в строке устройства указан правильный adapter ID или MAC.

Если bridge много раз подряд не может переподключить одну и ту же колонку, настроенный **Auto-disable threshold** может сохранить устройство как disabled. После устранения проблемы pairing/signal/adapter включите его снова в **Configuration → Devices**.

## "No sink" или тишина при воспроизведении

**No sink** означает, что Bluetooth подключён, но аудио-sink ещё не привязался.

| Причина | Что попробовать |
|---|---|
| Аудиосервер не работает | Проверить `pactl info` |
| Sink ещё не успел подняться | Подождать несколько секунд после BT connect |
| Неправильное соответствие user/socket | Проверить exposure аудио-сокета |
| Неверный профиль | Убедиться, что есть профиль A2DP sink |

На медленных системах помогает увеличение **PulseAudio latency (ms)** и включение **Prefer SBC codec**.

## Scan ничего не находит

Если **Scan** не возвращает результатов:

1. Переведите колонку в pairing mode до запуска сканирования.
2. Дождитесь завершения полного фонового сканирования.
3. Посмотрите текст ошибки прямо в discovery card.
4. Повторяйте попытку только после окончания cooldown.
5. Используйте **Already paired**, если хост уже знает устройство.

## Не проходит token-flow Music Assistant

Если **Get token automatically** или **Get token** не завершается успешно:

1. Убедитесь, что URL MA указан правильно и доступен.
2. Если bridge уже подключён, сначала нажмите **Reconfigure** в **Configuration → Music Assistant**, чтобы снова открыть auth-секцию.
3. В HA Ingress обновите страницу из Home Assistant, чтобы у браузера был актуальный HA session/token.
4. Помните, что **Get token automatically** доступен только в addon/Ingress flow. Вне него используйте прямой MA login или вставьте token вручную.
5. Разрешите popup-окна для страницы bridge — fallback HA auth flow открывает popup, когда silent auth недостаточно.
6. Если MA работает поверх HA и встроенный MA-login отклоняет credentials, повторите попытку и завершите шаг HA MFA, а не ожидайте чистый MA-password flow.
7. Помните, что bridge сохраняет long-lived MA token, но не сохраняет введённый пароль.

## Empty state ведёт не туда

После редизайна empty-state действия должны работать так:

- **Scan for devices** → **Configuration → Devices → Discovery & import**.
- **Add adapter** → **Configuration → Bluetooth** с пустой строкой адаптера.

Если этого не происходит, проверьте, что веб-интерфейс обновлён до актуального релиза.

## Проблемы аутентификации

### Ошибка на MFA / TOTP шаге

Когда Home Assistant требует MFA, login page переключается на отдельный шаг с кодом. Если flow ломается:

1. Начните со свежей страницы входа, а не со старой закладки на MFA-step.
2. Убедитесь, что этот же пользователь может войти в Home Assistant вне bridge.
3. Проверьте, не слишком ли маленький `Session timeout` и не была ли страница слишком долго простаивающей между вводом пароля и TOTP.

### Сработала блокировка локального входа

По умолчанию **5 неудачных попыток за 1 минуту** дают **5 минут** блокировки. Эти значения меняются в **Configuration → Security**.

### Веб-интерфейс без auth

Если сверху виден жёлтый warning-banner, локальная auth-защита отключена. Используйте ссылку в баннере для быстрого перехода в **Configuration → Security**.

Для standalone-login важны и restart-applied параметры вроде включения auth и session timeout. Если вы меняли эти значения, используйте **Save & Restart** прежде чем делать вывод, что настройка не подхватилась.

## Mute или volume не совпадают с Music Assistant

Проверьте вкладку **Music Assistant**:

- **Route volume through MA** синхронизирует bridge с ползунками MA.
- **Route mute through MA** синхронизирует состояние mute с MA.

Если эти тумблеры выключены, bridge использует direct PulseAudio control для более быстрого локального отклика, но MA может показывать другое состояние.

## Save vs Save & Restart vs Cancel

Если изменение конфигурации ведёт себя непредсказуемо:

- Используйте **Save** для простого сохранения.
- Используйте **Save & Restart**, если runtime-компоненты должны переподключиться или переинициализироваться.
- Используйте **Cancel**, чтобы выбросить несохранённые изменения и восстановить последние сохранённые значения формы.

Прогресс перезапуска отображается в шапке и показывает шаги сохранения, остановки, reconnect и восстановления связи с Music Assistant.

## Diagnostics и bug reports

Раздел **Diagnostics** стоит открыть, если нужно быстро понять:

- видит ли bridge адаптеры,
- правильно ли назначены sinks,
- жив ли Music Assistant,
- что происходит с каждым устройством,
- в каком состоянии subprocess и runtime окружение.

Кнопки **Download diagnostics** и **Submit bug report** помогают собрать актуальные данные перед созданием GitHub issue.

Bug-report dialog теперь заранее подставляет редактируемое suggested description из замаскированной диагностики. Перед отправкой дополните его точными шагами воспроизведения и ожидаемым/фактическим поведением.

## В Home Assistant Supervisor нет интернета или не работают update checks на HAOS в Proxmox

В текущем HAOS-on-Proxmox окружении причина оказалась связана с **MTU/path behavior**, а не с настройкой TLS версии Supervisor. Установка MTU **1400** на сетевом интерфейсе VM восстановила Supervisor internet checks.

Если Supervisor пишет, что интернета нет, хотя в остальном сеть выглядит рабочей, сначала проверьте MTU VM/сети, а не TLS-параметры.

## Нет звука на armv7l

Если Bluetooth подключается, UI показывает playback, но звука нет, обновитесь до релиза с PyAV compatibility patch. Старые сборки PyAV на armv7l не имеют layout-атрибута, который ожидает FLAC decoder.
