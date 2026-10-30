import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import SyncChip from '@/components/devices/chips/SyncChip.vue'
import { resetReanchorMemory } from '@/utils/reanchorHold'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'

const mountChip = (playout: Record<string, unknown>) =>
  mount(SyncChip, {
    props: {
      device: makeDevice({
        id: 'po',
        playback: { playing: true },
        audio: { streaming: true },
        timing: { available: true, reanchor_count: 7, playout },
      }),
    },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
const base = { mode: 'measure', state: 'playing', warm: true, warm_s: 90, warmup_s: 60, error_ms: 0.4, jumps: 3 }

describe('SyncChip playout', () => {
  beforeEach(() => resetReanchorMemory())

  it('shows the playout label and error', () => {
    expect(mountChip(base).get('[data-chip="sync"]').text()).toContain('Playout')
    expect(mountChip(base).get('[data-chip="sync"]').text()).toContain('0.4 ms')
  })

  it('shows the warming label with the seconds left', () => {
    const w = mountChip({ ...base, warm: false, warm_s: 25 })
    expect(w.get('[data-chip="sync"]').text()).toContain('Warming 35 s')
  })

  it('titles the count as jumps, not re-anchors', () => {
    const w = mountChip(base)
    expect(w.find('[data-chip="reanchors"]').exists()).toBe(false)
    const chip = w.get('[data-chip="jumps"]')
    expect(chip.attributes('title')).toBe('Playout jumps: 3 this session')
    expect(chip.text()).toContain('×3')
  })
})
