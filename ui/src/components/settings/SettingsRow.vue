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
}>()
</script>

<template>
  <div
    class="flex flex-col gap-2 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-6"
    :class="wide ? 'sm:flex-col sm:items-stretch' : ''"
  >
    <div class="min-w-0 sm:flex-1">
      <label :for="$props.for" class="block text-sm font-medium text-text-primary">{{ label }}</label>
      <p v-if="help" :id="helpId" class="mt-0.5 text-[13px] leading-snug text-text-secondary">{{ help }}</p>
    </div>
    <div class="min-w-0 shrink-0" :class="wide ? 'w-full' : 'sm:max-w-[55%]'">
      <slot />
    </div>
  </div>
</template>
