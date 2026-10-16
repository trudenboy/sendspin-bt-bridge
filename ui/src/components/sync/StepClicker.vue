<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Play } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'

/**
 * Preparation, step 2: the click track plays on the whole group through Music Assistant. Starting it
 * again while it plays starts it over; the next step opens once the bridge sees it playing.
 */
defineProps<{
  clickerPlaying: boolean
  /** The clicker was started in this session already (the button then says "again"). */
  started: boolean
  busy: boolean
  /** Why the last start failed, already translated. */
  error: string
  /** ``skip`` where Cancel only skips the preparation (the Manual tab) instead of ending the session. */
  cancelLabel?: 'cancel' | 'skip'
  /** A line about the step before (e.g. "The group is not playing — moving on."), already translated. */
  note?: string
}>()
const emit = defineEmits<{ start: []; next: []; back: []; cancel: [] }>()

const { t } = useI18n()
</script>

<template>
  <div class="flex flex-col gap-4">
    <p v-if="note" data-auto-note role="status" class="text-sm text-text-primary">{{ note }}</p>
    <p class="text-sm text-text-secondary">{{ t('sync.prep.clicker.help') }}</p>
    <div class="flex flex-wrap items-center gap-3">
      <SbButton
        type="button"
        data-start-clicker
        :variant="clickerPlaying ? 'outline' : 'primary'"
        :loading="busy"
        @click="emit('start')"
      >
        <template #icon-left><Play aria-hidden="true" /></template>
        {{ started ? t('sync.prep.clicker.again') : t('sync.prep.clicker.start') }}
      </SbButton>
      <span data-clicker-state role="status" aria-live="polite">
        <SbBadge :tone="clickerPlaying ? 'success' : 'neutral'" size="sm">
          {{ clickerPlaying ? t('sync.prep.clicker.playing') : t('sync.prep.clicker.stopped') }}
        </SbBadge>
      </span>
    </div>
    <p v-if="error" role="alert" class="text-sm text-error">{{ error }}</p>
    <div class="flex justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">
        {{ cancelLabel === 'skip' ? t('sync.prep.skip') : t('sync.wizard.cancel') }}
      </SbButton>
      <SbButton type="button" variant="outline" data-back @click="emit('back')">{{ t('sync.wizard.back') }}</SbButton>
      <SbButton
        type="button"
        data-next
        :disabled="!clickerPlaying || busy"
        :aria-describedby="!clickerPlaying ? 'sync-clicker-next-why' : undefined"
        @click="emit('next')"
      >
        {{ t('sync.wizard.next') }}
      </SbButton>
    </div>
    <p v-if="!clickerPlaying" id="sync-clicker-next-why" data-why class="text-right text-xs text-text-secondary">
      {{ t('sync.prep.clicker.whyDisabled') }}
    </p>
  </div>
</template>
