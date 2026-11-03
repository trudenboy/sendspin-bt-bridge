import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import FirstRunChecklist from '@/components/hardware/FirstRunChecklist.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function setBridge({ devices = 0, maConnected = false, source = null, loading = false } = {}) {
  useBridgeStore().loading = loading
  useBridgeStore().snapshot = {
    bridge: { ma_connected: maConnected, sendspin_source: source },
    devices: Array.from({ length: devices }, (_, i) => ({ id: String(i) })),
    groups: [],
  } as never
}

function mountChecklist() {
  return mount(FirstRunChecklist, {
    global: {
      plugins: [i18n],
      stubs: { RouterLink: { props: ['to'], template: '<a :href="String(to)"><slot /></a>' } },
    },
  })
}

describe('Home: first steps checklist', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('shows the three steps and the progress on a fresh bridge', () => {
    setBridge()
    const list = mountChecklist()
    expect(list.find('[data-first-run]').exists()).toBe(true)
    expect(list.get('[data-first-run-progress]').text()).toContain(en.home.checklist.progress.replace('{done}', '0').replace('{total}', '2'))
    expect(list.get('[data-step="speaker"]').text()).toContain(en.home.checklist.speaker)
    expect(list.get('[data-step="ma"]').text()).toContain(en.home.checklist.ma)
    expect(list.get('[data-step="speaker"]').attributes('data-done')).toBe('false')
  })

  it('marks a step done and counts it', () => {
    setBridge({ devices: 2 })
    const list = mountChecklist()
    expect(list.get('[data-step="speaker"]').attributes('data-done')).toBe('true')
    expect(list.get('[data-step="ma"]').attributes('data-done')).toBe('false')
    expect(list.get('[data-first-run-progress]').text()).toContain('1')
  })

  it('disappears when every step is done', () => {
    setBridge({ devices: 1, maConnected: true })
    expect(mountChecklist().find('[data-first-run]').exists()).toBe(false)
  })

  it('asks the page to open the add-speaker dialog', async () => {
    setBridge()
    const list = mountChecklist()
    await list.get('[data-step="speaker"] button, [data-step="speaker"] a').trigger('click')
    expect(list.emitted('addSpeaker')).toHaveLength(1)
  })

  it('hides itself for good once dismissed', async () => {
    setBridge()
    const first = mountChecklist()
    await first.get('[data-first-run-dismiss]').trigger('click')
    expect(first.find('[data-first-run]').exists()).toBe(false)
    expect(localStorage.getItem('sendspin-ui:first-run-dismissed')).toBe('1')
    expect(mountChecklist().find('[data-first-run]').exists()).toBe(false)
  })
})
