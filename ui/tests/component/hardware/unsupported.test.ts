import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ApiError } from '@/api/client'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { mountView } from './support'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

describe('Hardware page: a bridge without the hardware API', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('names the unsupported bridge instead of saying no devices are paired', async () => {
    vi.mocked(getHardware).mockRejectedValue(new ApiError({ status: 404, code: 'not_found', title: 'Nope' }))

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('unsupported')
    expect(wrapper.text()).toContain(en.hardware.unsupported.title)
    expect(wrapper.text()).not.toContain(en.hardware.noDevices)
  })
})
