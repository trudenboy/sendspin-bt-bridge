import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

/** Every useEventStream the store creates, in order. */
const streams = vi.hoisted(() => ({
  options: [] as Array<{
    types?: string[]
    onEvent: (event: { type: string; data: unknown }) => void
    onConnect?: () => void
    onDisconnect?: () => void
    onRejected?: () => void
  }>,
}))

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: (options: (typeof streams.options)[number]) => {
    streams.options.push(options)
    return { connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }
  },
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import { useHardwareStore } from '@/stores/hardware'

const controller = (id: string) => ({
  id,
  id_synthetic: false,
  mac: 'C0:FB:F9:62:D6:9D',
  name: 'hci0',
  alias: 'Living room dongle',
  powered: true,
  present: true,
  class_of_device: '0x000000',
  area: null,
  device_count: 1,
})

const device = (mac: string) => ({
  mac,
  name: 'ENEBY20',
  alias: 'ENEBY20',
  paired: true,
  bonded: true,
  trusted: true,
  connected: true,
  controller_macs: ['C0:FB:F9:62:D6:9D'],
  battery_pct: 80,
  rssi: null,
  transport: null,
  roles: [{ role: 'output', evidence: 'transport:active' }],
  player_id: 'eneby20',
  source_phone: false,
})

/** Every read is taken a second later: the store drops reads that are not newer. */
let asOfTick = 0
const nextAsOf = () => new Date(Date.UTC(2026, 9, 31, 11, 2, 12 + ++asOfTick)).toISOString()

const snapshot = (overrides: Record<string, unknown> = {}) => ({
  as_of: nextAsOf(),
  stale_after_s: 2,
  stale: false,
  capabilities: { bluetooth: true, dbus: true, audio_server: 'pulseaudio', capture: false, default_sink: true, reasons: {} },
  controllers: [controller('hci0')],
  bluetooth_devices: [device('FC:58:FA:EB:08:6C')],
  degraded: [],
  ...overrides,
})

const problem = (status: number, code: string) => new ApiError({ status, code, title: 'Nope' })

describe('useHardwareStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    streams.options.length = 0
    asOfTick = 0
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('connects with the default event types and loads the snapshot', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()

    expect(streams.options[0]!.types).toBeUndefined()
    expect(store.snapshot?.controllers.map((c) => c.id)).toEqual(['hci0'])
    expect(store.snapshot?.bluetooth_devices.map((d) => d.mac)).toEqual(['FC:58:FA:EB:08:6C'])
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('reconnects with hardware added once the aggregate reads', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()

    expect(streams.options).toHaveLength(2)
    expect(streams.options[1]!.types).toEqual(expect.arrayContaining(['status', 'job', 'hardware']))
  })

  it('stays on one stream and never asks for the type when the bridge has no route', async () => {
    vi.mocked(getHardware).mockRejectedValue(problem(404, 'not_found'))
    const store = useHardwareStore()
    store.connect()
    await flushPromises()

    expect(store.error).toBeTruthy()
    expect(streams.options).toHaveLength(1)

    // Even a later successful read (a bridge that grew the route) must not
    // mention a type this stream was told the server does not know.
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    await store.refresh()
    expect(streams.options).toHaveLength(1)
  })

  it('treats a 500 probe as final too', async () => {
    vi.mocked(getHardware).mockRejectedValue(problem(500, 'server_error'))
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    expect(streams.options).toHaveLength(1)
  })

  it('exposes a missing route as unsupported, but not a server error', async () => {
    vi.mocked(getHardware).mockRejectedValue(problem(404, 'not_found'))
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    expect(store.unsupported).toBe(true)

    vi.mocked(getHardware).mockRejectedValue(problem(500, 'server_error'))
    setActivePinia(createPinia())
    const other = useHardwareStore()
    await other.refresh()
    expect(other.unsupported).toBe(false)
  })

  it('retries the probe after a network failure instead of giving up', async () => {
    vi.mocked(getHardware).mockRejectedValue(problem(0, 'network_error'))
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    expect(streams.options).toHaveLength(1)

    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    await store.refresh()
    expect(streams.options).toHaveLength(2)
  })

  it('clears unsupported when a later read succeeds', async () => {
    vi.mocked(getHardware).mockRejectedValue(problem(404, 'not_found'))
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    expect(store.unsupported).toBe(true)

    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    await store.refresh()
    expect(store.unsupported).toBe(false)
    expect(store.snapshot).not.toBeNull()
  })

  it('shows a busy flag for a manual refresh while the poll stays quiet', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()

    let resolve!: (value: unknown) => void
    vi.mocked(getHardware).mockReturnValue(new Promise((r) => (resolve = r)) as never)
    const pending = store.refresh({ manual: true })
    expect(store.refreshing).toBe(true)
    expect(store.loading).toBe(false)

    resolve(snapshot() as never)
    await pending
    expect(store.refreshing).toBe(false)

    // The 5 s poll is not a manual refresh: no button flicker.
    const poll = store.refresh()
    expect(store.refreshing).toBe(false)
    await poll
  })

  it('polls every five seconds until disconnected', async () => {
    vi.useFakeTimers()
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    const store = useHardwareStore()
    store.connect()
    await vi.advanceTimersByTimeAsync(0)
    const calls = vi.mocked(getHardware).mock.calls.length

    await vi.advanceTimersByTimeAsync(5000)
    expect(vi.mocked(getHardware).mock.calls.length).toBe(calls + 1)

    store.disconnect()
    await vi.advanceTimersByTimeAsync(15000)
    expect(vi.mocked(getHardware).mock.calls.length).toBe(calls + 1)
  })

  it('applies hardware events from the stream', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()

    const next = snapshot({ as_of: '2026-10-31T11:03:00Z', stale: true })
    streams.options[1]!.onEvent({ type: 'hardware', data: next })
    expect(store.snapshot?.as_of).toBe('2026-10-31T11:03:00Z')
    expect(store.snapshot?.stale).toBe(true)
  })

  it('drops a stream snapshot that is not newer than the current read', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ stale: false }) as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    const current = store.snapshot!.as_of

    // An SSE read started before a write lands after it; it must not revert the page.
    const older = snapshot({
      as_of: new Date(Date.parse(current) - 1000).toISOString(),
      stale: true,
    })
    streams.options[1]!.onEvent({ type: 'hardware', data: older })

    expect(store.snapshot?.as_of).toBe(current)
    expect(store.snapshot?.stale).toBe(false)
  })

  it('drops a manual refresh whose read is not newer than the current one', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ stale: false }) as never)
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    const current = store.snapshot!

    // The cached read served after a failed refresh carries its old timestamp.
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ as_of: current.as_of, stale: true }) as never,
    )
    await store.refresh({ manual: true })

    expect(store.snapshot).toBe(current)
    expect(store.refreshing).toBe(false)
  })

  it('exposes the degraded sections and keeps the last snapshot on a failed poll', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ degraded: [{ section: 'controllers', code: 'dbus_unavailable', detail: 'No D-Bus' }] }) as never,
    )
    const store = useHardwareStore()
    store.connect()
    await flushPromises()
    expect(store.degraded.map((d) => d.code)).toEqual(['dbus_unavailable'])

    vi.mocked(getHardware).mockRejectedValue(problem(0, 'network_error'))
    await store.refresh()
    expect(store.error).toBeTruthy()
    expect(store.snapshot).not.toBeNull()
  })
})
