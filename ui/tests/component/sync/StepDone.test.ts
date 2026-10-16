import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepDone from '@/components/sync/StepDone.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const base = { before: null, after: null, predicted: false, undone: false, saved: null, saveError: '', listening: false, listenError: '' }

describe('StepDone', () => {
  afterEach(() => { vi.useRealTimers() })

  it('says when the music returns while the test clip plays out, and that it cannot be stopped', () => {
    vi.useFakeTimers()
    const w = mount(StepDone, { global: { plugins: [i18n] }, props: { ...base, state: 'restoring', musicReturnsIn: 120 } })
    expect(w.get('[data-music-returns]').text()).toContain('Music returns in 120 s')
    expect(w.text()).toContain('cannot be stopped early')
    vi.advanceTimersByTime(2000)
    return w.vm.$nextTick().then(() => expect(w.get('[data-music-returns]').text()).toContain('118 s'))
  })

  it('has no countdown once the group is restored', () => {
    const w = mount(StepDone, { global: { plugins: [i18n] }, props: { ...base, state: 'done', musicReturnsIn: null } })
    expect(w.find('[data-music-returns]').exists()).toBe(false)
  })
})
