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

## Новый принцип v3

v3 стоит трактовать как **compatibility-preserving platform refresh**:

- это **не** rewrite с нуля
- но и **не** обещание вечно ограничиваться только маленькими incremental-изменениями
- это шанс заменить те runtime-seams и UI-surfaces, которые мешают росту продукта, сохранив Bluetooth reliability и operator trust

## Обновлённый приоритет v3

Wired и USB остаются первым крупным расширением v3, но теперь их нужно выпускать не как isolated backend-track, а как часть более широкой продуктовой волны.

Новая логика порядка:

1. считать `V3-0` уже собранной baseline-частью
2. запускать v3 как координированную программу из трёх треков: architecture, backend, frontend
3. выпустить первую multi-backend волну как **platform contracts + wired/USB runtime + modern operator console**
4. поднять observability, signal path и delay intelligence раньше AI и fleet
5. расширяться в AI-assisted support и control plane только после зрелого single-bridge multi-backend story

Bluetooth при этом остаётся главным и самым battle-tested runtime.

## Три координированных трека v3

### 1. Architecture / platform

Этот трек задаёт долгоживущий каркас v3:

- явный `AudioBackend`-style contract
- capability model в snapshots и API
- internal event model + event history как общая основа для diagnostics, hooks и fleet
- более жёсткое разделение `user config`, `runtime state` и `derived metadata`
- сжатие `state.py` до compatibility/cache layer
- simulator/mock runtime как штатный способ разработки и тестирования

### 2. Backend / runtime

Этот трек делает v3 по-настоящему multi-backend:

- Bluetooth остаётся primary runtime и эталоном надёжности
- wired/USB становятся first-class player type
- virtual sinks и composed zones идут как ранний follow-up, а не экзотика на потом
- observability, route ownership, signal path и delay intelligence работают поперёк backend'ов

### 3. Frontend / operator console

Этот трек превращает текущий UI в более современный операционный продукт:

- `Vue 3 + TypeScript + Vite` для новых и заменяемых high-churn surfaces
- typed frontend stores/models вокруг status, diagnostics, jobs и event history
- headless accessible primitives + shared design tokens
- modern operator console для device creation, diagnostics, details drawers, timelines и bulk actions
- разрешение заменять целые high-churn экраны, если это чище, чем бесконечно латать монолитный `static/app.js`

## Главная идея v3

Сделать Sendspin BT Bridge **Bluetooth-first, room-aware, multi-backend audio platform с modern operator console**.

Итоговые целевые свойства v3:

1. shared platform contracts вместо Bluetooth-only неявной модели
2. wired/USB и затем virtual sinks как реальное расширение продукта
3. observability-first runtime с signal path, health summaries и event history
4. современный operator console для create/diagnose/tune/recover flows
5. AI diagnostics и fleet control поверх тех же typed models, а не поверх отдельных ad hoc слоёв

## Фазы v3

### V3-0. Baseline operator polish перед v3

Статус: по сути уже закрыто в текущем коде.

Что считается уже достигнутым baseline:

- calmer guidance для non-empty installs
- preview и confirm для grouped recovery actions
- меньше шума в compact/mobile recovery
- один top-level owner для blocked-state explanations

Практический вывод: активная продуктовая работа начинается уже с **V3-1**.

### V3-1. Platform reset for v3

Главная цель:

- собрать shared platform model для v3 и заложить первые поверхности нового operator console

Ключевой scope:

- `AudioBackend`-style contract, capability model и backend-neutral runtime seams
- config/runtime model v2 с разделением config, runtime state и derived metadata
- internal event model, event history и typed read-models как общий фундамент
- simulator/mock runtime и hardware-light validation как норма
- frontend foundation: `Vue 3 + TS + Vite`, typed stores, reusable primitives, замена high-churn surfaces там, где это даёт более чистую основу

### V3-2. Modern operator console и wired/USB runtime

Главная цель:

- выпустить первую по-настоящему multi-backend продуктовую волну

Ключевой scope:

