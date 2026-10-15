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
import { useIngress } from "@/composables/useIngress";
import { decimationFactor, lowpassDecimate, toInt16 } from "./decimate";
import workletAsset from "./sync-capture.worklet.ts?worker&url";

/** How long to wait after a solo/mute change before recording (stage 0 default; tunable). */
export const SOLO_SETTLE_MS = 800;

/** Extra time past a recording's length before it is given up as stuck. */
const RECORD_GRACE_MS = 5000;

export interface Capture {
  pcm: Int16Array;
  sampleRate: number;
  startTimeS: number;
  /** The clock epoch the capture was recorded in; captures compare only within one epoch. */
  epoch: number;
}

export const SYNC_CAPTURE_ERROR_CODES = [
  "unsupported",
  "permission",
  "no_microphone",
  "mic_busy",
  "worklet",
  "not_open",
  "busy",
  "interrupted",
  "timeout",
  "failed",
] as const;
export type SyncCaptureErrorCode = (typeof SYNC_CAPTURE_ERROR_CODES)[number];

export class SyncCaptureError extends Error {
  readonly code: SyncCaptureErrorCode;
  constructor(code: SyncCaptureErrorCode, detail?: string) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "SyncCaptureError";
    this.code = code;
  }
  /** i18n key of the user-facing message. */
  get messageKey(): string {
    return `sync.capture.errors.${this.code}`;
  }
}

function micError(e: unknown): SyncCaptureError {
  const name = (e as { name?: string } | null)?.name ?? "";
  const detail = e instanceof Error ? e.message : String(e);
  if (name === "NotAllowedError" || name === "SecurityError")
    return new SyncCaptureError("permission", detail);
  if (name === "NotFoundError" || name === "OverconstrainedError")
    return new SyncCaptureError("no_microphone", detail);
  if (name === "NotReadableError" || name === "AbortError")
    return new SyncCaptureError("mic_busy", detail);
  return new SyncCaptureError("failed", detail);
}

/**
 * Where the built worklet lives. Vite gives a URL relative to the running chunk, which
 * on a reloaded nested page (`/sync/<group>`) carries that page's path; a built asset is
 * pinned to `<ingress base>/assets/<file>` instead. Dev-server URLs pass through.
 */
export function resolveWorkletUrl(
  viteUrl: string,
  basePath: string,
  origin: string,
): string {
  const url = new URL(viteUrl, origin);
  const at = url.pathname.lastIndexOf("/assets/");
  if (at < 0) return url.href;
  return `${origin}${basePath}${url.pathname.slice(at)}`;
}

export function workletUrl(): string {
  return resolveWorkletUrl(
    new URL(workletAsset, import.meta.url).href,
    useIngress().basePath,
    window.location.origin,
  );
}

interface Pending {
  frames: number;
  factor: number;
  state: { acc: number[]; phase: number };
  parts: Float32Array[];
  startFrame: number | null;
  rate: number;
  epoch: number;
  timer: ReturnType<typeof setTimeout>;
  resolve: (c: Capture) => void;
  reject: (e: SyncCaptureError) => void;
}

type WorkletMessage =
  | { type: "level"; rms: number }
  | { type: "started"; startFrame: number; sampleRate: number }
  | { type: "chunk"; data: Float32Array }
  | { type: "done" };

