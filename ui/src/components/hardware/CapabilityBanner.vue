<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    /** Stable reason code: `dbus_unavailable`, `bluetooth_unavailable`, `hardware_unavailable`. */
    reason: string
    detail?: string
  }>(),
  { detail: undefined },
)

const { t } = useI18n()

const title = computed(() => {
  switch (props.reason) {
    case 'dbus_unavailable':
      return t('hardware.unavailable.dbusTitle')
    case 'bluetooth_unavailable':
      return t('hardware.unavailable.bluetoothTitle')
    default:
      return t('hardware.unavailable.hardwareTitle')
  }
})

const body = computed(() => {
  switch (props.reason) {
    case 'dbus_unavailable':
      return t('hardware.unavailable.dbusBody')
    case 'bluetooth_unavailable':
      return t('hardware.unavailable.bluetoothBody')
    default:
      return t('hardware.unavailable.hardwareBody')
  }
})
</script>

<template>
  <section data-capability-banner role="status" class="mb-6 flex items-start gap-3 rounded-(--radius-card) border border-warning/40 bg-warning/10 px-4 py-3">
    <AlertTriangle class="mt-0.5 size-5 shrink-0 text-warning-text" aria-hidden="true" />
    <div class="min-w-0">
      <h2 class="text-sm font-medium text-text-primary">{{ title }}</h2>
      <p class="mt-1 text-sm text-text-secondary">{{ body }}</p>
      <p v-if="detail" class="mt-1 text-sm text-text-secondary">{{ detail }}</p>
      <p class="mt-1 font-mono text-xs text-text-secondary">
        {{ t('hardware.unavailable.reason', { reason }) }}
      </p>
      <p class="mt-2 text-sm text-text-primary">{{ t('hardware.unavailable.nextStep') }}</p>
    </div>
  </section>
</template>
