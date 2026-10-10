import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DeviceStatusBadge from '@/components/devices/DeviceStatusBadge.vue'
import en from '@/i18n/en.json'

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

describe('DeviceStatusBadge', () => {
  function mountBadge(state: string) {
    return mount(DeviceStatusBadge, {
      props: { state },
      global: { plugins: [buildI18n()] },
    })
  }

  it.each([
    ['streaming', 'Playing', 'success'],
    ['ready', 'Connected', 'neutral'],
    ['transitioning', 'Connecting…', 'warning'],
    ['recovering', 'Reconnecting…', 'warning'],
    ['degraded', 'Problem', 'error'],
    ['offline', 'Not connected', 'neutral'],
    ['standby', 'Standby', 'neutral'],
    ['disabled', 'Disabled', 'neutral'],
  ])('renders %s as %s with %s tone', (state, label, tone) => {
    const w = mountBadge(state)
    expect(w.text()).toContain(label)
    expect(w.find(`.tone-${tone}`).exists()).toBe(true)
  })

  it('falls back to raw state for unknown values', () => {
    const w = mountBadge('CUSTOM_STATE')
    expect(w.text()).toContain('CUSTOM_STATE')
    expect(w.find('.tone-neutral').exists()).toBe(true)
  })

  it('contains SbStatusDot element', () => {
    const w = mountBadge('streaming')
    expect(w.find('[role="status"]').exists()).toBe(true)
  })
})
