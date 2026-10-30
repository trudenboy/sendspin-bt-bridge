import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useEventStream } from '@/composables/useEventStream'

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
    this.readyState = 2
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
  failClosed() {
    this.readyState = 2
    this.onerror?.()
  }
}

const last = () => FakeEventSource.instances[FakeEventSource.instances.length - 1]
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

describe('useEventStream reconnect', () => {
  const streams: { disconnect: () => void }[] = []
  let fetchMock: ReturnType<typeof vi.fn>
  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    fetchMock = vi.fn().mockResolvedValue({ status: 200, ok: true })
    vi.stubGlobal('fetch', fetchMock)
    sessionStorage.clear()
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
  })
  afterEach(() => {
    streams.splice(0).forEach((x) => x.disconnect())
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function setup(extra: Partial<Parameters<typeof useEventStream>[0]> = {}) {
    const onPoll = vi.fn()
    const onConnect = vi.fn()
    const reload = vi.fn()
    const stream = useEventStream({ onEvent: vi.fn(), onPoll, onConnect, reload, pollInterval: 1000, ...extra })
    stream.connect()
    streams.push(stream)
    return { stream, onPoll, onConnect, reload }
  }

  it('a CLOSED source starts polling at once and reconnects by itself', async () => {
    const { onPoll, onConnect } = setup()
    last().open()
    expect(FakeEventSource.instances).toHaveLength(1)
    last().failClosed()
    vi.advanceTimersByTime(1000)
    expect(onPoll).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2000)
    await flush()
    expect(FakeEventSource.instances.length).toBeGreaterThan(1)
    onPoll.mockClear()
    last().open()
    expect(onConnect).toHaveBeenCalledTimes(2)
    expect(onPoll).toHaveBeenCalledTimes(1) // refreshed once on recovery
    vi.advanceTimersByTime(5000)
    expect(onPoll).toHaveBeenCalledTimes(1) // polling stopped
  })

  it('backs off between failed reconnects', async () => {
    setup()
    last().failClosed()
    fetchMock.mockResolvedValue({ status: 503, ok: false })
    await vi.advanceTimersByTimeAsync(1100)
    const before = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(1000)
    expect(fetchMock.mock.calls.length).toBe(before) // second wait is longer than 1 s
    await vi.advanceTimersByTimeAsync(2000)
    expect(fetchMock.mock.calls.length).toBeGreaterThan(before)
  })

  it('reconnects when the tab becomes visible or the network returns', async () => {
    setup()
    last().failClosed()
    const n = FakeEventSource.instances.length
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(FakeEventSource.instances.length).toBe(n + 1)
    last().failClosed()
    window.dispatchEvent(new Event('online'))
    await flush()
    expect(FakeEventSource.instances.length).toBe(n + 2)
  })

  it('reloads once when the probe answers 401, guarded for 60 s', async () => {
    const { reload } = setup()
    fetchMock.mockResolvedValue({ status: 401, ok: false })
    last().failClosed()
    await vi.advanceTimersByTimeAsync(1100)
    await flush()
    expect(reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(20000)
    expect(reload).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(60000)
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('does not reload on a plain 503 outside ingress', async () => {
    const { reload } = setup()
    fetchMock.mockResolvedValue({ status: 503, ok: false })
    last().failClosed()
    await vi.advanceTimersByTimeAsync(120000)
    expect(reload).not.toHaveBeenCalled()
  })

  it('stops reconnecting after disconnect', async () => {
    const { stream } = setup()
    last().failClosed()
    stream.disconnect()
    const n = FakeEventSource.instances.length
    await vi.advanceTimersByTimeAsync(60000)
    expect(FakeEventSource.instances.length).toBe(n)
  })
})
