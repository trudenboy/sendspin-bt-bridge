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
  setSourcePlayer: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { closeSourcePairing, openSourcePairing, setSourceEnabled, setSourcePlayer } from '@/api/source'
import { useHardwareStore } from '@/stores/hardware'

const sourceFeed = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  port: 8940,
  state: 'waiting',
  phone_mac: null,
  capture: { kind: 'pipewire_node', node: 'bluez_input.FC_58_FA_EB_08_6C.0' },
  relays: [],
  error: null,
  ...overrides,
})

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: '2026-10-31T11:02:13Z',
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
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(setSourcePlayer).mockResolvedValue({ device_id: 'eneby20', playing_source: true } as never)
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
    expect(store.source?.port).toBe(8940)

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

  it('surfaces a window the server could not open', async () => {
    vi.mocked(openSourcePairing).mockResolvedValue(window({ pairing_seconds_left: 0, error: 'no_adapter' }) as never)

    await store.startPairingWindow()

    expect(store.pairingSecondsLeft).toBe(0)
    expect(store.sourceError).toMatchObject({ code: 'no_adapter' })
  })

  it('arms and disarms a relay through the players route', async () => {
    await store.setSourcePlayer('eneby20', true)

    expect(setSourcePlayer).toHaveBeenCalledWith('eneby20', true)
    expect(getHardware).toHaveBeenCalledOnce()
    expect(store.sourceError).toBeNull()
  })

  it('keeps the server code and the speaker when a relay has no output (409 source_sink_missing)', async () => {
    vi.mocked(setSourcePlayer).mockRejectedValue(
      problem(409, 'source_sink_missing', 'That speaker has no audio output'),
    )

    await expect(store.setSourcePlayer('eneby20', true)).rejects.toMatchObject({ code: 'source_sink_missing' })

    expect(store.sourceError).toEqual({
      code: 'source_sink_missing',
      detail: 'That speaker has no audio output',
      player: 'eneby20',
    })
    expect(store.sourceBusy).toBe(false)
  })

  it('surfaces the server code when the source itself cannot start', async () => {
    vi.mocked(setSourceEnabled).mockRejectedValue(problem(422, 'capture_unavailable', 'No capture node'))

    await expect(store.startSource()).rejects.toMatchObject({ code: 'capture_unavailable' })

    expect(store.sourceError).toMatchObject({ code: 'capture_unavailable', player: null })
    expect(store.sourceBusy).toBe(false)
  })
})
