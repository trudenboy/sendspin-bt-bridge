import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { setActivePinia, createPinia } from 'pinia'
import router from '@/router'
import LoginView from '@/views/LoginView.vue'
import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  session: { authenticated: false, auth_enabled: true, methods: ['password'], ha_addon: false, csrf_token: 't' },
  signIn: vi.fn(),
}))
vi.mock('@/api/auth', () => ({ getSession: vi.fn(async () => api.session), signIn: api.signIn, signOut: vi.fn() }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ sseConnected: true, snapshot: {}, connectSSE: vi.fn() }) }))

describe('LoginView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('opens the home page after a successful sign-in', async () => {
    api.signIn.mockResolvedValue({ status: 'signed_in', csrf_token: 't', user: 'admin' })
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
    const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
    const w = mount(LoginView, { global: { plugins: [router, i18n] } })
    await w.find('input[type="password"]').setValue('secret')
    await w.find('form').trigger('submit')
    await flushPromises()
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('home'))
  })
})
