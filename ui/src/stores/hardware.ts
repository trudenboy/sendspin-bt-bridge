import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import {
  resumeSink,
  setCardProfile,
  setDefaultSink,
  setSinkMute,
  setSinkPort,
  setSinkVolume,
  suspendSink,
} from '@/api/audio'
import {
  closeSourcePairing,
  openSourcePairing,
  setSourceEnabled,
  setSourcePlayer as setSourcePlayerRequest,
  SOURCE_PAIRING_WINDOW_S,
  type SourcePairingWindow,
} from '@/api/source'
import { DEFAULT_EVENT_TYPES, useEventStream, type EventType } from '@/composables/useEventStream'
import type {
  AudioCardDetail,
  AudioCardSummary,
  AudioSection,
  AudioEndpointDetail,
  AudioEndpointSummary,
  HardwareSnapshot,
  SinkState,
  SourceFeed,
} from '@/api/types'

/** The design's poll cadence for a bridge that cannot stream hardware events. */
export const HARDWARE_POLL_MS = 5000

/** An audio section that says nothing, for a bridge whose read has no audio half. */
export const EMPTY_AUDIO: AudioSection = { cards: [], sinks: [], sources: [] }

/** A failed source command: the server's code, its message, and the speaker it concerned. */
export interface SourceFailure {
  code: string
  detail: string
  /** The player a relay command was about; the server's sink refusal names it. */
  player: string | null
}

/**
 * The physical layer: one aggregate read, the 5 s poll behind it, and the
 * ``hardware`` event type probed per bridge.
 *
 * Events are opt-in on the server (an unknown type is a 400), so the store
 * starts on the types every bridge knows and asks for ``hardware`` on a second
 * stream. Only when that stream opens does it retire the status/job probe; a
 * refusal closes the typed stream alone, leaves the probe in place, and the
 * page stays on the poll. A bridge that answers 404/405/5xx to the aggregate is
 * never asked for the type at all.
 */
