import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import GuidanceBanner from '@/components/GuidanceBanner.vue'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { i18n } from '@/i18n'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import keys from '../fixtures/server-text-keys.json'
import { makeStatus } from '../fixtures/status'

// Regression guard (UX stage 8): in the Russian UI, no English text from the
// API is rendered for a code the bridge can emit. Every code in the registry
// fixture is rendered once with an English sentinel as the bridge's fallback
// text; the sentinel appearing means a screen ignored the code.

const SENTINEL = 'ENGLISH-FROM-THE-API'
const timeline = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ query: {}, hash: '' }) }))
vi.mock('@/api/diagnostics', () => ({
  getDiagnostics: vi.fn().mockResolvedValue({}),
  getRecoveryAssistant: vi.fn().mockResolvedValue({}),
  getOperatorGuidance: vi.fn().mockResolvedValue({}),
  getRecoveryTimeline: (...a: unknown[]) => timeline(...a),
  downloadTimelineCsv: vi.fn(),
  rerunCheck: vi.fn(),
}))
vi.mock('@/stores/update', () => ({
  useUpdateStore: () => ({ updateAvailable: false, latestVersion: '', openDialog: vi.fn(), fetchInfo: vi.fn() }),
}))

const codes = (area: string, leaf: string) =>
  (keys as string[]).filter((k) => k.startsWith(`server.${area}.`) && k.endsWith(`.${leaf}`)).map((k) => k.split('.')[2]!)

const issueCodes = codes('issue', 'title')
const actionCodes = codes('action', 'label')
/** Checklist steps (setup_*) carry a title only; their detail line is the bridge's own sentence. */
const issueHasSummary = new Set(codes('issue', 'summary'))
const eventCodes = codes('event', 'summary')

function plugin() {
  i18n.global.locale.value = 'ru'
  return createI18n({ legacy: false, locale: 'ru', messages: { en, ru } })
}

describe('Russian UI: no English from the API for a known code', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    i18n.global.locale.value = 'en'
  })

  it('the registry has codes to check', () => {
    expect(issueCodes.length).toBeGreaterThan(20)
    expect(actionCodes.length).toBeGreaterThan(10)
    expect(eventCodes.length).toBeGreaterThan(20)
  })

  it('the checker can fail: an unknown code shows the English text', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [{ key: 'x', severity: 'warning', code: 'brand_new', title: SENTINEL, summary: SENTINEL }] }
    expect(mount(RecoveryPanel, { global: { plugins: [plugin()] } }).text()).toContain(SENTINEL)
  })

  it('every issue code reads in Russian on the recovery panel', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = {
      issues: issueCodes.map((code) => ({
        key: code,
        severity: 'warning',
        code,
        title: SENTINEL,
        summary: issueHasSummary.has(code) ? SENTINEL : undefined,
        params: { count: 1, device: 'Kitchen', device_names: ['Kitchen'] },
      })),
    }
    const text = mount(RecoveryPanel, { global: { plugins: [plugin()] } }).text()
    expect(text).not.toContain(SENTINEL)
    expect(text).not.toMatch(/\{\w+\}/)
  })

  it('every action code reads in Russian on the guidance banner', () => {
    const status = makeStatus()
    useBridgeStore().snapshot = status
    for (const code of actionCodes) {
      ;(status.bridge as Record<string, unknown>).guidance = {
        issue_groups: [
          {
            key: 'k',
            severity: 'warning',
            title: 'Kitchen is disconnected',
            summary: 'Power on the speaker.',
            code: 'disconnected',
            params: { count: 1, device: 'Kitchen', device_names: ['Kitchen'] },
            primary_action: { key: code, label: SENTINEL, code, params: { count: 1 } },
          },
        ],
      }
      const w = mount(GuidanceBanner, { global: { plugins: [plugin()] } })
      expect(w.text(), `action ${code}`).not.toContain(SENTINEL)
      w.unmount()
    }
  })

  it('every event code reads in Russian on the timeline', async () => {
    timeline.mockResolvedValue({
      entries: eventCodes.map((code, i) => ({
        at: `2026-01-01T10:00:${String(i % 60).padStart(2, '0')}Z`,
        level: 'info',
        source: 'Kitchen',
        label: 'event',
        summary: SENTINEL,
        code,
        params: { device: 'Kitchen' },
      })),
    })
    const w = mount(EventTimeline, { global: { plugins: [plugin()] } })
    await flushPromises()
    expect(w.text()).not.toContain(SENTINEL)
  })
})
