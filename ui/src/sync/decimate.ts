/** Main-thread decimation of the raw microphone blocks the capture worklet forwards. */

const TARGET_RATE = 16000
/** At least 4 taps, and at least `factor`, so the average nulls the band that folds onto DC. */
const MIN_TAPS = 4

/** The integer factor that brings `contextRate` to the lowest rate at or above 16 kHz. */
export function decimationFactor(contextRate: number): number {
  return Math.max(1, Math.floor(contextRate / TARGET_RATE))
}

/**
 * A moving average over max(4, factor) taps (a cheap anti-alias for a 2–6 kHz test
 * signal), keeping one output per `factor` inputs. `state` carries the filter history
 * (`acc`, the last taps−1 inputs) and the position inside the current window (`phase`) across blocks,
 * so splitting the input differently gives the same output. Output k is taken at input
 * k·factor + factor−1. A factor of 1 passes the samples through unfiltered.
 */
export function lowpassDecimate(
  input: Float32Array,
  factor: number,
  state: { acc: number[]; phase: number },
): Float32Array {
  if (factor <= 1) return Float32Array.from(input)
  const taps = Math.max(MIN_TAPS, factor)
  const out = new Float32Array(Math.floor((state.phase + input.length) / factor))
  const hist = state.acc
  let phase = state.phase
  let o = 0
  for (let i = 0; i < input.length; i++) {
    const v = input[i]!
    phase++
    if (phase === factor) {
      phase = 0
      let sum = v
      for (const h of hist) sum += h
      out[o++] = sum / (hist.length + 1)
    }
    hist.push(v)
    if (hist.length > taps - 1) hist.shift()
  }
  state.phase = phase
  return out
}

/** Float samples in [-1, 1] → 16-bit PCM: scaled by 32768, rounded, clamped. */
export function toInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i++) {
    const v = Math.round(samples[i]! * 32768)
    out[i] = v > 32767 ? 32767 : v < -32768 ? -32768 : v
  }
  return out
}
