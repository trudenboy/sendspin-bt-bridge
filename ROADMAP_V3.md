# sendspin-audio-bridge — Roadmap v3.0+

## Контекст

Текущий `ROADMAP.md` (фазы 1–6) закрывает внутренний рефакторинг BT-bridge: snapshot-модели,
оркестратор, versioned IPC, lifecycle-сервисы, события. Это фундамент. **v3.0 строится поверх него**
и расширяет проект до универсального аудио-bridge с несколькими backend-типами.

### Что MA уже закрывает — не дублируем

Snapcast output, SlimProto/Squeezelite, AirPlay 1+2, Chromecast+Sendspin, DLNA, WiiM (MA 2.9.0).

### Незакрытые ниши, которые занимает bridge

1. **A2DP Bluetooth** — ядро проекта, MA не имеет нативного BT-вывода вообще
2. **Любой локальный аудио-выход** (ALSA, PA sink, USB DAC, HDMI) как MA-плеер
3. **Snapcast → любой выход** — bridge как клиент, а не сервер (другое направление потока)
4. **VBAN** — MA VBAN alpha и только receive; bridge добавляет send→output путь
5. **LE Audio / LC3** — DIY-готовность ~2027, закладываем инфраструктуру сейчас
6. **Auracast broadcast** — RPi как TX без паринга, очень долгосрочно

### Интеграционная модель

MA придерживается принципа "не привязываться к hardware". Bridge физически управляет BT-адаптерами,
PulseAudio синками и переподключением устройств — это hardware-специфично по природе. Поэтому:

- **Правильный путь интеграции:** bridge → HA addon → `media_player` entities → MA через
  `hass_players` provider
- MA player provider в upstream **не планируется** — противоречит философии MA
- **HA Custom Component (HACS)** — центральный механизм интеграции, не "nice to have"
- OpenHome OHRenderer — для standalone discovery (Linn/Kazoo/Lumin) без MA

---

## Архитектурная эволюция

### Сейчас (v2.x)

```
[MA/Sendspin Server]
         ↓ Sendspin protocol
[sendspin-bt-bridge]
    ├── BluetoothManager × N (per device)
    ├── SendspinClient × N → subprocess(PULSE_SINK=bluez_sink.MAC)
    ├── Flask API
    └── state.py (shared mutable state)
```

### v3.0 — Backend Abstraction Layer

```
[MA / Sendspin Server]
         ↓ Sendspin protocol (per registered player)
[sendspin-audio-bridge]
    ├── BackendOrchestrator
    │   ├── BluetoothA2DPBackend  ← текущее, обёрнутое
    │   ├── LocalSinkBackend      ← v3.1: ALSA/PA/PW синки
    │   ├── USBAudioBackend       ← v3.1: USB DAC auto-discover
    │   ├── VirtualSinkBackend    ← v3.1: null-sink, loopback
    │   ├── SnapcastClientBackend ← v3.2: receive от Snapcast-сервера
    │   ├── VBANBackend           ← v3.2: VB-Audio UDP
    │   ├── LEAudioBackend        ← v3.4: BLE LC3 (experimental)
    │   └── AuracastTXBackend     ← v3.5: BLE broadcast
    ├── PlayerRegistry            ← единый реестр, backend-агностик
    ├── Bridge Core               ← Flask, config v2, HA/MA integration
    └── SubprocessPool            ← оптимизированный, lazy spawn
```

### `AudioBackend` интерфейс

```python
class AudioBackend(ABC):
    async def connect(self) -> bool: ...
    async def disconnect(self): ...
    def get_sink_name(self) -> str | None: ...  # PA sink, если применимо
    def get_capabilities(self) -> BackendCapabilities: ...
    async def set_volume(self, level: int): ...
    async def get_volume(self) -> int: ...
    def get_status(self) -> BackendStatus: ...
```

Subprocess (`daemon_process.py`) остаётся backend-агностичным: получает `PULSE_SINK` или
`AUDIO_DEVICE` и запускает sendspin-плеер. Backend обеспечивает готовность назначения до старта
subprocess.

---

## Config schema v2

