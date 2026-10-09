import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  getSession: vi.fn(),
  signIn: vi.fn(),
  signOut: vi.fn().mockResolvedValue(undefined),
}))

import { getSession, signIn, signOut } from '@/api/auth'
import { getCsrfToken } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const session = (over: Record<string, unknown> = {}) => ({
  authenticated: false,
  user: null,
  principal: null,
  auth_enabled: true,
  ha_addon: false,
  methods: ['ma', 'password'],
  csrf_token: 'csrf-1',
  ...over,
})

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('reads the session and keeps its CSRF token for later writes', async () => {
    vi.mocked(getSession).mockResolvedValue(session({ authenticated: true, user: 'alice', principal: 'session' }) as never)
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.authenticated).toBe(true)
    expect(store.username).toBe('alice')
    expect(store.methods).toEqual(['ma', 'password'])
    expect(getCsrfToken()).toBe('csrf-1')
  })

  it('treats an unreachable session endpoint as signed out', async () => {
    vi.mocked(getSession).mockRejectedValue(new Error('down'))
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.authenticated).toBe(false)
  })

  it('signs in and rotates the CSRF token', async () => {
    vi.mocked(signIn).mockResolvedValue({ status: 'signed_in', user: 'alice', csrf_token: 'csrf-2' } as never)
    const store = useAuthStore()
    const result = await store.login('password', 'secret')
    expect(signIn).toHaveBeenCalledWith({ method: 'password', username: '', password: 'secret' })
    expect(result.status).toBe('signed_in')
    expect(store.authenticated).toBe(true)
    expect(getCsrfToken()).toBe('csrf-2')
  })

  it('remembers a pending MFA step and finishes it with the code', async () => {
    vi.mocked(signIn)
      .mockResolvedValueOnce({ status: 'mfa_required', flow_id: 'f1', mfa_module_name: 'TOTP', csrf_token: 'c' } as never)
      .mockResolvedValueOnce({ status: 'signed_in', user: 'alice', csrf_token: 'c' } as never)
    const store = useAuthStore()
    await store.login('ha_via_ma', 'pw', 'alice')
    expect(store.authenticated).toBe(false)
    expect(store.pendingFlow).toEqual({ method: 'ha_via_ma', flowId: 'f1', moduleName: 'TOTP' })

    await store.submitCode('123456')
    expect(signIn).toHaveBeenLastCalledWith({ method: 'ha_via_ma', flow_id: 'f1', code: '123456' })
    expect(store.authenticated).toBe(true)
    expect(store.pendingFlow).toBeNull()
  })

  it('signs out and re-reads the session', async () => {
    vi.mocked(getSession).mockResolvedValue(session() as never)
    const store = useAuthStore()
    store.authenticated = true
    await store.logout()
    expect(signOut).toHaveBeenCalled()
    expect(store.authenticated).toBe(false)
  })
})
