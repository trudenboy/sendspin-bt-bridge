import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceModeSection from '@/components/settings/SourceModeSection.vue'
import { useSourceStore } from '@/stores/source'
import { getStatus } from '@/api/status'
import { clearActiveSource, setSourceEnabled } from '@/api/source'
import { confirmState, settleConfirm } from '@/composables/useConfirm'
import en from '@/i18n/locales/en.json'

vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', () => ({ setSourceEnabled: vi.fn(), clearActiveSource: vi.fn() }))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: false,
  state: 'off',
  ma_paired: false,
  ma_connected: false,
  ma_pairing_code: null,
  ma_pairing_seconds_left: 0,
  ma_error: null,
  error: null,
  ...overrides,
})

function setSource(source: Record<string, unknown> | null) {
  useSourceStore().applyStatus({ bridge: { sendspin_source: source }, devices: [], groups: [] } as never)
  vi.mocked(getStatus).mockResolvedValue({ bridge: { sendspin_source: source }, devices: [], groups: [] } as never)
}

function mountSection() {
  return mount(SourceModeSection, { global: { plugins: [i18n] } })
}

describe('Settings: audio source mode', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getStatus).mockResolvedValue({ bridge: { sendspin_source: null }, devices: [], groups: [] } as never)
    vi.mocked(setSourceEnabled).mockResolvedValue({} as never)
    vi.mocked(clearActiveSource).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    useSourceStore().teardown()
  })

  it('starts the source when switched on', async () => {
    setSource(sourceBlock())

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(setSourceEnabled).toHaveBeenCalledWith(true)
  })

  it('stops the source when switched off', async () => {
    setSource(sourceBlock({ enabled: true, state: 'waiting' }))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(setSourceEnabled).toHaveBeenCalledWith(false)
  })

  it('asks before switching off an active input, releases it, then stops the source', async () => {
    setSource(sourceBlock({ enabled: true, state: 'streaming', active_input: 'headset-mic:AA:BB:CC:DD:EE:FF' }))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(confirmState.open).toBe(true)
    expect(clearActiveSource).not.toHaveBeenCalled()
    expect(setSourceEnabled).not.toHaveBeenCalled()

    settleConfirm(true)
    await flushPromises()

    expect(clearActiveSource).toHaveBeenCalledOnce()
    expect(setSourceEnabled).toHaveBeenCalledWith(false)
    expect(vi.mocked(clearActiveSource).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(setSourceEnabled).mock.invocationCallOrder[0]!,
    )
  })

  it('leaves the source on when the switch-off is declined', async () => {
    setSource(sourceBlock({ enabled: true, state: 'streaming', active_input: 'headset-mic:AA:BB:CC:DD:EE:FF' }))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()
    settleConfirm(false)
    await flushPromises()

    expect(clearActiveSource).not.toHaveBeenCalled()
    expect(setSourceEnabled).not.toHaveBeenCalled()
    expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('true')
  })

  it('stops the source without a confirmation when no input is active', async () => {
    setSource(sourceBlock({ enabled: true, state: 'waiting', active_input: null }))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(confirmState.open).toBe(false)
    expect(setSourceEnabled).toHaveBeenCalledWith(false)
  })

  it('shows an error and keeps the source on when the start fails', async () => {
    setSource(sourceBlock())
    vi.mocked(setSourceEnabled).mockRejectedValue(new Error('capture_unavailable'))

    const wrapper = mountSection()
    await wrapper.get('[role="switch"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-source-mode-error]').text()).toBe(en.settings.parts.sourceMode.failed)
    expect(wrapper.get('[role="switch"]').attributes('aria-checked')).toBe('false')
  })
})
