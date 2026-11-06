import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
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
import { getStatus } from '@/api/status'
import { openMaPairing, openSourcePairing } from '@/api/source'
import { useHardwareStore } from '@/stores/hardware'
import { useSourceStore } from '@/stores/source'

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
const pairingWindow = (overrides: Record<string, unknown> = {}) => ({
  pairing_seconds_left: 120,
  pairing_adapters: ['C0:FB:F9:62:D6:9D'],
  error: null,
  ...overrides,
})

/** The bridge status's source block, with every field the generated type requires. */
const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
  phone: null,
  phone_mac: null,
  paired_phone_mac: null,
  streaming: false,
  server: null,
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

const bridgeStatus = (source: Record<string, unknown> | null = sourceBlock()) =>
  ({ bridge: { sendspin_source: source }, devices: [], groups: [] }) as never

let store: ReturnType<typeof useHardwareStore>
let source: ReturnType<typeof useSourceStore>

describe('useHardwareStore: source read data', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    asOfTick = 0
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus() as never)
    vi.mocked(openSourcePairing).mockResolvedValue(pairingWindow() as never)
    vi.mocked(openMaPairing).mockResolvedValue({ code: '12345678', seconds_left: 300, client_id: 'peer' } as never)
    store = useHardwareStore()
    source = useSourceStore()
    store.connect()
    await flushPromises()
    vi.mocked(getHardware).mockClear()
  })

  afterEach(() => {
    store.disconnect()
    source.teardown()
    vi.useRealTimers()
  })

  it('exposes the feed, and null when the bridge reports no source at all', async () => {
    expect(store.source?.state).toBe('waiting')
    expect(store.source?.ma.paired).toBe(true)

    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: null }) as never)
    await store.refresh()
    expect(store.source).toBeNull()
  })

  it('keeps the feed in the snapshot after the page is left', async () => {
    store.disconnect()

    expect(store.source?.state).toBe('waiting')
  })

  it('leaving the Hardware page keeps the source store pairing countdown', async () => {
    vi.useFakeTimers()
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceBlock({ pairing_seconds_left: 120 })) as never,
    )
    await source.openPairingWindow()
    expect(source.pairingSecondsLeft).toBeGreaterThan(0)

    store.disconnect()

    // The window belongs to the source, not to the Hardware page visit.
    expect(source.pairingSecondsLeft).toBeGreaterThan(0)
    await vi.advanceTimersByTimeAsync(1000)
    expect(source.pairingSecondsLeft).toBe(119)
  })

  it('a healthy hardware read never clears a pairing error (errors are per attempt)', async () => {
    vi.mocked(openMaPairing).mockRejectedValue(problem(409, 'ma_already_paired', 'Already paired'))
    await expect(source.openMaPairing()).rejects.toMatchObject({ code: 'ma_already_paired' })

    await store.refresh()
    store.disconnect()

    expect(source.error).toMatchObject({ code: 'ma_already_paired' })
  })
})
