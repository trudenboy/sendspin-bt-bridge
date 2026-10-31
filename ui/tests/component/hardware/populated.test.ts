import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import { setAdapterPower } from '@/api/bluetooth'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import en from '@/i18n/locales/en.json'
import { controller, mountView, snapshot } from './support'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/bluetooth', () => ({ setAdapterPower: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

/** The Home players, so the power confirmation can name the speakers it disconnects. */
function bridgeWithSpeaker() {
  const bridge = useBridgeStore()
  bridge.snapshot = {
    devices: [{ id: 'eneby20', name: 'ENEBY20', bluetooth: { adapter: { hci: 'hci0' } } }],
    groups: [],
    bridge: {},
  } as never
  return bridge
}

describe('Hardware page: controllers and devices', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('renders one controller card with power state, class, count and area', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('ready')
    const card = wrapper.get('[data-controller-card]')
    expect(card.text()).toContain('Living room dongle')
    expect(card.text()).toContain('C0:FB:F9:62:D6:9D')
    expect(card.text()).toContain('0x00010c')
    expect(card.text()).toContain('Living room')
    expect(card.text()).toContain('Devices: 1')
    expect(card.text()).toContain(en.hardware.powered)
    expect(wrapper.find('[data-hardware-stale]').exists()).toBe(false)
  })

  it('renders the device facts, role evidence and the player it backs', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    const card = wrapper.get('[data-device-card]')
    expect(card.text()).toContain('ENEBY20')
    expect(card.text()).toContain('FC:58:FA:EB:08:6C')
    expect(card.text()).toContain(en.hardware.connected)
    expect(card.text()).toContain(en.hardware.paired)
    expect(card.text()).toContain(en.hardware.trusted)
    expect(card.text()).toContain('Battery: 80%')
    expect(card.text()).toContain('Signal: -4 dB')
    expect(card.text()).toContain(en.hardware.roleOutput)
    expect(card.text()).toContain('transport:active')

    const link = card.get('[data-player-link]')
    expect(link.attributes('href')).toBe('/')
    expect(link.text()).toContain('eneby20')
  })

  it('asks before powering a controller off, then refreshes the read', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    bridgeWithSpeaker()

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-controller-power]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    expect(confirm.confirmDialog.mock.calls[0]![0].consequences.join(' ')).toContain('ENEBY20')
    expect(setAdapterPower).toHaveBeenCalledWith('hci0', false)
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(useNotificationStore().toasts.some((toast) => toast.message === en.adapter.powerToggled)).toBe(true)
  })

  it('does nothing when the power confirmation is declined', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    confirm.confirmDialog.mockResolvedValue(false)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-controller-power]').trigger('click')
    await flushPromises()

    expect(setAdapterPower).not.toHaveBeenCalled()
  })

  it('powers a controller on without asking first', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ controllers: [controller({ powered: false })] }) as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-controller-power]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).not.toHaveBeenCalled()
    expect(setAdapterPower).toHaveBeenCalledWith('hci0', true)
  })

  it('opens a controller in a drawer with its technical details', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-controller-details]').trigger('click')
    await flushPromises()

    // The drawer teleports to <body>, so it is not inside the view's wrapper.
    const drawer = document.querySelector('[role="dialog"]')
    expect(drawer).not.toBeNull()
    expect(drawer!.textContent).toContain('Living room dongle')
    expect(drawer!.textContent).toContain(en.hardware.drawer.classOfDevice)
    expect(drawer!.textContent).toContain('0x00010c')
    expect(drawer!.textContent).toContain('hci0')
  })
})
