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
  setCardProfile: vi.fn(),
  setSinkPort: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { setCardProfile, setSinkPort } from '@/api/audio'
import { useHardwareStore } from '@/stores/hardware'

const CARD = 'alsa_card.pci-0000_00_1f.3'
const SINK = 'alsa_output.pci-0000_00_1f.3.analog-stereo'

const card = (overrides: Record<string, unknown> = {}) => ({
  name: CARD,
  driver: 'module-alsa-card',
  active_profile: 'output:analog-stereo',
  profile_count: 2,
  port_count: 1,
  endpoint_refs: [{ kind: 'sink', name: SINK }],
  managed_by: null,
  ...overrides,
})

const sink = (overrides: Record<string, unknown> = {}) => ({
  kind: 'sink',
  name: SINK,
  description: 'Built-in audio output',
  owner: { card: CARD, bluetooth: null, internal: null },
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
  cards: [card()],
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

const cardDetail = (overrides: Record<string, unknown> = {}) => ({
  ...card(),
  profiles: [
    { name: 'output:analog-stereo', description: 'Analog Stereo Output', available: true },
    { name: 'off', description: 'Off', available: true },
  ],
  ports: [{ name: 'analog-output-speaker', description: 'Speakers', available: true, active: true, profiles: [] }],
  endpoints: [sink()],
  as_of: '2026-10-31T11:02:14Z',
  stale: false,
  ...overrides,
})

const endpointDetail = (overrides: Record<string, unknown> = {}) => ({
  ...sink(),
  identifiers: {},
  ports: [{ name: 'analog-output-speaker', description: 'Speakers', available: true, active: true, profiles: [] }],
  monitor_of: null,
  as_of: '2026-10-31T11:02:14Z',
  stale: false,
  ...overrides,
})

const problem = (status: number, code: string, detail = 'Nope') => new ApiError({ status, code, detail })

let store: ReturnType<typeof useHardwareStore>

describe('useHardwareStore: card profiles and output ports', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    asOfTick = 0
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    store = useHardwareStore()
    store.connect()
    await flushPromises()
    vi.mocked(getHardware).mockClear()
  })

  afterEach(() => {
    store.disconnect()
  })

  it('applies the write answer and refreshes the audio section so an added output shows up', async () => {
    vi.mocked(setCardProfile).mockResolvedValue(cardDetail({ active_profile: 'off' }) as never)
    const added = 'alsa_output.pci-0000_00_1f.3.hdmi-stereo'
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        as_of: '2026-10-31T11:02:20.000Z',
        audio: audio({ cards: [card({ active_profile: 'off' })], sinks: [sink(), sink({ name: added })] }),
      }) as never,
    )

    const detail = await store.chooseCardProfile(store.audio.cards[0]!, 'off')

    expect(setCardProfile).toHaveBeenCalledWith(CARD, 'off')
    expect(detail?.active_profile).toBe('off')
    expect(store.audio.cards[0]!.active_profile).toBe('off')
    // A profile can add or remove outputs: the whole section is read after the
    // write, so the output list cannot stay on the pre-write shape.
    expect(store.audio.sinks.map((entry) => entry.name)).toContain(added)
    // The page card stays a summary: the detail's extra lists belong to the drawer.
    expect('profiles' in store.audio.cards[0]!).toBe(false)
    expect(getHardware).toHaveBeenCalledOnce()
  })

  it('replaces the sink with the write answer, including the new port state', async () => {
    vi.mocked(setSinkPort).mockResolvedValue(
      endpointDetail({ active_port: 'hdmi-output-0', active_port_available: 'not available' }) as never,
    )

    const detail = await store.chooseSinkPort(store.audio.sinks[0]!, 'hdmi-output-0')

    expect(setSinkPort).toHaveBeenCalledWith(SINK, 'hdmi-output-0')
    expect(detail?.active_port).toBe('hdmi-output-0')
    expect(store.audio.sinks[0]!.active_port).toBe('hdmi-output-0')
    expect(store.audio.sinks[0]!.active_port_available).toBe('not available')
    // A port belongs to the sink's existing card: no section-wide read is needed.
    expect(getHardware).not.toHaveBeenCalled()
  })

  it('does not let an in-flight read from before the write flip the port row back', async () => {
    vi.mocked(setSinkPort).mockResolvedValue(
      endpointDetail({
        active_port: 'hdmi-output-0',
        as_of: '2026-10-31T11:10:00.000Z',
        stale: false,
      }) as never,
    )

    await store.chooseSinkPort(store.audio.sinks[0]!, 'hdmi-output-0')
    // The poll was sent before the write and answers with the old port; its
    // timestamp is newer than the aggregate the page held, but older than the
    // write's own read.
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ as_of: '2026-10-31T11:05:00.000Z', audio: audio({ sinks: [sink()] }) }) as never,
    )
    await store.refresh()

    expect(store.audio.sinks[0]!.active_port).toBe('hdmi-output-0')
  })

  it('keeps the server code on a refusal and leaves the last read as it was', async () => {
    vi.mocked(setCardProfile).mockRejectedValue(problem(400, 'invalid_profile'))

    await expect(store.chooseCardProfile(store.audio.cards[0]!, 'nope')).rejects.toMatchObject({
      code: 'invalid_profile',
    })
    expect(store.audio.cards[0]!.active_profile).toBe('output:analog-stereo')

    vi.mocked(setSinkPort).mockRejectedValue(problem(400, 'invalid_port'))
    await expect(store.chooseSinkPort(store.audio.sinks[0]!, 'nope')).rejects.toMatchObject({ code: 'invalid_port' })
    expect(store.audio.sinks[0]!.active_port).toBe('analog-output-speaker')
  })

  it('refreshes the audio section when the write answers without a detail', async () => {
    vi.mocked(setCardProfile).mockResolvedValue(null as never)
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: audio({ cards: [card({ active_profile: 'off' })] }) }) as never)

    const detail = await store.chooseCardProfile(store.audio.cards[0]!, 'off')

    expect(detail).toBeNull()
    expect(getHardware).toHaveBeenCalledOnce()
    expect(store.audio.cards[0]!.active_profile).toBe('off')
  })
})
