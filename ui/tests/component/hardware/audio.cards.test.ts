import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware, getHardwareAudioCard, getHardwareAudioSink } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { audioCard, audioCardDetail, audioEndpointDetail, mountView, snapshot } from './support'

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

const drawer = () => document.querySelector('[data-audio-detail]')

describe('Hardware page: audio cards and their ports', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('renders the card summary the aggregate carries', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    const card = wrapper.get('[data-audio-card]')
    expect(card.text()).toContain(audioCard().name)
    expect(card.text()).toContain('module-alsa-card')
    expect(card.text()).toContain('output:analog-stereo')
    expect(card.text()).toContain(`${en.hardware.audio.profileCount}`)
    expect(card.text()).toContain(`${en.hardware.audio.portCount}`)
    // Endpoint refs are visible before opening anything.
    expect(card.findAll('[data-endpoint-ref]')).toHaveLength(2)
  })

  it('shows an unplugged port, an available port and an unknown one as three different answers', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getHardwareAudioCard).mockResolvedValue(audioCardDetail() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-audio-card-details]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(getHardwareAudioCard).toHaveBeenCalledWith(audioCard().name)
    expect(drawer()).not.toBeNull()
    const ports = drawer()!.querySelectorAll('[data-port-row]')
    expect(ports).toHaveLength(3)
    const states = [...drawer()!.querySelectorAll('[data-port-state]')].map((node) =>
      node.getAttribute('data-port-state'),
    )
    expect(states).toEqual(['available', 'unavailable', 'unknown'])
    // The three answers do not share a label: unknown is not a fault.
    const labels = [...drawer()!.querySelectorAll('[data-port-state]')].map((node) => node.textContent?.trim())
    expect(new Set(labels).size).toBe(3)
    expect(drawer()!.textContent).toContain(en.hardware.audio.portAvailable)
    expect(drawer()!.textContent).toContain(en.hardware.audio.portUnavailable)
    expect(drawer()!.textContent).toContain(en.hardware.audio.portUnknown)
    expect(drawer()!.querySelector('[data-port-active]')!.textContent).toContain(en.hardware.audio.portActive)
  })

  it('lists profiles read-only and marks the active one', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getHardwareAudioCard).mockResolvedValue(audioCardDetail() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-audio-card-details]').trigger('click')
    await flushPromises()
    await flushPromises()

    const rows = drawer()!.querySelectorAll('[data-profile-row]')
    expect(rows).toHaveLength(3)
    expect(drawer()!.querySelectorAll('[data-profile-active]')).toHaveLength(1)
    // No profile button: switching a profile is not a v1 action.
    for (const row of rows) expect(row.querySelector('button')).toBeNull()
  })

  it('opens a sink from a card endpoint ref and reads its detail route', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(getHardwareAudioCard).mockResolvedValue(audioCardDetail() as never)
    vi.mocked(getHardwareAudioSink).mockResolvedValue(audioEndpointDetail() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-endpoint-ref][data-ref-kind="sink"]').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(drawer()).not.toBeNull()
    expect(drawer()!.textContent).toContain(en.hardware.audio.output)
    // RouterLink is stubbed by mountView; the point is that it is the sink read.
    expect(drawer()!.textContent).toContain(audioCard().endpoint_refs[0]!.name)
  })
})
