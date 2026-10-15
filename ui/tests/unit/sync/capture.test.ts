import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { SyncRecorder, SyncCaptureError, SYNC_CAPTURE_ERROR_CODES, resolveWorkletUrl } from '@/sync/capture'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

class FakePort {
  onmessage: ((e: MessageEvent) => void) | null = null
  posted: unknown[] = []
  postMessage(m: unknown) {
    this.posted.push(m)
  }
}
class FakeNode {
  port = new FakePort()
  connect() {}
  disconnect() {}
}
class FakeContext {
  static initialState = 'running'
  state = FakeContext.initialState
  sampleRate = 48000
  currentTime = 0
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) }
  listeners: Record<string, () => void> = {}
  resumed = 0
  closed = false
  createMediaStreamSource() {
    return { connect() {}, disconnect() {} }
  }
  createGain() {
    return { gain: { value: 1 }, connect() {}, disconnect() {} }
  }
  get destination() {
    return {}
  }
  addEventListener(n: string, f: () => void) {
    this.listeners[n] = f
  }
  resume() {
    this.resumed++
    this.state = 'running'
    return Promise.resolve()
  }
  close() {
    this.closed = true
    this.state = 'closed'
    return Promise.resolve()
  }
}

let node: FakeNode
let trackStops: number
let track: { stop(): void; onended: (() => void) | null }
// Recording messages carry the id of the start they answer; default to the latest start.
const lastStartId = () =>
  (node.port.posted.filter((m) => (m as { cmd?: string }).cmd === 'start').at(-1) as { id?: number } | undefined)?.id
const send = (data: Record<string, unknown>) =>
  node.port.onmessage!({
    data: data.type === 'level' || 'id' in data ? data : { ...data, id: lastStartId() },
  } as MessageEvent)
