import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { makeDevice } from '../../fixtures/device'

const foreign = makeDevice({
  playback: {
    server_connected: true,
    server: { name: 'Test MA', id: 'srv-1', host: '192.168.10.229', url: '192.168.10.229:8927', own: false },
    refused_servers: [{ host: '192.168.10.50', name: 'Old MA', server_id: 's2', owner: 'ma.local', at: null, count: 3 }],
  },
})

vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [foreign],
    adapters: [],
    maConnected: true,
    deviceById: (id: string) => (id === foreign.id ? foreign : undefined),
    refreshAdapters: vi.fn(),
  }),
}))
vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({ players: [] }),
  saveConfig: vi.fn(),
}))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(),
  createSession: vi.fn(),
  uploadRecording: vi.fn(),
  endSession: vi.fn(),
}))

async function mountDrawer(locale: 'en' | 'ru') {
  setActivePinia(createPinia())
  const w = mount(DeviceDetailDrawer, {
    props: { deviceId: foreign.id, open: true },
    global: { plugins: [createI18n({ legacy: false, locale, messages: { en, ru } })], stubs: { Teleport: true } },
  })
  await nextTick()
  await nextTick()
  return w
}

describe('DeviceDetailDrawer server details', () => {
  it('shows the server in full and warns that it is not the bridge’s own', async () => {
    const w = await mountDrawer('en')
    const section = w.find('[data-server-details]')
    expect(section.text()).toContain('Test MA')
    expect(section.text()).toContain('192.168.10.229')
    expect(section.text()).toContain('srv-1')
    expect(section.find('[data-server-warning]').text()).toContain('not to this bridge')
  })

  it('lists the servers the player turned away', async () => {
    const w = await mountDrawer('en')
    const refused = w.find('[data-refused-servers]')
    expect(refused.text()).toContain('Old MA')
    expect(refused.text()).toContain('192.168.10.50')
    expect(refused.text()).toContain('3')
  })

  it('speaks Russian', async () => {
    const w = await mountDrawer('ru')
    expect(w.find('[data-server-warning]').text()).toContain('не к Music Assistant этого моста')
  })
})
