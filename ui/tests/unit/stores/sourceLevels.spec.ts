import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

/**
 * The source store now owns the live input level and its probe. The event
 * stream and the HTTP surface are the only fakes: the store under test is the
 * real one, so its queue, its epoch rule and its reconciling read all run.
 */
const stream = vi.hoisted(() => ({
  useEventStream: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
}))

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: stream.useEventStream,
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/bluetooth', () => ({ getAdapters: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  getSourceInputs: vi.fn(),
  clearActiveSource: vi.fn(),
  setActiveVolume: vi.fn(),
  setActiveMute: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getStatus } from '@/api/status'
import {
  clearActiveSource,
  getSourceInputs,
  setActiveMute,
  setActiveVolume,
} from '@/api/source'
import { useSourceStore } from '@/stores/source'

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`

const micInput = (overrides: Record<string, unknown> = {}) => ({
  id: INPUT_ID,
  kind: 'headset_microphone',
  name: 'Headset',
  description: 'HFP microphone',
  active: true,
  available: true,
  connected: true,
  monitor: false,
  mac: MAC,
  node: null,
  volume: 40,
  muted: false,
  ...overrides,
})

const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'streaming',
  phone: null,
  phone_mac: null,
  paired_phone_mac: null,
  streaming: true,
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
  inputs: [micInput()],
  target_player: null,
  target_error: null,
  active_input: INPUT_ID,
  error: null,
  ...overrides,
})

const bridgeStatus = (source: Record<string, unknown> | null = sourceBlock()) =>
  ({ bridge: { sendspin_source: source }, devices: [], groups: [] }) as never

const problem = (status: number, code: string) => new ApiError({ status, code, detail: 'Nope' })

let store: ReturnType<typeof useSourceStore>

describe('source store: input level and gain', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    stream.useEventStream.mockReturnValue({
      connected: { value: false },
      connect: stream.connect,
      disconnect: stream.disconnect,
    })
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus() as never)
    vi.mocked(getSourceInputs).mockResolvedValue({ inputs: [], local_candidates: [] } as never)
    vi.mocked(clearActiveSource).mockResolvedValue(undefined as never)
    vi.mocked(setActiveVolume).mockResolvedValue({} as never)
    vi.mocked(setActiveMute).mockResolvedValue({} as never)
    store = useSourceStore()
  })

  afterEach(() => {
    store.teardown()
  })

  it('keeps the level of each input it has been told about', () => {
    store.applyLevel({ id: INPUT_ID, level: 0.42, muted: true })

    expect(store.levelFor(INPUT_ID)).toMatchObject({ level: 0.42, muted: true })
    expect(store.levelFor('local:alsa_input.line')).toBeNull()
  })

  it('clamps a level into 0..1 and ignores a frame without an id', () => {
    store.applyLevel({ id: INPUT_ID, level: 3, muted: false })
    store.applyLevel({ id: '', level: 0.5, muted: false })
    store.applyLevel(null as never)

    expect(store.levelFor(INPUT_ID)?.level).toBe(1)
    expect(store.levelFor('') ?? null).toBeNull()
  })

  it('opens the source.level probe only after the inputs read answered', async () => {
    vi.mocked(getSourceInputs).mockRejectedValueOnce(new Error('bridge unreachable'))

    await store.ensureLevels()
    expect(stream.useEventStream).not.toHaveBeenCalled()

    await store.ensureLevels()
    expect(getSourceInputs).toHaveBeenCalledTimes(2)
    expect(stream.useEventStream).toHaveBeenCalledOnce()
    const options = stream.useEventStream.mock.calls[0]![0]
    expect(options.types).toContain('source.level')

    options.onEvent({
      v: 1,
      type: 'source.level',
      at: '2026-11-06T10:00:00Z',
      data: { id: INPUT_ID, level: 0.25, muted: false },
    })
    expect(store.levelFor(INPUT_ID)?.level).toBe(0.25)
  })

  it('tolerates a bridge that refuses the source.level type', async () => {
    await store.ensureLevels()
    const options = stream.useEventStream.mock.calls[0]![0]

    expect(() => options.onRejected()).not.toThrow()
    expect(stream.disconnect).toHaveBeenCalledOnce()

    // The refusal is remembered: no read and no second stream.
    await store.ensureLevels()
    expect(getSourceInputs).toHaveBeenCalledOnce()
    expect(stream.useEventStream).toHaveBeenCalledOnce()
  })

  it('moves the gain at once and rolls it back when the bridge refuses', async () => {
    store.applyStatus(bridgeStatus())
    const row = store.status!.inputs[0]!
    vi.mocked(setActiveVolume).mockRejectedValue(problem(422, 'input_unavailable'))
    // The reconciling read fails too: the rollback, not a fresh snapshot, is
    // what must put the old gain back.
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))

    const command = store.setGain(80)

    expect(row.volume).toBe(80)
    await expect(command).rejects.toMatchObject({ code: 'input_unavailable' })
    expect(row.volume).toBe(40)
    expect(store.error).toMatchObject({ code: 'input_unavailable' })
  })

  it('moves mute at once and rolls it back when the bridge refuses', async () => {
    store.applyStatus(bridgeStatus())
    const row = store.status!.inputs[0]!
    vi.mocked(setActiveMute).mockRejectedValue(problem(409, 'input_unavailable'))
    vi.mocked(getStatus).mockRejectedValue(new Error('bridge unreachable'))

    const command = store.setMuted(true)

    expect(row.muted).toBe(true)
    await expect(command).rejects.toMatchObject({ code: 'input_unavailable' })
    expect(row.muted).toBe(false)
  })

  it('releases the active input and adopts the cleared snapshot', async () => {
    store.applyStatus(bridgeStatus())
    vi.mocked(getStatus).mockResolvedValue(
      bridgeStatus(sourceBlock({ active_input: null, inputs: [micInput({ active: false })] })) as never,
    )

    await store.release()

    expect(clearActiveSource).toHaveBeenCalledOnce()
    expect(store.status?.active_input).toBeNull()
  })
})
