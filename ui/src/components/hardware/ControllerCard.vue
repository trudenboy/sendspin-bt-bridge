<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Power } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import type { ControllerSummary } from '@/api/types'

const props = withDefaults(
  defineProps<{
    controller: ControllerSummary
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{
  open: [id: string]
  togglePower: [controller: ControllerSummary]
}>()

const { t } = useI18n()
const displayName = computed(() => props.controller.alias || props.controller.name || props.controller.id)
const area = computed(() => props.controller.area?.name || props.controller.area?.id || null)
</script>

<template>
  <article data-controller-card class="relative flex flex-col gap-3 rounded-(--radius-card) border border-border bg-surface-card p-4 transition-colors hover:border-border-strong">
    <header class="flex items-start gap-2">
      <div class="min-w-0 flex-1">
        <h3 class="text-base font-medium leading-snug text-text-primary">
          <button
            type="button"
            data-controller-details
            class="block max-w-full text-left after:absolute after:inset-0 after:rounded-(--radius-card) after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-(--sb-focus-ring)"
            :aria-label="t('hardware.openDetails', { name: displayName })"
            @click="emit('open', controller.id)"
          >
            <span class="line-clamp-2 break-words">{{ displayName }}</span>
          </button>
        </h3>
        <p class="mt-0.5 flex flex-wrap items-center gap-x-2 font-mono text-xs text-text-secondary">
          <span>{{ controller.mac }}</span>
          <span aria-hidden="true">·</span>
          <span class="font-sans">{{ t('hardware.deviceCount', { n: controller.device_count }) }}</span>
        </p>
      </div>
      <SbBadge :tone="controller.powered ? 'success' : 'neutral'" size="sm">
        {{ controller.powered ? t('hardware.powered') : t('hardware.poweredOff') }}
      </SbBadge>
    </header>

    <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <dt class="text-text-secondary">{{ t('hardware.controllerId') }}</dt>
      <dd class="font-mono text-xs text-text-primary">{{ controller.id }}</dd>
      <template v-if="controller.class_of_device">
        <dt class="text-text-secondary">{{ t('hardware.classOfDevice') }}</dt>
        <dd class="font-mono text-xs text-text-primary">{{ controller.class_of_device }}</dd>
      </template>
      <template v-if="area">
        <dt class="text-text-secondary">{{ t('hardware.area') }}</dt>
        <dd class="text-text-primary">{{ area }}</dd>
      </template>
    </dl>

    <div class="relative z-10 mt-1">
      <SbButton
        variant="outline"
        size="sm"
        data-controller-power
        :loading="busy"
        @click="emit('togglePower', controller)"
      >
        <template #icon-left><Power class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ controller.powered ? t('adapter.turnOff') : t('adapter.turnOn') }}
      </SbButton>
    </div>
  </article>
</template>
