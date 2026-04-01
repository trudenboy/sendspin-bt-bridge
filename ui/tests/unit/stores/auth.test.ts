import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  checkAuth: vi.fn(),
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue({ success: true }),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { checkAuth, login, logout } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useAuthStore()
    expect(store.authenticated).toBe(false)
    expect(store.username).toBeNull()
    expect(store.haUser).toBe(false)
    expect(store.checking).toBe(false)
  })

  it('checkAuth sets authenticated state', async () => {
    vi.mocked(checkAuth).mockResolvedValue({
      authenticated: true,
      username: 'admin',
      ha_user: true,
    })
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.authenticated).toBe(true)
    expect(store.username).toBe('admin')
    expect(store.haUser).toBe(true)
    expect(store.checking).toBe(false)
  })

  it('checkAuth handles unauthenticated', async () => {
    vi.mocked(checkAuth).mockResolvedValue({
      authenticated: false,
    })
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.authenticated).toBe(false)
    expect(store.username).toBeNull()
  })

  it('checkAuth handles API error gracefully', async () => {
    vi.mocked(checkAuth).mockRejectedValue(new Error('network'))
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.authenticated).toBe(false)
    expect(store.checking).toBe(false)
  })

  it('login sets authenticated on success', async () => {
    vi.mocked(login).mockResolvedValue({ success: true })
    const store = useAuthStore()
    const result = await store.login('secret')
    expect(login).toHaveBeenCalledWith('secret')
    expect(store.authenticated).toBe(true)
    expect(result.success).toBe(true)
  })

  it('login does not set authenticated on failure', async () => {
    vi.mocked(login).mockResolvedValue({ success: false, error: 'bad password' })
    const store = useAuthStore()
    const result = await store.login('wrong')
    expect(store.authenticated).toBe(false)
    expect(result.error).toBe('bad password')
  })

  it('logout resets all auth state', async () => {
    const store = useAuthStore()
    store.authenticated = true
    store.username = 'admin'
    store.haUser = true
    await store.logout()
    expect(logout).toHaveBeenCalled()
    expect(store.authenticated).toBe(false)
    expect(store.username).toBeNull()
    expect(store.haUser).toBe(false)
  })

  it('setAuthenticated updates flag directly', () => {
    const store = useAuthStore()
    store.setAuthenticated(true)
    expect(store.authenticated).toBe(true)
    store.setAuthenticated(false)
    expect(store.authenticated).toBe(false)
  })
})
