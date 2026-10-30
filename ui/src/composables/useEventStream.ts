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
  /** Reload the page (injectable for tests); defaults to ``location.reload``. */
  reload?: () => void
}

const RELOAD_KEY = 'sbb:event-stream-reload-at'
const RELOAD_GUARD_MS = 60_000
const BACKOFF_MIN_MS = 1000
const BACKOFF_MAX_MS = 30_000
/** Failed probes in a row, inside HA ingress, before the page reloads itself. */
const INGRESS_FAILURES_BEFORE_RELOAD = 5

/**
 * The bridge's one event stream (``/api/v1/events``, SSE).
 *
 * The server ends a stream after a while and EventSource reconnects on its
 * own while the connection merely drops. After a non-200 answer (an expired
 * HA ingress session: Supervisor 401/502/504) it goes CLOSED and never
 * retries, so then we poll at once and reconnect ourselves with backoff,
 * probing an authenticated endpoint first: a 401/403 means the session is
 * gone and one guarded page reload gets a fresh one.
 */
export function useEventStream(options: EventStreamOptions) {
  const connected = ref(false)
  const types = options.types ?? ['status', 'job']
  let source: EventSource | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let failures = 0
  let active = false
  let wasDown = false
  let attempts = 0
  let probeFailures = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

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

  function clearReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  /** One reload at most per RELOAD_GUARD_MS; false when it was skipped. */
  function guardedReload(): boolean {
    try {
      const last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0)
      if (Date.now() - last < RELOAD_GUARD_MS) return false
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
    } catch {
      return false
    }
    try {
      ;(options.reload ?? (() => window.location.reload()))()
    } catch {
      return false
    }
    return true
  }

  function embedded() {
    return window.location.pathname.includes('/hassio_ingress/')
  }

  /** ok / expired (the session is gone) / down (unreachable or gateway error). */
  async function probe(): Promise<'ok' | 'expired' | 'down'> {
    try {
      const res = await fetch(apiUrl('/api/v1/auth/session'), { credentials: 'same-origin', cache: 'no-store' })
      if (res.status === 401 || res.status === 403) return 'expired'
      return res.status >= 500 ? 'down' : 'ok'
    } catch {
      return 'down'
    }
  }

  function scheduleReconnect() {
    if (!active || reconnectTimer) return
    const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** attempts)
    attempts += 1
    reconnectTimer = setTimeout(() => void reconnectNow(false), base * (0.75 + Math.random() * 0.5))
  }

  async function reconnectNow(resetBackoff: boolean) {
    if (!active || connected.value) return
    clearReconnect()
    if (resetBackoff) attempts = 0
    const state = await probe()
    if (!active) return
    if (state === 'expired') {
      guardedReload()
    } else if (state === 'down') {
      probeFailures += 1
      if (embedded() && probeFailures >= INGRESS_FAILURES_BEFORE_RELOAD) guardedReload()
    } else {
      probeFailures = 0
    }
    if (state === 'ok') open()
    else scheduleReconnect()
  }

  function open() {
    close()
    source = new EventSource(apiUrl(`/api/v1/events?types=${types.join(',')}`), { withCredentials: true })
    source.onopen = () => {
      failures = 0
      attempts = 0
      probeFailures = 0
      clearReconnect()
      connected.value = true
      stopPolling()
      options.onConnect?.()
      if (wasDown) options.onPoll?.() // refresh once after the gap
      wasDown = false
    }
    source.onerror = () => {
      if (connected.value) options.onDisconnect?.()
      connected.value = false
      wasDown = true
      failures += 1
      if (source?.readyState === EventSource.CLOSED) {
        // Non-200 answer: the browser will not retry. Poll now, reconnect ourselves.
        close()
        startPolling()
        scheduleReconnect()
      } else if (failures >= 3) {
        startPolling()
      }
    }
    for (const type of types) source.addEventListener(type, handle as EventListener)
  }

  function onWake() {
    if (document.visibilityState === 'hidden') return
    if (active && !connected.value) void reconnectNow(true)
  }

  function connect() {
    if (!active) {
      active = true
      document.addEventListener('visibilitychange', onWake)
      window.addEventListener('online', onWake)
    }
    open()
  }

  function close() {
    source?.close()
    source = null
  }

  function disconnect() {
    active = false
    document.removeEventListener('visibilitychange', onWake)
    window.removeEventListener('online', onWake)
    clearReconnect()
    close()
    stopPolling()
    connected.value = false
  }

  return { connected, connect, disconnect }
}
