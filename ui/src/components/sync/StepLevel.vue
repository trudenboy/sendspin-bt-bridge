<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbButton } from '@/kit'
import LevelMeter from './LevelMeter.vue'

defineProps<{
  /** The microphone level now, RMS 0..1. */
  read: () => number
  /** The test signal is not playing yet (the session is still starting it). */
  starting: boolean
  disabled: boolean
}>()
const emit = defineEmits<{ next: []; cancel: [] }>()

const { t } = useI18n()
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-secondary">
      {{ starting ? t('sync.state.signal_starting') : t('sync.wizard.level.help') }}
    </p>
    <LevelMeter :read="read" />
    <div class="flex justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">{{ t('sync.wizard.cancel') }}</SbButton>
      <SbButton type="button" data-next :disabled="disabled" @click="emit('next')">{{ t('sync.wizard.next') }}</SbButton>
    </div>
  </div>
</template>
