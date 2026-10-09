import { describe, it, expect, vi } from 'vitest'
import { openExternal, safeHttpUrl } from '@/utils/safeUrl'

describe('safeHttpUrl', () => {
  it('keeps http and https addresses', () => {
    expect(safeHttpUrl('https://github.com/x')).toBe('https://github.com/x')
    expect(safeHttpUrl('http://192.168.10.10:8095')).toBe('http://192.168.10.10:8095/')
  })

  it('refuses other schemes and malformed values', () => {
    for (const bad of ['javascript:alert(1)', 'data:text/html,hi', 'file:///etc/passwd', 'not a url', '', null, undefined]) {
      expect(safeHttpUrl(bad as string)).toBeNull()
    }
  })
})

describe('openExternal', () => {
  it('opens only safe addresses, without an opener', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    openExternal('javascript:alert(1)')
    expect(open).not.toHaveBeenCalled()
    openExternal('https://example.com')
    expect(open).toHaveBeenCalledWith('https://example.com/', '_blank', 'noopener,noreferrer')
    open.mockRestore()
  })
})
