import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter, type Router } from 'vue-router'
import { NAV } from '@/router/nav'
import { useBluetoothStore } from '@/stores/bluetooth'

/** Whether the "?" list of shortcuts is open (ShortcutsDialog renders it). */
export const shortcutsOpen = ref(false)

export const SHORTCUTS = [
  { keys: ['1', '2', '3', '4'], action: 'places' },
  { keys: ['a'], action: 'add' },
  { keys: ['/'], action: 'search' },
  { keys: ['?'], action: 'help' },
  { keys: ['Esc'], action: 'close' },
] as const

function typing(target: EventTarget | null) {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable
}

/** One keydown, outside text fields and without modifiers. Exported for tests. */
export function handleShortcut(e: KeyboardEvent, router: Router) {
  if (e.key === 'Escape') {
    window.dispatchEvent(new CustomEvent('sb:escape'))
    return
  }
  if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return

  const place = Number(e.key)
  if (Number.isInteger(place) && place >= 1 && place <= NAV.length) {
    void router.push(NAV[place - 1]!.to)
  } else if (e.key === 'a') {
    useBluetoothStore().scanRequested = true
    void router.push('/')
  } else if (e.key === '/') {
    const search = document.querySelector<HTMLInputElement>('input[type="search"]')
    if (!search) return
    e.preventDefault()
    search.focus()
  } else if (e.key === '?') {
    shortcutsOpen.value = true
  }
}

export function useKeyboardShortcuts() {
  const router = useRouter()
  const onKeydown = (e: KeyboardEvent) => handleShortcut(e, router)
  onMounted(() => document.addEventListener('keydown', onKeydown))
  onUnmounted(() => document.removeEventListener('keydown', onKeydown))
}
