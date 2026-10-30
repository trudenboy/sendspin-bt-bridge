import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/locales/en.json'

const bridge = vi.hoisted(() => ({
  devices: [],
  adapters: [],
  maConnected: false,
  bridge: {
    disabled_devices: [
      { id: 'off-1', player_name: 'Garage @ hp', mac: 'AA:00:00:00:00:03', backend_type: 'bluetooth_a2dp', adapter: 'hci1' },
    ],
  },
  deviceById: () => undefined,
  refresh: vi.fn(),
  refreshAdapters: vi.fn(),
}))
const notify = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }))

vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => notify }))
vi.mock('@/api/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/devices')>()),
  setDeviceEnabled: vi.fn().mockResolvedValue({ enabled: true, restart_required: true }),
}))
vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({
    players: [{ id: 'off-1', name: 'Garage', enabled: false, backend: { type: 'bluetooth_a2dp', mac: 'AA:00:00:00:00:03' } }],
  }),
  saveConfig: vi.fn(),
}))

import { setDeviceEnabled } from '@/api/devices'

async function mountDrawer() {
  const w = mount(DeviceDetailDrawer, {
    props: { deviceId: 'off-1', open: true },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { Teleport: true } },
  })
  await flushPromises()
  return w
}

describe('the details of a disabled player', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('opens on its settings, named like the card', async () => {
    const w = await mountDrawer()
    expect(w.find('[role="dialog"]').text()).toContain('Garage')
    expect(w.text()).not.toContain('@ hp')
    expect(w.text()).toContain(en.drawer.config.enabled)
    expect(w.text()).toContain('AA:00:00:00:00:03')
    // No runtime tabs: it has no player to report on.
    expect(w.text()).not.toContain(en.drawer.tabs.timing)
  })

  it('enables it from the settings and says a restart is needed', async () => {
    const w = await mountDrawer()
    await w.get('[role="switch"]').trigger('click')
    await flushPromises()
    expect(setDeviceEnabled).toHaveBeenCalledWith('off-1', true)
    expect(bridge.refresh).toHaveBeenCalled()
    expect(notify.warning).toHaveBeenCalledWith(en.settings.restartNeeded)
  })
})
