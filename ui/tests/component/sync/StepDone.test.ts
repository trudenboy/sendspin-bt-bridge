import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepDone from '@/components/sync/StepDone.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const base = { before: null, after: null, predicted: false, undone: false, saved: null, saveError: '', listening: false, listenError: '' }

describe('StepDone', () => {
  it('says the speakers are being unmuted and the clicker stopped, with no music countdown', () => {
    const w = mount(StepDone, { global: { plugins: [i18n] }, props: { ...base, state: 'restoring' } })
    expect(w.text()).toContain('Unmuting the speakers and stopping the click track')
    expect(w.find('[data-music-returns]').exists()).toBe(false)
    expect(w.text()).not.toContain('Music returns')
  })

  it('once restored, says the music does not come back by itself', () => {
    const w = mount(StepDone, { global: { plugins: [i18n] }, props: { ...base, state: 'done' } })
    expect(w.text()).toContain('start your music again in Music Assistant')
  })
})
