# Дорожная карта v3

> Последнее обновление: март 2026 (v2.52.1)

**Обозначения статуса:** ✅ Реализовано · 🔄 В процессе · без отметки = Запланировано

## Назначение

Эта дорожная карта написана для **волны v3**, стартующей от реальности, уже выпущенной в `v2.46.x+`.

v3 стоит трактовать как **compatibility-preserving platform refresh**:

- это **не** rewrite с нуля
- но и **не** обещание вечно ограничиваться только маленькими incremental-изменениями
- это осознанная возможность модернизировать архитектуру, backend contracts и operator-facing UI, не жертвуя Bluetooth reliability

Проект уже имеет:

- ✅ ~~явную bridge lifecycle и orchestration-модель~~
- ✅ ~~typed status и diagnostics read models~~
- ✅ ~~нормализованные onboarding, recovery и operator-guidance surfaces~~
- ✅ ~~config migration и validation flows~~
- 🔄 room metadata, readiness и handoff foundations для room-aware сценариев Music Assistant — группы MA реализованы, но нет нативного назначения комнат
- ✅ ~~усиленные Docker и Raspberry Pi startup diagnostics, которые выводят runtime UID, audio socket path, socket ownership и live `pactl` probe status~~
- ✅ ~~versioned subprocess IPC и стабильные operator-facing diagnostics endpoints~~

Дорожная карта поэтому отвечает на более конкретный вопрос:

- какие глубокие seams нужно заменить, чтобы v3 было проще развивать
- как сделать wired и USB support настоящей продуктовой волной, а не экспериментом
- как превратить UI в modern operator console, не теряя уже работающие deployment realities

## Обновлённый приоритет v3

Первое крупное расширение v3 — по-прежнему **wired и USB audio support**, но теперь его нельзя трактовать как isolated backend-track.

Новая логика порядка:

1. считать operator polish wave уже собранной baseline-частью, а не главной активной фазой
2. запускать v3 как координированную программу из трёх треков: architecture, backend, frontend
3. выпустить первую multi-backend волну как **shared platform contracts + wired/USB runtime + modern operator console**
4. поднять observability, signal path и delay intelligence до first-class уровня раньше AI и fleet
5. расширяться в AI-assisted support и multi-bridge control plane только после зрелого single-bridge multi-backend story

Bluetooth при этом остаётся главным и самым battle-tested runtime. v3 должен расширять продукт вокруг этого ядра, а не понижать его.

## Продуктовый тезис v3

Sendspin BT Bridge v3 должен стать **Bluetooth-first, room-aware, multi-backend audio platform с modern operator console**.

Это значит — сохранять Bluetooth reliability как ядро продукта, добавляя поверх пять больших возможностей:

1. **Shared platform layer** с явными backend contracts, capability modeling, config/runtime separation и event history
2. **USB DAC и wired audio support** как первый смежный backend, затем virtual sink composition
3. **Observability-first operations** с signal-path visibility, health summaries, recovery timelines и delay intelligence
4. **Modern operator console** для creation, diagnostics, history и bulk operations
5. **AI-assisted diagnostics и позднее fleet management**, построенные на тех же typed data models, а не на отдельной ad hoc логике

## Стартовая база из v2

Дорожная карта считает следующее уже устоявшимися основами:

- ✅ ~~Bluetooth остаётся primary и самым battle-tested runtime~~
- ✅ ~~onboarding, recovery guidance, diagnostics и bugreport tooling — реальные operator-facing surfaces~~
- ✅ ~~bridge и device health, recent events и blocked-action reasoning достаточно явные, чтобы на них строить~~
- 🔄 room metadata, transfer readiness и fast-handoff profiles — группы MA реализованы, но нативное назначение комнат пока отсутствует
- ✅ ~~Home Assistant и Music Assistant integration — часть нормального продуктового пути, а не afterthought~~
- ✅ ~~Docker и Raspberry Pi startup diagnostics уже выводят runtime UID, audio socket path, socket ownership и live `pactl` probe status~~
- ✅ ~~subprocess IPC, config migration и diagnostics endpoints уже ведут себя как versioned product contracts~~

