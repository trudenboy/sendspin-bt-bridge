import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceSettingsPanel from '@/components/devices/DeviceSettingsPanel.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({
    players: [
      { id: 'cfg-other', name: 'Other', backend: { type: 'bluetooth_a2dp', mac: '11:22:33:44:55:66' } },
      { id: 'cfg-y', name: 'Kitchen', backend: { type: 'bluetooth_a2dp', mac: 'AA:BB:CC:DD:EE:FF' } },
    ],
  }),
  saveConfig: vi.fn().mockResolvedValue({ warnings: [], reconfig: {} }),
}))

async function mountPanel(device: ReturnType<typeof makeDevice>) {
  setActivePinia(createPinia())
  const w = mount(DeviceSettingsPanel, {
    props: { device },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  await flushPromises()
  return w
}

describe('DeviceSettingsPanel', () => {
  it('finds a running speaker by its config id when the runtime id differs', async () => {
    const w = await mountPanel(makeDevice({ id: 'runtime-x', config_id: 'cfg-y' }))
    expect(w.text()).not.toContain(en.drawer.config.notConfigured)
  })

  it('reports a speaker without a config entry as not configured', async () => {
    const w = await mountPanel(makeDevice({ id: 'runtime-x', config_id: null }))
    expect(w.text()).toContain(en.drawer.config.notConfigured)
  })
})
