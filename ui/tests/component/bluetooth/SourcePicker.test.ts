import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourcePicker from '@/components/bluetooth/SourcePicker.vue'
import { ApiError } from '@/api/client'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import { getStatus } from '@/api/status'
import { addLocalSource, closeSourcePairing, getSourceInputs, openSourcePairing, selectSource } from '@/api/source'
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

/** A bond BlueZ already knew when the window opened: not the phone being added. */
const existingBond = {
  id: 'bluetooth:FC:58:FA:EB:08:6C',
  kind: 'bluetooth',
  name: 'ENEBY20',
  description: 'ENEBY20',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: 'FC:58:FA:EB:08:6C',
  node: null,
}

/** The phone that bonds during the window: it is the new source. */
const phoneBond = {
  ...existingBond,
  id: 'bluetooth:34:C7:E9:25:A3:A3',
  name: 'Pixel',
  mac: '34:C7:E9:25:A3:A3',
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

  it('an existing bond is not taken for the new phone when the dialog mounts mid-window', async () => {
    vi.useFakeTimers()
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    // The window was opened before this dialog mounted; its clock lives in the
    // source store. Nothing new has paired, but a bond is already listed.
    useSourceStore().applyStatus({
      bridge: { sendspin_source: sourceBlock({ pairing_seconds_left: 120 }) },
      devices: [],
      groups: [],
    } as never)
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    const before = vi.mocked(getSourceInputs).mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)

    expect(selectSource).not.toHaveBeenCalled()
    expect(wrapper.find('[data-source-added]').exists()).toBe(false)
    // The 2 s poll is still running: one more read, not a stopped window.
    expect(vi.mocked(getSourceInputs).mock.calls.length).toBe(before + 1)
    wrapper.unmount()
  })

  it('a phone that bonds during the window is still taken for the new source', async () => {
    vi.useFakeTimers()
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    // The next poll sees the phone's new bond above the existing one.
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond, phoneBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(0)

    expect(selectSource).toHaveBeenCalledWith(phoneBond.id)
    expect(wrapper.get('[data-source-added]').text()).toContain('Pixel')
    wrapper.unmount()
  })

  it('a list already loading when Pair is clicked measures the baseline before the window opens', async () => {
    vi.useFakeTimers()
    let resolveInputs!: (value: unknown) => void
    vi.mocked(getSourceInputs).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveInputs = resolve
      }) as never,
    )
    let resolveOpen!: (value: unknown) => void
    vi.mocked(openSourcePairing).mockReturnValue(
      new Promise((resolve) => {
        resolveOpen = resolve
      }) as never,
    )
    statusWith(sourceBlock({ pairing_seconds_left: 0 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    // Pair is clicked while the first list is still loading.
    await wrapper.get('[data-source-pair]').trigger('click')

    // The list answers before the open does, with the bond that predates it.
    resolveInputs({
      active_input: null,
      inputs: [existingBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    })
    await vi.advanceTimersByTimeAsync(0)

    // The open answer lands afterwards and starts the window.
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    resolveOpen({ pairing_seconds_left: 120, pairing_adapters: [], error: null })
    await vi.advanceTimersByTimeAsync(0)

    // The next poll sees the phone's new bond above the one that was there.
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond, phoneBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(0)

    expect(selectSource).toHaveBeenCalledWith(phoneBond.id)
    expect(wrapper.get('[data-source-added]').text()).toContain('Pixel')
    wrapper.unmount()
  })

  it('a list that answers after the window opened still measures the baseline before the phone', async () => {
    vi.useFakeTimers()
    let resolveInputs!: (value: unknown) => void
    vi.mocked(getSourceInputs).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveInputs = resolve
      }) as never,
    )
    let resolveOpen!: (value: unknown) => void
    vi.mocked(openSourcePairing).mockReturnValue(
      new Promise((resolve) => {
        resolveOpen = resolve
      }) as never,
    )
    statusWith(sourceBlock({ pairing_seconds_left: 0 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    await wrapper.get('[data-source-pair]').trigger('click')

    // The open answer lands first; the first list follows with the old bond.
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    resolveOpen({ pairing_seconds_left: 120, pairing_adapters: [], error: null })
    await vi.advanceTimersByTimeAsync(0)
    resolveInputs({
      active_input: null,
      inputs: [existingBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    })
    await vi.advanceTimersByTimeAsync(0)

    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond, phoneBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(0)

    expect(selectSource).toHaveBeenCalledWith(phoneBond.id)
    expect(wrapper.get('[data-source-added]').text()).toContain('Pixel')
    wrapper.unmount()
  })

  it('a failed first list read never seeds a baseline from nothing', async () => {
    vi.useFakeTimers()
    vi.mocked(getSourceInputs).mockRejectedValueOnce(new Error('bridge unreachable'))
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)

    // Pair is clicked after the first read failed: there is no list to measure.
    await wrapper.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    // The 2 s poll recovers and lists the bond that was already there.
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(0)

    expect(selectSource).not.toHaveBeenCalled()
    expect(wrapper.find('[data-source-added]').exists()).toBe(false)

    // The poll keeps running: the old bond was not mistaken for the phone.
    const reads = vi.mocked(getSourceInputs).mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(vi.mocked(getSourceInputs).mock.calls.length).toBe(reads + 1)
    wrapper.unmount()
  })

  it('identifies the phone from the mac the server reports when the first list failed', async () => {
    vi.useFakeTimers()
    vi.mocked(getSourceInputs).mockRejectedValueOnce(new Error('bridge unreachable'))
    statusWith(sourceBlock({ pairing_seconds_left: 120 }))
    const wrapper = mountPicker()
    await vi.advanceTimersByTimeAsync(0)
    await wrapper.get('[data-source-pair]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)

    // The phone bonded before the first trusted list; the status names it.
    useSourceStore().applyStatus({
      bridge: { sendspin_source: sourceBlock({ pairing_seconds_left: 120, paired_phone_mac: phoneBond.mac }) },
      devices: [],
      groups: [],
    } as never)
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [existingBond, phoneBond],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(0)

    expect(selectSource).toHaveBeenCalledWith(phoneBond.id)
    expect(wrapper.get('[data-source-added]').text()).toContain('Pixel')
    wrapper.unmount()
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

  it('never offers a player microphone as a source to add', async () => {
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [
        existingBond,
        {
          ...existingBond,
          id: 'headset-mic:AA:BB:CC:DD:EE:FF',
          kind: 'headset_microphone',
          name: 'Headset microphone',
          mac: 'AA:BB:CC:DD:EE:FF',
        },
      ],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    const wrapper = mountPicker()
    await flushPromises()

    // "Add a source" covers phones (Bluetooth bonds) and local line-ins; a
    // player's headset microphone is switched on from its own card instead.
    expect(wrapper.find('[data-source-bluetooth-list]').text()).toContain('ENEBY20')
    expect(wrapper.find('[data-source-bluetooth-list]').text()).not.toContain('Headset microphone')
    expect(wrapper.findAll('[data-source-bluetooth-list] li')).toHaveLength(1)
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
