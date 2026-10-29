import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import DiagnosticsView from '@/views/DiagnosticsView.vue'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const route = vi.hoisted(() => ({ query: {} as Record<string, string> }))
vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/stores/diagnostics', () => ({
  useDiagnosticsStore: () => ({ fetchDiagnostics: vi.fn(), fetchRecovery: vi.fn(), loading: false, recovery: null }),
}))
for (const c of ['HealthSummary', 'EventTimeline', 'RecoveryPanel', 'LogsViewer', 'SystemPanel']) {
  vi.doMock(`@/components/diagnostics/${c}.vue`, () => ({ default: { template: '<div />' } }))
}
vi.mock('@/components/BugReportDialog.vue', () => ({ default: { template: '<div />' } }))

describe('DiagnosticsView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('tab labels follow the UI language', async () => {
    const i18n = createI18n({ legacy: false, locale: 'en', messages: { en, ru } })
    const wrapper = mount(DiagnosticsView, {
      global: { plugins: [i18n], stubs: { HealthSummary: true, EventTimeline: true, RecoveryPanel: true, LogsViewer: true, SystemPanel: true, BugReportDialog: true } },
    })
    await flushPromises()
    const tabs = () => wrapper.findAll('[role="tab"]').map((t) => t.text())
    expect(tabs()[0]).toBe(en.diagnostics.tabs.overview)
    i18n.global.locale.value = 'ru'
    await flushPromises()
    expect(tabs()[0]).toBe(ru.diagnostics.tabs.overview)
  })

  it('opens the tab named in the ?tab= query', async () => {
    route.query = { tab: 'logs' }
    const wrapper = mount(DiagnosticsView, {
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en, ru } })], stubs: { HealthSummary: true, EventTimeline: true, RecoveryPanel: true, LogsViewer: true, SystemPanel: true, BugReportDialog: true } },
    })
    await flushPromises()
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.find((t) => t.attributes('aria-selected') === 'true')!.text()).toBe(en.diagnostics.tabs.logs)
    route.query = {}
  })
})