### Детализация реализованного в v2

#### ✅ Руководство оператора и восстановление

- ✅ ~~Панель руководства оператора (`services/operator_guidance.py`)~~
- ✅ ~~Помощник восстановления (`services/recovery_assistant.py`)~~
- ✅ ~~Помощник начальной настройки (`services/onboarding_assistant.py`)~~
- ✅ ~~Отслеживание здоровья устройств (`services/device_health_state.py`)~~
- ✅ ~~API диагностики (`/api/diagnostics`, `/api/status/*`)~~
- ✅ ~~Отчёт об ошибке через GitHub (`routes/api_status.py`)~~

#### ✅ Управление устройствами и мостом

- ✅ ~~Реестр устройств (`services/device_registry.py`)~~
- ✅ ~~Оркестрация жизненного цикла моста (`bridge_orchestrator.py`)~~
- ✅ ~~Модель состояния моста и снимки (`services/bridge_state_model.py`)~~
- ✅ ~~Управление состоянием жизненного цикла (`services/lifecycle_state.py`)~~
- ✅ ~~Сопряжение и подключение устройств (`services/bluetooth.py`)~~

#### ✅ Интеграция с Music Assistant

- ✅ ~~Клиент и монитор MA (`services/ma_client.py`, `ma_monitor.py`)~~
- ✅ ~~Обнаружение MA через mDNS (`services/ma_discovery.py`)~~
- ✅ ~~Группы и синхронизация MA (`routes/ma_groups.py`)~~
- ✅ ~~Управление воспроизведением и очередью (`routes/ma_playback.py`)~~
- ✅ ~~Прокси обложек (`services/ma_artwork.py`)~~
- ✅ ~~OAuth/token авторизация для MA (`routes/ma_auth.py`)~~
- ✅ ~~WebSocket подключение к MA (`services/ma_monitor.py`)~~

#### ✅ Управление аудио и транспорт

- ✅ ~~Нативное управление транспортом (`routes/api_transport.py`)~~
- ✅ ~~Режим ожидания / отключение при простое~~
- ✅ ~~Контроллер громкости PA (`services/pa_volume_controller.py`)~~
- ✅ ~~Компенсация статической задержки~~

#### ✅ Инфраструктура

- ✅ ~~Вебхуки (`services/event_hooks.py`)~~
- ✅ ~~Внутренние события pub/sub (`services/internal_events.py`)~~
- ✅ ~~Версионирование IPC протокола (`services/ipc_protocol.py`)~~
- ✅ ~~Управление подпроцессами (`services/daemon_process.py`)~~
- ✅ ~~Интеграция с HA Core API (`services/ha_core_api.py`)~~
- ✅ ~~Слой совместимости Sendspin (`services/sendspin_compat.py`)~~
- ✅ ~~Проверка обновлений (`services/update_checker.py`)~~

#### ✅ Конфигурация

- ✅ ~~Валидация конфигурации (`services/config_validation.py`)~~
- ✅ ~~Миграция конфигурации (`config_migration.py`)~~
- ✅ ~~Потокобезопасное хранение конфигурации (`config.py`)~~

#### ✅ Развёртывание

- ✅ ~~HA аддон (`ha-addon/`, `ha-addon-beta/`, `ha-addon-rc/`)~~
- ✅ ~~Docker мультиархитектурная сборка (amd64, arm64, armv7)~~
- ✅ ~~LXC развёртывание (`lxc/`)~~
- ✅ ~~Лендинг (`landing/`)~~
- ✅ ~~Документация (`docs-site/`)~~
- ✅ ~~Дашборд статистики (`docs-site/src/pages/stats/`)~~
- ✅ ~~CI/CD pipeline с поддержкой beta-ветки~~

#### ✅ Тестирование

- ✅ ~~965+ тестов в 68+ файлах~~

## Три координированных трека v3

v3 нельзя описывать как «backend-работа с frontend-enablement сбоку». Он должен работать как три координированных трека, которые выпускаются вместе в каждой крупной фазе.

