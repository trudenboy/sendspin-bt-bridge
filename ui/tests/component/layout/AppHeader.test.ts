import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import AppHeader from '@/components/layout/AppHeader.vue'
import en from '@/i18n/en.json'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ bridge: null, fetchBridge: vi.fn(), fetch: vi.fn() }) }))
vi.mock('@/stores/update', () => ({
  useUpdateStore: () => ({ updateAvailable: true, latestVersion: '9.9.9', openDialog: vi.fn(), fetchInfo: vi.fn() }),
}))

describe('AppHeader update badge', () => {
  it('has an accessible name (the text is hidden on phones)', () => {
    const w = mount(AppHeader, {
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        stubs: { BugReportDialog: true, SbDropdown: true, SbDropdownItem: true },
      },
    })
    const badge = w.find('button[title="Update available"]')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('aria-label')).toContain('9.9.9')
  })
})
