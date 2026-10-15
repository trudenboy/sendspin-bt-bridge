<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ArrowDown, ArrowUp, Check } from 'lucide-vue-next'
import { SbBadge, SbButton, SbSpinner } from '@/kit'
import type { SessionPoint, SyncMeasurement, SyncMember } from '@/api/types'

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
  /** Per-member failure that is not a measurement result (already translated). */
  errors: Record<string, string>
  /** Members that cannot be measured (not adjustable), by name. */
  notMeasured: string[]
  complete: boolean
}>()
const emit = defineEmits<{
  record: [member: string]
  skip: [member: string]
  move: [member: string, by: -1 | 1]
  next: []
  back: []
  cancel: []
}>()

const { t } = useI18n()

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

    <ol class="flex flex-col gap-3">
      <li
        v-for="(m, i) in members"
        :key="m.ma_player_id"
        :data-member-step="m.ma_player_id"
        class="flex flex-col gap-2 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3"
      >
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-sm tabular-nums text-text-secondary" aria-hidden="true">{{ i + 1 }}.</span>
          <span class="min-w-0 flex-1 font-medium text-text-primary">{{ m.name }}</span>
          <SbButton
            type="button"
            variant="ghost"
            size="sm"
            icon
            :data-move-up="m.ma_player_id"
            :aria-label="t('sync.wizard.members.moveUp', { name: m.name })"
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
            :aria-label="t('sync.wizard.members.moveDown', { name: m.name })"
            :disabled="i === members.length - 1 || !!active"
            @click="emit('move', m.ma_player_id, 1)"
          >
            <ArrowDown aria-hidden="true" />
          </SbButton>
        </div>

        <template v-if="isDeparted(m.ma_player_id)">
          <SbBadge tone="neutral" size="sm" class="self-start">{{ t('sync.wizard.departed') }}</SbBadge>
        </template>
        <template v-else>
          <p class="text-sm text-text-primary">
            {{ point === 'device' ? t('sync.wizard.holdNear', { name: m.name }) : t('sync.wizard.stayPut') }}
          </p>

          <div class="flex flex-wrap items-center gap-2">
            <p
              v-if="active?.member === m.ma_player_id"
              class="flex items-center gap-2 text-sm text-text-secondary"
            >
              <SbSpinner size="sm" />{{ t(`sync.wizard.${active.phase}`, { name: m.name }) }}
            </p>
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
                :aria-label="t('sync.wizard.recordMember', { name: m.name })"
                :disabled="!!active"
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
                :aria-label="t('sync.wizard.repeatMember', { name: m.name })"
                :disabled="!!active"
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
                :aria-label="t('sync.wizard.skipMember', { name: m.name })"
                :disabled="!!active"
                @click="emit('skip', m.ma_player_id)"
              >
                {{ t('sync.wizard.skip') }}
              </SbButton>
            </span>
          </div>
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
      <SbButton type="button" data-to-proposal :disabled="!complete || !!active" @click="emit('next')">
        {{ t('sync.wizard.toProposal') }}
      </SbButton>
    </div>
  </div>
</template>
