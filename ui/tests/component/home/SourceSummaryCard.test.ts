import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceSummaryCard from '@/components/hardware/SourceSummaryCard.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function setSource(source: Record<string, unknown> | null) {
  useBridgeStore().snapshot = { bridge: { sendspin_source: source }, devices: [], groups: [] } as never
}

function mountCard() {
  return mount(SourceSummaryCard, {
    global: {
      plugins: [i18n],
      stubs: { RouterLink: { props: ['to'], template: '<a :href="String(to)"><slot /></a>' } },
    },
  })
}

describe('Home: audio source card', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('stays out of the way when the bridge has no source', () => {
    setSource(null)
    expect(mountCard().find('[data-home-source]').exists()).toBe(false)
  })

  it('stays out of the way before Music Assistant pairing', () => {
    setSource({ enabled: true, state: 'pairing', ma_paired: false, ma_connected: false })
    expect(mountCard().find('[data-home-source]').exists()).toBe(false)
  })

  it('shows the source state after Music Assistant pairing, without a pairing link', () => {
    setSource({ enabled: true, state: 'streaming', ma_paired: true, ma_connected: true })

    const card = mountCard().get('[data-home-source]')

    expect(card.get('[data-home-source-status]').text()).toContain(en.home.source.streaming)
    expect(card.get('[data-home-source-status]').text()).toContain(en.home.source.maConnected)
    expect(card.find('[data-home-source-pair]').exists()).toBe(false)
    expect(card.get('[data-home-source-hardware]').attributes('href')).toBe('/hardware')
  })
})
