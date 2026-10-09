import { describe, it, expect } from 'vitest'
import { planCorrection } from '@/calibration/plan'

const target = { id: 'target', delay: 100 }
const reference = { id: 'ref', delay: 40 }

describe('planCorrection', () => {
  it('raises the target\'s delay when it plays later', () => {
    // Heard 30 ms after the reference: declaring 30 ms more delay makes the
    // bridge send the target's audio 30 ms earlier.
    const plan = planCorrection([31, 30, 29], target, reference)
    expect(plan).toEqual({ ok: true, deviceId: 'target', value: 130, median: 30, spread: 1 })
  })

  it('adjusts the reference when the target plays earlier', () => {
    const plan = planCorrection([-20, -22, -21], target, reference)
    expect(plan).toEqual({ ok: true, deviceId: 'ref', value: 61, median: -21, spread: 1 })
  })

  it('needs at least two good measurements', () => {
    expect(planCorrection([25], target, reference)).toEqual({ ok: false, reason: 'not_detected' })
  })

  it('refuses measurements that disagree', () => {
    expect(planCorrection([10, 60, 120], target, reference)).toMatchObject({ ok: false, reason: 'unstable' })
  })

  it('keeps the result inside the allowed range', () => {
    const plan = planCorrection([900, 900, 900], { id: 't', delay: 4800 }, reference)
    expect(plan).toMatchObject({ ok: true, value: 5000 })
  })
})
