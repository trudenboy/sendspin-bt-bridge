import { describe, expect, it } from 'vitest'
import { deviceState, isDeviceConnected } from '@/utils/deviceStatus'
import { makeDevice } from '../../fixtures/device'

/**
 * The card's state and its Bluetooth chip are drawn side by side. The chip
 * shows the bridge's ``links.bluetooth``; the card state and "connected"
 * follow that same fact, so the two can never disagree — even when a raw
 * field arrives stale.
 */
const link = (state: string, reason: string | null = null) => ({
  links: { bluetooth: { state, tone: 'neutral', reason } },
})

describe('deviceStatus follows the Bluetooth link the chip shows', () => {
  it('is connected only when the link says connected', () => {
    expect(isDeviceConnected(makeDevice(link('connected')))).toBe(true)
    for (const state of ['connecting', 'disconnected', 'standby', 'released', 'disabled', 'not_paired']) {
      expect(isDeviceConnected(makeDevice({ ...link(state), bluetooth: { connected: true } })), state).toBe(false)
    }
  })

  it('names the card after the link state when the link decides it', () => {
    expect(deviceState(makeDevice({ ...link('standby'), bluetooth: { standby: false } }))).toBe('standby')
    expect(deviceState(makeDevice({ ...link('released', 'user'), bluetooth: { management_enabled: true } }))).toBe(
      'released',
    )
    expect(deviceState(makeDevice({ ...link('connecting', 'reconnecting'), bluetooth: { reconnecting: false } }))).toBe(
      'reconnecting',
    )
  })

  it('never calls a speaker whose link is down ready', () => {
    const d = makeDevice({ ...link('disconnected'), bluetooth: { connected: true }, health: { state: 'unknown' } })
    expect(deviceState(d)).toBe('offline')
  })

  it('keeps a local output connected by its sink, with no Bluetooth link', () => {
    const output = makeDevice({ backend: { type: 'pulse_sink' }, links: { bluetooth: null }, audio: { has_sink: true } })
    expect(isDeviceConnected(output)).toBe(true)
    expect(isDeviceConnected({ ...output, audio: { ...output.audio, sink_missing: true } })).toBe(false)
  })
})
