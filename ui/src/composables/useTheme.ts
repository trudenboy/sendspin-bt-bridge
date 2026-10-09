import { ref, watchEffect } from 'vue'

type ThemeMode = 'light' | 'dark' | 'auto'

const STORAGE_KEY = 'sendspin-ui:theme-mode'

const mode = ref<ThemeMode>(
  (localStorage.getItem(STORAGE_KEY) as ThemeMode) || 'auto',
)

const resolved = ref<'light' | 'dark'>('light')

function applyTheme() {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  const isDark = mode.value === 'dark' || (mode.value === 'auto' && prefersDark)
  resolved.value = isDark ? 'dark' : 'light'

  document.documentElement.classList.toggle('dark', isDark)
  localStorage.setItem(STORAGE_KEY, mode.value)
}

function toggleTheme() {
  const order: ThemeMode[] = ['light', 'dark', 'auto']
  const idx = order.indexOf(mode.value)
  mode.value = order[(idx + 1) % order.length]
}

let initialized = false

export function useTheme() {
  if (!initialized) {
    initialized = true
    applyTheme()

    window
      .matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', applyTheme)

    // Listen for HA Ingress theme injection
    window.addEventListener('message', (e) => {
      if (e.data?.type === 'theme-update' && e.data.dark !== undefined) {
        mode.value = e.data.dark ? 'dark' : 'light'
      }
    })

    watchEffect(applyTheme)
  }

  return { mode, resolved, toggleTheme }
}
