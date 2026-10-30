import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepMembers from '@/components/sync/StepMembers.vue'
import en from '@/i18n/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 0 }]
const base = { members, point: 'device', results: {}, stale: [], departed: [], active: null, progress: 0, errors: {}, notMeasured: [], complete: false }

const mountStep = (over: Record<string, unknown>) =>
  mount(StepMembers, { global: { plugins: [i18n] }, props: { ...base, ...over } as never })

describe('StepMembers: the clicker', () => {
  it('records only while the clicker plays', () => {
    const playing = mountStep({ clickerPlaying: true })
    expect(playing.get('[data-record="a"]').attributes('disabled')).toBeUndefined()
    expect(playing.find('[data-clicker-stopped]').exists()).toBe(false)
    const stopped = mountStep({ clickerPlaying: false })
    expect(stopped.get('[data-record="a"]').attributes('disabled')).toBeDefined()
    expect(stopped.get('[data-clicker-stopped]').text()).toContain('Clicks are not playing.')
  })

  it('offers to start the clicker again once it stopped', async () => {
    const w = mountStep({ clickerPlaying: false })
    expect(w.get('[data-restart-clicker]').text()).toContain('Start the clicks again')
    await w.get('[data-restart-clicker]').trigger('click')
    expect(w.emitted('restart')).toHaveLength(1)
  })
})

describe('StepMembers: Cancel while a speaker records', () => {
  it('keeps Cancel active (the emergency stop) while Back and Next wait', async () => {
    const w = mountStep({ clickerPlaying: true, active: { member: 'a', phase: 'recording' }, complete: true })
    expect(w.get('[data-cancel]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-back]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-to-tune]').attributes('disabled')).toBeDefined()
    await w.get('[data-cancel]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
  })
})
