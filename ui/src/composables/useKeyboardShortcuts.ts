import { onMounted, onUnmounted } from 'vue'

export function useKeyboardShortcuts() {
  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      window.dispatchEvent(new CustomEvent('sb:escape'))
    }
  }

  onMounted(() => document.addEventListener('keydown', handleKeydown))
  onUnmounted(() => document.removeEventListener('keydown', handleKeydown))
}
