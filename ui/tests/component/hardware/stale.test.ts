import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'
import { mountView, snapshot } from './support'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

describe('Hardware page: a stale snapshot', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('badges the data as out of date while still showing it', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({
        stale: true,
        degraded: [{ section: 'hardware', code: 'refresh_failed', detail: 'The last hardware refresh failed.' }],
      }) as never,
    )

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.get('[data-hardware-stale]').text()).toContain(en.hardware.stale)
    expect(wrapper.get('[data-state]').attributes('data-state')).toBe('ready')
    expect(wrapper.get('[data-device-card]').text()).toContain('ENEBY20')
  })
})
