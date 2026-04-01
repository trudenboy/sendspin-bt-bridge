<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import {
  LayoutDashboard,
  Speaker,
  Settings,
  Activity,
  Music,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-vue-next'
import { ref, type Component } from 'vue'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const bridge = useBridgeStore()
const route = useRoute()

const STORAGE_KEY = 'sendspin-ui:sidebar-collapsed'

const collapsed = ref(localStorage.getItem(STORAGE_KEY) === 'true')

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEY, String(collapsed.value))
}

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

function isActive(to: string): boolean {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>

<template>
  <aside
    :class="[
      'flex h-[calc(100vh-4rem)] flex-col border-r border-surface-secondary bg-surface-card transition-[width] duration-200 dark:bg-gray-900',
      collapsed ? 'w-16' : 'w-60',
    ]"
    data-testid="app-sidebar"
  >
    <!-- Navigation -->
    <nav class="flex-1 space-y-1 px-2 py-4">
      <router-link
        v-for="link in navLinks"
        :key="link.to"
        :to="link.to"
        :class="[
          'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
          isActive(link.to)
            ? 'bg-primary/10 text-primary'
            : 'text-text-secondary hover:bg-surface-secondary hover:text-text-primary',
          collapsed ? 'justify-center' : '',
        ]"
        :title="collapsed ? t(link.label) : undefined"
      >
        <component :is="link.icon" class="h-5 w-5 shrink-0" />
        <span v-if="!collapsed" class="truncate">{{ t(link.label) }}</span>
      </router-link>
    </nav>

    <!-- Footer -->
    <div class="border-t border-surface-secondary px-2 py-3">
      <!-- Version info -->
      <div v-if="!collapsed && bridge.version" class="mb-2 px-3">
        <span class="text-xs text-text-disabled">
          v{{ bridge.version }}
        </span>
      </div>

      <!-- Collapse toggle -->
      <button
        class="flex w-full items-center justify-center rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary"
        :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        data-testid="sidebar-toggle"
        @click="toggleCollapsed"
      >
        <ChevronsRight v-if="collapsed" class="h-4 w-4" />
        <ChevronsLeft v-else class="h-4 w-4" />
      </button>
    </div>
  </aside>
</template>
