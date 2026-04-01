<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import {
  LayoutDashboard,
  Speaker,
  Settings,
  Activity,
  Music,
  Sun,
  Moon,
  Monitor,
  Menu,
  X,
  Globe,
} from 'lucide-vue-next'
import { ref, type Component } from 'vue'
import { useRoute } from 'vue-router'

const { t, locale } = useI18n()
const { mode, toggleTheme } = useTheme()
const bridge = useBridgeStore()
const route = useRoute()
const mobileMenuOpen = ref(false)

interface NavLink {
  to: string
  label: string
  icon: Component
}

const navLinks: NavLink[] = [
  { to: '/', label: 'app.dashboard', icon: LayoutDashboard },
  { to: '/devices', label: 'app.devices', icon: Speaker },
  { to: '/config', label: 'app.config', icon: Settings },
  { to: '/diagnostics', label: 'app.diagnostics', icon: Activity },
  { to: '/ma', label: 'app.ma', icon: Music },
]

const themeIcon = {
  light: Sun,
  dark: Moon,
  auto: Monitor,
} as const

function isActive(to: string): boolean {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}

function toggleLocale() {
  const next = locale.value === 'en' ? 'ru' : 'en'
  locale.value = next
  localStorage.setItem('sendspin-ui:locale', next)
}
</script>

<template>
  <header
    class="fixed top-0 right-0 left-0 z-30 border-b border-surface-secondary bg-surface-card shadow-sm"
  >
    <div class="flex h-16 items-center gap-4 px-4 sm:px-6">
      <!-- Logo + Title -->
      <router-link to="/" class="flex shrink-0 items-center gap-2">
        <img
          src="/bridge-logo.svg"
          alt="Sendspin Bridge"
          class="h-8 w-8"
          width="32"
          height="32"
        />
        <span class="hidden text-lg font-semibold text-text-primary sm:inline">
          {{ t('app.title') }}
        </span>
      </router-link>

      <span
        v-if="bridge.version"
        class="hidden rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary sm:inline-flex"
      >
        v{{ bridge.version }}
      </span>

      <!-- Desktop nav -->
      <nav class="ml-4 hidden items-center gap-1 md:flex">
        <router-link
          v-for="link in navLinks"
          :key="link.to"
          :to="link.to"
          :class="[
            'flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors',
            isActive(link.to)
              ? 'border-primary font-medium text-primary'
              : 'border-transparent text-text-secondary hover:border-surface-secondary hover:text-text-primary',
          ]"
        >
          <component :is="link.icon" class="h-4 w-4" />
          {{ t(link.label) }}
        </router-link>
      </nav>

      <div class="ml-auto flex items-center gap-1">
        <!-- Language toggle -->
        <button
          class="flex items-center gap-1 rounded-lg px-2 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-secondary"
          :title="locale === 'en' ? 'Русский' : 'English'"
          @click="toggleLocale"
        >
          <Globe class="h-4 w-4" />
          <span class="hidden text-xs font-medium uppercase sm:inline">{{ locale }}</span>
        </button>

        <!-- Theme toggle -->
        <button
          class="rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary"
          :title="`Theme: ${mode}`"
          @click="toggleTheme"
        >
          <component :is="themeIcon[mode]" class="h-5 w-5" />
        </button>

        <!-- Mobile menu button -->
        <button
          class="rounded-lg p-2 text-text-secondary md:hidden"
          aria-label="Toggle menu"
          @click="mobileMenuOpen = !mobileMenuOpen"
        >
          <X v-if="mobileMenuOpen" class="h-5 w-5" />
          <Menu v-else class="h-5 w-5" />
        </button>
      </div>
    </div>

    <!-- Mobile dropdown nav -->
    <Transition
      enter-active-class="transition-all duration-200 ease-out"
      leave-active-class="transition-all duration-150 ease-in"
      enter-from-class="max-h-0 opacity-0"
      enter-to-class="max-h-80 opacity-100"
      leave-from-class="max-h-80 opacity-100"
      leave-to-class="max-h-0 opacity-0"
    >
      <nav
        v-if="mobileMenuOpen"
        class="overflow-hidden border-t border-surface-secondary px-4 pb-3 pt-2 md:hidden"
      >
        <router-link
          v-for="link in navLinks"
          :key="link.to"
          :to="link.to"
          :class="[
            'flex items-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
            isActive(link.to)
              ? 'bg-primary/10 text-primary'
              : 'text-text-secondary hover:bg-surface-secondary',
          ]"
          @click="mobileMenuOpen = false"
        >
          <component :is="link.icon" class="h-4 w-4" />
          {{ t(link.label) }}
        </router-link>
      </nav>
    </Transition>
  </header>
</template>
