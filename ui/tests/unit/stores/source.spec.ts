import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/bluetooth', () => ({ getAdapters: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/source', () => ({
  SOURCE_PAIRING_WINDOW_S: 120,
  setSourceEnabled: vi.fn(),
  useSourceDevice: vi.fn(),
  forgetSourceDevice: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
  openMaPairing: vi.fn(),
  cancelMaPairing: vi.fn(),
  selectSource: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getStatus } from '@/api/status'
import {
  cancelMaPairing,
  closeSourcePairing,
  forgetSourceDevice,
  openMaPairing,
  openSourcePairing,
  selectSource,
  setSourceEnabled,
  useSourceDevice,
} from '@/api/source'
import { useBridgeStore } from '@/stores/bridge'
import { PAIRING_GRACE_MS, useSourceStore } from '@/stores/source'
import type { SourcePairingWindow } from '@/api/source'

/** The bridge status's source block, with every field the generated type requires. */
const sourceStatus = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
  phone: null,
  phone_mac: null,
  paired_phone_mac: null,
  streaming: false,
  server: 'ws://ma.local:8927/sendspin',
  ma_connected: true,
  ma_paired: true,
  ma_pairing_required: false,
  ma_pairing_code: null,
  ma_pairing_seconds_left: 0,
  ma_error: null,
  pairing_seconds_left: 0,
  pairing_adapters: [],
  devices: [],
  inputs: [],
  target_player: null,
  target_error: null,
  active_input: null,
  error: null,
  ...overrides,
})

const bridgeStatus = (source: Record<string, unknown> | null = sourceStatus()) =>
  ({ bridge: { sendspin_source: source }, devices: [], groups: [] }) as never

const problem = (status: number, code: string, detail = 'Nope') => new ApiError({ status, code, detail })
const pairingWindow = (overrides: Record<string, unknown> = {}) => ({
  pairing_seconds_left: 120,
  pairing_adapters: ['C0:FB:F9:62:D6:9D'],
  error: null,
  ...overrides,
})

let store: ReturnType<typeof useSourceStore>
let bridge: ReturnType<typeof useBridgeStore>