export const useHardwareStore = defineStore('hardware', () => {
  const snapshot = ref<HardwareSnapshot | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** A manual Refresh is in flight; the poll must not toggle this, or the button flickers. */
  const refreshing = ref(false)
  const degraded = computed(() => snapshot.value?.degraded ?? [])

  /**
   * The audio half, normalised so callers never branch on undefined. An older
   * bridge (before E2) omits the field; ``audioReported`` is what tells the page
   * to show the "this bridge cannot report audio" story rather than an empty
   * list — an absent field is not a host without audio.
   */
  const audio = computed<AudioSection>(() => snapshot.value?.audio ?? EMPTY_AUDIO)
  const audioReported = ref(false)
  /**
   * The source strip's model: ``null`` means this bridge does not report a
   * source (the feature is off), which is a different story from a stopped one.
   */
  const source = computed<SourceFeed | null>(() => snapshot.value?.source ?? null)
  /** The last source command's failure, with the server's stable code kept intact. */
  const sourceError = ref<SourceFailure | null>(null)
  /** A source command is in flight: the strip's buttons rest while it runs. */
  const sourceBusy = ref(false)

  /** A phone pairing window, counted down from the server's own answer. */
  const pairingEndsAt = ref<number | null>(null)
  const pairingNow = ref(Date.now())
  const pairingSecondsLeft = computed(() => {
    // ``pairingNow`` is the tick: Date.now() alone would not make this reactive.
    pairingNow.value
    if (pairingEndsAt.value === null) return 0
    return Math.max(0, Math.ceil((pairingEndsAt.value - Date.now()) / 1000))
  })
  let pairingTimer: ReturnType<typeof setInterval> | null = null

  /** The API has no `/hardware` route: the page shows its unsupported screen. */
  const unsupported = ref(false)
  /** A 404/405/5xx probe means the event type will never be accepted. */
  let eventsUnsupported = false
  /** A typed stream is already open (or opening): nothing left to probe. */
  let streaming = false
  let pollTimer: ReturnType<typeof setInterval> | null = null
  /** The page was left: a read that resolves later must not start a stream or listeners. */
  let disposed = false
  let manualInFlight = 0

  let hardwareStream: ReturnType<typeof useEventStream> | null = null

  function makeStream(withHardware: boolean) {
    const types: EventType[] = [...DEFAULT_EVENT_TYPES, 'hardware']
    return useEventStream({
      ...(withHardware ? { types } : {}),
      onEvent(event) {
        if (event.type === 'hardware') apply(event.data as HardwareSnapshot)
      },
      ...(withHardware
        ? {
            onConnect() {
              // Proven to open: the typed stream now replaces the status/job probe.
              stream.disconnect()
            },
            onRejected() {
              // An old bridge refuses the type. The status/job stream was never
              // touched; close the refused one and keep polling hardware.
              eventsUnsupported = true
              streaming = false
              hardwareStream?.disconnect()
              hardwareStream = null
            },
          }
        : {}),
    })
  }

  let stream = makeStream(false)

  /** A field can only be absent on an older bridge: the generated type says it is required. */
  function reportsAudio(next: HardwareSnapshot) {
    return Object.prototype.hasOwnProperty.call(next, 'audio') && next.audio != null
  }

  /** ISO-8601 read times; a read taken before the current one must never overwrite it. */
  function isNewerRead(next: HardwareSnapshot, current: HardwareSnapshot) {
    const nextAt = Date.parse(next.as_of)
    const currentAt = Date.parse(current.as_of)
    if (!Number.isFinite(nextAt)) return false
    if (!Number.isFinite(currentAt)) return true
    return nextAt > currentAt
  }

  /** A feed that answered without an error: a command refusal about it no longer applies. */
  function sourceHealthy(next: HardwareSnapshot) {
    if (!next.source) return false
    return next.source.state !== 'error' && !next.source.error
  }

  function apply(next: HardwareSnapshot) {
    // SSE, the poll and a manual refresh race: the read's own timestamp decides.
    if (snapshot.value && !isNewerRead(next, snapshot.value)) return
    audioReported.value = reportsAudio(next)
    snapshot.value = next
    error.value = null
    loading.value = false
    // A route that answers now is supported again.
    unsupported.value = false
    if (sourceHealthy(next)) sourceError.value = null
  }

  /** True when the answer means this bridge will never accept the type. */
  function givesUp(e: unknown) {
    if (!(e instanceof ApiError)) return false
    return e.status === 404 || e.status === 405 || e.status >= 500
  }

  /** The route itself is absent: the page has an "unsupported bridge" screen for that. */
  function missingRoute(e: unknown) {
    if (!(e instanceof ApiError)) return false
    return e.status === 404 || e.status === 405
  }

  /** Open the typed stream in addition to the status/job one, never instead of it. */
  function enableEvents() {
    if (disposed || eventsUnsupported || streaming) return
    streaming = true
    hardwareStream = makeStream(true)
    hardwareStream.connect()
  }

  async function refresh(options: { manual?: boolean } = {}) {
    if (disposed) return
    const manual = options.manual === true
    if (manual) {
      manualInFlight += 1
      refreshing.value = true
    }
    if (!snapshot.value) loading.value = true
    try {
      const next = await getHardware()
      if (disposed) return
      apply(next)
      enableEvents()
    } catch (e) {
      if (disposed) return
      error.value = e instanceof Error ? e.message : String(e)
      loading.value = false
      if (givesUp(e)) eventsUnsupported = true
      if (missingRoute(e)) unsupported.value = true
    } finally {
      if (manual) {
        manualInFlight -= 1
        if (manualInFlight === 0) refreshing.value = false
      }
    }
  }

  // -------------------------------------------------------------------------
  // Audio commands
  // -------------------------------------------------------------------------

  function sink(name: string) {
    return audio.value.sinks.find((entry) => entry.name === name) ?? null
  }

  /** Replace one sink in the snapshot, keeping everything else as the last read had it. */
  function patchSink(name: string, patch: Partial<AudioEndpointSummary>) {
    const current = snapshot.value
    if (!current) return
    const section = current.audio ?? EMPTY_AUDIO
    snapshot.value = {
      ...current,
      audio: {
        ...section,
        sinks: section.sinks.map((entry) => (entry.name === name ? { ...entry, ...patch } : entry)),
      },
    }
  }

  /** Two sliders racing must not let an older answer overwrite a newer level. */
  const volumeSeq = new Map<string, number>()
  const muteSeq = new Map<string, number>()

  /** Optimistic: the slider moves at once; a refusal rolls the level back and rethrows with the server's code. */
  async function setVolume(name: string, volume: number): Promise<SinkState> {
    const before = sink(name)
    const seq = (volumeSeq.get(name) ?? 0) + 1
    volumeSeq.set(name, seq)
    patchSink(name, { volume })
    try {
      const state = await setSinkVolume(name, volume)
      // A volume response was read before the write: it owns the level, not the mute flag.
      if (volumeSeq.get(name) === seq) patchSink(name, { volume: state.volume })
      return state
    } catch (e) {
      if (volumeSeq.get(name) === seq) patchSink(name, { volume: before?.volume ?? volume })
      throw e
    }
  }

  /** Optimistic for the same reason as the volume slider; a refusal puts the flag back. */
  async function setMute(name: string, muted: boolean): Promise<SinkState> {
    const before = sink(name)
    const seq = (muteSeq.get(name) ?? 0) + 1
    muteSeq.set(name, seq)
    patchSink(name, { muted })
    try {
      const state = await setSinkMute(name, muted)
      // The mute response carries the level from before a concurrent slider move.
      if (muteSeq.get(name) === seq) patchSink(name, { muted: state.muted })
      return state
    } catch (e) {
      if (muteSeq.get(name) === seq) patchSink(name, { muted: before?.muted ?? !muted })
      throw e
    }
  }

  /** The write is the server's; the refreshed read model carries ``is_default`` to the page. */
  async function setDefault(name: string): Promise<SinkState> {
    const state = await setDefaultSink(name)
    await refresh()
    return state
  }

  /** Silencing an output is disruptive, so the caller asks first; the state patch is the server's answer. */
  async function suspend(name: string): Promise<SinkState> {
    const state = await suspendSink(name)
    patchSink(name, statePatch(state))
    return state
  }

  /** The server reads the sink before resuming it, so its own state can still say "suspended" here. */
  async function resume(name: string): Promise<SinkState> {
    const state = await resumeSink(name)
    patchSink(name, statePatch(state))
    await refresh()
    return state
  }

  /** Suspend/resume own the endpoint's state; the level and mute flag come from other commands. */
  function statePatch(state: SinkState): Partial<AudioEndpointSummary> {
    return { state: state.state }
  }

  /** Replace one card in the snapshot with the write's own answer, keeping everything else. */
  function replaceCard(name: string, detail: AudioCardDetail) {
    const current = snapshot.value
    if (!current) return
    const section = current.audio ?? EMPTY_AUDIO
    // The aggregate carries summaries. The detail's extra lists belong to the
    // drawer that asked for the write, not to a page card that never read them.
    const summary: AudioCardSummary = {
      name: detail.name,
      driver: detail.driver,
      active_profile: detail.active_profile,
      profile_count: detail.profile_count,
      port_count: detail.port_count,
      endpoint_refs: detail.endpoint_refs,
      managed_by: detail.managed_by,
    }
    snapshot.value = {
      ...current,
      audio: {
        ...section,
        cards: section.cards.map((entry) => (entry.name === name ? summary : entry)),
      },
    }
  }

  /** Replace one endpoint in the snapshot; the detail is a superset of the row's summary. */
  function replaceEndpoint(name: string, detail: AudioEndpointDetail) {
    const current = snapshot.value
    if (!current) return
    const section = current.audio ?? EMPTY_AUDIO
    const summary: AudioEndpointSummary = detail
    snapshot.value = {
      ...current,
      audio: {
        ...section,
        sinks: section.sinks.map((entry) => (entry.name === name ? summary : entry)),
      },
    }
  }

  /**
   * A write's detail is a read newer than the aggregate it lands in. Move the
   * snapshot's clock with it, or a poll started before the write can still
   * satisfy ``isNewerRead`` and flip the row back to the old port. A stale
   * detail is a pre-write read and must never move the clock; a read taken
   * after the write is already newer and wins on its own.
   */
  function adoptReadTime(detail: { as_of?: string; stale?: boolean }) {
    const current = snapshot.value
    if (!current || detail.stale) return
    const at = Date.parse(detail.as_of ?? '')
    if (!Number.isFinite(at)) return
    const currentAt = Date.parse(current.as_of)
    if (Number.isFinite(currentAt) && at <= currentAt) return
    snapshot.value = { ...current, as_of: detail.as_of as string }
  }

  /**
   * A profile switch answers with the updated card, so the drawer shows the new
   * profile at once. A profile can also add or remove outputs, and the in-place
   * replace touches the card list only, so the aggregate is read again after
   * the write; the refreshed rows then carry the same read as the detail.
   */
  async function chooseCardProfile(
    card: AudioCardSummary | AudioCardDetail,
    profile: string,
  ): Promise<AudioCardDetail | null> {
    const detail = await setCardProfile(card.name, profile)
    if (!detail) {
      // No detail means no answer to apply: fall back to the aggregate read.
      await refresh()
      return null
    }
    replaceCard(card.name, detail)
    await refresh()
    adoptReadTime(detail)
    return detail
  }

  /**
   * The port counterpart of a profile switch: replace the sink with the detail
   * the write returned. Ports belong to the sink's existing card, so the audio
   * section cannot gain or lose rows here.
   */
  async function chooseSinkPort(
    sink: AudioEndpointSummary | AudioEndpointDetail,
    port: string,
  ): Promise<AudioEndpointDetail | null> {
    const detail = await setSinkPort(sink.name, port)
    if (!detail) {
      await refresh()
      return null
    }
    replaceEndpoint(sink.name, detail)
    adoptReadTime(detail)
    return detail
  }

  // -------------------------------------------------------------------------
  // Source commands
  // -------------------------------------------------------------------------

  function failure(e: unknown, player: string | null = null): SourceFailure {
    if (e instanceof ApiError) return { code: e.code, detail: e.message, player }
    return { code: 'request_failed', detail: e instanceof Error ? e.message : String(e), player }
  }

  /** Every command clears the previous refusal first, so the strip never shows a stale one. */
  async function command<T>(run: () => Promise<T>): Promise<T> {
    sourceError.value = null
    sourceBusy.value = true
    try {
      return await run()
    } finally {
      sourceBusy.value = false
    }
  }

  function stopPairingTicker() {
    if (pairingTimer) clearInterval(pairingTimer)
    pairingTimer = null
  }

  function setPairingWindow(seconds: number) {
    pairingNow.value = Date.now()
    if (seconds <= 0) {
      pairingEndsAt.value = null
      stopPairingTicker()
      return
    }
    pairingEndsAt.value = Date.now() + seconds * 1000
    stopPairingTicker()
    // Four ticks a second: the shown second never lags by a whole second.
    pairingTimer = setInterval(() => {
      pairingNow.value = Date.now()
      if (pairingSecondsLeft.value <= 0) {
        pairingEndsAt.value = null
        stopPairingTicker()
      }
    }, 250)
  }

  function clearPairingWindow() {
    pairingEndsAt.value = null
    pairingNow.value = Date.now()
    stopPairingTicker()
  }

  // The status stream already carries the window's remaining seconds. The view
  // hands it over, so a page reloaded mid-window picks the countdown up again
  // instead of offering to open a second one. Adopted only when it is longer
  // than what we already count, so a lagging status event cannot shorten a
  // window a command has just opened.
  function adoptPairingWindow(seconds: number) {
    if (seconds > pairingSecondsLeft.value) setPairingWindow(seconds)
  }

  async function startSource() {
    return command(async () => {
      try {
        await setSourceEnabled(true)
        await refresh()
      } catch (e) {
        sourceError.value = failure(e)
        throw e
      }
    })
  }

  async function stopSource() {
    return command(async () => {
      try {
        await setSourceEnabled(false)
        // The source stops its pairing agent too; a local countdown would lie.
        clearPairingWindow()
        await refresh()
      } catch (e) {
        sourceError.value = failure(e)
        throw e
      }
    })
  }

  async function setSourcePlayer(playerId: string, enabled: boolean) {
    return command(async () => {
      try {
        const answer = await setSourcePlayerRequest(playerId, enabled)
        await refresh()
        return answer
      } catch (e) {
        // ``source_sink_missing`` is about one speaker: keep it in the refusal.
        sourceError.value = failure(e, playerId)
        throw e
      }
    })
  }

  async function startPairingWindow(seconds = SOURCE_PAIRING_WINDOW_S) {
    // A new attempt replaces any leftover countdown, however the previous one ended.
    clearPairingWindow()
    return command(async () => {
      try {
        const window: SourcePairingWindow = await openSourcePairing(seconds)
        const left = Number(window?.pairing_seconds_left)
        setPairingWindow(Number.isFinite(left) ? left : seconds)
        // Read after the window is set: a healthy feed clears the previous refusal,
        // then this window's own answer lands (and stays until the next read).
        await refresh()
        if (window?.error) {
          // A 200 that names the refusal is a failed command, not an open window:
          // the caller must not offer Undo for a window that never existed.
          sourceError.value = { code: window.error, detail: '', player: null }
        }
        return window
      } catch (e) {
        sourceError.value = failure(e)
        throw e
      }
    })
  }

  async function stopPairingWindow() {
    return command(async () => {
      try {
        await closeSourcePairing()
        clearPairingWindow()
        await refresh()
      } catch (e) {
        sourceError.value = failure(e)
        throw e
      }
    })
  }

  function clearSourceError() {
    sourceError.value = null
  }

  function startPolling() {
    if (pollTimer) return
    pollTimer = setInterval(() => void refresh(), HARDWARE_POLL_MS)
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  }

  function connect() {
    disposed = false
    stream.connect()
    startPolling()
    void refresh()
  }

  function disconnect() {
    disposed = true
    stopPolling()
    // A countdown belongs to the page visit: a returned page must be able to pair again.
    clearPairingWindow()
    clearSourceError()
    stream.disconnect()
    hardwareStream?.disconnect()
    hardwareStream = null
    streaming = false
    refreshing.value = false
    manualInFlight = 0
  }

  return {
    snapshot,
    loading,
    refreshing,
    error,
    degraded,
    unsupported,
    audio,
    audioReported,
    source,
    sourceError,
    sourceBusy,
    pairingSecondsLeft,
    connect,
    disconnect,
    refresh,
    setVolume,
    setMute,
    setDefault,
    suspend,
    resume,
    chooseCardProfile,
    chooseSinkPort,
    startSource,
    stopSource,
    setSourcePlayer,
    startPairingWindow,
    adoptPairingWindow,
    stopPairingWindow,
    clearSourceError,
  }
})
