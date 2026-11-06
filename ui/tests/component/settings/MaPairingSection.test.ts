import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import MaPairingSection from '@/components/settings/MaPairingSection.vue'
import { useSourceStore } from '@/stores/source'
import { getStatus } from '@/api/status'
import { cancelMaPairing, openMaPairing } from '@/api/source'
import en from '@/i18n/locales/en.json'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', () => ({
  setSourceEnabled: vi.fn(),
  openMaPairing: vi.fn(),
  cancelMaPairing: vi.fn(),
  openSourcePairing: vi.fn(),
  closeSourcePairing: vi.fn(),
}))

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

/** The status block the bridge stream feeds the source store with. */
const sourceBlock = (overrides: Record<string, unknown> = {}) => ({
  enabled: true,
  state: 'waiting',
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
    vi.mocked(getStatus).mockResolvedValue({ bridge: { sendspin_source: null }, devices: [], groups: [] } as never)
  })

  afterEach(() => {
    useSourceStore().teardown()
    vi.useRealTimers()
  })

  it('renders nothing while the source is off', () => {
    setSource(sourceBlock({ enabled: false, state: 'off', ma_paired: false, ma_pairing_code: null }))

    const wrapper = mountSection()

    expect(wrapper.find('[data-ma-pairing]').exists()).toBe(false)
  })

  it('shows the eight-digit code from the status', async () => {
    setSource(sourceBlock({ state: 'pairing', ma_pairing_code: '12345678', ma_pairing_seconds_left: 47 }))

    const wrapper = mountSection()
    await flushPromises()

    expect(wrapper.text()).toContain('12345678')
    expect(wrapper.text()).toContain('0:47')
    expect(wrapper.get('[data-ma-pairing]').text()).toContain(en.settings.parts.maPairing.whereToEnter)
  })

  it('the single source-store clock keeps the countdown across a remount', async () => {
    vi.useFakeTimers()
    setSource(sourceBlock({ state: 'pairing', ma_pairing_code: '12345678', ma_pairing_seconds_left: 47 }))

    const first = mountSection()
    await vi.advanceTimersByTimeAsync(1000)
    expect(first.text()).toContain('0:46')
    first.unmount()

    // Coming back to Settings re-reads the same status; the deadline must not slide.
    setSource(sourceBlock({ state: 'pairing', ma_pairing_code: '12345678', ma_pairing_seconds_left: 47 }))
    const second = mountSection()
    expect(second.text()).toContain('0:46')
  })

  it('the window survives a route change away and back without restarting', async () => {
    vi.useFakeTimers()
    setSource(sourceBlock({ state: 'pairing', ma_pairing_code: '12345678', ma_pairing_seconds_left: 300 }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/config', component: MaPairingSection },
        { path: '/hardware', component: { template: '<div data-other-route />' } },
      ],
    })
    await router.push('/config')
    const wrapper = mount(
      { template: '<router-view />' },
      {
        global: {
          plugins: [i18n, router],
          stubs: {
            RouterLink: { props: ['to'], template: '<a :href="String(to)"><slot /></a>' },
          },
        },
      },
    )
    await flushPromises()
    expect(wrapper.text()).toContain('12345678')
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toContain('4:59')

    // Leaving Settings unmounts the section; the window belongs to the store.
    await router.push('/hardware')
    await flushPromises()
    expect(wrapper.find('[data-ma-pairing]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(1000)

    await router.push('/config')
    await flushPromises()
    expect(wrapper.text()).toContain('12345678')
    // The clock kept running while the page was away: it neither restarted nor slid.
    expect(wrapper.text()).toContain('4:58')
    expect(openMaPairing).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('forgets the pairing only after the confirm dialog', async () => {
    setSource(sourceBlock({ ma_paired: true, ma_connected: true }))

    const wrapper = mountSection()
    await wrapper.get('[data-ma-pairing-forget]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalled()
    expect(cancelMaPairing).toHaveBeenCalledOnce()
  })

  it('keeps the forget action behind the confirm: a refusal changes nothing', async () => {
    confirm.confirmDialog.mockResolvedValue(false)
    setSource(sourceBlock({ ma_paired: true, ma_connected: true }))

    const wrapper = mountSection()
    await wrapper.get('[data-ma-pairing-forget]').trigger('click')
    await flushPromises()

    expect(cancelMaPairing).not.toHaveBeenCalled()
  })
})
