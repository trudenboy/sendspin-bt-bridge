# Дорожная карта v3

Эта страница — краткое русское резюме v3-roadmap. Полная версия и основной source of truth находятся в [`ROADMAP.md`](ROADMAP.md).

## Что уже считается базой для v3

v3 начинается не с нуля, а поверх уже собранного foundation из `v2.46.x+`:

- явная lifecycle и orchestration-модель
- typed snapshots для status и diagnostics
- onboarding, recovery guidance и bugreport tooling
- room metadata, readiness и handoff foundations
- config migration и validation flows
- versioned subprocess IPC и стабильные diagnostics endpoints
- усиленные Docker и Raspberry Pi startup diagnostics

Поэтому v3 должен быть не rewrite, а следующей продуктовой волной над уже стабилизированным Bluetooth-first runtime.

## Обновлённый приоритет v3

Первое крупное расширение v3 — это теперь **wired и USB audio**, а не поздний optional track.

Логика порядка теперь такая:

1. закончить последние operator UX polish-задачи
2. ввести backend abstraction и config schema v2
3. выпустить первый adjacent backend для USB DAC и wired outputs
4. добавить audio health visibility и delay intelligence
5. только потом наращивать AI-assisted support и multi-bridge fleet control plane

Bluetooth при этом остаётся главным и самым battle-tested runtime.

## Главная идея v3

Сделать Sendspin BT Bridge **Bluetooth-first, room-aware и multi-backend-capable runtime для Music Assistant**.

Пять главных тем v3:

1. **Backend abstraction и config schema v2**
2. **USB DAC и wired audio как первый adjacent backend**
3. **Audio health visibility, sync telemetry и delay tuning**
4. **AI-assisted diagnostics и planning развёртывания**
5. **Централизованное управление несколькими bridge**

## Фазы v3

### V3-0. Закрыть последние pre-v3 operator polish задачи

Нужно убрать оставшиеся UX-проблемы:

- calmer guidance для non-empty installs
- preview и confirm для grouped recovery actions
- меньше шума в compact/mobile recovery
- один top-level owner для blocked-state explanations

### V3-1. Backend abstraction и config schema v2

Цели:

- ввести `AudioBackend`-style contract
- обернуть текущий Bluetooth runtime в этот contract первым
- перейти к player/backend config schema
- расширить IPC, status snapshots и diagnostics contracts так, чтобы новые backend fields добавлялись инкрементально

### V3-2. USB DAC и wired audio backend

Это первый реальный adjacent backend и ближайший продуктовый приоритет.

Цели:

- находить USB DAC, built-in audio и другие wired outputs через PulseAudio/PipeWire/ALSA
- создать direct-sink player type без Bluetooth pairing lifecycle
- добавить aliasing и room mapping для raw audio devices
- затем добавить hotplug/discovery follow-up для USB devices

### V3-2.5. Custom PulseAudio sinks

После V3-2 становится намного ценнее дать UI для:

- `module-combine-sink` для party mode и open floor plans
- `module-remap-sink` для split-zone сценариев и multichannel USB DAC
- сохранения и восстановления custom sinks после рестарта

Это parallel-friendly track после wired/USB foundations, а не блокер первой поставки.

### V3-3. Audio health dashboard и signal path visibility

Цели:

- показать codec, sample rate, sink route, uptime и sync health в UI
- визуализировать signal path для Bluetooth и wired backend'ов
- вынести degraded sync и route problems в явные operator surfaces

### V3-4. Automatic delay tuning и sync intelligence

Цели:

- убрать большую часть ручного подбора `static_delay_ms`
- добавить drift/sync telemetry и confidence signals
- сделать guided calibration
- разрешить bounded auto-tuning только там, где confidence достаточно высок

### V3-5. AI-assisted diagnostics и deployment planning

AI должен быть operator copilot, а не скрытым control plane.

Цели:

- собрать canonical machine-readable diagnostics bundle
- сделать planner развёртывания для HA add-on / Docker / RPi / LXC
- давать plain-language diagnostics summary и safe next actions
- добавить redaction, opt-in и provider/local boundaries

### V3-6. Fleet control plane для нескольких bridge

Цели:

- stable identity для bridge-инстансов
- aggregate health, inventory, room coverage и backend mix
- duplicate/conflict detection между bridge
- bulk diagnostics, compare/export/import config и fleet event timeline

### V3-7. Избирательное расширение после стабилизации

Только после стабильных предыдущих фаз:

- system-wide audio runtime / non-user-scoped socket support для Raspberry Pi и других embedded-host сценариев
- richer sync/drift telemetry across groups and bridges
- Snapcast/VBAN/backend strategy tracks
- более широкая federation / plugin / HA component strategy
- per-room DSP / EQ surfaces

## Ограничения

Не нужно:

- делать большой rewrite вместо инкрементальной миграции
- делать wired/USB expansion через слом текущего Bluetooth runtime
- делать AI обязательным или cloud-only
- включать opaque auto-remediation без operator approval
- начинать спекулятивные backend'ы до abstraction layer

Нужно:

- держать Bluetooth reliability главным приоритетом
- делать wired/USB additive, а не заменяющим базовый runtime
- делать AI optional и secret-safe
- делать auto delay tuning bounded и explainable
- держать fleet management additive, а не обязательным для одиночного bridge
- выпускать migrations, docs и tests вместе с каждой фазой

## Реалистичный первый milestone для v3

`v3.0.0-rc.1` должен реалистично включать:

- finish V3-0 polish
- backend abstraction и config schema v2
- первый wired/USB backend с operator-driven creation flow
- базовую audio health visibility и signal path publication
- delay telemetry + manual calibration path
- diagnostics bundle foundations для будущего planner/AI слоя

То есть первый RC должен чувствоваться как: **"Bluetooth-first bridge с реальным wired/USB expansion path и заметно лучшей observability"**.

Полная англоязычная версия остаётся в [`ROADMAP.md`](ROADMAP.md).