const ctxOf = (r: SyncRecorder) => (r as unknown as { ctx: FakeContext }).ctx
const failWith = (name: string) =>
  (navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValue(
    Object.assign(new Error(name), { name }),
  )

beforeEach(() => {
  node = new FakeNode()
  trackStops = 0
  FakeContext.initialState = 'running'
  vi.stubGlobal('AudioContext', FakeContext)
  vi.stubGlobal(
    'AudioWorkletNode',
    // vitest 4: a constructible mock needs a function, not an arrow
    vi.fn(function () {
      return node
    }),
  )
  vi.stubGlobal('navigator', {
    mediaDevices: {
      getUserMedia: vi.fn().mockImplementation(async () => {
        track = {
          onended: null,
          stop() {
            trackStops++
          },
        }
        const tracks = [track]
        return { getTracks: () => tracks }
      }),
    },
  })
  vi.stubGlobal('isSecureContext', true)
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('SyncRecorder', () => {
  it('opens the microphone before the context and stamps recordings with the worklet clock', async () => {
    const order: string[] = []
    ;(navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      order.push('mic')
      return { getTracks: () => [] }
    })
    const OrigCtx = globalThis.AudioContext
    vi.stubGlobal(
      'AudioContext',
      class extends OrigCtx {
        constructor() {
          super()
          order.push('ctx')
        }
      },
    )
    const r = new SyncRecorder()
    await r.open()
    expect(order).toEqual(['mic', 'ctx'])
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      },
    })
    const p = r.record(0.01)
    expect(node.port.posted[0]).toMatchObject({ cmd: 'start', frames: 480 })
    send({ type: 'started', startFrame: 96000, sampleRate: 48000 })
    send({ type: 'chunk', data: new Float32Array(480).fill(0.25) })
    send({ type: 'done' })
    const cap = await p
    expect(cap.startTimeS).toBe(2)
    expect(cap.sampleRate).toBe(16000)
    expect(cap.pcm.length).toBe(160)
    expect(cap.pcm[cap.pcm.length - 1]).toBe(8192)
    expect(cap.epoch).toBe(0)
  })

  it('decimates a 44.1 kHz context by two and stamps with its own frame clock', async () => {
    const r = new SyncRecorder()
    await r.open()
    ctxOf(r).sampleRate = 44100
    const p = r.record(0.02)
    expect(node.port.posted[0]).toMatchObject({ cmd: 'start', frames: 882 })
    send({ type: 'started', startFrame: 44100 * 3 + 441, sampleRate: 44100 })
    send({ type: 'chunk', data: new Float32Array(500).fill(-0.5) })
    send({ type: 'chunk', data: new Float32Array(382).fill(-0.5) })
    send({ type: 'done' })
    const cap = await p
    expect(cap.sampleRate).toBe(22050)
    expect(cap.startTimeS).toBeCloseTo(3.01, 9)
    expect(cap.pcm.length).toBe(441)
    expect(cap.pcm[440]).toBe(-16384)
  })

  it('reports the level from the worklet, and from the recording while it runs', async () => {
    const r = new SyncRecorder()
    expect(r.level()).toBe(0)
    await r.open()
    send({ type: 'level', rms: 0.3 })
    expect(r.level()).toBe(0.3)
    const p = r.record(0.01)
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    send({ type: 'chunk', data: new Float32Array(480).fill(0.5) })
    expect(r.level()).toBeCloseTo(0.5, 6)
    send({ type: 'done' })
    await p
  })

  it('flags the epoch when the context stops running', async () => {
    const r = new SyncRecorder()
    await r.open()
    const ctx = ctxOf(r)
    ctx.state = 'suspended'
    ctx.listeners['statechange']!()
    expect(r.epochInvalidated).toBe(true)
  })

  it('starts a new epoch on resume and tells listeners about the break', async () => {
    const r = new SyncRecorder()
    await r.open()
    const seen: number[] = []
    r.onEpochInvalidated((epoch) => seen.push(epoch))
    const ctx = ctxOf(r)
    ctx.state = 'interrupted'
    ctx.listeners['statechange']!()
    expect(seen).toEqual([0])
    await expect(r.record(1)).rejects.toMatchObject({ code: 'interrupted' })
    await r.resume()
    expect(ctx.resumed).toBe(1)
    expect(r.epochInvalidated).toBe(false)
    expect(r.epoch).toBe(1)
    const p = r.record(0.01)
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    send({ type: 'done' })
    expect((await p).epoch).toBe(1)
  })

  it('keeps the epoch when resume is called on a running context', async () => {
    const r = new SyncRecorder()
    await r.open()
    await r.resume()
    expect(r.epoch).toBe(0)
  })

  it('fails a recording in progress when the context is interrupted', async () => {
    const r = new SyncRecorder()
    await r.open()
    const p = r.record(1)
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    const ctx = ctxOf(r)
    ctx.state = 'suspended'
    ctx.listeners['statechange']!()
    await expect(p).rejects.toMatchObject({ code: 'interrupted' })
  })

  it('resumes a context that starts suspended (iOS) inside open', async () => {
    FakeContext.initialState = 'suspended'
    const r = new SyncRecorder()
    await r.open()
    expect(ctxOf(r).resumed).toBe(1)
    expect(r.epochInvalidated).toBe(false)
    expect(r.epoch).toBe(0)
  })

  it('maps a denied permission and a missing microphone to coded errors', async () => {
    failWith('NotAllowedError')
    const denied = await new SyncRecorder().open().catch((e: unknown) => e)
    expect(denied).toBeInstanceOf(SyncCaptureError)
    expect(denied).toMatchObject({
      code: 'permission',
      messageKey: 'sync.capture.errors.permission',
    })
    failWith('NotFoundError')
    await expect(new SyncRecorder().open()).rejects.toMatchObject({
      code: 'no_microphone',
    })
    failWith('NotReadableError')
    await expect(new SyncRecorder().open()).rejects.toMatchObject({
      code: 'mic_busy',
    })
  })

  it('refuses to open where capture is not supported', async () => {
    vi.stubGlobal('isSecureContext', false)
    expect(SyncRecorder.supported()).toBe(false)
    await expect(new SyncRecorder().open()).rejects.toMatchObject({
      code: 'unsupported',
    })
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
  })

  it('reports support in a secure context with the audio APIs present', () => {
    expect(SyncRecorder.supported()).toBe(true)
  })

  it('stops the microphone if the worklet module fails to load', async () => {
    const OrigCtx = globalThis.AudioContext
    vi.stubGlobal(
      'AudioContext',
      class extends OrigCtx {
        constructor() {
          super()
          this.audioWorklet.addModule = vi.fn().mockRejectedValue(new Error('blocked'))
        }
      },
    )
    await expect(new SyncRecorder().open()).rejects.toMatchObject({
      code: 'worklet',
    })
    expect(trackStops).toBe(1)
  })

  it('refuses a second recording while one runs, and recording before open', async () => {
    await expect(new SyncRecorder().record(1)).rejects.toMatchObject({
      code: 'not_open',
    })
    const r = new SyncRecorder()
    await r.open()
    const first = r.record(1)
    await expect(r.record(1)).rejects.toMatchObject({ code: 'busy' })
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    send({ type: 'done' })
    await first
  })

  it('drops messages from an interrupted recording that arrive after the next one started', async () => {
    const r = new SyncRecorder()
    await r.open()
    const first = r.record(1).catch((e: unknown) => e)
    const oldId = lastStartId()
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    const ctx = ctxOf(r)
    ctx.state = 'suspended'
    ctx.listeners['statechange']!()
    expect(await first).toMatchObject({ code: 'interrupted' })
    expect(node.port.posted.at(-1)).toEqual({ cmd: 'stop' })
    await r.resume()
    const p = r.record(0.01)
    expect(lastStartId()).not.toBe(oldId)
    send({ type: 'chunk', id: oldId, data: new Float32Array(480).fill(0.9) })
    send({ type: 'done', id: oldId })
    send({ type: 'started', startFrame: 4800, sampleRate: 48000 })
    send({ type: 'chunk', data: new Float32Array(480).fill(0.25) })
    send({ type: 'done' })
    const cap = await p
    expect(cap.startTimeS).toBeCloseTo(0.1, 9)
    expect(cap.pcm.length).toBe(160)
    expect(Math.max(...cap.pcm)).toBe(8192)
  })

  it('tells the worklet to stop when a recording times out', async () => {
    vi.useFakeTimers()
    const r = new SyncRecorder()
    await r.open()
    const p = r.record(1).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(7000)
    expect(await p).toMatchObject({ code: 'timeout' })
    expect(node.port.posted.at(-1)).toEqual({ cmd: 'stop' })
  })

  it('starts a fresh, valid epoch when reopened after close', async () => {
    const r = new SyncRecorder()
    await r.open()
    const ctx = ctxOf(r)
    ctx.state = 'interrupted'
    ctx.listeners['statechange']!()
    r.close()
    await r.open()
    expect(r.epochInvalidated).toBe(false)
    expect(r.epoch).toBe(1)
    const p = r.record(0.01)
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    send({ type: 'done' })
    expect((await p).epoch).toBe(1)
  })

  it('fails the recording when the microphone goes away and reopens on the next open', async () => {
    const r = new SyncRecorder()
    await r.open()
    const p = r.record(1)
    track.onended!()
    await expect(p).rejects.toMatchObject({ code: 'mic_lost' })
    expect(r.needsReopen).toBe(true)
    await expect(r.record(1)).rejects.toMatchObject({ code: 'mic_lost' })
    const oldCtx = ctxOf(r)
    await r.open()
    expect(oldCtx.closed).toBe(true)
    expect(ctxOf(r)).not.toBe(oldCtx)
    expect(r.needsReopen).toBe(false)
    expect(r.epoch).toBe(1)
  })

  it('shares one open between concurrent callers', async () => {
    const r = new SyncRecorder()
    await Promise.all([r.open(), r.open()])
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1)
  })

  it('releases the microphone and the context if building the graph throws', async () => {
    let made: FakeContext | null = null
    const OrigCtx = globalThis.AudioContext
    vi.stubGlobal(
      'AudioContext',
      class extends OrigCtx {
        constructor() {
          super()
          made = this as unknown as FakeContext
        }
      },
    )
    vi.stubGlobal(
      'AudioWorkletNode',
      vi.fn(function () {
        throw new Error('InvalidStateError')
      }),
    )
    await expect(new SyncRecorder().open()).rejects.toBeInstanceOf(SyncCaptureError)
    expect(trackStops).toBe(1)
    expect(made!.closed).toBe(true)
  })

  it('does not hang open when the context will not resume without a tap', async () => {
    vi.useFakeTimers()
    FakeContext.initialState = 'suspended'
    const OrigCtx = globalThis.AudioContext
    vi.stubGlobal(
      'AudioContext',
      class extends OrigCtx {
        resume() {
          return new Promise<void>(() => {})
        }
      },
    )
    const r = new SyncRecorder()
    let opened = false
    void r.open().then(() => (opened = true))
    await vi.advanceTimersByTimeAsync(2000)
    expect(opened).toBe(true)
    expect(r.needsResume).toBe(true)
    await expect(r.record(1)).rejects.toMatchObject({ code: 'interrupted' })
  })

  it('smooths the level over about 125 ms while recording', async () => {
    const r = new SyncRecorder()
    await r.open()
    const p = r.record(1)
    send({ type: 'started', startFrame: 0, sampleRate: 48000 })
    for (let i = 0; i < 47; i++) send({ type: 'chunk', data: new Float32Array(128).fill(0.5) })
    send({ type: 'chunk', data: new Float32Array(128) })
    expect(r.level()).toBeGreaterThan(0.45)
    for (let i = 0; i < 60; i++) send({ type: 'chunk', data: new Float32Array(128) })
    expect(r.level()).toBe(0)
    r.close()
    await p.catch(() => {})
  })

  it('gives up when the worklet never finishes', async () => {
    vi.useFakeTimers()
    const r = new SyncRecorder()
    await r.open()
    const p = r.record(1).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(7000)
    expect(await p).toMatchObject({ code: 'timeout' })
  })

  it('releases the microphone and the context on close', async () => {
    const r = new SyncRecorder()
    await r.open()
    const ctx = ctxOf(r)
    const p = r.record(1)
    r.close()
    expect(trackStops).toBe(1)
    expect(ctx.closed).toBe(true)
    await expect(p).rejects.toMatchObject({ code: 'not_open' })
  })
})

