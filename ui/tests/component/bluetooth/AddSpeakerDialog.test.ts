import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import AddSpeakerDialog from '@/components/bluetooth/AddSpeakerDialog.vue'
import en from '@/i18n/en.json'
import { playClick } from '@/api/calibration'
import { makeDevice } from '../../fixtures/device'

const bt = reactive({
  scanning: false,
  scanResults: [] as { mac: string; name: string; adapter: string; rssi_dbm: number }[],
  scanError: null as string | null,
  pairing: false,
  pairTarget: null as string | null,
  pairError: null as string | null,
  startScan: vi.fn(),
  pairDevice: vi.fn(),
  addToBridge: vi.fn(),
})
const bridge = reactive({
  adapters: [{ id: 'hci0', mac: 'C0:00', name: 'Desk', powered: true }],
  devices: [] as ReturnType<typeof makeDevice>[],
})

vi.mock('@/stores/bluetooth', () => ({ useBluetoothStore: () => bt }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => bridge }))
vi.mock('@/api/calibration', () => ({ playClick: vi.fn().mockResolvedValue(undefined) }))
vi.mock('./PairedDevicesList.vue', () => ({ default: { template: '<div />' } }))

function mountDialog() {
  return mount(AddSpeakerDialog, {
    props: { open: true },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { PairedDevicesList: true } },
  })
}

const button = (w: ReturnType<typeof mountDialog>, text: string) => w.findAll('button').find((b) => b.text() === text)!

describe('AddSpeakerDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    bt.scanResults = [{ mac: 'AA:BB', name: 'Kitchen Boom', adapter: 'C0:00', rssi_dbm: -55 }]
    bt.pairDevice.mockResolvedValue(true)
    bt.addToBridge.mockResolvedValue(true)
    bridge.devices = []
  })

  it('starts searching as soon as it opens', () => {
    mountDialog()
    expect(bt.startScan).toHaveBeenCalledWith('hci0', true)
  })

  it('pairs, then adds with the chosen name and room', async () => {
    const w = mountDialog()
    await button(w, 'Add').trigger('click')
    await flushPromises()
    expect(bt.pairDevice).toHaveBeenCalledWith('AA:BB', expect.objectContaining({ adapter: 'hci0' }))

    const [nameInput, roomInput] = w.findAll('input[type="text"]')
    expect((nameInput!.element as HTMLInputElement).value).toBe('Kitchen Boom')
    await roomInput!.setValue('Kitchen')
    await button(w, 'Add to bridge').trigger('click')
    await flushPromises()
    expect(bt.addToBridge).toHaveBeenCalledWith('AA:BB', 'Kitchen Boom', 'hci0', { room: 'Kitchen' })
    expect(w.text()).toContain('Kitchen Boom is on the bridge.')
  })

  it('offers the test sound once the speaker has audio', async () => {
    const w = mountDialog()
    await button(w, 'Add').trigger('click')
    await flushPromises()
    await button(w, 'Add to bridge').trigger('click')
    await flushPromises()
    expect(button(w, 'Play test sound').attributes('disabled')).toBeDefined()

    bridge.devices = [makeDevice({ id: 'kb', bluetooth: { mac: 'AA:BB' }, audio: { has_sink: true } })]
    await flushPromises()
    await button(w, 'Play test sound').trigger('click')
    expect(playClick).toHaveBeenCalledWith('kb')
  })

  it('says why pairing failed and stays on the list', async () => {
    bt.pairDevice.mockImplementation(async () => {
      bt.pairError = 'PIN required'
      return false
    })
    const w = mountDialog()
    await button(w, 'Add').trigger('click')
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('PIN required')
    expect(bt.addToBridge).not.toHaveBeenCalled()
  })
})
