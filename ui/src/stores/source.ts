import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  SOURCE_PAIRING_WINDOW_S,
  cancelMaPairing as apiForgetMaPairing,
  clearActiveSource,
  closeSourcePairing,
  forgetSourceDevice as apiForgetSourceDevice,
  getSourceInputs,
  openMaPairing as apiOpenMaPairing,
  openSourcePairing,
  selectSource,
  setActiveMute,
  setActiveVolume,
  setSourceEnabled,
  useSourceDevice as apiUseSourceDevice,
  type MaPairingWindow,
  type SourcePairingWindow,
} from '@/api/source'
import { DEFAULT_EVENT_TYPES, useEventStream, type EventType } from '@/composables/useEventStream'
import type { BridgeStatus, SendspinSourceStatus, SourceLevelEvent } from '@/api/types'
import { headsetMicMac, type SourceTransition } from '@/utils/sourceMode'
import { useBridgeStore, type RefreshOutcome } from './bridge'

/**
 * How long a command's change to a pairing window is protected from pushed
 * status frames. The bridge batches status events into a 100 ms debounce, so a
 * frame describing the state *before* a command can still be delivered after
 * the command answered. The grace must comfortably exceed that debounce plus
 * transport and event-loop delay; two seconds covers it while still letting a
 * window opened or closed from another screen show up almost at once: a change
 * that arrives during the grace is discarded, and the store rereads once when
 * the grace runs out rather than waiting for some later unrelated event.
 */
export const PAIRING_GRACE_MS = 2000

/** A failed source command: the server's stable code and its sentence. */
export interface SourceFailure {
  code: string
  detail: string
}

/** One serialized command attempt. Its generation decides whether an answer still counts. */
interface Attempt {
  id: number
}

/** A status that arrived while a command was queued or running, with the epoch it began in. */
interface BufferedStatus {
  source: SendspinSourceStatus | null
  application: StatusApplication
}

/**
 * A status application: the epoch it began in and, for a read, when its request
 * started. A pushed frame has no request time — it may have been produced even
 * before the command that preceded its delivery.
 */
interface StatusApplication {
  epoch: number
  requestedAt?: number
}

/** A command's change to one pairing window, held against pushed status frames. */
interface WindowGrace {
  direction: 'opened' | 'closed'
  at: number
}

