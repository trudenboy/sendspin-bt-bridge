# Дорожная карта (краткая версия)

Эта страница — краткое русское резюме актуального roadmap. Полная версия и основной source of truth находятся в [`ROADMAP.md`](ROADMAP.md).

## Зачем обновлён roadmap

Проект уже прошёл часть большого внутреннего рефакторинга. В кодовой базе **уже есть**:

- `BridgeOrchestrator`
- startup progress и runtime metadata
- snapshot/read-side модели для status и diagnostics
- protocol-versioned IPC helpers
- onboarding assistant и базовая config validation

Поэтому roadmap больше не должен описывать эти вещи как «будущие». Его задача теперь — довести начатую архитектуру до завершённого состояния и только потом идти в v3-расширение.

## Куда проект движется

### Фаза 1. Завершить foundation текущего v2 runtime

Главная цель — сделать уже добавленные архитектурные слои каноническими:

- довести migration routes на snapshots
- превратить `device_registry` в полноценный ownership service
- окончательно вынести lifecycle semantics в `BridgeOrchestrator`
- уменьшить центральную роль `state.py`

### Фаза 2. Контракты, диагностика и lifecycle конфигурации

Цель — сделать bridge безопаснее для развития и понятнее в эксплуатации:

- формализовать IPC envelopes вокруг `protocol_version`
- унифицировать event history и health explanations
- довести config lifecycle до migration-ready состояния
- добавить resource telemetry и hook/webhook surfaces

### Фаза 3. Onboarding, recovery UX и capability model

Цель — уменьшить количество guesswork у пользователей и операторов:

- превратить текущий onboarding assistant в более guided setup flow
- сделать recovery actions понятнее
- добавить явную capability model для устройств и bridge-функций
- улучшить latency guidance и structured diagnostics exports

### Фаза 4. Backend abstraction для v3

Эта фаза начинается только после стабилизации Bluetooth core:

- ввести `AudioBackend` abstraction
- завернуть текущий runtime в `BluetoothA2DPBackend`
- подготовить config schema v2
- добавить первые соседние backend'ы: `LocalSinkBackend` и `ALSADirectBackend`

### Фаза 5. Выборочное расширение

Только после выполнения предыдущих фаз:

- USB audio auto-discovery
- virtual sink / test-oriented backend
- richer sync telemetry
- при наличии спроса — Snapcast, VBAN, federation, HACS, plugin surfaces

## Рекомендуемый порядок работ

1. Закончить snapshot/read-side migration.
2. Формализовать registry ownership.
3. Довести lifecycle boundaries orchestrator'а.
4. Завершить IPC contracts и event/diagnostics model.
5. Сделать config lifecycle migration-ready.
6. Усилить onboarding, capability model и recovery UX.
7. Только потом переходить к backend abstraction и v3.

## Важные ограничения

Не стоит:

- начинать новый «большой рефакторинг» с нуля
- превращать mock/demo в отдельную главную фазу развития
- идти в generic audio platform раньше стабилизации Bluetooth runtime
- тащить backend abstraction как оправдание полного rewrite

Стоит:

- мигрировать инкрементально
- сохранять compatibility layers во время переходов
- проверять поведение на реальном runtime после structural changes
- держать Bluetooth reliability главным приоритетом
