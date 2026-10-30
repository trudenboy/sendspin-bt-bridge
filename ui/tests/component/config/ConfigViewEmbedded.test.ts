import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/locales/en.json'
import ConfigView from '@/views/ConfigView.vue'
import { getConfig } from '@/api/config'
import { useTheme } from '@/composables/useTheme'

vi.mock('@/api/config', () => ({
  getConfig: vi.fn(), saveConfig: vi.fn(), validateConfig: vi.fn(), downloadConfig: vi.fn(), uploadConfig: vi.fn(), testSendspin: vi.fn(),
}))
vi.mock('@/components/settings/LatencyAssistantSection.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/api/auth', () => ({ listTokens: vi.fn().mockResolvedValue({ tokens: [] }), issueToken: vi.fn(), revokeToken: vi.fn(), setPassword: vi.fn() }))
vi.mock('@/api/haIntegration', () => ({
  getMqttStatus: vi.fn().mockResolvedValue({}), getMosquitto: vi.fn().mockResolvedValue({ available: false }),
  getCustomComponent: vi.fn().mockResolvedValue({ installed: false }), getMdns: vi.fn().mockResolvedValue({}), probeMqtt: vi.fn(), testMqtt: vi.fn(),
}))
vi.mock('@/api/updates', () => ({ getUpdateInfo: vi.fn().mockResolvedValue({ update_available: false }), startUpdateCheck: vi.fn(), applyUpdate: vi.fn() }))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/composables/useIngress', () => ({ useIngress: () => ({ basePath: '', apiBase: '' }) }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn(), useRoute: () => ({ hash: '' }) }))

const CONFIG = {
  BRIDGE_NAME: 'b', TZ: 'UTC', LOG_LEVEL: 'INFO', HA_INTEGRATION: { mode: 'off', mqtt: {}, rest: {} },
  BLUETOOTH_ADAPTERS: [], players: [], _password_set: true, TRUSTED_PROXIES: [],
}

async function mountView(embedded: boolean) {
  useTheme().homeAssistant.value = embedded ? { dark: false, primary: null } : null
  vi.mocked(getConfig).mockResolvedValue(structuredClone(CONFIG) as never)
  const w = mount(ConfigView, {
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })], stubs: { MaLoginFlow: true, Teleport: true } },
  })
  await flushPromises()
  return w
}
const section = (w: Awaited<ReturnType<typeof mountView>>, id: string) => w.get(`#settings-${id}`)

describe('Settings inside Home Assistant', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('replaces Security with where sign-in comes from', async () => {
    const w = await mountView(true)
    const s = section(w, 'security')
    expect(s.text()).toContain('Signed in through Home Assistant')
    expect(s.find('input, button[role="switch"], [role="switch"]').exists()).toBe(false)
  })

  it('replaces the update settings with a pointer to the add-on store', async () => {
    const w = await mountView(true)
    const s = section(w, 'updates')
    expect(s.text()).toContain('Updates come from the Home Assistant add-on store')
    expect(s.find('select, [role="switch"]').exists()).toBe(false)
  })

  it('shows both sections as usual elsewhere', async () => {
    const w = await mountView(false)
    expect(section(w, 'security').text()).not.toContain('Signed in through Home Assistant')
    expect(section(w, 'updates').find('select').exists()).toBe(true)
  })
})
