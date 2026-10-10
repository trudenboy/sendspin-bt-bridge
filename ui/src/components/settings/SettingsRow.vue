<script setup lang="ts">
/**
 * One setting: label and help on the left, the control on the right, stacked
 * on narrow screens. Home Assistant's ha-settings-row pattern.
 */
defineProps<{
  label: string
  help?: string
  /** id of the control, so the label focuses it. */
  for?: string
  /** Help text id for aria-describedby on the control. */
  helpId?: string
  wide?: boolean
  /** Edited but not saved yet. */
  changed?: boolean
}>()
</script>

<template>
  <div
    class="relative flex flex-col gap-2 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-6"
    :class="wide ? 'sm:flex-col sm:items-stretch' : ''"
  >
    <span v-if="changed" class="absolute top-3 bottom-3 -left-4 w-0.5 rounded-full bg-primary sm:-left-5" aria-hidden="true" />
    <div class="min-w-0 sm:flex-1">
      <label :for="$props.for" class="flex items-center gap-2 text-sm font-medium text-text-primary">
        {{ label }}
        <span v-if="changed" class="rounded-full tone-info px-1.5 py-px text-[11px] font-medium">{{ $t('settings.changed') }}</span>
      </label>
      <p v-if="help" :id="helpId" class="mt-0.5 text-[13px] leading-snug text-text-secondary">{{ help }}</p>
    </div>
    <div class="min-w-0 shrink-0" :class="wide ? 'w-full' : 'sm:max-w-[55%]'">
      <slot />
    </div>
  </div>
</template>
