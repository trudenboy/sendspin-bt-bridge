<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton, SbSlider } from '@/kit'
import type { SyncMember } from '@/api/types'

const props = defineProps<{ member: SyncMember; modelValue: number }>()
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

const nudgeLabel = (d: number) => t(d < 0 ? 'sync.manual.minus' : 'sync.manual.plus', { n: Math.abs(d) })
const inputId = `sync-delay-${props.member.ma_player_id}`
</script>

<template>
  <div data-delay-row :data-member-id="member.ma_player_id" class="flex flex-col gap-2 py-3">
    <div class="flex items-center justify-between gap-3">
      <label :for="inputId" class="min-w-0 flex-1 truncate font-medium text-text-primary">{{ member.name }}</label>
      <SbBadge size="sm">{{ t(`sync.origin.${member.origin}`) }}</SbBadge>
      <span class="flex items-center gap-1 text-sm text-text-secondary">
        <input
          :id="inputId"
          type="number"
          inputmode="numeric"
          :min="MIN"
          :max="MAX"
          step="1"
          :value="modelValue"
          :aria-label="t('sync.manual.valueLabel', { name: member.name })"
          class="h-8 w-20 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 text-right text-sm tabular-nums text-text-primary outline-none focus:ring-2 focus:ring-primary/40"
          @change="onNumber"
        />
        <span aria-hidden="true">ms</span>
      </span>
    </div>
    <SbSlider
      :model-value="modelValue"
      :min="MIN"
      :max="MAX"
      :step="1"
      :show-value="false"
      :aria-label="member.name"
      :format-value="(v: number) => t('sync.delay', { ms: v })"
      @update:model-value="set"
    />
    <div role="group" :aria-label="t('sync.manual.nudges', { name: member.name })" class="flex gap-1">
      <SbButton
        v-for="d in NUDGES"
        :key="d"
        type="button"
        size="sm"
        variant="secondary"
        class="min-w-12 flex-1 tabular-nums"
        :aria-label="nudgeLabel(d)"
        @click="set(modelValue + d)"
      >
        {{ d < 0 ? '−' : '+' }}{{ Math.abs(d) }}
      </SbButton>
    </div>
  </div>
</template>
