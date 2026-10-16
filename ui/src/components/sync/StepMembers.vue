<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDown, ArrowUp, Check } from 'lucide-vue-next'
import { SbBadge, SbButton, SbSpinner } from '@/kit'
import type { SessionPoint, SyncMeasurement, SyncMember } from '@/api/types'
import { speakerName } from '@/sync/names'
import { formatClock } from '@/sync/format'

export type MemberPhase = 'soloing' | 'recording'

const props = defineProps<{
  /** Measured members, in the order they are measured. */
  members: SyncMember[]
  point: SessionPoint
  results: Record<string, SyncMeasurement>
  /** Members whose result is from an older microphone clock and must be recorded again. */
  stale: string[]
  departed: string[]
  /** The member being soloed or recorded, and what is happening to it. */
  active: { member: string; phase: MemberPhase } | null
  /** Share of the running recording done, 0..1. */
  progress: number
  /** Per-member failure that is not a measurement result (already translated). */
  errors: Record<string, string>
  /** Members that cannot be measured (not adjustable), by name. */
  notMeasured: string[]
  complete: boolean
  /** The clicker plays on the group (recordings only then); when it stopped, it is offered again. */
  clickerPlaying?: boolean
  /** Seconds of the click track left while it plays. */
  clickerLeftS?: number | null
  /** Starting the clicker again is under way. */
  restarting?: boolean
}>()
const emit = defineEmits<{
  record: [member: string]
  skip: [member: string]
  move: [member: string, by: -1 | 1]
  next: []
  back: []
  cancel: []
  restart: []
}>()

const { t } = useI18n()

const ready = computed(() => props.clickerPlaying ?? true)
const nameOf = (m: SyncMember) => speakerName(m.name, m.ma_player_id)
/** Members still to record or skip before the next stage opens. */
const left = computed(
  () =>
    props.members.filter((m) => {
      const id = m.ma_player_id
      if (props.departed.includes(id)) return false
      const r = props.results[id]
      return !r || (r.status !== 'ok' && r.status !== 'skipped') || props.stale.includes(id)
    }).length,
)
/** What to do about a result that does not count (not heard, unstable). */
function hint(m: SyncMember, r: SyncMeasurement) {
  if (r.status === 'not_heard') return t('sync.wizard.hint.notHeard', { name: nameOf(m) })
  if (r.status === 'unstable') return t('sync.wizard.hint.unstable', { name: nameOf(m) })
  return ''
}
const isDeparted = (id: string) => props.departed.includes(id)
const isStale = (id: string) => props.stale.includes(id)
const pct = (r: SyncMeasurement) => Math.round((r.confidence ?? 0) * 100)
const tone = (status: SyncMeasurement['status']) =>
  status === 'ok' ? 'success' : status === 'unstable' ? 'warning' : status === 'skipped' ? 'neutral' : 'error'
