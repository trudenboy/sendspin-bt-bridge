import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepMembers from '@/components/sync/StepMembers.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 }]
const base = { members, point: 'device', results: {}, stale: [], departed: [], active: null, progress: 0, errors: {}, notMeasured: [], complete: false }

const mountStep = (over: Record<string, unknown>) =>
  mount(StepMembers, { global: { plugins: [i18n] }, props: { ...base, ...over } as never })

describe('StepMembers: the test clip', () => {
  it('records only while the clicks are audible', () => {
    expect(mountStep({ signal: 'ready', waitS: null }).get('[data-record="a"]').attributes('disabled')).toBeUndefined()
    const starting = mountStep({ signal: 'starting', waitS: null })
    expect(starting.get('[data-record="a"]').attributes('disabled')).toBeDefined()
    expect(starting.get('[data-signal-state]').text()).toContain('Starting the clicks')
  })

  it('says when a round that needs a fresh clip starts', () => {
    const w = mountStep({ signal: 'waiting', waitS: 42 })
    expect(w.get('[data-record="a"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-signal-state]').text()).toContain('42 s')
  })

  it('offers to start the clicks again once the clip ended', async () => {
    const w = mountStep({ signal: 'ended', waitS: null })
    expect(w.get('[data-record="a"]').attributes('disabled')).toBeDefined()
    await w.get('[data-restart-signal]').trigger('click')
    expect(w.emitted('restart')).toHaveLength(1)
  })
})
