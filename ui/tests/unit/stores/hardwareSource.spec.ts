import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/source', () => ({
  SOURCE_PAIRING_WINDOW_S: 120,
  setSourceEnabled: vi.fn(),
  openMaPairing: vi.fn(),
  cancelMaPairing: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { cancelMaPairing, closeSourcePairing, openMaPairing, openSourcePairing, setSourceEnabled } from '@/api/source'
import { useHardwareStore } from '@/stores/hardware'

const sourceFeed = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
  phone_mac: null,
  paired_phone_mac: null,
  capture: { kind: 'pipewire_node', node: 'bluez_input.FC_58_FA_EB_08_6C.0' },
  ma: {
    server: 'ws://ma.local:8927/sendspin',
    connected: true,
    paired: true,
    pairing_required: false,
    pairing_code: null,
    pairing_seconds_left: 0,
    error: null,
  },
  error: null,
  ...overrides,
})

/** Every read is taken a second later: the store drops reads that are not newer. */
let asOfTick = 0
const nextAsOf = () => new Date(Date.UTC(2026, 9, 31, 11, 2, 12 + ++asOfTick)).toISOString()

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: nextAsOf(),
  stale_after_s: 2,
  stale: false,
  capabilities: { bluetooth: true, dbus: true, audio_server: 'pipewire', capture: true, default_sink: true, reasons: {} },
  controllers: [],
  bluetooth_devices: [],
  audio: { cards: [], sinks: [], sources: [] },
  source: sourceFeed(),
  degraded: [],
  ...overrides,
})

const problem = (status: number, code: string, detail = 'Nope') => new ApiError({ status, code, detail })
const window = (overrides: Record<string, unknown> = {}) => ({
  pairing_seconds_left: 120,
  pairing_adapters: ['C0:FB:F9:62:D6:9D'],
  error: null,
  ...overrides,
})

let store: ReturnType<typeof useHardwareStore>

describe('useHardwareStore: source', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    asOfTick = 0
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(openMaPairing).mockResolvedValue({ code: '12345678', seconds_left: 300, client_id: 'peer' } as never)
    vi.mocked(cancelMaPairing).mockResolvedValue(undefined as never)
    vi.mocked(openSourcePairing).mockResolvedValue(window() as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
    store = useHardwareStore()
    store.connect()
    await flushPromises()
    vi.mocked(getHardware).mockClear()
  })

  afterEach(() => {
    store.disconnect()
    vi.useRealTimers()
  })

  it('exposes the feed, and null when the bridge reports no source at all', async () => {
    expect(store.source?.state).toBe('waiting')
    expect(store.source?.ma.paired).toBe(true)

    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: null }) as never)
    await store.refresh()
    expect(store.source).toBeNull()
  })

  it('starts and stops the source, refreshing after each command', async () => {
    await store.startSource()
    expect(setSourceEnabled).toHaveBeenCalledWith(true)
    expect(getHardware).toHaveBeenCalledOnce()
    expect(store.sourceError).toBeNull()

    vi.mocked(getHardware).mockClear()
    await store.stopSource()
    expect(setSourceEnabled).toHaveBeenCalledWith(false)
    expect(getHardware).toHaveBeenCalledOnce()
  })

  it('opens the pairing window with the shared 120 s default and counts it down', async () => {
    vi.useFakeTimers()

    await store.startPairingWindow()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(store.pairingSecondsLeft).toBe(120)

    await vi.advanceTimersByTimeAsync(1000)
    expect(store.pairingSecondsLeft).toBe(119)

    await vi.advanceTimersByTimeAsync(119_000)
    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('closes the pairing window and clears the countdown', async () => {
    await store.startPairingWindow()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)

    await store.stopPairingWindow()

    expect(closeSourcePairing).toHaveBeenCalledOnce()
    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('seeds the countdown from the server answer, not from the 120 s default', async () => {
    vi.useFakeTimers()
    vi.mocked(openSourcePairing).mockResolvedValue(window({ pairing_seconds_left: 47 }) as never)

    await store.startPairingWindow()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(store.pairingSecondsLeft).toBe(47)
  })

  it('adopts a window the bridge reports, so a reload keeps the countdown', async () => {
    vi.useFakeTimers()

    store.adoptPairingWindow(47)

    expect(store.pairingSecondsLeft).toBe(47)
  })

  it('never shortens a window it already counts', async () => {
    vi.useFakeTimers()
    store.adoptPairingWindow(90)

    store.adoptPairingWindow(20)

    expect(store.pairingSecondsLeft).toBe(90)
  })

  it('surfaces a window the server could not open', async () => {
    vi.mocked(openSourcePairing).mockResolvedValue(window({ pairing_seconds_left: 0, error: 'no_adapter' }) as never)

    const answer = await store.startPairingWindow()

    expect(answer).toMatchObject({ error: 'no_adapter' })
    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.sourceError).toMatchObject({ code: 'no_adapter' })
  })

  it('clears a refusal once a read shows the source healthy again', async () => {
    vi.mocked(openSourcePairing).mockResolvedValue(window({ pairing_seconds_left: 0, error: 'no_agent' }) as never)
    await store.startPairingWindow()
    expect(store.sourceError).toMatchObject({ code: 'no_agent' })

    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed({ state: 'streaming' }) }) as never)
    await store.refresh()

    expect(store.sourceError).toBeNull()
  })

  it('clears the countdown when the page is left, so a return can pair again', async () => {
    vi.useFakeTimers()
    await store.startPairingWindow()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)

    store.disconnect()

    expect(store.pairingSecondsLeft).toBe(0)
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.pairingSecondsLeft).toBe(0)
  })

  it('clears a refusal when the page is left', async () => {
    vi.mocked(openMaPairing).mockRejectedValue(problem(409, 'ma_already_paired', 'Already paired'))
    await expect(store.openMaPairingWindow()).rejects.toMatchObject({ code: 'ma_already_paired' })
    expect(store.sourceError).not.toBeNull()

    store.disconnect()

    expect(store.sourceError).toBeNull()
  })

  it('opens the Music Assistant pairing window and counts it down', async () => {
    vi.useFakeTimers()
    const result = await store.openMaPairingWindow()

    expect(openMaPairing).toHaveBeenCalledOnce()
    expect(result).toMatchObject({ code: '12345678' })
    expect(store.maPairingSecondsLeft).toBe(300)
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.maPairingSecondsLeft).toBe(299)
  })

  it('forgets the Music Assistant pairing through the delete route', async () => {
    await store.forgetMaPairing()

    expect(cancelMaPairing).toHaveBeenCalledOnce()
    expect(store.maPairingSecondsLeft).toBe(0)
    expect(store.sourceError).toBeNull()
  })

  it('surfaces the server code when the source itself cannot start', async () => {
    vi.mocked(setSourceEnabled).mockRejectedValue(problem(422, 'capture_unavailable', 'No capture node'))

    await expect(store.startSource()).rejects.toMatchObject({ code: 'capture_unavailable' })

    expect(store.sourceError).toMatchObject({ code: 'capture_unavailable', player: null })
    expect(store.sourceBusy).toBe(false)
  })
})

describe('useHardwareStore: pairing window follows the server', () => {
  it('clears the countdown when the bridge closes the window early', async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    const store = useHardwareStore()
    store.adoptPairingWindow(30)
    expect(store.pairingSecondsLeft).toBe(30)

    store.adoptPairingWindow(0)

    expect(store.pairingSecondsLeft).toBe(0)
  })
})
