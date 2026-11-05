import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import DeviceCardShell from '@/components/devices/card/DeviceCardShell.vue'
import { playerCardView } from '@/components/devices/card/viewModels'
import { makeDevice } from '../../../fixtures/device'
import en from '@/i18n/locales/en.json'

const i18n = () => createI18n({ legacy: false, locale: 'en', messages: { en } })
const view = playerCardView(makeDevice({ name: 'Kitchen @ bridge', room: { name: 'Kitchen' }, bluetooth: { battery_level: 42 } }))

function mountShell(extra: Record<string, unknown> = {}) {
  return mount(DeviceCardShell, {
    props: { view, ...extra },
    slots: { context: '<p>Song</p>', footer: '<button data-primary>Details</button>' },
    global: { plugins: [i18n()] },
  })
}

describe('DeviceCardShell', () => {
  it('renders identity, role, status, meta and context', () => {
    const w = mountShell()
    expect(w.get('[data-device-card]').attributes('data-role')).toBe('output')
    expect(w.text()).toContain('Kitchen')
    expect(w.text()).toContain('Output')
    expect(w.text()).toContain('Connected')
    expect(w.text()).toContain('#EEFF')
    expect(w.text()).toContain('Song')
    expect(w.get('[data-primary]').exists()).toBe(true)
  })

  it('supports compact density', () => {
    expect(mountShell({ density: 'compact' }).get('[data-device-card]').attributes('data-density')).toBe('compact')
  })

  it('shows a checkbox when selection is available and keeps the menu', () => {
    const w = mount(DeviceCardShell, {
      props: { view, selectable: true },
      slots: { menu: '<button data-menu>menu</button>' },
      global: { plugins: [i18n()] },
    })
    expect(w.find('input[type="checkbox"]').exists()).toBe(true)
    expect(w.find('[data-menu]').exists()).toBe(true)
  })

  it('keeps the context line reserved', () => {
    expect(mountShell().find('.min-h-5').exists()).toBe(true)
  })
})
