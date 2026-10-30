<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton, SbSlider } from '@/kit'
import type { SyncMember } from '@/api/types'
import { formatDelta } from '@/sync/format'
import { speakerName } from '@/sync/names'

const props = defineProps<{
  member: SyncMember
  modelValue: number
  /** The delay the measurement found for this member (semi-automatic): shown, with "Reset to measured". */
  measured?: number
  /** The delay before this session changed it (the "measured" change is shown against it). */
  before?: number
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [number] }>()

const { t } = useI18n()

const MIN = 0
const MAX = 5000
const NUDGES = [-10, -1, 1, 10] as const

/** Every way in (slider, buttons, number field) lands on a whole number of ms in 0–5000. */
function clamp(v: number) {
  if (!Number.isFinite(v)) return props.modelValue
  return Math.max(MIN, Math.min(MAX, Math.round(v)))
}

function set(v: number) {
  emit('update:modelValue', clamp(v))
}

function onNumber(e: Event) {
  const input = e.target as HTMLInputElement
  const next = clamp(Number(input.value))
  // Show the clamped value even when the model does not change (5001 → 5000 while already at 5000).
  input.value = String(next)
  emit('update:modelValue', next)
}

const name = computed(() => speakerName(props.member.name))
const nudgeLabel = (d: number) => t(d < 0 ? 'sync.manual.minus' : 'sync.manual.plus', { n: Math.abs(d) })
const inputId = `sync-delay-${props.member.ma_player_id}`
const hintId = `${inputId}-hint`
const measuredChange = computed(() =>
  props.measured == null || props.before == null ? null : formatDelta(props.measured - props.before),
)
</script>

<template>
  <div data-delay-row :data-member-id="member.ma_player_id" class="flex flex-col gap-2 py-3">
    <div class="flex items-center justify-between gap-3">
      <label :for="inputId" :title="member.name" class="min-w-0 flex-1 truncate font-medium text-text-primary">{{ name }}</label>
      <span class="flex items-center gap-1 text-sm text-text-secondary">
        <input
          :id="inputId"
          type="number"
          inputmode="numeric"
          :min="MIN"
          :max="MAX"
          step="1"
          :value="modelValue"
          :aria-label="t('sync.manual.valueLabel', { name })"
          :aria-describedby="hintId"
          class="h-8 w-20 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 text-right text-sm tabular-nums text-text-primary"
          :disabled="disabled"
          @change="onNumber"
        />
        <span aria-hidden="true">ms</span>
      </span>
    </div>
    <div v-if="measured != null" data-measured class="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
      <SbBadge size="sm" tone="info">
        {{ t('sync.manual.measured', { ms: measured, change: measuredChange ?? '' }) }}
      </SbBadge>
      <SbButton
        v-if="modelValue !== measured"
        type="button"
        size="sm"
        variant="ghost"
        :data-reset-measured="member.ma_player_id"
        :disabled="disabled"
        :aria-label="t('sync.manual.resetMeasuredMember', { name })"
        @click="set(measured)"
      >
        {{ t('sync.manual.resetMeasured') }}
      </SbButton>
    </div>
    <SbSlider
      :model-value="modelValue"
      :min="MIN"
      :max="MAX"
      :step="1"
      :show-value="false"
      :disabled="disabled"
      :aria-label="name"
      :format-value="(v: number) => t('sync.delay', { ms: v })"
      @update:model-value="set"
    />
    <div role="group" :aria-label="t('sync.manual.nudges', { name })" class="flex gap-1">
      <SbButton
        v-for="d in NUDGES"
        :key="d"
        type="button"
        size="sm"
        variant="secondary"
        class="min-w-12 flex-1 tabular-nums"
        :aria-label="nudgeLabel(d)"
        :disabled="disabled"
        @click="set(modelValue + d)"
      >
        {{ d < 0 ? '−' : '+' }}{{ Math.abs(d) }}
      </SbButton>
    </div>
    <p :id="hintId" data-sign-hint class="text-xs text-text-secondary">{{ t('sync.sign') }}</p>
  </div>
</template>
