import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  SOURCE_PAIRING_WINDOW_S,
  cancelMaPairing as apiForgetMaPairing,
  closeSourcePairing,
  forgetSourceDevice as apiForgetSourceDevice,
  openMaPairing as apiOpenMaPairing,
  openSourcePairing,
  selectSource,
  setSourceEnabled,
  useSourceDevice as apiUseSourceDevice,
  type MaPairingWindow,
  type SourcePairingWindow,
} from '@/api/source'
import type { BridgeStatus, SendspinSourceStatus } from '@/api/types'
import { useBridgeStore } from './bridge'

/** A failed source command: the server's stable code and its sentence. */
export interface SourceFailure {
  code: string
  detail: string
}

/** One serialized command attempt. Its generation decides whether an answer still counts. */
interface Attempt {
  id: number
}

/**
 * The single owner of the Source feed in the UI: its snapshot, its commands and
 * the two pairing windows' clocks.
 *
 * The snapshot is the bridge status's own ``sendspin_source`` block, forwarded
 * by ``useBridgeStore`` — this store never opens an SSE connection and never
 * fetches a status of its own. The hardware snapshot keeps the feed as read
 * data for the Hardware page; it owns nothing here.
 *
 * Commands are queued, so a Cancel clicked while Open is still in flight runs
 * after it and cannot be undone by the open's late answer. A status event that
 * lands while a command is in flight is buffered, and once the queue drains the
 * store reconciles with a fresh status read: the server's answer, not a stale
 * read, owns the window.
 */
