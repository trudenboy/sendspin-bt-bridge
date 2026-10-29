import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import DeviceDetailDrawer from '@/components/devices/DeviceDetailDrawer.vue'
import en from '@/i18n/en.json'
import { makeDevice } from '../../fixtures/device'

const mine = makeDevice({ id: 'dev-1', name: 'Kitchen' } as never)
const sibling = makeDevice({ id: 'dev-2', name: 'Bedroom' } as never)
const other = makeDevice({ id: 'dev-3', name: 'Garage', bluetooth: { adapter: { hci: 'hci1' } } } as never)
const adapters = vi.hoisted(() => ({ list: [{ id: 'hci0', mac: 'AA', name: 'Living room', powered: true }] }))
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
const bt = vi.hoisted(() => ({ setAdapterPower: vi.fn().mockResolvedValue(undefined) }))

vi.mock('@/stores/bridge', () => ({
  useBridgeStore: () => ({
    devices: [mine, sibling, other],
    adapters: adapters.list,
    maConnected: false,
    deviceById: (id: string) => [mine, sibling, other].find((d) => d.id === id),
    refreshAdapters: vi.fn(),
  }),
}))
vi.mock('@/composables/useConfirm', () => confirm)
vi.mock('@/api/bluetooth', () => bt)
vi.mock('@/api/config', () => ({ getConfig: vi.fn().mockResolvedValue({ players: [] }), saveConfig: vi.fn() }))
vi.mock('@/api/calibration', () => ({
  getLatencyHistory: vi.fn().mockResolvedValue({ samples: [] }),
  setMetronome: vi.fn(), createSession: vi.fn(), uploadRecording: vi.fn(), endSession: vi.fn(),
}))

async function settings() {
  const w = mount(DeviceDetailDrawer, {
    props: { deviceId: 'dev-1', open: true },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { Teleport: true } },
  })
  await flushPromises()
  await w.findAll('[role="tab"]').find((t) => t.text() === en.drawer.tabs.config)!.trigger('click')
  await flushPromises()
  return w
}

describe('DeviceDetailDrawer adapter management', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    adapters.list[0]!.powered = true
  })

  it('asks before powering the adapter off and names the speakers on it', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    const w = await settings()
    await w.get('[data-adapter-power]').trigger('click')
    await flushPromises()
    const o = confirm.confirmDialog.mock.calls[0]![0]
    const msg = [o.message, ...(o.consequences ?? [])].join(' ')
    expect(msg).toContain('Kitchen')
    expect(msg).toContain('Bedroom')
    expect(msg).not.toContain('Garage')
    expect(bt.setAdapterPower).not.toHaveBeenCalled()
  })

  it('powers the adapter off once confirmed', async () => {
    confirm.confirmDialog.mockResolvedValue(true)
    const w = await settings()
    await w.get('[data-adapter-power]').trigger('click')
    await flushPromises()
    expect(bt.setAdapterPower).toHaveBeenCalledWith('hci0', false)
  })

  it('asks before a reboot and does nothing when declined', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    const w = await settings()
    await w.get('[data-adapter-reboot]').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog.mock.calls[0]![0].consequences.join(' ')).toContain('Bedroom')
    expect(bt.setAdapterPower).not.toHaveBeenCalled()
  })

  it('shows the action the button will take, and turning on needs no confirmation', async () => {
    const w = await settings()
    expect(w.get('[data-adapter-power]').text()).toBe(en.adapter.turnOff)
    adapters.list[0]!.powered = false
    const w2 = await settings()
    expect(w2.get('[data-adapter-power]').text()).toBe(en.adapter.turnOn)
    await w2.get('[data-adapter-power]').trigger('click')
    await flushPromises()
    expect(confirm.confirmDialog).not.toHaveBeenCalled()
    expect(bt.setAdapterPower).toHaveBeenCalledWith('hci0', true)
  })
})
