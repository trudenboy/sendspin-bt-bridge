import { describe, it, expect } from 'vitest'
import { basePathFor, useIngress } from '@/composables/useIngress'
import { APP_SEGMENTS } from '@/router/segments'
import router from '@/router'

describe('basePathFor', () => {
  it('is empty for the app served at the root, on any page', () => {
    for (const p of ['/', '/groups', '/config', '/diagnostics', '/groups/']) expect(basePathFor(p)).toBe('')
  })

  it('keeps the HA ingress prefix in front of a page', () => {
    const ingress = '/api/hassio_ingress/AbC123'
    expect(basePathFor(`${ingress}/`)).toBe(ingress)
    expect(basePathFor(`${ingress}/groups`)).toBe(ingress)
    expect(basePathFor(`${ingress}/config`)).toBe(ingress)
  })
})

describe('APP_SEGMENTS', () => {
  it('lists the first segment of every route', () => {
    const segments = router
      .getRoutes()
      .map((r) => r.path.split('/')[1])
      .filter((s): s is string => !!s)
    expect(segments.filter((s) => !(APP_SEGMENTS as readonly string[]).includes(s))).toEqual([])
  })
})

describe('useIngress', () => {
  it('returns strings', () => {
    const { basePath, apiBase } = useIngress()
    expect(typeof basePath).toBe('string')
    expect(typeof apiBase).toBe('string')
  })
})
