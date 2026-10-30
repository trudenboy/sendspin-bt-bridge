import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import GuidanceBanner from '@/components/GuidanceBanner.vue'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import BlockedHints from '@/components/devices/chips/BlockedHints.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { ApiError } from '@/api/client'
import { i18n } from '@/i18n'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { makeDevice } from '../fixtures/device'
import { makeStatus } from '../fixtures/status'

// The bridge sends English plus a code; the page shows the words of the chosen
// language for a known code and the bridge's English for anything else.

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

function plugin(locale: 'en' | 'ru') {
  return createI18n({ legacy: false, locale, messages: { en, ru } })
}

function setLocale(locale: 'en' | 'ru') {
  i18n.global.locale.value = locale
  return plugin(locale)
}

const GROUP = {
  key: 'disconnected',
  severity: 'warning',
  title: 'Kitchen is disconnected',
  summary: 'Power on the speaker or trigger a reconnect.',
  code: 'disconnected',
  params: { count: 1, device: 'Kitchen', device_names: ['Kitchen'] },
  primary_action: { key: 'reconnect_device', label: 'Reconnect speaker', code: 'reconnect_device', params: { count: 1 } },
}

describe('coded server texts on the page', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => setLocale('en'))

  function banner(locale: 'en' | 'ru', group: Record<string, unknown> = GROUP) {
    const status = makeStatus()
    ;(status.bridge as Record<string, unknown>).guidance = { issue_groups: [group] }
    useBridgeStore().snapshot = status
    return mount(GuidanceBanner, { global: { plugins: [setLocale(locale)] } })
  }

  it('the guidance banner shows the words of the chosen language, action label included', () => {
    const w = banner('ru')
    expect(w.text()).toContain('Kitchen: колонка отключена')
    expect(w.text()).toContain('Включите колонку или переподключите её.')
    expect(w.get('button').text()).toBe('Переподключить колонку')
  })

  it('the banner shows the bridge English when the code is unknown or missing', () => {
    expect(banner('ru', { ...GROUP, code: 'brand_new' }).text()).toContain('Kitchen is disconnected')
    const { code: _code, ...legacy } = GROUP
    expect(banner('ru', legacy).text()).toContain('Power on the speaker or trigger a reconnect.')
  })

  it('the recovery panel and its copied text use the same words', () => {
    const store = useDiagnosticsStore()
    store.loading = false
    store.recovery = { issues: [{ ...GROUP, summary_variant: 'error', params: { ...GROUP.params, error: 'boom' } }] }
    const w = mount(RecoveryPanel, { global: { plugins: [setLocale('ru')] } })
    expect(w.text()).toContain('Kitchen: колонка отключена')
    expect(w.text()).toContain('boom')
    expect(w.text()).not.toContain('Kitchen is disconnected')
  })

  it('the header pill shows the lead issue title, or its own label', () => {
    const header = (guidance: unknown) => {
      useBridgeStore().snapshot = { ...makeStatus(), bridge: { ...makeStatus().bridge, guidance } } as never
      return mount(AppHeader, {
        global: { plugins: [i18nPlugin()], stubs: { BugReportDialog: true, SbDropdown: true, SbDropdownItem: true } },
      })
    }
    const i18nPlugin = () => plugin(i18n.global.locale.value as 'en' | 'ru')
    setLocale('ru')
    const issue = { tone: 'warning', label: 'Kitchen is disconnected', code: 'disconnected', area: 'issue', params: { count: 1, device: 'Kitchen' } }
    expect(header({ header_status: issue }).text()).toContain('Kitchen: колонка отключена')
    const startup = { tone: 'info', label: 'Startup 40%', code: 'startup', area: 'header', params: { percent: 40 } }
    expect(header({ header_status: startup }).text()).toContain('Запуск 40%')
  })

  it('timeline entries show translated title, source and summary; unknown ones stay English', async () => {
    timeline.mockResolvedValue({
      entries: [
        { at: '2026-01-01T10:00:00Z', level: 'info', source: 'Bridge startup', source_code: 'bridge_startup', label: 'ready', summary: 'Startup complete', code: 'startup-ready', params: {} },
        { at: '2026-01-01T10:00:01Z', level: 'info', source: 'Kitchen', label: 'mystery', summary: 'Something new', code: 'mystery-event', params: {} },
      ],
    })
    const w = mount(EventTimeline, { global: { plugins: [setLocale('ru')] } })
    await flushPromises()
    expect(w.text()).toContain('Готово')
    expect(w.text()).toContain('Запуск моста: Запуск завершён')
    expect(w.text()).toContain('Kitchen: Something new')
  })

  it('a blocked control explains itself in the chosen language', async () => {
    const device = makeDevice({
      capabilities: {
        actions: {
          play_pause: {
            supported: true,
            currently_available: false,
            blocked_reason: 'Sendspin is not connected.',
            blocked_reason_detail: { code: 'daemon_disconnected' },
            depends_on: [],
            recommended_action: null,
          },
        },
      },
    })
    const w = mount(BlockedHints, { props: { device }, global: { plugins: [setLocale('ru')] } })
    expect(w.text()).toContain('Аудиоканал с Music Assistant не подключён.')
  })

  it('an API error reads in the chosen language for a fixed problem and keeps the detail otherwise', () => {
    setLocale('ru')
    expect(new ApiError({ status: 401, code: 'unauthorized', detail: 'Not authenticated' }).message).toBe('Войдите, чтобы продолжить.')
    expect(new ApiError({ status: 400, code: 'invalid_adapter', detail: 'Invalid adapter identifier: x' }).message).toBe(
      'Invalid adapter identifier: x',
    )
  })
})