### Трек A. Architecture и platform contracts

Этот трек задаёт долгоживущий каркас v3:

- определить настоящий `AudioBackend`-style contract вместо того, чтобы Bluetooth оставался неявной моделью
- сделать capability modeling явным в API и snapshots
- сжать `state.py` в сторону compatibility/cache layer, а не архитектурного центра
- более жёстко разделить `user config`, `runtime state` и `derived metadata`
- стандартизироваться на typed read models и лёгком internal event model
- держать simulator и mock runtime support first-class, чтобы backend и UI работа не требовала железа

### Трек B. Backend и runtime expansion

Этот трек делает v3 по-настоящему multi-backend:

- сохранить Bluetooth как primary runtime и reliability benchmark
- добавить wired и USB outputs как first-class player types, а не special cases
- открыть virtual sinks и composed zones, когда смежный backend story станет реальностью
- провести route ownership, health и signal-path visibility сквозь все типы backend'ов
- сделать delay и sync tooling backend-aware, а не Bluetooth-only

### Трек C. Frontend и operator console

Этот трек превращает текущий web UI в более чёткий операционный продукт:

- эволюционировать от монолитного runtime script к typed feature modules и shared UI primitives
- использовать **Vue 3 + TypeScript + Vite** для новых и заменяемых high-churn surfaces
- сохранить server-driven entry points, ingress compatibility и fetch/SSE contracts там, где они всё ещё полезны
- построить настоящий operator console вокруг creation flows, diagnostics, details drawers, timelines и bulk actions
- разрешать замену целых high-churn screens, когда это даёт более чистый продукт, чем бесконечные incremental patches

### Трек D. Management CLI (`sbb`)

Этот трек добавляет терминальный интерфейс оператора параллельно веб-интерфейсу:

- автономный CLI-инструмент (`sbb`), работающий через существующий REST API — без прямой связи с runtime
- **Click** в качестве фреймворка (уже является транзитивной зависимостью через Flask, ноль новых deps)
- noun-verb структура команд (`sbb device list`, `sbb config get`, `sbb ma groups`) по образцу kubectl/docker
- двойной режим работы: параметрический (one-shot) и интерактивный REPL (`sbb shell`) с общими функциями-обработчиками
- `--output json|table|yaml|csv` для машинно-читаемого и человеко-понятного вывода
- автодополнение для bash, zsh и fish, генерируемое из определений команд Click
- цепочка обнаружения конфигурации: CLI-флаг → переменная `SBB_URL` → `~/.config/sbb/config.toml` → значение по умолчанию `http://localhost:8080`
- возможность отдельной установки от bridge (например, на ноутбук для управления удалённым инстансом через `pip install sbb-cli`)
- **rich** как опциональная soft-dependency для стилизованных таблиц; plain-text fallback при отсутствии
- **prompt_toolkit** для интерактивного REPL с persistent history и динамическим автодополнением MAC/адаптеров

CLI **не заменяет** веб-интерфейс — он дополняет его для SSH-доступа, скриптинга, CI/CD-автоматизации и power-user сценариев.

## Целевые результаты

v3 успешен, когда проект может делать всё перечисленное, не становясь хрупким или непрозрачным:

1. Один bridge скучен и надёжен в HA, Docker, Raspberry Pi и LXC environments.
2. Тот же bridge может хостить Bluetooth players и хотя бы один wired или USB-backed player type с целостным operator UX.
3. Операторы могут создавать, диагностировать, тюнить и восстанавливать плееры из modern console, а не собирать решение из множества разрозненных UI surfaces.
4. Signal path, route ownership, health и event history видны настолько, что проблемы обнаруживаются UI раньше, чем на слух.
5. Delay tuning становится guided и explainable, а не trial and error.
6. AI support и позднее fleet management могут строиться на тех же contracts, diagnostics bundles и event history, а не изобретать отдельные data models.

## Фаза V3-0: Baseline operator polish перед v3

### Статус

