import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import {
  cancelMaPairing,
  closeSourcePairing,
  openMaPairing,
  openSourcePairing,
  setSourceEnabled,
} from '@/api/source'
import { useBridgeStore } from '@/stores/bridge'
import { useHardwareStore } from '@/stores/hardware'
import { useNotificationStore } from '@/stores/notifications'
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
}))

let wrapper: ReturnType<typeof mountView> | null = null

const phoneWindow = () => ({
  pairing_seconds_left: 120,
  pairing_adapters: ['C0:FB:F9:62:D6:9D'],
  error: null,
})

describe('Hardware page: source strip', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(snapshot({ source: sourceFeed() }) as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(openMaPairing).mockResolvedValue({ code: '12345678', seconds_left: 300, client_id: 'peer' } as never)
    vi.mocked(cancelMaPairing).mockResolvedValue(undefined as never)
    vi.mocked(openSourcePairing).mockResolvedValue(phoneWindow() as never)
    vi.mocked(closeSourcePairing).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  it('says the source is off when the bridge reports none, with no controls', async () => {
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

  it('shows the Music Assistant link and the phone block without relays', async () => {
    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('waiting')
    expect(panel.get('[data-source-state-badge]').text()).toContain(en.hardware.source.stateWaiting)
    expect(panel.get('[data-source-ma]').text()).toContain('ws://ma.local:8927/sendspin')
    expect(panel.find('[data-source-player]').exists()).toBe(false)
    expect(panel.find('[data-source-relay]').exists()).toBe(false)
    expect(panel.get('[data-source-phone-block]').exists()).toBe(true)
    expect(panel.find('[data-source-pair]').exists()).toBe(true)
    expect(panel.find('[data-source-stop]').exists()).toBe(true)
  })

  it('shows the streaming phone by name and MAC', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ source: sourceFeed({ state: 'streaming', phone_mac: 'FC:58:FA:EB:08:6C' }) }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.attributes('data-source-state')).toBe('streaming')
    const phone = panel.get('[data-source-phone]')
    expect(phone.text()).toContain('ENEBY20')
    expect(phone.text()).toContain('FC:58:FA:EB:08:6C')
  })

  it('shows the Music Assistant pairing code and its countdown', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        source: sourceFeed({
          state: 'pairing',
          ma: {
            server: 'ws://ma.local:8927/sendspin',
            connected: true,
            paired: false,
            pairing_required: true,
            pairing_code: '12345678',
            pairing_seconds_left: 47,
            error: null,
          },
        }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const panel = wrapper.get('[data-source-panel]')
    expect(panel.get('[data-source-ma-code]').text()).toContain('12345678')
    expect(panel.get('[data-source-ma-code]').text()).toContain(
      en.hardware.source.maCodeHint.replace('{time}', '0:47'),
    )
    expect(panel.find('[data-source-ma-pair]').exists()).toBe(true)
  })

  it('pairs with Music Assistant through the route and shows the returned code', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        source: sourceFeed({
          state: 'pairing',
          ma: {
            server: 'ws://ma.local:8927/sendspin',
            connected: true,
            paired: false,
            pairing_required: true,
            pairing_code: null,
            pairing_seconds_left: 0,
            error: null,
          },
        }),
      }) as never,
    )
    vi.mocked(getHardware).mockResolvedValueOnce(
      snapshot({
        source: sourceFeed({
          state: 'pairing',
          ma: {
            server: 'ws://ma.local:8927/sendspin',
            connected: true,
            paired: false,
            pairing_required: true,
            pairing_code: null,
            pairing_seconds_left: 0,
            error: null,
          },
        }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()
    const panel = wrapper.get('[data-source-panel]')
    expect(panel.find('[data-source-ma-code]').exists()).toBe(false)

    await wrapper.get('[data-source-ma-pair]').trigger('click')
    await flushPromises()

    expect(openMaPairing).toHaveBeenCalledOnce()
    expect(useHardwareStore().maPairingSecondsLeft).toBe(300)
  })

  it('forgets the Music Assistant pairing after a confirm', async () => {
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-source-ma-forget]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalled()
    expect(cancelMaPairing).toHaveBeenCalledOnce()
  })

  it('explains an unreachable Music Assistant with a next step', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        source: sourceFeed({
          state: 'waiting',
          ma: {
            server: 'ws://ma.local:8927/sendspin',
            connected: false,
            paired: true,
            pairing_required: false,
            pairing_code: null,
            pairing_seconds_left: 0,
            error: 'ma_unreachable',
          },
        }),
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    const error = wrapper.get('[data-source-error]')
    expect(error.text()).toContain(en.hardware.source.maUnreachable)
    expect(error.text()).toContain(en.hardware.source.maUnreachableNext)
    expect(error.text()).toContain('ma_unreachable')
  })

  it('opens the phone pairing window and shows the server countdown, then closes it', async () => {
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

  it('treats a refused phone pairing window as a failure: inline reason, no toast', async () => {
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

    const now = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 2000)
    expect(store.pairingSecondsLeft).toBe(0)
    now.mockRestore()

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-source-pairing]').exists()).toBe(false)
    expect(wrapper.find('[data-source-pair]').exists()).toBe(true)
  })

  it('picks the phone countdown up from the bridge status after a reload', async () => {
    useBridgeStore().snapshot = {
      bridge: { sendspin_source: { pairing_seconds_left: 47 } },
      devices: [],
      groups: [],
    } as never

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-source-pairing]').text()).toContain(
      en.hardware.source.pairing.replace('{time}', '0:47'),
    )
  })
})
