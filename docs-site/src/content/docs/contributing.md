---
title: Разработка и участие
description: Руководство разработчика для Sendspin Bluetooth Bridge
---

## Запуск локально

Требуется Docker и Docker Compose. Bluetooth-колонка должна быть **спарена с хостом** до запуска.

```bash
git clone https://github.com/trudenboy/sendspin-bt-bridge.git
cd sendspin-bt-bridge

# Собрать и запустить
docker compose up --build

# Просмотр логов
docker logs -f sendspin-client

# Веб UI
open http://localhost:8080
```

Запуск без Docker (требуются системные пакеты Bluetooth и аудио):

```bash
pip install -r requirements.txt
python sendspin_client.py
```

## Структура проекта

```
sendspin_client.py    # Точка входа: SendspinClient + main()
bluetooth_manager.py  # BluetoothManager — BT подключения через bluetoothctl
config.py             # Конфигурация, shared lock, load_config()
mpris.py              # MPRIS D-Bus интеграция, MprisIdentityService
web_interface.py      # Flask API + Waitress сервер
templates/index.html  # HTML шаблон
static/style.css      # Стили
static/app.js         # JavaScript
entrypoint.sh         # Docker entrypoint: D-Bus, аудио инициализация
ha-addon/             # Home Assistant addon конфигурация
lxc/                  # Proxmox LXC установочный скрипт
```

## Чеклист ручного тестирования

Автоматических тестов нет. Используйте этот чеклист при внесении изменений:

- [ ] Контейнер запускается без ошибок (`docker logs -f sendspin-client`)
- [ ] Веб UI загружается на `http://localhost:8080`
- [ ] Bluetooth-устройство подключается и отображается в веб UI
- [ ] Music Assistant определяет плеер
- [ ] Аудио воспроизводится через Bluetooth-колонку
- [ ] Слайдер громкости в веб UI изменяет громкость колонки
- [ ] Авто-переподключение срабатывает после отключения колонки (~10 с)
- [ ] Изменения конфигурации через веб UI сохраняются после перезапуска контейнера
- [ ] `/api/status` возвращает корректный JSON
- [ ] `/api/config` GET возвращает текущую конфигурацию; POST с корректными данными сохраняет её

При изменениях HA аддона — дополнительно протестируйте через локальный репозиторий аддонов HA.

## Стратегия веток

- `main` — стабильная, всегда готова к релизу
- Ветки фич — ответвляйтесь от `main`, называйте `feat/<описание>` или `fix/<описание>`
- PR — направляйте в `main`

## Сообщение об ошибке

Откройте [issue на GitHub](https://github.com/trudenboy/sendspin-bt-bridge/issues). Укажите:

- Метод деплоя (Docker / HA Addon / Proxmox LXC)
- Релевантный вывод лога
- ОС хоста, аудиосистему (PipeWire или PulseAudio), модель Bluetooth-адаптера
- Шаги воспроизведения

## CI/CD

Пуш тега `v*` в `main` автоматически:

1. Собирает multi-platform Docker образ (`linux/amd64`, `linux/arm64`)
2. Публикует в `ghcr.io/trudenboy/sendspin-bt-bridge`
3. Синхронизирует версию в `ha-addon/config.yaml`

## Attribution

Проект вырос из [loryanstrant/Sendspin-client](https://github.com/loryanstrant/Sendspin-client). Благодарность команде [Music Assistant](https://www.music-assistant.io/) за протокол Sendspin и CLI.