✅ По сути уже закрыто в текущем коде. Эта секция сохраняется как baseline context, а не как главная активная фаза.

### Цель

Задокументировать operator polish, который теперь формирует спокойную стартовую поверхность для v3.

### Scope

- ✅ ~~полный onboarding доминирует только для настоящего empty state~~
- ✅ ~~preview и confirm для grouped recovery actions перед запуском multi-device operations~~
- ✅ ~~меньше шума в compact/mobile recovery (`top issue + N more`, меньше дублирования copy)~~
- ✅ ~~blocked row-level hints согласованы с одним top-level guidance owner~~
- ✅ ~~diagnostics и recovery detail доступны, даже когда top-level guidance компактен~~

### Exit criteria

- ✅ ~~зрелые инсталляции спокойны по умолчанию~~
- ✅ ~~grouped recovery actions ощущаются осознанными и понятными~~
- ✅ ~~top-level guidance владеет основным объяснением, а не дублированный microcopy~~

### Текущая оценка

Эти результаты уже отражены в выпущенных operator-guidance и recovery flows. Активная работа по roadmap должна начинаться с **V3-1**, а не с V3-0.

## Фаза V3-1: Platform reset для v3

### Цель

Собрать shared platform model для v3 и заложить первые modern operator-console foundations параллельно.

### Scope

#### Epic 1. Runtime contracts и ownership seams

- 🔄 определить `AudioBackend`-style contract для lifecycle, capabilities, health, diagnostics, room metadata и route ownership — основа заложена в `status_snapshot.py`, но формальный `AudioBackend` ABC пока отсутствует
- обернуть существующий Bluetooth runtime за этим контрактом первым
- ✅ ~~держать subprocess и control-plane contracts backend-agnostic где практично~~
- сжать `state.py` из архитектурного центра в compatibility/cache layer по мере перехода routes и services к explicit ownership и snapshot reads

#### Epic 2. Config и runtime model v2

- перейти от Bluetooth-device-only модели к player и backend-ориентированной конфигурации
- 🔄 отделить user-owned config от runtime-derived state и generated metadata — `bridge_state_model.py` реализован, но config schema v2 ещё не создана
- ✅ ~~добавить compatibility loading и migration tooling для текущей схемы~~
- держать downgrade и partial-migration assumptions явными и задокументированными

#### Epic 3. Event model, read models и simulator foundation

- 🔄 стандартизироваться на лёгком internal event model — `internal_events.py` реализован, но персистентность истории событий пока отсутствует
- сделать per-device и per-bridge event history first-class typed surface, а не разрозненные ad hoc payloads
- расширить typed snapshots и health summaries, чтобы degraded-mode reporting стал продуктовой поверхностью, а не только debug aid
- держать mock runtime и simulator path жизнеспособными для backend, config, diagnostics и onboarding flows
- сделать hardware-light tests нормальным validation path для contract work

#### Epic 4. Operator console foundation

- принять **Vue 3 + TypeScript + Vite** для новых и заменяемых high-churn surfaces
- построить typed frontend models и stores вокруг `BridgeSnapshot`, `DeviceSnapshot`, guidance, diagnostics, jobs и event history
- определить shared design tokens, headless accessible primitives и reusable drawer/dialog/filter/table patterns
- сохранить Flask-rendered entry points и ingress compatibility, но разрешить замену high-churn UI surfaces, где это даёт более чистый продукт

### Exit criteria

- runtime может описать backend-neutral players и explicit capabilities
- config/runtime separation реальна и достаточна для чистой поддержки будущих backend'ов
- event history и typed read models используются слоями diagnostics и UI
- ключевые backend и UI flows можно проверить без реального Bluetooth hardware
- у проекта есть жизнеспособная modern-console foundation, а не только один растущий runtime script

## Фаза V3-2: Modern operator console и wired/USB runtime

### Цель

Выпустить первую явно multi-backend продуктовую волну: wired и USB players плюс новые operator workflows, необходимые для хорошего управления ими.

### Scope

#### Epic 5. Wired и USB backend

