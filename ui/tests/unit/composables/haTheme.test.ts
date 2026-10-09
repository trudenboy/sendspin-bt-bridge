import { describe, it, expect, afterEach } from 'vitest'
import { readHomeAssistantTheme, hostDocument } from '@/composables/haTheme'

function makeHost(vars: Record<string, string>) {
  const doc = document.implementation.createHTMLDocument('ha')
  for (const [k, v] of Object.entries(vars)) doc.documentElement.style.setProperty(k, v)
  return doc
}

describe('readHomeAssistantTheme', () => {
  it('reads dark mode and the accent from HA dark variables', () => {
    const host = makeHost({ '--primary-background-color': '#111111', '--primary-color': '#ff9800' })
    expect(readHomeAssistantTheme(host)).toEqual({ dark: true, primary: '#ff9800' })
  })

  it('reads light mode from HA light variables', () => {
    const host = makeHost({ '--primary-background-color': '#fafafa', '--primary-color': 'rgb(3, 169, 244)' })
    expect(readHomeAssistantTheme(host)).toEqual({ dark: false, primary: 'rgb(3, 169, 244)' })
  })

  it('returns null for a page that is not Home Assistant', () => {
    expect(readHomeAssistantTheme(makeHost({}))).toBeNull()
  })

  it('ignores a primary colour that is not a colour', () => {
    const host = makeHost({ '--primary-background-color': '#111', '--primary-color': 'url(javascript:alert(1))' })
    expect(readHomeAssistantTheme(host)).toEqual({ dark: true, primary: null })
  })
})

describe('hostDocument', () => {
  const realParent = Object.getOwnPropertyDescriptor(window, 'parent')
  afterEach(() => {
    if (realParent) Object.defineProperty(window, 'parent', realParent)
  })

  it('is null when the page is not framed', () => {
    expect(hostDocument()).toBeNull()
  })

  it('is null when the parent is another origin', () => {
    const crossOrigin = {
      get document() {
        throw new DOMException('Blocked a frame', 'SecurityError')
      },
    }
    Object.defineProperty(window, 'parent', { configurable: true, value: crossOrigin })
    expect(hostDocument()).toBeNull()
  })
})