describe('useSourceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus() as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(openSourcePairing).mockResolvedValue(pairingWindow() as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
    vi.mocked(openMaPairing).mockResolvedValue({ code: '12345678', seconds_left: 300, client_id: 'peer' } as never)
    vi.mocked(cancelMaPairing).mockResolvedValue(undefined as never)
    vi.mocked(useSourceDevice).mockResolvedValue({} as never)
    vi.mocked(forgetSourceDevice).mockResolvedValue(undefined as never)
    vi.mocked(selectSource).mockResolvedValue({} as never)
    store = useSourceStore()
    bridge = useBridgeStore()
  })

  afterEach(() => {
    store.teardown()
    vi.useRealTimers()
  })

  it('holds the source projection forwarded from the bridge status', () => {
    store.applyStatus(bridgeStatus(sourceStatus({ state: 'streaming', phone_mac: '34:C7:E9:25:A3:A3' })))

    expect(store.status?.state).toBe('streaming')
    expect(store.status?.phone_mac).toBe('34:C7:E9:25:A3:A3')

    store.applyStatus(bridgeStatus(null))
    expect(store.status).toBeNull()
  })

  it('adopts a phone window the bridge reports, so a page opened mid-window counts down', async () => {
    vi.useFakeTimers()

    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 47 })))

    expect(store.pairingSecondsLeft).toBe(47)
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.pairingSecondsLeft).toBe(46)
  })

  it('adopting the same window again never moves its deadline later', async () => {
    vi.useFakeTimers()
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 90 })))
    await vi.advanceTimersByTimeAsync(10_000)
    expect(store.pairingSecondsLeft).toBe(80)

    // A page mounting again re-reads the same status; the deadline must not slide.
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 90 })))

    expect(store.pairingSecondsLeft).toBe(80)
  })

  it('opens the phone pairing window with the shared 120 s default and counts it down', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceStatus({ pairing_seconds_left: 120, pairing_adapters: ['C0:FB:F9:62:D6:9D'] })) as never,
    )

    await store.openPairingWindow()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(store.pairingSecondsLeft).toBe(120)
    expect(store.pairingAdapters).toEqual(['C0:FB:F9:62:D6:9D'])

    await vi.advanceTimersByTimeAsync(1000)
    expect(store.pairingSecondsLeft).toBe(119)

    await vi.advanceTimersByTimeAsync(119_000)
    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('seeds the countdown from the server answer, not from the 120 s default', async () => {
    vi.useFakeTimers()
    vi.mocked(openSourcePairing).mockResolvedValue(pairingWindow({ pairing_seconds_left: 47 }) as never)
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 47 })) as never)

    await store.openPairingWindow()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(store.pairingSecondsLeft).toBe(47)
  })

  it('closes the phone pairing window and clears the countdown', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)

    vi.mocked(getStatus).mockResolvedValue(bridgeStatus() as never)
    await store.closePairingWindow()

    expect(closeSourcePairing).toHaveBeenCalledOnce()
    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('surfaces a window the server could not open', async () => {
    vi.mocked(openSourcePairing).mockResolvedValue(pairingWindow({ pairing_seconds_left: 0, error: 'no_adapter' }) as never)

    const answer = await store.openPairingWindow()

    expect(answer).toMatchObject({ error: 'no_adapter' })
    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.error).toMatchObject({ code: 'no_adapter' })
  })

  it('a server terminal answer closes the phone countdown once the post-command grace passes', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)

    // A pushed zero is indistinguishable from a pre-open frame for the length
    // of the grace (see the regression above), so the close is applied after it.
    await vi.advanceTimersByTimeAsync(PAIRING_GRACE_MS + 1)
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })))

    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.pairingAdapters).toEqual([])
  })

  it('opens the Music Assistant pairing window and counts it down', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceStatus({ ma_paired: false, ma_pairing_code: '12345678', ma_pairing_seconds_left: 300 })) as never,
    )

    const result = await store.openMaPairing()

    expect(openMaPairing).toHaveBeenCalledOnce()
    expect(result).toMatchObject({ code: '12345678' })
    expect(store.maPairingCode).toBe('12345678')
    expect(store.maPairingSecondsLeft).toBe(300)
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.maPairingSecondsLeft).toBe(299)
  })

  it('forgetting the Music Assistant pairing drops the old code and adopts the fresh window', async () => {
    vi.useFakeTimers()
    store.applyStatus(bridgeStatus(sourceStatus({ ma_paired: true, ma_pairing_code: null })))
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceStatus({ ma_paired: false, ma_pairing_code: '87654321', ma_pairing_seconds_left: 120 })) as never,
    )

    await store.forgetMaPairing()

    expect(cancelMaPairing).toHaveBeenCalledOnce()
    // The delete route clears the PSK and opens a new window with a new code.
    expect(store.maPairingCode).toBe('87654321')
    expect(store.maPairingSecondsLeft).toBe(120)
  })

  it('disabling the feed closes both local windows at once and adopts the post-stop status', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(
        sourceStatus({ pairing_seconds_left: 120, ma_pairing_code: '12345678', ma_pairing_seconds_left: 300 }),
      ) as never,
    )
    await store.openPairingWindow()
    await store.openMaPairing()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)
    expect(store.maPairingSecondsLeft).toBeGreaterThan(0)

    await store.enable()
    expect(setSourceEnabled).toHaveBeenCalledWith(true)

    // The local close is the immediate optimistic effect, before the command's
    // reconciling read has answered.
    let resolveStatus!: (value: unknown) => void
    vi.mocked(getStatus).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStatus = resolve
      }) as never,
    )
    const disabling = store.disable()
    await vi.advanceTimersByTimeAsync(0)

    expect(setSourceEnabled).toHaveBeenCalledWith(false)
    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.maPairingSecondsLeft).toBe(0)

    // The realistic post-stop status: stopping the source ends both windows
    // server-side (the phone window is closed in SourceClient.stop()).
    resolveStatus(
      bridgeStatus(
        sourceStatus({
          enabled: false,
          state: 'off',
          ma_paired: false,
          ma_pairing_code: null,
          ma_pairing_seconds_left: 0,
          pairing_seconds_left: 0,
          pairing_adapters: [],
        }),
      ) as never,
    )
    await disabling

    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.maPairingSecondsLeft).toBe(0)
    expect(store.status).toMatchObject({ enabled: false, state: 'off' })
  })

  it('keeps the server code when a command fails and stops being busy', async () => {
    vi.mocked(setSourceEnabled).mockRejectedValue(problem(422, 'capture_unavailable', 'No capture node'))

    await expect(store.enable()).rejects.toMatchObject({ code: 'capture_unavailable' })

    expect(store.error).toMatchObject({ code: 'capture_unavailable' })
    expect(store.busy).toBe(false)
  })

  it('a healthy status never clears a pairing error', async () => {
    vi.mocked(openMaPairing).mockRejectedValue(problem(409, 'ma_already_paired', 'Already paired'))
    await expect(store.openMaPairing()).rejects.toMatchObject({ code: 'ma_already_paired' })
    expect(store.error).not.toBeNull()

    // The hardware read is healthy and the feed is connected, but the refusal
    // belongs to the attempt: nothing in a status snapshot may clear it.
    store.applyStatus(bridgeStatus(sourceStatus({ state: 'streaming', ma_connected: true })))

    expect(store.error).toMatchObject({ code: 'ma_already_paired' })
  })

  it('a new command clears the previous attempt error', async () => {
    vi.mocked(setSourceEnabled).mockRejectedValueOnce(problem(422, 'capture_unavailable'))
    await expect(store.enable()).rejects.toMatchObject({ code: 'capture_unavailable' })
    expect(store.error).not.toBeNull()

    await store.disable()

    expect(store.error).toBeNull()
  })

  it('serializes a cancel after a slow open so the late answer cannot revive the window', async () => {
    let resolveOpen!: (window: SourcePairingWindow) => void
    vi.mocked(openSourcePairing).mockReturnValue(
      new Promise<SourcePairingWindow>((resolve) => {
        resolveOpen = resolve
      }) as never,
    )

    const opening = store.openPairingWindow()
    const cancel = store.closePairingWindow()
    resolveOpen(pairingWindow())
    await Promise.all([opening, cancel])

    expect(closeSourcePairing).toHaveBeenCalledOnce()
    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.busy).toBe(false)
  })

  it('a cancelled open never revives the window, even when the cancel itself fails', async () => {
    let resolveOpen!: (window: SourcePairingWindow) => void
    vi.mocked(openSourcePairing).mockReturnValue(
      new Promise<SourcePairingWindow>((resolve) => {
        resolveOpen = resolve
      }) as never,
    )
    vi.mocked(closeSourcePairing).mockRejectedValue(problem(500, 'close_failed'))
    // The reconciling read fails too: only the attempt's generation can keep
    // the cancelled open's answer out of the countdown.
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))

    const opening = store.openPairingWindow()
    const cancel = store.closePairingWindow()
    resolveOpen(pairingWindow())
    await opening
    await expect(cancel).rejects.toMatchObject({ code: 'close_failed' })

    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.error).toMatchObject({ code: 'close_failed' })
  })

  it('a double click opens one window, not two', async () => {
    let resolveOpen!: (window: SourcePairingWindow) => void
    vi.mocked(openSourcePairing).mockReturnValue(
      new Promise<SourcePairingWindow>((resolve) => {
        resolveOpen = resolve
      }) as never,
    )

    const first = store.openPairingWindow()
    const second = store.openPairingWindow()
    resolveOpen(pairingWindow())
    await Promise.all([first, second])

    expect(openSourcePairing).toHaveBeenCalledOnce()
  })

  it('buffers a status that lands while a command is in flight, then reconciles with a fresh read', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    let resolvePairing!: (window: { code: string; seconds_left: number; client_id: string | null }) => void
    vi.mocked(openMaPairing).mockReturnValue(
      new Promise((resolve) => {
        resolvePairing = resolve
      }) as never,
    )
    const pairing = store.openMaPairing()

    // A status read started before the command answers with "no window". It is
    // buffered, not applied over the window a command already owns.
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })))
    expect(store.pairingSecondsLeft).toBe(120)

    resolvePairing({ code: '12345678', seconds_left: 300, client_id: 'peer' })
    await pairing

    expect(store.pairingSecondsLeft).toBe(120)
  })

  it('a status request started before a command cannot overwrite its answer', async () => {
    vi.useFakeTimers()
    let resolveStatus!: (status: never) => void
    vi.mocked(getStatus).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStatus = resolve
      }) as never,
    )
    // The read begins before the command and answers after it has reconciled.
    const staleRead = bridge.refresh()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    resolveStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })) as never)
    await staleRead

    // The older read must not overwrite the command's fresh answer.
    expect(store.pairingSecondsLeft).toBe(120)
  })

  it('a status read that began before a cancel cannot revive the phone window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    // A read begins before the cancel and stays in flight.
    let resolveStale!: (value: unknown) => void
    vi.mocked(getStatus).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStale = resolve
      }) as never,
    )
    const staleRead = bridge.refresh()

    // Cancel; its own reconciling read fails, so no newer read has applied.
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.closePairingWindow()
    expect(store.pairingSecondsLeft).toBe(0)

    // The pre-cancel snapshot still says the window has 120 s left.
    resolveStale(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await staleRead

    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('a status read that began before an open cannot close the new phone window', async () => {
    vi.useFakeTimers()
    let resolveStale!: (value: unknown) => void
    vi.mocked(getStatus).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStale = resolve
      }) as never,
    )
    const staleRead = bridge.refresh()

    // The open succeeds; its own reconciling read fails, so the stale read is
    // the only status that will land.
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    // That read still carries the pre-open "no window".
    resolveStale(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })) as never)
    await staleRead

    expect(store.pairingSecondsLeft).toBe(120)
  })

  it('a status read that began before opening a Music Assistant window cannot close it', async () => {
    vi.useFakeTimers()
    let resolveStale!: (value: unknown) => void
    vi.mocked(getStatus).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStale = resolve
      }) as never,
    )
    const staleRead = bridge.refresh()

    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.openMaPairing()
    expect(store.maPairingSecondsLeft).toBe(300)

    resolveStale(bridgeStatus(sourceStatus({ ma_pairing_seconds_left: 0, ma_pairing_code: null })) as never)
    await staleRead

    expect(store.maPairingSecondsLeft).toBe(300)
    expect(store.maPairingCode).toBe('12345678')
  })

  it('a pushed status produced before a cancel cannot reopen the phone window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    // The cancel succeeds but its reconciling read fails, so the only status
    // that reaches the store afterwards is a frame the server produced while
    // the window was still open.
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.closePairingWindow()
    expect(store.pairingSecondsLeft).toBe(0)

    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })))

    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('a pushed status carrying the pre-open zero cannot close the new phone window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    // A frame produced before the open landed still says there is no window.
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })))

    expect(store.pairingSecondsLeft).toBe(120)
  })

  it('a server-side close after the post-command grace still closes the phone window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    await vi.advanceTimersByTimeAsync(PAIRING_GRACE_MS + 1)
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 0 })))

    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('a phone window opened from another screen is adopted after the post-command grace', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.closePairingWindow()
    expect(store.pairingSecondsLeft).toBe(0)

    await vi.advanceTimersByTimeAsync(PAIRING_GRACE_MS + 1)
    store.applyStatus(bridgeStatus(sourceStatus({ pairing_seconds_left: 90 })))

    expect(store.pairingSecondsLeft).toBe(90)
  })

  it('a pushed status carrying the pre-open zero cannot close the new Music Assistant window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))
    await store.openMaPairing()
    expect(store.maPairingSecondsLeft).toBe(300)

    store.applyStatus(bridgeStatus(sourceStatus({ ma_paired: false, ma_pairing_seconds_left: 0 })))

    expect(store.maPairingSecondsLeft).toBe(300)
    expect(store.maPairingCode).toBe('12345678')
  })

  it('a failed reconcile never restores the buffered pre-command snapshot over a newer read', async () => {
    vi.useFakeTimers()
    store.applyStatus(bridgeStatus(sourceStatus({ enabled: false, state: 'off' })))
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ enabled: true, state: 'streaming' })) as never)

    let finishCommand!: (value: unknown) => void
    vi.mocked(setSourceEnabled).mockReturnValue(
      new Promise((resolve) => {
        finishCommand = resolve
      }) as never,
    )
    let rejectStatus!: (error: unknown) => void
    vi.mocked(getStatus).mockReturnValue(
      new Promise((_resolve, reject) => {
        rejectStatus = reject
      }) as never,
    )

    const enabling = store.enable()
    // A frame produced before the enable is buffered while the command runs.
    store.applyStatus(bridgeStatus(sourceStatus({ enabled: false, state: 'off' })))
    finishCommand({})
    await vi.advanceTimersByTimeAsync(0)

    // The post-enable snapshot arrives from the stream while the reconciling
    // read is still pending, and the read then fails.
    store.applyStatus(bridgeStatus(sourceStatus({ enabled: true, state: 'streaming' })))
    rejectStatus(new Error('bridge unreachable'))
    await enabling

    expect(store.status).toMatchObject({ enabled: true, state: 'streaming' })
  })

  it('a server terminal answer closes the Music Assistant countdown once the post-command grace passes', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceStatus({ ma_paired: false, ma_pairing_code: '12345678', ma_pairing_seconds_left: 300 })) as never,
    )
    await store.openMaPairing()
    expect(store.maPairingSecondsLeft).toBe(300)

    await vi.advanceTimersByTimeAsync(PAIRING_GRACE_MS + 1)
    store.applyStatus(bridgeStatus(sourceStatus({ ma_paired: false, ma_pairing_seconds_left: 0 })))

    expect(store.maPairingSecondsLeft).toBe(0)
    expect(store.maPairingCode).toBeNull()
  })

  it('a stale reconcile answer never applies the buffered pre-command status over a newer read', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceStatus({ state: 'streaming', pairing_seconds_left: 120 })) as never,
    )
    await store.openPairingWindow()
    expect(store.pairingSecondsLeft).toBe(120)

    // A command is in flight while a pre-command status lands; it is buffered.
    let finishCommand!: (value: unknown) => void
    vi.mocked(useSourceDevice).mockReturnValue(
      new Promise((resolve) => {
        finishCommand = resolve
      }) as never,
    )
    const command = store.useDevice('34:C7:E9:25:A3:A3')
    store.applyStatus(bridgeStatus(sourceStatus({ state: 'waiting', pairing_seconds_left: 0 })))

    // The command's reconciling read answers 0, but a newer read has already
    // applied the open window, so the older answer is dropped as stale.
    let calls = 0
    vi.mocked(getStatus).mockImplementation(async () => {
      calls += 1
      if (calls === 1) {
        await bridge.refresh()
        return bridgeStatus(sourceStatus({ state: 'waiting', pairing_seconds_left: 0 })) as never
      }
      return bridgeStatus(sourceStatus({ state: 'streaming', pairing_seconds_left: 120 })) as never
    })
    finishCommand({})
    await command

    // The buffered status is older than the read that applied: it must not be
    // replayed over it, not even for the status block it carries.
    expect(store.pairingSecondsLeft).toBe(120)
    expect(store.status?.state).toBe('streaming')
  })

  it('one ticker drives both windows and stops when both are closed', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(
        sourceStatus({ pairing_seconds_left: 120, ma_pairing_code: '12345678', ma_pairing_seconds_left: 300 }),
      ) as never,
    )
    await store.openPairingWindow()
    await store.openMaPairing()

    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.pairingSecondsLeft).toBe(119)
    expect(store.maPairingSecondsLeft).toBe(299)

    await vi.advanceTimersByTimeAsync(299_000)
    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.maPairingSecondsLeft).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('teardown stops the local ticker without closing the server window', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus(sourceStatus({ pairing_seconds_left: 120 })) as never)
    await store.openPairingWindow()
    expect(vi.getTimerCount()).toBe(1)

    store.teardown()

    expect(vi.getTimerCount()).toBe(0)
    expect(closeSourcePairing).not.toHaveBeenCalled()
  })

  it('uses, forgets and selects source devices through the store', async () => {
    await store.useDevice('34:C7:E9:25:A3:A3')
    expect(useSourceDevice).toHaveBeenCalledWith('34:C7:E9:25:A3:A3')

    await store.forgetDevice('34:C7:E9:25:A3:A3')
    expect(forgetSourceDevice).toHaveBeenCalledWith('34:C7:E9:25:A3:A3')

    await store.selectInput('local:alsa_input.line')
    expect(selectSource).toHaveBeenCalledWith('local:alsa_input.line')
    expect(getStatus).toHaveBeenCalledTimes(3)
  })
})
