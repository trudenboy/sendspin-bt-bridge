import { describe, it, expect } from 'vitest'
import { decimationFactor, lowpassDecimate, toInt16 } from '@/sync/decimate'

describe('decimate', () => {
  it('picks the integer factor that lands at or above 16 kHz', () => {
    expect(decimationFactor(48000)).toBe(3)
    expect(decimationFactor(44100)).toBe(2)
    expect(decimationFactor(16000)).toBe(1)
    expect(decimationFactor(8000)).toBe(1)
    expect(decimationFactor(96000)).toBe(6)
  })

  it('keeps the sample count consistent across blocks', () => {
    const state = { acc: [] as number[], phase: 0 }
    const a = lowpassDecimate(new Float32Array(100).fill(0.5), 3, state)
    const b = lowpassDecimate(new Float32Array(50).fill(0.5), 3, state)
    expect(a.length + b.length).toBe(50)
    expect(Math.abs(b[b.length - 1]! - 0.5)).toBeLessThan(1e-6)
  })

  it('gives the same output whether the input arrives whole or in odd-sized blocks', () => {
    const input = new Float32Array(1000)
    for (let i = 0; i < input.length; i++) input[i] = Math.sin(i * 0.37) * 0.8
    const whole = lowpassDecimate(input, 3, { acc: [], phase: 0 })
    const state = { acc: [] as number[], phase: 0 }
    const parts: number[] = []
    for (let i = 0; i < input.length; i += 127) parts.push(...lowpassDecimate(input.subarray(i, i + 127), 3, state))
    expect(parts.length).toBe(whole.length)
    parts.forEach((v, i) => expect(v).toBeCloseTo(whole[i]!, 6))
  })

  it('suppresses a tone at the 12 kHz notch of a 48 kHz input', () => {
    const input = new Float32Array(4800)
    for (let i = 0; i < input.length; i++) input[i] = Math.sin((2 * Math.PI * 12000 * i) / 48000)
    const out = lowpassDecimate(input, 3, { acc: [], phase: 0 })
    const tail = out.subarray(10)
    expect(Math.max(...Array.from(tail, Math.abs))).toBeLessThan(1e-6)
  })

  it('suppresses a tone that would alias onto DC when decimating 96 kHz by six', () => {
    const input = new Float32Array(9600)
    for (let i = 0; i < input.length; i++) input[i] = Math.sin((2 * Math.PI * 16000 * i) / 96000)
    const out = lowpassDecimate(input, 6, { acc: [], phase: 0 })
    expect(Math.max(...Array.from(out.subarray(10), Math.abs))).toBeLessThan(1e-6)
  })

  it('passes samples through untouched when no decimation is needed', () => {
    const input = new Float32Array([0.1, -0.2, 0.3])
    expect(Array.from(lowpassDecimate(input, 1, { acc: [], phase: 0 }))).toEqual(Array.from(input))
  })

  it('converts to clamped int16', () => {
    expect(Array.from(toInt16(new Float32Array([0, 0.5, 1.5, -2])))).toEqual([0, 16384, 32767, -32768])
    expect(Array.from(toInt16(new Float32Array([-0.5, 0.25])))).toEqual([-16384, 8192])
  })
})
