import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

import { getHardware } from '@/api/hardware'
import { useHardwareStore } from '@/stores/hardware'

/**
 * The store is exercised against the real ``useEventStream`` here: the stream
 * swap and the backoff live in that composable, so a mocked stream would hide
 * exactly the bug these tests guard (a refused typed URL retried forever).
 */
class FakeEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  static instances: FakeEventSource[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }

  addEventListener() {}

  close() {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  open() {
    this.readyState = FakeEventSource.OPEN
    this.onopen?.()
  }

  failClosed() {
    this.readyState = FakeEventSource.CLOSED
    this.onerror?.()
  }
}

const instances = () => FakeEventSource.instances
const defaultSource = () => instances().find((source) => !source.url.includes('hardware'))!
const typedSource = () => instances().find((source) => source.url.includes('hardware'))!

const snapshot = () => ({
  as_of: '2026-10-31T11:02:13Z',
  stale_after_s: 2,
  stale: false,
  capabilities: { bluetooth: true, dbus: true, audio_server: 'pulseaudio', capture: false, default_sink: true, reasons: {} },
  controllers: [],
  bluetooth_devices: [],
  degraded: [],
})

let store: ReturnType<typeof useHardwareStore> | null = null

describe('useHardwareStore event streams', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
  })

  afterEach(() => {
    store?.disconnect()
    store = null
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps the status/job stream when the bridge refuses the hardware type', async () => {
    store = useHardwareStore()
    store.connect()
    await flushPromises()

    expect(instances()).toHaveLength(2)
    const working = defaultSource()
    const typed = typedSource()
    working.open()
    typed.failClosed()
    await flushPromises()

    // The refused URL is closed once and never retried with backoff.
    expect(typed.closed).toBe(true)
    expect(working.closed).toBe(false)
    expect(working.readyState).toBe(FakeEventSource.OPEN)

    await vi.advanceTimersByTimeAsync(60_000)
    expect(instances()).toHaveLength(2)
    expect(working.closed).toBe(false)
  })

  it('retires the status/job probe once the typed stream opens', async () => {
    store = useHardwareStore()
    store.connect()
    await flushPromises()

    const probe = defaultSource()
    const typed = typedSource()
    probe.open()
    typed.open()
    await flushPromises()

    expect(probe.closed).toBe(true)
    expect(typed.closed).toBe(false)
  })

  it('opens no typed stream and adds no listener when the read resolves after leaving', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(getHardware).mockReturnValue(new Promise((r) => (resolve = r)) as never)
    const addDocument = vi.spyOn(document, 'addEventListener')
    const addWindow = vi.spyOn(window, 'addEventListener')

    store = useHardwareStore()
    store.connect()
    expect(instances()).toHaveLength(1)

    store.disconnect()
    expect(defaultSource().closed).toBe(true)
    addDocument.mockClear()
    addWindow.mockClear()

    resolve(snapshot())
    await flushPromises()

    expect(instances()).toHaveLength(1)
    expect(store.snapshot).toBeNull()
    expect(addDocument).not.toHaveBeenCalled()
    expect(addWindow).not.toHaveBeenCalled()
  })
})
