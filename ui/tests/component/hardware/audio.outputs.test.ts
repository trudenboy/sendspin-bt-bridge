import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import { resumeSink, setDefaultSink, setSinkMute, setSinkVolume, suspendSink } from '@/api/audio'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import en from '@/i18n/locales/en.json'
import { audio, audioSink, mountView, snapshot } from './support'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
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
vi.mock('@/api/audio', () => ({
  getAudioSinks: vi.fn(),
  setSinkVolume: vi.fn(),
  setSinkMute: vi.fn(),
  setDefaultSink: vi.fn(),
  suspendSink: vi.fn(),
  resumeSink: vi.fn(),
}))

let wrapper: ReturnType<typeof mountView> | null = null

const toast = (message: string) => useNotificationStore().toasts.some((entry) => entry.message === message)
const sinkState = (overrides: Record<string, unknown> = {}) => ({
  name: audioSink().name,
  volume: 60,
  muted: false,
  state: 'running',
  ...overrides,
})

describe('Hardware page: local outputs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(setSinkVolume).mockResolvedValue(sinkState() as never)
    vi.mocked(setSinkMute).mockResolvedValue(sinkState({ muted: true }) as never)
    vi.mocked(setDefaultSink).mockResolvedValue(sinkState() as never)
    vi.mocked(suspendSink).mockResolvedValue(sinkState({ state: 'suspended' }) as never)
    vi.mocked(resumeSink).mockResolvedValue(sinkState({ state: 'idle' }) as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('shows the description, state and the player using the output', async () => {
    const sink = audioSink({ in_use_by: 'kitchen' })
    vi.mocked(getHardware).mockResolvedValue(snapshot({ audio: audio({ sinks: [sink] }) }) as never)

    wrapper = mountView()
    await flushPromises()

    const row = wrapper.get('[data-audio-outputs] [data-endpoint-row]')
    expect(row.text()).toContain('Built-in audio output')
    expect(row.text()).toContain(en.hardware.audio.stateRunning)
    expect(row.text()).toContain(en.hardware.audio.inUse)
    const link = row.get('[data-endpoint-player]')
    expect(link.attributes('href')).toBe('/')
    expect(link.text()).toBe('kitchen')
  })

  it('mutes through the undo policy and offers Undo', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-mute-toggle]').trigger('click')
    await flushPromises()

    expect(setSinkMute).toHaveBeenCalledWith(audioSink().name, true)
    expect(toast(en.undo.audioMuted.replace('{name}', 'Built-in audio output'))).toBe(true)
    expect(useNotificationStore().toasts.some((entry) => entry.action?.label === en.undo.label)).toBe(true)
  })

  it('sends the volume the slider settles on and shows the level in the toast', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-endpoint-volume] input').setValue('75')
    await new Promise((resolve) => setTimeout(resolve, 300))
    await flushPromises()

    expect(setSinkVolume).toHaveBeenCalledWith(audioSink().name, 75)
    expect(toast(en.undo.audioVolume.replace('{name}', 'Built-in audio output (75%)'))).toBe(true)
  })

  it('asks before pausing an output and patches the row to Paused', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-suspend]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    const options = confirm.confirmDialog.mock.calls[0]![0]
    expect(options.title).toContain('Built-in audio output')
    expect(options.consequences.join(' ')).toContain('Built-in audio output')
    expect(options.danger).toBe(true)
    expect(suspendSink).toHaveBeenCalledWith(audioSink().name)
    expect(toast(en.hardware.audio.suspended.replace('{name}', 'Built-in audio output'))).toBe(true)
    expect(wrapper.get('[data-endpoint-state]').text()).toContain(en.hardware.audio.stateSuspended)
    expect(wrapper.find('[data-resume]').exists()).toBe(true)
  })

  it('resumes a paused output without another dialog', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ audio: audio({ sinks: [audioSink({ state: 'suspended' })] }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-resume]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).not.toHaveBeenCalled()
    expect(resumeSink).toHaveBeenCalledWith(audioSink().name)
    expect(toast(en.undo.audioResumed.replace('{name}', 'Built-in audio output'))).toBe(true)
  })

  it('makes an output the default after a neutral question', async () => {
    vi.mocked(getHardware)
      .mockResolvedValueOnce(snapshot() as never)
      // The refresh after the write is what says who is default now.
      .mockResolvedValueOnce(
        snapshot({ audio: audio({ sinks: [audioSink({ is_default: true })] }) }) as never,
      )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-set-default]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    expect(confirm.confirmDialog.mock.calls[0]![0].danger).toBe(false)
    expect(setDefaultSink).toHaveBeenCalledWith(audioSink().name)
    expect(toast(en.hardware.audio.defaultSet.replace('{name}', 'Built-in audio output'))).toBe(true)
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-default-marker]').exists()).toBe(true)
    expect(wrapper.find('[data-set-default]').exists()).toBe(false)
  })

  it('shows the Default badge from the read model and offers no redundant action', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ audio: audio({ sinks: [audioSink({ is_default: true })] }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const row = wrapper.get('[data-audio-outputs] [data-endpoint-row]')
    expect(row.find('[data-default-marker]').text()).toBe(en.hardware.audio.default)
    expect(row.find('[data-set-default]').exists()).toBe(false)
  })

  it('moves the Default badge off the previous output after the refresh', async () => {
    const hdmi = audioSink({ name: 'alsa_output.pci-0000_00_1f.3.hdmi-stereo', description: 'HDMI output' })
    vi.mocked(getHardware)
      .mockResolvedValueOnce(
        snapshot({ audio: audio({ sinks: [audioSink({ is_default: true }), hdmi] }) }) as never,
      )
      .mockResolvedValueOnce(
        snapshot({
          audio: audio({ sinks: [audioSink({ is_default: false }), { ...hdmi, is_default: true }] }),
        }) as never,
      )

    wrapper = mountView()
    await flushPromises()

    let rows = wrapper.findAll('[data-audio-outputs] [data-endpoint-row]')
    expect(rows[0]!.find('[data-default-marker]').exists()).toBe(true)
    expect(rows[1]!.find('[data-default-marker]').exists()).toBe(false)

    await rows[1]!.get('[data-set-default]').trigger('click')
    await flushPromises()

    rows = wrapper.findAll('[data-audio-outputs] [data-endpoint-row]')
    expect(rows[0]!.find('[data-default-marker]').exists()).toBe(false)
    expect(rows[1]!.find('[data-default-marker]').exists()).toBe(true)
    expect(wrapper.findAll('[data-default-marker]')).toHaveLength(1)
  })

  it('explains an output whose active port is unavailable without claiming it hears nothing', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        audio: audio({ sinks: [audioSink({ active_port_available: 'not available' })] }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const row = wrapper.get('[data-audio-outputs] [data-endpoint-row]')
    expect(row.text()).toContain(en.hardware.audio.portUnavailable)
    expect(row.get('[data-output-reason]').text()).toBe(en.hardware.audio.outputPortUnavailableReason)
    expect(row.find('[data-input-reason]').exists()).toBe(false)
    expect(row.text()).not.toContain(en.hardware.audio.portUnavailableReason)
  })

  it('surfaces the server code when a command is refused', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(suspendSink).mockRejectedValue(
      new ApiError({ status: 409, code: 'not_supported_for_backend', detail: 'The audio server refused it.' }),
    )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-suspend]').trigger('click')
    await flushPromises()

    const errors = useNotificationStore().toasts.filter((entry) => entry.type === 'error')
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toContain('not_supported_for_backend')
    expect(errors[0]!.message).toContain('The audio server refused it.')
  })
})
