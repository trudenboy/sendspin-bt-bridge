import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import SbDialog from '@/kit/SbDialog.vue'
import ConfirmConsequences from '@/kit/ConfirmConsequences.vue'
import UpdateDialog from '@/components/UpdateDialog.vue'
import BugReportDialog from '@/components/BugReportDialog.vue'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { seriousViolations } from '../helpers/axe'

// Regression guard (UX stage 8): dialogs have a role, a name and labelled
// controls, in both languages.

const update = vi.hoisted(() => ({
  showDialog: true, info: { update_available: true, version: '9.9.9' }, latestVersion: '9.9.9', channel: 'stable',
  updateMethod: 'one_click', applying: false, checking: false, loading: false, error: null, releaseNotes: '',
  doApplyUpdate: vi.fn(), checkForUpdates: vi.fn(), updateAvailable: true,
}))
vi.mock('@/stores/update', () => ({ useUpdateStore: () => update }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ version: '1.0.0' }) }))
const api = vi.hoisted(() => ({
  getBugreport: vi.fn(), checkProxyAvailable: vi.fn(), submitBugreport: vi.fn(), downloadBugreport: vi.fn(),
}))
vi.mock('@/api/diagnostics', () => api)

let mounted: VueWrapper | null = null
afterEach(() => {
  mounted?.unmount()
  mounted = null
  document.body.innerHTML = ''
})
const plugin = (locale: 'en' | 'ru') => createI18n({ legacy: false, locale, messages: { en, ru } })
const global = (locale: 'en' | 'ru') => ({ plugins: [plugin(locale)], stubs: { Teleport: true } })

describe('accessibility: dialogs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    api.getBugreport.mockResolvedValue({ markdown_short: 'm', text_full: 't', suggested_description: '' })
    api.checkProxyAvailable.mockResolvedValue({ available: true })
  })

  for (const locale of ['en', 'ru'] as const) {
    it(`the base dialog has no serious violations (${locale})`, async () => {
      mounted = mount(SbDialog, {
        props: { modelValue: true, title: 'Title' },
        slots: { default: '<p>Body</p>' },
        global: global(locale),
        attachTo: document.body,
      })
      await flushPromises()
      expect(document.querySelector('[role="dialog"]')).not.toBeNull()
      expect(await seriousViolations()).toEqual([])
    })

    it(`the confirmation dialog has no serious violations (${locale})`, async () => {
      mounted = mount(ConfirmConsequences, {
        props: { modelValue: true, title: 'Remove Kitchen?', consequences: ['Playback stops'], confirmLabel: 'Remove', cancelLabel: 'Cancel', danger: true },
        global: global(locale),
        attachTo: document.body,
      })
      await flushPromises()
      expect(await seriousViolations()).toEqual([])
    })

    it(`the update dialog has no serious violations (${locale})`, async () => {
      mounted = mount(UpdateDialog, { global: global(locale), attachTo: document.body })
      await flushPromises()
      expect(await seriousViolations()).toEqual([])
    })

    it(`the bug report dialog has no serious violations (${locale})`, async () => {
      mounted = mount(BugReportDialog, { props: { modelValue: false }, global: global(locale), attachTo: document.body })
      await mounted.setProps({ modelValue: true })
      await flushPromises()
      expect(await seriousViolations()).toEqual([])
    })
  }
})
