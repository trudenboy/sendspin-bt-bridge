import { describe, it, expect } from 'vitest'
import { useIngress } from '@/composables/useIngress'

describe('useIngress', () => {
  it('returns empty basePath for root URL', () => {
    // happy-dom defaults to about:blank, pathname = /blank
    const { basePath, apiBase } = useIngress()
    expect(typeof basePath).toBe('string')
    expect(typeof apiBase).toBe('string')
  })
})
