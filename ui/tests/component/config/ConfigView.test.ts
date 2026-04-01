import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import ConfigView from '@/views/ConfigView.vue'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn().mockResolvedValue({
    BRIDGE_NAME: 'test',
    SENDSPIN_SERVER: 'auto',
    SENDSPIN_PORT: 9000,
    WEB_PORT: 8080,
    TZ: 'UTC',
    LOG_LEVEL: 'INFO',
    BLUETOOTH_DEVICES: [],
    players: [],
    adapters: [],
    MA_API_URL: '',
    MA_API_TOKEN: '',
    VOLUME_VIA_MA: false,
    PULSE_LATENCY_MSEC: 800,
    PREFER_SBC_CODEC: false,
    BT_CHECK_INTERVAL: 15,
    BT_MAX_RECONNECT_FAILS: 10,
  }),
  saveConfig: vi.fn().mockResolvedValue({ success: true }),
  validateConfig: vi.fn().mockResolvedValue({ valid: true }),
  downloadConfig: vi.fn(),
  uploadConfig: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

vi.mock('@/api/status', () => ({
  getStatus: vi.fn().mockResolvedValue({ devices: [], groups: [], adapters: [] }),
}))

vi.mock('@/composables/useSSE', () => ({
  useSSE: () => ({ connected: { value: false } }),
}))

function createWrapper() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })

  return mount(ConfigView, {
    global: {
      plugins: [i18n],
    },
  })
}

describe('ConfigView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('renders page title', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Configuration')
  })

  it('renders save button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Save')
  })

  it('renders reset button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Reset')
  })

  it('renders validate button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Validate')
  })

  it('renders all 6 tab buttons', () => {
    const wrapper = createWrapper()
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.length).toBe(6)
  })

  it('renders tab labels', () => {
    const wrapper = createWrapper()
    const text = wrapper.text()
    expect(text).toContain('General')
    expect(text).toContain('Audio')
    expect(text).toContain('Bluetooth')
    expect(text).toContain('Music Assistant')
    expect(text).toContain('Security')
    expect(text).toContain('Advanced')
  })

  it('shows spinner while loading', async () => {
    const { useConfigStore } = await import('@/stores/config')
    const store = useConfigStore()
    store.loading = true
    const wrapper = createWrapper()
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('does not show unsaved badge initially', async () => {
    const wrapper = createWrapper()
    // isDirty is false by default, so no badge
    const badgeTexts = wrapper.findAll('.inline-flex').map((el) => el.text())
    const hasUnsaved = badgeTexts.some((t) => t.includes('Unsaved'))
    expect(hasUnsaved).toBe(false)
  })
})
