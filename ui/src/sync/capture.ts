/**
 * Browser microphone capture for group sync measurements.
 *
 * One AudioContext lives for the whole tuning session; every recording is stamped
 * with that context's own frame clock (the worklet's `currentFrame / sampleRate`),
 * so recordings within one epoch share a time base. If the context stops running
 * (suspended, iOS "interrupted", closed) the frame clock stops while real time goes
 * on: the epoch is invalidated, and `resume()` — called from a user gesture — starts
 * a new one. Captures carry their epoch, so the wizard can redo the steps recorded
 * in an older one. Recordings stay in memory only.
 */
import { useIngress } from '@/composables/useIngress'
import { decimationFactor, lowpassDecimate, toInt16 } from './decimate'
import workletAsset from './sync-capture.worklet.ts?worker&url'

/** How long to wait after a solo/mute change before recording (stage 0 default; tunable). */
export const SOLO_SETTLE_MS = 800

/** Extra time past a recording's length before it is given up as stuck. */
const RECORD_GRACE_MS = 5000

/** How long open()/resume() wait for the context to start before leaving it to a tap. */
const RESUME_TIMEOUT_MS = 1000

/** Window of the level meter: RMS over about the last 125 ms, while idle and while recording. */
const LEVEL_WINDOW_S = 0.125

export interface Capture {
  pcm: Int16Array
  sampleRate: number
  startTimeS: number
  /** The clock epoch the capture was recorded in; captures compare only within one epoch. */
  epoch: number
}

export const SYNC_CAPTURE_ERROR_CODES = [
  'unsupported',
  'permission',
  'no_microphone',
  'mic_busy',
  'worklet',
  'not_open',
  'busy',
  'interrupted',
  'timeout',
  'mic_lost',
  'failed',
] as const
export type SyncCaptureErrorCode = (typeof SYNC_CAPTURE_ERROR_CODES)[number]

export class SyncCaptureError extends Error {
  readonly code: SyncCaptureErrorCode
  constructor(code: SyncCaptureErrorCode, detail?: string) {
    super(detail ? `${code}: ${detail}` : code)
    this.name = 'SyncCaptureError'
    this.code = code
  }
  /** i18n key of the user-facing message. */
  get messageKey(): string {
    return `sync.capture.errors.${this.code}`
  }
}

function micError(e: unknown): SyncCaptureError {
  const name = (e as { name?: string } | null)?.name ?? ''
  const detail = e instanceof Error ? e.message : String(e)
  if (name === 'NotAllowedError' || name === 'SecurityError') return new SyncCaptureError('permission', detail)
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return new SyncCaptureError('no_microphone', detail)
  if (name === 'NotReadableError' || name === 'AbortError') return new SyncCaptureError('mic_busy', detail)
  return new SyncCaptureError('failed', detail)
}

/**
 * Where the built worklet lives. Vite gives a URL relative to the running chunk, which
 * on a reloaded nested page (`/sync/<group>`) carries that page's path; a built asset is
 * pinned to `<ingress base>/assets/<file>` instead. Dev-server URLs pass through.
 */
export function resolveWorkletUrl(viteUrl: string, basePath: string, origin: string): string {
  const url = new URL(viteUrl, origin)
  const at = url.pathname.lastIndexOf('/assets/')
  if (at < 0) return url.href
  return `${origin}${basePath}${url.pathname.slice(at)}`
}

export function workletUrl(): string {
  return resolveWorkletUrl(new URL(workletAsset, import.meta.url).href, useIngress().basePath, window.location.origin)
}

interface Pending {
  id: number
  frames: number
  factor: number
  state: { acc: number[]; phase: number }
  parts: Float32Array[]
  startFrame: number | null
  rate: number
  epoch: number
  timer: ReturnType<typeof setTimeout>
  /** Energy of recent chunks, oldest first, for the smoothed level meter. */
  levelChunks: { sumSq: number; n: number }[]
  levelSumSq: number
  levelN: number
  resolve: (c: Capture) => void
  reject: (e: SyncCaptureError) => void
}

type WorkletMessage =
  | { type: 'level'; rms: number }
  | { type: 'started'; id: number; startFrame: number; sampleRate: number }
  | { type: 'chunk'; id: number; data: Float32Array }
  | { type: 'done'; id: number }

const settleWithin = (p: Promise<unknown>, ms: number) =>
  Promise.race([p.catch(() => undefined), new Promise((r) => setTimeout(r, ms))])

