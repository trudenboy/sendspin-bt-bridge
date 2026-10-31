<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Mic } from 'lucide-vue-next'

/**
 * The phone-over-Bluetooth input when the bridge cannot capture it. It is a
 * row of its own, with the reason: a missing capture node must never look like
 * a host with no inputs.
 */
withDefaults(
  defineProps<{
    /** The server's reason, from the `audio/capture_unavailable` degraded entry. */
    detail?: string
    code?: string
  }>(),
  { detail: undefined, code: 'capture_unavailable' },
)

const { t } = useI18n()
</script>

<template>
  <div
    data-capture-unavailable
    role="status"
    class="flex items-start gap-3 rounded-(--radius-card) border border-warning/40 bg-warning/10 px-4 py-3"
  >
    <Mic class="mt-0.5 size-5 shrink-0 text-warning-text" aria-hidden="true" />
    <div class="min-w-0">
      <h3 class="text-sm font-medium text-text-primary">{{ t('hardware.audio.captureUnavailableTitle') }}</h3>
      <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.audio.captureUnavailableBody') }}</p>
      <p v-if="detail" class="mt-1 text-sm text-text-secondary">{{ detail }}</p>
      <p class="mt-1 font-mono text-xs text-text-secondary">
        {{ t('hardware.unavailable.reason', { reason: code }) }}
      </p>
    </div>
  </div>
</template>
