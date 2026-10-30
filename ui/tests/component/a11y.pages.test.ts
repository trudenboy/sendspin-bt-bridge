import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import ConfigView from '@/views/ConfigView.vue'
import DiagnosticsView from '@/views/DiagnosticsView.vue'
import { getConfig } from '@/api/config'
import { setTechnicalDetails } from '@/composables/useTechnicalDetails'
import { seriousViolations } from '../helpers/axe'

// Regression guard (UX stage 8): no serious or critical axe violation on the
// Settings and Diagnostics pages, in both languages, with technical details on
// and off.

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
  players: [],
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
vi.mock('@/components/settings/LatencyAssistantSection.vue', () => ({ default: { template: '<div />' } }))
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
const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => confirm)
import { restartBridge } from '@/api/status'
vi.mock('@/composables/useIngress', () => ({ useIngress: () => ({ basePath: '', apiBase: '' }) }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn(), useRoute: () => ({ hash: '', query: {} }) }))

vi.mock('@/stores/diagnostics', () => ({
  useDiagnosticsStore: () => ({ fetchDiagnostics: vi.fn(), fetchRecovery: vi.fn(), loading: false, recovery: null }),
}))
vi.mock('@/components/BugReportDialog.vue', () => ({ default: { template: '<div />' } }))

let mounted: VueWrapper | null = null
afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.innerHTML = ''
})
const plugin = (locale: 'en' | 'ru') => createI18n({ legacy: false, locale, messages: { en, ru } })

describe('accessibility: pages', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    localStorage.clear()
  })

  for (const locale of ['en', 'ru'] as const) {
    for (const technical of [true, false]) {
      it(`Settings has no serious violations (${locale}, technical details ${technical ? 'on' : 'off'})`, async () => {
        setTechnicalDetails(technical)
        vi.mocked(getConfig).mockResolvedValue(structuredClone(BASE) as never)
        mounted = mount(ConfigView, {
          global: { plugins: [plugin(locale)], stubs: { MaLoginFlow: true, Teleport: true } },
          attachTo: document.body,
        })
        await flushPromises()
        expect(await seriousViolations()).toEqual([])
      })
    }

    it(`Diagnostics has no serious violations (${locale})`, async () => {
      mounted = mount(DiagnosticsView, {
        global: {
          plugins: [plugin(locale)],
          stubs: { HealthSummary: true, EventTimeline: true, RecoveryPanel: true, LogsViewer: true, SystemPanel: true, BugReportDialog: true },
        },
        attachTo: document.body,
      })
      await flushPromises()
      expect(await seriousViolations()).toEqual([])
    })
  }
})
