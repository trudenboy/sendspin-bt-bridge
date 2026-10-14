import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import AdaptersSection from '@/components/settings/AdaptersSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/en.json'

const route = { query: {} as Record<string, string>, hash: '' }
vi.mock('vue-router', () => ({ useRoute: () => route }))

function mountSection() {
  useBridgeStore().adapters = [
    { id: 'hci0', mac: 'C0:00:00:00:00:01', name: 'one', powered: true },
    { id: 'hci1', mac: 'C0:00:00:00:00:02', name: 'two', powered: true },
  ]
  return mount(AdaptersSection, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('AdaptersSection deep link', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    route.query = {}
  })

  it('gives each adapter an anchor a card can link to', () => {
    const w = mountSection()
    expect(w.find('#settings-adapter-hci0').exists()).toBe(true)
    expect(w.find('#settings-adapter-hci1').exists()).toBe(true)
  })

  it('highlights the adapter named in the link', () => {
    route.query = { adapter: 'hci1' }
    const w = mountSection()
    expect(w.find('#settings-adapter-hci1').attributes('data-highlighted')).toBe('true')
    expect(w.find('#settings-adapter-hci0').attributes('data-highlighted')).toBeUndefined()
  })

  it('highlights nothing without a link', () => {
    const w = mountSection()
    expect(w.find('[data-highlighted]').exists()).toBe(false)
  })

  it('scrolls to the linked adapter once the adapter list arrives', async () => {
    route.query = { adapter: 'hci1' }
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const bridge = useBridgeStore()
    bridge.adapters = []
    mount(AdaptersSection, {
      attachTo: document.body,
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(scroll).not.toHaveBeenCalled()
    bridge.adapters = [{ id: 'hci1', mac: 'C0:00:00:00:00:02', name: 'two', powered: true }]
    await flushPromises()
    expect(scroll).toHaveBeenCalledTimes(1)
    expect((scroll.mock.contexts[0] as HTMLElement).id).toBe('settings-adapter-hci1')
  })
})