```json
{
  "CONFIG_SCHEMA_VERSION": 2,
  "players": [
    {
      "id": "living-room-bt",
      "player_name": "Living Room",
      "backend": {
        "type": "bluetooth_a2dp",
        "mac": "FC:58:FA:EB:08:6C",
        "adapter": "hci0",
        "prefer_sbc_codec": false
      },
      "static_delay_ms": -600,
      "listen_port": 8928,
      "enabled": true
    },
    {
      "id": "kitchen-local",
      "player_name": "Kitchen DAC",
      "backend": {
        "type": "local_sink",
        "sink_name": "alsa_output.usb-Focusrite_2i2.analog-stereo"
      },
      "static_delay_ms": 0,
      "enabled": true
    }
  ],
  "adapters": []
}
```

Миграция: `scripts/migrate_v2_to_v3.py` — автоматически конвертирует `bluetooth_devices[]` →
`players[]` с `backend.type=bluetooth_a2dp`.

---

## Phase 0: Rebrand & Foundation — v3.0.0

**Ветка:** `dev/v3.0` (нестабильная, breaking changes разрешены)

**Prerequisite:** завершение фаз 1–3 текущего `ROADMAP.md` (snapshot-модели, оркестратор,
versioned IPC).

### Переименование

| Было | Стало |
|------|-------|
| `sendspin-bt-bridge` | `sendspin-audio-bridge` |
| `ghcr.io/trudenboy/sendspin-bt-bridge` | `ghcr.io/trudenboy/sendspin-audio-bridge` |
| HA addon slug `sendspin_bt_bridge` | `sendspin_audio_bridge` |

### Deliverables

- `AudioBackend` ABC + `BackendCapabilities` + `BackendStatus`
- `BluetoothA2DPBackend` — обёртка вокруг существующего `BluetoothManager`
- `BackendFactory.from_config(player_config)` → `AudioBackend`
- `PlayerRegistry` — единый реестр вместо `bluetooth_devices` в `state.py`
- Config schema v2 + `scripts/migrate_v2_to_v3.py`
- Новый HA addon manifest, переименованный Docker image
- Обновлённая архитектурная документация в `docs-site`

---

## Phase 1: Local Audio Backends — v3.1.x

**Цель:** любой локальный аудио-выход хоста становится MA-плеером.
**Целевое железо:** x86 LXC (Proxmox), RPi с DAC/HDMI.

### v3.1.0 — LocalSinkBackend (PulseAudio / PipeWire)

Самый близкий к текущему коду backend: использует `pulsectl-asyncio` (уже в проекте).

```python
class LocalSinkBackend(AudioBackend):
    sink_name: str       # "alsa_output.usb-Focusrite_2i2.analog-stereo"
    auto_discover: bool  # переподключать если синк исчез и появился снова

    async def connect(self) -> bool:
        sink = await afind_sink_by_name(self.sink_name)
        return sink is not None

    def get_sink_name(self) -> str:
        return self.sink_name  # subprocess получает PULSE_SINK=this
```

- Нет reconnect loop (синк присутствует пока система работает)
- Нет `BluetoothManager` — subprocess стартует сразу
- Volume: тот же `aset_sink_volume()` из `services/pulse.py`
- Web UI: dropdown «Выбрать синк» через `pactl list sinks short`

Референс: Squeezelite + ALSA — стандартный паттерн для RPi плееров. Bridge делает то же через
Sendspin/MA.

### v3.1.1 — ALSA Direct Backend

Для контейнеров без PA/PW (OpenWrt LXC, stripped containers):

```python
class ALSADirectBackend(AudioBackend):
    device: str   # "hw:0,0" или "plughw:CARD=U192k"

    def get_sink_name(self) -> None:
        return None  # нет PA sink

    # subprocess запускается с AUDIO_DEVICE=hw:0,0
    # через sendspin --audio-device флаг
```

### v3.1.2 — USB Audio Auto-Discovery

```
USB DAC подключён → udev event → bridge обнаруживает → UI уведомляет → опционально авторегистрирует
```

- `pyudev` для мониторинга udev событий (добавление/удаление USB audio)
- `pactl list sinks` после события → сопоставление нового синка с USB-устройством
- UX-цель: «Подключи USB DAC → появляется в MA за 5 секунд»
- `auto_register: true` — автоматически создаёт плеер в конфиге

### v3.1.3 — VirtualSinkBackend

Кейсы: тестирование без железа, мониторинг/запись, loopback.

```python
class VirtualSinkBackend(AudioBackend):
    sink_type: Literal["null", "loopback", "combine"]
    stream_output: bool  # если True — поднять HTTP stream (IceCast/OGG)

    async def connect(self) -> bool:
        # pactl load-module module-null-sink sink_name=bridge_virtual_0
        # опционально: ffmpeg → IceCast на http://bridge:8080/stream/<player_id>
```

