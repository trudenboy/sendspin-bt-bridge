import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { DEFAULT_EVENT_TYPES, useEventStream, type EventType } from '@/composables/useEventStream'
import type { HardwareSnapshot } from '@/api/types'

/** The design's poll cadence for a bridge that cannot stream hardware events. */
export const HARDWARE_POLL_MS = 5000

/**
 * The physical layer: one aggregate read, the 5 s poll behind it, and the
 * ``hardware`` event type probed per bridge.
 *
 * Events are opt-in on the server (an unknown type is a 400), so the stream
 * starts on the types every bridge knows. Only after the aggregate reads does
 * the store reconnect asking for ``hardware`` too; a bridge that answers
 * 404/405/5xx is never asked again and the page stays on the poll.
 */
export const useHardwareStore = defineStore('hardware', () => {
  const snapshot = ref<HardwareSnapshot | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const sseConnected = ref(false)
  const degraded = computed(() => snapshot.value?.degraded ?? [])

  /** The bridge said it does not know the ``hardware`` type: never mention it again. */
  let unsupported = false
  /** The current stream already carries hardware events: nothing left to probe. */
  let streaming = false
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function makeStream(withHardware: boolean) {
    const types: EventType[] = [...DEFAULT_EVENT_TYPES, 'hardware']
    return useEventStream({
      ...(withHardware ? { types } : {}),
      onEvent(event) {
        if (event.type === 'hardware') apply(event.data as HardwareSnapshot)
      },
      onConnect() {
        sseConnected.value = true
      },
      onDisconnect() {
        sseConnected.value = false
      },
    })
  }

  let stream = makeStream(false)

  function apply(next: HardwareSnapshot) {
    snapshot.value = next
    error.value = null
    loading.value = false
  }

  /** True when the answer means this bridge will never accept the type. */
  function givesUp(e: unknown) {
    if (!(e instanceof ApiError)) return false
    return e.status === 404 || e.status === 405 || e.status >= 500
  }

  /** Swap the default stream for one that also carries hardware events. */
  function enableEvents() {
    if (unsupported || streaming) return
    streaming = true
    stream.disconnect()
    stream = makeStream(true)
    stream.connect()
  }

  async function refresh() {
    if (!snapshot.value) loading.value = true
    try {
      apply(await getHardware())
      enableEvents()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      loading.value = false
      if (givesUp(e)) unsupported = true
    }
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
    stream.connect()
    startPolling()
    void refresh()
  }

  function disconnect() {
    stopPolling()
    stream.disconnect()
    sseConnected.value = false
  }

  return { snapshot, loading, error, degraded, sseConnected, connect, disconnect, refresh }
})
