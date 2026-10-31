import { describe, it, expect } from 'vitest'
import router from '@/router'

describe('router', () => {
  it('has a group sync page per Music Assistant group', () => {
    const route = router.resolve({ name: 'sync', params: { groupId: 'syncgroup_x' } })
    expect(route.path).toBe('/sync/syncgroup_x')
    expect(route.meta.title).toBe('nav.sync')
  })
})

describe('hardware page', () => {
  it('is a route of its own with the navigation title', () => {
    const route = router.resolve('/hardware')
    expect(route.name).toBe('hardware')
    expect(route.meta.title).toBe('nav.hardware')
  })
})

describe('navigation', () => {
  it('keeps Groups highlighted on a group sync page', async () => {
    const { isActive } = await import('@/router/nav')
    expect(isActive('/sync/syncgroup_x', '/groups')).toBe(true)
    expect(isActive('/sync/syncgroup_x', '/config')).toBe(false)
  })

  it('lists Hardware as the fifth place', async () => {
    const { NAV } = await import('@/router/nav')
    expect(NAV).toHaveLength(5)
    expect(NAV[4]).toMatchObject({ to: '/hardware', label: 'nav.hardware', short: 'nav.hardwareShort' })
    expect(NAV[4]!.icon).toBeTruthy()
  })
})
