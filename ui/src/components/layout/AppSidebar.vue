<script setup lang="ts">
import { NAV, isActive as navActive } from '@/router/nav'
import { useI18n } from 'vue-i18n'
import { ChevronsLeft, ChevronsRight } from 'lucide-vue-next'
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { useTheme } from '@/composables/useTheme'

const { t } = useI18n()
const route = useRoute()

const STORAGE_KEY = 'sendspin-ui:sidebar-collapsed'

const { embedded } = useTheme()
// Inside Home Assistant's panel the sidebar starts as icons only; a choice made here is kept.
const stored = localStorage.getItem(STORAGE_KEY)
const collapsed = ref(stored === null ? embedded.value : stored === 'true')

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEY, String(collapsed.value))
}

const navLinks = NAV
</script>

<template>
  <aside
    :class="[
      'flex h-[calc(100vh-var(--sb-header-h))] flex-col border-r border-border bg-surface-card transition-[width] duration-200',
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
          navActive(route.path, link.to)
            ? 'bg-primary/10 text-primary-text'
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

      <!-- Collapse toggle -->
      <button
        class="flex w-full items-center justify-center rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary"
        :title="collapsed ? t('sidebar.expand') : t('sidebar.collapse')"
        :aria-label="collapsed ? t('sidebar.expand') : t('sidebar.collapse')"
        :aria-expanded="!collapsed"
        data-testid="sidebar-toggle"
        @click="toggleCollapsed"
      >
        <ChevronsRight v-if="collapsed" class="h-4 w-4" />
        <ChevronsLeft v-else class="h-4 w-4" />
      </button>
    </div>
  </aside>
</template>
