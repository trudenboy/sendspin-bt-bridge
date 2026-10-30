<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed, onMounted, provide, watch, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import RestartBanner from '@/components/layout/RestartBanner.vue'
import ConnectionBanner from '@/components/layout/ConnectionBanner.vue'
import MobileNav from '@/components/layout/MobileNav.vue'
import RuntimeRow from '@/components/layout/RuntimeRow.vue'
import UpdateDialog from '@/components/UpdateDialog.vue'
import ConfirmHost from '@/components/ConfirmHost.vue'
import ShortcutsDialog from '@/components/ShortcutsDialog.vue'
import { SbToastContainer } from '@/kit'
import { KIT_LABELS_KEY, kitLabelsFrom } from '@/kit/labels'
import { useConfigStore } from '@/stores/config'
import { setTechnicalDetailsDefault } from '@/composables/useTechnicalDetails'
import { useTheme } from '@/composables/useTheme'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useMaAutoConnect } from '@/composables/useMaAutoConnect'

useTheme()
useKeyboardShortcuts()
// In the Home Assistant add-on: sign in to Music Assistant on any page, and again when the bridge loses it.
useMaAutoConnect()

const route = useRoute()
const { t, locale } = useI18n()
const bridge = useBridgeStore()
provide(KIT_LABELS_KEY, computed(() => kitLabelsFrom(t)))

// Screen readers pick the voice from <html lang>; the tab title names the page and bridge.
watchEffect(() => {
  document.documentElement.lang = locale.value
  const page = route.meta.title ? t(route.meta.title) : ''
  const name = bridge.bridge?.name || t('app.title')
  document.title = page ? `${page} — ${name}` : name
})

const { embedded } = useTheme()
const hideNav = computed(() => route.meta.hideNav === true)

// The bridge's saved "technical details" default applies until this browser chooses.
const configStore = useConfigStore()
watch(
  () => configStore.originalConfig?.SHOW_TECHNICAL_DETAILS,
  (v) => setTechnicalDetailsDefault(v),
  { immediate: true },
)
onMounted(() => {
  if (!hideNav.value && !configStore.config) void configStore.fetchConfig()
})

</script>

<template>
  <div class="min-h-screen bg-surface">
    <AppHeader v-if="!hideNav" />
    <div :class="[hideNav ? '' : embedded ? 'pt-11' : 'pt-16']">
      <RuntimeRow v-if="!hideNav" class="hidden md:block" />
      <RestartBanner v-if="!hideNav" />
      <ConnectionBanner v-if="!hideNav" />
      <div class="flex">
        <AppSidebar v-if="!hideNav" class="sticky top-(--sb-header-h) hidden lg:flex" />
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
