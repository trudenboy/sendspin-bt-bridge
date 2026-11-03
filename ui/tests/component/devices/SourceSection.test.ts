import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceSection from '@/components/devices/SourceSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import { removeSource, renameSource, selectSource } from '@/api/source'
import en from '@/i18n/locales/en.json'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/api/source', () => ({
  removeSource: vi.fn(),
  renameSource: vi.fn(),
  selectSource: vi.fn(),
}))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

const inputs = [
  { id: 'bluetooth:34:C7:E9:25:A3:A3', kind: 'bluetooth', name: 'Pixel', description: '', active: false, available: true, connected: true, monitor: false, mac: '34:C7:E9:25:A3:A3', node: null },
  { id: 'local:alsa_input.line', kind: 'local', name: 'Turntable', description: 'Line In', active: true, available: true, connected: false, monitor: false, mac: null, node: 'alsa_input.line' },
]

function setSource(overrides: Record<string, unknown> = {}) {
  useBridgeStore().snapshot = {
    bridge: {
      sendspin_source: {
        enabled: true,
        state: 'waiting',
        active_input: 'local:alsa_input.line',
        inputs,
        target_player: null,
        target_error: null,
        error: null,
        ...overrides,
      },
    },
    devices: [],
    groups: [],
  } as never
}

function mountSection() {
  return mount(SourceSection, { global: { plugins: [i18n] } })
}

describe('Devices: source section', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(selectSource).mockResolvedValue({ active_input: 'local:alsa_input.line' } as never)
    vi.mocked(renameSource).mockResolvedValue({} as never)
    vi.mocked(removeSource).mockResolvedValue(undefined as never)
    setSource()
  })

  it('shows the active source first and switches with Use', async () => {
    const wrapper = mountSection()
    await flushPromises()

    const cards = wrapper.findAll('[data-source-card]')
    expect(cards[0]!.attributes('data-source-id')).toBe('local:alsa_input.line')
    await cards[1]!.get('[data-source-use]').trigger('click')
    await flushPromises()

    expect(selectSource).toHaveBeenCalledWith('bluetooth:34:C7:E9:25:A3:A3')
  })

  it('renames a local input inline', async () => {
    const wrapper = mountSection()
    await flushPromises()

    const active = wrapper.get('[data-source-id="local:alsa_input.line"]')
    await active.get('[data-source-rename]').trigger('click')
    await active.get('[data-source-rename-input]').setValue('Record player')
    await active.get('form').trigger('submit')
    await flushPromises()

    expect(renameSource).toHaveBeenCalledWith('local:alsa_input.line', 'Record player')
  })

  it('asks before forgetting a Bluetooth source', async () => {
    const wrapper = mountSection()
    await flushPromises()

    await wrapper.get('[data-source-id="bluetooth:34:C7:E9:25:A3:A3"] [data-source-remove]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalled()
    expect(removeSource).toHaveBeenCalledWith('bluetooth:34:C7:E9:25:A3:A3')
  })
})
