import { describe, it, expect } from 'vitest'
import router from '@/router'

describe('router', () => {
  it('has a group sync page per Music Assistant group', () => {
    const route = router.resolve({ name: 'sync', params: { groupId: 'syncgroup_x' } })
    expect(route.path).toBe('/sync/syncgroup_x')
    expect(route.meta.title).toBe('nav.sync')
  })
})

describe('navigation', () => {
  it('keeps Groups highlighted on a group sync page', async () => {
    const { isActive } = await import('@/router/nav')
    expect(isActive('/sync/syncgroup_x', '/groups')).toBe(true)
    expect(isActive('/sync/syncgroup_x', '/config')).toBe(false)
  })
})
