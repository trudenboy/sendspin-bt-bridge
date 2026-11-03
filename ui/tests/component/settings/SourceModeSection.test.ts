import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceModeSection from '@/components/settings/SourceModeSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import { getHardware } from '@/api/hardware'
import { setSourceEnabled } from '@/api/source'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/source', () => ({ setSourceEnabled: vi.fn() }))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function setSource(source: Record<string, unknown> | null) {
  useBridgeStore().snapshot = { bridge: { sendspin_source: source }, devices: [], groups: [] } as never
}

function mountSection() {
  return mount(SourceModeSection, { global: { plugins: [i18n] } })
}

describe('Settings: audio source mode', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getHardware).mockResolvedValue({ source: null } as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
  })

  it('starts the source when switched on', async () => {
    setSource({ enabled: false, state: 'off' })

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(setSourceEnabled).toHaveBeenCalledWith(true)
  })

  it('stops the source when switched off', async () => {
    setSource({ enabled: true, state: 'waiting' })

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(setSourceEnabled).toHaveBeenCalledWith(false)
  })

  it('shows an error and stays off when the start fails', async () => {
    setSource({ enabled: false, state: 'off' })
    vi.mocked(setSourceEnabled).mockRejectedValue(new Error('capture_unavailable'))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-source-mode-error]').text()).toBe(en.settings.parts.sourceMode.failed)
    expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('false')
  })
})
