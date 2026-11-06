import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceSection from '@/components/devices/SourceSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'

const MAC = 'AA:BB:CC:DD:EE:FF'
const MIC_ID = `headset-mic:${MAC}`
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  removeSource: vi.fn(),
  renameSource: vi.fn(),
  selectSource: vi.fn(),
}))

const phone = {
  id: 'bluetooth:34:C7:E9:25:A3:A3',
  kind: 'bluetooth',
  name: 'Pixel',
  description: '34:C7:E9:25:A3:A3',
  active: false,
  available: true,
  connected: true,
  monitor: false,
  mac: '34:C7:E9:25:A3:A3',
  node: null,
}

const mic = (active: boolean) => ({
  id: MIC_ID,
  kind: 'headset_microphone',
  name: 'Sony WH-1000XM4',
  description: 'HFP microphone',
  active,
  available: true,
  connected: true,
  monitor: false,
  mac: MAC,
  node: null,
  volume: 40,
  muted: false,
})

const headset = () =>
  makeDevice({
    id: 'dev-1',
    name: 'Sony WH-1000XM4',
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true },
  } as never)

function mountSection(inputs: Record<string, unknown>[], devices = [headset()]) {
  useBridgeStore().snapshot = {
    bridge: {
      sendspin_source: {
        enabled: true,
        state: 'streaming',
        active_input: MIC_ID,
        inputs,
        target_player: null,
        target_error: null,
        error: null,
      },
    },
    devices: [],
    groups: [],
  } as never
  return mount(SourceSection, {
    props: { query: '', devices },
    global: {
      plugins: [i18n],
      stubs: {
        SourceModeCard: {
          props: ['device'],
          template: '<div data-source-mode-card :data-role="\'input\'">{{ device.name }}</div>',
        },
      },
    },
  })
}

describe('Devices: source section after source mode', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('lists current inputs only: no player microphone entries beside its own card', async () => {
    const w = mountSection([phone, mic(false), mic(true)], [headset()])
    await flushPromises()

    expect(w.find(`[data-source-id="${MIC_ID}"]`).exists()).toBe(false)
    expect(w.find('[data-source-mode-card]').text()).toBe('Sony WH-1000XM4')
    expect(w.find(`[data-source-id="${phone.id}"]`).exists()).toBe(true)
    expect(w.get('[data-source-mode-card]').attributes('data-role')).toBe('input')
  })

  it('shows the empty hint when only microphone rows exist and no card represents them', async () => {
    const w = mountSection([mic(false)], [])
    await flushPromises()

    expect(w.find('[data-source-mode-card]').exists()).toBe(false)
    expect(w.get('[data-sources-empty]').exists()).toBe(true)
  })
})