Референс: `module-null-sink` + `parec` — стандартная техника в Snapcast и Mopidy стеках.

---

## Phase 2: Subprocess Optimization — v3.1.x (параллельно)

**Цель:** поддержка 20–50 плееров на bridge без деградации ресурсов.

### Текущие затраты на subprocess

| Компонент | ~RSS |
|-----------|------|
| Python interpreter | ~30 MB |
| sendspin binary | ~40 MB |
| PA context | ~10 MB |
| **Итого per subprocess** | **~80–150 MB** |

При 20 плеерах: 1.6–3 GB. При 50: 4–7.5 GB.

### Оптимизации

**Lazy spawn** — subprocess стартует только при первом подключении MA или команде play,
не при запуске bridge.

**Idle timeout** — per-backend политика:

```python
IDLE_TIMEOUT_SECONDS = {
    "bluetooth_a2dp": None,   # всегда живой (reconnect loop)
    "local_sink":     300,    # 5 минут тишины → exit, respawn при следующем connect
    "virtual_sink":   None,   # управляется явно
    "snapcast_client": 60,
    "vban":           120,
}
```

**Pre-forked warm pool** для local sinks: N subprocess'ов предзапущены, новый плеер
«захватывает» готовый вместо cold start.

**Thinner subprocess** — backend-специфичный entrypoint вместо одного универсального
`daemon_process.py`. Цель: cold start < 500ms для local sink (сейчас ~2–3s).

**Метрики:**
- RSS/CPU per subprocess → в diagnostics API
- Суммарный footprint bridge → `/api/status/resources`
- Предупреждение при > 80% доступной памяти

---

## Phase 3: Network Protocol Backends — v3.2.x

### v3.2.0 — SnapcastClientBackend

Bridge как Snapcast-клиент. Смысл: у многих энтузиастов есть Snapcast-инфраструктура с
Volumio, Mopidy, librespot. Bridge → универсальный BT/local output для любого Snapcast-стека.

```
[Volumio / Mopidy / Spotifyd → Snapcast Server]
              ↓ TCP (python-snapcast)
[SnapcastClientBackend]
              ↓ PCM chunks → PA sink
[BT Speaker / Local DAC]
```

- Библиотека: `python-snapcast` (уже используется MA)
- Sync precision: < 0.2ms в LAN — Snapcast NTP-style timestamp per chunk
- Несколько bridge-экземпляров как Snapcast-клиенты = идеальная мульти-рум синхронизация
  без дополнительной координации

### v3.2.1 — VBANBackend

VBAN = открытый UDP-протокол VB-Audio (vbaudio.com/vban/spec). Порт 6980.
Используется в Windows-аудио-экосистеме: OBS, Voicemeeter, VB-Cable.

```
[Windows PC: Voicemeeter / OBS → VBAN sender]
              ↓ UDP 6980 (PCM 16/24/32-bit, up to 48kHz)
[VBANBackend → PA sink → BT Speaker / Local DAC]
```

```python
class VBANBackend(AudioBackend):
    bind_address: str = "0.0.0.0"
    port: int = 6980
    stream_name: str  # VBAN stream name filter

    async def _receive_loop(self):
        while True:
            data, addr = await self._sock.recvfrom(65536)
            header = VBANHeader.parse(data[:28])  # 28-byte fixed header
            pcm = data[28:]
            await self._pa_writer.write(pcm)
```

~200 строк реализации. Открытый протокол. MA VBAN — только alpha receive-only;
bridge добавляет полноценный receive → any output путь.

### v3.2.2 — LE Audio / LC3 Backend (experimental tracker)

Инфраструктурная заготовка, не для продакшна.

**Состояние экосистемы (2026):**
- BlueZ 5.86: BAP/BASS/VCP/MCP реализованы
- liblc3 (Google): открытый кодек, packaging на ARM/Linux = последний барьер
- PipeWire 1.0+: spa-codec-lc3 есть
- Реальная DIY-готовность: ~2027

```python
class LEAudioBackend(AudioBackend):
    mac: str
    experimental: bool = True  # guard flag

    def get_sink_name(self) -> str:
        return f"bluez_le_sink.{self._normalized_mac}"
```

Включается через `"experimental_backends": ["le_audio"]` в конфиге.

### v3.2.3 — Auracast TX Backend (very long-term placeholder)

RPi как BLE broadcast transmitter без паринга → любые Auracast-наушники.

