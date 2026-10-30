import { describe, it, expect, vi } from 'vitest'

describe('useTheme inside Home Assistant', () => {
  it('copies only the decorative accent; AA-checked tokens (focus ring, warning text) keep the bridge values', async () => {
    const doc = document.implementation.createHTMLDocument('ha')
    doc.documentElement.style.setProperty('--primary-background-color', '#111111')
    doc.documentElement.style.setProperty('--primary-color', '#ff9800')
    Object.defineProperty(window, 'parent', { configurable: true, value: { document: doc } })
    if (!window.matchMedia) {
      vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener: () => undefined }))
    }
    const { useTheme } = await import('@/composables/useTheme')
    useTheme()
    await Promise.resolve()
    const root = document.documentElement
    expect(root.style.getPropertyValue('--sb-primary')).toBe('#ff9800')
    expect(root.hasAttribute('data-embedded')).toBe(true)
    expect(root.classList.contains('dark')).toBe(true)
    for (const token of ['--sb-focus-ring', '--sb-warning-text', '--sb-primary-fill', '--sb-primary-text'])
      expect(root.style.getPropertyValue(token), token).toBe('')
  })
})
