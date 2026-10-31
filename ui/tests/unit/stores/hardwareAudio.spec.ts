import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/audio', () => ({
  getAudioSinks: vi.fn(),
  setSinkVolume: vi.fn(),
  setSinkMute: vi.fn(),
  setDefaultSink: vi.fn(),
  suspendSink: vi.fn(),
  resumeSink: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { resumeSink, setDefaultSink, setSinkMute, setSinkVolume, suspendSink } from '@/api/audio'
import { useHardwareStore } from '@/stores/hardware'

const SINK = 'alsa_output.pci-0000_00_1f.3.analog-stereo'

const sink = (overrides: Record<string, unknown> = {}) => ({
  kind: 'sink',
  name: SINK,
  description: 'Built-in audio output',
  owner: { card: 'alsa_card.pci-0000_00_1f.3', bluetooth: null, internal: null },
  state: 'running',
  sample_spec: 's16le 2ch 48000Hz',
  muted: false,
  volume: 60,
  in_use_by: null,
  active_port: 'analog-output-speaker',
  active_port_available: 'available',
  is_default: false,
  ...overrides,
})

const audio = (overrides: Record<string, unknown> = {}) => ({
  cards: [],
  sinks: [sink()],
  sources: [],
  ...overrides,
})

/** Every read is taken a second later: the store drops reads that are not newer. */
let asOfTick = 0
const nextAsOf = () => new Date(Date.UTC(2026, 9, 31, 11, 2, 12 + ++asOfTick)).toISOString()

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: nextAsOf(),
  stale_after_s: 2,
  stale: false,
  capabilities: {
    bluetooth: true,
    dbus: true,
    audio_server: 'pulseaudio',
    capture: false,
    default_sink: true,
    reasons: {},
  },
  controllers: [],
  bluetooth_devices: [],
  audio: audio(),
  degraded: [],
  ...overrides,
})

const state = (overrides: Record<string, unknown> = {}) => ({
  name: SINK,
  volume: 60,
  muted: false,
  state: 'running',
  ...overrides,
})

const problem = (status: number, code: string, detail = 'Nope') => new ApiError({ status, code, detail })

/** A write whose answer is released by the test, so two commands can overlap. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => (resolve = done))
  return { promise, resolve }
}

let store: ReturnType<typeof useHardwareStore>

describe('useHardwareStore: audio', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    asOfTick = 0
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(setSinkVolume).mockResolvedValue(state() as never)
    vi.mocked(setSinkMute).mockResolvedValue(state({ muted: true }) as never)
    vi.mocked(setDefaultSink).mockResolvedValue(state() as never)
    vi.mocked(suspendSink).mockResolvedValue(state({ state: 'suspended' }) as never)
    vi.mocked(resumeSink).mockResolvedValue(state({ state: 'idle' }) as never)
    store = useHardwareStore()
    store.connect()
    await flushPromises()
    vi.mocked(getHardware).mockClear()
  })

  afterEach(() => {
    store.disconnect()
  })

  it('normalises an absent audio field without claiming the host has no audio', async () => {
    const { audio: _absent, ...older } = snapshot()
    vi.mocked(getHardware).mockResolvedValue(older as never)

    await store.refresh()

    expect(store.audio).toEqual({ cards: [], sinks: [], sources: [] })
    expect(store.audioReported).toBe(false)
  })

  it('reports audio as present when the field is there, even when every list is empty', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: { cards: [], sinks: [], sources: [] } }) as never)

    await store.refresh()

    expect(store.audio).toEqual({ cards: [], sinks: [], sources: [] })
    expect(store.audioReported).toBe(true)
  })

  it('rethrows the server code and rolls an optimistic volume back when the write is refused', async () => {
    let reject!: (reason: unknown) => void
    vi.mocked(setSinkVolume).mockReturnValue(new Promise((_, fail) => (reject = fail)) as never)

    const call = store.setVolume(SINK, 85)
    // The slider moved before the round trip: the page must not wait for it.
    expect(store.audio.sinks[0]!.volume).toBe(85)

    reject(problem(409, 'not_supported_for_backend', 'The audio server did not accept a volume change.'))
    await expect(call).rejects.toMatchObject({ code: 'not_supported_for_backend' })

    expect(store.audio.sinks[0]!.volume).toBe(60)
  })

  it('patches the sink with the server answer when volume and mute succeed', async () => {
    vi.mocked(setSinkVolume).mockResolvedValue(state({ volume: 55 }) as never)
    vi.mocked(setSinkMute).mockResolvedValue(state({ muted: true, volume: 55 }) as never)

    await store.setVolume(SINK, 55)
    expect(store.audio.sinks[0]!.volume).toBe(55)

    await store.setMute(SINK, true)
    expect(store.audio.sinks[0]!.muted).toBe(true)
    expect(store.audio.sinks[0]!.volume).toBe(55)
  })

  it('patches the state after pause and refreshes after resume', async () => {
    await store.suspend(SINK)
    expect(store.audio.sinks[0]!.state).toBe('suspended')

    await store.resume(SINK)
    // The resume command's own state was read before the write; the aggregate is the truth.
    expect(getHardware).toHaveBeenCalledOnce()
    expect(resumeSink).toHaveBeenCalledWith(SINK)
  })

  it('keeps the newest volume answer when the responses arrive out of order', async () => {
    const first = deferred<unknown>()
    const second = deferred<unknown>()
    vi.mocked(setSinkVolume)
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never)

    const older = store.setVolume(SINK, 20)
    const newer = store.setVolume(SINK, 80)

    second.resolve(state({ volume: 80 }))
    await newer
    // The older request's answer lands after the newer one: it must not win.
    first.resolve(state({ volume: 20 }))
    await older

    expect(store.audio.sinks[0]!.volume).toBe(80)
  })

  it('does not let a volume answer re-mute a sink the user just muted', async () => {
    const volumeAnswer = deferred<unknown>()
    vi.mocked(setSinkVolume).mockReturnValueOnce(volumeAnswer.promise as never)
    vi.mocked(setSinkMute).mockResolvedValue(state({ muted: true, volume: 60 }) as never)

    // The volume read started before the mute; its answer is older than the mute.
    const volume = store.setVolume(SINK, 70)
    await store.setMute(SINK, true)
    volumeAnswer.resolve(state({ volume: 70, muted: false }))
    await volume

    expect(store.audio.sinks[0]!.muted).toBe(true)
    expect(store.audio.sinks[0]!.volume).toBe(70)
  })

  it('refreshes after a default write so the read model carries the badge', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ audio: audio({ sinks: [sink({ is_default: true })] }) }) as never,
    )

    await store.setDefault(SINK)

    expect(setDefaultSink).toHaveBeenCalledWith(SINK)
    expect(getHardware).toHaveBeenCalledOnce()
    expect(store.audio.sinks[0]!.is_default).toBe(true)

    vi.mocked(setDefaultSink).mockRejectedValue(problem(409, 'not_supported_for_backend'))
    await expect(store.setDefault(SINK)).rejects.toMatchObject({ code: 'not_supported_for_backend' })
    // A refused write leaves the last read exactly as it was.
    expect(store.audio.sinks[0]!.is_default).toBe(true)
  })
})
