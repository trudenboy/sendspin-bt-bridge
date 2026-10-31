import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { resumeSink, setDefaultSink, setSinkMute, setSinkVolume, suspendSink } from '@/api/audio'
import { DEFAULT_EVENT_TYPES, useEventStream, type EventType } from '@/composables/useEventStream'
import type { AudioSection, AudioEndpointSummary, HardwareSnapshot, SinkState } from '@/api/types'

/** The design's poll cadence for a bridge that cannot stream hardware events. */
export const HARDWARE_POLL_MS = 5000

/** An audio section that says nothing, for a bridge whose read has no audio half. */
export const EMPTY_AUDIO: AudioSection = { cards: [], sinks: [], sources: [] }

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
  /** The sink this session made default; the read model does not carry one. */
  const defaultSink = ref<string | null>(null)

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

  function apply(next: HardwareSnapshot) {
    audioReported.value = reportsAudio(next)
    snapshot.value = next
    error.value = null
    loading.value = false
    // A route that answers now is supported again.
    unsupported.value = false
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
      if (volumeSeq.get(name) === seq) patchSink(name, statePatch(state))
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
      if (muteSeq.get(name) === seq) patchSink(name, statePatch(state))
      return state
    } catch (e) {
      if (muteSeq.get(name) === seq) patchSink(name, { muted: before?.muted ?? !muted })
      throw e
    }
  }

  /** A write the read model cannot confirm: wait, then mark the sink this session chose. */
  async function setDefault(name: string): Promise<SinkState> {
    const state = await setDefaultSink(name)
    defaultSink.value = name
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

  function statePatch(state: SinkState): Partial<AudioEndpointSummary> {
    return { volume: state.volume, muted: state.muted, state: state.state }
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
    defaultSink,
    connect,
    disconnect,
    refresh,
    setVolume,
    setMute,
    setDefault,
    suspend,
    resume,
  }
})