const label = (status: SyncMeasurement['status']) =>
  t(`sync.wizard.${status === 'ok' ? 'heard' : status === 'not_heard' ? 'notHeard' : status}`)
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-secondary">{{ t('sync.wizard.members.help') }}</p>
    <p class="text-xs text-text-secondary">{{ t('sync.wizard.members.order') }}</p>
    <p v-if="ready && clickerLeftS != null" data-clicks-left class="text-xs text-text-secondary">
      {{ t('sync.prep.clicker.left', { time: formatClock(clickerLeftS) }) }}
    </p>
    <div
      v-if="!ready"
      data-clicker-stopped
      role="status"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.prep.clicker.stopped') }}</span>
      <SbButton
        type="button"
        size="sm"
        data-restart-clicker
        :loading="restarting"
        :disabled="!!active"
        @click="emit('restart')"
      >
        {{ t('sync.prep.clicker.again') }}
      </SbButton>
    </div>

    <ol class="flex flex-col gap-3">
      <li
        v-for="(m, i) in members"
        :key="m.ma_player_id"
        :data-member-step="m.ma_player_id"
        class="flex flex-col gap-2 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm tabular-nums text-text-secondary" aria-hidden="true">{{ i + 1 }}.</span>
          <span :title="m.name" class="min-w-0 flex-1 truncate font-medium text-text-primary">{{ nameOf(m) }}</span>
          <SbButton
            type="button"
            variant="ghost"
            size="sm"
            icon
            :data-move-up="m.ma_player_id"
            :aria-label="t('sync.wizard.members.moveUp', { name: nameOf(m) })"
            :disabled="i === 0 || !!active"
            @click="emit('move', m.ma_player_id, -1)"
          >
            <ArrowUp aria-hidden="true" />
          </SbButton>
          <SbButton
            type="button"
            variant="ghost"
            size="sm"
            icon
            :data-move-down="m.ma_player_id"
            :aria-label="t('sync.wizard.members.moveDown', { name: nameOf(m) })"
            :disabled="i === members.length - 1 || !!active"
            @click="emit('move', m.ma_player_id, 1)"
          >
            <ArrowDown aria-hidden="true" />
          </SbButton>
        </div>

        <template v-if="isDeparted(m.ma_player_id)">
          <SbBadge tone="neutral" size="sm" class="self-start">{{ t('sync.wizard.departed') }}</SbBadge>
          <p v-if="errors[m.ma_player_id]" class="text-sm text-text-secondary">{{ errors[m.ma_player_id] }}</p>
        </template>
        <template v-else>
          <p class="text-sm text-text-primary">
            {{ point === 'device' ? t('sync.wizard.holdNear', { name: nameOf(m) }) : t('sync.wizard.stayPut') }}
          </p>

          <div class="flex flex-wrap items-center gap-2">
            <div v-if="active?.member === m.ma_player_id" class="flex min-w-0 flex-1 flex-col gap-1">
              <p class="flex items-center gap-2 text-sm text-text-secondary">
                <SbSpinner size="sm" />{{ t(`sync.wizard.${active.phase}`, { name: nameOf(m) }) }}
              </p>
              <div
                v-if="active.phase === 'recording'"
                role="progressbar"
                :aria-label="t('sync.wizard.recording', { name: nameOf(m) })"
                aria-valuemin="0"
                aria-valuemax="100"
                :aria-valuenow="Math.round(progress * 100)"
                class="h-2 overflow-hidden rounded-full bg-surface-secondary"
              >
                <div class="h-full rounded-full bg-primary transition-[width] duration-100" :style="{ width: `${Math.round(progress * 100)}%` }" />
              </div>
            </div>
            <template v-else-if="results[m.ma_player_id]">
              <span :data-result="m.ma_player_id" class="flex flex-wrap items-center gap-2">
                <SbBadge :tone="tone(results[m.ma_player_id]!.status)" size="sm">
                  <Check v-if="results[m.ma_player_id]!.status === 'ok'" class="size-3" aria-hidden="true" />
                  {{ label(results[m.ma_player_id]!.status) }}
                  <template v-if="results[m.ma_player_id]!.status === 'ok'">
                    · {{ t('sync.wizard.confidence', { pct: pct(results[m.ma_player_id]!) }) }}
                  </template>
                </SbBadge>
                <SbBadge v-if="isStale(m.ma_player_id)" tone="warning" size="sm">{{ t('sync.wizard.redo') }}</SbBadge>
              </span>
            </template>

            <span class="ml-auto flex gap-2">
              <SbButton
                v-if="!results[m.ma_player_id] || results[m.ma_player_id]!.status === 'skipped'"
                type="button"
                size="sm"
                :data-record="m.ma_player_id"
                :aria-label="t('sync.wizard.recordMember', { name: nameOf(m) })"
                :disabled="!!active || !ready"
                @click="emit('record', m.ma_player_id)"
              >
                {{ t('sync.wizard.record') }}
              </SbButton>
              <SbButton
                v-else
                type="button"
                size="sm"
                :variant="results[m.ma_player_id]!.status === 'ok' && !isStale(m.ma_player_id) ? 'outline' : 'primary'"
                :data-repeat="m.ma_player_id"
                :aria-label="t('sync.wizard.repeatMember', { name: nameOf(m) })"
                :disabled="!!active || !ready"
                @click="emit('record', m.ma_player_id)"
              >
                {{ t('sync.wizard.repeat') }}
              </SbButton>
              <SbButton
                v-if="results[m.ma_player_id]?.status !== 'skipped'"
                type="button"
                size="sm"
                variant="ghost"
                :data-skip="m.ma_player_id"
                :aria-label="t('sync.wizard.skipMember', { name: nameOf(m) })"
                :disabled="!!active"
                @click="emit('skip', m.ma_player_id)"
              >
                {{ t('sync.wizard.skip') }}
              </SbButton>
            </span>
          </div>
          <p
            v-if="results[m.ma_player_id] && hint(m, results[m.ma_player_id]!) && active?.member !== m.ma_player_id"
            :data-hint="m.ma_player_id"
            class="text-sm text-text-secondary"
          >
            {{ hint(m, results[m.ma_player_id]!) }}
          </p>
          <p v-if="errors[m.ma_player_id]" role="alert" class="text-sm text-error">{{ errors[m.ma_player_id] }}</p>
        </template>
      </li>
    </ol>

    <p v-if="notMeasured.length" data-not-measured class="text-sm text-text-secondary">
      {{ t('sync.wizard.members.notMeasured', { names: notMeasured.join(', ') }) }}
    </p>

    <div class="flex flex-wrap justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel :disabled="!!active" @click="emit('cancel')">
        {{ t('sync.wizard.cancel') }}
      </SbButton>
      <SbButton type="button" variant="outline" data-back :disabled="!!active" @click="emit('back')">
        {{ t('sync.wizard.back') }}
      </SbButton>
      <SbButton
        type="button"
        data-to-tune
        :disabled="!complete || !!active"
        :aria-describedby="!complete ? 'sync-members-next-why' : undefined"
        @click="emit('next')"
      >
        {{ t('sync.wizard.toTune') }}
      </SbButton>
    </div>
    <p v-if="!complete" id="sync-members-next-why" data-why class="text-right text-xs text-text-secondary">
      {{ t('sync.wizard.members.left', { n: left }, left) }}
    </p>
  </div>
</template>
