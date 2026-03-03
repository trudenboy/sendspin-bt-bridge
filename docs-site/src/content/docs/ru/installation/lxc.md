---
title: Установка — Proxmox LXC
description: Установка Sendspin Bluetooth Bridge в Proxmox LXC контейнер
---

import { Steps, Aside } from '@astrojs/starlight/components';

## Преимущества LXC перед Docker

В отличие от Docker, LXC-контейнер имеет **собственный bluetoothd и PulseAudio**, что даёт более стабильную работу с Bluetooth: паринг сохраняется между перезапусками, нет конфликтов с хостовым bluetoothd.

## Требования

- Proxmox VE 7.x или 8.x
- USB Bluetooth-адаптер (рекомендуется один адаптер — одна колонка)

## Установка

<Steps>

1. **Запустите скрипт установки**

   На хосте Proxmox выполните:

   ```bash
   bash -c "$(wget -qO - https://raw.githubusercontent.com/trudenboy/sendspin-bt-bridge/main/lxc/install.sh)"
   ```

   Скрипт создаст LXC контейнер, установит все зависимости, настроит PulseAudio и запустит сервис.

2. **Войдите в консоль контейнера**

   ```bash
   pct enter <ID>
   ```

3. **Откройте веб-интерфейс**

   ```
   http://<IP-контейнера>:8080
   ```

4. **Добавьте Bluetooth-устройство**

   В веб-интерфейсе перейдите в **Конфигурация → Bluetooth Devices**, нажмите **Scan** для поиска устройств или **+ Add Device** для ручного ввода MAC-адреса.

</Steps>

## Паринг колонки

Если колонка ещё не спарена:

1. Переведите колонку в режим паринга
2. В веб-интерфейсе нажмите **🔍 Scan** и подождите ~10 секунд
3. Нажмите **Re-pair** рядом с найденным устройством

Или через `bluetoothctl` внутри контейнера:

```bash
bluetoothctl
# power on
# scan on
# pair AA:BB:CC:DD:EE:FF
# trust AA:BB:CC:DD:EE:FF
# connect AA:BB:CC:DD:EE:FF
```

## Управление сервисом

```bash
systemctl status sendspin-client
systemctl restart sendspin-client
journalctl -u sendspin-client -f
```

## Обновление

```bash
cd /opt/sendspin-bt-bridge
git pull
systemctl restart sendspin-client
```

<Aside type="tip">
  Для нескольких колонок рекомендуется создать отдельный LXC контейнер под каждый USB Bluetooth адаптер. Это изолирует bluetoothd и PulseAudio, устраняя конфликты кодеков.
</Aside>
