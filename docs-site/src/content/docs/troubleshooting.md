---
title: Устранение неполадок
description: Решение типичных проблем Sendspin Bluetooth Bridge
---

import { Aside } from '@astrojs/starlight/components';

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