- обнаруживать ALSA и PulseAudio / PipeWire output sinks через `pactl list sinks` и `aplay -l`
- фильтровать и классифицировать вероятные outputs: USB DAC, built-in audio, HDMI, virtual sinks
- создать direct-sink player type, который переиспользует subprocess model, status reporting, volume control и diagnostics patterns без Bluetooth pairing lifecycle
- поддержать per-device volume persistence, mute state и backend-specific health reporting

#### Epic 6. Capability-driven player management UX

- заменить самые high-churn device-management flows backend-aware creation и edit experience
- добавить typed forms, validation и room/alias mapping для Bluetooth и wired players
- показывать обнаруженное hardware с backend type, friendly naming и capability hints
- использовать overview + details-drawer patterns вместо перегрузки одной монолитной page surface

#### Epic 7. Hotplug и route lifecycle

- отслеживать появление и исчезновение wired и USB devices
- уведомлять UI, когда новый sink становится доступен, меняет identity или исчезает
- опционально разрешить operator-approved player creation для вновь обнаруженных USB DAC
- явно показывать route ownership и sink disappearance issues в новой console, а не прятать их в логах

#### Epic 8. Management CLI foundation (`sbb`)

- создать пакет `sbb_cli/` с Click-based grouped subcommands
- реализовать `BridgeClient` HTTP-обёртку для REST API с timeout, auth и структурированным маппингом ошибок
- реализовать основные группы команд: `device` (list, info, scan, pair, remove, connect, disconnect, enable, disable, wake, standby), `adapter` (list, power, scan), `config` (show, get, set, export, import, validate), `status` (show, health, groups), `logs` (show, follow, download), `diag` (preflight, runtime, bugreport, recovery), `ma` (discover, groups, nowplaying, login), `update` (check, apply)
- добавить top-level shortcuts: `volume`, `mute`, `restart`
- реализовать `--output json|table|yaml|csv` форматирование с rich как опциональной soft-dependency
- цепочка обнаружения конфигурации: CLI-флаг → переменная `SBB_URL` → `~/.config/sbb/config.toml` → значение по умолчанию
- генерация скриптов автодополнения для bash, zsh и fish через `sbb completion`
- SSE streaming для `status show --watch` и `logs follow`
- реализовать `sbb shell` — интерактивный REPL с prompt_toolkit, persistent history и динамическим автодополнением MAC/адаптеров
- публикация на PyPI как `sbb-cli` для standalone-установки

### Exit criteria

- USB DAC и wired outputs появляются в UI рядом с Bluetooth speakers как first-class player shapes
- операторы могут создавать и управлять wired players через новые operator workflows, а не raw config edits
- Bluetooth и wired players разделяют одну capability-driven model без регрессий Bluetooth reliability
- modern console отвечает за самые high-churn player-management paths
- `sbb` CLI может листать устройства, показывать статус, управлять конфигурацией и запускать диагностику из терминала без браузера

## Фаза V3-2.5: Virtual sinks и composed zones

### Цель

Превратить PulseAudio virtual sinks в реальные продуктовые поверхности, когда первая multi-backend модель уже работает.

### Scope

#### Epic 9. Combine sink creation

- добавить operator flows для выбора 2+ sinks и создания `module-combine-sink`
- целевые сценарии: party mode, open floor plans, лёгкая multi-room группировка
- включить test-tone или route verification action

#### Epic 10. Remap sink creation

- добавить operator flows для извлечения каналов из multi-channel devices через `module-remap-sink`
- целевые сценарии: split-zone, например 4-канальный USB DAC, становящийся двумя stereo zones
- поддержать стандартный PulseAudio channel-name mapping и наглядные channel previews

#### Epic 11. Composed-zone lifecycle management

- сохранять custom sinks в config и воссоздавать при перезапуске
- показывать state, configuration summary, capability surface и delete actions
- проверять наличие master и slave sinks перед попыткой создания
- позволить virtual sinks участвовать в player creation и room assignment flows

### Exit criteria

