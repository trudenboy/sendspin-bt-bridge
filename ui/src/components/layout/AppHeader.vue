<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import { Sun, Moon, Monitor, Menu } from 'lucide-vue-next'
import { ref } from 'vue'

const { t } = useI18n()
const { mode, toggleTheme } = useTheme()
const bridge = useBridgeStore()
const mobileMenuOpen = ref(false)

const navLinks = [
  { to: '/', label: 'app.dashboard' },
  { to: '/devices', label: 'app.devices' },
  { to: '/config', label: 'app.config' },
  { to: '/diagnostics', label: 'app.diagnostics' },
  { to: '/ma', label: 'app.ma' },
] as const

const themeIcon = {
  light: Sun,
  dark: Moon,
  auto: Monitor,
}
</script>

<template>
  <header
    class="sticky top-0 z-40 border-b border-surface-secondary bg-surface-card shadow-sm"
  >
    <div class="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
      <!-- Logo + Title -->
      <router-link to="/" class="flex shrink-0 items-center gap-2">
        <img
          src="/bridge-logo.svg"
          alt="Sendspin Bridge"
          class="h-8 w-8"
          width="32"
          height="32"
        />
        <span class="text-lg font-semibold text-text-primary">
          {{ t('app.title') }}
        </span>
      </router-link>

      <span
        v-if="bridge.version"
        class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary"
      >
        v{{ bridge.version }}
      </span>

      <!-- Desktop nav -->
      <nav class="ml-auto hidden items-center gap-1 md:flex">
        <router-link
          v-for="link in navLinks"
          :key="link.to"
          :to="link.to"
          class="rounded-lg px-3 py-2 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
          active-class="!bg-primary/10 !text-primary"
        >
          {{ t(link.label) }}
        </router-link>
      </nav>

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
        @click="mobileMenuOpen = !mobileMenuOpen"
      >
        <Menu class="h-5 w-5" />
      </button>
    </div>

    <!-- Mobile nav -->
    <nav
      v-if="mobileMenuOpen"
      class="border-t border-surface-secondary px-4 pb-3 pt-2 md:hidden"
    >
      <router-link
        v-for="link in navLinks"
        :key="link.to"
        :to="link.to"
        class="block rounded-lg px-3 py-2 text-sm font-medium text-text-secondary"
        active-class="!bg-primary/10 !text-primary"
        @click="mobileMenuOpen = false"
      >
        {{ t(link.label) }}
      </router-link>
    </nav>
  </header>
</template>