describe('resolveWorkletUrl', () => {
  const origin = 'https://ha.local:8123'
  it('pins a built asset under the ingress base, whatever page the app runs on', () => {
    expect(
      resolveWorkletUrl(
        'https://ha.local:8123/api/hassio_ingress/tok/sync/assets/sync-capture.worklet-abc123.js',
        '/api/hassio_ingress/tok',
        origin,
      ),
    ).toBe('https://ha.local:8123/api/hassio_ingress/tok/assets/sync-capture.worklet-abc123.js')
    expect(resolveWorkletUrl('https://ha.local:8123/sync/assets/sync-capture.worklet-abc.js', '', origin)).toBe(
      'https://ha.local:8123/assets/sync-capture.worklet-abc.js',
    )
  })
  it('leaves a dev-server URL alone', () => {
    const dev = 'http://localhost:5173/src/sync/sync-capture.worklet.ts?worker_file&type=module'
    expect(resolveWorkletUrl(dev, '', 'http://localhost:5173')).toBe(dev)
  })
})

describe('capture error messages', () => {
  it('has an English and a Russian text for every error code', () => {
    const lookup = (msgs: unknown, key: string) =>
      key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown> | undefined)?.[k], msgs)
    for (const code of SYNC_CAPTURE_ERROR_CODES) {
      const key = new SyncCaptureError(code).messageKey
      expect(typeof lookup(en, key), `en ${key}`).toBe('string')
      expect(typeof lookup(ru, key), `ru ${key}`).toBe('string')
    }
  })
})
