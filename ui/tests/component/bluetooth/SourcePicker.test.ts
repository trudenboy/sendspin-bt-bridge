import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourcePicker from '@/components/bluetooth/SourcePicker.vue'
import { ApiError } from '@/api/client'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import { getStatus } from '@/api/status'
import { addLocalSource, closeSourcePairing, getSourceInputs, openSourcePairing } from '@/api/source'
import en from '@/i18n/locales/en.json'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', () => ({
  getSourceInputs: vi.fn(),
  addLocalSource: vi.fn(),
  selectSource: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
  setSourceEnabled: vi.fn(),
  SOURCE_PAIRING_WINDOW_S: 120,
}))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const candidate = {
  id: 'local:alsa_input.line',
  kind: 'local',
  name: 'Line In',
  description: 'Line In',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: null,
  node: 'alsa_input.line',
}

const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
  ma_paired: false,
  ma_connected: false,
  ma_pairing_code: null,
  ma_pairing_seconds_left: 0,
  pairing_seconds_left: 0,
  pairing_adapters: [],
  devices: [],
  error: null,
  ...overrides,
})

/** The bridge's own answer for the request a command reconciles with. */
function statusWith(block: Record<string, unknown> | null) {
  vi.mocked(getStatus).mockResolvedValue({ bridge: { sendspin_source: block }, devices: [], groups: [] } as never)
}

function mountPicker() {
  useBridgeStore().snapshot = { bridge: { sendspin_source: null }, devices: [], groups: [] } as never
  return mount(SourcePicker, { global: { plugins: [i18n] } })
}

describe('Add device: audio source', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    statusWith(null)
    vi.mocked(openSourcePairing).mockResolvedValue({
      pairing_seconds_left: 120,
      pairing_adapters: [],
      error: null,
    } as never)
    vi.mocked(addLocalSource).mockResolvedValue({ id: 'local:alsa_input.line' } as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    useSourceStore().teardown()
    vi.useRealTimers()
  })

  it('adds a local audio input without requiring MA or source to be on', async () => {
    const wrapper = mountPicker()
    await flushPromises()

    await wrapper.get('[data-source-mode-local]').trigger('click')
    await wrapper.get('[data-source-local-list] button').trigger('click')
    await wrapper.get('[data-source-name] input').setValue('Turntable')
    await wrapper.get('[data-source-local-add]').trigger('click')
    await flushPromises()

    expect(getSourceInputs).toHaveBeenCalled()
    expect(addLocalSource).toHaveBeenCalledWith('alsa_input.line', 'Turntable', false)
  })

  it('opens the Bluetooth pairing window independently of MA', async () => {
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(wrapper.find('[data-source-pairing]').exists()).toBe(true)
  })

  it('unmounting stops this view only: the server-side window is never closed', async () => {
    vi.useFakeTimers()
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.find('[data-source-pairing]').exists()).toBe(true)

    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(0)

    expect(closeSourcePairing).not.toHaveBeenCalled()
    expect(useSourceStore().pairingSecondsLeft).toBeGreaterThan(0)
  })

  it('the window survives a remount without restarting its countdown', async () => {
    vi.useFakeTimers()
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const first = mountPicker()
    await vi.advanceTimersByTimeAsync(0)
    await first.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    expect(first.get('[data-source-pairing]').text()).toContain('1:59')
    first.unmount()

    // Re-opening the dialog re-reads the countdown; it must not go back to 2:00.
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const second = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    expect(second.get('[data-source-pairing]').text()).toContain('1:59')
  })

  it('cancelling the window is the explicit server-side close', async () => {
    vi.useFakeTimers()
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    statusWith(sourceBlock({ pairing_seconds_left: 0 }))
    await wrapper.get('[data-source-pairing-stop]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    expect(closeSourcePairing).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-source-pairing]').exists()).toBe(false)
  })

  it('shows the failure when the window cannot be opened', async () => {
    vi.mocked(openSourcePairing).mockRejectedValue(new ApiError({ status: 422, code: 'no_agent', detail: 'No agent' }))
    const wrapper = mountPicker()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-source-error]').text()).toBe(en.addSpeaker.source.pairFailed)
    expect(useSourceStore().error).toMatchObject({ code: 'no_agent' })
  })

  it('does not allow local add when capture is unavailable', async () => {
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [],
      local_candidates: [candidate],
      capture_available: false,
      capture_kind: 'pulseaudio',
    } as never)
    const wrapper = mountPicker()
    await flushPromises()
    await wrapper.get('[data-source-mode-local]').trigger('click')

    expect(wrapper.get('[data-source-capture-unavailable]').exists()).toBe(true)
    expect(wrapper.get('[data-source-local-list] button').attributes('disabled')).toBeDefined()
  })
})
