import { ref, watchEffect } from 'vue'
import { hostDocument, readHomeAssistantTheme, type HomeAssistantTheme } from './haTheme'

type ThemeMode = 'light' | 'dark' | 'auto'

const STORAGE_KEY = 'sendspin-ui:theme-mode'

function storedMode(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'light' || v === 'dark' || v === 'auto' ? v : 'auto'
  } catch {
    return 'auto'
  }
}

const mode = ref<ThemeMode>(storedMode())
const resolved = ref<'light' | 'dark'>('light')
/** Home Assistant's theme when the page runs in its ingress panel. */
const homeAssistant = ref<HomeAssistantTheme | null>(null)

function applyTheme() {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  // "Auto" follows Home Assistant inside its panel, the browser elsewhere.
  const autoDark = homeAssistant.value ? homeAssistant.value.dark : prefersDark
  const isDark = mode.value === 'dark' || (mode.value === 'auto' && autoDark)
  resolved.value = isDark ? 'dark' : 'light'

  const root = document.documentElement
  root.classList.toggle('dark', isDark)
  const accent = homeAssistant.value?.primary
  if (accent) root.style.setProperty('--sb-primary', accent)
  else root.style.removeProperty('--sb-primary')
  root.toggleAttribute('data-embedded', homeAssistant.value !== null)

  try {
    localStorage.setItem(STORAGE_KEY, mode.value)
  } catch {
    /* private mode: the choice lasts for this visit */
  }
}

function toggleTheme() {
  const order: ThemeMode[] = ['light', 'dark', 'auto']
  const idx = order.indexOf(mode.value)
  mode.value = order[(idx + 1) % order.length]!
}

function followHomeAssistant() {
  const host = hostDocument()
  if (!host) return
  const refresh = () => {
    homeAssistant.value = readHomeAssistantTheme(host)
  }
  refresh()
  // HA rewrites its root variables when the user switches theme or dark mode.
  new MutationObserver(refresh).observe(host.documentElement, {
    attributes: true,
    attributeFilter: ['style', 'class'],
  })
  host.defaultView
    ?.matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', () => setTimeout(refresh, 50))
}

let initialized = false

export function useTheme() {
  if (!initialized) {
    initialized = true
    followHomeAssistant()
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme)
    watchEffect(applyTheme)
  }

  return { mode, resolved, homeAssistant, toggleTheme }
}
