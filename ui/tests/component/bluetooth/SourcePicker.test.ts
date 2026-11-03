import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourcePicker from '@/components/bluetooth/SourcePicker.vue'
import { useBridgeStore } from '@/stores/bridge'
import { addLocalSource, getSourceInputs, openSourcePairing } from '@/api/source'
import { getHardware } from '@/api/hardware'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/source', () => ({
  getSourceInputs: vi.fn(),
  addLocalSource: vi.fn(),
  selectSource: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
  setSourceEnabled: vi.fn(),
  SOURCE_PAIRING_WINDOW_S: 120,
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const candidate = {
  id: 'local:alsa_input.line',
  kind: 'local',
  name: 'Line In',
  description: 'Line In',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: null,
  node: 'alsa_input.line',
}

function mountPicker() {
  useBridgeStore().snapshot = { bridge: { sendspin_source: null }, devices: [], groups: [] } as never
  return mount(SourcePicker, { global: { plugins: [i18n] } })
}

describe('Add device: audio source', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [],
      local_candidates: [candidate],
      capture_available: true,
      capture_kind: 'pulseaudio',
    } as never)
    vi.mocked(getHardware).mockResolvedValue({ source: null } as never)
    vi.mocked(openSourcePairing).mockResolvedValue({ pairing_seconds_left: 120, pairing_adapters: [], error: null } as never)
    vi.mocked(addLocalSource).mockResolvedValue({ id: 'local:alsa_input.line' } as never)
  })

  it('adds a local audio input without requiring MA or source to be on', async () => {
    const wrapper = mountPicker()
    await flushPromises()

    await wrapper.get('[data-source-mode-local]').trigger('click')
    await wrapper.get('[data-source-local-list] button').trigger('click')
    await wrapper.get('[data-source-name] input').setValue('Turntable')
    await wrapper.get('[data-source-local-add]').trigger('click')
    await flushPromises()

    expect(getSourceInputs).toHaveBeenCalled()
    expect(addLocalSource).toHaveBeenCalledWith('alsa_input.line', 'Turntable', false)
  })

  it('opens the Bluetooth pairing window independently of MA', async () => {
    const wrapper = mountPicker()
    await flushPromises()

    await wrapper.get('[data-source-pair]').trigger('click')
    await flushPromises()

    expect(openSourcePairing).toHaveBeenCalledWith(120)
  })

  it('does not allow local add when capture is unavailable', async () => {
    vi.mocked(getSourceInputs).mockResolvedValue({
      active_input: null,
      inputs: [],
      local_candidates: [candidate],
      capture_available: false,
      capture_kind: 'pulseaudio',
    } as never)
    const wrapper = mountPicker()
    await flushPromises()
    await wrapper.get('[data-source-mode-local]').trigger('click')

    expect(wrapper.get('[data-source-capture-unavailable]').exists()).toBe(true)
    expect(wrapper.get('[data-source-local-list] button').attributes('disabled')).toBeDefined()
  })
})
