import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import RuntimeRow from '@/components/layout/RuntimeRow.vue'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import en from '@/i18n/locales/en.json'
import { makeDevice } from '../../fixtures/device'
import { makeStatus } from '../../fixtures/status'

vi.mock('@/stores/update', () => ({
  useUpdateStore: () => ({ updateAvailable: false, latestVersion: '', openDialog: vi.fn(), fetchInfo: vi.fn() }),
}))

const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: ['/', '/groups', '/config', '/diagnostics'].map((path) => ({ path, name: path, component: { template: '<div />' } })),
  })
const plugins = () => [createI18n({ legacy: false, locale: 'en', messages: { en } }), router()]

function setup(embedded: boolean) {
  setActivePinia(createPinia())
  localStorage.clear()
  useTheme().homeAssistant.value = embedded ? { dark: false, primary: null } : null
  const bridge = useBridgeStore()
  const status = makeStatus([makeDevice(), makeDevice({ id: 'b' })])
  Object.assign(status.bridge, { runtime: 'ha_addon', hostname: 'ha', ip_address: '10.0.0.5', uptime: '1 day' })
  bridge.snapshot = status
}

const mountHeader = () =>
  mount(AppHeader, { global: { plugins: plugins(), stubs: { BugReportDialog: true, SbDropdown: true, SbDropdownItem: true } } })

describe('Home Assistant embedded layout', () => {
  beforeEach(() => setup(true))

  it('has no brand header and no sponsor link, but keeps the status pill with the runtime facts', () => {
    const w = mountHeader()
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).not.toContain('Sendspin Bluetooth Bridge')
    expect(w.find('a[href*="support"]').exists()).toBe(false)
    const pill = w.get('[data-status-pill]')
    expect(pill.attributes('title')).toContain('10.0.0.5')
    expect(pill.attributes('title')).toContain('Home Assistant add-on')
  })

  it('folds the runtime row away', () => {
    const w = mount(RuntimeRow, { global: { plugins: plugins() } })
    expect(w.find('[role="group"]').exists()).toBe(false)
  })

  it('starts with the sidebar collapsed unless the person chose otherwise', () => {
    const w = mount(AppSidebar, { global: { plugins: plugins() } })
    expect(w.get('[data-testid="app-sidebar"]').classes()).toContain('w-16')
    localStorage.setItem('sendspin-ui:sidebar-collapsed', 'false')
    const open = mount(AppSidebar, { global: { plugins: plugins() } })
    expect(open.get('[data-testid="app-sidebar"]').classes()).toContain('w-60')
  })
})

describe('outside Home Assistant', () => {
  beforeEach(() => setup(false))

  it('keeps the brand header, sponsor link, runtime row and the open sidebar', () => {
    const w = mountHeader()
    expect(w.find('img').exists()).toBe(true)
    expect(w.find('a[href*="support"]').exists()).toBe(true)
    expect(mount(RuntimeRow, { global: { plugins: plugins() } }).find('[role="group"]').exists()).toBe(true)
    expect(mount(AppSidebar, { global: { plugins: plugins() } }).get('[data-testid="app-sidebar"]').classes()).toContain('w-60')
  })
})
