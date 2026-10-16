<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircle2 } from 'lucide-vue-next'
import { SbButton, SbSpinner } from '@/kit'
import type { SessionStateName } from '@/api/types'
import { formatMs } from '@/sync/format'
import { ENDED } from '@/stores/sync'
import { useCountdown } from './useCountdown'

/**
 * Step 5: the group is being restored, then restored. Shows the spread before → after, what
 * happened to the delays (undone, saved as a profile), and offers a quick listen on the whole group.
 */
const props = defineProps<{
  /** The measuring session's last known state (``restoring`` until the bridge reports ``done``). */
  state: SessionStateName | null
  before: number | null
  after: number | null
  /** ``after`` is the planner's prediction (no check round measured it). */
  predicted: boolean
  undone: boolean
  saved: string | null
  saveError: string
  listening: boolean
  listenError: string
  /** Seconds until the test clip ends and Music Assistant brings the music back (the bridge's count). */
  musicReturnsIn?: number | null
}>()
const emit = defineEmits<{ listen: []; again: [] }>()

const { t } = useI18n()

/* Every end goes through a successful restore; the outcome itself (done, cancelled) is not shown here. */
const restored = computed(() => !!props.state && ENDED.has(props.state))
const musicLeft = useCountdown(() => (restored.value ? null : props.musicReturnsIn))
</script>

<template>
  <div data-done class="flex flex-col gap-4">
    <div class="flex items-start gap-3 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3">
      <CheckCircle2 v-if="restored" class="mt-0.5 size-5 shrink-0 text-success" aria-hidden="true" />
      <SbSpinner v-else size="sm" class="mt-1" />
      <div class="flex flex-col gap-1 text-sm">
        <p class="font-medium text-text-primary">{{ restored ? t('sync.done.restored') : t('sync.done.restoring') }}</p>
        <p class="text-text-secondary">{{ restored ? t('sync.done.restoredHelp') : t('sync.done.restoringHelp') }}</p>
        <p v-if="!restored && musicLeft != null" data-music-returns role="status" class="text-text-primary">
          {{ musicLeft > 0 ? t('sync.signal.musicReturns', { s: musicLeft }) : t('sync.signal.musicReturnsSoon') }}
        </p>
      </div>
    </div>

    <p v-if="undone" data-undone class="text-sm text-text-primary">{{ t('sync.done.undone') }}</p>
    <p v-else-if="before != null && after != null" data-spread class="text-sm text-text-primary">
      {{ t(predicted ? 'sync.done.spreadPredicted' : 'sync.done.spread', { before: formatMs(before), after: formatMs(after) }) }}
    </p>
    <p v-if="saved" data-saved role="status" class="text-sm text-text-primary">{{ t('sync.done.saved', { name: saved }) }}</p>
    <p v-if="saveError" role="alert" class="text-sm text-error">{{ saveError }}</p>

    <div class="flex flex-col gap-1">
      <div class="flex flex-wrap items-center gap-3">
        <SbButton
          type="button"
          variant="outline"
          data-listen
          :disabled="!restored || listening"
          aria-describedby="sync-done-listen-help"
          @click="emit('listen')"
        >
          {{ t('sync.done.listen') }}
        </SbButton>
        <span v-if="listening" role="status" class="flex items-center gap-2 text-sm text-text-secondary">
          <SbSpinner size="sm" />{{ t('sync.done.listening') }}
        </span>
      </div>
      <p id="sync-done-listen-help" class="text-xs text-text-secondary">{{ t('sync.done.listenHelp') }}</p>
      <p v-if="listenError" role="alert" class="text-sm text-error">{{ listenError }}</p>
    </div>

    <div class="flex justify-end">
      <SbButton type="button" data-again :disabled="listening" @click="emit('again')">{{ t('sync.done.again') }}</SbButton>
    </div>
  </div>
</template>