/**
 * The single owner of the Source feed in the UI: its snapshot, its commands and
 * the two pairing windows' clocks.
 *
 * The snapshot is the bridge status's own ``sendspin_source`` block, forwarded
 * by ``useBridgeStore`` — the store reads no status of its own. The one stream
 * it opens is the ``source.level`` probe: an event type newer than the default
 * subscription, asked for only after ``GET /bridge/source/inputs`` answered and
 * dropped quietly when an older bridge refuses it. The hardware snapshot keeps
 * the feed as read data for the Hardware page; it owns nothing here.
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
  /**
   * The source-mode command a device's card is waiting on, keyed by its bare
   * MAC. A card reads its own entry to hold its section and to replace its
   * status chip with the transition.
   */
  const transitions = ref<Record<string, SourceTransition>>({})
  /** The device whose source-mode command failed; only that card shows ``error``. */
  const errorTarget = ref<string | null>(null)
  /**
   * The latest ``source.level`` frame per input id, 0..1. Only the active input
   * is published; keeping them keyed means a card that is handed the input
   * later still has no stale level of its own.
   */
  const levels = ref<Record<string, { level: number; muted: boolean }>>({})
  /** The level type is asked for once; a refusal is remembered for this page. */
  let levelProbe: 'idle' | 'probing' | 'open' | 'unsupported' = 'idle'
  let levelStream: ReturnType<typeof useEventStream> | null = null

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
  /**
   * Commands completed so far. A status application carries the epoch it began
   * in; one that began before the last completed command cannot say anything
   * about the pairing windows — it predates the command's effect.
   */
  const commandEpoch = ref(0)
  /**
   * When a command last changed each window. A pushed frame may have been
   * produced before that command even though it arrived after it, so until the
   * grace ends it may not undo the change.
   */
  let pairingGrace: WindowGrace | null = null
  let maPairingGrace: WindowGrace | null = null
  /** Forgetting the Music Assistant pairing reopens a window as part of the same command. */
  let maReopenAuthorised = false
  /** When the last command completed; only a read begun after it can describe its effect. */
  let lastCommandCompletedAt: number | null = null

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

  /** Forget a grace that has run its course; its direction no longer constrains anything. */
  function activeGrace(grace: WindowGrace | null): WindowGrace | null {
    if (grace === null) return null
    return Date.now() - grace.at < PAIRING_GRACE_MS ? grace : null
  }

  /**
   * Whether a status may speak for a window a command just changed. During the
   * grace a pushed frame may not reopen a closed window (no positive adoption),
   * and it may not close an opened one unless the application is a read that
   * began after the command completed. The one exception is a command that
   * closes a window in order to reopen it: its own fresh read may adopt the new
   * window, which the operator is waiting to see.
   */
  function mayApplyWindow(
    grace: WindowGrace | null,
    seconds: number,
    trusted: boolean,
    reopenAuthorised = false,
  ): boolean {
    if (grace === null) return true
    if (grace.direction === 'closed') return trusted && reopenAuthorised && seconds > 0
    return seconds > 0 || trusted
  }

  /** A reread armed because a pushed status had to be dropped during a grace. */
  let graceReread: ReturnType<typeof setTimeout> | null = null

  /**
   * Look again once the grace has run out. A status that had to be dropped
   * while a window was protected is the only evidence this screen has that a
   * change happened elsewhere, so without this the change would wait for an
   * unrelated event — and a bridge with nothing else changing may never send one.
   */
  function rereadAfterGrace() {
    if (graceReread !== null) return
    graceReread = setTimeout(() => {
      graceReread = null
      void useBridgeStore().refresh()
    }, PAIRING_GRACE_MS + 1)
  }

  /** A read that began after the last command completed can describe that command's effect. */
  function requestedAfterCommand(application: StatusApplication): boolean {
    return (
      application.requestedAt !== undefined &&
      lastCommandCompletedAt !== null &&
      application.requestedAt >= lastCommandCompletedAt
    )
  }

  function rememberPairingChange(direction: WindowGrace['direction']) {
    pairingGrace = { direction, at: Date.now() }
  }

  function rememberMaChange(direction: WindowGrace['direction']) {
    maPairingGrace = { direction, at: Date.now() }
    maReopenAuthorised = false
  }

  function applySource(
    next: SendspinSourceStatus | null,
    application: StatusApplication = { epoch: commandEpoch.value },
  ) {
    // A status older than the last completed command describes the server
    // before that command: neither its snapshot nor its windows are facts about
    // now. In particular the failed-reconcile fallback must not regress a newer
    // snapshot the stream already delivered.
    if (application.epoch < commandEpoch.value) return
    status.value = next
    if (!next) return
    const trusted = requestedAfterCommand(application)

    const phoneGrace = activeGrace(pairingGrace)
    const phoneSeconds = Number(next.pairing_seconds_left) || 0
    if (mayApplyWindow(phoneGrace, phoneSeconds, trusted)) {
      pairingAdapters.value = Array.isArray(next.pairing_adapters) ? next.pairing_adapters : []
      adoptPairingSeconds(phoneSeconds)
    } else if (phoneSeconds !== pairingSecondsLeft.value) {
      rereadAfterGrace()
    }

    const maGrace = activeGrace(maPairingGrace)
    const maSeconds = Number(next.ma_pairing_seconds_left) || 0
    if (mayApplyWindow(maGrace, maSeconds, trusted, maReopenAuthorised)) {
      if (maGrace !== null && maReopenAuthorised && trusted) maReopenAuthorised = false
      // The code belongs to the window: a late frame must not resurrect an old
      // one, so while the grace runs only the command's own read may replace it.
      if (next.ma_pairing_code && (maGrace === null || trusted)) maPairingCode.value = next.ma_pairing_code
      adoptMaPairingSeconds(maSeconds)
    } else if (maSeconds !== maPairingSecondsLeft.value) {
      rereadAfterGrace()
    }
  }

  // -------------------------------------------------------------------------
  // Status input
  // -------------------------------------------------------------------------

  /** A status that arrived while a command was queued or running, applied only if the fresh read fails. */
  let buffered: BufferedStatus | undefined
  /** The fresh read after a command is itself a status application, not an intermediate one. */
  let reconciling = false

  function applyStatus(next: BridgeStatus | null | undefined, application: Partial<StatusApplication> = {}) {
    const source = next?.bridge?.sendspin_source ?? null
    const resolved: StatusApplication = {
      epoch: application.epoch ?? commandEpoch.value,
      requestedAt: application.requestedAt,
    }
    if (busy.value && !reconciling) {
      buffered = { source, application: resolved }
      return
    }
    applySource(source, resolved)
  }

  async function reconcile() {
    if (pending > 0) return
    const stale = buffered
    buffered = undefined
    reconciling = true
    let answered: RefreshOutcome = 'failed'
    try {
      answered = await useBridgeStore().refresh()
    } finally {
      reconciling = false
    }
    // Only a read that never answered may fall back to the status seen while the
    // command ran. That status began before the command, so the epoch rule drops
    // it: the snapshot is left as the stream last had it rather than regressing.
    if (answered === 'failed' && stale !== undefined) applySource(stale.source, stale.application)
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

  /** One comparable key for a MAC: a card and an input id never disagree on spelling. */
  function deviceKey(mac: string | null | undefined): string | null {
    const key = String(mac ?? '').replace(/[^0-9A-Fa-f]/g, '').toUpperCase()
    return key || null
  }

  function beginTransition(mac: string | null | undefined, transition: SourceTransition) {
    const key = deviceKey(mac)
    if (!key) return
    transitions.value = { ...transitions.value, [key]: transition }
  }

  function endTransition(mac: string | null | undefined, transition: SourceTransition) {
    const key = deviceKey(mac)
    if (!key || transitions.value[key] !== transition) return
    const next = { ...transitions.value }
    delete next[key]
    transitions.value = next
  }

  /** The transition a device's card is showing, or null when no command is running for it. */
  function transitionFor(mac: string | null | undefined): SourceTransition | null {
    const key = deviceKey(mac)
    return key ? (transitions.value[key] ?? null) : null
  }

  /** The refusal to show on that device's card; a command for another card leaves it alone. */
  function failureFor(mac: string | null | undefined): SourceFailure | null {
    const key = deviceKey(mac)
    return key && errorTarget.value === key ? error.value : null
  }

  function enqueue<T>(key: string, run: (attempt: Attempt) => Promise<T>): Promise<T> {
    const running = inflight.get(key)
    if (running) return running as Promise<T>
    const attempt: Attempt = { id: ++generation }
    error.value = null
    errorTarget.value = null
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
          // The command is over: any status application that began before this
          // point predates its effect on the pairing windows.
          commandEpoch.value += 1
          lastCommandCompletedAt = Date.now()
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
      // Stopping the feed ends the capture and its level frames. A later
      // enable re-probes the type rather than trusting a stream that is gone.
      closeLevelProbe()
      // Stopping the feed ends both pairing windows on the server as well; a
      // local countdown that kept running would offer a window that is gone.
      closePairingClock()
      rememberPairingChange('closed')
      closeMaPairingClock()
      rememberMaChange('closed')
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
    // A headset microphone belongs to one player: the card that pressed this
    // shows the switch and keeps its section until the answer arrives.
    const mac = headsetMicMac(sourceId)
    return enqueue(`source.select:${sourceId}`, async (attempt) => {
      beginTransition(mac, 'to-source')
      try {
        await selectSource(sourceId)
      } catch (e) {
        if (mac && attempt.id === generation) errorTarget.value = deviceKey(mac)
        throw e
      } finally {
        endTransition(mac, 'to-source')
      }
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
          rememberPairingChange('closed')
        } else if (Number.isFinite(left) && left > 0) {
          serverPairingSeen = true
          setPairingDeadline(Date.now() + left * 1000)
          pairingAdapters.value = answer?.pairing_adapters ?? []
          rememberPairingChange('opened')
        } else {
          closePairingClock()
          rememberPairingChange('closed')
        }
      }
      return answer
    })
  }

  function closePairingWindow() {
    return enqueue('pairing.close', async () => {
      await closeSourcePairing()
      closePairingClock()
      rememberPairingChange('closed')
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
          rememberMaChange('opened')
        } else {
          closeMaPairingClock()
          rememberMaChange('closed')
        }
      }
      return answer
    })
  }

  function forgetMaPairing() {
    return enqueue('ma.forget', async () => {
      await apiForgetMaPairing()
      // The delete route forgets the PSK and opens a fresh window; the fresh
      // read after the command carries its new code and deadline. The window
      // stays closed to pushed frames, but that read may reopen it.
      closeMaPairingClock()
      rememberMaChange('closed')
      maReopenAuthorised = true
    })
  }

  // -------------------------------------------------------------------------
  // Input level, gain and mute
  // -------------------------------------------------------------------------

  /** The row of the input the bridge currently captures, if it is listed. */
  function activeInputRow() {
    const active = status.value?.active_input
    if (!active) return null
    return status.value?.inputs?.find((input) => input.id === active) ?? null
  }

  /** One level frame: clamp to the meter's range and ignore a frame without an input. */
  function applyLevel(event: SourceLevelEvent | null | undefined) {
    const id = typeof event?.id === 'string' ? event.id : ''
    if (!id) return
    const raw = Number(event?.level)
    const level = Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : 0
    levels.value = { ...levels.value, [id]: { level, muted: event?.muted === true } }
  }

  function levelFor(sourceId: string) {
    return levels.value[sourceId] ?? null
  }

  function closeLevelProbe() {
    levelStream?.disconnect()
    levelStream = null
    levelProbe = 'idle'
  }

  /**
   * Ask for ``source.level`` the way the hardware page asks for ``hardware``:
   * only once a read proves this bridge knows the feature, and with the refusal
   * of an older bridge treated as an answer, not an error.
   */
  async function ensureLevels() {
    if (levelProbe !== 'idle') return
    levelProbe = 'probing'
    try {
      await getSourceInputs()
    } catch {
      // A failed read says nothing about the bridge's event types: retry later.
      levelProbe = 'idle'
      return
    }
    levelStream = useEventStream({
      types: [...DEFAULT_EVENT_TYPES, 'source.level'] as EventType[],
      onEvent(event) {
        if (event.type === 'source.level') applyLevel(event.data as SourceLevelEvent)
      },
      onConnect() {
        levelProbe = 'open'
      },
      onRejected() {
        // An old bridge answers 400 for the type. Nothing else was asked for;
        // close the refused stream and keep the cards on their status snapshot.
        levelProbe = 'unsupported'
        levelStream?.disconnect()
        levelStream = null
      },
    })
    levelStream.connect()
  }

  /**
   * Set the gain of the active input. The slider moves at once; a refusal puts
   * the previous value back. The queue keeps the attempt's error and epoch
   * rules, exactly as every other source command.
   */
  function setGain(volume: number) {
    const asked = Math.round(Number(volume))
    const level = Number.isFinite(asked) ? Math.min(100, Math.max(0, asked)) : 0
    const input = activeInputRow()
    const previous = input?.volume
    if (input) input.volume = level
    return enqueue(`source.gain:${level}`, async () => {
      try {
        await setActiveVolume(level)
      } catch (e) {
        if (input && previous !== undefined) input.volume = previous
        throw e
      }
    })
  }

  /** Mute the active input's capture, without ending its stream to Music Assistant. */
  function setMuted(muted: boolean) {
    const input = activeInputRow()
    const previous = input?.muted
    if (input) input.muted = muted
    return enqueue(`source.mute:${muted}`, async () => {
      try {
        await setActiveMute(muted)
      } catch (e) {
        if (input && previous !== undefined) input.muted = previous
        throw e
      }
    })
  }

  /** Release the active input so its player can resume; the feature stays enabled. */
  function release(mac?: string | null) {
    // The card that pressed Back to playback names its device so it can show
    // the A2DP restore and stay in the Sources section until it settles.
    const key = deviceKey(mac)
    return enqueue(`source.release${key ? `:${key}` : ''}`, async (attempt) => {
      beginTransition(mac, 'to-playback')
      try {
        await clearActiveSource()
      } catch (e) {
        if (mac && attempt.id === generation) errorTarget.value = key
        throw e
      } finally {
        endTransition(mac, 'to-playback')
      }
    })
  }

  function clearError() {
    error.value = null
    errorTarget.value = null
  }

  /**
   * The app is going away: stop the local ticker. The server's window is not
   * closed — only its own expiry, a successful pairing or an explicit cancel
   * ends it.
   */
  function teardown() {
    stopTicker()
    closeLevelProbe()
    transitions.value = {}
    errorTarget.value = null
  }

  return {
    status,
    busy,
    error,
    transitionFor,
    failureFor,
    pairingSecondsLeft,
    maPairingSecondsLeft,
    maPairingCode,
    pairingAdapters,
    commandEpoch,
    applyStatus,
    applyLevel,
    levelFor,
    ensureLevels,
    setGain,
    setMuted,
    release,
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
