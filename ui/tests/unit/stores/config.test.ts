import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(),
  saveConfig: vi.fn().mockResolvedValue({ success: true }),
  validateConfig: vi.fn(),
  downloadConfig: vi.fn(),
  uploadConfig: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { getConfig, saveConfig, validateConfig, uploadConfig } from '@/api/config'
import { useConfigStore } from '@/stores/config'
import type { BridgeConfig } from '@/api/types'

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

describe('useConfigStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useConfigStore()
    expect(store.config).toBeNull()
    expect(store.originalConfig).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.saving).toBe(false)
    expect(store.validationErrors).toEqual({})
    expect(store.isDirty).toBe(false)
    expect(store.isValid).toBe(true)
  })

  it('fetchConfig loads config and sets original', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const store = useConfigStore()
    await store.fetchConfig()
    expect(store.config).toEqual(MOCK_CONFIG)
    expect(store.originalConfig).toEqual(MOCK_CONFIG)
    expect(store.loading).toBe(false)
  })

  it('isDirty detects changes', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const store = useConfigStore()
    await store.fetchConfig()
    expect(store.isDirty).toBe(false)
    store.config!.BRIDGE_NAME = 'modified'
    expect(store.isDirty).toBe(true)
  })

  it('saveConfig persists and resets dirty state', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const store = useConfigStore()
    await store.fetchConfig()
    store.config!.BRIDGE_NAME = 'modified'
    expect(store.isDirty).toBe(true)
    await store.saveConfig()
    expect(saveConfig).toHaveBeenCalledWith(store.config)
    expect(store.isDirty).toBe(false)
  })

  it('updateField sets nested value', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const store = useConfigStore()
    await store.fetchConfig()
    store.updateField('BRIDGE_NAME', 'new-name')
    expect(store.config!.BRIDGE_NAME).toBe('new-name')
  })

  it('resetChanges reverts to original', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const store = useConfigStore()
    await store.fetchConfig()
    store.config!.BRIDGE_NAME = 'modified'
    store.resetChanges()
    expect(store.config!.BRIDGE_NAME).toBe('test-bridge')
    expect(store.isDirty).toBe(false)
  })

  it('validateConfig populates errors on failure', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    vi.mocked(validateConfig).mockResolvedValue({
      valid: false,
      errors: ['Invalid port', 'Missing name'],
    })
    const store = useConfigStore()
    await store.fetchConfig()
    await store.validateConfig()
    expect(store.isValid).toBe(false)
    expect(Object.values(store.validationErrors)).toContain('Invalid port')
  })

  it('validateConfig clears errors on success', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    vi.mocked(validateConfig).mockResolvedValue({ valid: true })
    const store = useConfigStore()
    await store.fetchConfig()
    store.validationErrors = { '0': 'old error' }
    await store.validateConfig()
    expect(store.isValid).toBe(true)
  })

  it('uploadConfig calls API then refreshes', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    const file = new File(['{}'], 'config.json', { type: 'application/json' })
    const store = useConfigStore()
    await store.uploadConfig(file)
    expect(uploadConfig).toHaveBeenCalledWith(file)
    expect(getConfig).toHaveBeenCalled()
  })
})