export class SyncRecorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private pending: Pending | null = null;
  private lastLevel = 0;
  private _epoch = 0;
  private _invalidated = false;
  private listeners = new Set<(epoch: number) => void>();

  static supported(): boolean {
    return (
      globalThis.isSecureContext === true &&
      typeof navigator !== "undefined" &&
      typeof navigator.mediaDevices?.getUserMedia === "function" &&
      typeof globalThis.AudioContext === "function" &&
      typeof globalThis.AudioWorkletNode === "function"
    );
  }

  /** Current clock epoch: bumped by `resume()` after the context stopped running. */
  get epoch(): number {
    return this._epoch;
  }

  /** True once the context left 'running': captures from now on would not share the old clock. */
  get epochInvalidated(): boolean {
    return this._invalidated;
  }

  /** Called with the broken epoch when the context stops running. Returns an unsubscribe. */
  onEpochInvalidated(cb: (epoch: number) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  /** Must be called from a user gesture (iOS will not start audio otherwise). */
  async open(): Promise<void> {
    if (this.ctx) return;
    if (!SyncRecorder.supported()) throw new SyncCaptureError("unsupported");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
        },
      });
    } catch (e) {
      throw micError(e);
    }
    const ctx = new AudioContext();
    try {
      await ctx.audioWorklet.addModule(workletUrl());
    } catch (e) {
      for (const t of stream.getTracks()) t.stop();
      void ctx.close();
      throw new SyncCaptureError(
        "worklet",
        e instanceof Error ? e.message : String(e),
      );
    }
    this.ctx = ctx;
    this.stream = stream;
    this.source = ctx.createMediaStreamSource(stream);
    this.node = new AudioWorkletNode(ctx, "sync-capture");
    this.node.port.onmessage = (e: MessageEvent) =>
      this.onMessage(e.data as WorkletMessage);
    // A silent path to the destination keeps the graph — and so the worklet — pulled.
    this.sink = ctx.createGain();
    this.sink.gain.value = 0;
    this.source.connect(this.node);
    this.node.connect(this.sink);
    this.sink.connect(ctx.destination);
    ctx.addEventListener("statechange", () => this.onStateChange());
    if (ctx.state !== "running") await ctx.resume();
  }

  /**
   * Resume a stopped context (from a user gesture). If the clock had stopped, a new
   * epoch begins: earlier captures no longer line up with new ones.
   */
  async resume(): Promise<void> {
    const ctx = this.ctx;
    if (!ctx) throw new SyncCaptureError("not_open");
    if (ctx.state !== "running") await ctx.resume();
    if (this._invalidated && ctx.state === "running") {
      this._invalidated = false;
      this._epoch++;
    }
  }

  /** Records `durationS` seconds, decimated to ≥ 16 kHz, stamped with the context's frame clock. */
  record(durationS: number): Promise<Capture> {
    const ctx = this.ctx;
    const node = this.node;
    if (!ctx || !node) return Promise.reject(new SyncCaptureError("not_open"));
    if (this.pending) return Promise.reject(new SyncCaptureError("busy"));
    if (this._invalidated || ctx.state !== "running")
      return Promise.reject(new SyncCaptureError("interrupted"));
    const rate = ctx.sampleRate;
    const frames = Math.round(durationS * rate);
    return new Promise<Capture>((resolve, reject) => {
      this.pending = {
        frames,
        factor: decimationFactor(rate),
        state: { acc: [], phase: 0 },
        parts: [],
        startFrame: null,
        rate,
        epoch: this._epoch,
        timer: setTimeout(
          () => this.fail(new SyncCaptureError("timeout")),
          durationS * 1000 + RECORD_GRACE_MS,
        ),
        resolve,
        reject,
      };
      node.port.postMessage({ cmd: "start", frames });
    });
  }

  /** Last RMS (0..1) from the worklet while idle, or of the latest block while recording. */
  level(): number {
    return this.lastLevel;
  }

  close(): void {
    this.fail(new SyncCaptureError("not_open"));
    if (this.node) this.node.port.onmessage = null;
    this.source?.disconnect();
    this.node?.disconnect();
    this.sink?.disconnect();
    for (const t of this.stream?.getTracks() ?? []) t.stop();
    void this.ctx?.close();
    this.ctx = this.stream = this.source = this.node = this.sink = null;
    this.lastLevel = 0;
  }

  private onStateChange(): void {
    if (!this.ctx || this.ctx.state === "running" || this._invalidated) return;
    this._invalidated = true;
    this.fail(new SyncCaptureError("interrupted"));
    for (const cb of this.listeners) cb(this._epoch);
  }

  private fail(err: SyncCaptureError): void {
    const p = this.pending;
    if (!p) return;
    this.pending = null;
    clearTimeout(p.timer);
    p.reject(err);
  }

  private onMessage(msg: WorkletMessage): void {
    if (msg.type === "level") {
      this.lastLevel = msg.rms;
      return;
    }
    const p = this.pending;
    if (!p) return;
    if (msg.type === "started") {
      p.startFrame = msg.startFrame;
      p.rate = msg.sampleRate;
    } else if (msg.type === "chunk") {
      let sum = 0;
      for (const v of msg.data) sum += v * v;
      if (msg.data.length) this.lastLevel = Math.sqrt(sum / msg.data.length);
      p.parts.push(lowpassDecimate(msg.data, p.factor, p.state));
    } else if (msg.type === "done") {
      this.pending = null;
      clearTimeout(p.timer);
      if (p.startFrame === null) {
        p.reject(new SyncCaptureError("failed", "no start frame"));
        return;
      }
      let n = 0;
      for (const part of p.parts) n += part.length;
      const all = new Float32Array(n);
      let o = 0;
      for (const part of p.parts) {
        all.set(part, o);
        o += part.length;
      }
      p.resolve({
        pcm: toInt16(all),
        sampleRate: p.rate / p.factor,
        startTimeS: p.startFrame / p.rate,
        epoch: p.epoch,
      });
    }
  }
}
