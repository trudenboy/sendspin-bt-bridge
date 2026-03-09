---
title: Устранение неполадок
description: Решение типичных проблем Sendspin Bluetooth Bridge
---


## Аудио играет только через одну колонку

При отключении и повторном подключении Bluetooth-колонки PulseAudio (`module-rescue-streams`) автоматически переводит все активные аудиопотоки на синк по умолчанию. При переподключении потоки сами не возвращаются.

**Самовосстановление**: мост исправляет это автоматически. При следующем старте воспроизведения после переподключения он обнаруживает неверно маршрутизированные потоки и перемещает их обратно. В логах можно увидеть сообщение `Corrected 1 sink-input(s) to bluez_sink.XX_XX...`.

Если проблема повторяется:
1. Проверьте логи: `docker logs sendspin-client | grep -i "sink\|routing\|corrected"`
2. Убедитесь, что BT-синк корректно определяется: `docker exec sendspin-client pactl list sinks short`
3. Попробуйте перезапустить контейнер после переподключения колонки

## Music Assistant не видит плеер

**Проверьте:**

1. Провайдер Sendspin включён в MA: Settings → Providers
2. `SENDSPIN_SERVER` указан верно (или `auto` работает — проверьте mDNS)
3. Плеер запускается без ошибок: `docker logs sendspin-client | grep ERROR`
4. Порт не занят другим процессом: `ss -tlnp | grep 892`

**При `auto`:** mDNS требует `network_mode: host`. Убедитесь, что он задан.

## Bluetooth не подключается

**Проверьте:**

1. Устройство спарено: `bluetoothctl devices` должен показывать ваш MAC
2. D-Bus доступен: `/var/run/dbus` смонтирован в контейнер
3. Адаптер включён: `bluetoothctl show` → `Powered: yes`

**Переспарьте устройство** через кнопку **🔗 Re-pair** в веб-интерфейсе.

```bash
# Диагностика внутри контейнера
docker exec -it sendspin-client bluetoothctl show
docker exec -it sendspin-client bluetoothctl devices
```

## Нет звука (No Sink)

Статус **No Sink** означает, что BT подключён, но PulseAudio/PipeWire синк не найден.

**Причины и решения:**

| Причина | Решение |
|---|---|
| PulseAudio не запущен | `docker exec sendspin-client pactl info` |
| Синк ещё не инициализирован | Подождите 5–10 сек после подключения BT |
| Неверный UID аудио-сокета | Установите `AUDIO_UID` равным UID пользователя хоста (`id -u`) |
| A2DP профиль не загружен | `pactl list cards` — проверьте профиль `a2dp-sink` |

## Звук прерывается (заикается)

**Решения:**

1. Увеличьте `PULSE_LATENCY_MSEC` (попробуйте 400–600)
2. Включите `PREFER_SBC_CODEC: true` — SBC требует меньше CPU
3. В MA установите Audio Quality → PCM 44.1kHz/16-bit (устраняет FLAC декодирование)
4. Проверьте нагрузку CPU: `docker stats sendspin-client`

## Веб-интерфейс не открывается через HA

**Проверьте:**

- Версия аддона ≥ 1.4.1 (исправлен HA Ingress)
- Аддон запущен: вкладка **Info** → статус **Running**
- Консоль браузера: нет ли ошибок загрузки CSS/JS

## Конфигурация не сохраняется

Docker: убедитесь, что volume `/etc/docker/Sendspin:/config` смонтирован и директория доступна на запись:

```bash
ls -la /etc/docker/Sendspin/
```

## Проблемы в LXC (Proxmox)

**bluetoothctl не находит адаптеры:**
- Убедитесь, что USB Bluetooth-адаптер проброшен в контейнер (`lxc.cgroup2.devices.allow`)
- Перезапустите bluetoothd: `systemctl restart bluetooth`

**PulseAudio не видит BT-синк:**
- Проверьте, что `pulseaudio-module-bluetooth` установлен
- Перезапустите PulseAudio: `pulseaudio -k && pulseaudio --start`

**Адаптер не отвечает по имени `hciN`:**
- Используйте MAC-адрес адаптера в поле `adapter` вместо `hci0` — в LXC имена интерфейсов нестабильны

## BT-сканирование не возвращает результат

Если интерфейс сканирования показывает job_id и продолжает опрашивать без результата, или отображает ошибку:

1. Сканирование длится ~10 сек в фоне — дождитесь завершения перед повторной попыткой
2. Если в диалоге сканирования показан текст ошибки — в нём содержится причина (например, `bluetoothctl timed out`)
3. Проверьте доступность bluetoothctl: `docker exec -it sendspin-client bluetoothctl list`
4. Попробуйте перезапустить контейнер — зависшая D-Bus сессия может блокировать сканирование

## Кнопка паузы устройства не работает

Кнопка паузы конкретного устройства сопоставляет плеер по `player_name` через D-Bus. Если нажатие не имеет эффекта:

1. Убедитесь, что `player_name` в `config.json` точно совпадает с именем, отображаемым в веб-интерфейсе (с учётом регистра)
2. Проверьте, что процесс sendspin запущен: `docker exec sendspin-client ps aux | grep sendspin`
3. Проверьте логи на ошибки D-Bus: `docker logs sendspin-client | grep -i "dbus\|pause"`

## Сбор логов для отчёта об ошибке

```bash
# Docker
docker logs sendspin-client > bridge.log 2>&1

# HA Addon
# Settings → Add-ons → Sendspin Bluetooth Bridge → Logs

# LXC / systemd
journalctl -u sendspin-client --no-pager > bridge.log
```

Приложите лог к [отчёту об ошибке](https://github.com/trudenboy/sendspin-bt-bridge/issues) вместе с:
- Методом деплоя (Docker/HA/LXC)
- Аудиосистемой (PipeWire/PulseAudio)
- Версией из `/api/version`

## Нет звука на armv7l (ARM 32-бит)

**Симптом:** Bluetooth подключён, веб-интерфейс показывает «playing», но полная тишина. В логах ошибки `Audio worker is not running`.

**Причина:** PyAV 12.3.0 (единственная версия, компилирующаяся на armv7l) не имеет атрибута `AudioLayout.nb_channels`, который использует FLAC-декодер sendspin. Поток audio worker падает на первом FLAC-фрейме.

**Решение:** Обновитесь до v2.16.0+ — monkey-patch в `services/daemon_process.py` автоматически адаптирует FLAC-декодер для PyAV <13. Запустите скрипт обновления:

```bash
# Внутри LXC-контейнера
bash <(wget -qO- https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/upgrade.sh)
systemctl restart sendspin-client
```

**Проверка:** В логах не должно быть строк `Audio worker is not running` или `daemon stderr`:

```bash
journalctl -u sendspin-client --since "30 sec ago" | grep -E "Audio worker|daemon stderr"
```