- операторы могут создавать combine и remap sinks без прямой работы с `pactl`
- composed zones переживают рестарты и естественно вписываются в player-management flows
- ошибки явно показаны, когда prerequisite sinks недоступны

## Фаза V3-3: Observability-first runtime и operations center

### Цель

Сделать health, signal path и recovery state first-class operator surfaces, а не advanced diagnostics, спрятанные за логами.

### Scope

#### Epic 12. Live telemetry и degraded-mode summaries

- показывать текущий codec, sample rate, buffer и stream state, uptime, reconnect count и resolved output sink где доступно
- получать telemetry из subprocess status lines, bridge state, backend callbacks и event history
- включить structured per-device event history: reconnects, sink loss/acquisition, route corrections, re-anchor events, MA sync failures
- публиковать compact degraded-mode и health-summary surfaces в дополнение к raw live status

#### Epic 13. Signal path и route ownership visibility

- 🔄 рендерить end-to-end path для каждого типа backend — health-данные доступны, но сквозная визуализация сигнального пути пока отсутствует:
  - MA → Sendspin → subprocess → PulseAudio / PipeWire sink → Bluetooth A2DP → speaker
  - MA → Sendspin → subprocess → PulseAudio / ALSA sink → wired speaker / DAC
- показывать measured или estimated latency на каждом hop где доступно
- указывать route ownership, bottlenecks или degraded hops: codec fallback, sink mismatch, missing route ownership

#### Epic 14. Operations center и reusable UI system

- построить unified diagnostics и recovery center вместо разбрасывания операционных деталей по несвязанным UI sections
- добавить frontend operation model, который может показывать live state, pending actions, recovery history и bulk actions без дублирования бизнес-логики по cards, rows, dialogs и modals
- выстроить более сильную UI component system: badges, notices, toasts, drawers, dialogs, filters, timeline/event-list views и более спокойная mobile density
- предпочитать split-pane, drawer и progressive-disclosure patterns, которые масштабируются на desktop и mobile лучше, чем бесконечно расширяющиеся rows

### Exit criteria

- операторы видят codec, sample rate, sink route, health и event history без чтения логов
- signal path понятен с первого взгляда для Bluetooth, wired и virtual-sink players
- degradation выявляется проактивно, а не обнаруживается только после того, как звук начал звучать неправильно
- у UI есть reusable operations vocabulary, а не ручная сборка каждой diagnostic surface

## Фаза V3-4: Delay intelligence и guided tuning

### Цель

Уменьшить ручной подбор `static_delay_ms` и сделать sync decisions более измеримыми, guided и explainable.

### Scope

#### Epic 15. Delay telemetry foundation

- захватывать timing и drift telemetry, которые могут поддержать per-device delay decisions
- показывать sync health, drift, confidence и measurement quality на уровне diagnostics и оператора
- разделять «мы можем что-то измерить» и «мы доверяем этому достаточно, чтобы рекомендовать изменение тюнинга»

#### Epic 16. Guided delay calibration

- добавить calibration flow, который может измерить и предложить `static_delay_ms`
- показывать recommended value, confidence и before/after сравнение
- разрешить approve, apply и rollback вместо принудительного ручного редактирования

#### Epic 17. Bounded auto-tuning

- добавить опциональную консервативную автоматическую подстройку для устройств со стабильным measurement quality
- держать adjustments bounded, visible и reversible
- показывать, когда auto-tuning выключен, uncertain или недавно откачен

### Exit criteria

- большинство пользователей могут достичь хорошего delay value без trial-and-error editing
- delay recommendations видимы и объяснимы
- любой automatic tuning остаётся консервативным и operator-traceable

## Фаза V3-5: AI-assisted diagnostics и deployment planning

### Цель

Использовать AI как **operator copilot**, а не как скрытый control plane.

### Scope

#### Epic 18. Structured diagnostics bundles

- определить canonical machine-readable diagnostics bundle, объединяющий:
  - bridge и runtime state
  - device snapshots
  - recovery timeline
  - deployment environment facts
  - preflight results
  - backend identity и routing facts
- сделать bundle достаточно стабильным для support tooling, bug reports и будущих AI consumers

