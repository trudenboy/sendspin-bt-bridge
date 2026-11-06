import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import HomeView from '@/views/HomeView.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import { ApiError } from '@/api/client'
import { clearActiveSource, getSourceInputs, selectSource } from '@/api/source'
import { getStatus } from '@/api/status'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

/**
 * The source-mode switch is a multi-second HFP/A2DP operation. These tests
 * choose the card they press and hold the command open, so the transition the
 * operator sees and the section the card lives in are the real store's doing:
 * only the HTTP surface and the event stream are faked.
 */
vi.mock('@/api/audio', () => ({ getAudioSinks: vi.fn().mockResolvedValue([]) }))
vi.mock('@/api/playback', () => ({
  transport: vi.fn(),
  setVolume: vi.fn(),
  setMute: vi.fn(),
  setDevicePlayback: vi.fn(),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  selectSource: vi.fn(),
  clearActiveSource: vi.fn(),
  getSourceInputs: vi.fn(),
}))

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const SWITCHING_TO_SOURCE = 'Switching to source…'
const SWITCHING_TO_PLAYBACK = 'Back to playback…'

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

function sourceBlock(activeInput: string | null, inputs: Record<string, unknown>[] = []) {
  return {
    enabled: true,
    state: 'waiting',
    phone: null,
    phone_mac: null,
    paired_phone_mac: null,
    streaming: false,
    server: 'ws://ma.local:8927/sendspin',
    ma_connected: true,
    ma_paired: true,
    ma_pairing_required: false,
    ma_pairing_code: null,
    ma_pairing_seconds_left: 0,
    ma_error: null,
    pairing_seconds_left: 0,
    pairing_adapters: [],
    devices: [],
    inputs,
    target_player: null,
    target_error: null,
    active_input: activeInput,
    error: null,
  }
}

const headset = () =>
  makeDevice({
    id: 'dev-1',
    name: 'Sony WH-1000XM4',
    room: { name: 'Study' },
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, connected: true },
  } as never)

function statusFor(activeInput: string | null) {
  const status = makeStatus([headset()])
  ;(status.bridge as Record<string, unknown>).sendspin_source = sourceBlock(
    activeInput,
    [micRow(activeInput === INPUT_ID)],
  )
  return status
}

function show(status: ReturnType<typeof statusFor>) {
  const bridge = useBridgeStore()
  bridge.snapshot = status
  bridge.loading = false
  useSourceStore().applyStatus(status)
}

function mountHome(activeInput: string | null) {
  localStorage.setItem('sb-view-mode', 'grid')
  show(statusFor(activeInput))
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const wrapper = mount(HomeView, {
    attachTo: document.body,
    global: {
      plugins: [i18n, router],
      stubs: { DeviceDetailDrawer: true, AddSpeakerDialog: true },
    },
  })
  void router.push({ path: '/' })
  return wrapper
}

const playerCards = (w: ReturnType<typeof mountHome>) =>
  w.findAll('[data-device-card][data-role="output"]')
const movedCards = (w: ReturnType<typeof mountHome>) => w.findAll('[data-source-mode-card]')
const button = (w: ReturnType<typeof mountHome>, label: string) =>
  w.findAll('button').find((b) => b.text() === label)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let wrapper: ReturnType<typeof mountHome> | null = null

