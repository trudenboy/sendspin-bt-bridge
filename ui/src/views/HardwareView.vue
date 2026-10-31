<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useHardwareStore } from '@/stores/hardware'
import { SbBadge, SbButton, SbEmptyState, SbSpinner } from '@/kit'
import { RefreshCw } from 'lucide-vue-next'

const { t } = useI18n()
const store = useHardwareStore()

onMounted(() => store.connect())
onUnmounted(() => store.disconnect())
</script>

<template>
  <div>
    <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0">
        <h1 class="text-2xl font-semibold tracking-tight text-text-primary">{{ t('nav.hardware') }}</h1>
        <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.subtitle') }}</p>
      </div>
      <div class="flex items-center gap-2">
        <SbBadge v-if="store.snapshot?.stale" tone="warning" size="sm">{{ t('hardware.stale') }}</SbBadge>
        <SbButton size="sm" variant="outline" :loading="store.loading" @click="store.refresh()">
          <template #icon-left>
            <RefreshCw class="h-3.5 w-3.5" />
          </template>
          {{ t('hardware.refresh') }}
        </SbButton>
      </div>
    </div>

    <div
      v-if="store.error"
      role="alert"
      class="mb-4 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ store.error }}
    </div>

    <div
      v-if="store.snapshot && !store.snapshot.capabilities.bluetooth"
      role="status"
      class="mb-4 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ t('hardware.unavailable') }}
    </div>

    <div
      v-if="store.loading && !store.snapshot"
      class="flex items-center gap-2 text-sm text-text-secondary"
      aria-busy="true"
    >
      <SbSpinner />
      {{ t('common.loading') }}
    </div>

    <template v-else-if="store.snapshot">
      <section class="mb-8">
        <h2 class="mb-3 text-lg font-semibold text-text-primary">{{ t('hardware.controllers') }}</h2>
        <div
          v-if="store.snapshot.controllers.length"
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4"
        >
          <article
            v-for="controller in store.snapshot.controllers"
            :key="controller.id"
            class="rounded-(--radius-card) border border-border bg-surface-card p-4"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <p class="truncate font-medium text-text-primary">{{ controller.alias || controller.name }}</p>
                <p class="truncate font-mono text-xs text-text-secondary">{{ controller.mac }}</p>
              </div>
              <SbBadge size="sm" :tone="controller.powered ? 'success' : 'neutral'">
                {{ controller.powered ? t('hardware.powered') : t('hardware.poweredOff') }}
              </SbBadge>
            </div>
            <p class="mt-3 text-sm text-text-secondary">
              <span class="font-mono text-xs">{{ controller.id }}</span>
              · {{ t('hardware.deviceCount', { n: controller.device_count }) }}
            </p>
          </article>
        </div>
        <SbEmptyState v-else :title="t('hardware.noControllers')" />
      </section>

      <section>
        <h2 class="mb-3 text-lg font-semibold text-text-primary">{{ t('hardware.devices') }}</h2>
        <ul v-if="store.snapshot.bluetooth_devices.length" class="space-y-2">
          <li
            v-for="device in store.snapshot.bluetooth_devices"
            :key="device.mac"
            class="rounded-(--radius-card) border border-border bg-surface-card px-4 py-3"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <div class="min-w-0">
                <p class="truncate font-medium text-text-primary">{{ device.alias || device.name }}</p>
                <p class="font-mono text-xs text-text-secondary">{{ device.mac }}</p>
              </div>
              <div class="flex flex-wrap items-center gap-1.5">
                <SbBadge v-if="device.connected" size="sm" tone="success">{{ t('hardware.connected') }}</SbBadge>
                <SbBadge v-for="role in device.roles" :key="role.role" size="sm">
                  {{ role.role === 'input' ? t('hardware.roleInput') : t('hardware.roleOutput') }}
                </SbBadge>
                <SbBadge v-if="device.battery_pct !== null" size="sm">
                  {{ t('hardware.battery', { pct: device.battery_pct }) }}
                </SbBadge>
                <router-link
                  v-if="device.player_id"
                  to="/"
                  class="text-sm text-primary-text underline-offset-2 hover:underline"
                >
                  {{ t('hardware.player') }}
                </router-link>
              </div>
            </div>
          </li>
        </ul>
        <SbEmptyState v-else :title="t('hardware.noDevices')" />
      </section>
    </template>

    <SbEmptyState v-else-if="!store.loading && !store.error" :title="t('hardware.unavailable')" />
  </div>
</template>
