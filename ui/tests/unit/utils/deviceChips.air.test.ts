import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SyncChip from '@/components/devices/chips/SyncChip.vue'
import { airChipSummary, signedMs } from '@/utils/deviceChips'
import { resetReanchorMemory } from '@/utils/reanchorHold'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'

const device = (air: Record<string, unknown> | null) =>
  makeDevice({
    id: 'air',
    playback: { playing: true },
    audio: { streaming: true },
    timing: { available: true, air_sensor: air } as never,
  })
const title = (air: Record<string, unknown> | null) =>
  mount(SyncChip, {
    props: { device: device(air) },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
    .get('[data-chip="sync"]')
    .attributes('title')

describe('air sensor in the sync chip', () => {
  it('formats signed milliseconds with one decimal', () => {
    expect(signedMs(12.44)).toBe('+12.4')
    expect(signedMs(-3.1)).toBe('−3.1')
  })

  it('summarises only a sensor that is not off', () => {
    expect(airChipSummary(device(null))).toBeNull()
    expect(airChipSummary(device({ state: 'off', reason: 'needs_measure' }))).toBeNull()
    expect(airChipSummary(device({ state: 'locked', host_placement_ms: 12.4 }))).toEqual({ state: 'locked', hMs: 12.4 })
  })

  it('adds one air line to the tooltip', () => {
    resetReanchorMemory()
    const t = title({ state: 'locked', host_placement_ms: 12.4 })!
    expect(t).toContain('air: locked, H +12.4 ms')
    expect(t.match(/air:/g)).toHaveLength(1)
  })

  it('adds no air line when off or null', () => {
    resetReanchorMemory()
    expect(title({ state: 'off', reason: 'needs_measure' })).not.toContain('air:')
    expect(title(null)).not.toContain('air:')
  })
})
