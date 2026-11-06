import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import SourceModeCard from '@/components/devices/SourceModeCard.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import {
  clearActiveSource,
  getSourceInputs,
  setActiveMute,
  setActiveVolume,
} from '@/api/source'
import { retryA2dp } from '@/api/devices'
import { getStatus } from '@/api/status'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  getSourceInputs: vi.fn(),
  clearActiveSource: vi.fn(),
  setActiveVolume: vi.fn(),
  setActiveMute: vi.fn(),
}))
vi.mock('@/api/devices', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/devices')>()),
  retryA2dp: vi.fn(),
}))

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const micInput = (overrides: Record<string, unknown> = {}) => ({
  id: INPUT_ID,
  kind: 'headset_microphone',
  name: 'Headset',
  description: 'HFP microphone',
  active: true,
  available: true,
  connected: true,
  monitor: false,
  mac: MAC,
  node: null,
  volume: 40,
  muted: false,
  ...overrides,
})

const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'streaming',
  phone: null,
  phone_mac: null,
  paired_phone_mac: null,
  streaming: true,
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
  inputs: [micInput()],
  target_player: null,
  target_error: null,
  active_input: INPUT_ID,
  error: null,
  ...overrides,
})

function setStatus(overrides: Record<string, unknown> = {}) {
  const block = sourceBlock(overrides)
  const status = { bridge: { sendspin_source: block }, devices: [], groups: [] }
  useBridgeStore().snapshot = status as never
  useSourceStore().applyStatus(status as never)
  vi.mocked(getStatus).mockResolvedValue(status as never)
}

const happyDevice = (overrides: Record<string, unknown> = {}) =>
  makeDevice({
    name: 'Sony WH-1000XM4 @ bridge',
    room: { name: 'Study' },
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, connected: true },
    ...overrides,
  } as never)

function mountCard(device: Device = happyDevice()) {
  return mount(SourceModeCard, { props: { device }, global: { plugins: [i18n] } })
}

const labelled = (w: ReturnType<typeof mountCard>, label: string) =>
  w.findAll('button').find((b) => b.attributes('aria-label') === label || b.text() === label)

describe('SourceModeCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getSourceInputs).mockResolvedValue({ inputs: [], local_candidates: [] } as never)
    vi.mocked(clearActiveSource).mockResolvedValue(undefined as never)
    vi.mocked(setActiveVolume).mockResolvedValue({} as never)
    vi.mocked(setActiveMute).mockResolvedValue({} as never)
    vi.mocked(retryA2dp).mockResolvedValue(undefined as never)
    setStatus()
  })

  afterEach(() => {
    useSourceStore().teardown()
    vi.useRealTimers()
  })

  it('wears the input role on the same identity and offers Back to playback', () => {
    const w = mountCard()
    expect(w.get('[data-device-card]').attributes('data-role')).toBe('input')
    expect(w.text()).toContain('Sony WH-1000XM4')
    expect(w.text()).toContain('Study')
    expect(labelled(w, en.devices.sources.backToPlayback)).toBeTruthy()
  })

  it('releases the input when Back to playback is chosen', async () => {
    const w = mountCard()
    await labelled(w, en.devices.sources.backToPlayback)!.trigger('click')
    await flushPromises()

    expect(clearActiveSource).toHaveBeenCalledOnce()
  })

  it('moves the gain and sends it to the active input', async () => {
    vi.useFakeTimers()
    const w = mountCard()
    const gain = w.get('input[aria-label="Gain"]')

    await gain.setValue('70')
    await vi.advanceTimersByTimeAsync(300)

    expect(setActiveVolume).toHaveBeenCalledWith(70)
  })

  it('mutes and unmutes the active input', async () => {
    const w = mountCard()
    await labelled(w, en.devices.sources.mute)!.trigger('click')
    await flushPromises()

    expect(setActiveMute).toHaveBeenCalledWith(true)
  })

  it('shows the live level on the meter', async () => {
    const w = mountCard()
    useSourceStore().applyLevel({ id: INPUT_ID, level: 0.5, muted: false })
    await w.vm.$nextTick()

    const meter = w.get('[role="meter"]')
    expect(meter.attributes('aria-valuenow')).toBe('50')
  })

  it('shows waiting for stream while the capture is not open, with the input controls', () => {
    setStatus({ state: 'waiting', streaming: false })
    const w = mountCard()

    expect(w.find('[data-source-waiting]').exists()).toBe(true)
    expect(w.find('input[aria-label="Gain"]').exists()).toBe(true)
    expect(w.find('[data-source-mute]').exists()).toBe(true)
    expect(w.find('[role="meter"]').exists()).toBe(true)
  })

  it('does not call this device Streaming while it is not the captured input', () => {
    setStatus({ state: 'streaming', streaming: true, inputs: [micInput({ active: false })] })
    const w = mountCard()

    expect(w.find('[data-source-waiting]').exists()).toBe(true)
    expect(w.get('[data-status]').text()).toContain(en.devices.sources.waitingForStream)
    expect(w.get('[data-status]').text()).not.toContain(en.devices.sources.streaming)
  })

  it('hints after five seconds of silence while the capture is open', async () => {
    vi.useFakeTimers()
    const w = mountCard()
    useSourceStore().applyLevel({ id: INPUT_ID, level: 0, muted: false })
    await vi.advanceTimersByTimeAsync(4_000)
    expect(w.find('[data-source-no-signal]').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(1_100)
    expect(w.get('[data-source-no-signal]').text()).toContain(en.devices.sources.noSignal)
  })

  it('shows the failed A2DP restore and retries it on this card', async () => {
    setStatus({ error: 'headset_a2dp_restore_failed' })
    const w = mountCard()

    expect(w.get('[data-source-a2dp-failure]').text()).toContain(en.hardware.source.errorHeadsetA2dp)
    await w.get('[data-source-a2dp-retry]').trigger('click')
    await flushPromises()

    expect(retryA2dp).toHaveBeenCalledWith('dev-1')
  })

  it('shows the failure the device health reports', () => {
    setStatus({ error: null })
    const w = mountCard(happyDevice({ health: { code: 'a2dp_unavailable' } }))
    expect(w.get('[data-source-a2dp-failure]').text()).toContain(en.device.a2dpUnavailable)
  })

  it('disables Back to playback and says why while the speaker is disconnected', () => {
    const w = mountCard(
      happyDevice({ bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, connected: false } }),
    )
    const button = labelled(w, en.devices.sources.backToPlayback)!

    expect(button.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.devices.sources.unavailableDisconnected)
  })
})
