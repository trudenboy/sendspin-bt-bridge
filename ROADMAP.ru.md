# Дорожная карта v3

Эта страница - краткое русское резюме v3-roadmap. Полная версия и основной source of truth находятся в [`ROADMAP.md`](ROADMAP.md).

## Что уже считается базой для v3

v3 начинается не с нуля, а поверх уже собранного foundation из `v2.46.x`:

- явная lifecycle/orchestration модель
- typed snapshots для status и diagnostics
- onboarding, recovery guidance и bugreport tooling
- room metadata, transfer readiness и fast handoff
- усиленные Docker/RPi startup diagnostics

Поэтому v3 должен быть не rewrite, а следующей продуктовой волной над уже стабилизированным Bluetooth-first runtime.

## Главная идея v3

Сделать Sendspin BT Bridge **Bluetooth-first, room-aware и fleet-manageable runtime для Music Assistant**.

Четыре главные темы v3:

1. **AI-assisted diagnostics и planning развёртывания**
2. **Automatic delay tuning и sync intelligence**
3. **Централизованное управление несколькими bridge**
4. **Backend abstraction для выборочного non-Bluetooth расширения**

## Фазы v3

### V3-0. Закрыть последние pre-v3 operator polish задачи

Нужно убрать оставшиеся UX-проблемы:

- calmer guidance для non-empty installs
- preview/confirm для grouped recovery actions
- меньше шума в compact/mobile recovery
- один top-level owner для blocked-state explanations

### V3-1. AI-assisted diagnostics и deployment planning

AI должен быть operator copilot, а не скрытым control plane.

Цели:

- собрать canonical machine-readable diagnostics bundle
- сделать planner развёртывания для HA add-on / Docker / RPi / LXC
- давать plain-language diagnostics summary и safe next actions
- добавить redaction, opt-in и provider/local boundaries

### V3-2. Automatic delay tuning

Цели:

- убрать большую часть ручного подбора `static_delay_ms`
- добавить telemetry для drift/sync health
- сделать guided calibration
- разрешить bounded auto-tuning только там, где confidence достаточно высок

### V3-3. Fleet control plane для нескольких bridge

Цели:

- stable identity для bridge-инстансов
- aggregate health/inventory/room coverage
- duplicate/conflict detection между bridge
- bulk diagnostics, compare/export/import config, fleet event timeline

### V3-4. Backend abstraction и config schema v2

Цели:

- ввести `AudioBackend` contract
- обернуть текущий Bluetooth runtime в этот contract
- перейти к player/backend config schema
- доказать abstraction через первый adjacent backend (`LocalSinkBackend`, возможно `ALSADirectBackend`)

### V3-5. Избирательное расширение после стабилизации

Только после стабильных предыдущих фаз:

- USB audio auto-discovery
- richer sync/drift telemetry
- Snapcast/VBAN tracks
- более широкая federation / plugin / HA component strategy

## Ограничения

Не нужно:

- делать большой rewrite вместо инкрементальной миграции
- делать AI обязательным или cloud-only
- включать opaque auto-remediation без operator approval
- начинать спекулятивные backend'ы до abstraction layer

Нужно:

- держать Bluetooth reliability главным приоритетом
- делать AI optional и secret-safe
- делать auto delay tuning bounded и explainable
- держать fleet management additive, а не обязательным для одиночного bridge

## Реалистичный первый milestone для v3

`v3.0.0-rc.1` должен реалистично включать:

- finish V3-0 polish
- diagnostics bundle foundations
- первый deployment planner draft
- delay telemetry + manual calibration path
- первые fleet identity/inventory surfaces

Полная англоязычная версия остаётся в [`ROADMAP.md`](ROADMAP.md).
