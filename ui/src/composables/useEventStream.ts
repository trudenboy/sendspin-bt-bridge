import { ref } from 'vue'
import { apiUrl } from '@/api/client'
import type { EventEnvelope } from '@/api/types'

export type EventType = EventEnvelope['type']

export interface EventStreamOptions {
  types?: EventType[]
  onEvent: (event: EventEnvelope) => void
  onConnect?: () => void
  onDisconnect?: () => void
  /** Called while the stream is down, so the caller can poll instead. */
  onPoll?: () => void
  pollInterval?: number
}

/**
 * The bridge's one event stream (``/api/v1/events``, SSE).
 *
 * The server ends a stream after a while and EventSource reconnects on its
 * own; on repeated failures the caller is asked to poll until it is back.
 */
export function useEventStream(options: EventStreamOptions) {
  const connected = ref(false)
  const types = options.types ?? ['status', 'job']
  let source: EventSource | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let failures = 0

  function handle(raw: MessageEvent) {
    try {
      options.onEvent(JSON.parse(raw.data) as EventEnvelope)
    } catch {
      /* a malformed frame is skipped, the stream goes on */
    }
  }

  function startPolling() {
    if (pollTimer || !options.onPoll) return
    pollTimer = setInterval(() => options.onPoll?.(), options.pollInterval ?? 5000)
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  }

  function connect() {
    close()
    source = new EventSource(apiUrl(`/api/v1/events?types=${types.join(',')}`), { withCredentials: true })
    source.onopen = () => {
      failures = 0
      connected.value = true
      stopPolling()
      options.onConnect?.()
    }
    source.onerror = () => {
      if (connected.value) options.onDisconnect?.()
      connected.value = false
      failures += 1
      if (failures >= 3) startPolling()
    }
    for (const type of types) source.addEventListener(type, handle as EventListener)
  }

  function close() {
    source?.close()
    source = null
  }

  function disconnect() {
    close()
    stopPolling()
    connected.value = false
  }

  return { connected, connect, disconnect }
}
