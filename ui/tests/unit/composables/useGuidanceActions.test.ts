import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import { reconnectDevice } from '@/api/devices'
import { useGuidanceActions } from '@/composables/useGuidanceActions'
import { useBluetoothStore } from '@/stores/bluetooth'

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
vi.mock('@/api/devices', () => ({ reconnectDevice: vi.fn().mockResolvedValue({}), unmuteSink: vi.fn() }))
vi.mock('@/api/diagnostics', () => ({ rerunCheck: vi.fn() }))
vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [
      { id: 'kitchen-id', name: 'Kitchen' },
      { id: 'office-id', name: 'Office' },
    ],
    refresh: vi.fn(),
  }),
}))
vi.mock('@/stores/notifications', () => ({
  useNotificationStore: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }),
}))

/** Runs the composable inside an app so useI18n works. */
function setup() {
  let api!: ReturnType<typeof useGuidanceActions>
  const pinia = createPinia()
  setActivePinia(pinia)
  const app = createApp({ setup: () => ((api = useGuidanceActions()), () => null) })
  app.use(pinia).use(createI18n({ legacy: false, locale: 'en', messages: { en } }))
  app.mount(document.createElement('div'))
  return api
}

describe('useGuidanceActions', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens the settings section an action points to', async () => {
    await setup().run({ key: 'open_bluetooth_settings', label: 'Bluetooth' })
    expect(push).toHaveBeenCalledWith({ path: '/config', hash: '#settings-bluetooth' })
  })

  it('reconnects every speaker the action names', async () => {
    await setup().run({ key: 'reconnect_devices', label: 'Reconnect', device_names: ['Office', 'Kitchen'] })
    expect(vi.mocked(reconnectDevice).mock.calls.map((c) => c[0]).sort()).toEqual(['kitchen-id', 'office-id'])
  })

  it('asks the devices page to open the scan, without a route parameter', async () => {
    const api = setup()
    await api.run({ key: 'scan_devices', label: 'Scan' })
    expect(useBluetoothStore().scanRequested).toBe(true)
    expect(push).toHaveBeenCalledWith('/')
  })

  it('falls back to diagnostics for an action it does not know', async () => {
    await setup().run({ key: 'something_new', label: 'New' })
    expect(push).toHaveBeenCalledWith('/diagnostics')
  })
})