#### Epic 19. Deployment planner

- добавить planner, который может инспектировать environment facts и предлагать:
  - рекомендованный install path (HA add-on, Docker, Raspberry Pi, LXC)
  - необходимые mounts и capabilities
  - вероятные `AUDIO_UID`, port и adapter configuration
  - когда wired или USB outputs лучше подходят для комнаты, чем Bluetooth
  - safe next steps для первого развёртывания
- держать planner operator-facing: генерировать планы и config suggestions, а не silent changes

#### Epic 20. AI diagnostics summarizer

- резюмировать failures plain language по данным diagnostics
- ранжировать likely root causes и safe next actions
- генерировать support-ready summaries для GitHub или forum issues
- разрешить prompt export или support bundle export для внешнего или local AI analysis
- показывать AI summaries так, чтобы сохранялся operator trust:
  - explicit provenance из diagnostics data
  - visible confidence и uncertainty
  - one-click доступ к underlying raw diagnostics и event history

#### Epic 21. AI safety и privacy boundaries

- редактировать secrets перед любым external AI handoff
- поддерживать pluggable providers и local/manual mode
- требовать explicit operator approval перед применением suggested changes
- держать non-AI diagnostics полностью usable сами по себе
- строить AI summaries на тех же typed diagnostics, capability и event-history models, что используются non-AI tooling и operator console

### Exit criteria

- diagnostics bundles стабильны и структурированы
- deployment planning полезен для реальных пользователей, особенно HA, Docker, Raspberry Pi и смешанных Bluetooth/wired инсталляций
- AI-generated explanations улучшают support, не становясь обязательными для нормальной работы

## Фаза V3-6: Централизованный multi-bridge control plane

### Цель

Превратить несколько bridge instances в управляемый fleet только после того, как single-bridge multi-backend продукт и modern operator console станут solid.

### Scope

#### Epic 22. Bridge registry и fleet identity

- определить stable bridge instance identity и registration semantics
- агрегировать version, host, adapter, room, backend и health metadata по bridge'ам
- обнаруживать duplicate speakers, overlapping rooms и inconsistent bridge naming

#### Epic 23. Fleet overview и bulk operations

- построить centralized overview для:
  - bridge health
  - device inventory
  - room coverage
  - recovery attention
  - update status
- добавить safe bulk actions:
  - restart selected bridges
  - re-run diagnostics на selected bridges
  - export и import configuration sets
  - compare configs и versions across the fleet

#### Epic 24. Fleet event timeline и policy surfaces

- централизовать event и recovery timelines через bridge'и
- добавить fleet-level webhook и telemetry views
- разрешить higher-level policies: room ownership, update-channel consistency
- переиспользовать тот же lightweight internal event model и hardened hook/webhook contracts вместо отдельных fleet-only event semantics

### Exit criteria

- операторы могут рассуждать о нескольких bridge'ах как об одной системе
- duplicate или conflicting configuration легче обнаружить до того, как они вызовут runtime issues
- fleet operations не заменяют single-bridge simplicity; они расширяют его

## Фаза V3-7: Избирательное расширение после стабилизации ядра

### Кандидатные работы

Начинать их только после стабилизации предыдущих фаз и подтверждённого спроса:

- system-wide audio runtime или non-user-scoped socket support для Raspberry Pi и других embedded hosts, где per-user PulseAudio или PipeWire sessions создают проблемы
- richer sync и drift telemetry across groups и bridges
- Snapcast, VBAN или другие backend strategy tracks
- multi-bridge federation за пределами single control plane
- Home Assistant custom component или HACS strategy
- plugin или extension surfaces
- per-room DSP и EQ через virtual sinks или backend-specific processing surfaces

## Сквозные ограничения

### 1. Bluetooth reliability остаётся на первом месте

Никакая тема v3 не должна ухудшать реальные Bluetooth deployments в HA, Docker, Raspberry Pi или LXC.

### 2. v3 — compatibility-preserving platform refresh