describe('HomeView: a source-mode switch in flight', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
    vi.mocked(getSourceInputs).mockResolvedValue({ inputs: [], local_candidates: [] } as never)
    vi.mocked(selectSource).mockResolvedValue({ active_input: INPUT_ID } as never)
    vi.mocked(clearActiveSource).mockResolvedValue(undefined as never)
    vi.mocked(getStatus).mockResolvedValue(statusFor(null) as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    useSourceStore().teardown()
    vi.useRealTimers()
  })

  it('shows the switch on the card, holds its section, and moves it when the command settles', async () => {
    const pending = deferred<{ active_input: string }>()
    vi.mocked(selectSource).mockReturnValue(pending.promise as never)
    vi.mocked(getStatus).mockResolvedValue(statusFor(INPUT_ID) as never)

    wrapper = mountHome(null)
    await flushPromises()
    expect(playerCards(wrapper)).toHaveLength(1)
    expect(movedCards(wrapper)).toHaveLength(0)

    await button(wrapper, en.devices.sources.enterSourceMode)!.trigger('click')
    await flushPromises()

    // The status chip names what is running, and the toggle cannot queue a
    // second, conflicting command while it does.
    expect(playerCards(wrapper)[0]!.text()).toContain(SWITCHING_TO_SOURCE)
    expect(button(wrapper, en.devices.sources.enterSourceMode)!.attributes('disabled')).toBeDefined()
    expect(movedCards(wrapper)).toHaveLength(0)

    // A status frame produced by the still-running switch puts the input live
    // early; the card stays where the operator pressed.
    show(statusFor(INPUT_ID))
    await nextTick()
    await nextTick()
    expect(movedCards(wrapper)).toHaveLength(0)
    expect(playerCards(wrapper)[0]!.text()).toContain(SWITCHING_TO_SOURCE)
    // The microphone is represented by the pinned card, not listed twice.
    expect(wrapper.find(`[data-source-id="${INPUT_ID}"]`).exists()).toBe(false)

    pending.resolve({ active_input: INPUT_ID })
    await flushPromises()

    expect(movedCards(wrapper)).toHaveLength(1)
    expect(playerCards(wrapper)).toHaveLength(0)
    expect(movedCards(wrapper)[0]!.text()).toContain('Sony WH-1000XM4')
  })

  it('keeps the card where it was and shows the refusal when the switch is refused', async () => {
    const pending = deferred<{ active_input: string }>()
    vi.mocked(selectSource).mockReturnValue(pending.promise as never)

    wrapper = mountHome(null)
    await flushPromises()

    await button(wrapper, en.devices.sources.enterSourceMode)!.trigger('click')
    await flushPromises()
    expect(playerCards(wrapper)[0]!.text()).toContain(SWITCHING_TO_SOURCE)

    pending.reject(
      new ApiError({
        status: 409,
        code: 'headset_profile_unavailable',
        detail: 'Could not switch the headset to HFP.',
      }),
    )
    await flushPromises()

    expect(playerCards(wrapper)).toHaveLength(1)
    expect(movedCards(wrapper)).toHaveLength(0)
    const card = playerCards(wrapper)[0]!
    expect(card.text()).toContain('Could not switch the headset to HFP.')
    expect(card.text()).not.toContain(SWITCHING_TO_SOURCE)
    expect(button(wrapper, en.devices.sources.enterSourceMode)!.attributes('disabled')).toBeUndefined()
  })

  it('shows the playback switch on the moved card and moves it back when the release settles', async () => {
    const pending = deferred<void>()
    vi.mocked(clearActiveSource).mockReturnValue(pending.promise as never)
    vi.mocked(getStatus).mockResolvedValue(statusFor(null) as never)

    wrapper = mountHome(INPUT_ID)
    await flushPromises()
    expect(movedCards(wrapper)).toHaveLength(1)
    expect(playerCards(wrapper)).toHaveLength(0)

    await button(wrapper, en.devices.sources.backToPlayback)!.trigger('click')
    await flushPromises()

    expect(movedCards(wrapper)[0]!.text()).toContain(SWITCHING_TO_PLAYBACK)
    expect(button(wrapper, en.devices.sources.backToPlayback)!.attributes('disabled')).toBeDefined()
    expect(playerCards(wrapper)).toHaveLength(0)

    // The A2DP restore publishes the cleared input before it answers; the
    // moved card stays put until the command actually settles.
    show(statusFor(null))
    await nextTick()
    await nextTick()
    expect(movedCards(wrapper)).toHaveLength(1)
    expect(playerCards(wrapper)).toHaveLength(0)
    expect(movedCards(wrapper)[0]!.text()).toContain(SWITCHING_TO_PLAYBACK)

    pending.resolve()
    await flushPromises()

    expect(movedCards(wrapper)).toHaveLength(0)
    expect(playerCards(wrapper)).toHaveLength(1)
    expect(playerCards(wrapper)[0]!.text()).toContain('Sony WH-1000XM4')
  })
})
