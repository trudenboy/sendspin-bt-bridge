# UX glossary (EN / RU)

One term per concept in the web UI, in both languages. The vitest lint
`ui/tests/unit/i18n/glossary.test.ts` fails when a retired term comes back
into `ui/src/i18n/en.json` or `ru.json`. Decision record: UI/UX overhaul Q8.

Add a new user-facing word here first, then use it. If two words name the
same thing, the one in the "Use" columns wins.

## Devices

| Concept | Use (EN) | Use (RU) | Short definition | Do not use |
|---|---|---|---|---|
| Bluetooth device the bridge plays to | Speaker | Колонка | A Bluetooth speaker or headset. | Device, sink, BT device (except while scanning for unpaired devices) |
| Wired or local device | Audio output | Аудиовыход | A sound output of the bridge computer (USB DAC, sound card, HDMI). | Sink, local output, ALSA/PulseAudio names in default text |
| Either of the two, as seen by Music Assistant | Player | Плеер | The Music Assistant player the bridge creates for a speaker or audio output. | Device, client, daemon (outside technical details) |

## Actions and states

| Concept | Use (EN) | Use (RU) | Short definition | Do not use |
|---|---|---|---|---|
| Bridge stops managing a speaker's Bluetooth so another device can use it | Release / Released | Освободить / Освобождена (menu: Освободить колонку) | Reversible; the speaker does not reconnect until reclaimed. | Hand over, Handed over, Let go, Give up, Передать, Передана, Отпустить, Отпущена |
| Undo of the above | Reclaim | Вернуть (menu: Вернуть колонку) | The bridge manages the speaker's Bluetooth again. | Take back, Claim, Забрать обратно, Забрать звук |
| Automatic release after reconnect loops | Released automatically | Освобождена автоматически | The bridge released the speaker by itself after repeated connection problems. | Auto-released, Handed over automatically, Передана автоматически, Отпущена автоматически |
| Make this bridge the active Sendspin source | Use this bridge as the source | Использовать этот мост как источник | Takes playback from another source for this player. | Claim audio, Make this bridge the active source, Забрать звук |
| Disconnect Bluetooth to save speaker battery | Standby | Режим ожидания (В режим ожидания) | Bluetooth disconnects; the player stays configured. | Sleep, Idle disconnect (as an action), Ожидание (alone) |
| Undo of standby | Wake | Разбудить | Reconnects a speaker on standby. | Wake from standby, Resume (that is for output), Вывести из ожидания |
| Turn a speaker or player off in the configuration | Disable / Enable | Выключить / Включить | A disabled speaker keeps its settings but gets no player. | Turn off, Deactivate, Отключить (that means Disconnect) |
| Bluetooth link ends | Disconnect | Отключить | Only the Bluetooth connection ends. | Disable (different thing) |
| Free the audio output while idle, Bluetooth stays connected | Power save (on / off) | Энергосбережение (вкл / выкл) | The power-save behaviour: the audio output is freed after a while without sound. | Pause output, Resume output, Suspend, Release audio, Пауза вывода, Поставить вывод на паузу, Приостановить, Отпускать звук |
| Switch a player between rooms | Room switch | Смена комнаты | Chip showing the player is ready to move to another room. | Transfer, Handoff, Передача |

## Timing and sync

| Concept | Use (EN) | Use (RU) | Short definition | Do not use |
|---|---|---|---|---|
| Per-speaker offset the bridge applies | Speaker latency compensation | Компенсация задержки колонки | How late this speaker plays; the bridge sends audio earlier by this much. Larger = earlier. Running text may say "delay" as shorthand for this value. | Speaker delay, Static delay, Статическая задержка, Задержка колонки (as a label) |
| What the speaker reports about itself over Bluetooth | Reported latency | Заявленная задержка | A2DP delay report; advisory, not measured. | Reported delay |
| Difference between this speaker and the group | Sync error | Ошибка синхронизации | Measured offset in ms. | Drift, Skew |
| Stream timing is re-established | Resync / Resyncing | Пересинхронизация | The stream timing was corrected after a glitch. | Re-anchor, Re-anchoring, Подстройка, Routing corrections |
| Feature that watches Bluetooth packets leaving the adapter | Air sensor | Датчик эфира | Experimental SBC-only measurement. | Air probe, Радиодатчик |
| Time from the audio leaving the air to sound | Air-to-sound latency | Задержка эфир → звук | A measurement result, per speaker, codec, rate and adapter. It does not change the compensation by itself. | Speaker latency (air → sound), Измеренная задержка колонки |
| Correction the loop applies from the air sensor | Air correction | Коррекция по эфиру | What playout sync adds from the air measurement. | Air bias as a name for the whole feature |
| One measurement path, three ways | Calibrate (the air sensor's stored result) / Tune by ear (adjust while listening) / Measure (record with a microphone) | Калибровка / Настроить на слух / Измерить | Calibrate = stored air result; Tune by ear = manual; Measure = microphone recording. | Mixing "calibrate" for by-ear tuning |

## Status names (basis for the one status model, Q1)

| Concept | Use (EN) | Use (RU) | Short definition |
|---|---|---|---|
| Sendspin stream between this player and Music Assistant | Audio link | Аудиоканал | Per speaker. |
| Sign-in of the bridge to the Music Assistant API | Music Assistant account | Аккаунт Music Assistant | Bridge-wide. |
| Bluetooth link between the bridge and the speaker | Bluetooth connection | Bluetooth-соединение | Per speaker. |

These three names are agreed here; the UI adopts them with the status model
work, not in this change.

## Allowed exceptions

- A software release: "Release notes", "Release candidate" (update dialog and
  `UPDATE_CHANNEL`). These keys are allowlisted in the lint.
- Technical-details text (API fields, log lines, diagnostics export) may name
  `re-anchor`, `claim` or `release` when it quotes a code identifier.
