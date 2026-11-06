import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import DeviceCard from '@/components/devices/DeviceCard.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import { selectSource } from '@/api/source'
import { getStatus } from '@/api/status'
import en from '@/i18n/locales/en.json'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  selectSource: vi.fn(),
}))

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function account(overrides: Record<string, unknown> = {}) {
  const block = {
    enabled: true,
    state: 'waiting',
    active_input: null,
    inputs: [
      {
        id: INPUT_ID,
        kind: 'headset_microphone',
        name: 'Headset',
        description: 'HFP microphone',
        active: false,
        available: true,
        connected: true,
        monitor: false,
        mac: MAC,
        node: null,
      },
    ],
    target_player: null,
    target_error: null,
    error: null,
    ...overrides,
  }
  useBridgeStore().snapshot = {
    bridge: { sendspin_source: block },
    devices: [],
    groups: [],
  } as never
  vi.mocked(getStatus).mockResolvedValue({ bridge: { sendspin_source: block }, devices: [], groups: [] } as never)
}

const capable = (overrides: Record<string, unknown> = {}) =>
  makeDevice({
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true },
    ...overrides,
  } as never)

function mountCard(device: Device = capable()) {
  return mount(DeviceCard, { props: { device }, global: { plugins: [i18n] } })
}

const toggleButton = (w: ReturnType<typeof mountCard>, label: string) =>
  w.findAll('button').find((b) => b.text() === label)

describe('DeviceCard: the source mode toggle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(selectSource).mockResolvedValue({ active_input: INPUT_ID } as never)
    account()
  })

  it('offers Source on a capable player and makes the microphone the active input', async () => {
    const w = mountCard()
    const button = toggleButton(w, 'Source')
    expect(button, 'Source').toBeTruthy()

    await button!.trigger('click')
    await flushPromises()

    expect(selectSource).toHaveBeenCalledWith(INPUT_ID)
  })

  it('draws no toggle for a player without a microphone', () => {
    const w = mountCard(makeDevice({ bluetooth: { mac: MAC } } as never))
    expect(toggleButton(w, 'Source')).toBeUndefined()
  })

  it('draws no toggle while the feature is off', () => {
    account({ enabled: false })
    expect(toggleButton(mountCard(), 'Source')).toBeUndefined()
  })

  it('disables the toggle and says why when the speaker is disconnected', () => {
    account()
    const w = mountCard(capable({ bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, connected: false } }))
    const button = toggleButton(w, 'Source')

    expect(button?.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.devices.sources.unavailableDisconnected)
  })

  it('disables the toggle in standby and for a disabled player', () => {
    account()
    expect(
      toggleButton(
        mountCard(capable({ bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, standby: true } })),
        'Source',
      )?.attributes('disabled'),
    ).toBeDefined()
    expect(toggleButton(mountCard(capable({ enabled: false })), 'Source')?.attributes('disabled')).toBeDefined()
  })

  it('keeps the source command failure as the store error', async () => {
    vi.mocked(selectSource).mockRejectedValue(new Error('refused'))
    const w = mountCard()

    await toggleButton(w, 'Source')!.trigger('click')
    await flushPromises()

    expect(useSourceStore().error).toMatchObject({ code: 'request_failed' })
  })
})
