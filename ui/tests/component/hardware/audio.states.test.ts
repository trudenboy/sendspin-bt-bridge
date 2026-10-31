import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { audio, capabilities, mountView, snapshot } from './support'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({
  getHardware: vi.fn(),
  getHardwareAudioCard: vi.fn(),
  getHardwareAudioSink: vi.fn(),
  getHardwareAudioSource: vi.fn(),
}))

let wrapper: ReturnType<typeof mountView> | null = null

const EMPTY = { cards: [], sinks: [], sources: [] }

describe('Hardware page: the three audio states', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('treats an absent audio field as a bridge that cannot report audio, not as no audio', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: undefined }) as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-audio-not-reported]').text()).toContain(en.hardware.audio.notReportedTitle)
    expect(wrapper.find('[data-audio-empty]').exists()).toBe(false)
    expect(wrapper.find('[data-audio-unavailable]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(en.hardware.audio.noDevices)
  })

  it('shows a reason banner when the audio server is unavailable', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        audio: audio(EMPTY),
        capabilities: capabilities({ audio_server: 'none', default_sink: false }),
        degraded: [
          {
            section: 'audio',
            code: 'pulse_unavailable',
            detail: 'No PulseAudio-compatible server answered; cards, sinks and sources cannot be read.',
          },
        ],
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const banner = wrapper.get('[data-audio-unavailable]')
    expect(banner.text()).toContain(en.hardware.audio.unavailableTitle)
    expect(banner.text()).toContain(en.hardware.audio.unavailableBody)
    expect(banner.text()).toContain('No PulseAudio-compatible server answered')
    expect(banner.text()).toContain('pulse_unavailable')
    expect(wrapper.find('[data-audio-empty]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(en.hardware.audio.noDevices)
    // The Bluetooth half still works: the page state is unchanged.
    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('ready')
  })

  it('still shows the reason when a bridge names no audio server and sends no degraded entry', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ audio: audio(EMPTY), capabilities: capabilities({ audio_server: 'none', default_sink: false }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-audio-unavailable]').text()).toContain(en.hardware.audio.unavailableTitle)
    expect(wrapper.find('[data-audio-empty]').exists()).toBe(false)
  })

  it('uses the empty state only when the audio server answered with nothing', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: audio(EMPTY) }) as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-audio-empty]').text()).toContain(en.hardware.audio.noDevices)
    expect(wrapper.get('[data-audio-empty]').text()).toContain(en.hardware.audio.noDevicesBody)
    expect(wrapper.find('[data-audio-unavailable]').exists()).toBe(false)
    expect(wrapper.find('[data-audio-not-reported]').exists()).toBe(false)
  })

  it('keeps the audio group out of the Bluetooth empty screen', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ audio: audio(EMPTY), controllers: [], bluetooth_devices: [] }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('empty')
    expect(wrapper.text()).toContain(en.hardware.noDevices)
    expect(wrapper.find('[data-audio-empty]').exists()).toBe(true)
  })
})
