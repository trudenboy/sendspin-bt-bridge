import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SyncChip from '@/components/devices/chips/SyncChip.vue'
import { resetReanchorMemory } from '@/utils/reanchorHold'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

const mountChip = (air: Record<string, unknown>) =>
  mount(SyncChip, {
    props: {
      device: makeDevice({
        id: 'air',
        playback: { playing: true },
        audio: { streaming: true },
        timing: { available: true, reanchor_count: 0, air_sensor: air } as never,
      }),
    },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en }, missingWarn: false })] },
  })

describe('SyncChip air sensor line', () => {
  beforeEach(() => resetReanchorMemory())

  it('names a known state', () => {
    const title = mountChip({ state: 'locked', host_placement_ms: 12.4 }).get('[data-chip="sync"]').attributes('title')
    expect(title).toContain('locked')
  })

  it('falls back to the raw state for one it does not know', () => {
    const title = mountChip({ state: 'brand_new_state' }).get('[data-chip="sync"]').attributes('title')
    expect(title).not.toMatch(/timing\.air/)
    expect(title).toContain('brand_new_state')
  })
})
