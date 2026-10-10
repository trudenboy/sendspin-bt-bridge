<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import ConnectionBanner from '@/components/layout/ConnectionBanner.vue'
import MobileNav from '@/components/layout/MobileNav.vue'
import UpdateDialog from '@/components/UpdateDialog.vue'
import ConfirmHost from '@/components/ConfirmHost.vue'
import ShortcutsDialog from '@/components/ShortcutsDialog.vue'
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

</script>

<template>
  <div class="min-h-screen bg-surface">
    <AppHeader v-if="!hideNav" />
    <div :class="[hideNav ? '' : 'pt-16']">
      <RestartBanner v-if="!hideNav" />
      <ConnectionBanner v-if="!hideNav" />
      <div class="flex">
        <AppSidebar v-if="!hideNav" class="sticky top-16 hidden lg:flex" />
        <main class="min-h-screen min-w-0 flex-1 px-4 pt-6 pb-24 sm:px-6 lg:pb-10">
          <div class="mx-auto w-full max-w-7xl">
            <RouterView />
          </div>
        </main>
      </div>
    </div>

    <MobileNav v-if="!hideNav" class="lg:hidden" />
    <UpdateDialog />
    <ConfirmHost />
    <ShortcutsDialog />
    <SbToastContainer />
  </div>
</template>
