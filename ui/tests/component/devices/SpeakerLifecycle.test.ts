import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import SpeakerActionsMenu from '@/components/devices/SpeakerActionsMenu.vue'
import { makeDevice } from '../../fixtures/device'

const store = { reconnect: vi.fn(), wake: vi.fn(), standby: vi.fn(), release: vi.fn(), setEnabled: vi.fn(), remove: vi.fn() }
const bridge = reactive({
  bridge: { disabled_devices: [{ id: 'garage-id', mac: 'AA:00', player_name: 'Garage @ hp' }] },
  refresh: vi.fn(),
})
vi.mock('@/stores/devices', () => ({ useDeviceStore: () => store }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))
vi.mock('@/stores/notifications', () => ({ useNotificationStore: () => ({ error: vi.fn(), warning: vi.fn() }) }))
vi.mock('@/api/devices', () => ({ claimAudio: vi.fn() }))

const i18n = () => createI18n({ legacy: false, locale: 'en', messages: { en } })

async function menuItems(device = makeDevice()) {
  const w = mount(SpeakerActionsMenu, { props: { device }, global: { plugins: [i18n()], stubs: { BtDeviceInfoModal: true } } })
  await w.get('button[aria-haspopup], button').trigger('click')
  return w.findAll('[role="menuitem"]').map((b) => b.text())
}

describe('speaker actions', () => {
  beforeEach(() => vi.clearAllMocks())

  it('hides the actions the backend does not support', async () => {
    const items = await menuItems(makeDevice({ supported_actions: ['volume', 'transport', 'power_save'] }))
    for (const gone of ['Reconnect', 'Put on standby', 'Make this bridge the active source']) expect(items).not.toContain(gone)
    expect(items).toContain('Disable')
    expect(items).toContain('Remove speaker…')
  })

  it('treats a missing supported_actions list as nothing supported', async () => {
    const device = makeDevice()
    delete (device as { supported_actions?: string[] }).supported_actions
    const items = await menuItems(device)
    expect(items).not.toContain('Reconnect')
  })

  it('groups connection, bridge and removal in the same words everywhere', async () => {
    const items = await menuItems()
    expect(items).toEqual([
      'Reconnect',
      'Put on standby',
      'Make this bridge the active source',
      'Hand over to other devices',
      'Disable',
      'Bluetooth details',
      'Remove speaker…',
    ])
  })

  it('offers no connection actions for a speaker handed over to other devices', async () => {
    const items = await menuItems(makeDevice({ bluetooth: { management_enabled: false } }))
    expect(items).toEqual(['Take back', 'Disable', 'Bluetooth details', 'Remove speaker…'])
  })
})
