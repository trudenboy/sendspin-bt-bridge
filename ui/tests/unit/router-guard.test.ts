import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

const session = vi.hoisted(() => ({
  state: { authenticated: true, auth_enabled: true, methods: ['password'], ha_addon: false, csrf_token: 't' } as Record<string, unknown>,
}))
vi.mock('@/api/auth', () => ({ getSession: vi.fn(async () => session.state), signIn: vi.fn(), signOut: vi.fn() }))
vi.mock('@/stores/bridge', () => ({ useBridgeStore: () => ({ sseConnected: true, snapshot: {}, connectSSE: vi.fn() }) }))

describe('router guard', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('sends a signed-in user who opens /login to the home page', async () => {
    const { default: router } = await import('@/router')
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('sends anyone who opens /login to the home page while sign-in is off', async () => {
    const { default: router } = await import('@/router')
    const auth = useAuthStore()
    auth.authEnabled = false
    auth.authenticated = false
    await router.push('/groups')
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('home')
  })
})

describe('named navigation', () => {
  it('only targets routes that exist', async () => {
    const { default: router } = await import('@/router')
    const known = new Set(router.getRoutes().map((r) => String(r.name)))
    const used: string[] = []
    const walk = (dir: string) => {
      for (const f of readdirSync(dir)) {
        const p = join(dir, f)
        if (statSync(p).isDirectory()) walk(p)
        else if (/\.(ts|vue)$/.test(f)) {
          for (const m of readFileSync(p, 'utf8').matchAll(/(?:push|replace|:to=)\(?\s*"?\{\s*name:\s*'([^']+)'/g)) used.push(m[1]!)
        }
      }
    }
    walk(join(__dirname, '../../src'))
    expect(used.length).toBeGreaterThan(3)
    expect(used.filter((n) => !known.has(n))).toEqual([])
  })
})
