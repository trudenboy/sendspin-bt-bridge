import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import MaPairingSection from '@/components/settings/MaPairingSection.vue'
import { useBridgeStore } from '@/stores/bridge'
import { cancelMaPairing, openMaPairing } from '@/api/source'
import en from '@/i18n/locales/en.json'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn().mockResolvedValue({ source: null }) }))
vi.mock('@/api/source', () => ({
  setSourceEnabled: vi.fn(),
  openMaPairing: vi.fn(),
  cancelMaPairing: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
}))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

function setSource(source: Record<string, unknown> | null) {
  useBridgeStore().snapshot = {
    bridge: { sendspin_source: source },
    devices: [],
    groups: [],
  } as never
}

function mountSection() {
  return mount(MaPairingSection, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: { props: ['to'], template: '<a :href="String(to)"><slot /></a>' },
      },
    },
  })
}

describe('Settings: Music Assistant pairing', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(openMaPairing).mockResolvedValue({ code: '12345678', seconds_left: 300, client_id: 'peer' } as never)
    vi.mocked(cancelMaPairing).mockResolvedValue(undefined as never)
  })

  it('renders nothing while the source is off', () => {
    setSource({ enabled: false, state: 'off', ma_paired: false, ma_pairing_code: null })

    const wrapper = mountSection()

    expect(wrapper.find('[data-ma-pairing]').exists()).toBe(false)
  })

  it('shows the eight-digit code from the status', async () => {
    setSource({
      enabled: true,
      state: 'pairing',
      ma_paired: false,
      ma_pairing_code: '12345678',
      ma_pairing_seconds_left: 47,
    })

    const wrapper = mountSection()
    await flushPromises()

    expect(wrapper.text()).toContain('12345678')
    expect(wrapper.text()).toContain('0:47')
    expect(wrapper.get('[data-ma-pairing]').text()).toContain(en.settings.parts.maPairing.whereToEnter)
  })

  it('forgets the pairing only after the confirm dialog', async () => {
    setSource({ enabled: true, state: 'waiting', ma_paired: true, ma_connected: true })

    const wrapper = mountSection()
    await wrapper.get('[data-ma-pairing-forget]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalled()
    expect(cancelMaPairing).toHaveBeenCalledOnce()
  })

  it('keeps the forget action behind the confirm: a refusal changes nothing', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    setSource({ enabled: true, state: 'waiting', ma_paired: true, ma_connected: true })

    const wrapper = mountSection()
    await wrapper.get('[data-ma-pairing-forget]').trigger('click')
    await flushPromises()

    expect(cancelMaPairing).not.toHaveBeenCalled()
  })
})
