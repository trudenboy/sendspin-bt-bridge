<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import DeviceCardStatus from './DeviceCardStatus.vue'
import type { DeviceCardView } from './types'

defineProps<{ view: DeviceCardView }>()
const { t } = useI18n()
</script>

<template>
  <div class="min-w-0 flex-1">
    <div class="flex flex-wrap items-center gap-2">
      <SbBadge size="sm">{{ t(`device.role.${view.role}`) }}</SbBadge>
      <DeviceCardStatus
        :label-key="view.status.labelKey"
        :params="view.status.params"
        :tone="view.status.tone"
        :dot="view.status.dot"
        :title="view.status.title"
      />
    </div>
    <h2 class="mt-1 break-words text-lg font-semibold text-text-primary">{{ view.identity.name }}</h2>
    <p class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-secondary">
      <span v-if="view.meta.room">{{ view.meta.room }}</span>
      <span v-if="view.meta.transport" class="font-mono">{{ view.meta.transport }}</span>
      <span v-if="view.identity.technicalId" class="font-mono">{{ view.identity.technicalId }}</span>
    </p>
  </div>
</template>
