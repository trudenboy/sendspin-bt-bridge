<script setup lang="ts">
import { RouterView, useRoute } from 'vue-router'
import { computed } from 'vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import MobileNav from '@/components/layout/MobileNav.vue'
import { SbToastContainer } from '@/kit'
import { useTheme } from '@/composables/useTheme'

useTheme()

const route = useRoute()
const hideNav = computed(() => route.meta.hideNav === true)
</script>

<template>
  <div class="min-h-screen bg-surface">
    <AppHeader v-if="!hideNav" />
    <div :class="['flex', hideNav ? '' : 'pt-16']">
      <AppSidebar v-if="!hideNav" class="sticky top-16 hidden lg:flex" />
      <main class="min-h-screen flex-1 pb-20 lg:pb-0">
        <RouterView />
      </main>
    </div>
    <MobileNav v-if="!hideNav" class="lg:hidden" />
    <SbToastContainer />
  </div>
</template>
