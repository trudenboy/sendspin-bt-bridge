import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import en from '@/i18n/en.json'

const SIDEBAR_KEY = 'sendspin-ui:sidebar-collapsed'

let store: Record<string, string> = {}

vi.stubGlobal('localStorage', {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value },
  removeItem: (key: string) => { delete store[key] },
})

function buildRouter(initialRoute = '/') {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/groups', name: 'groups', component: { template: '<div />' } },
      { path: '/config', name: 'config', component: { template: '<div />' } },
      { path: '/diagnostics', name: 'diagnostics', component: { template: '<div />' } },
    ],
  })
}

function buildI18n() {
  return createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
  })
}

async function mountSidebar(initialRoute = '/') {
  const router = buildRouter(initialRoute)
  await router.push(initialRoute)
  await router.isReady()

  return mount(AppSidebar, {
    global: {
      plugins: [router, buildI18n()],
    },
  })
}

describe('AppSidebar', () => {
  beforeEach(() => {
    store = {}
    setActivePinia(createPinia())
  })

  it('links the four places of the app', async () => {
    const wrapper = await mountSidebar()
    expect(wrapper.findAll('a').map((a) => [a.attributes('href'), a.text()])).toEqual([
      ['/', 'Speakers'],
      ['/groups', 'Groups'],
      ['/config', 'Settings'],
      ['/diagnostics', 'Diagnostics'],
    ])
  })

  it('highlights the active route', async () => {
    const wrapper = await mountSidebar('/groups')
    const link = wrapper.findAll('a').find((a) => a.attributes('href') === '/groups')!
    expect(link.classes()).toContain('text-primary-text')
    expect(link.classes()).toContain('bg-primary/10')
  })

  it('does not highlight inactive routes', async () => {
    const wrapper = await mountSidebar('/groups')
    const dashboardLink = wrapper.findAll('a').find((a) => a.attributes('href') === '/')!
    expect(dashboardLink.classes()).toContain('text-text-secondary')
  })

  it('renders collapse toggle button', async () => {
    const wrapper = await mountSidebar()
    const toggle = wrapper.find('[data-testid="sidebar-toggle"]')
    expect(toggle.exists()).toBe(true)
  })

  it('collapses on toggle click and hides labels', async () => {
    const wrapper = await mountSidebar()

    // Initially labels visible
    expect(wrapper.text()).toContain('Speakers')

    // Collapse
    await wrapper.find('[data-testid="sidebar-toggle"]').trigger('click')

    // Labels hidden (truncate spans have v-if="!collapsed")
    const labelSpans = wrapper.findAll('nav a span')
    expect(labelSpans.length).toBe(0)
  })

  it('persists collapsed state in localStorage', async () => {
    const wrapper = await mountSidebar()
    await wrapper.find('[data-testid="sidebar-toggle"]').trigger('click')
    expect(store[SIDEBAR_KEY]).toBe('true')
  })

  it('restores collapsed state from localStorage', async () => {
    store[SIDEBAR_KEY] = 'true'
    const wrapper = await mountSidebar()
    // Should start collapsed — no label spans
    const labelSpans = wrapper.findAll('nav a span')
    expect(labelSpans.length).toBe(0)
  })

  it('has correct width classes when expanded', async () => {
    const wrapper = await mountSidebar()
    const aside = wrapper.find('[data-testid="app-sidebar"]')
    expect(aside.classes()).toContain('w-60')
  })

  it('has correct width classes when collapsed', async () => {
    store[SIDEBAR_KEY] = 'true'
    const wrapper = await mountSidebar()
    const aside = wrapper.find('[data-testid="app-sidebar"]')
    expect(aside.classes()).toContain('w-16')
  })
})
