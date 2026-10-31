import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { capabilities, mountView, snapshot } from './support'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

describe('Hardware page: the host cannot read its Bluetooth', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('shows the reason, not an empty device list', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        controllers: [],
        bluetooth_devices: [],
        capabilities: capabilities({
          bluetooth: false,
          dbus: false,
          reasons: { bluetooth: 'bluetooth_unavailable', dbus: 'dbus_unavailable' },
        }),
        degraded: [
          {
            section: 'controllers',
            code: 'dbus_unavailable',
            detail: 'D-Bus did not answer; the inventory was read through bluetoothctl.',
          },
        ],
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('unavailable')
    const banner = wrapper.get('[data-capability-banner]')
    expect(banner.text()).toContain(en.hardware.unavailable.dbusTitle)
    expect(banner.text()).toContain(en.hardware.unavailable.dbusBody)
    expect(wrapper.text()).not.toContain(en.hardware.noDevices)
    expect(wrapper.find('[data-device-card]').exists()).toBe(false)
  })
})
