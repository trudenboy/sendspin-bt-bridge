import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware, getHardwareAudioCard, getHardwareAudioSink, getHardwareAudioSource } from '@/api/hardware'
import { setCardProfile, setSinkPort } from '@/api/audio'
import { ApiError } from '@/api/client'
import en from '@/i18n/locales/en.json'
import { seriousViolations } from '../../helpers/axe'
import { audioCard, audioCardDetail, audioEndpointDetail, audioSink, mountView, snapshot } from './support'

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
  setCardProfile: vi.fn(),
  setSinkPort: vi.fn(),
}))

let wrapper: ReturnType<typeof mountView> | null = null

/** The drawer lives in a teleport, so its controls are read from the document. */
const node = <T extends Element>(selector: string) => document.querySelector(selector) as T | null

function pick(selector: string, value: string) {
  const select = node<HTMLSelectElement>(selector)
  expect(select, selector).not.toBeNull()
  select!.value = value
  select!.dispatchEvent(new Event('change'))
}

async function openCard() {
  wrapper = mountView()
  await flushPromises()
  await wrapper.get('[data-audio-card-details]').trigger('click')
  await flushPromises()
  await flushPromises()
}

async function openSink() {
  wrapper = mountView()
  await flushPromises()
  await wrapper.get('[data-endpoint-ref][data-ref-kind="sink"]').trigger('click')
  await flushPromises()
  await flushPromises()
}

async function openSource() {
  wrapper = mountView()
  await flushPromises()
  await wrapper.get('[data-endpoint-ref][data-ref-kind="source"]').trigger('click')
  await flushPromises()
  await flushPromises()
}

const problem = (status: number, code: string, detail = 'Nope') => new ApiError({ status, code, detail })

describe('Hardware page: card profiles', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getHardwareAudioCard).mockResolvedValue(audioCardDetail() as never)
    vi.mocked(getHardwareAudioSink).mockResolvedValue(audioEndpointDetail() as never)
    vi.mocked(getHardwareAudioSource).mockResolvedValue(
      audioEndpointDetail({ kind: 'source', volume: null, muted: null, in_use_by: null }) as never,
    )
    vi.mocked(setCardProfile).mockResolvedValue(audioCardDetail({ active_profile: 'off' }) as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('switches a profile after naming the card and shows the returned detail without a second read', async () => {
    await openCard()

    const select = node<HTMLSelectElement>('[data-profile-select]')
    expect(select).not.toBeNull()
    expect(select!.value).toBe('output:analog-stereo')
    // The option carries the name and the description, not just one of them.
    const options = [...select!.options].map((option) => option.textContent ?? '')
    expect(options.some((text) => text.includes('Analog Stereo Output'))).toBe(true)
    expect(options.some((text) => text.includes('output:analog-stereo'))).toBe(true)
    expect(options.some((text) => text.includes(en.hardware.audio.profileActive))).toBe(true)

    pick('[data-profile-select]', 'off')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    const dialog = confirm.confirmDialog.mock.calls[0]![0]
    expect(dialog.title).toContain(audioCard().name)
    expect(dialog.consequences.join(' ')).toContain(audioCard().name)
    expect(dialog.danger).toBe(true)
    expect(setCardProfile).toHaveBeenCalledWith(audioCard().name, 'off')

    await flushPromises()

    // The write's own answer is the drawer's new state; neither route was read twice.
    expect(node<HTMLSelectElement>('[data-profile-select]')!.value).toBe('off')
    const offRow = [...document.querySelectorAll('[data-profile-row]')].find((row) =>
      row.textContent?.includes('Off'),
    )
    expect(offRow?.querySelector('[data-profile-active]')).not.toBeNull()
    expect(getHardwareAudioCard).toHaveBeenCalledOnce()
    expect(getHardware).toHaveBeenCalledOnce()
  })

  it('disables an unavailable profile and says why', async () => {
    vi.mocked(getHardwareAudioCard).mockResolvedValue(
      audioCardDetail({
        profiles: [
          { name: 'output:analog-stereo', description: 'Analog Stereo Output', available: true },
          { name: 'off', description: 'Off', available: false },
        ],
      }) as never,
    )

    await openCard()

    const off = node<HTMLOptionElement>('[data-profile-select] option[value="off"]')
    expect(off).not.toBeNull()
    expect(off!.disabled).toBe(true)
    expect(off!.textContent).toContain(en.hardware.audio.profileUnavailable)
  })

  it('shows an invalid_profile refusal with the server code', async () => {
    vi.mocked(setCardProfile).mockRejectedValue(problem(400, 'invalid_profile', 'The card does not offer it.'))

    await openCard()
    pick('[data-profile-select]', 'off')
    await flushPromises()

    const refusal = node('[data-action-error]')
    expect(refusal).not.toBeNull()
    expect(refusal!.textContent).toContain('invalid_profile')
    expect(node('[data-profile-select]')).not.toBeNull()
  })

  it('the profile select has no serious violations', async () => {
    await openCard()
    expect(node('[data-profile-select]')).not.toBeNull()
    expect(await seriousViolations()).toEqual([])
  })
})

