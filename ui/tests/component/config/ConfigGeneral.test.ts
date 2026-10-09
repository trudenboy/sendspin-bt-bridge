import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import type { BridgeConfig } from '@/api/types'
import ConfigGeneral from '@/components/config/ConfigGeneral.vue'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(),
  saveConfig: vi.fn().mockResolvedValue({ success: true }),
  validateConfig: vi.fn().mockResolvedValue({ valid: true }),
  downloadConfig: vi.fn(),
  uploadConfig: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { getConfig } from '@/api/config'
import { useConfigStore } from '@/stores/config'

const MOCK_CONFIG: BridgeConfig = {
  BRIDGE_NAME: 'test-bridge',
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
}

function createWrapper() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })

  return mount(ConfigGeneral, {
    global: {
      plugins: [i18n],
    },
  })
}

describe('ConfigGeneral', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getConfig).mockResolvedValue({ ...MOCK_CONFIG })
    const store = useConfigStore()
    await store.fetchConfig()
  })

  it('renders bridge name input', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Bridge Name')
  })

  it('renders timezone input', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Timezone')
  })

  it('renders web port input', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Web Port')
  })

  it('renders log level selector', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Log Level')
  })

  it('displays current bridge name value', () => {
    const wrapper = createWrapper()
    const inputs = wrapper.findAll('input')
    const nameInput = inputs.find(
      (i) => (i.element as HTMLInputElement).value === 'test-bridge',
    )
    expect(nameInput).toBeTruthy()
  })

  it('updates store when bridge name changes', async () => {
    const wrapper = createWrapper()
    const store = useConfigStore()
    const inputs = wrapper.findAll('input')
    // First input is bridge name
    await inputs[0].setValue('new-name')
    expect(store.config?.BRIDGE_NAME).toBe('new-name')
  })

  it('renders timezone quick-select buttons', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('UTC')
    expect(wrapper.text()).toContain('Europe/Moscow')
  })

  it('renders bridge name hint', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Auto-populated from hostname')
  })
})
