<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  LayoutDashboard,
  Speaker,
  Settings,
  Activity,
  Music,
} from 'lucide-vue-next'
import { type Component } from 'vue'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()

interface NavTab {
  to: string
  label: string
  icon: Component
}

const tabs: NavTab[] = [
  { to: '/', label: 'nav.dashboard', icon: LayoutDashboard },
  { to: '/devices', label: 'nav.devices', icon: Speaker },
  { to: '/config', label: 'nav.config', icon: Settings },
  { to: '/diagnostics', label: 'nav.diagnostics', icon: Activity },
  { to: '/ma', label: 'nav.ma', icon: Music },
]

function isActive(to: string): boolean {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>

<template>
  <nav
    class="fixed right-0 bottom-0 left-0 z-40 border-t border-border bg-surface-card shadow-[0_-2px_8px_rgb(0_0_0/0.08)] pb-[env(safe-area-inset-bottom)]"
    data-testid="mobile-nav"
  >
    <div class="flex items-stretch justify-around">
      <router-link
        v-for="tab in tabs"
        :key="tab.to"
        :to="tab.to"
        :class="[
          'flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium transition-colors',
          isActive(tab.to)
            ? 'text-primary-text'
            : 'text-text-secondary',
        ]"
      >
        <component :is="tab.icon" class="h-5 w-5" />
        <span class="block max-w-full truncate">{{ t(tab.label) }}</span>
      </router-link>
    </div>
  </nav>
</template>
