<script setup lang="ts">
import { NAV, isActive as navActive } from '@/router/nav'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()

const tabs = NAV.map((n) => ({ to: n.to, label: n.short, icon: n.icon }))

function isActive(to: string) {
  return navActive(route.path, to)
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