export const useSourceStore = defineStore('source', () => {
  /** The bridge's word on the feed; null when it reports none. */
  const status = ref<SendspinSourceStatus | null>(null)
  /** A source command is running or queued; buttons rest while it does. */
  const busy = ref(false)
  /** The latest attempt's failure, with the server's stable code. A status read never clears it. */
  const error = ref<SourceFailure | null>(null)

  /** The phone pairing deadline, and the Music Assistant one; null = no window. */
  const pairingEndsAt = ref<number | null>(null)
  const maPairingEndsAt = ref<number | null>(null)
  /** The eight digits shown while Music Assistant's window is open. */
  const maPairingCode = ref<string | null>(null)
  /** The adapters the phone's window made discoverable. */
  const pairingAdapters = ref<string[]>([])
  /** The tick that makes both countdowns advance. */
  const pairingNow = ref(Date.now())
  /** The server has confirmed this window open at least once, so its zero means "closed". */
  let serverPairingSeen = false
  let serverMaPairingSeen = false

  const pairingSecondsLeft = computed(() => {
    // ``pairingNow`` is the tick: Date.now() alone would not make this reactive.
    pairingNow.value
    return secondsLeft(pairingEndsAt.value)
  })
  const maPairingSecondsLeft = computed(() => {
    pairingNow.value
    return secondsLeft(maPairingEndsAt.value)
  })

  function secondsLeft(endsAt: number | null) {
    if (endsAt === null) return 0
    return Math.max(0, Math.ceil((endsAt - Date.now()) / 1000))
  }

  // -------------------------------------------------------------------------
  // One ticker for both windows
  // -------------------------------------------------------------------------

  let ticker: ReturnType<typeof setInterval> | null = null

  function windowOpen() {
    return pairingEndsAt.value !== null || maPairingEndsAt.value !== null
  }

  function stopTicker() {
    if (ticker) clearInterval(ticker)
    ticker = null
  }

  function syncTicker() {
    if (!windowOpen()) {
      stopTicker()
      return
    }
    pairingNow.value = Date.now()
    // Four ticks a second: the shown second never lags by a whole second.
    if (ticker === null) ticker = setInterval(tick, 250)
  }

  function tick() {
    pairingNow.value = Date.now()
    if (pairingEndsAt.value !== null && secondsLeft(pairingEndsAt.value) <= 0) {
      pairingEndsAt.value = null
      pairingAdapters.value = []
      serverPairingSeen = false
    }
    if (maPairingEndsAt.value !== null && secondsLeft(maPairingEndsAt.value) <= 0) {
      maPairingEndsAt.value = null
      maPairingCode.value = null
      serverMaPairingSeen = false
    }
    if (!windowOpen()) stopTicker()
  }

  function setPairingDeadline(endsAt: number | null) {
    pairingEndsAt.value = endsAt
    pairingNow.value = Date.now()
    syncTicker()
  }

  function setMaPairingDeadline(endsAt: number | null) {
    maPairingEndsAt.value = endsAt
    pairingNow.value = Date.now()
    syncTicker()
  }

  function closePairingClock() {
    serverPairingSeen = false
    pairingEndsAt.value = null
    pairingAdapters.value = []
    pairingNow.value = Date.now()
    syncTicker()
  }

  function closeMaPairingClock() {
    serverMaPairingSeen = false
    maPairingCode.value = null
    maPairingEndsAt.value = null
    pairingNow.value = Date.now()
    syncTicker()
  }

  /**
   * Adopt the server's remaining seconds without ever moving a deadline later:
   * a page mounting again re-reads the same status, and re-adopting it must not
   * push the window into the future.
   */
  function adoptPairingSeconds(seconds: number) {
    if (seconds > 0) {
      serverPairingSeen = true
      const candidate = Date.now() + seconds * 1000
      const current = pairingEndsAt.value
      setPairingDeadline(current === null ? candidate : Math.min(current, candidate))
      return
    }
    if (!serverPairingSeen) return
    closePairingClock()
  }

  function adoptMaPairingSeconds(seconds: number) {
    if (seconds > 0) {
      serverMaPairingSeen = true
      const candidate = Date.now() + seconds * 1000
      const current = maPairingEndsAt.value
      setMaPairingDeadline(current === null ? candidate : Math.min(current, candidate))
      return
    }
    if (!serverMaPairingSeen) return
    closeMaPairingClock()
  }

  function applySource(next: SendspinSourceStatus | null) {
    status.value = next
    if (!next) return
    if (next.ma_pairing_code) maPairingCode.value = next.ma_pairing_code
    pairingAdapters.value = Array.isArray(next.pairing_adapters) ? next.pairing_adapters : []
    adoptPairingSeconds(Number(next.pairing_seconds_left) || 0)
    adoptMaPairingSeconds(Number(next.ma_pairing_seconds_left) || 0)
  }

  // -------------------------------------------------------------------------
  // Status input
  // -------------------------------------------------------------------------

  /** A status that arrived while a command was queued or running, applied only if the fresh read fails. */
  let buffered: SendspinSourceStatus | null | undefined
  /** The fresh read after a command is itself a status application, not an intermediate one. */
  let reconciling = false

  function applyStatus(next: BridgeStatus | null | undefined) {
    const source = next?.bridge?.sendspin_source ?? null
    if (busy.value && !reconciling) {
      buffered = source
      return
    }
    applySource(source)
  }

  async function reconcile() {
    if (pending > 0) return
    const stale = buffered
    buffered = undefined
    reconciling = true
    let fresh = false
    try {
      fresh = await useBridgeStore().refresh()
    } finally {
      reconciling = false
    }
    // The bridge did not answer: keep the last status seen while the command ran.
    if (!fresh && stale !== undefined) applySource(stale)
  }

  // -------------------------------------------------------------------------
  // Command queue
  // -------------------------------------------------------------------------

  let generation = 0
  let pending = 0
  let chain: Promise<unknown> = Promise.resolve()
  /** The one in-flight attempt per action: a double click is the same command, not two. */
  const inflight = new Map<string, Promise<unknown>>()

  function failure(e: unknown): SourceFailure {
    if (e instanceof ApiError) return { code: e.code, detail: e.message }
    return { code: 'request_failed', detail: e instanceof Error ? e.message : String(e) }
  }

  function enqueue<T>(key: string, run: (attempt: Attempt) => Promise<T>): Promise<T> {
    const running = inflight.get(key)
    if (running) return running as Promise<T>
    const attempt: Attempt = { id: ++generation }
    error.value = null
    pending += 1
    busy.value = true
    const result = chain
      .catch(() => undefined)
      .then(async () => {
        try {
          return await run(attempt)
        } catch (e) {
          // A late failure from a cancelled attempt must not replace the newer
          // attempt's answer, nor its error.
          if (attempt.id === generation) error.value = failure(e)
          throw e
        } finally {
          pending -= 1
          if (pending === 0) await reconcile()
          busy.value = pending > 0
        }
      })
    chain = result
    inflight.set(key, result)
    const done = () => {
      if (inflight.get(key) === result) inflight.delete(key)
    }
    void result.then(done, done)
    return result
  }

  // -------------------------------------------------------------------------
  // Commands
  // -------------------------------------------------------------------------

  function enable() {
    return enqueue('source.enable', async () => {
      await setSourceEnabled(true)
    })
  }

  function disable() {
    return enqueue('source.disable', async () => {
      await setSourceEnabled(false)
      // Stopping the feed ends both pairing windows on the server as well; a
      // local countdown that kept running would offer a window that is gone.
      closePairingClock()
      closeMaPairingClock()
    })
  }

  function useDevice(mac: string) {
    return enqueue(`source.use:${mac}`, async () => {
      await apiUseSourceDevice(mac)
    })
  }

  function forgetDevice(mac: string) {
    return enqueue(`source.forget:${mac}`, async () => {
      await apiForgetSourceDevice(mac)
    })
  }

  function selectInput(sourceId: string) {
    return enqueue(`source.select:${sourceId}`, async () => {
      await selectSource(sourceId)
    })
  }

  function openPairingWindow(seconds = SOURCE_PAIRING_WINDOW_S): Promise<SourcePairingWindow> {
    return enqueue('pairing.open', async (attempt) => {
      // A new attempt replaces whatever countdown the previous one left.
      closePairingClock()
      const answer = await openSourcePairing(seconds)
      if (attempt.id === generation) {
        const left = Number(answer?.pairing_seconds_left)
        if (answer?.error) {
          // A 200 that names the refusal is a failed command, not an open window.
          error.value = { code: answer.error, detail: '' }
          closePairingClock()
        } else if (Number.isFinite(left) && left > 0) {
          serverPairingSeen = true
          setPairingDeadline(Date.now() + left * 1000)
          pairingAdapters.value = answer?.pairing_adapters ?? []
        } else {
          closePairingClock()
        }
      }
      return answer
    })
  }

  function closePairingWindow() {
    return enqueue('pairing.close', async () => {
      await closeSourcePairing()
      closePairingClock()
    })
  }

  function openMaPairing(): Promise<MaPairingWindow> {
    return enqueue('ma.open', async (attempt) => {
      closeMaPairingClock()
      const answer = await apiOpenMaPairing()
      if (attempt.id === generation) {
        maPairingCode.value = answer?.code ?? null
        const left = Number(answer?.seconds_left)
        if (Number.isFinite(left) && left > 0) {
          serverMaPairingSeen = true
          setMaPairingDeadline(Date.now() + left * 1000)
        } else {
          closeMaPairingClock()
        }
      }
      return answer
    })
  }

  function forgetMaPairing() {
    return enqueue('ma.forget', async () => {
      await apiForgetMaPairing()
      // The delete route forgets the PSK and opens a fresh window; the fresh
      // read after the command carries its new code and deadline.
      closeMaPairingClock()
    })
  }

  function clearError() {
    error.value = null
  }

  /**
   * The app is going away: stop the local ticker. The server's window is not
   * closed — only its own expiry, a successful pairing or an explicit cancel
   * ends it.
   */
  function teardown() {
    stopTicker()
  }

  return {
    status,
    busy,
    error,
    pairingSecondsLeft,
    maPairingSecondsLeft,
    maPairingCode,
    pairingAdapters,
    applyStatus,
    enable,
    disable,
    useDevice,
    forgetDevice,
    selectInput,
    openPairingWindow,
    closePairingWindow,
    openMaPairing,
    forgetMaPairing,
    clearError,
    teardown,
  }
})
