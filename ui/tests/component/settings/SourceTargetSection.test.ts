import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import SourceTargetSection from '@/components/settings/SourceTargetSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import { getSourceTargets, setSourceTarget } from '@/api/source'
import en from '@/i18n/locales/en.json'

vi.mock('@/api/source', () => ({ getSourceTargets: vi.fn(), setSourceTarget: vi.fn() }))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function setSource(source: Record<string, unknown> | null) {
  useBridgeStore().snapshot = { bridge: { sendspin_source: source }, devices: [], groups: [] } as never
}

function mountSection() {
  return mount(SourceTargetSection, { global: { plugins: [i18n] } })
}

const targets = {
  targets: [
    { player_id: 'house', name: 'House', type: 'group', available: true },
    { player_id: 'kitchen', name: 'Kitchen', type: 'player', available: false },
  ],
  selected: null,
  ma_api_available: true,
  error: null,
}

describe('Settings: source target', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getSourceTargets).mockResolvedValue(targets as never)
    vi.mocked(setSourceTarget).mockResolvedValue({ target_player: 'house', target_name: 'House', target_error: null } as never)
    vi.spyOn(useBridgeStore(), 'refresh').mockResolvedValue()
  })

  it('lists MA groups and players and keeps the default choice', async () => {
    setSource({ enabled: true, ma_paired: true, target_player: null })

    const wrapper = mountSection()
    await flushPromises()

    const options = wrapper.findAll('[data-source-target-select] option')
    expect(options.map((option) => option.text())).toEqual([
      en.settings.parts.sourceTarget.default,
      'House',
      'Kitchen — unavailable',
    ])
    expect(wrapper.get('[data-source-target-select]').element.value).toBe('')
    expect(wrapper.get('[data-source-target]').text()).toContain(en.settings.parts.sourceTarget.note)
  })

  it('saves the chosen player', async () => {
    setSource({ enabled: true, ma_paired: true, target_player: null })

    const wrapper = mountSection()
    await flushPromises()
    await wrapper.get('[data-source-target-select]').setValue('house')
    await flushPromises()

    expect(setSourceTarget).toHaveBeenCalledWith('house')
  })

  it('disables the selector without an MA API token and points at the settings', async () => {
    vi.mocked(getSourceTargets).mockResolvedValue({ ...targets, ma_api_available: false, targets: [] } as never)
    setSource({ enabled: true, ma_paired: true, target_player: null })

    const wrapper = mountSection()
    await flushPromises()

    expect(wrapper.get('[data-source-target-select]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-source-target-api]').text()).toContain(en.settings.parts.sourceTarget.apiUnavailable)
    expect(wrapper.get('[data-source-target-api] a').attributes('href')).toBe('#settings-musicAssistant')
  })

  it('shows the saved target and explains a play failure', async () => {
    setSource({ enabled: true, ma_paired: true, target_player: 'house', target_error: 'target_unreachable' })

    const wrapper = mountSection()
    await flushPromises()

    expect(wrapper.get('[data-source-target-select]').element.value).toBe('house')
    expect(wrapper.get('[data-source-target-error]').text()).toContain(en.settings.parts.sourceTarget.playFailed)
  })
})
