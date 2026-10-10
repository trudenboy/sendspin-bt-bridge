<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed, ref, watch, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import MobileNav from '@/components/layout/MobileNav.vue'
import UpdateDialog from '@/components/UpdateDialog.vue'
import ConfirmHost from '@/components/ConfirmHost.vue'
import { SbToastContainer } from '@/kit'
import { useTheme } from '@/composables/useTheme'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'

useTheme()
useKeyboardShortcuts()

const route = useRoute()
const { t, locale } = useI18n()
const bridge = useBridgeStore()

// Screen readers pick the voice from <html lang>; the tab title names the page and bridge.
watchEffect(() => {
  document.documentElement.lang = locale.value
  const page = route.meta.title ? t(route.meta.title) : ''
  const name = bridge.bridge?.name || t('app.title')
  document.title = page ? `${page} — ${name}` : name
})

const hideNav = computed(() => route.meta.hideNav === true)

const mobileSidebarOpen = ref(false)

function toggleMobileSidebar() {
  mobileSidebarOpen.value = !mobileSidebarOpen.value
}

// Close mobile sidebar on route change
watch(() => route.path, () => {
  mobileSidebarOpen.value = false
})
</script>

<template>
  <div class="min-h-screen bg-surface">
    <AppHeader v-if="!hideNav" @toggle-sidebar="toggleMobileSidebar" />
    <div :class="[hideNav ? '' : 'pt-16']">
      <RestartBanner v-if="!hideNav" />
      <div class="flex">
        <AppSidebar v-if="!hideNav" class="sticky top-16 hidden lg:flex" />
        <main class="min-h-screen min-w-0 flex-1 px-4 pt-6 pb-24 sm:px-6 lg:pb-10">
          <div class="mx-auto w-full max-w-7xl">
            <RouterView />
          </div>
        </main>
      </div>
    </div>

    <!-- Mobile sidebar overlay -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition-opacity duration-200 ease-out"
        leave-active-class="transition-opacity duration-150 ease-in"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-if="mobileSidebarOpen && !hideNav"
          class="fixed inset-0 z-40 bg-black/50 lg:hidden"
          @click="mobileSidebarOpen = false"
        />
      </Transition>
      <Transition
        enter-active-class="transition-transform duration-200 ease-out"
        leave-active-class="transition-transform duration-150 ease-in"
        enter-from-class="-translate-x-full"
        enter-to-class="translate-x-0"
        leave-from-class="translate-x-0"
        leave-to-class="-translate-x-full"
      >
        <div
          v-if="mobileSidebarOpen && !hideNav"
          class="fixed top-16 bottom-0 left-0 z-50 lg:hidden"
        >
          <AppSidebar class="flex h-full" />
        </div>
      </Transition>
    </Teleport>

    <MobileNav v-if="!hideNav" class="lg:hidden" />
    <UpdateDialog />
    <ConfirmHost />
    <SbToastContainer />
  </div>
</template>
