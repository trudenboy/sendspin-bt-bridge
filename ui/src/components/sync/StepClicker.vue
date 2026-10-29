<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Play } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { formatClock } from '@/sync/format'

/**
 * Preparation, step 1 (stopping the music and starting the clicks are one step): the click track
 * plays on the whole group through Music Assistant. When the group plays something else, the same
 * button stops it first (after a confirmation). Starting it again while it plays starts it over;
 * the next step opens once the bridge sees it playing.
 */
const props = defineProps<{
  /** The group's playback state as the bridge last read it (playing, idle, paused; null: unknown). */
  groupState?: string | null
  clickerPlaying: boolean
  /** The clicker was started in this session already (the button then says "again"). */
  started: boolean
  busy: boolean
  /** Why the last start failed, already translated. */
  error: string
  /** ``skip`` where Cancel only skips the preparation (the Manual tab) instead of ending the session. */
  cancelLabel?: 'cancel' | 'skip'
  /** Seconds of the click track left while it plays (the bridge's count). */
  leftS?: number | null
}>()
const emit = defineEmits<{ start: []; next: []; cancel: [] }>()

const { t } = useI18n()
/** Something else plays: starting the clicks stops it. */
const otherPlaying = computed(() => props.groupState === 'playing' && !props.clickerPlaying)
const stateLabel = computed(() => {
  const g = props.groupState
  return t(`sync.prep.group.${g === 'playing' || g === 'idle' || g === 'paused' ? g : 'unknown'}`)
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-secondary">{{ t(otherPlaying ? 'sync.prep.clicker.helpStop' : 'sync.prep.clicker.help') }}</p>
    <p v-if="groupState !== undefined" data-group-state role="status" aria-live="polite" class="flex items-center gap-2 text-sm text-text-primary">
      {{ t('sync.prep.clicker.groupState') }}
      <SbBadge :tone="otherPlaying ? 'warning' : 'success'" size="sm">{{ stateLabel }}</SbBadge>
    </p>
    <div class="flex flex-wrap items-center gap-3">
      <SbButton
        type="button"
        data-start-clicker
        :variant="clickerPlaying ? 'outline' : 'primary'"
        :loading="busy"
        @click="emit('start')"
      >
        <template #icon-left><Play aria-hidden="true" /></template>
        {{ otherPlaying ? t('sync.prep.clicker.stopAndStart') : started ? t('sync.prep.clicker.again') : t('sync.prep.clicker.start') }}
      </SbButton>
      <span data-clicker-state role="status" aria-live="polite">
        <SbBadge :tone="clickerPlaying ? 'success' : 'neutral'" size="sm">
          {{ clickerPlaying ? t('sync.prep.clicker.playing') : t('sync.prep.clicker.stopped') }}
        </SbBadge>
      </span>
      <SbBadge v-if="clickerPlaying && leftS != null" data-clicks-left size="sm" tone="neutral">
        {{ t('sync.prep.clicker.left', { time: formatClock(leftS) }) }}
      </SbBadge>
    </div>
    <p v-if="error" role="alert" class="text-sm text-error">{{ error }}</p>
    <div class="flex justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">
        {{ cancelLabel === 'skip' ? t('sync.prep.skip') : t('sync.wizard.cancel') }}
      </SbButton>
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