```
[PA sink] → [AuracastTXBackend] → BLE Periodic Advertising + LC3 broadcast
                                → [Auracast receiver 1]
                                → [Auracast receiver N]
```

Требует: BlueZ 5.84+ BASS, liblc3, Python BLE bindings. Architectural placeholder, ETA не определён.

---

## Phase 4: Scale & Multi-Bridge Federation — v3.3.x

**Цель:** десятки плееров на bridge, десятки bridge'ей на MA-сервер.

### Bridge Discovery (mDNS)

```
_sendspin-bridge._tcp.local
    → name: "Living Room Bridge"
    → version: "3.3.0"
    → player_count: 8
    → api_port: 8080
```

MA и другие bridge'и видят друг друга без ручной настройки.

### Cross-Bridge Groups

MA sync groups работают поверх player_id с bridge-префиксом:

```
bridge-living-room::bt-eneby20
bridge-bedroom::local-hdmi
bridge-kitchen::bt-jbl
```

Синхронизация через MA — не требует межбриджевой координации на уровне bridge.

### Central Bridge Dashboard

Hub mode: агрегированный статус всех bridge'ей через mDNS discovery,
единый SSE поток, `/api/federation/bridges`.

### Sync Quality Telemetry

```python
@dataclass
class SyncTelemetry:
    player_id: str
    measured_delay_ms: float    # реально измеренная задержка
    target_delay_ms: float      # static_delay_ms из конфига
    drift_ms: float             # разница
    reanchor_count_1h: int
    sync_quality: Literal["good", "degraded", "poor"]
```

Авто-тюнинг `static_delay_ms`: если drift стабильно > 20ms в течение 10 минут →
предложить корректировку через UI/API.

---

## Phase 5: HA + AI Automation — v3.4.x

### 5A — HA Custom Component (HACS, монорепо)

Расположение: `custom_components/sendspin_audio_bridge/`

```yaml
# HA entities per bridge player:
media_player.sendspin_living_room:
  state: playing
  attributes:
    source: Music Assistant
    sync_drift_ms: 3.2
    backend_type: bluetooth_a2dp
    bt_battery: 78

sensor.sendspin_living_room_connection:
  state: connected
  attributes: { rssi: -62, sink: "bluez_sink.FC_58..." }

button.sendspin_living_room_reconnect: ~
button.sendspin_living_room_calibrate: ~
```

HA triggers:
- `sendspin_audio_bridge.device_connected`
- `sendspin_audio_bridge.device_disconnected`
- `sendspin_audio_bridge.sync_degraded`

Dashboard cards для статуса bridge в Lovelace.

### 5B — Presence-Based Zone Management

```json
"zone_management": {
  "enabled": true,
  "zones": [
    {
      "ha_area": "kitchen",
      "players": ["kitchen-local", "kitchen-bt"],
      "auto_enable_when_occupied": true,
      "idle_subprocess_when_empty": true,
      "idle_delay_seconds": 60
    }
  ]
}
```

- HA person/device_tracker → bridge zone state via webhook
- Occupied: subprocess warm, плеер доступен в MA
- Empty: idle timeout ускорен до 60s, subprocess выходит

### 5C — Auto-Delay Calibration

```
1. Bridge воспроизводит reference tone через player (1kHz, 100ms)
2. ESPHome-микрофон (INMP441) в той же комнате детектирует тон
3. Bridge получает timestamp детекции через HA entity или webhook
4. Вычисляет roundtrip → вычитает известные задержки → static_delay_ms
5. Предлагает применить через UI
```

```http
POST /api/calibrate/start
{"player_id": "living-room-bt", "mic_entity": "sensor.kitchen_mic_trigger"}
→ {"job_id": "cal-001"}

GET /api/calibrate/result/cal-001
→ {"measured_delay_ms": 187, "current_static_ms": -600, "suggested_ms": -413}
```

### 5D — LLM-Driven Playback Control

Bridge как слой между HA Assist и MA:

```
"Включи что-нибудь джазовое в кухне"
    ↓ HA Assist (intent extraction)
    ↓ POST /api/ma/intent {"text": "...", "context": {"area": "kitchen"}}
    ↓ Bridge: resolve area → MA group → MA play_media
    ↓ MA: поиск + воспроизведение
```

Интеграция с HA Conversation agent (LLM tool call или простая heuristics).

### 5E — Adaptive Quality

