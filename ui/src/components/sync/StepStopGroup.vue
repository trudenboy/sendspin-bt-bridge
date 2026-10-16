<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CircleStop } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'

/**
 * Preparation, step 1: nothing else may play while the speakers are measured. The group's state is
 * the bridge's live reading (``group_state``); the next step opens once the group no longer plays.
 */
const props = defineProps<{
  /** The group's playback state as the bridge last read it (playing, idle, paused; null: unknown). */
  groupState: string | null
  /** The group plays this session's clicker (the operator came back to this step). */
  clickerPlaying?: boolean
  busy: boolean
  /** Why the last stop failed, already translated. */
  error: string
  /** ``skip`` where Cancel only skips the preparation (the Manual tab) instead of ending the session. */
  cancelLabel?: 'cancel' | 'skip'
}>()
const emit = defineEmits<{ stop: []; next: []; cancel: [] }>()

const { t } = useI18n()
const playing = computed(() => props.groupState === 'playing' && !props.clickerPlaying)
const stateLabel = computed(() => {
  const s = props.groupState
  return t(`sync.prep.group.${s === 'playing' || s === 'idle' || s === 'paused' ? s : 'unknown'}`)
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-secondary">{{ t('sync.prep.stop.help') }}</p>
    <div class="flex flex-wrap items-center gap-3">
      <p data-group-state role="status" aria-live="polite" class="flex items-center gap-2 text-sm text-text-primary">
        {{ t('sync.prep.stop.state') }}
        <SbBadge :tone="playing ? 'warning' : 'success'" size="sm">{{ stateLabel }}</SbBadge>
      </p>
      <SbButton type="button" variant="outline" data-stop-group :loading="busy" :disabled="!playing" @click="emit('stop')">
        <template #icon-left><CircleStop aria-hidden="true" /></template>
        {{ t('sync.prep.stop.button') }}
      </SbButton>
    </div>
    <p v-if="playing" id="sync-stop-next-why" data-why class="text-sm text-text-secondary">{{ t('sync.prep.stop.stillPlaying') }}</p>
    <p v-if="error" role="alert" class="text-sm text-error">{{ error }}</p>
    <div class="flex justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">
        {{ cancelLabel === 'skip' ? t('sync.prep.skip') : t('sync.wizard.cancel') }}
      </SbButton>
      <SbButton
        type="button"
        data-next
        :disabled="playing || busy"
        :aria-describedby="playing ? 'sync-stop-next-why' : undefined"
        @click="emit('next')"
      >
        {{ t('sync.wizard.next') }}
      </SbButton>
    </div>
  </div>
</template>
