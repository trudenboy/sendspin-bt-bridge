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

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: '2026-10-31T11:02:13Z',
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

let store: ReturnType<typeof useHardwareStore>

describe('useHardwareStore: audio', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
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
