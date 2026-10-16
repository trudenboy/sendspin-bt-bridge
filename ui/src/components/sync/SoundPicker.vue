<script setup lang="ts">
import { nextTick, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import type { SyncSound } from '@/api/types'

/** Manual tuning's listening sound: a segmented control (one radio group) with a hint for the one picked. */
const props = defineProps<{ modelValue: SyncSound; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [sound: SyncSound] }>()

const { t } = useI18n()
const uid = useId()
const SOUNDS: readonly SyncSound[] = ['clicks', 'accent', 'noise']
const buttons = ref<HTMLButtonElement[]>([])

function pick(sound: SyncSound) {
  if (!props.disabled && sound !== props.modelValue) emit('update:modelValue', sound)
}

/** Arrow keys move the choice along the group, as in any radio group. */
async function onKey(e: KeyboardEvent, index: number) {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
  if (!step) return
  e.preventDefault()
  const next = (index + step + SOUNDS.length) % SOUNDS.length
  pick(SOUNDS[next]!)
  await nextTick()
  buttons.value[next]?.focus()
}
</script>

<template>
  <div data-sound class="flex flex-col gap-1">
    <div class="flex flex-wrap items-center gap-2">
      <span :id="`${uid}-label`" class="text-sm text-text-secondary">{{ t('sync.sound.label') }}</span>
      <div
        role="radiogroup"
        :aria-labelledby="`${uid}-label`"
        :aria-describedby="`${uid}-hint`"
        class="inline-flex max-w-full flex-wrap rounded-(--radius-button) border border-border-strong p-0.5"
      >
        <button
          v-for="(sound, i) in SOUNDS"
          :key="sound"
          ref="buttons"
          type="button"
          role="radio"
          :data-sound-option="sound"
          :aria-checked="modelValue === sound"
          :tabindex="modelValue === sound ? 0 : -1"
          :disabled="disabled"
          :class="[
            'h-7 cursor-pointer whitespace-nowrap rounded-(--radius-button) px-3 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 disabled:cursor-not-allowed disabled:opacity-50',
            modelValue === sound ? 'bg-primary-fill text-on-primary' : 'text-text-primary hover:bg-surface-secondary',
          ]"
          @click="pick(sound)"
          @keydown="onKey($event, i)"
        >
          {{ t(`sync.sound.${sound}`) }}
        </button>
      </div>
    </div>
    <p :id="`${uid}-hint`" data-sound-hint class="text-xs text-text-secondary">{{ t(`sync.sound.hint.${modelValue}`) }}</p>
  </div>
</template>