describe('Hardware page: output ports', () => {
  const PORTS = [
    { name: 'analog-output-speaker', description: 'Speakers', available: true, active: true, profiles: [] },
    { name: 'analog-output-headphones', description: 'Headphones', available: false, active: false, profiles: [] },
    { name: 'hdmi-output-0', description: 'HDMI output', available: null, active: false, profiles: [] },
  ]

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getHardwareAudioCard).mockResolvedValue(audioCardDetail() as never)
    vi.mocked(getHardwareAudioSink).mockResolvedValue(audioEndpointDetail({ ports: PORTS }) as never)
    vi.mocked(getHardwareAudioSource).mockResolvedValue(
      audioEndpointDetail({ kind: 'source', volume: null, muted: null, in_use_by: null }) as never,
    )
    vi.mocked(setSinkPort).mockResolvedValue(
      audioEndpointDetail({ active_port: 'hdmi-output-0', ports: PORTS }) as never,
    )
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('lists the ports with their availability and disables the unavailable one', async () => {
    await openSink()

    const select = node<HTMLSelectElement>('[data-port-select]')
    expect(select).not.toBeNull()
    expect(select!.value).toBe('analog-output-speaker')
    const labels = [...select!.options].map((option) => option.textContent ?? '')
    expect(labels[0]).toContain(en.hardware.audio.portActive)
    expect(labels[1]).toContain(en.hardware.audio.portUnavailable)
    expect(labels[2]).toContain(en.hardware.audio.portUnknown)
    expect(node<HTMLOptionElement>('[data-port-select] option[value="analog-output-headphones"]')!.disabled).toBe(
      true,
    )
  })

  it('switches to another port after naming what may go silent and applies the returned detail', async () => {
    await openSink()

    pick('[data-port-select]', 'hdmi-output-0')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    const dialog = confirm.confirmDialog.mock.calls[0]![0]
    expect(dialog.title).toContain(audioSink().description)
    expect(dialog.consequences.join(' ')).toContain(audioSink().description)
    expect(dialog.danger).toBe(true)
    expect(setSinkPort).toHaveBeenCalledWith(audioSink().name, 'hdmi-output-0')

    await flushPromises()

    expect(node<HTMLSelectElement>('[data-port-select]')!.value).toBe('hdmi-output-0')
    expect(getHardwareAudioSink).toHaveBeenCalledOnce()
    expect(getHardware).toHaveBeenCalledOnce()
  })

  it('shows an invalid_port refusal with the server code', async () => {
    vi.mocked(setSinkPort).mockRejectedValue(problem(400, 'invalid_port', 'The sink has no such port.'))

    await openSink()
    pick('[data-port-select]', 'hdmi-output-0')
    await flushPromises()

    const refusal = node('[data-action-error]')
    expect(refusal).not.toBeNull()
    expect(refusal!.textContent).toContain('invalid_port')
  })

  it('offers no port choice on an input endpoint', async () => {
    await openSource()
    expect(node('[data-port-select]')).toBeNull()
  })

  it('the port select has no serious violations', async () => {
    await openSink()
    expect(node('[data-port-select]')).not.toBeNull()
    expect(await seriousViolations()).toEqual([])
  })
})
