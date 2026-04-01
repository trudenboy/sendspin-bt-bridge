import { ref, onUnmounted } from 'vue'
import { useIngress } from './useIngress'

export interface UseSSEOptions {
  /** Max reconnect attempts before falling back to polling. */
  maxRetries?: number
  /** Polling interval in ms when SSE is unavailable. */
  pollInterval?: number
}

/**
 * Composable for SSE (Server-Sent Events) with auto-reconnect and polling fallback.
 *
 * Mirrors the current app.js SSE behavior: exponential backoff (1s → 16s),
 * max 5 retries, then fall back to polling every 2s.
 */
export function useSSE<T>(
  path: string,
  onMessage: (data: T) => void,
  options: UseSSEOptions = {},
) {
  const { apiBase } = useIngress()
  const { maxRetries = 5, pollInterval = 2000 } = options

  const connected = ref(false)
  const error = ref<string | null>(null)

  let es: EventSource | null = null
  let retries = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function connect() {
    cleanup()
    const url = `${apiBase}${path}`

    try {
      es = new EventSource(url)
    } catch {
      fallbackToPolling()
      return
    }

    es.onopen = () => {
      connected.value = true
      error.value = null
      retries = 0
    }

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as T
        onMessage(data)
      } catch (e) {
        error.value = `Parse error: ${e}`
      }
    }

    es.onerror = () => {
      connected.value = false
      es?.close()
      es = null

      if (retries < maxRetries) {
        const delay = Math.min(1000 * 2 ** retries, 16000)
        retries++
        reconnectTimer = setTimeout(connect, delay)
      } else {
        fallbackToPolling()
      }
    }
  }

  function fallbackToPolling() {
    error.value = 'SSE unavailable, using polling'
    pollTimer = setInterval(async () => {
      try {
        const resp = await fetch(`${apiBase}${path.replace('/stream', '')}`)
        if (resp.ok) {
          const data = (await resp.json()) as T
          onMessage(data)
          connected.value = true
        }
      } catch {
        connected.value = false
      }
    }, pollInterval)
  }

  function cleanup() {
    es?.close()
    es = null
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (pollTimer) clearInterval(pollTimer)
    reconnectTimer = null
    pollTimer = null
  }

  function disconnect() {
    cleanup()
    connected.value = false
  }

  connect()
  onUnmounted(disconnect)

  return { connected, error, reconnect: connect, disconnect }
}
