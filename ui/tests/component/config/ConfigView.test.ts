import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import ConfigView from '@/views/ConfigView.vue'
import { getConfig, saveConfig } from '@/api/config'
import { useConfigStore } from '@/stores/config'

const BASE = {
  BRIDGE_NAME: 'Kitchen bridge',
  TZ: 'UTC',
  WEB_PORT: 8080,
  LOG_LEVEL: 'INFO',
  SENDSPIN_SERVER: 'auto',
  SENDSPIN_PORT: 9000,
  BASE_LISTEN_PORT: 8928,
  PULSE_LATENCY_MSEC: 600,
  BRUTE_FORCE_PROTECTION: true,
  TRUSTED_PROXIES: [],
  HA_INTEGRATION: { enabled: false, mode: 'off', mqtt: { broker: 'auto', port: 1883 }, rest: {} },
  BLUETOOTH_ADAPTERS: [],
  BLUETOOTH_DEVICES: [],
  _password_set: true,
}

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(),
  saveConfig: vi.fn(),
  validateConfig: vi.fn(),
  downloadConfig: vi.fn(),
  uploadConfig: vi.fn(),
  testSendspin: vi.fn(),
}))
vi.mock('@/api/auth', () => ({
  listTokens: vi.fn().mockResolvedValue({ tokens: [] }),
  issueToken: vi.fn(),
  revokeToken: vi.fn(),
  setPassword: vi.fn(),
}))
vi.mock('@/api/haIntegration', () => ({
  getMqttStatus: vi.fn().mockResolvedValue({ running: true, state: 'connected', broker: 'core-mosquitto' }),
  getMosquitto: vi.fn().mockResolvedValue({ available: false }),
  getCustomComponent: vi.fn().mockResolvedValue({ installed: false }),
  getMdns: vi.fn().mockResolvedValue({ advertised: false }),
  probeMqtt: vi.fn(),
  testMqtt: vi.fn(),
}))
vi.mock('@/api/updates', () => ({
  getUpdateInfo: vi.fn().mockResolvedValue({ update_available: false }),
  startUpdateCheck: vi.fn(),
  applyUpdate: vi.fn(),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn().mockResolvedValue({}) }))
vi.mock('@/composables/useIngress', () => ({ useIngress: () => ({ basePath: '', apiBase: '' }) }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn(), useRoute: () => ({ hash: '' }) }))

async function mountView(config: Record<string, unknown> = BASE) {
  vi.mocked(getConfig).mockResolvedValue(structuredClone(config) as never)
  const wrapper = mount(ConfigView, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: { MaLoginFlow: true, Teleport: true },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

const labels = (w: Awaited<ReturnType<typeof mountView>>) => w.findAll('label').map((l) => l.text())

describe('ConfigView (settings)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('shows every section, named after what it does', async () => {
    const w = await mountView()
    const titles = w.findAll('section h2').map((h) => h.text())
    expect(titles).toEqual([
      'General',
      'Music Assistant',
      'Audio',
      'Bluetooth',
      'Home Assistant',
      'Security',
      'Updates',
      'Guidance',
      'Backup',
    ])
  })

  it('hides advanced settings until asked', async () => {
    const w = await mountView()
    expect(labels(w)).toContain('Bridge name')
    expect(labels(w)).not.toContain('Web port')

    await w.get('[role="switch"]').trigger('click')

    expect(labels(w)).toContain('Web port')
  })

  it('shows MQTT fields only for the MQTT connection, and keeps the legacy switch in step', async () => {
    const w = await mountView()
    expect(labels(w)).not.toContain('MQTT broker')

    const mode = w.findAll('select').find((s) => s.findAll('option').some((o) => o.text() === 'Integration'))!
    await mode.setValue('mqtt')

    expect(labels(w)).toContain('MQTT broker')
    const store = useConfigStore()
    expect(store.config?.HA_INTEGRATION).toMatchObject({ mode: 'mqtt', enabled: true })
  })

  it('offers saving only after a change, and saves the edited document', async () => {
    vi.mocked(saveConfig).mockResolvedValue({ reconfig: {}, warnings: [] } as never)
    const w = await mountView()
    expect(w.text()).not.toContain('Unsaved changes')

    const name = w.findAll('input').find((i) => (i.element as HTMLInputElement).value === 'Kitchen bridge')!
    await name.setValue('Living room bridge')
    expect(w.text()).toContain('Unsaved changes')

    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await flushPromises()

    expect(saveConfig).toHaveBeenCalledWith(expect.objectContaining({ BRIDGE_NAME: 'Living room bridge' }))
    expect(w.text()).not.toContain('Unsaved changes')
  })

  it('asks for a restart when the bridge could not apply a change live', async () => {
    vi.mocked(saveConfig).mockResolvedValue({ reconfig: { restart_required: [{ key: 'WEB_PORT' }] }, warnings: [] } as never)
    const w = await mountView()
    const name = w.findAll('input').find((i) => (i.element as HTMLInputElement).value === 'Kitchen bridge')!
    await name.setValue('x')
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Restart the bridge to apply some changes')
  })

  it('stores null for an emptied automatic number and a list from lines', async () => {
    localStorage.setItem('sendspin-ui:settings-advanced', '1')
    const w = await mountView()
    const store = useConfigStore()

    const basePort = w.findAll('input[type="number"]').find((i) => (i.element as HTMLInputElement).value === '8928')!
    await basePort.setValue('')
    await basePort.trigger('change')
    expect(store.config?.BASE_LISTEN_PORT).toBeNull()

    const proxies = w.get('textarea')
    await proxies.setValue('10.0.0.1\n 10.0.0.2 \n')
    await proxies.trigger('change')
    expect(store.config?.TRUSTED_PROXIES).toEqual(['10.0.0.1', '10.0.0.2'])
  })

  it('finds a setting by its label, including advanced ones', async () => {
    const w = await mountView()
    await w.get('input[type="search"]').setValue('web port')
    expect(w.findAll('section label').map((l) => l.text())).toEqual(['Web port'])
    expect(w.findAll('section h2').map((h) => h.text())).toEqual(['General'])

    await w.get('input[type="search"]').setValue('zzz-nothing')
    expect(w.text()).toContain('Nothing matches')
  })

  it('marks a field that was edited but not saved', async () => {
    const w = await mountView()
    expect(w.text()).not.toContain('Changed')
    const name = w.findAll('input').find((i) => (i.element as HTMLInputElement).value === 'Kitchen bridge')!
    await name.setValue('Living room bridge')
    await name.trigger('change')
    const row = w.findAll('label').find((l) => l.text().startsWith('Bridge name'))!
    expect(row.text()).toContain('Changed')
  })

  it('says why the settings did not load, and retries', async () => {
    vi.mocked(getConfig).mockRejectedValueOnce(new Error('Internal Server Error'))
    const w = mount(ConfigView, {
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        stubs: { MaLoginFlow: true },
      },
    })
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('Settings could not be loaded')

    vi.mocked(getConfig).mockResolvedValue(structuredClone(BASE) as never)
    await w.findAll('button').find((b) => b.text() === 'Try again')!.trigger('click')
    await flushPromises()
    expect(w.findAll('section h2').length).toBeGreaterThan(0)
  })
})
