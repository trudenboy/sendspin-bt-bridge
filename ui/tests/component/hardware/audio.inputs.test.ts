import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { audio, audioSource, mountView, snapshot } from './support'

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

const kinds = (root: Element | null) =>
  [...(root?.querySelectorAll('[data-input-kind]') ?? [])].map((node) => node.getAttribute('data-kind'))

describe('Hardware page: local inputs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('names the kind of every input: microphone, line-in, monitor and the phone', async () => {
    const sources = [
      audioSource(),
      audioSource({ name: 'alsa_input.pci-0000_00_1f.3.linein', description: 'Line In' }),
      audioSource({ name: 'alsa_output.pci-0000_00_1f.3.analog-stereo.monitor', description: 'Monitor of Built-in Audio' }),
      audioSource({
        name: 'bluez_source.FC_58_FA_EB_08_6C',
        description: 'ENEBY20 capture',
        owner: { card: null, bluetooth: 'FC:58:FA:EB:08:6C', internal: null },
      }),
    ]
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: audio({ sources }) }) as never)

    wrapper = mountView()
    await flushPromises()

    expect(kinds(wrapper.get('[data-audio-inputs]').element)).toEqual(['microphone', 'lineIn', 'monitor', 'phone'])
    const text = wrapper.get('[data-audio-inputs]').text()
    expect(text).toContain(en.hardware.audio.kindMicrophone)
    expect(text).toContain(en.hardware.audio.kindLineIn)
    expect(text).toContain(en.hardware.audio.kindMonitor)
    expect(text).toContain(en.hardware.audio.kindPhone)
  })

  it('explains an input whose active port is unavailable instead of leaving a bare row', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        audio: audio({
          sources: [audioSource({ active_port_available: 'not available', name: 'alsa_input.pci-0000_00_1f.3.linein' })],
        }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const row = wrapper.get('[data-audio-inputs] [data-endpoint-row]')
    expect(row.text()).toContain(en.hardware.audio.portUnavailable)
    expect(row.get('[data-input-reason]').text()).toContain(en.hardware.audio.portUnavailableReason)
  })

  it('shows the capture reason when the phone cannot be captured, never an empty list', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        audio: audio({ cards: [], sinks: [], sources: [] }),
        degraded: [
          {
            section: 'audio',
            code: 'capture_unavailable',
            detail: 'No bluez_input node and no bluez_source is present; the phone is not streaming.',
          },
        ],
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const notice = wrapper.get('[data-capture-unavailable]')
    expect(notice.text()).toContain(en.hardware.audio.captureUnavailableTitle)
    expect(notice.text()).toContain('No bluez_input node and no bluez_source is present')
    expect(notice.text()).toContain('capture_unavailable')
    expect(wrapper.find('[data-audio-empty]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(en.hardware.audio.noDevices)
  })

  it('shows the phone input beside the local ones when the capture is the only gap', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        degraded: [
          {
            section: 'audio',
            code: 'capture_unavailable',
            detail: 'No bluez_input node is present; the phone is not streaming.',
          },
        ],
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    // The local microphone still has its row; the phone has a reason of its own.
    expect(wrapper.findAll('[data-audio-inputs] [data-endpoint-row]')).toHaveLength(1)
    expect(wrapper.get('[data-capture-unavailable]').text()).toContain('No bluez_input node is present')
  })
})
