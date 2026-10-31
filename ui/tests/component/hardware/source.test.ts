import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { getHardware } from '@/api/hardware'
import { closeSourcePairing, openSourcePairing, setSourceEnabled, setSourcePlayer } from '@/api/source'
import { ApiError } from '@/api/client'
import { useBridgeStore } from '@/stores/bridge'
import { useHardwareStore } from '@/stores/hardware'
import { useNotificationStore } from '@/stores/notifications'
import SourceFeedPanel from '@/components/hardware/SourceFeedPanel.vue'
import en from '@/i18n/locales/en.json'
import { btDevice, mountView, snapshot, sourceFeed } from './support'

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
  setSourcePlayer: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
}))

function bridgeWithPlayers() {
  const bridge = useBridgeStore()
  bridge.snapshot = {
    devices: [
      { id: 'eneby20', name: 'ENEBY20' },
      { id: 'kitchen', name: 'Kitchen' },
    ],
    groups: [],
    bridge: {},
  } as never
}

let wrapper: ReturnType<typeof mountView> | ReturnType<typeof mountPanel> | null = null

const mountPanel = (props: Record<string, unknown>) =>
  mount(SourceFeedPanel, {
    props,
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      components: { RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' } },
    },
  })

describe('Hardware page: source strip', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed() }) as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(setSourcePlayer).mockResolvedValue({ device_id: 'eneby20', playing_source: true } as never)
    vi.mocked(openSourcePairing).mockResolvedValue({
      pairing_seconds_left: 120,
      pairing_adapters: ['C0:FB:F9:62:D6:9D'],
      error: null,
    } as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  it('says the source is off in Settings when the bridge reports none, with no controls', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: null }) as never)

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('off')
    expect(panel.get('[data-source-off]').text()).toBe(en.hardware.source.offBody)
    expect(panel.find('[data-source-start]').exists()).toBe(false)
    expect(panel.find('[data-source-stop]').exists()).toBe(false)
    expect(panel.find('[data-source-pair]').exists()).toBe(false)
    expect(panel.find('[data-source-player]').exists()).toBe(false)
  })

  it('shows a waiting source with its port, the relayed speakers and the pairing action', async () => {
    bridgeWithPlayers()
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ relays: ['eneby20'] }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('waiting')
    expect(panel.get('[data-source-state-badge]').text()).toContain(en.hardware.source.stateWaiting)
    expect(panel.text()).toContain(en.hardware.source.waitingBody.replace('{port}', '8940'))
    expect(panel.get('[data-source-relays]').text()).toContain('ENEBY20')
    expect(panel.find('[data-source-pair]').exists()).toBe(true)
    expect(panel.find('[data-source-stop]').exists()).toBe(true)
  })

  it('shows the streaming phone by name and MAC', async () => {
    bridgeWithPlayers()
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        source: sourceFeed({ state: 'streaming', phone_mac: 'FC:58:FA:EB:08:6C', relays: ['eneby20'] }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('streaming')
    expect(panel.get('[data-source-state-badge]').text()).toContain(en.hardware.source.stateStreaming)
    const phone = panel.get('[data-source-phone]')
    expect(phone.text()).toContain('ENEBY20')
    expect(phone.text()).toContain('FC:58:FA:EB:08:6C')
    expect(panel.text()).toContain(en.hardware.source.streamingBody.replace('{port}', '8940'))
  })

  it('explains a failed source with the server code and a next step', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ state: 'error', error: 'port_in_use' }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('error')
    expect(panel.get('[data-source-state-badge]').text()).toContain(en.hardware.source.stateError)
    const error = panel.get('[data-source-error]')
    expect(error.text()).toContain(en.hardware.source.errorPortInUse.replace('{port}', '8940'))
    expect(error.text()).toContain(en.hardware.source.errorPortInUseNext)
    expect(error.text()).toContain('port_in_use')
  })

  it('opens the pairing window and shows the server countdown, then closes it', async () => {
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
    const panel = wrapper.get('[data-source-panel]')
    expect(panel.get('[data-source-pairing]').text()).toContain(
      en.hardware.source.pairing.replace('{time}', '2:00'),
    )

    await wrapper.get('[data-source-pairing-stop]').trigger('click')
    await flushPromises()

    expect(closeSourcePairing).toHaveBeenCalledOnce()
    expect(panel.find('[data-source-pairing]').exists()).toBe(false)
  })

  it('treats a refused pairing window as a failure: inline reason, no toast and no Undo', async () => {
    vi.mocked(openSourcePairing).mockResolvedValue({
      pairing_seconds_left: 0,
      pairing_adapters: [],
      error: 'no_adapter',
    } as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.find('[data-source-pairing]').exists()).toBe(false)
    expect(panel.get('[data-source-failure]').text()).toContain('no_adapter')
    expect(panel.get('[data-source-failure]').text()).toContain(en.hardware.source.failureNextStep)

    const toasts = useNotificationStore().toasts
    expect(toasts.some((toast) => toast.type === 'success')).toBe(false)
    expect(toasts.some((toast) => toast.type === 'error')).toBe(false)
    expect(toasts.some((toast) => toast.action != null)).toBe(false)
  })

  it('clears a running countdown on unmount, so returning can pair again', async () => {
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-source-pairing]').text()).toContain(
      en.hardware.source.pairing.replace('{time}', '2:00'),
    )

    const store = useHardwareStore()
    expect(store.pairingSecondsLeft).toBeGreaterThan(0)

    wrapper.unmount()
    wrapper = null

    // Two seconds after leaving, the old countdown must not survive the visit.
    const now = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 2000)
    expect(store.pairingSecondsLeft).toBe(0)
    now.mockRestore()

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-source-pairing]').exists()).toBe(false)
    expect(wrapper.find('[data-source-pair]').exists()).toBe(true)
  })

  it('tells an absent source apart from one that is configured but off', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: null }) as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-source-panel]').attributes('data-source-presence')).toBe('absent')
    expect(wrapper.get('[data-source-panel]').attributes('data-source-state')).toBe('off')

    wrapper.unmount()
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ enabled: false, state: 'off' }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-source-panel]').attributes('data-source-presence')).toBe('configured')
    expect(wrapper.get('[data-source-panel]').attributes('data-source-state')).toBe('off')
    expect(wrapper.find('[data-source-start]').exists()).toBe(true)
  })

  it('starts a configured but stopped source', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ enabled: false, state: 'off' }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-start]').trigger('click')
    await flushPromises()

    expect(setSourceEnabled).toHaveBeenCalledWith(true)
  })

  it('keeps the speaker when a relay is refused with 409 source_sink_missing', async () => {
    bridgeWithPlayers()
    vi.mocked(setSourcePlayer).mockRejectedValue(
      new ApiError({ status: 409, code: 'source_sink_missing', detail: 'That speaker has no audio output' }),
    )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-relay]').trigger('click')
    await flushPromises()

    const failure = wrapper.get('[data-source-failure]')
    expect(failure.text()).toContain('source_sink_missing')
    expect(failure.text()).toContain('ENEBY20')
    expect(failure.text()).toContain(en.hardware.source.sinkMissingNext)
    expect(useNotificationStore().toasts.some((toast) => toast.type === 'error')).toBe(true)
  })

  it('renders the countdown the store hands it, not a second copy of the default', () => {
    const panel = mountPanel({
      source: sourceFeed(),
      pairingSecondsLeft: 119,
      players: [{ id: 'eneby20', name: 'ENEBY20' }],
    })
    wrapper = panel

    expect(panel.get('[data-source-pairing]').text()).toContain(
      en.hardware.source.pairing.replace('{time}', '1:59'),
    )
    panel.get('[data-source-pairing-stop]').trigger('click')
    expect(panel.emitted('stopPairing')).toHaveLength(1)
  })
})