Сохранять operator trust, ingress compatibility и stable contracts там, где они уже работают, но разрешать глубокую замену runtime seams и high-churn UI surfaces, когда это даёт более чистый v3 foundation.

### 3. Wired и USB support должен оставаться additive

Первый смежный backend должен переиспользовать проверенные runtime seams, diagnostics и subprocess patterns, а не заменять их полностью.

### 4. Architecture должна опережать product sprawl

Backend expansion, AI summaries и fleet views должны строиться на explicit services, typed read models, capability modeling, event history и hardware-light testability, а не обходить эти foundations.

### 5. Frontend modernization может заменять high-churn surfaces

Новая frontend infrastructure должна снижать сложность, улучшать accessibility и делать operational workflows понятнее. Она не обязана останавливаться на маленьких островках, если замена high-churn screen — более чистый путь.

### 6. AI должен быть optional и operator-controlled

- никакой обязательной cloud dependency
- никакого silent external sharing sensitive config или state
- никакого hidden auto-remediation без explicit approval

### 7. Delay automation должен быть bounded и explainable

Система может предлагать или консервативно auto-apply delay changes, но никогда как opaque magic.

### 8. Fleet management должен оставаться additive

Один bridge должен оставаться простым в deploy и эксплуатации. Fleet management не должен становиться обязательным для базового использования.

### 9. Migrations, docs и tests выпускаются с каждой фазой

v3 должен добавлять compatibility layers, постепенно мигрировать callers и config, документировать новые contracts по мере их появления и расширять tests параллельно с каждым новым backend или diagnostics surface.

### 10. CLI должен оставаться чистым HTTP-клиентом

`sbb` CLI не должен импортировать runtime-код bridge и зависеть от Bluetooth, PulseAudio или D-Bus библиотек. Он взаимодействует исключительно через REST API и может быть установлен на любой машине, включая ноутбуки разработчиков без аудио-оборудования.

## Заметки о реализации и зависимостях

Фазы roadmap выше — продуктовые, но наиболее безопасный порядок реализации внутри них должен учитывать несколько program-level dependencies:

- двигать три трека вместе в release waves, а не трактовать frontend как late enablement после завершения backend work
- выпустить backend contracts, event history и config/runtime separation до того, как AI или fleet features начнут от них зависеть
- держать simulator и mock-runtime improvements рядом с backend и UI changes, чтобы новые flows оставались тестируемыми без hardware
- использовать одни и те же event contracts для diagnostics, hooks, operator timelines и будущих fleet views
- заменять high-churn surfaces первыми: player creation/editing, details views, diagnostics и history должны мигрировать раньше, чем уже стабильные pages будут переписаны ради самих себя
- позволить virtual sinks и позднее fleet views строиться на тех же capability и read-model surfaces, что используются Bluetooth и wired players

## Вне scope раннего v3

- giant all-at-once rewrite каждого слоя
- speculative backends до появления backend contract
- AI-driven silent configuration edits
- fleet-first complexity до доказанности single-bridge multi-backend story
- замена operator diagnostics на AI вместо дополнения ими
- отказ от deployment compatibility realities вроде HA ingress без доказуемо лучшего operator path

## Реалистичный первый milestone v3

Реалистичный `v3.0.0-rc.1` должен включать:

- ✅ ~~V3-0 уже выпущенный guidance и recovery polish как baseline~~
- ядро V3-1:
  - backend contracts и capability modeling
  - config и runtime model v2 foundations
  - event-history и simulator foundations
  - первые operator-console platform pieces
- ядро V3-2:
  - первый wired и USB backend
  - backend-aware player creation и editing flows
  - первые новые diagnostics и details surfaces в operator console
  - `sbb` CLI с основными командами device, config, status и diagnostics
- baseline audio health visibility и signal-path publication
- delay telemetry foundations и manual calibration path
- structured diagnostics bundle foundations для будущей planner и AI работы

Этого достаточно, чтобы v3 ощущался принципиально иначе: **«Bluetooth-first multiroom с настоящей multi-backend platform, modern operator console и заметно лучшей audio visibility»**.
