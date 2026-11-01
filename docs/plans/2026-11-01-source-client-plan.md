# План: 3.2.0-beta.2 — телефон как Sendspin-источник Music Assistant

Дата: 2026-11-01 · Ветка: `v3` · Основание:
[`docs/research/2026-11-01-phone-as-source-on-haos.md`](../research/2026-11-01-phone-as-source-on-haos.md) и
[`docs/research/2026-11-01-bluealsa2sendspin-review.md`](../research/2026-11-01-bluealsa2sendspin-review.md).

## Решение

Мост перестаёт быть Sendspin-сервером для телефона и становится **source-клиентом**
сервера Music Assistant: телефон приходит по A2DP в хост, мост захватывает его и отдаёт
в MA как Sendspin-источник. MA владеет часами, джиттер-буфером и маршрутизацией; плееры
моста играют источник как обычный поток MA.

Причины: на HAOS эндпоинты A2DP уже принадлежат host-PulseAudio; серверная инверсия
заставляла мост раздавать собственный поток и держать relay. Подробности и альтернативы —
в research-заметках и ADR-0003.

## Этапы

| Этап | Содержание |
|---|---|
| S1 | `services/source/capture/` (PipeWire, PulseAudio, synthetic), capability по аудио-серверу, kill-on-timeout, атомарная личность |
| S2 | `SourceClient` (aiosendspin `Roles.SOURCE`), pairing с MA, `line_sense`, state/manager, API v2, UI v2, удаление сервера/relay/`/players`/`SENDSPIN_SOURCE_PORT` |
| S3 | демо с in-process `SendspinServer` и синтетическим PCM, ADR-0003, docs en/ru, скриншоты |
| S4 | событийное обнаружение PA, детерминированный выбор при двух телефонах, эксперимент с таймстампом, протокольный интеграционный тест |
| S5 | локальная приёмка, `VERSION` 3.2.0-beta.2, HAOS-приёмка, fix-forward |

## Контракт API

- `POST /api/v1/bridge/source` `{enabled}` — включает/выключает источник и пишет `SENDSPIN_SOURCE`.
- `POST /api/v1/bridge/source/ma-pairing` — окно спаривания с MA, ответ `{code, seconds_left, client_id}`.
- `DELETE /api/v1/bridge/source/ma-pairing` — забыть PSK и закрыть окно.
- `POST|DELETE /api/v1/bridge/source/pairing` — окно спаривания BT-телефона (как было).
- `/bridge/source/players` удалён.

Коды ошибок: `ma_unreachable`, `ma_pairing_required`, `ma_pairing_rejected`,
`ma_source_unsupported`, `capture_unavailable`, `no_adapter`, `no_agent`, `ma_already_paired`.

## Конфиг

- Остаются `SENDSPIN_SOURCE` (bool, пишется UI) и `SENDSPIN_SERVER`/`SENDSPIN_PORT` (общие с плеерами).
- Удаляются `SENDSPIN_SOURCE_PORT`, `_HOST`, `_NAME`, `_RATE`, `_CHANNELS`; `SENDSPIN_SOURCE_NODE` — только env-оверрайд.
- Старые `source_identity.key` / `source_pairing.json` не трогаем; новые — `sendspin_source_identity.json` и `sendspin_source_pairing.json`.

## Проверки

- `.venv/bin/python -m pytest tests/unit -q`; новый `tests/integration/test_source_protocol.py` против реального `SendspinServer`.
- `ruff check`, `ruff format --check`, `mypy`, `scripts/export_openapi.py --check`.
- UI: `NODE_ENV=test npm test -- --run`, `npm run build`.
- Docs: `cd docs-site && npm ci && npm run build`; `scripts/lint_changelog.py`.
- Релиз: `scripts/generate_ha_addon_variants.py sync-current-repo --output-dir . --beta-version 3.2.0-beta.2`, затем `check-current-repo`.

## Приёмка

1. Локально: код спаривания → MA → телефон играет → `streaming`; stop/start в MA; рестарт моста без повторного кода; `DELETE` сбрасывает спаривание.
2. HAOS: ENEBY Portable и Pixel 8 спарены с HAOS; телефон играет через MA на ENEBY Portable; `capture.kind=pulse_source`; рестарт аддона/MA восстанавливается; `:8940` не слушается.

Фактические отклонения и результаты замеров записываются здесь по завершении этапов.

## Отклонения по ходу

- **S1 и S2 сблизились**: захват и source-клиент делались подряд, поэтому серверный
  источник и relay удалены сразу в S2, без промежуточного «сервер на новом захвате».
- **S1: `probe_capture(server_name)` обязателен.** Проба без аргумента вызывала
  `probe_audio_server()` изнутри уже запущенного event loop ридера инвентаря и роняла
  весь аудио-раздел; сервер теперь передаётся снаружи.
- **S1: `CaptureEndpoint.running` — это присутствие телефона**, а не «кто-то пишет»:
  на PulseAudio источник может быть SUSPENDED, пока никто не записывает, но телефон
  уже подключён; `line_sense` сообщает присутствие, как и PipeWire-бэкенд.
- **Демо**: вместо встроенного `SendspinServer` с автоспариванием демо публикует
  состояние источника (`streaming`, MA подключён) через фейковый менеджер. Реальный
  протокол покрыт `tests/integration/test_source_protocol.py`; встроенный сервер в
  демо остаётся кандидатом на следующий цикл (fallback B/C из плана).