export class SyncRecorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private node: AudioWorkletNode | null = null
  private sink: GainNode | null = null
  private pending: Pending | null = null
  private opening: Promise<void> | null = null
  /** Bumped by `close()`: an open started before it must not take hold of anything. */
  private generation = 0
  private nextId = 1
  private lastLevel = 0
  private _epoch = 0
  private everOpened = false
  private _invalidated = false
  private _micLost = false
  private listeners = new Set<(epoch: number) => void>()

  static supported(): boolean {
    return (
      globalThis.isSecureContext === true &&
      typeof navigator !== 'undefined' &&
      typeof navigator.mediaDevices?.getUserMedia === 'function' &&
      typeof globalThis.AudioContext === 'function' &&
      typeof globalThis.AudioWorkletNode === 'function'
    )
  }

  /**
   * Current clock epoch. It changes whenever the frame clock may have jumped: on a
   * `resume()` after the context stopped running, and on every open after the first
   * (a new context counts frames from zero).
   */
  get epoch(): number {
    return this._epoch
  }

  /** True once the context left 'running': captures from now on would not share the old clock. */
  get epochInvalidated(): boolean {
    return this._invalidated
  }

  /** The context is open but not running (iOS without a tap, or interrupted): call `resume()` from a tap. */
  get needsResume(): boolean {
    return this.ctx !== null && this.ctx.state !== 'running'
  }

  /** The microphone track ended (unplugged, permission revoked): call `open()` again. */
  get needsReopen(): boolean {
    return this._micLost
  }

  /** Called with the broken epoch when the context stops running. Returns an unsubscribe. */
  onEpochInvalidated(cb: (epoch: number) => void): () => void {
    this.listeners.add(cb)
    return () => this.listeners.delete(cb)
  }

  /** Must be called from a user gesture (iOS will not start audio otherwise). */
  open(): Promise<void> {
    if (this.opening) return this.opening
    if (this.ctx && !this._micLost) return Promise.resolve()
    const opening: Promise<void> = this.doOpen().finally(() => {
      if (this.opening === opening) this.opening = null
    })
    this.opening = opening
    return opening
  }

  private async doOpen(): Promise<void> {
    if (this.ctx) this.close()
    const gen = this.generation
    if (!SyncRecorder.supported()) throw new SyncCaptureError('unsupported')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
      })
    } catch (e) {
      throw micError(e)
    }
    // Closed while the permission prompt was up: the microphone must not stay live.
    if (gen !== this.generation) {
      for (const t of stream.getTracks()) t.stop()
      throw new SyncCaptureError('not_open', 'closed while opening')
    }
    const ctx = new AudioContext()
    const release = () => {
      for (const t of stream.getTracks()) t.stop()
      void ctx.close()
    }
    try {
      await ctx.audioWorklet.addModule(workletUrl())
    } catch (e) {
      release()
      throw new SyncCaptureError('worklet', e instanceof Error ? e.message : String(e))
    }
    if (gen !== this.generation) {
      release()
      throw new SyncCaptureError('not_open', 'closed while opening')
    }
    let source: MediaStreamAudioSourceNode
    let node: AudioWorkletNode
    let sink: GainNode
    try {
      source = ctx.createMediaStreamSource(stream)
      node = new AudioWorkletNode(ctx, 'sync-capture')
      // A silent path to the destination keeps the graph — and so the worklet — pulled.
      sink = ctx.createGain()
      sink.gain.value = 0
      source.connect(node)
      node.connect(sink)
      sink.connect(ctx.destination)
    } catch (e) {
      release()
      throw new SyncCaptureError('failed', e instanceof Error ? e.message : String(e))
    }
    node.port.onmessage = (e: MessageEvent) => this.onMessage(e.data as WorkletMessage)
    for (const t of stream.getTracks()) t.onended = () => this.onMicLost(ctx)
    ctx.addEventListener('statechange', () => this.onStateChange(ctx))
    Object.assign(this, { ctx, stream, source, node, sink })
    if (this.everOpened) this._epoch++
    this.everOpened = true
    this._invalidated = false
    this._micLost = false
    this.lastLevel = 0
    // iOS: a context created after the await above may start suspended; never wait forever.
    if (ctx.state !== 'running') await settleWithin(ctx.resume(), RESUME_TIMEOUT_MS)
  }

  /**
   * Resume a stopped context (from a user gesture). If the clock had stopped, a new
   * epoch begins: earlier captures no longer line up with new ones. Check `needsResume`
   * afterwards: the browser may still refuse.
   */
  async resume(): Promise<void> {
    const ctx = this.ctx
    if (!ctx) throw new SyncCaptureError('not_open')
    if (ctx.state !== 'running') await settleWithin(ctx.resume(), RESUME_TIMEOUT_MS)
    if (this._invalidated && ctx.state === 'running') {
      this._invalidated = false
      this._epoch++
    }
  }

  /** Records `durationS` seconds, decimated to ≥ 16 kHz, stamped with the context's frame clock. */
  record(durationS: number): Promise<Capture> {
    const ctx = this.ctx
    const node = this.node
    if (!ctx || !node) return Promise.reject(new SyncCaptureError('not_open'))
    if (this._micLost) return Promise.reject(new SyncCaptureError('mic_lost'))
    if (this.pending) return Promise.reject(new SyncCaptureError('busy'))
    if (this._invalidated || ctx.state !== 'running') return Promise.reject(new SyncCaptureError('interrupted'))
    const rate = ctx.sampleRate
    const frames = Math.round(durationS * rate)
    const id = this.nextId++
    return new Promise<Capture>((resolve, reject) => {
      this.pending = {
        id,
        frames,
        factor: decimationFactor(rate),
        state: { acc: [], phase: 0 },
        parts: [],
        startFrame: null,
        rate,
        epoch: this._epoch,
        timer: setTimeout(() => this.fail(new SyncCaptureError('timeout')), durationS * 1000 + RECORD_GRACE_MS),
        levelChunks: [],
        levelSumSq: 0,
        levelN: 0,
        resolve,
        reject,
      }
      node.port.postMessage({ cmd: 'start', id, frames })
    })
  }

  /** RMS (0..1) over about the last 125 ms: from the worklet while idle, from the recording while it runs. */
  level(): number {
    return this.lastLevel
  }

  /** Releases the microphone and the context; an open still in progress is cancelled. */
  close(): void {
    this.generation++
    this.opening = null
    this.fail(new SyncCaptureError('not_open'))
    if (this.node) this.node.port.onmessage = null
    this.source?.disconnect()
    this.node?.disconnect()
    this.sink?.disconnect()
    for (const t of this.stream?.getTracks() ?? []) {
      t.onended = null
      t.stop()
    }
    void this.ctx?.close()
    this.ctx = this.stream = this.source = this.node = this.sink = null
    this.lastLevel = 0
  }

  private onStateChange(ctx: AudioContext): void {
    if (ctx !== this.ctx || ctx.state === 'running' || this._invalidated) return
    this._invalidated = true
    this.fail(new SyncCaptureError('interrupted'))
    for (const cb of this.listeners) cb(this._epoch)
  }

  private onMicLost(ctx: AudioContext): void {
    if (ctx !== this.ctx) return
    this._micLost = true
    this.fail(new SyncCaptureError('mic_lost'))
  }

  /** Give up the running recording, and stop the worklet so it sends nothing more for it. */
  private fail(err: SyncCaptureError): void {
    const p = this.pending
    if (!p) return
    this.pending = null
    clearTimeout(p.timer)
    this.node?.port.postMessage({ cmd: 'stop' })
    p.reject(err)
  }

  private trackLevel(p: Pending, data: Float32Array): void {
    let sumSq = 0
    for (const v of data) sumSq += v * v
    p.levelChunks.push({ sumSq, n: data.length })
    p.levelSumSq += sumSq
    p.levelN += data.length
    const window = p.rate * LEVEL_WINDOW_S
    while (p.levelChunks.length > 1 && p.levelN - p.levelChunks[0]!.n >= window) {
      const old = p.levelChunks.shift()!
      p.levelSumSq -= old.sumSq
      p.levelN -= old.n
    }
    if (p.levelN > 0) this.lastLevel = Math.sqrt(Math.max(0, p.levelSumSq) / p.levelN)
  }

  private onMessage(msg: WorkletMessage): void {
    if (msg.type === 'level') {
      if (!this.pending) this.lastLevel = msg.rms
      return
    }
    const p = this.pending
    // A message for another recording is left over from one already given up on.
    if (!p || msg.id !== p.id) return
    if (msg.type === 'started') {
      p.startFrame = msg.startFrame
      p.rate = msg.sampleRate
    } else if (msg.type === 'chunk') {
      this.trackLevel(p, msg.data)
      p.parts.push(lowpassDecimate(msg.data, p.factor, p.state))
    } else if (msg.type === 'done') {
      this.pending = null
      clearTimeout(p.timer)
      if (p.startFrame === null) {
        p.reject(new SyncCaptureError('failed', 'no start frame'))
        return
      }
      let n = 0
      for (const part of p.parts) n += part.length
      const all = new Float32Array(n)
      let o = 0
      for (const part of p.parts) {
        all.set(part, o)
        o += part.length
      }
      p.resolve({
        pcm: toInt16(all),
        sampleRate: p.rate / p.factor,
        startTimeS: p.startFrame / p.rate,
        epoch: p.epoch,
      })
    }
  }
}