- USB DAC, built-in audio, HDMI и другие wired outputs как first-class player types
- backend-aware device creation/edit flow вместо старой несвязной device-management логики
- typed forms, aliasing, room mapping и capability-driven UX
- hotplug/discovery и явные route-lifecycle surfaces в UI

### V3-2.5. Virtual sinks и composed zones

Главная цель:

- превратить `module-combine-sink` и `module-remap-sink` в реальные продуктовые сценарии

Ключевой scope:

- combine sinks для party mode/open floor plans
- remap sinks для split-zone и multichannel USB DAC сценариев
- persistence, recreation, verification и участие virtual sinks в player-management flow

### V3-3. Observability-first runtime и operations center

Главная цель:

- сделать health, signal path и recovery state видимыми оператору без чтения логов

Ключевой scope:

- live telemetry, degraded-mode summaries и per-device event history
- signal path и route ownership surfaces для Bluetooth, wired и virtual backend'ов
- unified diagnostics/recovery center, timeline/event-list views, bulk actions, split-pane/drawer patterns и более зрелая component system

### V3-4. Delay intelligence и guided tuning

Главная цель:

- убрать большую часть ручного подбора `static_delay_ms`

Ключевой scope:

- drift/sync telemetry и confidence signals
- guided calibration с approve/apply/rollback flow
- bounded auto-tuning только там, где measurement quality достаточно высок

### V3-5. AI-assisted diagnostics и deployment planning

AI должен быть operator copilot, а не скрытым control plane.

Ключевой scope:

- canonical machine-readable diagnostics bundle
- planner развёртывания для HA add-on / Docker / RPi / LXC
- plain-language diagnostics summary и safe next actions
- redaction, opt-in и provider/local boundaries
- AI-слой поверх тех же typed diagnostics, capability и event-history models

### V3-6. Fleet control plane для нескольких bridge

Главная цель:

- превратить несколько bridge в управляемую систему только после зрелости single-bridge multi-backend story

Ключевой scope:

- stable identity для bridge-инстансов
- aggregate health, inventory, room coverage и backend mix
- bulk diagnostics, compare/export/import config и fleet event timeline
- reuse того же event model и hook/webhook contracts

### V3-7. Избирательное расширение после стабилизации

Только после стабильных предыдущих фаз:

- system-wide audio runtime / non-user-scoped socket support
- richer sync/drift telemetry across groups and bridges
- Snapcast/VBAN/backend strategy tracks
- federation / plugin / HA component strategy
- per-room DSP / EQ surfaces

## Ограничения

Не нужно:

- делать giant all-at-once rewrite всех слоёв
- начинать speculative backend'ы до platform contracts
- делать AI обязательным или cloud-only
- включать opaque auto-remediation без operator approval
- гнать fleet-first complexity до зрелого single-bridge multi-backend продукта

Нужно:

- держать Bluetooth reliability главным приоритетом
- трактовать v3 как compatibility-preserving refresh, а не как запрет на глубокие замены
- делать wired/USB additive, а не ломающим базовый runtime
- держать architecture ahead of product sprawl
- разрешать frontend заменять целые high-churn screens, если это улучшает operator workflows
- делать AI optional и secret-safe
- делать auto delay tuning bounded и explainable
- держать fleet management additive, а не обязательным для одиночного bridge
- выпускать migrations, docs и tests вместе с каждой фазой

## Реалистичный первый milestone для v3

`v3.0.0-rc.1` должен реалистично включать:

- `V3-0` как уже достигнутый baseline polish
- ядро `V3-1`:
  - backend contracts и capability model
  - foundations для config/runtime model v2
  - event history и simulator foundation
  - первые operator-console platform pieces
- ядро `V3-2`:
  - первый wired/USB backend
  - backend-aware player creation/edit flow
  - первые новые diagnostics/details surfaces
- базовую audio health visibility и signal-path publication
- delay telemetry foundations и manual calibration path
- diagnostics bundle foundations для будущего planner/AI слоя

То есть первый RC должен ощущаться как: **"Bluetooth-first multi-backend platform с modern operator console и заметно лучшей audio visibility"**.

Полная англоязычная версия остаётся в [`ROADMAP.md`](ROADMAP.md).
