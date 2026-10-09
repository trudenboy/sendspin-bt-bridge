/**
 * Recording one speaker's click track through the browser microphone.
 *
 * The microphone runs with the browser's voice processing off (echo
 * cancellation and noise suppression would erase the clicks); the click is
 * started on the speaker 250 ms into the recording and 2.5 s are kept,
 * downsampled to ~8 kHz, which is all the server's envelope comparison uses.
 */
import { playClick } from '@/api/calibration'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function microphoneAvailable(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && window.isSecureContext
}

export function openMicrophone(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
  })
}

export async function recordClick(stream: MediaStream, deviceId: string) {
  const context = new AudioContext()
  const source = context.createMediaStreamSource(stream)
  // ScriptProcessor is deprecated but needs no worklet module, which the
  // page's script-src 'self' policy and HA ingress would both complicate.
  const processor = context.createScriptProcessor(4096, 1, 1)
  const chunks: Float32Array[] = []
  processor.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)))
  source.connect(processor)
  processor.connect(context.destination)
  const rate = context.sampleRate
  try {
    await sleep(250)
    await playClick(deviceId)
    await sleep(2500)
  } finally {
    processor.disconnect()
    source.disconnect()
    await context.close()
  }
  const stride = Math.max(1, Math.round(rate / 8000))
  const samples: number[] = []
  let i = 0
  for (const chunk of chunks) for (const v of chunk) if (i++ % stride === 0) samples.push(Math.round(v * 1e6) / 1e6)
  return { samples, sample_rate: Math.round(rate / stride) }
}
