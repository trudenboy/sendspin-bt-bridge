import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import HomeView from '@/views/HomeView.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/api/audio', () => ({ getAudioSinks: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/playback', () => ({ transport: vi.fn(), setVolume: vi.fn(), setMute: vi.fn(), setDevicePlayback: vi.fn() }))

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`

const stubs = {
  DeviceDetailDrawer: true,
  AddSpeakerDialog: true,
  GuidanceBanner: true,
  DeviceCard: {
    props: ['device', 'selectable', 'selected'],
    emits: ['toggleSelect', 'openDetail'],
    template:
      '<div data-card :data-id="device.id" :data-name="device.name">' +
      '<input v-if="selectable" type="checkbox" :aria-label="\'select \' + device.name" @click="$emit(\'toggleSelect\', device.id)" />' +
      '{{ device.name }}</div>',
  },
  SourceModeCard: {
    props: ['device'],
    template: '<div data-source-mode-card :data-role="\'input\'">{{ device.name }}</div>',
  },
}

function sourceBlock(activeInput: string | null, inputs: Record<string, unknown>[] = []) {
  return {
    enabled: true,
    state: 'waiting',
    active_input: activeInput,
    inputs,
    target_player: null,
    target_error: null,
    error: null,
  }
}

const micRow = (active: boolean) => ({
  id: INPUT_ID,
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

const lineRow = {
  id: 'local:alsa_input.line',
  kind: 'local',
  name: 'Turntable',
  description: 'Line In',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: null,
  node: 'alsa_input.line',
  volume: 100,
  muted: false,
}

function mountHome(devices: Device[], activeInput: string | null, inputs: Record<string, unknown>[] = []) {
  localStorage.setItem('sb-view-mode', 'grid')
  const bridge = useBridgeStore()
  const status = makeStatus(devices)
  ;(status.bridge as Record<string, unknown>).sendspin_source = sourceBlock(activeInput, inputs)
  bridge.snapshot = status
  bridge.loading = false
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const wrapper = mount(HomeView, {
    attachTo: document.body,
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } }), router],
      stubs,
    },
  })
  void router.push({ path: '/' })
  return wrapper
}

const playerCards = (w: ReturnType<typeof mountHome>) => w.findAll('[data-card]').map((c) => c.text())
const movedCards = (w: ReturnType<typeof mountHome>) => w.findAll('[data-source-mode-card]').map((c) => c.text())

const player = (id: string, name: string) =>
  makeDevice({ id, name, bluetooth: { mac: '11:22:33:44:55:66' } } as never)
const headset = () =>
  makeDevice({
    id: 'dev-1',
    name: 'Sony WH-1000XM4',
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true },
  } as never)

describe('HomeView: source mode moves the card', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('renders the player that owns the active microphone input in the Sources section', async () => {
    const w = mountHome([headset(), player('dev-2', 'Alpha')], INPUT_ID, [micRow(true)])
    await flushPromises()

    expect(movedCards(w)).toEqual(['Sony WH-1000XM4'])
    expect(playerCards(w)).toEqual(['Alpha'])
    expect(w.findAll('[data-source-id]')).toHaveLength(0)
  })

  it('returns the card to the players grid when the input is released', async () => {
    const w = mountHome([headset(), player('dev-2', 'Alpha')], INPUT_ID, [micRow(true)])
    await flushPromises()
    expect(playerCards(w)).toEqual(['Alpha'])

    const bridge = useBridgeStore()
    const status = makeStatus([headset(), player('dev-2', 'Alpha')])
    ;(status.bridge as Record<string, unknown>).sendspin_source = sourceBlock(null, [micRow(false)])
    bridge.snapshot = status
    await nextTick()
    await nextTick()

    expect(movedCards(w)).toEqual([])
    expect(playerCards(w).sort()).toEqual(['Alpha', 'Sony WH-1000XM4'])
  })

  it('counts the moved card in the Sources section for the six-card filter threshold', async () => {
    const devices = [
      headset(),
      ...['b', 'c', 'd', 'e', 'f'].map((id) => player(id, id.toUpperCase())),
    ]
    const w = mountHome(devices, INPUT_ID, [micRow(true)])
    await flushPromises()

    // Five players plus one source card: the filter bar still earns its space.
    expect(w.find('[role="searchbox"]').exists()).toBe(true)
    expect(playerCards(w)).toHaveLength(5)
    expect(movedCards(w)).toHaveLength(1)
  })

  it('keeps bulk selection to the players still in the players section', async () => {
    const devices = [headset(), ...['b', 'c', 'd', 'e', 'f'].map((id) => player(id, id.toUpperCase()))]
    const w = mountHome(devices, INPUT_ID, [micRow(true)])
    await flushPromises()

    await w.get('[aria-label="select B"]').trigger('click')
    await flushPromises()
    await w.get('[aria-label="Select all"]').trigger('change')
    await flushPromises()

    expect(w.text()).toContain('5 of 5 selected')
    // The source-mode card stays visible, it is simply not a bulk target.
    expect(movedCards(w)).toEqual(['Sony WH-1000XM4'])
  })

  it('searches both sections', async () => {
    const w = mountHome([headset(), player('dev-2', 'Alpha')], INPUT_ID, [micRow(true), lineRow])
    await flushPromises()

    const store = useDeviceStore()
    store.filter.search = 'sony'
    await nextTick()
    expect(movedCards(w)).toEqual(['Sony WH-1000XM4'])
    expect(playerCards(w)).toEqual([])

    store.filter.search = 'turn'
    await nextTick()
    await nextTick()
    expect(movedCards(w)).toEqual([])
    expect(w.find('[data-source-id="local:alsa_input.line"]').exists()).toBe(true)
  })

  it('shows the no-players empty state only when both sections are empty', async () => {
    const w = mountHome([], null)
    await flushPromises()

    expect(w.text()).toContain(en.devices.empty.title)
    expect(w.text()).not.toContain(en.common.noResults)
  })

  it('never calls the empty grid empty while the Sources section holds every player', async () => {
    const w = mountHome([headset()], INPUT_ID, [micRow(true)])
    await flushPromises()

    expect(w.text()).not.toContain(en.devices.empty.title)
    expect(movedCards(w)).toEqual(['Sony WH-1000XM4'])
  })
})
