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
    const { players: _players, ...settings } = store.config!
    expect(saveConfig).toHaveBeenCalledWith(settings)
    expect(store.isDirty).toBe(false)
  })

  it('a settings save leaves the players to the bridge', async () => {
    // The players list was read when the page opened. Since then the bridge
    // may have written a speaker's latency compensation, an air calibration or
    // a learned latency; sending the old list would put the old values back.
    const loaded = {
      ...MOCK_CONFIG,
      players: [{ id: 'p1', name: 'Kitchen', static_delay_ms: 0 }],
    } as unknown as BridgeConfig
    vi.mocked(getConfig).mockResolvedValue(loaded)
    const store = useConfigStore()
    await store.fetchConfig()
    store.config!.LOG_LEVEL = 'DEBUG'

    await store.saveConfig()

    const sent = vi.mocked(saveConfig).mock.calls[0][0] as Record<string, unknown>
    expect(sent).not.toHaveProperty('players')
    expect(sent.LOG_LEVEL).toBe('DEBUG')
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
      errors: [
        { field: 'WEB_PORT', message: 'Invalid port' },
        { field: 'players[0].name', message: 'Missing name' },
      ],
      warnings: [],
      normalized_config: {},
    })
    const store = useConfigStore()
    await store.fetchConfig()
    await store.validateConfig()
    expect(store.isValid).toBe(false)
    expect(store.validationErrors.WEB_PORT).toBe('Invalid port')
  })

  it('validateConfig clears errors on success', async () => {
    vi.mocked(getConfig).mockResolvedValue(MOCK_CONFIG)
    vi.mocked(validateConfig).mockResolvedValue({ valid: true, errors: [], warnings: [], normalized_config: {} })
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
