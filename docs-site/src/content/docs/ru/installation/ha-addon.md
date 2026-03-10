---
title: Установка — Home Assistant Addon
description: Пошаговая установка Sendspin Bluetooth Bridge как аддона Home Assistant
---


## Требования

- Home Assistant OS или Supervised
- Bluetooth-адаптер, доступный хосту HA
- Music Assistant Server на вашей сети

## Поддерживаемые платформы

| Архитектура | Устройства HA | Статус |
|---|---|---|
| **amd64** (x86_64) | Intel NUC, мини-ПК, Proxmox/VMware VM | ✅ Протестировано |
| **aarch64** (ARM64) | HA Green, HA Yellow, Raspberry Pi 4/5, ODROID N2+ | ✅ Тестируется сообществом |
| **armv7** (ARM 32-бит) | Raspberry Pi 3, ODROID XU4, Tinker Board | ⚠️ Ограниченная поддержка |

## Установка

<Steps>

1. **Добавьте репозиторий аддонов**

   Нажмите кнопку для автоматического добавления репозитория:

   [![Добавить репозиторий в HA](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftrudenboy%2Fsendspin-bt-bridge)

   Или вручную: **Настройки → Аддоны → Магазин аддонов → ⋮ (меню) → Репозитории** и добавьте:
   ```
   https://github.com/trudenboy/sendspin-bt-bridge
   ```

2. **Установите аддон**

   Найдите **Sendspin Bluetooth Bridge** в магазине аддонов и нажмите **Установить**.

3. **Настройте аддон**

   Перейдите на вкладку **Конфигурация** и добавьте ваши устройства:

   ```yaml
   sendspin_server: auto          # или IP/hostname вашего MA сервера
   sendspin_port: 9000
   bluetooth_devices:
     - mac: "AA:BB:CC:DD:EE:FF"
       player_name: "Колонка в гостиной"
     - mac: "11:22:33:44:55:66"
       player_name: "Колонка на кухне"
       adapter: hci1              # только для конфигураций с несколькими адаптерами
       static_delay_ms: -500      # компенсация задержки A2DP в мс
   ```

4. **Запустите аддон**

   Нажмите **Запустить**. Аддон появится в боковой панели HA.

5. **Подключение к Music Assistant**

   Откройте веб-интерфейс → Configuration → Advanced settings → нажмите **🏠 Sign in with Home Assistant**. Бридж автоматически создаст токен MA API — включая метаданные now-playing, транспортные кнопки и групповую синхронизацию громкости.

</Steps>

## Открытие веб-интерфейса

Аддон предоставляет веб-интерфейс через **HA Ingress** — нажмите **Открыть веб-интерфейс** в странице аддона или перейдите по ссылке в боковой панели. Порт 8080 не нужно пробрасывать.

Интерфейс автоматически применяет тему HA (тёмная/светлая) через Ingress `postMessage` API.

## Аудио-маршрутизация (HA OS)

Аддон запрашивает `audio: true` в манифесте, поэтому HA Supervisor автоматически инжектирует переменную `PULSE_SERVER`. Ручная настройка сокетов не требуется.

## Применение изменений конфигурации

Изменения в конфигурации аддона вступают в силу после перезапуска. Используйте кнопку **Перезапустить** в странице аддона или нажмите **Сохранить и перезапустить** в веб-интерфейсе.

<Aside type="tip">
  Если Music Assistant не видит плеер после запуска — проверьте, что в настройках MA включён провайдер **Sendspin**. Перейдите в Settings → Providers и убедитесь, что Sendspin активен.
</Aside>
