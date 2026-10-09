<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed, ref, watch } from 'vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import MobileNav from '@/components/layout/MobileNav.vue'
import UpdateDialog from '@/components/UpdateDialog.vue'
import { SbToastContainer } from '@/kit'
import { useTheme } from '@/composables/useTheme'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'

useTheme()
useKeyboardShortcuts()

const route = useRoute()
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
        <main class="min-h-screen flex-1 pb-20 lg:pb-0">
          <RouterView />
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
    <SbToastContainer />
  </div>
</template>
