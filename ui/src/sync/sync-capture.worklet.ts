/**
 * Microphone capture for group sync measurements, run in the AudioWorkletGlobalScope.
 *
 * Protocol — main → worklet: `{cmd: 'start', frames}`. Worklet → main: `{type: 'level', rms}`
 * about every 128 ms while idle; `{type: 'started', startFrame, sampleRate}` on the first
 * block after a start (startFrame is the context's own frame clock, `currentFrame`);
 * then `{type: 'chunk', data}` raw blocks and `{type: 'done'}` once `frames` were sent.
 * Decimation runs on the main thread, so this stays trivial.
 */
class SyncCaptureProcessor extends AudioWorkletProcessor {
  private remaining = 0;
  private started = false;
  private levelAcc = 0;
  private levelN = 0;

  constructor() {
    super();
    this.port.onmessage = (e: MessageEvent) => {
      const data = e.data as { cmd?: string; frames?: number } | null;
      if (data?.cmd === "start" && typeof data.frames === "number") {
        this.remaining = Math.max(0, Math.floor(data.frames));
        this.started = false;
      }
    };
  }

  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    if (this.remaining > 0) {
      if (!this.started) {
        this.started = true;
        this.port.postMessage({
          type: "started",
          startFrame: currentFrame,
          sampleRate,
        });
      }
      const n = Math.min(ch.length, this.remaining);
      this.port.postMessage({ type: "chunk", data: ch.slice(0, n) });
      this.remaining -= n;
      if (this.remaining === 0) this.port.postMessage({ type: "done" });
    } else {
      for (const v of ch) this.levelAcc += v * v;
      this.levelN += ch.length;
      if (this.levelN >= sampleRate / 8) {
        this.port.postMessage({
          type: "level",
          rms: Math.sqrt(this.levelAcc / this.levelN),
        });
        this.levelAcc = 0;
        this.levelN = 0;
      }
    }
    return true;
  }
}

registerProcessor("sync-capture", SyncCaptureProcessor);
