import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepVolume from '@/components/sync/StepVolume.vue'
import StepClicker from '@/components/sync/StepClicker.vue'
import en from '@/i18n/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [
  { ma_player_id: 'a', name: 'ENEBY Portable @ 85b1ecde-sendspin-bt-bridge-beta', origin: 'bridge', adjustable: true, delay_ms: 0 },
  { ma_player_id: 'k', name: 'Web Kiosk (wk_723e2...) [192.168.10.229]', origin: 'sendspin', adjustable: true, delay_ms: 0 },
]

describe('preparation steps (UX round)', () => {
  afterEach(() => { vi.useRealTimers() })

  it('says the volumes go back, flags a speaker at 0 % and uses short names', () => {
    const w = mount(StepVolume, { global: { plugins: [i18n] }, props: { members, volumes: { a: 40, k: 0 }, error: '' } as never })
    expect(w.get('[data-volumes-back]').text()).toBe('Volumes go back to what they were when you finish.')
    expect(w.find('[data-silent="a"]').exists()).toBe(false)
    expect(w.get('[data-silent="k"]').text()).toBe('Silent: turn it up to measure it')
    expect(w.text()).toContain('ENEBY Portable')
    expect(w.text()).not.toContain('85b1ecde')
    expect(w.text()).not.toContain('192.168.10.229')
    expect(w.get('[data-next]').text()).toBe('Next')
  })

  it('counts the clicks down while they play and says why Next waits', () => {
    const playing = mount(StepClicker, { global: { plugins: [i18n] }, props: { clickerPlaying: true, started: true, busy: false, error: '', leftS: 161 } })
    expect(playing.get('[data-clicks-left]').text()).toBe('Clicks: 2:41 left')
    const stopped = mount(StepClicker, { global: { plugins: [i18n] }, props: { clickerPlaying: false, started: false, busy: false, error: '', leftS: null } })
    expect(stopped.find('[data-clicks-left]').exists()).toBe(false)
    expect(stopped.get('[data-why]').text()).toBe('Start the clicks to go on.')
    expect(stopped.get('[data-next]').attributes('aria-describedby')).toBe('sync-clicker-next-why')
  })
})
