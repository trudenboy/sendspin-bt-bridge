import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createApp, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { handleShortcut, shortcutsOpen } from '@/composables/useKeyboardShortcuts'
import { useBluetoothStore } from '@/stores/bluetooth'

const push = vi.fn()
const router = { push } as never

function key(k: string, target: HTMLElement = document.body) {
  const e = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })
  Object.defineProperty(e, 'target', { value: target })
  return e
}

describe('handleShortcut', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    createApp({}).use(createPinia())
    shortcutsOpen.value = false
    document.body.innerHTML = ''
  })

  it('goes to a place by its number', () => {
    handleShortcut(key('2'), router)
    expect(push).toHaveBeenCalledWith('/groups')
  })

  it('opens adding a speaker', () => {
    handleShortcut(key('a'), router)
    expect(useBluetoothStore().scanRequested).toBe(true)
    expect(push).toHaveBeenCalledWith('/')
  })

  it('focuses the search on the page', async () => {
    document.body.innerHTML = '<input type="search" id="s" />'
    const e = key('/')
    handleShortcut(e, router)
    await nextTick()
    expect(document.activeElement?.id).toBe('s')
    expect(e.defaultPrevented).toBe(true)
  })

  it('shows the list of shortcuts', () => {
    handleShortcut(key('?'), router)
    expect(shortcutsOpen.value).toBe(true)
  })

  it('ignores keys typed into a field', () => {
    const input = document.createElement('input')
    handleShortcut(key('2', input), router)
    handleShortcut(key('a', input), router)
    expect(push).not.toHaveBeenCalled()
  })

  it('ignores keys with modifiers', () => {
    const e = new KeyboardEvent('keydown', { key: '1', ctrlKey: true })
    handleShortcut(e, router)
    expect(push).not.toHaveBeenCalled()
  })
})
