import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import type { BridgeConfig } from '@/api/types'
import ConfigAdvanced from '@/components/config/ConfigAdvanced.vue'

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

  return mount(ConfigAdvanced, {
    global: {
      plugins: [i18n],
    },
  })
}

describe('ConfigAdvanced', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getConfig).mockResolvedValue({ ...MOCK_CONFIG })
    const store = useConfigStore()
    await store.fetchConfig()
  })

  it('renders JSON editor textarea', () => {
    const wrapper = createWrapper()
    const textarea = wrapper.find('[data-testid="json-editor"]')
    expect(textarea.exists()).toBe(true)
  })

  it('renders upload config button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Upload Config')
  })

  it('renders download config button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Download Config')
  })

  it('renders raw JSON warning', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Editing raw JSON can break')
    expect(wrapper.text()).toContain('break your configuration')
  })

  it('displays config as JSON in textarea', () => {
    const wrapper = createWrapper()
    const textarea = wrapper.find('[data-testid="json-editor"]')
    const value = (textarea.element as HTMLTextAreaElement).value
    expect(value).toContain('test-bridge')
    expect(value).toContain('BRIDGE_NAME')
  })

  it('has hidden file input for upload', () => {
    const wrapper = createWrapper()
    const fileInput = wrapper.find('[data-testid="file-input"]')
    expect(fileInput.exists()).toBe(true)
    expect(fileInput.attributes('type')).toBe('file')
    expect(fileInput.attributes('accept')).toBe('.json')
  })

  it('renders validate button', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Validate')
  })
})
