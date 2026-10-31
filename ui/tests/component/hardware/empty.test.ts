import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { controller, mountView, snapshot } from './support'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

describe('Hardware page: no paired devices', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('says no devices are paired instead of showing an empty list', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ controllers: [controller()], bluetooth_devices: [] }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('empty')
    expect(wrapper.text()).toContain(en.hardware.noDevices)
    expect(wrapper.find('[data-device-card]').exists()).toBe(false)
    // A controller with no bonds is still a controller: it stays visible.
    expect(wrapper.find('[data-controller-card]').exists()).toBe(true)
    expect(wrapper.find('[data-capability-banner]').exists()).toBe(false)
  })
})
