import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import type { BridgeConfig } from '@/api/types'
import ConfigAudio from '@/components/config/ConfigAudio.vue'

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

  return mount(ConfigAudio, {
    global: {
      plugins: [i18n],
    },
  })
}

describe('ConfigAudio', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getConfig).mockResolvedValue({ ...MOCK_CONFIG })
    const store = useConfigStore()
    await store.fetchConfig()
  })

  it('renders pulse latency slider', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Pulse Latency')
  })

  it('renders SBC codec toggle', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Prefer SBC Codec')
  })

  it('renders static delay slider', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('Static Audio Delay')
  })

  it('renders pulse latency hint', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('buffer latency in milliseconds')
  })

  it('renders SBC hint', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('better compatibility')
  })

  it('renders static delay hint', () => {
    const wrapper = createWrapper()
    expect(wrapper.text()).toContain('compensate for Bluetooth latency')
  })

  it('has range inputs for sliders', () => {
    const wrapper = createWrapper()
    const rangeInputs = wrapper.findAll('input[type="range"]')
    expect(rangeInputs.length).toBeGreaterThanOrEqual(2)
  })
})