```python
class AdaptiveQualityManager:
    async def tick(self, player: Player):
        t = get_sync_telemetry(player.id)
        if t.reanchor_count_1h > 5:
            await update_pulse_latency(player, current + 100)
        if t.drift_ms > 30:
            await force_codec(player, "sbc")
        elif t.sync_quality == "good" and current_codec == "sbc":
            await try_codec(player, "aac")
```

---

## Phase 6: Ecosystem & Platform — v3.5.x+

### HA Custom Component — полная зрелость

- HACS listed
- HA Brand в brands.home-assistant.io
- Config flow с UI-wizard
- Translations (EN/RU минимум)
- Полное покрытие тестами (pytest-homeassistant-custom-component)

### Backend Plugin SDK

```python
# Третьи стороны добавляют backends без форка:
# pip install sendspin-audio-backend-roc
# pip install sendspin-audio-backend-dante

from sendspin_audio_bridge.backends import AudioBackend, register_backend

@register_backend("roc")
class ROCBackend(AudioBackend):
    ...
```

`AudioBackend` base class публикуется как отдельный PyPI пакет: `sendspin-audio-backend`.

### OpenHome OHRenderer

Bridge регистрируется как UPnP MediaRenderer с OpenHome services:
- OHPlaylist / OHVolume / OHTime / OHProduct
- Linn/Kazoo/Lumin приложения управляют bridge напрямую без MA
- Открытая спецификация openhome.org + `async-upnp-client` (уже в MA)

Целевая аудитория: нишевая audiophile. Ценность: credibility в OpenHome экосистеме и
режим работы без MA вообще.

### ESPHome Sendspin Component (совместная разработка)

Прямое подключение вместо ESPHome → HA → hass_players → MA (500ms+ latency):

```
[MA] → Sendspin protocol → [ESP32 ESPHome component] → I2S DAC → Speaker
```

Требует Sendspin C++ client library для ESP32. Совместная работа с MA/Sendspin командой.

---

## Версионная карта

| Версия | Фаза | Ключевое |
|--------|------|----------|
| **v3.0.0** | Rebrand & Foundation | Новое имя, config schema v2, AudioBackend ABC, migration tool |
| **v3.1.x** | Local Audio | LocalSink (PA/PW), ALSA direct, USB auto-discover, VirtualSink |
| **v3.2.x** | Network Backends | SnapcastClient, VBAN, LE Audio tracker, Auracast placeholder |
| **v3.3.x** | Scale & Federation | Multi-bridge mDNS, sync telemetry, cross-bridge groups, auto-tune |
| **v3.4.x** | HA + AI | HACS component, presence zones, auto-calibration, LLM intent, adaptive quality |
| **v3.5.x** | Platform | HACS mature, Plugin SDK, OpenHome OHRenderer, ESPHome component |

---

## Hardware Targets по фазам

| Фаза | Основное железо | Вторичное |
|------|----------------|-----------|
| v3.0 | x86 LXC (Proxmox), HAOS VM | — |
| v3.1 | x86 LXC с USB DAC / HDMI, RPi 4 | RPi Zero 2W |
| v3.2 | RPi Zero 2W (дешёвый network endpoint) | x86, ESP32 |
| v3.3 | Любое (federation агностична к железу) | — |
| v3.4 | HAOS VM + RPi с INMP441 mic (калибровка) | ESP32 с INMP441 |
| v3.5 | Все платформы | Linn/Naim hardware (OHRenderer) |

---

## Риски

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| LE Audio BlueZ Python bindings незрелые | Высокая | Guard за `experimental: true`, ETA 2027 |
| Sendspin protocol breaking change в v6 | Средняя | Versioned IPC contracts из текущего roadmap |
| Subprocess model не масштабируется | Средняя | Lazy spawn + idle timeout (Phase 2) |
| OpenHome нишевая аудитория | Высокая | После HACS mature, не вместо |
| ESPHome community не примет компонент | Средняя | Начать с RFC в ESPHome discussions |

---

## Принципы разработки

- **Backend-first:** новый тип устройства = новый backend, не изменение core
- **Incremental migration:** v2.x config мигрирует автоматически, не ломается
- **Subprocess-agnostic:** subprocess не знает какой backend его запустил
- **HA-native:** bridge — HA addon first, всё остальное поверх
- **No MA hardware coupling:** интеграция через `hass_players`, не upstream MA provider
- **Observable:** каждый backend публикует structured telemetry
- **Reuse over rewrite:** `pulsectl-asyncio`, `python-snapcast`, `async-upnp-client` уже доступны
