import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import SpeakerActionsMenu from '@/components/devices/SpeakerActionsMenu.vue'
import DisabledSpeakers from '@/components/devices/DisabledSpeakers.vue'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
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

describe('DisabledSpeakers', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists switched-off speakers by name and enables them by id', async () => {
    store.setEnabled.mockResolvedValue({ enabled: true, restart_required: true })
    const w = mount(DisabledSpeakers, { global: { plugins: [i18n()] } })
    expect(w.text()).toContain('Garage')
    expect(w.text()).not.toContain('@ hp')
    await w.findAll('button').find((b) => b.text() === 'Enable')!.trigger('click')
    await flushPromises()
    expect(store.setEnabled).toHaveBeenCalledWith('garage-id', true)
  })

  it('removes one after confirmation', async () => {
    const w = mount(DisabledSpeakers, { global: { plugins: [i18n()] } })
    await w.get('button[aria-label="Remove speaker"]').trigger('click')
    expect(confirmState.options?.title).toBe('Remove Garage?')
    settleConfirm(true)
    await flushPromises()
    expect(store.remove).toHaveBeenCalledWith('garage-id')
  })
})
