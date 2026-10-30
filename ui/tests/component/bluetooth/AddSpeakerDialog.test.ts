import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createI18n } from 'vue-i18n'
import AddSpeakerDialog from '@/components/bluetooth/AddSpeakerDialog.vue'
import en from '@/i18n/locales/en.json'
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

const audio = reactive({
  sinks: [] as Record<string, unknown>[],
  loading: false,
  error: null as string | null,
  fetchSinks: vi.fn(),
  addAudioOutput: vi.fn(),
})

vi.mock('@/stores/audio', () => ({ useAudioStore: () => audio }))
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

  it('does not search when it opens', () => {
    mountDialog()
    expect(bt.startScan).not.toHaveBeenCalled()
  })

  it('hints at pairing mode and offers Search before any scan', () => {
    bt.scanResults = []
    const w = mountDialog()
    expect(w.text()).toContain('Put the speaker in pairing mode, then press Search.')
    expect(w.text()).not.toContain('No devices found')
    expect(button(w, 'Search')).toBeDefined()
  })

  it('searches only when Search is pressed, then offers Search again', async () => {
    bt.scanResults = []
    const w = mountDialog()
    await button(w, 'Search').trigger('click')
    await flushPromises()
    expect(bt.startScan).toHaveBeenCalledWith('hci0', true)
    expect(button(w, 'Search again')).toBeDefined()
    expect(w.text()).not.toContain('Put the speaker in pairing mode, then press Search.')
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

  describe('Audio output tab', () => {
    const sink = (name: string, description: string, in_use_by: string | null = null) => ({ name, description, in_use_by, state: 'idle' })
    beforeEach(() => {
      audio.sinks = [sink('alsa_output.usb', 'USB DAC'), sink('alsa_output.hdmi', 'HDMI', 'p1')]
      audio.addAudioOutput.mockResolvedValue(true)
    })
    const openTab = async (w: ReturnType<typeof mountDialog>) => {
      await w.findAll('[role="tab"]').find((t) => t.text() === 'Audio output')!.trigger('click')
      await flushPromises()
    }

    it('lists the outputs when the tab opens and greys out the ones in use', async () => {
      const w = mountDialog()
      await openTab(w)
      expect(audio.fetchSinks).toHaveBeenCalled()
      expect(w.text()).toContain('USB DAC')
      const addButtons = w.findAll('button').filter((b) => b.text() === 'Add')
      expect(addButtons).toHaveLength(2)
      expect(addButtons[0]!.attributes('disabled')).toBeUndefined()
      expect(addButtons[1]!.attributes('disabled')).toBeDefined()
      expect(w.text()).toContain('Already used by another player')
    })

    it('names the output and adds it as a player', async () => {
      const w = mountDialog()
      await openTab(w)
      await w.findAll('button').filter((b) => b.text() === 'Add')[0]!.trigger('click')
      const [nameInput, roomInput] = w.findAll('input[type="text"]')
      expect((nameInput!.element as HTMLInputElement).value).toBe('USB DAC')
      await nameInput!.setValue('Desk speakers')
      await roomInput!.setValue('Study')
      await button(w, 'Add to bridge').trigger('click')
      await flushPromises()
      expect(audio.addAudioOutput).toHaveBeenCalledWith('alsa_output.usb', 'Desk speakers', { room: 'Study' })
      expect(w.text()).toContain('Desk speakers is on the bridge.')
    })
  })
})
