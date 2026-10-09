import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import MobileNav from '@/components/layout/MobileNav.vue'
import en from '@/i18n/en.json'

function buildRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' } },
      { path: '/devices', name: 'devices', component: { template: '<div />' } },
      { path: '/config', name: 'config', component: { template: '<div />' } },
      { path: '/diagnostics', name: 'diagnostics', component: { template: '<div />' } },
      { path: '/ma', name: 'ma', component: { template: '<div />' } },
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

async function mountMobileNav(initialRoute = '/') {
  const router = buildRouter()
  await router.push(initialRoute)
  await router.isReady()

  return mount(MobileNav, {
    global: {
      plugins: [router, buildI18n()],
    },
  })
}

describe('MobileNav', () => {
  it('renders 5 tab links', async () => {
    const wrapper = await mountMobileNav()
    const links = wrapper.findAll('a')
    expect(links.length).toBe(5)
  })

  it('renders all tab labels', async () => {
    const wrapper = await mountMobileNav()
    const text = wrapper.text()
    expect(text).toContain('Dashboard')
    expect(text).toContain('Devices')
    expect(text).toContain('Configuration')
    expect(text).toContain('Diagnostics')
    expect(text).toContain('Music Assistant')
  })

  it('highlights active tab', async () => {
    const wrapper = await mountMobileNav('/config')
    const configLink = wrapper.findAll('a').find((a) => a.attributes('href') === '/config')!
    expect(configLink.classes()).toContain('text-primary')
  })

  it('does not highlight inactive tabs', async () => {
    const wrapper = await mountMobileNav('/config')
    const dashboardLink = wrapper.findAll('a').find((a) => a.attributes('href') === '/')!
    expect(dashboardLink.classes()).toContain('text-text-secondary')
  })

  it('has mobile-nav testid', async () => {
    const wrapper = await mountMobileNav()
    expect(wrapper.find('[data-testid="mobile-nav"]').exists()).toBe(true)
  })

  it('renders icons for each tab', async () => {
    const wrapper = await mountMobileNav()
    // Each link should contain an SVG icon
    const links = wrapper.findAll('a')
    for (const link of links) {
      expect(link.find('svg').exists()).toBe(true)
    }
  })

  it('highlights dashboard on root route', async () => {
    const wrapper = await mountMobileNav('/')
    const dashLink = wrapper.findAll('a').find((a) => a.attributes('href') === '/')!
    expect(dashLink.classes()).toContain('text-primary')
  })

  it('does not highlight dashboard on sub-routes', async () => {
    const wrapper = await mountMobileNav('/devices')
    const dashLink = wrapper.findAll('a').find((a) => a.attributes('href') === '/')!
    expect(dashLink.classes()).not.toContain('text-primary')
  })
})
