import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { makeDevice } from '../fixtures/device'
import { seriousViolations } from '../helpers/axe'

// Regression guard (UX stage 8): no serious or critical axe violation on the
// speaker card and on every tab of the speaker drawer, in both languages.

vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/devices', async (orig) => ({ ...(await orig<object>()), claimAudio: vi.fn() }))
const dev = makeDevice({
  id: 'dev-1',
  music_assistant: { syncgroup_id: 'g1' },
  playback: { playing: true, track: { title: 'Song', artist: 'Band' } },
})
vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [dev],
    groups: [],
    adapters: [{ id: 'hci0', mac: 'AA', name: 'Living room', powered: true }],
    maConnected: false,
    maAccount: null,
    deviceById: (id: string) => (id === dev.id ? dev : undefined),
    refreshAdapters: vi.fn(),
  }),
}))
vi.mock('@/api/config', () => ({ getConfig: vi.fn().mockResolvedValue({ players: [] }), saveConfig: vi.fn() }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(), createSession: vi.fn(), uploadRecording: vi.fn(), endSession: vi.fn(),
}))

let mounted: VueWrapper | null = null
afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.innerHTML = ''
})
const plugin = (locale: 'en' | 'ru') => createI18n({ legacy: false, locale, messages: { en, ru } })

describe('accessibility: the checker can fail', () => {
  it('flags a button without a name', async () => {
    mounted = mount({ template: '<div><button type="button"></button></div>' }, { attachTo: document.body })
    expect((await seriousViolations()).map((v) => v.rule)).toContain('button-name')
  })
})

describe('accessibility: speaker card and drawer', () => {
  beforeEach(() => setActivePinia(createPinia()))

  for (const locale of ['en', 'ru'] as const) {
    it(`the speaker card has no serious violations (${locale})`, async () => {
      mounted = mount(DeviceCard, { props: { device: dev }, global: { plugins: [plugin(locale)] }, attachTo: document.body })
      await flushPromises()
      expect(await seriousViolations()).toEqual([])
    })

    it(`every drawer tab has no serious violations (${locale})`, async () => {
      mounted = mount(DeviceDetailDrawer, {
        props: { deviceId: 'dev-1', open: true },
        global: { plugins: [plugin(locale)], stubs: { RouterLink: { template: '<a href="#"><slot /></a>' } } },
        attachTo: document.body,
      })
      await flushPromises()
      const tabs = mounted.findAll('[role="tab"]')
      expect(tabs.length).toBe(4)
      for (const tab of tabs) {
        await tab.trigger('click')
        await flushPromises()
        expect(await seriousViolations(), `tab ${tab.attributes('data-tab-id')}`).toEqual([])
      }
    })
  }
})
