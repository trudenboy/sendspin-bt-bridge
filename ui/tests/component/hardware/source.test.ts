import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import { closeSourcePairing, forgetSourceDevice, openSourcePairing, useSourceDevice } from '@/api/source'
import en from '@/i18n/locales/en.json'
import { mountView, snapshot, sourceFeed } from './support'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/source', () => ({
  SOURCE_PAIRING_WINDOW_S: 120,
  setSourceEnabled: vi.fn(),
  openMaPairing: vi.fn(),
  cancelMaPairing: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
  useSourceDevice: vi.fn(),
  forgetSourceDevice: vi.fn(),
}))

let wrapper: ReturnType<typeof mountView> | null = null

const phoneWindow = () => ({
  pairing_seconds_left: 120,
  pairing_adapters: ['C0:FB:F9:62:D6:9D'],
  error: null,
})

const devices = [
  { mac: '34:C7:E9:25:A3:A3', name: 'Pixel', connected: true, active: true },
  { mac: 'FC:58:FA:EB:08:6C', name: 'Tablet', connected: false, active: false },
]

describe('Hardware page: source strip', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed() }) as never)
    vi.mocked(openSourcePairing).mockResolvedValue(phoneWindow() as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
    vi.mocked(useSourceDevice).mockResolvedValue({} as never)
    vi.mocked(forgetSourceDevice).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  it('does not show the source strip when the source is off', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: null }) as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-source-panel]').exists()).toBe(false)
  })

  it('does not show the source strip before Music Assistant pairing', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ enabled: true, ma: { paired: false, connected: false, pairing_required: true, pairing_code: '12345678', server: null, pairing_seconds_left: 0, error: null } }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-source-panel]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('12345678')
  })

  it('lists paired source devices and marks the active one', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed({ devices }) }) as never)

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.findAll('[data-source-device]')).toHaveLength(2)
    expect(panel.get('[data-source-device="34:C7:E9:25:A3:A3"]').attributes('data-source-device-active')).toBe('true')
    expect(panel.get('[data-source-device="FC:58:FA:EB:08:6C"] [data-source-use]').exists()).toBe(true)
  })

  it('switches the active source device', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed({ devices }) }) as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-device="FC:58:FA:EB:08:6C"] [data-source-use]').trigger('click')
    await flushPromises()

    expect(useSourceDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
  })

  it('forgets a source device after confirmation', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed({ devices }) }) as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-device="FC:58:FA:EB:08:6C"] [data-source-forget]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalled()
    expect(forgetSourceDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
  })

  it('opens the device pairing window and closes it', async () => {
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    expect(wrapper.get('[data-source-pairing]').text()).toContain(en.hardware.source.pairing.replace('{time}', '2:00'))

    await wrapper.get('[data-source-pairing-stop]').trigger('click')
    await flushPromises()

    expect(closeSourcePairing).toHaveBeenCalledOnce()
  })

  it('shows a source error without a Music Assistant pairing block', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ state: 'error', error: 'capture_unavailable' }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.get('[data-source-error]').text()).toContain('capture_unavailable')
    expect(panel.find('[data-ma-pairing], [data-source-ma-code]').exists()).toBe(false)
  })
})
